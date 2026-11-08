#!/usr/bin/env python3
"""探测真实 XAU/USD 历史 K 线源在 GitHub Actions 网络下的可达性。

沙箱出口对 Yahoo / Stooq / Dukascopy 全部超时或 403，不能据此判定数据源不可用。
本脚本在 Actions runner（正常公网）上逐个试，结果写 xau/probe.json。

只读探测，不写任何业务数据；每个源独立 try，一个失败不影响其它。
"""
import json
import os
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
OUT = os.environ.get("PROBE_OUT", "xau/probe.json")
TIMEOUT = 25


def get(url, headers=None, raw=False):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
            return {
                "status": r.status,
                "ms": int((time.time() - t0) * 1000),
                "bytes": len(body),
                "body": body if raw else body.decode("utf-8", "replace"),
            }
    except urllib.error.HTTPError as e:
        return {"status": e.code, "ms": int((time.time() - t0) * 1000),
                "bytes": 0, "body": "", "error": "http_%d" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"status": 0, "ms": int((time.time() - t0) * 1000),
                "bytes": 0, "body": "", "error": type(e).__name__ + ": " + str(e)[:120]}


def yahoo(symbol, interval, rng):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s"
           "?range=%s&interval=%s&includePrePost=false" % (symbol, rng, interval))
    r = get(url, {"Accept": "application/json"})
    out = {"url": url, "status": r["status"], "ms": r["ms"], "bytes": r["bytes"],
           "error": r.get("error", "")}
    if r["status"] != 200:
        out["snippet"] = str(r["body"])[:160]
        return out
    try:
        j = json.loads(r["body"])
        res = (j.get("chart") or {}).get("result") or []
        if not res:
            out["error"] = "no result"
            out["snippet"] = json.dumps(j)[:200]
            return out
        q = res[0]
        ts = q.get("timestamp") or []
        quote = ((q.get("indicators") or {}).get("quote") or [{}])[0]
        closes = [c for c in (quote.get("close") or []) if c is not None]
        out.update({
            "bars": len(ts),
            "first": ts[0] if ts else None,
            "last": ts[-1] if ts else None,
            "first_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts[0])) if ts else "",
            "last_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts[-1])) if ts else "",
            "close_sample": closes[-1] if closes else None,
            "tz": q.get("meta", {}).get("exchangeTimezoneName", ""),
        })
        # 相邻间隔断层：用于判断周末是否真的缺 bar
        gaps = []
        for i in range(1, len(ts)):
            dt = ts[i] - ts[i - 1]
            if dt > 3600 * 26 and interval.endswith("h"):
                gaps.append({"i": i, "hours": round(dt / 3600, 1),
                             "at_utc": time.strftime("%m-%d %H:%M", time.gmtime(ts[i - 1]))})
            elif dt > 86400 * 4 and interval == "1d":
                gaps.append({"i": i, "days": round(dt / 86400, 1),
                             "at_utc": time.strftime("%m-%d", time.gmtime(ts[i - 1]))})
        out["gaps"] = gaps[-6:]
        out["gap_count"] = len(gaps)
    except Exception as e:  # noqa: BLE001
        out["error"] = "parse: " + str(e)[:120]
        out["snippet"] = r["body"][:200]
    return out


def stooq(symbol, interval):
    url = "https://stooq.com/q/d/l/?s=%s&i=%s" % (symbol, interval)
    r = get(url)
    out = {"url": url, "status": r["status"], "ms": r["ms"], "bytes": r["bytes"],
           "error": r.get("error", "")}
    if r["status"] == 200:
        lines = [x for x in r["body"].splitlines() if x.strip()]
        out["rows"] = max(0, len(lines) - 1)
        out["head"] = lines[:2]
        out["tail"] = lines[-2:]
    return out


def dukascopy(interval):
    url = ("https://freeserv.dukascopy.com/2.0/index.php?path=chart/json3"
           "&instrument=XAU/USD&offer_side=B&interval=%s"
           "&splits=true&stocks=true&time_direction=P" % interval)
    r = get(url, {"Referer": "https://www.dukascopy.com/"})
    out = {"url": url, "status": r["status"], "ms": r["ms"], "bytes": r["bytes"],
           "error": r.get("error", "")}
    out["snippet"] = str(r["body"])[:240]
    return out


def main():
    res = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sources": {
            "yahoo_xauusd_1h": yahoo("XAUUSD=X", "1h", "1mo"),
            "yahoo_xauusd_1d": yahoo("XAUUSD=X", "1d", "5y"),
            "yahoo_gcf_1h": yahoo("GC=F", "1h", "1mo"),
            "stooq_xauusd_d": stooq("xauusd", "d"),
            "stooq_xauusd_h": stooq("xauusd", "h"),
            "dukascopy_1h": dukascopy("1HOUR"),
            "gold_api_spot": (lambda r: {"url": "https://api.gold-api.com/price/XAU",
                                         "status": r["status"], "ms": r["ms"],
                                         "body": r["body"][:200],
                                         "error": r.get("error", "")})(
                get("https://api.gold-api.com/price/XAU")),
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    for k, v in res["sources"].items():
        print("%-20s status=%-4s bars=%-5s bytes=%-7s %s" % (
            k, v.get("status"), v.get("bars", "-"), v.get("bytes", "-"),
            v.get("error", "") or (v.get("first_utc", "") + " → " + v.get("last_utc", ""))))


if __name__ == "__main__":
    main()
