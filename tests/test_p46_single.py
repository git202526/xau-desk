"""v6.18 单引擎重写 · 第 1 期（引擎层）。

口径（见 docs/开发计划书-P5-单引擎重写.md）：
  p_i  = 0.87·H − (i−1)·d          i = 1…6，第 1 档恒 0.87H
  ī    = Σw_i·i / Σw_i             默认 [8,12,24,36,12,8] → 2.56
  d    = (0.87·H − T) / ī          目标反解，一步除法
  plan_i = w_i/Σw × totalGrams()   与已买标记无关
  state  = plan / bought / pending / over

基准参数：H=4700、W=200、ATR14≈1.18%（≈48 美元）、现价 4072.4。
K 线由 bars_calm() 造：每日振幅 48 美元、末根收盘正好等于现价，使 ATR$≈48，
与计划书 §4.3/§4.5 的实测值对齐（d=73.8 对应 α=1.54）。
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H = 4700.0
W = 200
T = 3900.0
D_3900 = 73.828125          # (0.87*4700 - 3900) / 2.56

DEFAULT_W = [8, 12, 24, 36, 12, 8]
PRICES_3900 = [4089.0, 4015.171875, 3941.34375, 3867.515625, 3793.6875, 3719.859375]


def bars_calm(n=240, px=PRICE, amp=24.0):
    """每日 h−l = 48 美元、末根收盘 = px 的确定性序列 → ATR14 ≈ 48。"""
    now = 1761696000000
    step = 24 * 3600 * 1000
    raw = [px * 0.93 + i * (px * 0.07 / (n - 1)) + math.sin(i / 9.0) * 11 for i in range(n)]
    adj = raw[-1] - px
    out = []
    for i in range(n):
        c = raw[i] - adj
        o = c - 4
        h = c + amp
        l = c - amp
        t = int(now - (n - 1 - i) * step)
        out.append([t, "%.2f" % o, "%.2f" % h, "%.2f" % l, "%.2f" % c,
                    "12.5", t + step - 1, "0", 0, "0", "0", "0"])
    return out


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W)
    c.update(kw)
    return c


def buy(gram, cny_per_gram, gid="b1", gear=None):
    return {"id": gid, "name": "买入", "gram": gram, "priceCny": cny_per_gram,
            "time": "2026-10-01T02:00:00.000Z", "note": "", "gear": gear}


@pytest.fixture
def env(make_env):
    def _f(c=None, **kw):
        return make_env(cfg=c if c is not None else cfg(), bars=bars_calm(), **kw)
    return _f


def near(a, b, tol=0.02):
    return a is not None and abs(a - b) <= tol


# ---------------------------------------------------------------- 归一化与重心

def test_normw_falls_back_to_default(env):
    p = env().page
    dflt = DEFAULT_W
    assert p.evaluate("() => window.__xt.normW(undefined)") == dflt
    assert p.evaluate("() => window.__xt.normW(null)") == dflt
    assert p.evaluate("() => window.__xt.normW([1,2,3])") == dflt          # 长度≠6
    assert p.evaluate("() => window.__xt.normW([0,0,0,0,0,0])") == dflt    # 总和 0
    assert p.evaluate("() => window.__xt.normW([1,-1,3,4,5,6])") == dflt   # 含负数
    assert p.evaluate("() => window.__xt.normW(['a',2,3,4,5,6])") == dflt  # 非数
    assert p.evaluate("() => window.__xt.normW([4,6,12,18,6,4])") == [4, 6, 12, 18, 6, 4]


def test_wcenter_is_weighted_mean_of_index(env):
    p = env().page
    assert near(p.evaluate("() => window.__xt.wCenter([8,12,24,36,12,8])"), 2.56, 1e-9)
    assert near(p.evaluate("() => window.__xt.wCenter([0,0,10,80,10,0])"), 3.0, 1e-9)
    assert near(p.evaluate("() => window.__xt.wCenter([6,8,28,40,10,8])"), 2.64, 1e-9)
    assert near(p.evaluate("() => window.__xt.wCenter([30,25,20,15,5,5])"), 1.55, 1e-9)
    assert near(p.evaluate("() => window.__xt.wCenter([0,0,0,0,0,100])"), 5.0, 1e-9)
    assert p.evaluate("() => window.__xt.wCenter([100,0,0,0,0,0])") is None


# ---------------------------------------------------------------- 价格层

def test_first_gear_anchored_at_087h(env):
    p = env().page
    cases = [(4700, 73.828125), (4700, 0), (4700, 300), (3200, 12), (6000, 150)]
    for h, d in cases:
        pr = p.evaluate("([h, d]) => window.__xt.gearPrices(h, d)", [h, d])
        assert len(pr) == 6
        assert near(pr[0], 0.87 * h, 1e-6), "H=%s 第 1 档 %s" % (h, pr[0])
        assert near(pr[5], 0.87 * h - 5 * d, 1e-6)


def test_prices_are_arithmetic_sequence(env):
    p = env().page
    pr = p.evaluate("() => window.__xt.gearPrices(4700, 73.828125)")
    for i, v in enumerate(PRICES_3900):
        assert near(pr[i], v, 0.01), "第 %d 档 %s != %s" % (i + 1, pr[i], v)
    for i in range(1, 6):
        assert near(pr[i - 1] - pr[i], 73.828125, 1e-6)


def test_gear_prices_negative_d_clamped(env):
    p = env().page
    pr = p.evaluate("() => window.__xt.gearPrices(4700, -50)")
    assert all(near(x, 4089.0, 1e-6) for x in pr)


# ---------------------------------------------------------------- 目标反解

def test_target_3900_hits_exactly(env):
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.setTargetT(3900)")
    base = p.evaluate("() => window.__xt.gearBase()")
    assert near(base["d"], D_3900, 0.01)
    assert near(base["alpha"], D_3900 / 47.98, 0.06)      # ≈1.54
    meta = p.evaluate("() => window.__xt.gearMeta()")
    assert near(meta["avgAll"], 3900.0, 0.05)
    assert meta["spanSrc"] == "target"
    assert meta["feasible"] is True


def test_default_weights_200g(env):
    p = env().page
    plan = p.evaluate("() => window.__xt.planOf()")
    assert [round(x, 4) for x in plan] == [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]
    assert near(sum(plan), W, 1e-9)


def test_monotonic_in_target_and_alpha(env):
    p = env().page
    ds = []
    for t in (3980, 3970, 3960, 3950):
        p.evaluate("() => window.__xt.setTargetT(%d)" % t)
        ds.append(p.evaluate("() => window.__xt.gearBase().d"))
    assert ds == sorted(ds), "目标越低档距应越大：%s" % ds
    assert len(set(round(x, 6) for x in ds)) == 4

    avgs = []
    for a in (1.0, 1.1, 1.2, 1.3):
        p.evaluate("() => window.__xt.setAlpha(%s)" % a)
        avgs.append(p.evaluate("() => window.__xt.gearMeta().avgAll"))
    assert avgs == sorted(avgs, reverse=True), "α 越大均价应越低：%s" % avgs


def test_alpha_cap_bounds(env):
    p = env().page
    atr = p.evaluate("() => window.__xt.atrUsd()")
    p.evaluate("() => window.__xt.setTargetT(3700)")
    b = p.evaluate("() => window.__xt.gearBase()")
    assert near(b["alpha"], 151.953 / atr, 0.02)          # 3.17 @ATR48
    assert b["feasible"] is True and b["hint"] == ""

    p.evaluate("() => window.__xt.setTargetT(3600)")
    b2 = p.evaluate("() => window.__xt.gearBase()")
    assert b2["hint"] == "cap"
    assert b2["feasible"] is False
    assert near(b2["d"], 3.5 * atr, 0.05)                 # 按上限排，不硬解
    assert near(b2["alpha"], 3.5, 1e-9)


def test_span_mode_last_edit_wins(env):
    p = env().page
    p.evaluate("() => window.__xt.setTargetT(3900)")
    assert p.evaluate("() => window.__xt.gearBase().spanSrc") == "target"
    assert p.evaluate("() => window.__xt.gearBase().d") == pytest.approx(D_3900, abs=0.01)

    p.evaluate("() => window.__xt.setAlpha(1.2)")
    b = p.evaluate("() => window.__xt.gearBase()")
    assert b["spanSrc"] == "atr"                     # 档距来自 ATR，不是目标
    assert near(b["alpha"], 1.2, 1e-9)
    assert near(b["d"], 1.2 * 47.98, 0.6)
    assert p.evaluate("() => window.__xt.gearBase().target") == 3900   # 目标保留为读数

    p.evaluate("() => window.__xt.setTargetT(3900)")
    assert p.evaluate("() => window.__xt.gearBase().spanSrc") == "target"
    assert p.evaluate("() => window.__xt.gearBase().d") == pytest.approx(D_3900, abs=0.01)


# ---------------------------------------------------------------- 克重分布

def test_normalized_weights_equivalent(env):
    p = env().page
    a = p.evaluate("() => window.__xt.spanForTarget(4700, 3900, [8,12,24,36,12,8])")
    b = p.evaluate("() => window.__xt.spanForTarget(4700, 3900, [4,6,12,18,6,4])")
    c = p.evaluate("() => window.__xt.spanForTarget(4700, 3900, [16,24,48,72,24,16])")
    assert near(a["d"], b["d"], 1e-9) and near(a["d"], c["d"], 1e-9)
    assert near(a["d"], D_3900, 0.01)


def test_heavier_middle_shrinks_span(env):
    p = env().page
    r = p.evaluate("() => window.__xt.spanForTarget(4700, 3900, [6,8,28,40,10,8])")
    assert near(r["d"], 189.0 / 2.64, 1e-6)               # 71.59
    assert r["d"] < D_3900
    assert near(r["alpha"], 71.59 / 47.98, 0.06)


def test_single_fourth_gear(env):
    p = env().page
    r = p.evaluate("() => window.__xt.spanForTarget(4700, 3900, [0,0,10,80,10,0])")
    assert near(r["d"], 63.0, 1e-6)
    assert near(r["alpha"], 63.0 / 47.98, 0.06)


def test_shallow_weights_need_large_alpha(env):
    p = env().page
    r = p.evaluate("() => window.__xt.spanForTarget(4700, 3900, [30,25,20,15,5,5])")
    assert near(r["alpha"], (189 / 1.55) / 47.98, 0.06)   # ≈2.54
    assert 2.0 < r["alpha"] <= 3.5
    assert r["feasible"] is True


def test_flat_weights_cannot_steer(env):
    p = env().page
    p.evaluate("() => window.__xt.setTargetT(3900)")
    p.evaluate("() => window.__xt.setWPct([100,0,0,0,0,0])")
    b = p.evaluate("() => window.__xt.gearBase()")
    assert b["hint"] == "flat"
    assert p.evaluate("() => window.__xt.wCenter(window.__xt.curW())") is None
    r = p.evaluate("() => window.__xt.spanForTarget(4700, 3900, [100,0,0,0,0,0])")
    assert r["hint"] == "flat" and r["d"] is None
    # 档位价不跟着目标走，按 α 排
    pr = p.evaluate("() => window.__xt.gearPrices(4700, window.__xt.gearBase().d)")
    assert near(pr[0], 4089.0, 1e-6)


def test_set_and_reset_weights(env):
    p = env().page
    assert p.evaluate("() => window.__xt.setWPct([6,8,28,40,10,8])") == [6, 8, 28, 40, 10, 8]
    p.evaluate("() => window.__xt.setWPct([0,0,0,0,0,0])")
    assert p.evaluate("() => window.__xt.curW()") == DEFAULT_W       # 非法 → 回默认
    p.evaluate("() => window.__xt.setWPct([6,8,28,40,10,8])")
    assert p.evaluate("() => window.__xt.curW()") == [6, 8, 28, 40, 10, 8]
    p.evaluate("() => window.__xt.resetW()")
    assert p.evaluate("() => window.__xt.curW()") == DEFAULT_W
    plan = p.evaluate("() => window.__xt.planOf()")
    assert [round(x, 4) for x in plan] == [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]


# ---------------------------------------------------------------- 已持仓折算

def test_held_cost_folded_into_target(env):
    # 60g @ 4100$/oz，T=3980，W=200 → T_new = (3980*200 − 4100*60)/140 = 3928.571
    c = cfg(buys=[buy(60, 4100 * 7.10 / 31.1035)])
    p = env(c).page
    assert near(p.evaluate("() => window.__xt.holdStats().grams"), 60, 0.01)
    p.evaluate("() => window.__xt.setTargetT(3980)")
    b = p.evaluate("() => window.__xt.gearBase()")
    assert near(b["targetEff"], 3928.571, 0.2)
    assert near(b["d"], (4089 - 3928.571) / 2.56, 0.2)
    meta = p.evaluate("() => window.__xt.gearMeta()")
    assert near(meta["mix"]["avg"], 3980.0, 1.0)
    assert meta["mix"]["held"] == pytest.approx(60, abs=0.01)


def test_held_already_better_than_target(env):
    # 195g @ 4000，T=3900，W=200 → T_new = (780000−780000)/5 = 0 → 已达标
    c = cfg(buys=[buy(195, 4000 * 7.10 / 31.1035)])
    p = env(c).page
    p.evaluate("() => window.__xt.setTargetT(3900)")
    b = p.evaluate("() => window.__xt.gearBase()")
    assert near(b["targetEff"], 0, 0.5)
    assert b["hint"] == "done"


def test_planonly_skips_held_conversion(env):
    c = cfg(buys=[buy(60, 4100 * 7.10 / 31.1035)])
    p = env(c).page
    p.evaluate("() => window.__xt.setTargetT(3980)")
    assert near(p.evaluate("() => window.__xt.gearBase().targetEff"), 3928.571, 0.2)
    p.evaluate("() => window.__xt.setPlanOnly(true)")
    b = p.evaluate("() => window.__xt.gearBase()")
    assert b["targetEff"] == 3980            # 计划模式不折算
    assert near(b["d"], (4089 - 3980) / 2.56, 0.02)


# ---------------------------------------------------------------- ATR 兜底

def test_atr_missing_falls_back_to_one_percent(env):
    e = env()
    p = e.page
    p.unroute("**/data-api.binance.vision/**")
    p.route("**/data-api.binance.vision/**", lambda r: r.fulfill(
        status=404, content_type="application/json", body="{}"))
    p.reload(wait_until="domcontentloaded")
    p.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    p.wait_for_timeout(400)
    assert p.evaluate("() => window.__xt.atrPctOf()") is None
    assert p.evaluate("() => window.__xt.atrUsd()") is None
    b = p.evaluate("() => window.__xt.gearBase()")
    assert b["spanSrc"] == "fallback"
    assert near(b["d"], 47.0, 0.01)          # 0.01 × 4700
    assert near(b["alpha"], 1.0, 1e-9)
    assert p.evaluate("() => window.__xt.gearRows().length") == 8


# ---------------------------------------------------------------- 计划 / 标记

def test_plan_ignores_marks(env):
    p = env().page
    p.evaluate("() => window.__xt.setPlanOnly(true)")
    base = p.evaluate("() => window.__xt.planOf()")
    for marks in ({}, {"3": 10}, {"3": 60}, {"1": 16, "2": 24, "3": 48, "4": 72, "5": 24, "6": 16}):
        p.evaluate("(m) => { window.__xt.clearAllMarks(); "
                   "Object.keys(m).forEach(k => window.__xt.markGear(Number(k), m[k])); }", marks)
        cur = p.evaluate("() => window.__xt.planOf()")
        assert [round(x, 4) for x in cur] == [round(x, 4) for x in base], \
            "marks=%s 改了计划：%s" % (marks, cur)


def test_mark_partial_keeps_pending_in_place(env):
    p = env().page
    p.evaluate("() => window.__xt.setPlanOnly(true)")
    p.evaluate("() => window.__xt.markGear(3, 10)")
    st = p.evaluate("() => window.__xt.gearState(3)")
    assert st == {"plan": 48.0, "bought": 10.0, "pending": 38.0, "over": 0.0}
    s = p.evaluate("() => window.__xt.planSummary()")
    assert s["bought"] == 10 and s["pending"] == 190 and s["overBudget"] == 0
    assert s["done"] is False


def test_mark_full_clears_pending(env):
    p = env().page
    p.evaluate("() => window.__xt.setPlanOnly(true)")
    p.evaluate("() => window.__xt.markGear(3, 48)")
    st = p.evaluate("() => window.__xt.gearState(3)")
    assert st["pending"] == 0 and st["over"] == 0
    assert st["bought"] == 48


def test_mark_over_reports_overbudget(env):
    p = env().page
    p.evaluate("() => window.__xt.setPlanOnly(true)")
    p.evaluate("() => window.__xt.markGear(3, 58)")
    st = p.evaluate("() => window.__xt.gearState(3)")
    assert st["pending"] == 0 and st["over"] == 10
    s = p.evaluate("() => window.__xt.planSummary()")
    assert s["overBudget"] == 10
    assert s["bought"] == 58 and s["pending"] == 152   # 超买 10g 不回填待补


def test_unmark_restores_pending(env):
    p = env().page
    p.evaluate("() => window.__xt.setPlanOnly(true)")
    before = p.evaluate("() => window.__xt.planOf()")
    p.evaluate("() => window.__xt.markGear(3, 10)")
    p.evaluate("() => window.__xt.unmarkGear(3)")
    st = p.evaluate("() => window.__xt.gearState(3)")
    assert st["bought"] == 0 and st["pending"] == 48
    assert p.evaluate("() => window.__xt.planOf()") == before
    assert p.evaluate("() => window.__xt.planSummary().bought") == 0


def test_mark_is_cumulative_not_incremental(env):
    p = env().page
    p.evaluate("() => window.__xt.setPlanOnly(true)")
    p.evaluate("() => window.__xt.markGear(3, 10)")
    p.evaluate("() => window.__xt.markGear(3, 30)")
    assert p.evaluate("() => window.__xt.gearState(3).bought") == 30


def test_mark_does_not_move_prices_or_neighbors(env):
    p = env().page
    p.evaluate("() => window.__xt.setPlanOnly(true)")
    before = [r["price"] for r in p.evaluate("() => window.__xt.gearRows()")]
    p.evaluate("() => window.__xt.markGear(3, 10)")
    after = [r["price"] for r in p.evaluate("() => window.__xt.gearRows()")]
    assert before == after
    rows = {r["name"]: r for r in p.evaluate("() => window.__xt.gearRows()")}
    assert rows["第3档"]["pending"] == 38
    assert rows["第4档"]["pending"] == 72
    assert rows["第5档"]["pending"] == 24


def test_avg_bought_single_and_multi(env):
    p = env().page
    p.evaluate("() => window.__xt.setPlanOnly(true)")
    p.evaluate("() => window.__xt.setTargetT(3900)")
    p.evaluate("() => window.__xt.markGear(4, 20)")
    s = p.evaluate("() => window.__xt.planSummary()")
    assert near(s["avgBought"], 3867.515625, 0.05)        # 第 4 档价

    p.evaluate("() => window.__xt.clearAllMarks()")
    p.evaluate("() => { window.__xt.markGear(1,16); window.__xt.markGear(2,24); window.__xt.markGear(3,10); }")
    s2 = p.evaluate("() => window.__xt.planSummary()")
    assert s2["bought"] == pytest.approx(50, abs=0.01)
    assert near(s2["avgBought"], (16 * 4089 + 24 * 4015.171875 + 10 * 3941.34375) / 50, 0.05)


def test_illegal_marks_are_cleaned(env):
    p = env().page
    got = p.evaluate("() => window.__xt.cleanGearMarks({'0':{g:10},'7':{g:10},'3':{g:-5},'2':{g:24},'x':{g:5}})")
    assert list(got.keys()) == ["2"]
    assert got["2"]["g"] == 24
    assert p.evaluate("() => window.__xt.cleanGearMarks(null)") == {}
    assert p.evaluate("() => window.__xt.cleanGearMarks([1,2,3])") == {}


def test_unit_consistency_100g(env):
    p = env(cfg(TOTAL=100)).page
    p.evaluate("() => window.__xt.setPlanOnly(true)")
    plan = p.evaluate("() => window.__xt.planOf()")
    assert [round(x, 4) for x in plan] == [8.0, 12.0, 24.0, 36.0, 12.0, 8.0]
    p.evaluate("() => window.__xt.markGear(3, 5)")
    st = p.evaluate("() => window.__xt.gearState(3)")
    assert st["pending"] == 19 and st["over"] == 0
    p.evaluate("() => window.__xt.markGear(3, 24)")
    st2 = p.evaluate("() => window.__xt.gearState(3)")
    assert st2["pending"] == 0 and st2["over"] == 0


def test_degenerate_warns_have_no_plan(env):
    p = env().page
    rows = p.evaluate("() => window.__xt.gearRows()")
    warn = [r for r in rows if r["warn"]]
    assert len(warn) == 2
    assert all(r["plan"] == 0 and r["pending"] == 0 for r in warn)
    assert all(r["price"] > 0 for r in warn)
