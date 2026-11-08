#!/usr/bin/env python3
"""抓 Dukascopy 的 XAU/USD 分钟线，清洗、聚合，落 xau/klines.json。

为什么走这里：datafeed.dukascopy.com 无 CORS，浏览器直连必失败；沙箱出口也不通。
只能在 Actions 里抓，落成同源静态文件给前端读。

格式（已实测）：
  路径  XAUUSD/{YYYY}/{MM}/{DD}/BID_candles_min_1.bi5，MM 是 0-based
  每条  24 字节大端 >IIIIIf：time(当日秒偏移), open, close, low, high, volume
  价格  整数 ÷ 1000
  容量  1440 条/天，休市时段是 v=0 且四价相等的填充根

聚合口径写死：1h 按 UTC 整点，4h 按 UTC 00/04/08/12/16/20，
1d 按纽约 17:00（= UTC 22:00 冬 / 21:00 夏），否则日线会跨交易日被劈成两根。

用法：
  python3 tools/fetch_xau.py                # 增量：最近 4 天
  python3 tools/fetch_xau.py --days 100     # 回填：最近 100 天
  python3 tools/fetch_xau.py --force        # 忽略已有文件，全量重抓
"""
import argparse
import datetime as dt
import json
import lzma
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

BASE = ("https://datafeed.dukascopy.com/datafeed/XAUUSD/"
        "{y}/{m:02d}/{d:02d}/BID_candles_min_1.bi5")
OUT = os.environ.get("XAU_OUT", "xau/klines.json")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

KEEP = {"1h": 2600, "4h": 700, "1d": 1100, "1w": 220}   # 通道 90 根 + 回测 800 根 + 周线 60 根预热的余量
PERIOD_SEC = {"1h": 3600, "4h": 14400}


def log(*a):
    print(*a, file=sys.stderr)


def fetch_day(day, tries=4):
    """返回 [(ts, o, c, l, h, v)]，价格已还原为整数原值（未除 1000）。

    404 是正常情况（周末/节假日没有文件），必须直接返回空、不重试：
    旧版把它当异常重试 3 次，100 天回填里 28 个周末白白烧掉一半时间，直接跑超时。
    """
    url = BASE.format(y=day.year, m=day.month - 1, d=day.day)   # 月份 0-based
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=12) as r:
                if r.status == 404:
                    return []
                body = r.read()
            if not body:
                return []
            try:
                raw = lzma.decompress(body)
            except lzma.LZMAError:
                raw = lzma.decompress(body, format=lzma.FORMAT_ALONE)
            n = len(raw) // 24
            base = int(dt.datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
            out = []
            for k in range(n):
                t, o, c, l, h, v = struct.unpack(">IIIIIf", raw[k * 24:(k + 1) * 24])
                out.append((base + t, o, c, l, h, v))
            return out
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                # 周末/节假日没有文件时，Dukascopy 返回 403 而不是 404（实测）：
                # 只认 404 会把每个周末当网络故障重试 6 次，20 分钟全耗在必然失败上。
                return []
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        if i < tries - 1:
            time.sleep(1 + 2 * i)
    if isinstance(last, urllib.error.HTTPError):
        log("  ! %s 失败：HTTP %s" % (day, last.code))
    else:
        log("  ! %s 失败：%s" % (day, type(last).__name__))
    return None


def is_filler(o, c, l, h, v):
    """占位/填充根：零量且四价完全相等。缺一条都不算，避免误杀低流动性真实根。"""
    return v == 0 and o == h and h == l and l == c


def clean(rows):
    """去填充根 + 去重（同 t 留最后一根）。返回 (rows, synthetic, dup)。"""
    seen = {}
    synthetic = dup = 0
    for ts, o, c, l, h, v in rows:
        if is_filler(o, c, l, h, v):
            synthetic += 1
            continue
        if ts in seen:
            dup += 1
        seen[ts] = (ts, o, c, l, h, v)
    return [seen[k] for k in sorted(seen)], synthetic, dup


def trade_date(ts):
    """纽约 17:00 换日：17:00 之后归到次日。"""
    local = dt.datetime.fromtimestamp(ts, NY)
    return (local - dt.timedelta(hours=17)).date()


def day_start_ts(d):
    return int(dt.datetime(d.year, d.month, d.day, 17, 0, tzinfo=NY).timestamp())


def week_start_ts(ts):
    """周线桶：UTC 周一 00:00。用周一而不是周日，与 utcWeekKey() 前端判据同源。"""
    t = dt.datetime.fromtimestamp(ts, UTC)
    mon = t - dt.timedelta(days=t.weekday())
    return int(dt.datetime(mon.year, mon.month, mon.day, tzinfo=UTC).timestamp())


BASE_DAY = "https://datafeed.dukascopy.com/datafeed/XAUUSD/{y}/BID_candles_day_1.bi5"


def fetch_year_daily(year, tries=2):
    """年度日线文件：1 个请求换一整年（实测 2024 → 366 条，价格 2062 与真实金价吻合）。

    这是唯一低成本的深度来源：逐日抓分钟线回填 500 天会被源限流（503）并在
    40 分钟超时；年文件 3 个请求就够 3 年，周线 60 根预热与 800 根回测同时满足。

    时区口径：文件里的 time 是「当年第几天 × 86400」，落到 UTC 零点。为了让 1d
    全序列同一口径（纽约 17:00 换日），这里直接换成当天纽约 17:00 的时间戳。
    """
    url = BASE_DAY.format(y=year)
    base = int(dt.datetime(year, 1, 1, tzinfo=UTC).timestamp())
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status in (403, 404):
                    return []
                body = r.read()
            if not body:
                return []
            try:
                raw = lzma.decompress(body)
            except lzma.LZMAError:
                raw = lzma.decompress(body, format=lzma.FORMAT_ALONE)
            out = []
            for k in range(len(raw) // 24):
                t, o, c, l, h, v = struct.unpack(">IIIIIf", raw[k * 24:(k + 1) * 24])
                d = dt.datetime.fromtimestamp(base + t, UTC).date()
                out.append((day_start_ts(d), o, c, l, h, v))
            return out
        except Exception as e:                            # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(2 + 3 * i)
    log("  ! 年度日线 %d 失败：%s" % (year, type(last).__name__))
    return []


def bars_to_rows(bars):
    """[[t_ms, "o","h","l","c","v"]] → aggregate() 要的 (ts, o, c, l, h, v) 元组。"""
    out = []
    for b in bars:
        out.append((int(b[0]) // 1000, int(round(float(b[1]) * 1000)),
                    int(round(float(b[4]) * 1000)), int(round(float(b[3]) * 1000)),
                    int(round(float(b[2]) * 1000)), float(b[5])))
    return out


def aggregate(rows, period):
    """rows 已清洗；返回 Binance 数组格式 [[t_ms, "o","h","l","c","v"], ...]。"""
    buckets = {}
    for ts, o, c, l, h, v in rows:
        if period == "1d":
            key = day_start_ts(trade_date(ts))
        elif period == "1w":
            key = week_start_ts(ts)
        else:
            sec = PERIOD_SEC[period]
            key = ts - ts % sec
        b = buckets.get(key)
        if b is None:
            buckets[key] = [o, h, l, c, v]
        else:
            if h > b[1]:
                b[1] = h
            if l < b[2]:
                b[2] = l
            b[3] = c
            b[4] += v
    out = []
    for k in sorted(buckets):
        o, h, l, c, v = buckets[k]
        out.append([k * 1000, "%.3f" % (o / 1000.0), "%.3f" % (h / 1000.0),
                    "%.3f" % (l / 1000.0), "%.3f" % (c / 1000.0), "%.2f" % v])
    return out


def is_closed(ts):
    """纽约时间：周六全天、周日 17:00 前、周五 17:00 后、每日 17:00–18:00 为闭市。"""
    t = dt.datetime.fromtimestamp(ts, NY)
    wd = t.weekday()                     # 0=周一
    if wd == 5:
        return True
    if wd == 6 and t.hour < 17:
        return True
    if wd == 4 and t.hour >= 17:
        return True
    return t.hour == 17


def gaps_of(bars, period):
    """返回需要断开的位置。

    kind=session：中间点落在休市区间（周末、每日结算停盘），属正常；
    kind=missing：中间点落在开市时间，说明数据缺失，要报警。

    不加这个区分的话，150 天里 105 个 gap 全部混在一起，
    真正的缺日（如 9-29）会被 21 个周末和 84 个每日停盘淹没。
    """
    step = {"1d": 86400, "1w": 7 * 86400}.get(period) or PERIOD_SEC[period]
    out = []
    for i in range(1, len(bars)):
        dt_sec = (bars[i][0] - bars[i - 1][0]) / 1000.0
        if dt_sec <= step * 1.6:
            continue
        mid = (bars[i - 1][0] + bars[i][0]) / 2000.0
        out.append({"i": i, "from": bars[i - 1][0], "to": bars[i][0],
                    "hours": round(dt_sec / 3600.0, 1),
                    "kind": "session" if is_closed(mid) else "missing"})
    return out


def merge_old(old, new):
    """以 t 为键合并，新的覆盖旧的。"""
    m = {b[0]: b for b in old}
    for b in new:
        m[b[0]] = b
    return [m[k] for k in sorted(m)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4, help="回填天数（默认 4）")
    ap.add_argument("--force", action="store_true", help="忽略已有文件")
    ap.add_argument("--retry-failed", action="store_true",
                    help="只补旧文件 window.failed 里记录的日期")
    args = ap.parse_args()

    today = dt.datetime.now(UTC).date()
    start = today - dt.timedelta(days=max(1, args.days) - 1)

    old = {}
    old_failed = []
    old_window = {}
    if os.path.exists(OUT) and not args.force:
        try:
            prev = json.load(open(OUT, encoding="utf-8"))
            old = prev.get("kl", {})
            old_window = prev.get("window") or {}
            old_failed = old_window.get("failed") or []
            log("已有 %s，做增量合并" % OUT)
        except Exception:  # noqa: BLE001
            old = {}

    rows, syn_total, dup_total = [], 0, 0
    days_ok = days_empty = 0
    failed = []

    days = []
    if args.retry_failed and old_failed:
        for s in old_failed:
            try:
                days.append(dt.date.fromisoformat(s))
            except ValueError:
                pass
        log("只补失败日期：%d 天" % len(days))
    else:
        d = start
        while d <= today:
            days.append(d)
            d += dt.timedelta(days=1)

    # 并发 4：串行 100 天要 20 分钟以上（实测超时）。并发过高会丢连接，4 是平衡点，
    # 单日内部仍有 4 次重试兜底。
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(fetch_day, days))

    # 补漏轮：首轮失败的日期用单线程、更长退避再跑一遍。
    # 实测 Dukascopy 对 Azure 出口有明显抖动，首轮 100 天里 46 天失败；
    # 不补漏的话，缺日会在图上变成「假断层」，与真实周末缺口混在一起，无法区分。
    miss = [d for d, g in zip(days, results) if g is None]
    if miss:
        log("补漏 %d 天（单线程）" % len(miss))
        t0 = time.time()

        def slow(d):
            if time.time() - t0 > 1200:      # 补漏最多 20 分钟，超了就留给下一次跑
                return None
            time.sleep(1.0)
            return fetch_day(d, tries=6)

        with ThreadPoolExecutor(max_workers=1) as ex:
            again = list(ex.map(slow, miss))
        m = dict(zip(days, results))
        m.update(dict(zip(miss, again)))
        results = [m[d] for d in days]

    for day, got in zip(days, results):
        if got is None:                      # 重试耗尽 = 真失败，记下来
            failed.append(str(day))
            continue
        if not got:                          # 404 = 休市无文件，正常
            days_empty += 1
            continue
        days_ok += 1
        c, s, du = clean(got)
        syn_total += s
        dup_total += du
        rows.extend(c)

    if not rows:
        log("本次没抓到任何数据，保留旧文件不动")
        return 1

    rows.sort()
    kl, stats = {}, {}

    # 1h / 4h 只从分钟线聚合（深历史用不到分钟级，也不需要）
    for per in ("1h", "4h"):
        bars = aggregate(rows, per)
        if old.get(per):
            bars = merge_old(old[per], bars)
        bars = bars[-KEEP[per]:]
        kl[per] = bars

    # 日线：本窗口分钟聚合 + 年度日线文件（3 个请求换 3 年，周线与回测靠它）
    daily = aggregate(rows, "1d")
    hist = []
    for y in (today.year - 2, today.year - 1, today.year):
        got = fetch_year_daily(y)
        if got:
            c, _s, _d = clean(got)
            hist.extend(c)
            log("  年度日线 %d：%d 条（清洗后 %d）" % (y, len(got), len(c)))
    if hist:
        hist_bars = aggregate(hist, "1d")
        daily = merge_old(hist_bars, daily)          # 同 t 以分钟聚合为准（更细）
    if old.get("1d"):
        daily = merge_old(old["1d"], daily)
    kl["1d"] = daily[-KEEP["1d"]:]

    # 周线从日线聚合：单一来源、口径一致，避免「周线来自 7 天连续桶」的错位
    weekly = aggregate(bars_to_rows(kl["1d"]), "1w")
    if old.get("1w"):
        weekly = merge_old(old["1w"], weekly)
    kl["1w"] = weekly[-KEEP["1w"]:]

    for per in ("1h", "4h", "1d", "1w"):
        bars = kl[per]
        gp = gaps_of(bars, per)
        stats[per] = {"bars": len(bars), "gaps": len(gp),
                      "missing": sum(1 for g in gp if g["kind"] == "missing"),
                      "first": bars[0][0] if bars else None,
                      "last": bars[-1][0] if bars else None}

    if args.retry_failed and old_window:
        # 补漏只跑几天，不能把 150 天的统计覆盖成「2 天成功」——
        # 那会让 window 看起来像刚建库，也看不出历史回填是否完整。
        win = dict(old_window)
        win["failed"] = failed[:20]
        win["failed_n"] = len(failed)
        win["retried_at"] = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        win = {"from": str(min(days) if days else start),
               "to": str(max(days) if days else today),
               "days_ok": days_ok, "days_empty": days_empty,
               "failed_n": len(failed), "failed": failed[:20]}

    doc = {
        "updated": int(time.time() * 1000),
        "src": "dukascopy",
        "symbol": "XAUUSD",
        "price_type": "bid",
        "agg": {"1h": "utc_hour", "4h": "utc_4h", "1w": "utc_week_mon",
                "1d": "ny_17_00", "1d_tz": "America/New_York"},
        "window": win,
        "sanitized": {"synthetic": syn_total, "dup": dup_total},
        "stats": stats,
        "gaps": {p: gaps_of(kl[p], p) for p in kl},
        "kl": kl,
    }
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))

    log("写入 %s  合成根=%d 重复=%d  天成功=%d 空=%d" %
        (OUT, syn_total, dup_total, days_ok, days_empty))
    for p, s in stats.items():
        log("  %-3s %4d 根  gaps=%-2d  %s → %s" % (
            p, s["bars"], s["gaps"],
            dt.datetime.fromtimestamp((s["first"] or 0) / 1000, UTC).strftime("%m-%d %H:%M"),
            dt.datetime.fromtimestamp((s["last"] or 0) / 1000, UTC).strftime("%m-%d %H:%M")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
