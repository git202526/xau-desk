"""一次性探测：Dukascopy 的「年-日线」「月-小时线」两种粗粒度文件是否可用。

背景：逐日抓 1 分钟文件回填 520 天会被源限流（503）并在 40 分钟超时；
若年/月级文件可用，3–48 个请求就能拿到 3 年历史，周线（需 60 根）立刻够用。

只读探测，产物写 xau/probe_period.json，不碰任何业务文件。
"""
import json
import lzma
import struct
import urllib.request
import urllib.error
import datetime as dt

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
BASE = "https://datafeed.dukascopy.com/datafeed/"
UTC = dt.timezone.utc


def grab(path):
    url = BASE + path
    out = {"path": path, "status": None, "bytes": 0, "records": 0, "error": ""}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read()
            out["status"] = r.status
        out["bytes"] = len(body)
        if not body:
            out["error"] = "empty body"
            return out
        try:
            raw = lzma.decompress(body)
        except lzma.LZMAError:
            raw = lzma.decompress(body, format=lzma.FORMAT_ALONE)
        n = len(raw) // 24
        out["records"] = n
        if n:
            head = [struct.unpack(">IIIIIf", raw[k * 24:(k + 1) * 24]) for k in range(min(2, n))]
            tail = [struct.unpack(">IIIIIf", raw[(n - 1) * 24:n * 24])]
            out["head"] = [[int(x[0]), x[1], x[2], x[3], x[4], round(x[5], 2)] for x in head]
            out["tail"] = [[int(x[0]), x[1], x[2], x[3], x[4], round(x[5], 2)] for x in tail]
    except urllib.error.HTTPError as e:
        out["status"] = e.code
        out["error"] = "http %s" % e.code
    except Exception as e:                                  # noqa: BLE001
        out["error"] = type(e).__name__
    return out


def main():
    probes = [
        "XAUUSD/2025/BID_candles_day_1.bi5",       # 年 → 日线
        "XAUUSD/2024/BID_candles_day_1.bi5",
        "XAUUSD/2026/07/BID_candles_hour_1.bi5",   # 月 → 小时线（月份 0-based）
        "XAUUSD/2026/07/BID_candles_day_1.bi5",    # 月 → 日线
        "XAUUSD/2026/07/BID_candles_week_1.bi5",   # 月 → 周线
    ]
    doc = {"at": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": {}}
    for p in probes:
        r = grab(p)
        doc["sources"][p] = r
        print(p, "->", r["status"], r["bytes"], "rec", r["records"], r["error"])
        if r.get("head"):
            print("   head", r["head"][:2], "tail", r["tail"])
    with open("xau/probe_period.json", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
