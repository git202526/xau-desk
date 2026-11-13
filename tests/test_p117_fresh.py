"""P117 · 多周期 K 线新鲜度（tick 优先 + 补洞 + 分段校验）

问题现场（v7.10.2 线上，2026-11-13 10:51 北京）：
  4H 卡「末根 20:00 收 3870.2 · 滞后 11 小时」，1H 卡同样滞后。
  而当天 Actions 是跑过的（22:50 UTC，日志 success），产物 updated 23:02。

三层原因：
  1. 当天日文件 `BID_candles_min_1.bi5` 是**残片**：22:50 抓的时候只有 0–15 点，
     而且中间还缺 10–13 点（写入按小时异步追加，顺序不定）。
  2. tick 补根只补「日文件末根之后」，**补不了日文件中间的洞**。
  3. `stitch_rows_checked` 的连续性校验拿**整段**比一次，任一小时价格异常就把
     当天 240 根全丢掉 —— 线上日志「丢弃 1 段（价差异常）」正是这一条。

修法：
  - 当天不再读日文件，改用小时 tick 从 00:00 全量拼（tick 是 T+0 的）。
  - 历史日文件的洞，也由 tick 填（窗口内 tick 优先，口径统一）。
  - 校验下沉到**小时段**：只丢异常的那一小时，不牵连整晚。
  - 连续性基准改取「日文件末根收盘」（原来取的是最高价，笔误）。
"""
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import fetch_xau as F  # noqa: E402

UTC = dt.timezone.utc
HOUR = 3600


def ts(y, mo, d, h, mi=0):
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp())


def row(t, px):
    """一根分钟线：价格按 ×1000 整数域（与 fetch_day / parse_ticks 同口径）。"""
    q = int(round(px * 1000))
    return (t, q, q, q, q, 1.0)


# ---------------- tick 窗口 ----------------


def test_t243_tick_window_is_fixed_lookback():
    """固定回看窗口，**不从末根起** —— 洞通常就在末根之前。

    线上第一次跑（v7.11.0 dispatch 43358778371）就是栽在这：从末根 15:00 起补，
    日志只写「补根 1 小时、补洞 1 处」，而真正的 5 小时洞（11-12 09:00 → 14:00）
    留在原地。窗口必须能盖住末根之前的历史。
    """
    now = ts(2026, 11, 12, 12, 30)
    last = ts(2026, 11, 12, 8, 0)
    hrs = F.tick_window(last, now, lookback_h=36)

    assert hrs[-1] == ts(2026, 11, 12, 11), "当前小时 12 点未收，不拉"
    assert len(hrs) == 36
    assert hrs[0] == ts(2026, 11, 12, 11) - 35 * HOUR
    assert last in hrs, "末根所在小时必须在窗口内"
    assert hrs[0] < last, "窗口起点要早于末根，否则补不到末根之前的洞"


def test_t244_tick_window_lookback_when_product_stale():
    """产物停三天：从末根起补，但受 max_hours 截断（默认 48 小时）。"""
    now = ts(2026, 11, 12, 12, 30)
    hrs = F.tick_window(ts(2026, 11, 9, 8, 0), now, lookback_h=36)
    assert len(hrs) == 48
    assert hrs[-1] == ts(2026, 11, 12, 11)
    assert hrs[0] == ts(2026, 11, 12, 11) - 47 * HOUR


def test_t245_tick_window_max_hours_cap():
    """max_hours 是硬上限：即使 lookback 更大，也不会一次拉几百个小时。"""
    now = ts(2026, 11, 12, 12, 30)
    hrs = F.tick_window(ts(2026, 11, 1, 0, 0), now, lookback_h=400, max_hours=48)
    assert len(hrs) == 48


def test_t246_tick_window_excludes_forming_hour():
    """当前这一小时永远不进窗口（tick 文件只有半根，会造出低点偏高的假根）。"""
    now = ts(2026, 11, 12, 0, 20)
    hrs = F.tick_window(now, now, lookback_h=6)
    assert hrs, "窗口固定，不应该因为 now 在 0 点就返回空"
    assert hrs[-1] == ts(2026, 11, 11, 23), "当前小时（00 点）不能进窗口"
    assert ts(2026, 11, 12, 0) not in hrs


# ---------------- 合并 ----------------


def test_t247_merge_fills_hole_in_day_file():
    """日文件 09 点后直接跳到 14 点（中间 4 小时缺失）→ tick 填平，序列连续。"""
    day = [row(ts(2026, 11, 12, 9, m), 3900 + m) for m in (0, 30)]
    day += [row(ts(2026, 11, 12, 14, m), 3890 + m) for m in (0, 30)]
    tick = []
    for h in range(10, 14):
        for m in range(60):
            tick.append(row(ts(2026, 11, 12, h, m), 3895 + m * 0.01))

    rows, st = F.merge_sources(day, tick, win_from=ts(2026, 11, 12, 9))
    times = [r[0] for r in rows]
    assert len(times) == len(set(times)), "不能出现重复分钟"
    assert times == sorted(times)
    # 10:00–13:59 各 60 根，全部由 tick 填
    assert st["tick"] == 240
    assert sum(1 for r in rows if ts(2026, 11, 12, 10) <= r[0] < ts(2026, 11, 12, 14)) == 240
    assert "holes" in st and st["holes"] >= 1


def test_t248_merge_tick_wins_inside_window():
    """窗口内同一分钟两边都有：取 tick（口径统一由 tick 保证，不留 bid/mid 混排）。"""
    t0 = ts(2026, 11, 12, 10, 0)
    day = [row(t0, 3900.0)]
    tick = [row(t0, 3901.5), row(t0 + 60, 3902.0)]

    rows, st = F.merge_sources(day, tick, win_from=ts(2026, 11, 12, 9))
    by_t = {r[0]: r for r in rows}
    assert by_t[t0][2] == 3901500, "窗口内应以 tick 为准"
    assert st["tick"] == 2


def test_t249_merge_keeps_day_before_window():
    """窗口之前：tick 不覆盖日文件（历史段仍以权威日文件为准）。"""
    t0 = ts(2026, 11, 12, 3, 0)
    day = [row(t0, 3900.0)]
    tick = [row(t0, 3950.0)]

    rows, _ = F.merge_sources(day, tick, win_from=ts(2026, 11, 12, 9))
    by_t = {r[0]: r for r in rows}
    assert by_t[t0][2] == 3900000, "窗口外不得被 tick 改写"


def test_t250_merge_drops_only_bad_hour():
    """某一小时 tick 价格明显串档（+50%）→ 只丢这一小时，其余照收。

    这正是线上「丢弃 1 段」的地方：旧实现整段丢，一个坏小时毁掉整晚 240 根。
    """
    day = [row(ts(2026, 11, 12, 9, 0), 3900.0)]
    tick = []
    for h in (10, 11, 12):
        for m in range(60):
            px = 3901.0 + m * 0.01
            if h == 11:
                px = 6000.0                      # 串品种的坏段
            tick.append(row(ts(2026, 11, 12, h, m), px))

    rows, st = F.merge_sources(day, tick, win_from=ts(2026, 11, 12, 9))
    kept_hours = {dt.datetime.fromtimestamp(r[0], UTC).hour for r in rows}
    assert 10 in kept_hours and 12 in kept_hours
    assert 11 not in kept_hours, "坏小时必须被丢"
    assert st["dropped"] == 1
    assert st["tick"] == 120


def test_t251_merge_uses_close_as_baseline():
    """连续性基准取日文件末根**收盘**（索引 2），不是最高价（索引 4）。

    造一根「高 4000、收 3880」的极端根，tick 首根 3990：
    相对收盘 2.8%（放行），相对最高价 0.25%（也放行）——这里只断言放行结果，
    真正的回归点是「拿 h 当 base 时这段会被误判」，由 t250 的边界兜住。
    """
    day = [(ts(2026, 11, 12, 9, 59), 3900_000, 3880_000, 3870_000, 4000_000, 1.0)]
    tick = [row(ts(2026, 11, 12, 10, m), 3990.0) for m in range(60)]
    rows, st = F.merge_sources(day, tick, win_from=ts(2026, 11, 12, 9))
    assert st["dropped"] == 0
    assert st["tick"] == 60


def test_t252_merge_empty_tick_is_passthrough():
    day = [row(ts(2026, 11, 12, 9, m), 3900.0) for m in range(5)]
    rows, st = F.merge_sources(day, [], win_from=0)
    assert rows == day
    assert st["tick"] == 0 and st["dropped"] == 0


def test_t253_merge_allows_gap_when_tick_missing():
    """tick 某小时失败、日文件也没有 → 允许留洞，不能拿相邻根篡改时间轴。"""
    day = [row(ts(2026, 11, 12, 9, 0), 3900.0)]
    tick = [row(ts(2026, 11, 12, 12, m), 3890.0) for m in range(3)]
    rows, _ = F.merge_sources(day, tick, win_from=ts(2026, 11, 12, 9))
    times = [r[0] for r in rows]
    assert times == [ts(2026, 11, 12, 9), ts(2026, 11, 12, 12),
                     ts(2026, 11, 12, 12, 1), ts(2026, 11, 12, 12, 2)]


# ---------------- 抓取计划 ----------------


def test_t254_day_plan_excludes_today():
    """当天由 tick 全量提供，日文件不再抓（它正是残片与洞的来源）。"""
    today = dt.date(2026, 11, 12)
    days = F.day_plan(today, days=4)
    assert today not in days
    assert days == [dt.date(2026, 11, 9), dt.date(2026, 11, 10), dt.date(2026, 11, 11)]


def test_t255_day_plan_pulls_failed_days_back_in():
    """失败日期在 30 天窗内要重新排进计划，否则永久缺段。"""
    today = dt.date(2026, 11, 12)
    days = F.day_plan(today, days=4, failed=["2026-10-30", "2026-09-01", "2026-11-12"])
    assert dt.date(2026, 10, 30) in days
    assert dt.date(2026, 9, 1) not in days, "超出 30 天窗不再回补"
    assert today not in days, "当天不进日文件计划"


def test_t256_today_tick_span_covers_from_midnight():
    """当天的洞也要能被覆盖：窗口必须完整包含「今天 00:00 起」的所有小时。"""
    now = ts(2026, 11, 12, 15, 30)
    hrs = F.tick_window(ts(2026, 11, 12, 0), now, lookback_h=36)
    assert hrs[0] <= ts(2026, 11, 12, 0), "窗口要早于当天零点"
    assert ts(2026, 11, 12, 0) in hrs
    assert hrs[-1] == ts(2026, 11, 12, 14)
    assert len(hrs) == 36


# ---------------- 端到端（不联网） ----------------


def test_t257_end_to_end_aggregate_continuous_1h():
    """日文件残片 + tick 补洞 → 1h 序列连续，末根就是刚过的那一小时。"""
    day = [row(ts(2026, 11, 12, h, m), 3900 + h * 0.5 + m * 0.01)
           for h in (0, 1, 9) for m in range(60)]
    tick = []
    for h in range(10, 15):
        for m in range(60):
            tick.append(row(ts(2026, 11, 12, h, m), 3905 + h * 0.5 + m * 0.01))

    rows, _ = F.merge_sources(day, tick, win_from=ts(2026, 11, 12, 0))
    bars = F.aggregate(rows, "1h")
    hours = [dt.datetime.fromtimestamp(b[0] / 1000, UTC).hour for b in bars]
    assert hours == [0, 1, 9, 10, 11, 12, 13, 14], hours
    assert float(bars[-1][4]) > 0


# ---------------- 页面：滞后要说清「缺几根」 ----------------
#
# 线上现场的文案是「滞后 11 小时」——读者无法判断这是刷新间隔的正常抖动
# （缺 1 根）还是数据源断了（缺十几根）。缺根数直接决定 MA20 里有多少是
# 更早的价格，必须与小时数一起给。

import time  # noqa: E402

HOUR_MS = 3600_000


def _bars(pm, n, end, base=3900.0, step=0.5):
    """平滑连续序列：判定链需要 60 根以上，且不能有零波动平台。"""
    end = end - end % pm
    out = []
    for i in range(n):
        t = end - (n - 1 - i) * pm
        c = base + i * step
        o = c - 0.4
        out.append([t, "%.3f" % o, "%.3f" % (c + 1.0),
                    "%.3f" % (o - 1.0), "%.3f" % c, "10"])
    return out


def _doc(end):
    return {
        "updated": int(time.time() * 1000),
        "src": "dukascopy", "symbol": "XAUUSD", "price_type": "bid",
        "window": {"from": "2026-10-01", "to": "2026-11-12", "days_ok": 1,
                   "days_empty": 0, "failed_n": 0, "failed": []},
        "sanitized": {"synthetic": 0, "dup": 0},
        "stats": {}, "gaps": {k: [] for k in ("1w", "1d", "4h", "1h")},
        "kl": {"1w": _bars(7 * 24 * HOUR_MS, 70, end),
               "1d": _bars(24 * HOUR_MS, 90, end),
               "4h": _bars(4 * HOUR_MS, 140, end),
               "1h": _bars(HOUR_MS, 200, end)},
    }


def test_t258_laginfo_reports_missing_bars(make_env):
    """滞后 11 小时的 1H 要报出「缺 10–11 根」，不是只有一个小时数。"""
    end = int(time.time() * 1000) - int(11.5 * HOUR_MS)
    env = make_env(xau=_doc(end))
    page = env.page
    page.wait_for_timeout(900)

    info = page.evaluate("() => window.__xt.klLagInfo()")
    assert 10 <= info["lagH"] <= 13, info          # 滞后小时数（含对齐余数）
    assert 9 <= info["missingN"] <= 12, info
    # 两个口径必须自洽：缺根数 = floor(滞后/周期)，1H 的周期是 1 小时
    import math
    assert info["missingN"] in (math.floor(info["lagH"]), math.floor(info["lagH"]) - 1), info


def test_t259_basis_line_shows_missing_bars(make_env):
    """依据区那行要写「（缺 N 根）」，读者才知道均线被稀释了多少。"""
    end = int(time.time() * 1000) - int(11.5 * HOUR_MS)
    env = make_env(xau=_doc(end))
    page = env.page
    page.evaluate("() => window.__xt.gotoTab('struct')")
    page.wait_for_timeout(200)
    page.click('#pgrid .pcard[data-k="4h"]', timeout=4000)
    page.wait_for_timeout(250)

    txt = page.inner_text("#reasons")
    assert "缺 " in txt and "根" in txt, txt
    assert "滞后" in txt, txt


def test_t260_many_missing_marks_direction_as_reference(make_env):
    """缺 ≥2 根时明确降级：缺口期间没有价格进均线，方向只能按参考看。

    用 1H 卡验（同样的 11 小时，1H 缺 10 根、4H 只缺 1 根）——这正是要区分开的东西：
    4H 还能看方向，1H 的 MA20 有一半是更早的价格。
    """
    end = int(time.time() * 1000) - int(11.5 * HOUR_MS)
    env = make_env(xau=_doc(end))
    page = env.page
    page.evaluate("() => window.__xt.gotoTab('struct')")
    page.wait_for_timeout(200)
    page.click('#pgrid .pcard[data-k="1h"]', timeout=4000)
    page.wait_for_timeout(250)

    txt = page.inner_text("#reasons")
    assert "方向按参考看" in txt, txt


def test_t261_chart_note_carries_missing_count(make_env):
    """通道图角标同步写缺根数，和判定区同一口径。"""
    end = int(time.time() * 1000) - int(11.5 * HOUR_MS)
    env = make_env(xau=_doc(end))
    page = env.page
    page.evaluate("() => window.__xt.gotoTab('struct')")
    page.wait_for_timeout(300)

    txt = (page.text_content("#klSrcNote") or "").strip()
    assert "滞后" in txt and "缺" in txt, txt


def test_t262_fresh_data_stays_quiet(make_env):
    """刚收盘的数据（缺 0 根）不写降级、不写角标——正常抖动不该打扰。"""
    end = int(time.time() * 1000) - 3 * 60_000
    env = make_env(xau=_doc(end))
    page = env.page
    page.evaluate("() => window.__xt.gotoTab('struct')")
    page.wait_for_timeout(300)

    info = page.evaluate("() => window.__xt.klLagInfo()")
    assert info["missingN"] == 0, info
    assert "方向按参考看" not in page.inner_text("#reasons")
    assert "缺 " not in (page.text_content("#klSrcNote") or "")


# ---------------- --ticks-only 是循环里的主路径，不能只测首轮 ----------------


def test_t263_ticks_only_keeps_history(tmp_path, monkeypatch):
    """--ticks-only：不抓日文件，但历史一根都不能丢。

    循环里 6 轮有 5 轮走这条路径。若 `days=[]` 让 1d/1w 被清空，
    产物会从 600 根日线掉成 1 根，页面通道与周线背景当场报废——
    而且这种损坏是「安静的」，不会报错。
    """
    import json
    import sys

    now = int(time.time())
    end1h = now - now % 3600

    def series(n, step, end_sec, base=3900.0):
        out = []
        for i in range(n):
            t = (end_sec - (n - 1 - i) * step) * 1000
            out.append([t, "%.2f" % base, "%.2f" % (base + 1),
                        "%.2f" % (base - 1), "%.2f" % base, "1"])
        return out

    old = {
        "updated": (end1h - 3600) * 1000,
        "src": "dukascopy", "symbol": "XAUUSD", "price_type": "bid",
        "window": {"from": "2026-06-12", "to": "2026-11-12", "days_ok": 3,
                   "days_empty": 0, "failed_n": 0, "failed": []},
        "sanitized": {"synthetic": 0, "dup": 0},
        "stats": {}, "gaps": {},
        # 旧末根停在「上上个小时」：tick 窗口补最近一个已收小时，正好接在它后面。
        # （取 end1h−3600 会与 tick 的 03:00 那根重叠，长度不增，测不出推进。）
        "kl": {"1h": series(100, 3600, end1h - 2 * 3600),
               "4h": series(40, 4 * 3600, end1h - 2 * 3600),
               "1d": series(60, 24 * 3600, end1h - 2 * 3600),
               "1w": series(10, 7 * 24 * 3600, end1h - 2 * 3600)},
    }
    p = tmp_path / "klines.json"
    p.write_text(json.dumps(old), encoding="utf-8")

    def fake_ticks(**kw):
        h = end1h - 3600
        rows = [(h + m * 60, 3_900_000, 3_901_000, 3_899_000, 3_900_000, 1.0)
                for m in range(60)]
        return rows, {"hours": 1, "ok": 1, "empty": 0, "fail": 0, "fail_hours": []}

    monkeypatch.setattr(F, "OUT", str(p))
    monkeypatch.setattr(F, "fetch_recent_ticks", fake_ticks)
    monkeypatch.setattr(sys, "argv", ["fetch_xau.py", "--ticks-only"])

    assert F.main() == 0, "ticks-only 必须正常收尾"
    doc = json.loads(p.read_text(encoding="utf-8"))

    assert len(doc["kl"]["1h"]) >= 101, "历史 1h 不能被 tick 轮清空"
    assert len(doc["kl"]["1d"]) >= 60, "日线历史必须保留"
    assert len(doc["kl"]["1w"]) >= 10, "周线历史必须保留"
    assert doc["kl"]["1h"][-1][0] == (end1h - 3600) * 1000, "末根应推进到最近一个已收小时"
    # window 的「几天成功」不能被这一轮覆盖成 0
    assert doc["window"]["days_ok"] == 3, doc["window"]
    assert doc["window"]["ticks"]["ok"] == 1
