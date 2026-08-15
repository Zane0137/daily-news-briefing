# 每日新闻简报自动化 Agent

每天 20:07（北京时间）自动抓取 F1 与数码科技新闻，去重、筛选、用 DeepSeek 做中文总结，
生成适合手机阅读的短信文本。**当前阶段只生成预览文件，不发送短信。**

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

### iCloud 设置步骤（当前默认）

1. 打开 https://appleid.apple.com 并登录你的 Apple 账号。
2. 点"登录与安全"（Sign-In and Security）->"App 专用密码"（App-Specific Passwords），
   生成一个专用密码（标签随便填，如 `news-briefing`），复制这串 16 位密码。
3. 到 GitHub 仓库 Settings -> Secrets and variables -> Actions，添加 3 个密钥：

| 密钥名 | 填什么 |
|---|---|
| `SMTP_USER` | 你的 iCloud 完整邮箱（如 `you@icloud.com`） |
| `SMTP_PASS` | 上面生成的 16 位应用专用密码 |
| `SMTP_TO` | 收件邮箱（通常和 `SMTP_USER` 一样） |

4. 在 Actions 页面重新点一次 **Run workflow**，等运行完去邮箱查收"【每日简报】"邮件。

### 如果以后改用 Gmail 或 Outlook

只需改 `config.json` 里的 `smtp.host`，并换对应的"专用密码"：

| 邮箱 | 服务器 | 端口 | 密码 |
|---|---|---|---|
| Gmail | smtp.gmail.com | 587 | Google 应用专用密码 |
| iCloud | smtp.mail.me.com | 587 | Apple 专用密码（appleid.apple.com 生成） |
| Outlook | smtp.office365.com | 587 | Microsoft 应用密码（需开启两步验证） |

### 本地测试邮件

在 `.env` 里加上 `SMTP_USER`、`SMTP_PASS`、`SMTP_TO` 后运行 `python main.py` 即可。
收件地址和密码永远不会出现在日志里。

## 费用

- 当前阶段：0 元（不注册短信、不充值）。
- DeepSeek：每天几次调用，约几分钱（模型 `deepseek-v4-flash`）。
- 短信（未来阶段）：预计每天 0.1～0.3 元。

## 常见问题

- **某个源抓取失败**：`config.json` 里删掉或换源即可，主流程会自动跳过失败源。
- **同一天运行两次**：历史记录会记住已选新闻，第二次不会重复选取。
- **中英文报道同一事件**：AI 辅助去重会自动合并（例如 IT之家和 The Verge 报道同一件事时只保留一条）。
- **DeepSeek 调用失败**：自动重试 2 次，仍失败则用原标题兜底，不影响出稿。
- **DeepSeek 为什么偶尔返回空内容**：V4 模型默认会先"思考"，思考会占用输出额度。
  本项目已通过 `"thinking": {"type": "disabled"}` 关闭思考，保证正文稳定返回、更快更省。
- **云端日志里为什么看不到简报内容**：这是隐私设计，正文只存在你本地的 `output/` 目录。
