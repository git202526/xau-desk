"""P116 · 多周期数据 T+0 补根（Dukascopy 小时 tick）

背景：上游 `BID_candles_min_1.bi5` 是 **T+1 全量生成**，当天文件恒为空。
管线再勤也只是「昨天完整 + 今天零根」，恒定滞后 0–24 小时（平均 12 小时）。
叠加 GitHub schedule 被降频（实测 7.7 / 10.7 小时间隔），用户看到的是 25 小时。

修法：日文件继续给 T+1 历史（权威、整包），**最近 N 小时改用小时 tick 文件补**。
tick 文件按小时切分，小时内结束后即可取（T+0），把滞后压到 ≤1 小时。

这些用例只测纯函数（解析 / 聚合 / 校验 / 拼接），不碰网络：tick 的二进制格式
在这里用 lzma + struct 自己造，与 Dukascopy 的 `>IIIff` 20 字节布局一致。
"""
import datetime as dt
import lzma
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import fetch_xau as F  # noqa: E402

UTC = dt.timezone.utc
HOUR = 3600


def hour_start(y, mo, d, h):
    return int(dt.datetime(y, mo, d, h, tzinfo=UTC).timestamp())


def make_ticks(points, scale=1000.0):
    """points: [(毫秒偏移, 价格)] → bi5 字节流（>IIIff，价格 ×scale 取整）。"""
    buf = b""
    for ms, px in points:
        q = int(round(px * scale))
        buf += struct.pack(">IIIff", int(ms), q, q, 1.0, 1.0)
    return lzma.compress(buf)


def test_t230_parse_ticks_price_and_time():
    """tick 价格 ÷1000 还原，时间戳 = 小时起点 + 毫秒偏移。"""
    h0 = hour_start(2026, 11, 9, 0)
    raw = make_ticks([(0, 4055.417), (30_000, 4056.0), (59_999, 4057.2)])
    rows = F.parse_ticks(raw, h0)

    assert rows, "应解析出分钟根"
    assert rows[0][0] == h0, rows[0]
    # 价格统一 ×1000 整数域，与 fetch_day 同口径
    # 返回顺序与 fetch_day 一致：(ts, o, c, l, h, v)
    assert rows[0][1] == 4055417, rows[0]
    assert rows[-1][4] == 4057200, rows[-1]


def test_t231_parse_ticks_minute_ohlc():
    """同一分钟的多笔 tick 合成一根：首笔开、末笔收、极值取高低。"""
    h0 = hour_start(2026, 11, 9, 0)
    raw = make_ticks([
        (1_000, 4050.0), (20_000, 4060.0),      # 第 1 分钟
        (40_000, 4040.0), (59_000, 4045.0),     # 第 1 分钟，制造高低点
        (61_000, 4048.0), (119_000, 4049.0),    # 第 2 分钟
    ])
    rows = F.parse_ticks(raw, h0)

    assert len(rows) == 2, rows
    m1 = rows[0]                       # (ts, o, c, l, h, v)
    assert m1[1] == 4050000, "open 取首笔"
    assert m1[4] == 4060000, "high 取极大"
    assert m1[3] == 4040000, "low 取极小"
    assert m1[2] == 4045000, "close 取末笔"
    assert rows[1][0] == h0 + 60


def test_t232_reject_wrong_scale():
    """缩放猜错（÷100 或 ÷10000）必须整段丢弃，不能把 41.28 塞进序列。

    Dukascopy 的品类 point 各不相同；XAUUSD 是 0.001。若上游某天改了缩放，
    解析出来的价格会差 10 倍或 1000 倍 —— 那会让 MA / ATR / 通道全错，
    比缺这一段糟得多。宁可空着。
    """
    h0 = hour_start(2026, 11, 9, 0)
    raw100 = make_ticks([(0, 4055.417), (60_000, 4056.0)], scale=100.0)
    assert F.parse_ticks(raw100, h0) == []

    raw10000 = make_ticks([(0, 4055.417), (60_000, 4056.0)], scale=10000.0)
    assert F.parse_ticks(raw10000, h0) == []


def test_t233_parse_ticks_skips_zero_price():
    """休市填充的零价 tick 不能进根（低点会被写成 0，ATR 直接爆表）。"""
    h0 = hour_start(2026, 11, 9, 0)
    buf = b""
    for ms, q in [(0, 0), (1_000, 4055417), (2_000, 0), (60_000, 4056000)]:
        buf += struct.pack(">IIIff", ms, q, q, 1.0, 1.0)
    rows = F.parse_ticks(lzma.compress(buf), h0)

    assert len(rows) == 2, rows
    assert all(r[3] >= 1_000_000 for r in rows), rows


def test_t234_stitch_no_overlap_no_gap():
    """日文件末尾 + tick 补当天 → 拼接后连续，无重叠、无假断层。"""
    day_rows = [(hour_start(2026, 11, 9, 23), 4050000, 4051000, 4049000, 4050500, 10.0)]
    tick_rows = [(hour_start(2026, 11, 10, 0) + i * 60,
                  4100000, 4100100, 4099900, 4100050, 1.0) for i in range(5)]

    merged = F.stitch_rows(day_rows, tick_rows)
    ts = [r[0] for r in merged]

    assert ts == sorted(ts), "必须按时间升序"
    assert len(ts) == len(set(ts)), "不能有重复时间戳"
    assert merged[-1][0] - merged[0][0] == 4 * 60 + 3600, merged


def test_t235_stitch_drops_outlier_segment():
    """tick 段与已知收盘偏差 > 3% 时整段丢弃（防串品种 / 防缩放错配）。"""
    day_rows = [(hour_start(2026, 11, 9, 23), 4050000, 4051000, 4049000, 4050500, 10.0)]
    # 价格跑到 6000：量级闸门放得过（仍在 500~20000），但与 4050 差 48%
    tick_rows = [(hour_start(2026, 11, 10, 0), 6000000, 6001000, 5999000, 6000000, 1.0)]

    merged, dropped = F.stitch_rows_checked(day_rows, tick_rows)
    assert dropped == 1, "越界段应整段丢弃"
    assert len(merged) == 1, merged


def test_t236_stitch_keeps_normal_segment():
    """正常衔接（偏差 <3%）照常并入，不被误杀。"""
    day_rows = [(hour_start(2026, 11, 9, 23), 4050000, 4051000, 4049000, 4050500, 10.0)]
    tick_rows = [(hour_start(2026, 11, 10, 0) + i * 60,
                  4090000, 4090100, 4089900, 4090050, 1.0) for i in range(3)]

    merged, dropped = F.stitch_rows_checked(day_rows, tick_rows)
    assert dropped == 0, merged
    assert len(merged) == 4, merged


def test_t237_tick_window_bounds():
    """要拉的小时区间：从日文件末根所在小时起，到「当前小时 - 1」止。"""
    last_ts = hour_start(2026, 11, 9, 23) + 12 * 60
    now = hour_start(2026, 11, 10, 3) + 40 * 60

    hrs = F.tick_hours(last_ts, now)
    assert hrs[0] == hour_start(2026, 11, 9, 23), hrs[0]
    assert hrs[-1] == hour_start(2026, 11, 10, 2), "当前这一小时还没收，不拉"
    assert len(hrs) == 4, hrs


def test_t238_tick_window_capped():
    """补根区间要有上限（默认 30 小时），避免产物长期没更新时一次拉几百个文件。"""
    last_ts = hour_start(2026, 11, 1, 0)
    now = hour_start(2026, 11, 10, 3)

    hrs = F.tick_hours(last_ts, now, max_hours=30)
    assert len(hrs) == 30, len(hrs)
    assert hrs[-1] == hour_start(2026, 11, 10, 2), "要从现在往回数，不是从旧末根往后数"


def test_t239_no_window_when_fresh():
    """日文件已经覆盖到当前小时之前 → 不需要 tick（周末/刚跑完就是这种情况）。"""
    now = hour_start(2026, 11, 10, 3) + 40 * 60
    assert F.tick_hours(hour_start(2026, 11, 10, 2), now) == []


@pytest.mark.parametrize("scale,ok", [(1000.0, True), (100.0, False)])
def test_t240_scale_gate_matches_xauusd(scale, ok):
    """量级闸门对 XAUUSD 的 0.001 point 成立，对错配的缩放不成立。"""
    h0 = hour_start(2026, 11, 9, 0)
    rows = F.parse_ticks(make_ticks([(0, 4136.7)], scale=scale), h0)
    assert bool(rows) is ok, rows


def test_t241_expand_days_retries_failed_within_window():
    """window.failed 里的日期要并进本次抓取：一次 503 不该永久丢一天。

    Dukascopy 对 Azure 出口有明显抖动（实测首轮 100 天里 46 天失败）。
    旧逻辑只在 `--retry-failed` 时才补，正常跑只取最近 4 天；某天连续失败
    就会在 4 天后滑出窗口，从此永久缺失——图上表现为一条假的深断层。
    """
    today = dt.date(2026, 11, 10)
    days = [dt.date(2026, 11, 9), dt.date(2026, 11, 10)]

    got = F.expand_days(days, ["2026-11-05", "2026-11-08", "2026-09-01", "bad"], today)
    assert got == [dt.date(2026, 11, 5), dt.date(2026, 11, 8),
                   dt.date(2026, 11, 9), dt.date(2026, 11, 10)], got
    # 30 天外的旧失败不追（否则 failed 无限增长会把每次运行拖成回填）
    assert dt.date(2026, 9, 1) not in got


def test_t242_expand_days_dedup_and_sorted():
    """已在窗口内的日期不重复抓；输出升序。"""
    today = dt.date(2026, 11, 10)
    days = [dt.date(2026, 11, 10), dt.date(2026, 11, 9)]
    got = F.expand_days(days, ["2026-11-10", "2026-11-09"], today)
    assert got == [dt.date(2026, 11, 9), dt.date(2026, 11, 10)], got
