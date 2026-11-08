#!/usr/bin/env python3
"""第二轮探测：Dukascopy 原始数据通道 + metals.live。

第一轮结论：Yahoo 429、Stooq 是 JS 挑战页、Dukascopy freeserv json3 返回 error([null])。
本轮改测：
  1. Dukascopy freeserv 的品种列表（确认 XAU/USD 在不在）
  2. json3 加 jsonp 回调名、instrument 换写法
  3. Dukascopy datafeed 的 bi5 原始 candle 文件（LZMA 压缩，行业标准，历史完整）
     注意：路径中的月份是 0-based（00=1月，09=10月）
  4. metals.live 现货接口
"""
import json
import os
import struct
import time
import urllib.error
import urllib.request
import lzma

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
OUT = os.environ.get("PROBE_OUT", "xau/probe2.json")


def get(url, headers=None, timeout=25):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return {"status": r.status, "ms": int((time.time() - t0) * 1000),
                    "bytes": len(body), "body": body}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "ms": int((time.time() - t0) * 1000),
                "bytes": 0, "body": b"", "error": "http_%d" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"status": 0, "ms": int((time.time() - t0) * 1000),
                "bytes": 0, "body": b"", "error": type(e).__name__ + ": " + str(e)[:120]}


def text(r, n=240):
    return r["body"].decode("utf-8", "replace")[:n] if r["bytes"] else ""


def try_bi5(path):
    """Dukascopy bi5：12 字节头（time/ask/bid/askvol/bidvol，大端）+ LZMA。
    只验证能不能下载与解压，不解析全部。"""
    url = "https://datafeed.dukascopy.com/datafeed/" + path
    r = get(url, timeout=30)
    out = {"url": url, "status": r["status"], "bytes": r["bytes"],
           "error": r.get("error", "")}
    if r["status"] == 200 and r["bytes"] > 0:
        try:
            raw = lzma.decompress(r["body"])
            n = len(raw) // 20
            out["unpacked"] = len(raw)
            out["records"] = n
            if n:
                t, ask, bid = struct.unpack(">III", raw[:12])
                out["first"] = {"t": t, "ask": ask / 1000.0, "bid": bid / 1000.0}
            # 记录跨度，确认是 1 分钟还是 1 小时
            out["ok"] = True
        except Exception as e:  # noqa: BLE001
            out["ok"] = False
            out["error"] = "lzma: " + str(e)[:80]
    return out


def main():
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sources": {}}
    S = res["sources"]

    r = get("https://freeserv.dukascopy.com/2.0/index.php?path=common/instruments")
    S["duka_instruments"] = {"status": r["status"], "bytes": r["bytes"],
                             "has_xau": "XAU" in text(r, 20000),
                             "snippet": text(r, 200), "error": r.get("error", "")}

    for tag, ins in (("xauusd_slash", "XAU/USD"), ("xauusd_plain", "XAUUSD")):
        url = ("https://freeserv.dukascopy.com/2.0/index.php?path=chart/json3"
               "&instrument=%s&offer_side=B&interval=1HOUR&splits=true&stocks=true"
               "&time_direction=P&jsonp=_callbacks____" % ins)
        r = get(url)
        S["duka_" + tag] = {"status": r["status"], "bytes": r["bytes"],
                            "snippet": text(r, 200), "error": r.get("error", "")}

    # bi5：日线 candle 按天存，月份 0-based；1 小时 tick 文件按小时存
    S["bi5_day_oct"] = try_bi5("XAUUSD/2026/09/01/BID_candles_day_1.bi5")
    S["bi5_min_oct"] = try_bi5("XAUUSD/2026/09/01/BID_candles_min_1.bi5")
    S["bi5_hour_tick"] = try_bi5("XAUUSD/2026/09/01/10h_ticks.bi5")

    r = get("https://api.metals.live/v1/spot")
    S["metals_live"] = {"status": r["status"], "bytes": r["bytes"],
                        "snippet": text(r, 200), "error": r.get("error", "")}

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    for k, v in S.items():
        print("%-22s status=%-4s bytes=%-7s ok=%-5s %s" % (
            k, v.get("status"), v.get("bytes"), v.get("ok", "-"),
            (v.get("error") or "")[:70]))


if __name__ == "__main__":
    main()
