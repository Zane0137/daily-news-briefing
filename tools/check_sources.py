#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 0 工具：实测 config.json 里每个 RSS 源是否可用，并展示最新 1 条。

注意：这个工具会打印新闻标题，仅供你本人在电脑上确认新闻源使用；
它不会进入 GitHub Actions 云端流程，云端日志只会输出统计数字。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import fetch_feed, load_config, now_utc, parse_feed


def main():
    config = load_config()
    total = 0
    ok = 0
    for sec_key, sec_cfg in config["sections"].items():
        print("== {} ==".format(sec_key))
        for src in sec_cfg["sources"]:
            total += 1
            try:
                data = fetch_feed(src["url"])
                items = parse_feed(data)
                if not items:
                    print("  [{}] 解析成功但 0 条（格式可能不支持）".format(src["name"]))
                    continue
                item = items[0]
                pub = item.get("published")
                age = ""
                if pub:
                    hours = max(0, int((now_utc() - pub).total_seconds() // 3600))
                    age = "（约 {} 小时前）".format(hours)
                print("  OK  {}：{}{}".format(src["name"], item.get("title", "")[:80], age))
                print("      {}".format(item.get("link", "")))
                ok += 1
            except Exception as exc:
                print("  FAIL {}：{}：{}".format(src["name"], type(exc).__name__, str(exc)[:120]))
    print("\n可用源：{}/{}".format(ok, total))
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
