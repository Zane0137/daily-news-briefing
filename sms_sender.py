#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMS 短信发送适配层（纯标准库，独立于邮件）。

约定：
- send_sms() 永不抛异常：返回 None=未配置跳过 / True=成功 / str=失败原因。
- 敏感信息（手机号、API Key、Secret）只从环境变量读取，绝不写入日志。
- 服务商通过 config.json 的 sms 块配置；endpoint 为空时自动跳过，不产生请求。
- 短信内容为纯文本；超过 sms.max_chars（默认 320）时按优先级裁剪：
  F1 比赛日特别简报（完整保留，永不截断）-> F1 速报 -> 数码科技 -> 国际要闻。
"""

import json
import hashlib
import os
import urllib.parse
import urllib.error
import urllib.request

DEFAULT_MAX_CHARS = 320
RACE_HEADER = "🏁 F1 比赛日特别简报"
SECTION_MARK = "■ "


def _sms_cfg(config):
    return config.get("sms", {}) or {}


def format_sms_text(config, text):
    """整理短信纯文本并做长度控制。

    返回 (text, dropped)：
    - text：不超过 max_chars；若存在 F1 比赛日块则完整保留（不参与截断）
    - dropped：被裁掉的普通栏目块名称列表（仅用于日志，不含敏感信息）
    """
    max_chars = int(_sms_cfg(config).get("max_chars", DEFAULT_MAX_CHARS))
    lines = [line.rstrip() for line in (text or "").splitlines()]

    # 压缩连续空行、去掉首尾空行
    cleaned = []
    blank = 0
    for line in lines:
        if not line.strip():
            blank += 1
            if blank <= 1:
                cleaned.append("")
        else:
            blank = 0
            cleaned.append(line)
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()

    # 分离 F1 比赛日块与普通栏目块
    race_lines = []
    normal_lines = []
    in_race = False
    for line in cleaned:
        if line.startswith(RACE_HEADER):
            in_race = True
        if in_race:
            race_lines.append(line)
        else:
            normal_lines.append(line)

    race_text = "\n".join(race_lines).strip()
    normal_text = "\n".join(normal_lines).strip()
    dropped = []

    if not race_text:
        # 无比赛日：按普通新闻顺序（F1 -> 数码 -> 国际）填充
        return _fill_sections(normal_text, max_chars, dropped), dropped

    # 有比赛日：比赛日块完整保留（绝不截断），剩余空间填充普通新闻
    if len(race_text) > max_chars:
        # 比赛日块本身超过总上限时仍完整保留（其自身模块已限制在更小的长度内）
        return race_text, dropped
    remaining = max_chars - len(race_text) - 2  # 2 为块间空行
    normal_part = _fill_sections(normal_text, remaining, dropped)
    if normal_part:
        return race_text + "\n\n" + normal_part, dropped
    return race_text, dropped


def _fill_sections(normal_text, budget, dropped):
    """按出现顺序逐块填充普通新闻；放不下的整块丢弃（不截断句子）。"""
    if budget <= 0 or not normal_text.strip():
        return ""

    lines = normal_text.splitlines()
    blocks = []
    current = []
    for line in lines:
        if line.startswith(SECTION_MARK) and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)

    kept = []
    total = 0
    for block in blocks:
        block_text = "\n".join(block).strip()
        if not block_text:
            continue
        separator = 2 if kept else 0
        if total + separator + len(block_text) > budget:
            name = block[0].replace(SECTION_MARK, "").split("（")[0].strip()
            dropped.append(name or "未知栏目")
            continue
        total += separator + len(block_text)
        kept.append(block_text)
    return "\n\n".join(kept)


def _send_http_json(config, text):
    """通用 HTTP-JSON 适配器（占位实现，不绑定具体服务商）。

    发送 POST JSON：{"phone": ..., "content": text, "sign": ...}
    鉴权：Authorization: Bearer <SMS_API_KEY>；可选 X-SMS-Secret: <SMS_API_SECRET>
    未来选定服务商后，在 _PROVIDERS 中新增函数即可，main.py 不需要改动。
    """
    sms = _sms_cfg(config)
    endpoint = (sms.get("endpoint") or "").strip()
    phone = (os.environ.get("SMS_PHONE") or "").strip()
    api_key = (os.environ.get("SMS_API_KEY") or "").strip()
    api_secret = (os.environ.get("SMS_API_SECRET") or "").strip()
    if not endpoint:
        raise ValueError("sms endpoint not configured")
    if not phone:
        raise ValueError("SMS_PHONE not configured")
    if not api_key:
        raise ValueError("SMS_API_KEY not configured")
    timeout = float(sms.get("timeout_seconds", 15) or 15)

    body = {
        "phone": phone,
        "content": text,
        "sign": sms.get("sign", ""),
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": "Bearer " + api_key,
        "User-Agent": "Mozilla/5.0 (compatible; DailyNewsBriefing/1.0)",
    }
    if api_secret:
        headers["X-SMS-Secret"] = api_secret
    req = urllib.request.Request(endpoint, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()  # 丢弃响应体，不回显到日志
    return True


_SMSBAO_ERRORS = {
    "-1": "参数不全",
    "30": "密码错误",
    "40": "账号不存在",
    "41": "余额不足",
    "42": "账号已过期",
    "43": "IP 受限",
    "50": "内容含敏感词",
    "51": "手机号不正确",
    "52": "内容过长",
    "53": "发送频率过快",
}


class ProviderError(Exception):
    """服务商返回的业务错误（只含错误码与说明，不含手机号/密钥/正文）。"""

    def __init__(self, provider, code, message):
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.message = message


def _smsbao_url(config, text):
    """构造短信宝发送 URL（纯函数，便于测试）。"""
    sms = _sms_cfg(config)
    user = (os.environ.get("SMS_BAO_USER") or "").strip()
    password = (os.environ.get("SMS_BAO_PASS") or "").strip()
    apikey = (os.environ.get("SMS_BAO_APIKEY") or "").strip()
    phone = (os.environ.get("SMS_PHONE") or "").strip()
    if not user:
        raise ValueError("SMS_BAO_USER not configured")
    if not phone:
        raise ValueError("SMS_PHONE not configured")
    if not password and not apikey:
        raise ValueError("SMS_BAO_PASS or SMS_BAO_APIKEY not configured")
    p = apikey if apikey else hashlib.md5(password.encode("utf-8")).hexdigest()
    params = {"u": user, "p": p, "m": phone, "c": text}
    timeout = float(sms.get("timeout_seconds", 15) or 15)
    endpoint = os.environ.get("SMS_BAO_ENDPOINT") or "https://api.smsbao.com/sms"
    return endpoint + "?" + urllib.parse.urlencode(params), timeout


def _send_smsbao(config, text):
    """短信宝国内短信发送（GET 接口，返回 0 表示提交成功）。"""
    url, timeout = _smsbao_url(config, text)
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; DailyNewsBriefing/1.0)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace").strip()
    first = body.splitlines()[0].strip() if body else ""
    if first == "0":
        return True
    meaning = _SMSBAO_ERRORS.get(first, "未知错误")
    raise ProviderError("短信宝", first, meaning)


_PROVIDERS = {
    "http_json": _send_http_json,
    "smsbao": _send_smsbao,
}


def _safe_error(exc):
    """把异常转成不含手机号/密钥的简短错误描述。"""
    if isinstance(exc, ProviderError):
        return "{}错误码 {}（{}）".format(exc.provider, exc.code, exc.message)
    if isinstance(exc, ValueError):
        return "配置错误：{}".format(str(exc)[:100])
    if isinstance(exc, urllib.error.HTTPError):
        return "HTTP {} {}".format(exc.code, exc.reason)
    if isinstance(exc, urllib.error.URLError):
        return "connection failed"
    return type(exc).__name__


def send_sms(config, text):
    """短信发送入口（永不抛异常）。"""
    sms = _sms_cfg(config)
    if not sms.get("enabled", False):
        return None
    provider = sms.get("provider", "http_json")
    if provider == "http_json" and not (sms.get("endpoint") or "").strip():
        return None  # 通用 HTTP 服务商未配置 endpoint，跳过
    sender = _PROVIDERS.get(provider)
    if sender is None:
        return "unknown provider: {}".format(provider)
    fmt_text, _dropped = format_sms_text(config, text)
    try:
        sender(config, fmt_text)
    except Exception as exc:
        return _safe_error(exc)
    return True
