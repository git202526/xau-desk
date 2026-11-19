"""P120 · 全周期自动更新（周线分桶 + 当前小时进窗口 + 单实例写盘）

现场（2026-11-16 13:14 北京，线上 v7.12.1）：
  · 周线卡「滞后 · 末根 11-09」，可当天的日线明明已经有 11-16；
  · 1H 卡「滞后 · 末根 10:00」，11/12/13 三根都不在；
  · 产物 updated 停在 03:09Z，下一次提交是 05:18Z —— **2 小时 9 分没有任何写入**。

三条独立成因，逐条对应下面的用例：
  1. `week_start_ts()` 直接对日线 ts 取 UTC weekday。日线 ts 是纽约 17:00 换日后的
     值（周一交易日的 ts 落在「周日 22:00 UTC」），于是周一那天的日线永远被并进
     **上一周**的桶：本周周根永不生成、上周周根 close 被最新价天天空顶。
  2. `tick_window()` 的 end 是「当前小时 − 1」，页面永远差一根；再叠加一轮没推上去，
     就是 3 小时。
  3. workflow 的 `cancel-in-progress: false` 让两个 run 同时 in_progress，两边抢着
     `git push` 同一个 klines.json，rebase 打架 → 连续四轮丢失。
"""
import datetime as dt
import json
import sys

import pytest

sys.path.insert(0, "tools")
import fetch_xau as F  # noqa: E402

UTC = dt.timezone.utc
HOUR = 3600
DAY = 86400


def ts(y, mo, d, h=0, mi=0):
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp())


# ---------------- 周线分桶 ----------------


def test_t295_week_start_of_monday_trade_day():
    """周一交易日的日线 ts（周日 22:00 UTC）必须归到**本周**一。

    这是 2026-11-16 现场的直接回归：旧实现返回 11-09，本周周根永远不生成。
    """
    monday_trade = ts(2026, 11, 15, 22)          # 冬令时：周一交易日开盘
    assert F.week_start_ts(monday_trade) == ts(2026, 11, 16)


def test_t296_week_start_of_friday_trade_day():
    """周五交易日的日线 ts（周四 22:00 UTC）归到本周一，不能跨到下一周。"""
    friday_trade = ts(2026, 11, 12, 22)
    assert F.week_start_ts(friday_trade) == ts(2026, 11, 9)


def test_t297_weekly_last_bar_is_this_week():
    """用真实日线序列聚合：末根是本週一，不是上週一。

    线上 1d 的末三根是 11-11 / 11-12 / 11-15（22:00 UTC），分别对应
    周四 / 周五 / 下周一三个交易日。
    """
    days = [dt.date(2026, 11, 9) + dt.timedelta(days=i) for i in range(7)]
    rows = []
    for i, d in enumerate(days):
        if d.weekday() >= 5 and d != dt.date(2026, 11, 15):
            continue                                  # 周六、周日白天无日线
        t = ts(d.year, d.month, d.day, 22)
        px = 3900 + i
        rows.append((t, int(px * 1000), int((px + 2) * 1000),
                     int((px - 2) * 1000), int((px + 1) * 1000), 1.0))
    wk = F.aggregate(rows, "1w")
    assert len(wk) == 2, [b[0] for b in wk]
    last = wk[-1]
    assert last[0] == ts(2026, 11, 16) * 1000, "末根必须是本周一"
    # 本周桶目前只有「11-15 那根日线」（= 周一交易日开盘），close 跟着它
    # （aggregate 的入参顺序是 o,c,l,h —— 测试里 c=px+2）
    assert float(last[4]) == pytest.approx(3900 + 6 + 2, abs=0.01)


def test_t298_previous_week_closes_on_friday_not_latest_price():
    """上周桶的 close 是周五收盘，不能等于最新价（旧实现被周日那根顶掉）。"""
    rows = []
    for d, px in [(dt.date(2026, 11, 9), 4000), (dt.date(2026, 11, 10), 4010),
                  (dt.date(2026, 11, 11), 4020), (dt.date(2026, 11, 12), 4030),
                  (dt.date(2026, 11, 15), 4100)]:
        t = ts(d.year, d.month, d.day, 22)
        rows.append((t, int(px * 1000), int(px * 1000), int(px * 1000),
                     int(px * 1000), 1.0))
    wk = F.aggregate(rows, "1w")
    assert float(wk[0][4]) == pytest.approx(4030.0), "上周 close 应停在周五"
    assert float(wk[1][4]) == pytest.approx(4100.0), "本周 close 才是最新价"


def test_t299_all_week_keys_are_monday():
    """128 根周线里不能出现一根非周一起点（否则前端 utcWeekKey 对不上）。"""
    rows = []
    d = dt.date(2025, 1, 6)
    while d <= dt.date(2026, 11, 16):
        if d.weekday() < 5:
            t = ts(d.year, d.month, d.day, 22)
            rows.append((t, 3_900_000, 3_910_000, 3_890_000, 3_905_000, 1.0))
        d += dt.timedelta(days=1)
    wk = F.aggregate(rows, "1w")
    off = [b[0] for b in wk
           if dt.datetime.fromtimestamp(b[0] / 1000, UTC).weekday() != 0]
    assert off == [], off


# ---------------- tick 窗口 ----------------


def test_t300_tick_window_includes_current_hour():
    """13:14 时窗口必须含 13:00 那根，否则页面只能显示 12:00 的末根。"""
    now = ts(2026, 11, 16, 5, 14)                 # 北京 13:14
    hrs = F.tick_window(ts(2026, 11, 16, 2), now, lookback_h=24)
    assert hrs[-1] == ts(2026, 11, 16, 5), hrs[-5:]


def test_t301_tick_window_keeps_current_hour_even_if_present():
    """上一轮已写入当前小时的半根 → 仍要重拉，否则半根永远补不全。"""
    cur = ts(2026, 11, 16, 5)
    have = set(range(ts(2026, 11, 15, 2), cur + 1, HOUR))
    hrs = F.tick_window(ts(2026, 11, 16, 2), ts(2026, 11, 16, 5, 40),
                        lookback_h=24, have=have)
    assert hrs == [cur], hrs


def test_t302_tick_window_skips_closed_hours():
    """休市小时不进窗口：源上本来没有文件，每轮重拉只刷 fail 计数。

    线上 11-16 那轮 fail=9，全部是 11-15 的休市小时（周六 05–12）。
    """
    now = ts(2026, 11, 16, 5, 14)
    hrs = F.tick_window(ts(2026, 11, 14, 0), now, lookback_h=48, max_hours=48)
    assert ts(2026, 11, 14, 5) not in hrs, "周六凌晨必须跳过"
    assert ts(2026, 11, 15, 12) not in hrs, "周日 17:00 前必须跳过"
    assert ts(2026, 11, 16, 5) in hrs, "周一开盘后要保留"
    assert all(not F.is_closed(h) for h in hrs)


# ---------------- 端到端：旧产物自愈 ----------------


def _daily_series(n, end_date, base=3900.0):
    """最近 n 个交易日（跳过周六），ts 按纽约 17:00 换日写死 22:00 UTC。"""
    out, d, i = [], end_date, 0
    while len(out) < n:
        if d.weekday() != 5:
            t = ts(d.year, d.month, d.day, 22) * 1000
            px = base + i * 0.5
            out.append([t, "%.3f" % px, "%.3f" % (px + 3),
                        "%.3f" % (px - 3), "%.3f" % px, "1"])
            i += 1
        d -= dt.timedelta(days=1)
    return list(reversed(out))


def test_t303_stale_product_self_heals_weekly(tmp_path, monkeypatch):
    """旧产物里 1w 末根停在「上上周」→ 下一轮自动纠正。

    线上 2026-11-16 的状态：1w 末根 11-09、close 被最新价顶掉（= 1h 末根）。

    ⚠️ 日期一律取真实今天。写死 2026-11-16 的夹具只在当天成立：过了那天，
    `main()` 里的 now 是真实时间，tick 全部落在窗口外，会直接返回 1
    （2026-11-19 全量跑时就是这么炸的，与前两期修的时间炸弹同一类）。
    """
    now_dt = dt.datetime.now(UTC)
    now = int(now_dt.timestamp())
    cur = now - now % HOUR
    # 纽约 17:00 换日：UTC 22:00 之后算次日交易日
    trade = (now_dt.date() + dt.timedelta(days=1)) if now_dt.hour >= 22 else now_dt.date()
    mon_this = trade - dt.timedelta(days=trade.weekday())
    mon_prev = mon_this - dt.timedelta(days=7)
    mon_prev2 = mon_this - dt.timedelta(days=14)

    def ms(d):
        return int(dt.datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()) * 1000

    def fake_ticks(**kw):
        rows = [(cur + m * 60, 3_999_000, 3_999_500, 3_998_500, 3_999_000, 1.0)
                for m in range(40)]
        return rows, {"hours": 1, "ok": 1, "empty": 0, "fail": 0, "fail_hours": []}

    bad_weekly = [[ms(mon_prev2), "3900", "3950", "3880", "3920", "1"],
                  [ms(mon_prev), "3920", "3960", "3850", "3900.5", "1"]]
    old = {
        "updated": (cur - 3 * HOUR) * 1000,
        "src": "dukascopy", "symbol": "XAUUSD", "price_type": "bid",
        "window": {"from": (mon_this - dt.timedelta(days=150)).isoformat(),
                   "to": trade.isoformat(), "days_ok": 3,
                   "days_empty": 0, "failed_n": 0, "failed": []},
        "sanitized": {"synthetic": 0, "dup": 0}, "stats": {}, "gaps": {},
        "kl": {"1h": [[(cur - 2 * HOUR) * 1000, "3900", "3902", "3898", "3901", "1"]],
               "4h": [[(cur - 4 * HOUR) * 1000, "3900", "3902", "3898", "3901", "1"]],
               "1d": _daily_series(60, mon_this - dt.timedelta(days=1)),
               "1w": bad_weekly},
    }
    p = tmp_path / "klines.json"
    p.write_text(json.dumps(old), encoding="utf-8")

    monkeypatch.setattr(F, "OUT", str(p))
    monkeypatch.setattr(F, "fetch_recent_ticks", fake_ticks)
    monkeypatch.setattr(sys, "argv", ["fetch_xau.py", "--ticks-only"])
    assert F.main() == 0

    doc = json.loads(p.read_text(encoding="utf-8"))
    wk = doc["kl"]["1w"]
    assert wk[-1][0] == ms(mon_this), "周线末根必须推进到本周"
    assert len(wk) >= 10, "60 天日线派生出的周线历史不能丢"
    # 本周 close 跟着今天的 tick 走（3999），上周那根被重算成周五收盘、绝不能
    # 再被最新价顶掉 —— 旧实现里两者会相等
    assert float(wk[-1][4]) == pytest.approx(3999.5, abs=0.01), wk[-1]
    assert float(wk[-2][4]) != pytest.approx(3999.5), wk[-2]


# ---------------- 页面：周线卡不再报「滞后」 ----------------


def _bars_upto(step_ms, n, end_ms, base=3900.0):
    out = []
    for i in range(n):
        t = end_ms - (n - 1 - i) * step_ms
        out.append([t, "%.2f" % base, "%.2f" % (base + 2),
                    "%.2f" % (base - 2), "%.2f" % base, "1"])
    return out


def _doc_week(monday_ms):
    HOUR_MS = 3600 * 1000
    DAY_MS = 86400 * 1000
    return {
        "updated": int(dt.datetime.now(UTC).timestamp() * 1000),
        "src": "dukascopy", "symbol": "XAUUSD", "price_type": "bid",
        "window": {"from": "2026-10-01", "to": "2026-11-16", "days_ok": 1,
                   "days_empty": 0, "failed_n": 0, "failed": []},
        "sanitized": {"synthetic": 0, "dup": 0},
        "stats": {}, "gaps": {k: [] for k in ("1w", "1d", "4h", "1h")},
        "kl": {"1w": _bars_upto(7 * DAY_MS, 70, monday_ms),
               "1d": _bars_upto(DAY_MS, 90, monday_ms + DAY_MS),
               "4h": _bars_upto(4 * HOUR_MS, 140, monday_ms + 5 * HOUR_MS),
               "1h": _bars_upto(HOUR_MS, 200, monday_ms + 5 * HOUR_MS)},
    }


def _this_monday_ms():
    now = dt.datetime.now(UTC)
    mon = now.date() - dt.timedelta(days=now.weekday())
    return int(dt.datetime(mon.year, mon.month, mon.day, tzinfo=UTC).timestamp() * 1000)


def test_t304_weekly_card_says_this_week(make_env):
    """周线末根是本周一 → 卡片标「本周」，不是「滞后」。"""
    env = make_env(xau=_doc_week(_this_monday_ms()))
    page = env.page
    page.wait_for_timeout(900)
    txt = page.inner_text("#pgrid")
    assert "本周" in txt, txt


def test_t305_weekly_card_shows_this_monday(make_env):
    """卡片上的末根日期必须是本周一，不能还是上周。"""
    mon = _this_monday_ms()
    env = make_env(xau=_doc_week(mon))
    page = env.page
    page.wait_for_timeout(900)
    d = dt.datetime.utcfromtimestamp(mon / 1000)
    stamp = "%02d-%02d" % (d.month, d.day)
    txt = page.inner_text("#pgrid")
    assert "末根 " + stamp in txt, (stamp, txt)


# ---------------- 当天日文件兜底 ----------------


def test_t306_day_plan_can_include_today():
    """include_today=True 把当天排进来（tick 全挂时唯一能推进 1H 的来源）。"""
    today = dt.date(2026, 11, 16)
    got = F.day_plan(today, days=3, include_today=True)
    assert got == [dt.date(2026, 11, 14), dt.date(2026, 11, 15), today], got
    no = F.day_plan(today, days=3, include_today=False)
    assert today not in no and len(no) == 2, no


def test_t307_today_day_file_advances_1h_when_tick_fails(tmp_path, monkeypatch):
    """tick 全部 fail 时，当天日文件的残片仍必须把 1H 推到它写到的位置。

    线上 2026-11-16 06:08 那轮就是 tick ok=0 / fail=2（05、06 两小时被源拒），
    1H 卡在 04:00。当天文件不是空的，只是按小时异步追加 —— 用它兜底比停在
    两小时前强，下一轮 tick 恢复后按 tick 覆盖。
    """
    now = dt.datetime.now(UTC)
    today = now.date()
    last_done_h = now.hour - 1                     # 最近一个已收小时
    assert last_done_h >= 1, "UTC 0 点跑这条没有已收小时可测"

    def fake_day(day, tries=4):
        if day != today:
            return []
        base = int(dt.datetime(today.year, today.month, today.day, tzinfo=UTC).timestamp())
        return [(base + h * 3600 + m * 60, 3_900_000, 3_901_000, 3_899_000,
                 3_900_000, 1.0) for h in range(last_done_h + 1) for m in range(60)]

    def fake_ticks(**kw):
        return [], {"hours": 2, "ok": 0, "empty": 0, "fail": 2,
                    "fail_hours": ["x", "y"]}

    monkeypatch.setattr(F, "OUT", str(tmp_path / "klines.json"))
    monkeypatch.setattr(F, "fetch_day", fake_day)
    monkeypatch.setattr(F, "fetch_recent_ticks", fake_ticks)
    monkeypatch.setattr(F, "fetch_year_daily", lambda y, tries=2: [])
    monkeypatch.setattr(sys, "argv", ["fetch_xau.py", "--days", "3"])
    assert F.main() == 0

    doc = json.loads((tmp_path / "klines.json").read_text(encoding="utf-8"))
    want = int(dt.datetime(today.year, today.month, today.day, last_done_h,
                           tzinfo=UTC).timestamp()) * 1000
    assert doc["kl"]["1h"][-1][0] == want, (doc["kl"]["1h"][-1][0], want)
    assert doc["window"]["ticks"]["fail"] == 2


def test_t308_no_new_bar_skips_write(tmp_path, monkeypatch):
    """同一份数据跑第二遍不重写文件 —— 否则每 15 分钟就产生一个空 commit。

    `updated` 每次都刷新会让 klines.json 每轮都变，仓库体积与 Pages 构建次数
    都白烧；页面判新鲜看末根，不看 updated。
    """
    cur = int(dt.datetime.now(UTC).timestamp()) // HOUR * HOUR

    def fake_ticks(**kw):
        rows = [(cur - HOUR + m * 60, 3_900_000, 3_900_000, 3_900_000,
                 3_900_000, 1.0) for m in range(60)]
        return rows, {"hours": 1, "ok": 1, "empty": 0, "fail": 0, "fail_hours": []}

    p = tmp_path / "klines.json"
    monkeypatch.setattr(F, "OUT", str(p))
    monkeypatch.setattr(F, "fetch_recent_ticks", fake_ticks)
    monkeypatch.setattr(sys, "argv", ["fetch_xau.py", "--ticks-only"])

    assert F.main() == 0
    first = json.loads(p.read_text(encoding="utf-8"))
    assert first["kl"]["1h"], "第一遍必须落盘"

    assert F.main() == 0
    second = json.loads(p.read_text(encoding="utf-8"))
    assert second["updated"] == first["updated"], "无新根时不能重写 updated"
