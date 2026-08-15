#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bark 推送自测工具（不抓新闻、不调 AI）。

用法：python tools/test_bark.py
运行后你的 iPhone 应立即收到一条测试推送。
"""

import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import sms_sender


def main():
    config_path = os.path.join(PROJECT_ROOT, "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    text = "这是一条测试推送：如果你看到这条消息，说明 Bark 已配置成功。"
    result = sms_sender.send_sms(config, text)
    if result is None:
        print("未检测到 BARK_KEY：请先在 .env 中填写（只填 key 或完整地址都可以）。")
        sys.exit(1)
    if result is True:
        print("发送成功：请查看你的 iPhone 通知。")
    else:
        print("发送失败：{}（请检查 .env 中的 BARK_KEY 是否复制完整）".format(result))
        sys.exit(1)


if __name__ == "__main__":
    main()
