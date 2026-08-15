# 每日新闻简报自动化 Agent

每天 20:07（北京时间）自动抓取 F1、数码科技、国际要闻三类新闻，去重、筛选、
用 DeepSeek 做中文总结（标题也翻译成中文，摘要严格 60 字左右、上下浮动不超过 5 字），
生成适合手机阅读的纯文本，自动发到你的邮箱预览，并可通过 Bark（iPhone 免费推送）
通知到手机。**运营商短信为后续阶段。**

## 隐私红线（重要）

- 公开仓库里**只放代码和配置**。
- `state/`（运行历史）、`output/`（每日简报）、`.env`（密钥）已被 `.gitignore` 排除，**永远不会上传**。
- 云端日志**只输出统计数字**（如"抓到 42 条 -> 去重后 18 条 -> 选取 3 条"），
  不会输出新闻标题全文、简报正文、手机号或密钥。
- 运行历史通过 GitHub Actions 缓存保存（仅本仓库工作流可访问，公网不可见）。

## 文件说明

| 文件 | 作用 |
|---|---|
| `main.py` | 主流程：抓取 -> 24 小时过滤 -> 规则去重 -> 评分 -> AI 辅助去重/排序 -> DeepSeek 总结 -> 生成预览文件 |
| `config.json` | 新闻源、每栏条数、关键词、模型名等配置（改配置不用改代码） |
| `tools/check_sources.py` | 阶段 0 工具：实测每个新闻源是否可用 |
| `sms_sender.py` | 通知适配层：邮件之外的第二出口（当前为 Bark 推送），失败不影响邮件 |
| `.github/workflows/daily.yml` | 云端定时（每天 20:07）+ 手动触发按钮 |
| `.github/workflows/keepalive.yml` | 每周一次空提交，防止定时任务被 GitHub 暂停 |
| `.env.example` | DeepSeek API Key 的填写模板 |

## 技术栈

只用 Python 标准库（`urllib`、`xml`、`json`、`difflib` 等），本地和云端都无需安装任何第三方库。

## 使用步骤（一次一步）

### 阶段 0：检查新闻源

运行以下命令，逐个实测 `config.json` 里的 RSS 源：

```
python tools/check_sources.py
```

会显示每个源抓到的最新 1 条新闻标题和链接。不可用的源会从 `config.json` 里移除。

### 阶段 1：本地跑通（不发送短信）

1. 复制 `.env.example` 并改名为 `.env`，填入 DeepSeek API Key：

```
DEEPSEEK_API_KEY=sk-你的真实Key
```

2. 先用测试模式跑通流程（不调用 AI、不写历史）：

```
python main.py --no-ai
```

3. 再正式运行一次（调用 DeepSeek 总结）：

```
python main.py
```

4. 打开 `output/briefing_日期.txt` 查看最终短信文本。

如果模型名有疑问，可以运行校验：

```
python main.py --check-model
```

### 阶段 2：部署到云端（之后再做）

1. 注册 GitHub 账号，安装 GitHub Desktop。
2. 创建公开仓库，把本项目文件夹推上去。
3. 在 GitHub 仓库页面：Settings -> Secrets and variables -> Actions，
   添加 `DEEPSEEK_API_KEY`（填 DeepSeek 的 Key）。
4. 打开 Actions 页面的"每日新闻简报"，点 **Run workflow** 手动触发一次。
5. 查看运行日志：应该只有统计数字，没有正文和密钥。

### 阶段 3：正式定时（之后再做）

定时已内置：每天 20:07 北京时间自动运行（cron `7 12 * * *`，UTC）。连续观察一周即可。

## 邮件预览（可选，推荐先用它代替短信）

云端每天生成简报后，会自动发到你的邮箱，不用开电脑也能看到。

## F1 比赛日简报（增量模块）

每天 20:07 的现有任务生成普通简报后，会读取 Jolpica F1 官方赛历（免费、无需 Key）：

- 若最近 24 小时内有**已结束**的排位赛 / 冲刺赛 / 正赛，按结束时间升序为每场生成一份
  短信友好简报（约 100~180 字，硬上限 220 字），追加到同一封邮件末尾，不另发邮件。
- 只显示 P1/P2/P3 + 迈凯伦（Norris/Piastri）名次与一句话事故/关键事件，不输出完整前十。
- 事实全部来自 Jolpica API；DeepSeek 只负责中文译名与一句话总结，不生成名次。
- 同一 Session 只生成一次（ID 如 `2026-11-Race`），重复运行显示 `Duplicate: skipped`。
- SprintQualifying 与练习赛只识别不生成（Jolpica 暂无 SprintQualifying 结果接口）。
- F1 API 失败不影响普通新闻简报；DeepSeek 失败输出事实型兜底简报。
- 所有参数在 `config.json` 的 `f1_race_day` 配置块中。

## Bark 推送（iPhone 免费通知，第二出口）

通知与邮件同源（同一份简报文本），邮件先行、Bark 随后；**Bark 失败绝不影响邮件**。
邮件与 Bark 可独立开关：`config.json` 的 `smtp.enabled` / `sms.enabled`。

Bark 是 iPhone 上的免费推送 App，不是运营商短信：无需注册、不按条收费，
但手机需要联网（Wi-Fi 或流量）并允许通知权限才能收到。

### 第一次使用（只需一次）

1. iPhone 上打开 App Store，搜索并安装 **Bark**（免费）。
2. 打开 Bark，首页会显示形如 `https://api.day.app/xxxx-xxxx-xxxx/` 的地址，
   点"复制"按钮复制完整地址。
3. 本地测试：把 `BARK_KEY=<地址或key>` 加进项目里的 `.env`
   （只填 key 或整个地址都可以，程序会自动提取；`.env` 已被 git 忽略，不会上传）。
4. 云端：到 GitHub 仓库 Settings -> Secrets and variables -> Actions，
   添加一个密钥 `BARK_KEY`（填同一段 key）。

改完 `.env` 后，可以先运行自测工具确认能收到推送：

```
python tools/test_bark.py
```

内容规则：

- 推送正文为纯文本（无 URL、无表格）；超过 `sms.max_chars`（默认 320）时自动裁剪：
  **F1 比赛日简报完整保留**，剩余空间按 F1 速报 -> 数码科技 -> 国际要闻 顺序填充。
- 密钥只放 GitHub Secrets / 本地 `.env`，绝不进代码、config.json 或日志。
- 云端日志只会显示 `SMS：已发送到手机` 或失败原因，不显示 key 与正文。

如果想用真正的运营商短信（会产生费用），后续可以再加服务商适配，
现有 `sms_sender.py` 结构已预留，不用改主流程。

### 163 邮箱设置步骤（当前默认）

1. 电脑浏览器打开 https://mail.163.com 登录你的 163 邮箱。
2. 点顶部"设置"-> 左侧选 **POP3/SMTP/IMAP** -> 开启 **SMTP 服务**（按提示用绑定手机发一条
   短信验证），会得到一个 **16 位授权码**，复制保存（这不是登录密码）。
3. 到 GitHub 仓库 Settings -> Secrets and variables -> Actions，添加 3 个密钥：

| 密钥名 | 填什么 |
|---|---|
| `SMTP_USER` | 你的 163 完整邮箱（如 `you@163.com`） |
| `SMTP_PASS` | 上面生成的 16 位授权码 |
| `SMTP_TO` | 收件邮箱（通常和 `SMTP_USER` 一样） |

4. 在 Actions 页面重新点一次 **Run workflow**，等运行完去邮箱查收"【每日简报】"邮件。

### 如果以后改用其他邮箱

只需改 `config.json` 里的 `smtp.host`，并换对应的"专用密码"：

| 邮箱 | 服务器 | 端口 | 密码 |
|---|---|---|---|
| 163（默认） | smtp.163.com | 465 | 16 位授权码 |
| Gmail | smtp.gmail.com | 587 | Google 应用专用密码 |
| iCloud | smtp.mail.me.com | 587 | Apple 专用密码（appleid.apple.com 生成） |
| Outlook | smtp.office365.com | 587 | Microsoft 应用密码（需开启两步验证） |

### 本地测试邮件

在 `.env` 里加上 `SMTP_USER`、`SMTP_PASS`、`SMTP_TO` 后运行 `python main.py` 即可。
收件地址和密码永远不会出现在日志里。

## 费用

- 当前阶段：0 元（Bark 免费，不注册短信、不充值）。
- DeepSeek：每天几次调用，约几分钱（模型 `deepseek-v4-flash`）。
- 运营商短信（未来可选阶段）：预计每天 0.1～0.3 元。

## 常见问题

- **某个源抓取失败**：`config.json` 里删掉或换源即可，主流程会自动跳过失败源。
- **同一天运行两次**：历史记录会记住已选新闻，第二次不会重复选取。
- **中英文报道同一事件**：AI 辅助去重会自动合并（例如 IT之家和 The Verge 报道同一件事时只保留一条）。
- **DeepSeek 调用失败**：自动重试 2 次，仍失败则用原标题兜底，不影响出稿。
- **DeepSeek 为什么偶尔返回空内容**：V4 模型默认会先"思考"，思考会占用输出额度。
  本项目已通过 `"thinking": {"type": "disabled"}` 关闭思考，保证正文稳定返回、更快更省。
- **云端日志里为什么看不到简报内容**：这是隐私设计，正文只存在你本地的 `output/` 目录。
