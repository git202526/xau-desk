#!/usr/bin/env python3
"""金十 MCP 一次性探测（J5）：只发最少的请求，把「文档没写清」的部分问明白。

⚠ 这是一次性工具，不参与日常抓取。J5 已经用它跑过一轮，结论写在
docs/J5-金十MCP实测记录.md：get_kline 只有分钟级、count 上限 100、窗口 24 小时，
因此主脚本 v6.58 起已停抓 K 线与报价，只留日历。
**品种代码在这里硬编码**，不读主脚本常量 —— 主脚本删掉 XAU_CODE 之后，
这个脚本仍然要能单独跑，用于日后复核。

它复核的是三件事：
  1. get_kline 的 inputSchema 里 time / count 的类型与含义没变
  2. 一次仍只返回 100 根、间隔仍是分钟级（决定它能否顶 4H / 日线）
  3. 外层结构仍是 data.klines

请求预算：tools/list ×1 + get_kline ×1 = 2 次。tools/list 是元数据请求，
按官方口径限流针对的是 tools/call，理论上不占 1500 额度；但不管占不占，这个量都可以忽略。

用法（count 上限 100，传更大只会被服务端拒，因此默认就是 100）：
    JIN10_TOKEN=*** python3 tools/probe_jin10.py

安全：token 只从环境变量读，不落盘、不打印；输出里出现的任何 sk- 开头串都会被抹掉。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_jin10 as F  # noqa: E402


def bars_of(payload):
    """把 data.klines 拆成 [{t,o,h,l,c}]，只取复核需要的字段。

    故意不调用主脚本的解析函数 —— 主脚本已经不再解析 K 线，它的 norm_kline
    随 get_kline 一起删掉了；探测脚本要能独立跑，就不能依赖那边。
    """
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        d = payload.get("data")
        rows = d.get("klines") if isinstance(d, dict) else payload.get("klines")
    else:
        rows = None
    out = []
    for it in (rows or []):
        if not isinstance(it, dict):
            continue
        try:
            t = int(it.get("time"))
            t = t * 1000 if t < 10 ** 11 else t
            out.append({"t": t, "o": float(it["open"]), "h": float(it["high"]),
                        "l": float(it["low"]), "c": float(it["close"])})
        except Exception:
            continue
    out.sort(key=lambda x: x["t"])
    return out


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

    # J5 实测上限 100；默认就取上限，不传 time（= 从当前时刻往前取）
    count = 100
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

    # ---- 1 次：get_kline，验证返回粒度与上限 ----
    # 不传 time：schema 里它是「起始 Unix 秒」，默认当前时刻，传周期名（如 "1d"）会被拒
    print("\n[probe] get_kline({code:XAUUSD, count:%d}) ..." % count)
    try:
        payload = mcp.call("get_kline", {"code": "XAUUSD", "count": count})
    except Exception as e:
        print("[probe] 失败：%s" % e)
        print("[probe] 未再重试，免得白烧额度。")
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

    bars = bars_of(ok)
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
