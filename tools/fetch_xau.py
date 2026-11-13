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
import bisect
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


TICK = ("https://datafeed.dukascopy.com/datafeed/XAUUSD/"
        "{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5")

# tick 价格闸门：XAUUSD 的 point 是 0.001，÷1000 后应落在 1000~20000 美元。
# 缩放猜错时（÷100 → 405 美元；÷10000 → 40554 美元）整段丢弃。
# 量级闸门只管数量级，挡不住同量级的错段——那由 stitch_rows_checked 的连续性校验管。
TICK_PX_MIN, TICK_PX_MAX = 1_000_000, 20_000_000


def _get(url, tries=3, timeout=12):
    """GET 一个 bi5。403/404 → b""（正常缺件）；网络故障重试后返回 None。"""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status in (403, 404):
                    return b""
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return b""
        except Exception:                                  # noqa: BLE001
            pass
        if i < tries - 1:
            time.sleep(1 + 2 * i)
    return None


def parse_ticks(raw, hour_start):
    """tick 字节 → 分钟根 [(ts, o, c, l, h, v)]，价格 ×1000 整数（与 fetch_day 同口径）。

    布局（实测）：>IIIff = time(相对该小时的毫秒), ask, bid, askVol, bidVol，20 字节。
    价格字段已是整数域（4128410 == 4128.410），所以直接除以 1000 之外的换算都错。

    为什么要做成纯函数：这是整条 T+0 补根链里唯一「格式靠推断」的一环，
    必须能被夹具用假 bi5 反复验证，不能只在 Actions 里跑一次看运气。
    """
    if not raw:
        return []
    try:
        data = lzma.decompress(raw)
    except lzma.LZMAError:
        try:
            data = lzma.decompress(raw, format=lzma.FORMAT_ALONE)
        except lzma.LZMAError:
            return []
    buckets = {}
    for k in range(len(data) // 20):
        t, ask, bid, av, bv = struct.unpack(">IIIff", data[k * 20:(k + 1) * 20])
        if ask <= 0 or bid <= 0:
            continue
        px = (ask + bid) / 2.0
        if not (TICK_PX_MIN <= px <= TICK_PX_MAX):
            continue
        ts = hour_start + t // 1000
        key = ts - ts % 60
        b = buckets.get(key)
        if b is None:
            buckets[key] = [px, px, px, px, (av + bv) / 2.0]
        else:
            if px > b[1]:
                b[1] = px
            if px < b[2]:
                b[2] = px
            b[3] = px
            b[4] += (av + bv) / 2.0
    return [(k, int(round(v[0])), int(round(v[3])), int(round(v[2])),
             int(round(v[1])), v[4]) for k, v in sorted(buckets.items())]


def stitch_rows(day_rows, tick_rows):
    """按 ts 合并：日文件段原样保留，tick 只补「日文件末根之后」的分钟。

    为什么不许覆盖重叠段：tick 取的是 (ask+bid)/2，日文件是 bid。同一分钟两边
    差 0.01–0.03%，混在一起会让历史段与新增段出现一条系统性台阶（1h 收盘突然
    跳 0.8 美元），排查看起来像行情跳空。日文件更权威，重叠一律以它为准。
    """
    cut = day_rows[-1][0] if day_rows else -1
    seen = {}
    for r in day_rows:
        seen[r[0]] = r
    for r in tick_rows:
        if r[0] > cut:
            seen[r[0]] = r
    return [seen[k] for k in sorted(seen)]


def stitch_rows_checked(day_rows, tick_rows, max_dev=0.03):
    """带连续性校验的拼接：tick 段首根开盘偏离日文件末根收盘 > 3% → 整段丢弃。

    防的是「串品种 / 上游错段」这类同量级错误：4050 跳到 6000 量级闸门放得过，
    但它会让通道与 ATR 直接错位。宁可少补一段，也不能把假数据写进序列。
    """
    if not tick_rows:
        return list(day_rows), 0
    if day_rows:
        base = day_rows[-1][4]
        head = tick_rows[0][1]
        if base and head and abs(head - base) / float(base) > max_dev:
            return list(day_rows), 1
    return stitch_rows(day_rows, tick_rows), 0


def tick_hours(last_ts, now, max_hours=30):
    """要补的小时起点：从 last_ts 所在小时，到「当前小时的前一小时」止。

    当前这小时还没走完，tick 文件只有半根；拉它会得到一个低点偏高的假根，
    留给页面用实时价推进更准。max_hours 从「现在」往回数，不是从旧末根往后数：
    产物停了一周时，补最近 30 小时比补七天前的 30 小时有用得多。
    """
    start = last_ts - last_ts % 3600
    end = now - now % 3600 - 3600
    if end <= start:
        return []
    hrs = list(range(start, end + 3600, 3600))
    if max_hours and len(hrs) > max_hours:
        hrs = hrs[-max_hours:]
    return hrs


# ---------- v7.11：tick 优先的数据新鲜度 ----------
# 现场（2026-11-13 10:51 北京，线上 v7.10.2）：
#   4H 卡「末根 20:00 收 3870.2 · 滞后 11 小时」，1H 同样；而当天 22:50 UTC
#   那次 Actions 是 success，产物 updated 23:02。跑成功了，数据还是 11 小时前。
#
# 三层原因，逐条对应下面的改法：
#   1. 当天 `BID_candles_min_1.bi5` 是**残片**：按小时异步追加，22:50 抓到的
#      只有 0–9 点、14–15 点，中间 10–13 点根本没有。→ 当天不再读日文件，
#      整段交给 tick（T+0，小时级可用）。
#   2. 旧实现只在「日文件末根之后」补 tick，补不到日文件**中间**的洞。→ tick
#      在窗口内优先，洞和尾部一起填。
#   3. `stitch_rows_checked` 拿整段比一次，任一小时价格异常就把整晚 240 根全丢
#      （线上日志「丢弃 1 段（价差异常）」）。→ 校验下沉到小时段，只丢坏的那小时。

TICK_LOOKBACK_H = 24      # 固定回看一天：当天日文件的洞最多也就是 24 小时内的洞
TICK_MAX_HOURS = 48       # 单次硬上限：产物停一周也只补最近 48 小时


def tick_window(last_ts, now, lookback_h=TICK_LOOKBACK_H, max_hours=TICK_MAX_HOURS):
    """要补的小时起点列表。

    固定回看 lookback_h 小时，**不从产物末根起**。这是 v7.11 上线后立刻踩到的
    坑：线上第一次跑显示「补根 1 小时、补洞 1 处」，而真正的 5 小时洞（11-12
    09:00 → 14:00）留在原地 —— 因为洞在末根之前，从末根起补永远够不着。

    取「窗口起点」与「末根」中更早者，是为了产物停更超过窗口时不额外留白：
    那时从末根起补，再由 max_hours 截断，代价可控。
    当前这一小时还没走完，tick 文件只有半根，一律不拉（留给页面用现价推进）。
    """
    end = now - now % 3600 - 3600
    if end <= 0:
        return []
    start_win = end - (max(1, lookback_h) - 1) * 3600
    start_last = (last_ts - last_ts % 3600) if last_ts else start_win
    start = min(start_win, start_last)
    floor = end - (max(1, max_hours) - 1) * 3600
    if start < floor:
        start = floor
    if start > end:
        return []
    return list(range(start, end + 3600, 3600))


def day_plan(today, days=4, failed=None, keep_days=30):
    """要抓日文件的日期（不含 today）。

    当天不抓：残留的日文件既断又缺，是洞的来源；tick 覆盖同一时段时更完整。
    已失败日期（30 天窗内）要重新排进来，否则一次 503 就永久丢一天。
    """
    n = max(1, int(days))
    out = [today - dt.timedelta(days=i) for i in range(n - 1, 0, -1)]
    have = set(out)
    floor = today - dt.timedelta(days=keep_days)
    for s in failed or []:
        try:
            d = dt.date.fromisoformat(s)
        except (ValueError, TypeError):
            continue
        if floor <= d < today and d not in have:
            out.append(d)
            have.add(d)
    out.sort()
    return out


def merge_sources(day_rows, tick_rows, win_from=None, max_dev=0.03):
    """合并日文件与 tick：窗口内 tick 优先，窗口外日文件为准。

    返回 (rows, stats)。stats = {tick, day, dropped, holes}
      tick   实际并入的分钟数（去重后）
      dropped 被丢弃的**小时段**数（价格与相邻日文件偏离 > max_dev）
      holes  被 tick 填上的缺口处数（前一分钟原先不存在的次数）

    为什么要分段校验：串品种/缩放错配是按小时成段出现的，按分钟逐条判会把
    正常波动误杀；按「整个 tick 窗口」判又会因为一小时异常丢掉整晚。
    以小时为粒度是这两者之间唯一站得住的粒度。
    """
    day_rows = sorted(day_rows)
    if not tick_rows:
        return day_rows, {"tick": 0, "day": len(day_rows), "dropped": 0, "holes": 0}

    win = -1 if win_from is None else int(win_from)
    times = [r[0] for r in day_rows]
    groups = {}
    for r in tick_rows:
        if r[0] < win:
            continue
        groups.setdefault(r[0] - r[0] % 3600, []).append(r)

    tick, dropped = {}, 0
    for h in sorted(groups):
        rs = groups[h]
        i = bisect.bisect_left(times, h)
        # 基准取「该小时之前最近一根」的收盘价（索引 2）：原来是索引 4（最高价），
        # 波幅大的根会把阈值抬高，等于放松了校验。
        base = day_rows[i - 1][2] if i > 0 else None
        head = rs[0][1]
        if base and head and abs(head - base) / float(base) > max_dev:
            dropped += 1
            continue
        for r in rs:
            tick[r[0]] = r

    out = {r[0]: r for r in day_rows}
    out.update(tick)
    rows = [out[k] for k in sorted(out)]
    holes = sum(1 for k in tick if (k - 60) not in out)
    return rows, {"tick": len(tick), "day": len(day_rows),
                  "dropped": dropped, "holes": holes}


def expand_days(days, failed, today, keep_days=30):
    """把 window.failed 里 keep_days 内的日期并进本次抓取。（v7.11 起 main 改用
    day_plan，本函数仅为 p116 的回归用例保留，行为不再影响线上。）

    为什么不能只靠 `--retry-failed`：Dukascopy 对 Azure 出口有明显抖动（实测
    首轮 100 天里 46 天失败）。某天连续失败、又在 4 天窗口外滑走，就永久缺失，
    图上表现为一条假的深断层，和真实周末缺口混在一起无法区分。
    30 天上限是为了不让 failed 列表无限增长把每次运行拖成回填。
    """
    floor = today - dt.timedelta(days=keep_days)
    out = list(days)
    have = set(out)
    for s in failed or []:
        try:
            d = dt.date.fromisoformat(s)
        except (ValueError, TypeError):
            continue
        if floor <= d <= today and d not in have:
            out.append(d)
            have.add(d)
    out.sort()
    return out


def fetch_recent_ticks(last_ts=None, now=None, max_hours=30, workers=4, tries=3,
                       hours=None):
    """按小时并发拉 tick，返回 (分钟根 rows, 统计)。失败的小时静默跳过。

    hours 直接给定时跳过 tick_hours 的推导（v7.11 的 tick_window 用的是
    「lookback 并集」，语义比旧的「从末根往后」更宽，不能共用一套参数）。
    """
    now = now or int(time.time())
    hrs = list(hours) if hours is not None else tick_hours(last_ts, now, max_hours)
    st = {"hours": len(hrs), "ok": 0, "empty": 0, "fail": 0}
    if not hrs:
        return [], st

    def one(h):
        d = dt.datetime.fromtimestamp(h, UTC)
        body = _get(TICK.format(y=d.year, m=d.month - 1, d=d.day, h=d.hour), tries)
        if body is None:
            return h, "fail", []
        if not body:
            return h, "empty", []
        rows = parse_ticks(body, h)
        return h, ("ok" if rows else "empty"), rows

    rows, bad = [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for h, kind, rs in ex.map(one, hrs):
            st[kind] += 1
            if kind == "fail":
                bad.append(h)
            rows.extend(rs)
    # 失败小时单独记一份：固定窗口每轮重拉，这个列表就是「下一轮能不能自愈」的观察点。
    # 只留前 8 个，产物里 window.ticks.fail_hours 直接可读，不用翻日志。
    st["fail_hours"] = [dt.datetime.fromtimestamp(h, UTC).strftime("%m-%d %H")
                        for h in bad[:8]]
    rows.sort(key=lambda r: r[0])
    return rows, st


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
                if d.weekday() >= 5:
                    # 周六是休市填充根；周日 UTC 日只有开盘后那 2 小时，和 23 小时的
                    # 交易日不可比（会把 ATR 拉低）。两者都不进日线序列。
                    continue
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
    # 日线按交易日连续性判：周五→周一差 3 天是常态，用 1.6 天阈值会把每个周末
    # 都算成缺口（实测 523 根里 105 个「缺口」全是周末）。4 天起才算真缺。
    step = {"1d": 4 * 86400, "1w": 7 * 86400}.get(period) or PERIOD_SEC[period]
    out = []
    for i in range(1, len(bars)):
        dt_sec = (bars[i][0] - bars[i - 1][0]) / 1000.0
        if dt_sec <= step * 1.6:
            continue
        mid = (bars[i - 1][0] + bars[i][0]) / 2000.0
        # 真实最长休市是圣诞/元旦那几天（≤4 天）。超过 14 天的不可能是休市，
        # 无论中点落在哪都只能算缺数据——否则 5 个月的洞会被标成「正常休市」。
        closed_ok = dt_sec <= 14 * 86400 and is_closed(mid)
        out.append({"i": i, "from": bars[i - 1][0], "to": bars[i][0],
                    "hours": round(dt_sec / 3600.0, 1),
                    "kind": "session" if closed_ok else "missing"})
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
    ap.add_argument("--rebuild-from-1h", action="store_true",
                    help="不联网：用产物里已有的 1h 重新派生 1d/1w（补历史缺段后修日线用）")
    ap.add_argument("--rebuild-daily", action="store_true",
                    help="用年度日线重建 1d/1w（不动 1h/4h）；口径变更后跑一次")
    ap.add_argument("--retry-failed", action="store_true",
                    help="只补旧文件 window.failed 里记录的日期")
    ap.add_argument("--no-ticks", action="store_true",
                    help="不拉小时 tick（只跑 T+1 日文件，排查补根问题用）")
    ap.add_argument("--ticks-only", action="store_true",
                    help="跳过日文件，只补 tick（循环刷新用：T+1 日文件一小时重抓一次就够了）")
    args = ap.parse_args()

    rebuild_daily = bool(getattr(args, "rebuild_daily", False))
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

    # 离线修复：只用产物里已有的 1h 重算 1d/1w，不联网。
    # 用途：逐日回填被限流中断后，日线缺段但 1h 还在——这时不需要再抓一遍。
    if getattr(args, "rebuild_from_1h", False):
        if not old.get("1h"):
            log("产物里没有 1h，无法派生")
            return 2
        daily = aggregate(bars_to_rows(old["1h"]), "1d")
        if old.get("1d"):
            daily = merge_old(old["1d"], daily)          # 同 t 以新派生为准
        weekly = aggregate(bars_to_rows(daily), "1w")
        out = dict(json.load(open(OUT, encoding="utf-8")))
        out["kl"] = dict(out.get("kl") or {})
        out["kl"]["1d"] = daily[-KEEP["1d"]:]
        out["kl"]["1w"] = weekly[-KEEP["1w"]:]
        out["stats"] = dict(out.get("stats") or {})
        out["gaps"] = dict(out.get("gaps") or {})
        for per in ("1d", "1w"):
            gp = gaps_of(out["kl"][per], per)
            out["gaps"][per] = gp
            out["stats"][per] = {"bars": len(out["kl"][per]), "gaps": len(gp),
                                 "missing": sum(1 for g in gp if g["kind"] == "missing"),
                                 "first": out["kl"][per][0][0], "last": out["kl"][per][-1][0]}
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        log("离线派生完成：1d %d 根 / 1w %d 根" % (len(out["kl"]["1d"]), len(out["kl"]["1w"])))
        return 0

    rows, syn_total, dup_total = [], 0, 0
    days_ok = days_empty = 0
    failed = []

    days = []
    if args.ticks_only:
        log("--ticks-only：跳过日文件，只补 tick")
    elif args.retry_failed and old_failed:
        for s in old_failed:
            try:
                d = dt.date.fromisoformat(s)
            except ValueError:
                continue
            if d != today:                 # 当天由 tick 全量提供，不走日文件
                days.append(d)
        days.sort()
        log("只补失败日期：%d 天" % len(days))
    else:
        days = day_plan(today, days=args.days, failed=old_failed)
        log("日文件计划：%d 天（%s → %s，当天由 tick 提供）" % (
            len(days), str(days[0]) if days else "-", str(days[-1]) if days else "-"))

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

    rows.sort()

    # T+0 补根（v7.11 重构）：tick 在窗口内优先，日文件负责更早的历史。
    #
    # 旧口径只补「日文件末根之后」，补不到日文件中部的洞；当天日文件本身又
    # 是残片（不是空，是断）。两件事叠起来的结果就是：运行成功、数据还停在
    # 十几小时前。现在把整个 lookback 窗口交给 tick，日文件只在窗口外兜底。
    # tick 全挂时静默退回日文件 —— 补根失败不能拖垮整包。
    tst = {"hours": 0, "ok": 0, "empty": 0, "fail": 0,
           "bars": 0, "dropped": 0, "holes": 0}
    if not args.no_ticks:
        now_ts = int(time.time())
        base = rows[-1][0] if rows else 0
        prev_1h = old.get("1h") or []
        if prev_1h:
            # 上次已补到的位置：不必重拉，否则每次都从日文件末根补起。
            # 未来时间戳的根不算（上游错数据会把窗口推到明天，直接补空）。
            pt = int(prev_1h[-1][0]) // 1000
            if pt <= now_ts:
                base = max(base, pt)
        hrs = tick_window(base, now_ts)
        # 并发 5 / 重试 4 次：实测 8 小时窗口里有 4 小时失败（503/超时），
        # 24 小时窗口放大后失败面更大；固定窗口每轮都会重拉，失败的小时下一轮能自愈。
        trs, got = fetch_recent_ticks(hours=hrs, workers=5, tries=4)
        tst.update(got)
        if trs:
            rows, mst = merge_sources(rows, trs, win_from=hrs[0])
            tst["bars"] = mst["tick"]
            tst["dropped"] = mst["dropped"]
            tst["holes"] = mst["holes"]
        if hrs:
            log("T+0 补根：%d 小时（ok=%d 空=%d 失败=%d）→ 采用 %d 根分钟线，"
                "补洞 %d 处，丢弃 %d 小时%s" % (
                    got["hours"], got["ok"], got["empty"], got["fail"],
                    tst["bars"], tst["holes"], tst["dropped"],
                    ("　失败小时：" + " ".join(got.get("fail_hours") or []))
                    if got.get("fail_hours") else ""))

    if not rows:
        log("本次既没有日文件也没有 tick，保留旧文件不动")
        return 1

    kl, stats = {}, {}

    # 1h / 4h 只从分钟线聚合（深历史用不到分钟级，也不需要）
    for per in ("1h", "4h"):
        bars = aggregate(rows, per)
        if old.get(per):
            bars = merge_old(old[per], bars)
        bars = bars[-KEEP[per]:]
        kl[per] = bars

    # 日线：本窗口分钟聚合 + 年度日线文件（3 个请求换 3 年，周线与回测靠它）
    # --ticks-only 时不重抓年度日线：它一天只变一次，循环里每 50 分钟拉 3 个文件是纯浪费。
    daily = aggregate(rows, "1d")
    hist = []
    if not args.ticks_only:
        for y in (today.year - 2, today.year - 1, today.year):
            got = fetch_year_daily(y)
            if got:
                c, _s, _d = clean(got)
                hist.extend(c)
                log("  年度日线 %d：%d 条（清洗后 %d）" % (y, len(got), len(c)))
    if hist:
        hist_bars = aggregate(hist, "1d")
        daily = merge_old(hist_bars, daily)          # 同 t 以分钟聚合为准（更细）
    if rebuild_daily:
        # 只保留「本窗口内」的旧日线：它们来自分钟聚合，会被本次重算覆盖；
        # 窗口外的旧日线若也保留，会把本次剔除掉的根（如周日短根）又带回来。
        # 不能直接丢掉全部旧日线——用 --days 4 重建时会把 150 天的分钟聚合日线一起扔掉，
        # 日线中间断 308 天，通道直接算飞（实测 mid 4423 vs 现价 4072）。
        win_ms = int(dt.datetime.combine(min(days), dt.time(0, 0), tzinfo=UTC).timestamp() * 1000) if days else 0
        keep = [b for b in (old.get("1d") or []) if b[0] >= win_ms]
        daily = merge_old(keep, daily)
    elif old.get("1d"):
        daily = merge_old(old["1d"], daily)
    kl["1d"] = daily[-KEEP["1d"]:]

    # 周线从日线聚合：单一来源、口径一致，避免「周线来自 7 天连续桶」的错位
    weekly = aggregate(bars_to_rows(kl["1d"]), "1w")
    if old.get("1w") and not rebuild_daily:
        weekly = merge_old(old["1w"], weekly)
    kl["1w"] = weekly[-KEEP["1w"]:]

    for per in ("1h", "4h", "1d", "1w"):
        bars = kl[per]
        gp = gaps_of(bars, per)
        stats[per] = {"bars": len(bars), "gaps": len(gp),
                      "missing": sum(1 for g in gp if g["kind"] == "missing"),
                      "first": bars[0][0] if bars else None,
                      "last": bars[-1][0] if bars else None}

    if (args.retry_failed or args.ticks_only) and old_window:
        # 补漏/纯 tick 轮只动最近一小段，不能把 150 天的统计覆盖成「0 天成功」——
        # 那会让 window 看起来像刚建库，也看不出历史回填是否完整。
        win = dict(old_window)
        if failed:
            win["failed"] = failed[:20]
            win["failed_n"] = len(failed)
        win["ticks"] = tst
        win["ticks_at"] = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if args.retry_failed:
            win["retried_at"] = win["ticks_at"]
    else:
        win = {"from": str(min(days) if days else start),
               "to": str(max(days) if days else today),
               "days_ok": days_ok, "days_empty": days_empty, "ticks": tst,
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
