#!/usr/bin/env python3
"""第三轮：确认 Dukascopy bi5 的周期粒度、时间戳基准与休市空档。

bi5 candle 格式（每 24 字节，大端）：
    uint32 time, uint32 open, uint32 close, uint32 low, uint32 high, float32 volume
价格 = 整数值 / 10^digits（XAUUSD digits=3）。

要回答：
  1. XAUUSD/2026/BID_candles_day_1.bi5 是不是全年日线
  2. XAUUSD/2026/BID_candles_hour_1.bi5 是不是全年小时线
  3. 分钟文件的 time 是 Unix 秒还是当日秒偏移
  4. 周六/周日文件是否存在、条数是否为 0（验证休市）
"""
import datetime
import json
import lzma
import os
import struct
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
OUT = os.environ.get("PROBE_OUT", "xau/probe3.json")
BASE = "https://datafeed.dukascopy.com/datafeed/"
DIGITS = 1000.0


def fetch(path, timeout=40):
    url = BASE + path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()[:80]


def parse(body, limit=3):
    raw = lzma.decompress(body)
    n = len(raw) // 24
    rows = []
    for i in range(min(limit, n)):
        t, o, c, l, h, v = struct.unpack(">IIIIIf", raw[i * 24:(i + 1) * 24])
        rows.append({"t": t, "o": o / DIGITS, "c": c / DIGITS,
                     "l": l / DIGITS, "h": h / DIGITS, "v": round(v, 2)})
    last = None
    if n:
        t, o, c, l, h, v = struct.unpack(">IIIIIf", raw[(n - 1) * 24:n * 24])
        last = {"t": t, "o": o / DIGITS, "c": c / DIGITS,
                "l": l / DIGITS, "h": h / DIGITS, "v": round(v, 2)}
    return n, rows, last


def probe(tag, path, res, limit=3):
    code, body = fetch(path)
    out = {"path": path, "status": code, "bytes": len(body)}
    if code == 200 and body:
        try:
            n, rows, last = parse(body, limit)
            out.update({"records": n, "first": rows, "last": last,
                        "span_hours": round((last["t"] - rows[0]["t"]) / 3600.0, 2) if last and rows else None,
                        "first_utc": _utc(rows[0]["t"]) if rows else None,
                        "last_utc": _utc(last["t"]) if last else None})
        except Exception as e:  # noqa: BLE001
            out["error"] = "lzma/parse: " + str(e)[:90]
    elif code == 200:
        out["records"] = 0          # 空文件 = 休市
    res[tag] = out


def _utc(t):
    try:
        return datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return "raw:%d" % t


def main():
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sources": {}}
    S = res["sources"]
    probe("day_2026", "XAUUSD/2026/BID_candles_day_1.bi5", S, 2)
    probe("hour_2026", "XAUUSD/2026/BID_candles_hour_1.bi5", S, 2)
    # 2026/09 的月份是 0-based（09=10月）；同时取 08（9月）做对照
    probe("min_2026_09_01", "XAUUSD/2026/09/01/BID_candles_min_1.bi5", S, 2)
    probe("min_2026_08_01", "XAUUSD/2026/08/01/BID_candles_min_1.bi5", S, 2)
    # 2026-10-03 是周六、10-04 周日（若 0-based 成立）
    probe("min_sat_1003", "XAUUSD/2026/09/03/BID_candles_min_1.bi5", S, 1)
    probe("min_sun_1004", "XAUUSD/2026/09/04/BID_candles_min_1.bi5", S, 1)
    # 2026-10-02 周五，作对照
    probe("min_fri_1002", "XAUUSD/2026/09/02/BID_candles_min_1.bi5", S, 1)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    for k, v in S.items():
        print("%-18s %s rec=%-5s %s → %s  %s" % (
            k, v.get("status"), v.get("records", "-"),
            v.get("first_utc", ""), v.get("last_utc", ""), v.get("error", "")))


if __name__ == "__main__":
    main()
