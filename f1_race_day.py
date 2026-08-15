#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F1 比赛日简报模块（增量功能，纯标准库）

职责分离：
- Jolpica F1 API 是事实来源（赛历、赛果、名次、积分），本模块绝不编造。
- DeepSeek 只负责中文车手名/站名翻译和一句话总结，不生成任何名次。

仅对已结束的 Qualifying / Sprint / Race 生成简报；SprintQualifying 与
Practice 只识别并记录，不生成。时间为 API 提供的 UTC，统一转北京时间。
"""

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

RUN_START = time.monotonic()
RUN_BUDGET_SECONDS = 420  # 与主流程一致的总预算，超时不再发起新的网络请求

SESSION_LABELS = {
    "qualifying": "排位赛",
    "sprint": "冲刺赛",
    "race": "正赛",
}
SESSION_ENDPOINTS = {
    "qualifying": "qualifying",
    "sprint": "sprint",
    "race": "results",
}
SESSION_API_FIELDS = {
    "qualifying": "QualifyingResults",
    "sprint": "SprintResults",
    "race": "Results",
}
PODIUM_EMOJI = ["🥇", "🥈", "🥉"]


def now_utc():
    return datetime.now(timezone.utc)


def beijing_now():
    return now_utc() + timedelta(hours=8)


def _http_get_json(url, timeout=15):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; DailyNewsBriefing/1.0)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _parse_utc(value):
    """解析 API 时间字符串为 UTC datetime；如无时区信息按 UTC 处理。"""
    s = (value or "").strip()
    if not s:
        raise ValueError("empty time")
    if not s.endswith("Z") and "+" not in s:
        s = s + "Z"
    fixed = s.replace("Z", "+00:00")
    fixed = re.sub(r"(\.\d{6})\d+", r"\1", fixed)
    return datetime.fromisoformat(fixed).astimezone(timezone.utc)


def _f1_cfg(config):
    return config.get("f1_race_day", {})


def _api_base(config):
    return _f1_cfg(config).get("api_base", "https://api.jolpi.ca/ergast/f1").rstrip("/")


def load_calendar(config):
    """读取整个赛季赛历。返回 MRData.RaceTable.Races 列表。"""
    season = _f1_cfg(config).get("season", "current")
    url = "{}/{}/races.json".format(_api_base(config), season)
    data = _http_get_json(url)
    return data["MRData"]["RaceTable"].get("Races", [])


def find_recent_sessions(config, calendar, now=None):
    """找出最近 lookback_hours 内已结束的目标 Session，按结束时间升序。

    返回 (sessions, practice_count, sprint_qualifying_count)。
    Session 结束时间优先用 API 提供的实际结束时间；当前 API 不提供时，
    用配置的 session_duration_hours 估算，并在结果中标记 end_estimated=True。
    """
    cfg = _f1_cfg(config)
    lookback = timedelta(hours=cfg.get("lookback_hours", 24))
    durations = cfg.get("session_duration_hours", {})
    include = set(cfg.get("include", ["qualifying", "sprint", "race"]))
    now = now or now_utc()

    found = []
    practice_count = 0
    sprint_quali_count = 0
    season = calendar[0].get("season") if calendar else "?"

    for race in calendar:
        round_no = race.get("round")
        race_name = race.get("raceName", "")
        for key, label in (
            ("FirstPractice", "practice"),
            ("SecondPractice", "practice"),
            ("ThirdPractice", "practice"),
            ("SprintQualifying", "sprint_qualifying"),
            ("SprintShootout", "sprint_qualifying"),
            ("Qualifying", "qualifying"),
            ("Sprint", "sprint"),
            ("Race", "race"),
        ):
            if label == "race":
                # 正赛时间在 API 中通常是顶层字段（race.date / race.time），
                # 部分版本也有 Race 子对象，两种情况都兼容。
                session = race.get("Race") or {
                    "date": race.get("date", ""),
                    "time": race.get("time", ""),
                }
            else:
                session = race.get(key)
            if not session:
                continue
            if label == "practice":
                practice_count += 1
                continue
            if label == "sprint_qualifying":
                sprint_quali_count += 1
                continue
            if label not in include:
                continue
            try:
                session_date = session.get("date") or race.get("date", "")
                start = _parse_utc(session_date + "T" + (session.get("time") or "00:00:00Z"))
            except Exception:
                continue
            end = None
            estimated = False
            if session.get("end_time"):  # 预留：未来 API 若提供实际结束时间
                try:
                    end = _parse_utc(session["end_time"])
                except Exception:
                    end = None
            if end is None:
                duration_hours = float(durations.get(label, 1))
                end = start + timedelta(hours=duration_hours)
                estimated = True
            # 严格条件：session_end <= now 且 >= now - lookback
            if not (now - lookback <= end <= now):
                continue
            found.append(
                {
                    "season": season,
                    "round": round_no,
                    "race_name": race_name,
                    "session_type": label,
                    "start_utc": start,
                    "end_utc": end,
                    "end_estimated": estimated,
                    "session_id": "{}-{}-{}".format(season, round_no, label.title()),
                }
            )

    found.sort(key=lambda s: s["end_utc"])
    return found, practice_count, sprint_quali_count


def get_session_data(config, season, round_no, session_type):
    """拉取单个 Session 的官方赛果并归一化（只含事实字段）。"""
    endpoint = SESSION_ENDPOINTS[session_type]
    url = "{}/{}/{}/{}.json".format(_api_base(config), season, round_no, endpoint)
    last_error = None
    data = None
    for attempt in range(2):
        try:
            data = _http_get_json(url)
            break
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    if data is None:
        raise last_error
    race = data["MRData"]["RaceTable"]["Races"][0]
    results = []
    for r in race.get(SESSION_API_FIELDS[session_type], []):
        try:
            position = int(r.get("position"))
        except (TypeError, ValueError):
            continue
        results.append(
            {
                "position": position,
                "driver": r.get("Driver", {}).get("familyName", ""),
                "given_name": r.get("Driver", {}).get("givenName", ""),
                "constructor": r.get("Constructor", {}).get("name", ""),
                "points": int(r.get("points") or 0),
                "status": r.get("status", ""),
            }
        )
    results.sort(key=lambda x: x["position"])
    return {
        "race_name": race.get("raceName", ""),
        "season": season,
        "round": round_no,
        "session_type": session_type,
        "results": results,
    }


def _ask_deepseek(config, facts, need_names, news_items, api_key, summary_max):
    """DeepSeek 只返回中文译名与一句话总结；任何失败都返回空，由调用方兜底。"""
    cfg = _f1_cfg(config)
    api_base = config.get("api_base", "https://api.deepseek.com").rstrip("/")
    model = config.get("model", "deepseek-v4-flash")

    fact_lines = [
        "赛季/轮次: {} Round {}".format(facts["season"], facts["round"]),
        "站名: {}".format(facts["race_name"]),
        "Session: {}".format(facts["session_type"]),
    ]
    for r in facts["results"]:
        fact_lines.append(
            "P{}: {} ({}) {}分 {}".format(
                r["position"], r["driver"], r["constructor"], r["points"], r["status"] or "-"
            )
        )
    news_text = "\n".join(
        "- " + str(n.get("title_cn") or n.get("title", ""))[:80] for n in (news_items or [])[:3]
    ) or "无"
    name_keys = ", ".join(sorted(set(need_names))) if need_names else "Norris"

    prompt = (
        "你是F1中文解说员。以下是官方赛果（事实，必须严格遵守，不得修改任何名次、"
        "车手、车队、积分）：\n{facts}\n\n"
        "当天相关F1新闻（仅供参考，若与赛果冲突，一律以官方赛果为准）：\n{news}\n\n"
        "请只输出 JSON，不要输出任何其他内容：\n"
        "{{\"race_name_cn\": \"站名中文（如：匈牙利大奖赛）\", "
        "\"names\": {{\"Norris\": \"诺里斯\"}}, "
        "\"summary\": \"{summary_max}字以内的一句话中文总结，结合赛果与新闻提炼重点，"
        "不编造任何名次或事实\"}} "
        "其中 names 的键必须严格使用以下英文姓氏，且每个键给出中文译名：{keys}"
    ).format(
        facts="\n".join(fact_lines), news=news_text,
        summary_max=summary_max, keys=name_keys,
    )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": "disabled"},
        "temperature": 0.3,
        "max_tokens": 300,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api_base + "/chat/completions",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
            "User-Agent": "Mozilla/5.0 (compatible; DailyNewsBriefing/1.0)",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"].strip()
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        return {}, "", ""
    obj = json.loads(match.group(0))
    names = {k: (v or "").strip() for k, v in (obj.get("names") or {}).items()}
    summary = (obj.get("summary") or "").strip()
    if len(summary) > summary_max:
        summary = summary[:summary_max].rstrip()
    race_cn = (obj.get("race_name_cn") or "").strip()
    return names, summary, race_cn


def build_report(config, session, facts, news_items, api_key, use_ai=True):
    """组装单场短信友好简报（纯文本）。

    长度策略：核心（标题+领奖台+McLaren 排名）绝不删除；超限时先裁 AI 总结，
    再裁积分段。目标 100~180 字，硬上限 max_chars（默认 220）。
    """
    cfg = _f1_cfg(config)
    max_chars = int(cfg.get("max_chars", 220))
    summary_max = int(cfg.get("ai_summary_max_chars", 100))
    mclaren_drivers = set(cfg.get("mclaren_drivers", ["Norris", "Piastri"]))

    results = facts["results"]
    podium = results[:3]
    mclaren_rows = [
        r for r in results if r["driver"] in mclaren_drivers and r["constructor"] == "McLaren"
    ]

    names_cn, summary, race_cn = {}, "", ""
    if use_ai and api_key:
        try:
            need = [r["driver"] for r in podium] + [r["driver"] for r in mclaren_rows]
            names_cn, summary, race_cn = _ask_deepseek(
                config, facts, need, news_items, api_key, summary_max
            )
        except Exception:
            names_cn, summary, race_cn = {}, "", ""

    race_disp = race_cn if race_cn else facts["race_name"]
    title_line = "🏁 F1 {}{}".format(race_disp, SESSION_LABELS.get(session["session_type"], session["session_type"]))
    podium_lines = []
    for i, r in enumerate(podium):
        cn = names_cn.get(r["driver"], "")
        name = "{}({})".format(cn, r["driver"]) if cn else r["driver"]
        podium_lines.append("{} {}".format(PODIUM_EMOJI[i], name))

    mclaren_lines = []
    if mclaren_rows:
        mclaren_lines.append("🟠 McLaren：")
        for r in mclaren_rows:
            pos = "P{}".format(r["position"])
            if r["status"] == "Retired":
                pos += "（退赛）"
            mclaren_lines.append("{} {}".format(r["driver"], pos))

    points_lines = []
    if session["session_type"] in ("sprint", "race") and mclaren_rows:
        points_lines.append("📊 积分：")
        for r in mclaren_rows:
            points_lines.append("{} +{}".format(r["driver"], r["points"]))

    summary_lines = ["📝 " + summary] if summary else []

    text = title_line + "\n" + "\n".join(podium_lines)
    if mclaren_lines:
        text += "\n\n" + "\n".join(mclaren_lines)
    # 核心部分（标题+领奖台+McLaren）很短，正常不会超限；仍做硬保护
    if len(text) > max_chars:
        return text[:max_chars].rstrip()
    if points_lines:
        candidate = text + "\n\n" + "\n".join(points_lines)
        if len(candidate) <= max_chars:
            text = candidate
    if summary_lines:
        candidate = text + "\n\n" + "\n".join(summary_lines)
        if len(candidate) <= max_chars:
            text = candidate
    return text.strip()


def maybe_run_f1(config, api_key, news_items, f1_history, use_ai=True):
    """主流程调用入口。

    返回 (报告文本或 None, 统计日志行列表, 新生成的 session_id 列表)。
    任何失败都只影响比赛日模块，不影响普通新闻流程。
    """
    stats = []
    cfg = _f1_cfg(config)
    if not cfg.get("enabled", True):
        return None, stats, []

    try:
        calendar = load_calendar(config)
    except Exception:
        stats.append("F1 calendar: FAIL")
        return None, stats, []
    stats.append("F1 calendar: OK")

    recent, practice_count, sq_count = find_recent_sessions(config, calendar)
    stats.append("Recent sessions: {}".format(len(recent)))
    if practice_count:
        stats.append("Practice sessions found: {} (skipped)".format(practice_count))
    if sq_count:
        stats.append("SprintQualifying found: {} (skipped, no results API)".format(sq_count))
    if not recent:
        return None, stats, []

    known = set(f1_history or [])
    sections = []
    new_ids = []
    for s in recent:
        if time.monotonic() - RUN_START > RUN_BUDGET_SECONDS:
            stats.append("F1 module: time budget exceeded, stopped")
            break
        stats.append("Session type: {}".format(s["session_type"].title()))
        end_beijing = s["end_utc"] + timedelta(hours=8)
        tag = "estimated" if s["end_estimated"] else "actual"
        stats.append(
            "  End (Beijing): {} {}".format(end_beijing.strftime("%Y-%m-%d %H:%M"), tag)
        )
        if s["session_id"] in known:
            stats.append("Duplicate: skipped")
            continue
        try:
            facts = get_session_data(config, s["season"], s["round"], s["session_type"])
        except Exception:
            stats.append("Result API: FAIL, skipped")
            continue
        stats.append("Result API: OK")
        report = build_report(config, s, facts, news_items, api_key, use_ai=use_ai)
        sections.append(report)
        new_ids.append(s["session_id"])
        stats.append("F1 report: generated")

    if not sections:
        return None, stats, []
    header = "🏁 F1 比赛日特别简报"
    return header + "\n\n" + "\n\n".join(sections), stats, new_ids
