#!/usr/bin/env python3
"""金十 MCP 一次性探测（J5）：只发最少的请求，把「文档没写清」的部分问明白。

要回答三个问题：
  1. get_kline 的 inputSchema 里 time / count 到底是什么类型、什么含义
  2. 一次能返回几根 K 线（文档只给了字段名，没给上限）
  3. 真实响应的外层结构是不是文档说的 data.klines

请求预算：tools/list ×1 + get_kline ×1 = 2 次。tools/list 是元数据请求，
按官方口径限流针对的是 tools/call，理论上不占 1500 额度；但不管占不占，这个量都可以忽略。

用法：
    JIN10_TOKEN=*** python3 tools/probe_jin10.py
    JIN10_TOKEN=*** python3 tools/probe_jin10.py --count 1000

安全：token 只从环境变量读，不落盘、不打印；输出里出现的任何 sk- 开头串都会被抹掉。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_jin10 as F  # noqa: E402


def redact(obj):
    """把输出里任何像 token 的片段抹掉，防止误打印。"""
    txt = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1)
    for line in txt.splitlines():
        if "sk-" in line:
            txt = txt.replace(line, "  <已脱敏>")
    return txt


def main():
    token = os.environ.get("JIN10_TOKEN", "").strip()
    if not token:
        print("缺少 JIN10_TOKEN", file=sys.stderr)
        return 2

    count = 1000
    for i, a in enumerate(sys.argv):
        if a == "--count" and i + 1 < len(sys.argv):
            count = int(sys.argv[i + 1])

    mcp = F.Mcp(F.MCP_URL, token)
    print("[probe] initialize ...")
    info = mcp.initialize()
    print("[probe] serverInfo:", json.dumps(info.get("serverInfo") or {}, ensure_ascii=False))
    print("[probe] protocolVersion:", info.get("protocolVersion"))

    # ---- 1 次：tools/list ----
    print("\n[probe] tools/list ...")
    tools = mcp.list_tools()
    for t in tools:
        name = t.get("name")
        if name not in ("get_kline", "get_quote", "list_calendar"):
            continue
        print("\n--- %s ---" % name)
        print("description:", (t.get("description") or "").strip()[:200])
        print("inputSchema:", redact(t.get("inputSchema") or {}))

    # ---- 1 次：get_kline，同时验参数语义与返回上限 ----
    print("\n[probe] get_kline({code:XAUUSD, time:1d, count:%d}) ..." % count)
    try:
        payload = mcp.call("get_kline", {"code": F.XAU_CODE, "time": "1d", "count": count})
    except Exception as e:
        print("[probe] 失败：%s" % e)
        print("[probe] 说明 time=1d 不被接受，或 count 太大。未再重试，免得白烧额度。")
        return 1

    ok, src = F.unwrap(payload)
    print("[probe] 取值来源:", src)
    if isinstance(ok, dict):
        print("[probe] 顶层键:", sorted(ok.keys()))
        for k, v in ok.items():
            if isinstance(v, list):
                print("[probe]   %s: 数组 %d 项" % (k, len(v)))
                if v:
                    print("[probe]     首项键:", sorted(v[0].keys()) if isinstance(v[0], dict) else type(v[0]))
                    print("[probe]     首项:", redact(v[0]))
                    print("[probe]     末项:", redact(v[-1]))
            else:
                print("[probe]   %s = %r" % (k, v))

    bars = F.norm_kline(ok)
    print("\n[probe] 归一化后 bars: %d 根" % len(bars))
    if bars:
        print("[probe] 首根:", bars[0])
        print("[probe] 末根:", bars[-1])
        step = bars[1]["t"] - bars[0]["t"] if len(bars) > 1 else 0
        print("[probe] 相邻间隔: %d 秒" % (step // 1000))
        print("[probe] 首末跨度: %.1f 天" % ((bars[-1]["t"] - bars[0]["t"]) / 86400000.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
