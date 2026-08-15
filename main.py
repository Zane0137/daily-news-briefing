#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日新闻简报自动化 Agent —— 主流程

仅使用 Python 标准库，本地与云端（GitHub Actions）都不需要安装任何第三方库。

流程：抓取 RSS -> 24 小时过滤 -> 去重 -> 重要性评分 -> DeepSeek 中文总结
      -> 生成短信文本预览文件 -> 写历史记录。

隐私约定：
  - 控制台只输出统计数字，不输出新闻正文、标题全文、手机号、密钥。
  - 历史记录与预览文件放在 state/ 和 output/（已被 .gitignore 排除）。
"""

import argparse
import difflib
import email.utils
import gzip
import hashlib
import html
import json
import os
import re
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.text import MIMEText

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
TITLE_SUFFIXES = [
    " - the verge", " | techcrunch", " | bbc sport", " - autosport",
    " | autosport", " - it之家", " | it之家", " - 少数派", " - sspai",
    " - formula 1", " - f1",
]
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "ref_src", "ref_url", "mc_cid", "mc_eid",
    "ich_track",
}


def now_utc():
    return datetime.now(timezone.utc)


def beijing_now():
    return now_utc() + timedelta(hours=8)


def load_env(path=ENV_PATH):
    """读取 .env（KEY=VALUE 格式），已存在的环境变量不覆盖。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def load_config(path=CONFIG_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------- 抓取 RSS -----------------------------

def fetch_feed(url, timeout=15):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, "
                      "application/xml, text/xml, */*",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            data = gzip.decompress(data)
    return data


def fetch_feed_with_retry(url, tries=2, timeout=15):
    last_error = None
    for attempt in range(tries):
        try:
            return fetch_feed(url, timeout=timeout)
        except Exception as exc:
            last_error = exc
            if attempt < tries - 1:
                time.sleep(1)
    raise last_error


def localname(tag):
    return tag.rsplit("}", 1)[-1]


def _child_text(node, name):
    for child in node:
        if localname(child.tag) == name:
            return (child.text or "").strip()
    return ""


def parse_date(s):
    """解析 RFC2822 或 ISO8601 日期，统一返回 UTC 时间；失败返回 None。"""
    if not s:
        return None
    s = s.strip()
    try:
        return email.utils.parsedate_to_datetime(s).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        fixed = s.replace("Z", "+00:00")
        fixed = re.sub(r"(\.\d{6})\d+", r"\1", fixed)
        dt = datetime.fromisoformat(fixed)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text):
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _rss_item(node):
    title = _child_text(node, "title")
    link = _child_text(node, "link")
    pub = _child_text(node, "pubDate") or _child_text(node, "date")
    desc = _child_text(node, "description") or _child_text(node, "summary")
    if not title and not link:
        return None
    return {
        "title": html.unescape(title or "").strip() or "(无标题)",
        "link": link,
        "published": parse_date(pub),
        "excerpt": strip_html(desc),
    }


def _atom_entry(node):
    title = _child_text(node, "title")
    link = ""
    for child in node:
        if localname(child.tag) == "link":
            link = child.get("href") or ""
            break
    pub = _child_text(node, "published") or _child_text(node, "updated")
    desc = _child_text(node, "summary") or _child_text(node, "content")
    if not title and not link:
        return None
    return {
        "title": html.unescape(title or "").strip() or "(无标题)",
        "link": link,
        "published": parse_date(pub),
        "excerpt": strip_html(desc),
    }


def parse_feed(data):
    """解析 RSS 1.0/2.0 或 Atom，返回条目列表（按元素名识别，兼容各种根标签）。"""
    root = ET.fromstring(data)
    items = []
    for node in root.iter():
        tag = localname(node.tag)
        if tag == "item":
            item = _rss_item(node)
        elif tag == "entry":
            item = _atom_entry(node)
        else:
            continue
        if item:
            items.append(item)
    return items


# ----------------------------- 去重 -----------------------------

def normalize_title(title, source_name=None):
    t = (title or "").lower()
    for suffix in TITLE_SUFFIXES:
        if t.endswith(suffix):
            t = t[: -len(suffix)]
    if source_name:
        nm = source_name.lower()
        for sep in (" | ", " - ", " — "):
            if t.endswith(sep + nm):
                t = t[: -len(sep + nm)]
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def normalize_url(url):
    try:
        parts = urllib.parse.urlsplit(url or "")
        query = "&".join(
            k + "=" + v
            for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        )
        return urllib.parse.urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, "")
        )
    except Exception:
        return (url or "").strip().lower().rstrip("/")


def fingerprint(title_norm, url_norm):
    return hashlib.sha1((title_norm + "|" + url_norm).encode("utf-8")).hexdigest()


def titles_similar(a, b, threshold=0.85):
    if not a or not b:
        return False
    if a == b:
        return True
    if difflib.SequenceMatcher(None, a, b).ratio() >= threshold:
        return True
    # 一个标题完全包含另一个（长度差异不大）也视为重复
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 6 and short in long and len(short) / len(long) >= 0.5:
        return True
    return False


# ----------------------------- 评分 -----------------------------

def importance_score(item, section_cfg):
    weight = float(item.get("weight", 1.0))
    published = item.get("published")
    freshness = 1.0
    if published:
        age_hours = max(0.0, (now_utc() - published).total_seconds() / 3600.0)
        if age_hours < 6:
            freshness = 1.0
        elif age_hours < 12:
            freshness = 0.8
        elif age_hours < 18:
            freshness = 0.6
        else:
            freshness = 0.4
    title_norm = item.get("title_norm", "")
    keyword_hits = sum(
        1 for kw in section_cfg.get("keywords", []) if kw.lower() in title_norm
    )
    return weight * freshness * (1.0 + min(keyword_hits, 3) * 0.5)


# ----------------------------- DeepSeek -----------------------------

def truncate(text, limit):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _deepseek_post(config, payload, api_key, timeout=30):
    url = config.get("api_base", "https://api.deepseek.com").rstrip("/") + "/chat/completions"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
            "User-Agent": DEFAULT_USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def summarize_with_deepseek(title, excerpt, config, api_key, retries=2):
    """返回 (中文标题, 中文摘要)；彻底失败返回 None（由调用方用原标题兜底）。"""
    target = int(config.get("summary_chars", 63))
    system = (
        "你是中文新闻编辑。请把下面这条新闻的标题翻译成中文，并写一句中文摘要。"
        "要求：摘要约 {} 个汉字（允许上下浮动 3 字）；内容具体明确，不要出现"
        "'某一个人''某一个车队'这类含糊表述；标题和摘要都只用中文"
        "（专有名词如 F1、AI 可保留）。"
        "只输出 JSON：{{\"title_cn\": \"中文标题\", \"summary\": \"中文摘要\"}}，"
        "不要输出任何其他内容。"
    ).format(target)
    user = "原标题：{}\n正文摘要：{}".format(title, truncate(excerpt or "（无正文摘要）", 800))
    payload = {
        "model": config.get("model", "deepseek-v4-flash"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "thinking": {"type": "disabled"},
        "temperature": 0.3,
        "max_tokens": 400,
    }
    for attempt in range(retries + 1):
        try:
            data = _deepseek_post(config, payload, api_key)
            content = data["choices"][0]["message"]["content"].strip()
            if not content:
                raise ValueError("empty content")
            match = re.search(r"\{.*\}", content, re.S)
            if not match:
                raise ValueError("no json in response")
            parsed = json.loads(match.group(0))
            title_cn = (parsed.get("title_cn") or "").strip() or title
            summary = (parsed.get("summary") or "").strip()
            if not summary:
                raise ValueError("empty summary")
            return truncate(title_cn, 40), truncate(summary, target + 3)
        except Exception:
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
            return None


def ai_merge_and_rank(config, api_key, sec_cfg, candidates, top_n, retries=2):
    """让 DeepSeek 合并同一事件（含中英文）的报道并按重要性排序。

    返回保留的候选下标（按优先级排列）；失败返回 None，由调用方退回规则结果。
    """
    lines = []
    for i, it in enumerate(candidates, 1):
        lines.append(
            "{}. [{}] {}".format(i, it.get("source", ""), truncate(it.get("title", ""), 120))
        )
    prompt = (
        "下面是一个新闻候选列表，可能有多条在报道同一事件（包括同一事件的中英文报道）。"
        "请做两件事：1) 把报道同一事件的条目合并，只保留其中最重要的一条；"
        "2) 按重要程度从高到低排序。"
        "只输出 JSON：{{\"keep\": [序号...]}}，最多保留 {} 个序号，不要输出任何其他内容。\n\n{}"
    ).format(top_n, "\n".join(lines))
    hint = sec_cfg.get("ai_hint", "")
    if hint:
        prompt = "额外要求：{}\n\n{}".format(hint, prompt)
    payload = {
        "model": config.get("model", "deepseek-v4-flash"),
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": "disabled"},
        "temperature": 0.2,
        "max_tokens": 200,
    }
    for attempt in range(retries + 1):
        try:
            data = _deepseek_post(config, payload, api_key)
            content = data["choices"][0]["message"]["content"].strip()
            if not content:
                raise ValueError("empty content")
            match = re.search(r"\{.*\}", content, re.S)
            if not match:
                raise ValueError("no json in response")
            parsed = json.loads(match.group(0))
            keep = [int(x) for x in parsed.get("keep", []) if str(x).isdigit()]
            ordered = []
            seen = set()
            for idx in keep:
                pos = idx - 1
                if 0 <= pos < len(candidates) and pos not in seen:
                    seen.add(pos)
                    ordered.append(pos)
            if not ordered:
                raise ValueError("empty keep list")
            return ordered[:top_n]
        except Exception:
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
            return None


def verify_model(config, api_key):
    """用一次极小的调用校验模型 id 是否可用。"""
    payload = {
        "model": config.get("model", "deepseek-v4-flash"),
        "messages": [{"role": "user", "content": "回复 OK"}],
        "thinking": {"type": "disabled"},
        "max_tokens": 50,
    }
    try:
        data = _deepseek_post(config, payload, api_key)
        content = data["choices"][0]["message"]["content"].strip()
        if not content:
            return False, "模型返回内容为空（可能是思考占用输出额度），请重试或换模型。"
        return True, "模型 {} 调用成功，返回：{}".format(
            config.get("model"), truncate(content, 20)
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        return False, "模型调用失败（HTTP {}）：{}".format(exc.code, detail)
    except Exception as exc:
        return False, "模型调用失败：{}".format(str(exc)[:200])


# ----------------------------- 历史记录 -----------------------------

def ensure_state_file(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"items": []}, f, ensure_ascii=False)


def load_history(path):
    ensure_state_file(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items", [])
    except Exception:
        items = []
    cutoff = (now_utc() - timedelta(days=30)).timestamp()
    return [it for it in items if it.get("ts", 0) >= cutoff][:2000]


def save_history(path, history):
    ensure_state_file(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"items": history}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ----------------------------- 排版 -----------------------------

def format_briefing(config, selected):
    date_str = beijing_now().strftime("%Y-%m-%d")
    weekday = WEEKDAY_CN[beijing_now().weekday()]
    lines = ["【每日简报】{} {}".format(date_str, weekday), ""]
    for sec_key, sec_cfg in config["sections"].items():
        items = selected.get(sec_key, [])
        lines.append("■ {}（{} 条）".format(sec_cfg.get("label", sec_key), len(items)))
        for index, item in enumerate(items, 1):
            lines.append("{}. {}".format(index, item.get("title_cn") or item["title"]))
            lines.append(item["summary"])
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def estimate_segments(text):
    # 中文短信按 70 字/段计，长短信自动拼接
    return max(1, (len(text) + 69) // 70)


def send_preview_email(body, config):
    """把简报通过 SMTP 发到自己的邮箱（免费预览用）。

    未配置 SMTP 环境变量时返回 None（跳过）；发送成功返回 True；失败返回 False。
    收件地址、密码来自环境变量（GitHub Secrets 或本地 .env），绝不打印。
    """
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    to_addr = os.environ.get("SMTP_TO", "") or user
    if not user or not password or not to_addr:
        return None
    host = os.environ.get("SMTP_HOST", "") or config.get("smtp", {}).get("host", "smtp.gmail.com")
    try:
        port = int(os.environ.get("SMTP_PORT", "") or config.get("smtp", {}).get("port", 587))
    except (TypeError, ValueError):
        port = 587
    subject = "【每日简报】{} {}".format(
        beijing_now().strftime("%Y-%m-%d"), WEEKDAY_CN[beijing_now().weekday()]
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = to_addr
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())
        server.quit()
        return True
    except Exception:
        return False


# ----------------------------- 主流程 -----------------------------

def run(config, use_ai=True, state_path=None):
    state_path = state_path or os.path.join(
        PROJECT_ROOT, config.get("state_file", "state/history.json")
    )
    history = load_history(state_path)
    history_fps = {h.get("fp") for h in history}
    history_titles = [h.get("title_norm", "") for h in history]

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    now = now_utc()
    window = timedelta(hours=config.get("window_hours", 24))
    future_tolerance = timedelta(minutes=30)

    stats = []
    selected = {}
    total_raw = 0
    total_filtered = 0
    total_deduped = 0

    for sec_key, sec_cfg in config["sections"].items():
        section_items = []
        for src in sec_cfg["sources"]:
            try:
                data = fetch_feed_with_retry(src["url"])
                parsed = parse_feed(data)
            except Exception:
                stats.append("  {} / {}：抓取失败（已跳过）".format(sec_key, src["name"]))
                continue
            for item in parsed:
                item["source"] = src["name"]
                item["weight"] = src.get("weight", 1.0)
                item["title_norm"] = normalize_title(item.get("title"), src["name"])
                item["url_norm"] = normalize_url(item.get("link"))
                item["fingerprint"] = fingerprint(item["title_norm"], item["url_norm"])
            section_items.extend(parsed)
            stats.append("  {} / {}：抓到 {} 条".format(sec_key, src["name"], len(parsed)))

        total_raw += len(section_items)

        # 24 小时时间窗过滤
        filtered = []
        for item in section_items:
            pub = item.get("published")
            if not pub:
                continue
            age = now - pub
            if -future_tolerance <= age <= window:
                filtered.append(item)
        total_filtered += len(filtered)

        # 去重：高权重、更新的来源优先保留
        filtered.sort(key=lambda it: (it.get("weight", 0.0), it.get("published") or now), reverse=True)
        kept = []
        seen_fps = set()
        seen_titles = []
        for item in filtered:
            if item["fingerprint"] in seen_fps or item["fingerprint"] in history_fps:
                continue
            if any(titles_similar(item["title_norm"], other) for other in seen_titles + history_titles):
                continue
            item["score"] = importance_score(item, sec_cfg)
            kept.append(item)
            seen_fps.add(item["fingerprint"])
            seen_titles.append(item["title_norm"])
        total_deduped += len(kept)

        kept.sort(key=lambda it: it["score"], reverse=True)
        top_n = sec_cfg.get("top_n", 3)

        # AI 辅助去重 + 重要性排序（跨语言也能识别同一事件）
        if use_ai:
            candidates = kept[: max(top_n * 3, 8)]
            ai_order = ai_merge_and_rank(config, api_key, sec_cfg, candidates, top_n)
            if ai_order is not None:
                chosen = [candidates[i] for i in ai_order]
                stats.append(
                    "  {}：AI 辅助去重/排序（候选 {} 条 -> 保留 {} 条）".format(
                        sec_key, len(candidates), len(chosen)
                    )
                )
            else:
                chosen = kept[:top_n]
                stats.append("  {}：AI 辅助去重失败，退回规则结果".format(sec_key))
        else:
            chosen = kept[:top_n]

        stats.append(
            "  {}：原始 {} 条 -> 24 小时内 {} 条 -> 去重后 {} 条 -> 选取 {} 条".format(
                sec_key, len(section_items), len(filtered), len(kept), len(chosen)
            )
        )

        # DeepSeek 中文总结
        for index, item in enumerate(chosen, 1):
            if use_ai:
                result = summarize_with_deepseek(
                    item["title"], item.get("excerpt", ""), config, api_key
                )
                if result is None:
                    item["title_cn"] = item["title"]
                    item["summary"] = item["title"]  # 原标题兜底
                    stats.append("  {}：第 {} 条总结失败，已用原标题兜底".format(sec_key, index))
                else:
                    item["title_cn"], item["summary"] = result
            else:
                item["title_cn"] = item["title"]
                item["summary"] = "（AI 总结未启用，接入 DeepSeek Key 后自动生成）"
        selected[sec_key] = chosen

    # 生成预览文件（不发送短信）
    briefing = format_briefing(config, selected)
    output_dir = os.path.join(PROJECT_ROOT, config.get("output_dir", "output"))
    os.makedirs(output_dir, exist_ok=True)
    preview_path = os.path.join(
        output_dir,
        "{}{}.txt".format(config.get("preview_prefix", "briefing_"), beijing_now().strftime("%Y-%m-%d")),
    )
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(briefing)
    stats.append(" 预览文件已保存：{}".format(preview_path))
    stats.append(" 预计短信段数（仅供参考，当前阶段不发送）：{} 段".format(estimate_segments(briefing)))

    email_result = send_preview_email(briefing, config)
    if email_result is None:
        stats.append(" 邮件预览：未配置（跳过）")
    elif email_result:
        stats.append(" 邮件预览：已发送到你的邮箱")
    else:
        stats.append(" 邮件预览：发送失败（不影响本地预览文件）")

    # 写历史记录（仅真实运行模式；--no-ai 测试模式不占用新闻）
    if use_ai:
        now_ts = now.timestamp()
        for sec_key, items in selected.items():
            for item in items:
                history.append(
                    {
                        "fp": item["fingerprint"],
                        "title_norm": item["title_norm"],
                        "ts": now_ts,
                        "date": item.get("published").strftime("%Y-%m-%d") if item.get("published") else "",
                    }
                )
        save_history(state_path, history)
        stats.append(" 历史记录已更新（{} 条新条目）".format(
            sum(len(v) for v in selected.values())
        ))
    else:
        stats.append(" --no-ai 为测试模式，本次不写入历史记录")

    stats.insert(0, "运行完成：原始 {} 条 -> 24 小时内 {} 条 -> 去重后 {} 条".format(
        total_raw, total_filtered, total_deduped
    ))
    return stats


def main():
    parser = argparse.ArgumentParser(description="每日新闻简报（RSS -> 去重 -> 评分 -> DeepSeek 总结 -> 预览）")
    parser.add_argument("--config", default=CONFIG_PATH, help="配置文件路径（默认 config.json）")
    parser.add_argument("--no-ai", action="store_true", help="跳过 DeepSeek 总结（测试模式，不写历史）")
    parser.add_argument("--check-model", action="store_true", help="校验 DeepSeek 模型 id 是否可用")
    parser.add_argument("--state", default=None, help="历史记录文件路径（默认 config.json 中的 state_file）")
    args = parser.parse_args()

    config = load_config(args.config)
    load_env()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    if args.check_model:
        if not api_key:
            print("未检测到 DEEPSEEK_API_KEY：请先在 .env 中填写。")
            sys.exit(2)
        ok, message = verify_model(config, api_key)
        print(message)
        sys.exit(0 if ok else 1)

    use_ai = not args.no_ai
    if use_ai and not api_key:
        print("未检测到 DEEPSEEK_API_KEY：请在 .env 中填写后重试，或先用 --no-ai 跑通流程。")
        sys.exit(2)

    for line in run(config, use_ai=use_ai, state_path=args.state):
        print(line)


if __name__ == "__main__":
    main()
