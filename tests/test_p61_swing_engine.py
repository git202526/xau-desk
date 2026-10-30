"""P6 循环档 · 第 1 期（引擎层，UI 不动）。

验数据与算法，不碰面板和行尾按钮：四数结构、净持仓语义、买入/卖出增量、
已实现差价、周转次数、swingGears 校验，以及「卖出不碰价位 / 计划 / 账本」三道边界。

数值口径：H = 4700、总仓 200g、alpha = 1 → 档距 48 美元，
第 1 档 4089、第 2 档 4041，计划 [16, 24, 48, 72, 24, 16]。
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H = 4700.0
W = 200.0
P1 = 4089.0
P2 = 4041.0
OZ = 31.1035


def bars_calm(n=240, px=PRICE, amp=24.0):
    now = 1761696000000
    step = 24 * 3600 * 1000
    raw = [px * 0.93 + i * (px * 0.07 / (n - 1)) + math.sin(i / 9.0) * 11 for i in range(n)]
    adj = raw[-1] - px
    out = []
    for i in range(n):
        c = raw[i] - adj
        t = int(now - (n - 1 - i) * step)
        out.append([t, "%.2f" % (c - 4), "%.2f" % (c + amp), "%.2f" % (c - amp),
                    "%.2f" % c, "12.5", t + step - 1, "0", 0, "0", "0", "0"])
    return out


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W, planOnly=True)
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    def _f(c=None, **kw):
        return make_env(cfg=c if c is not None else cfg(), bars=bars_calm(), **kw)
    return _f


def mark(p, i):
    return p.evaluate("(i) => (window.__xt.state().cfg.gearMarks || {})[String(i)] || null", i)


# ---------------------------------------------------------------- 1 单位换算

def test_s1_gram_to_usd(env):
    """16g @4089 是 2103.43 美元，不是 16×4089。"""
    p = env().page
    v = p.evaluate("() => window.__xt.gramToUsd(16, 4089)")
    assert v == pytest.approx(16 / OZ * 4089, abs=0.01)
    assert v == pytest.approx(2103.43, abs=0.01)
    assert p.evaluate("() => window.__xt.gramToUsd(0, 4089)") == 0
    assert p.evaluate("() => window.__xt.gramToUsd(16, 0)") == 0


# ---------------------------------------------------------------- 2 迁移

def test_s2_legacy_mark_migrates_to_four_numbers(env):
    """老标记 {g, at} 补齐 sold/proceeds=0；cost 不写值，由 gearCost 按档位价兜底。"""
    p = env().page
    out = p.evaluate("() => window.__xt.cleanGearMarks({'1': {g: 16, at: 1761753600000}})")
    m = out["1"]
    assert m["g"] == 16
    assert m["sold"] == 0 and m["proceeds"] == 0
    assert m["cost"] == 0                      # 清洗阶段不补价，避免污染纯结构函数
    assert m["at"] == 1761753600000
    # 兜底按档位价折算 → 与 s1 同一个数
    assert p.evaluate("() => window.__xt.gearCost(1, {g: 16})") == pytest.approx(2103.43, abs=0.02)


def test_s3_migration_is_idempotent(env):
    p = env().page
    a = p.evaluate("() => window.__xt.cleanGearMarks({'1': {g: 16, at: 1}})")
    b = p.evaluate("(o) => window.__xt.cleanGearMarks(o)", a)
    assert b["1"] == a["1"]
    assert b["1"]["sold"] == 0 and b["1"]["proceeds"] == 0


def test_s3b_mark_buy_is_incremental(env):
    """面板填 8 是「再买 8g」：买两次 8g 得到 16g，成本累加。"""
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 8, 4089)")
    p.evaluate("() => window.__xt.markBuy(1, 8, 4089)")
    m = mark(p, 1)
    assert m["g"] == 16
    assert m["cost"] == pytest.approx(2103.43, abs=0.02)


def test_s3c_mark_gear_is_absolute(env):
    """兼容壳保持 P5 语义：先设 8 再设 16 得到 16g，不是 24g。"""
    p = env().page
    p.evaluate("() => window.__xt.markGear(1, 8)")
    assert mark(p, 1)["g"] == 8
    p.evaluate("() => window.__xt.markGear(1, 16)")
    m = mark(p, 1)
    assert m["g"] == 16
    assert m["cost"] == pytest.approx(2103.43, abs=0.02)


# ---------------------------------------------------------------- 3 净持仓

def test_s4_net_is_gross_minus_sold(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    p.evaluate("() => window.__xt.markSell(1, 6, 4170)")
    st = p.evaluate("() => window.__xt.gearState(1)")
    assert st["gross"] == 16 and st["soldG"] == 6
    assert st["bought"] == 10
    assert st["pending"] == pytest.approx(16 - 10)


def test_s5_sell_releases_pending(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    assert p.evaluate("() => window.__xt.gearState(1).pending") == 0
    p.evaluate("() => window.__xt.markSell(1, 6, 4170)")
    assert p.evaluate("() => window.__xt.gearState(1).pending") == 6
    # 额度释放后可以再买回来
    assert p.evaluate("() => window.__xt.markBuy(1, 6, 4089)") is True
    assert p.evaluate("() => window.__xt.gearState(1).pending") == 0


# ---------------------------------------------------------------- 4 已实现差价

def test_s6_realized_basic(env):
    """16g 买入 @4089、全部卖出 @4170 → 差价 ≈ +41.7 美元。"""
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    p.evaluate("() => window.__xt.markSell(1, 16, 4170)")
    r = p.evaluate("() => window.__xt.gearState(1).realized")
    assert r == pytest.approx(16 / OZ * 4170 - 16 / OZ * 4089, abs=0.02)
    assert r == pytest.approx(41.7, abs=0.1)


def test_s7_realized_partial_sell(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    p.evaluate("() => window.__xt.markSell(1, 8, 4170)")
    r = p.evaluate("() => window.__xt.gearState(1).realized")
    assert r == pytest.approx(8 / OZ * 4170 - 8 / OZ * 4089, abs=0.02)
    assert r == pytest.approx(20.8, abs=0.1)


def test_s8_realized_can_be_negative(env):
    """卖亏了要出负数（回本位以下卖出）。"""
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    p.evaluate("() => window.__xt.markSell(1, 16, 4000)")
    r = p.evaluate("() => window.__xt.gearState(1).realized")
    assert r < 0
    assert r == pytest.approx(16 / OZ * (4000 - 4089), abs=0.02)


def test_s9_turns(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    p.evaluate("() => window.__xt.markSell(1, 16, 4170)")
    assert p.evaluate("() => window.__xt.gearState(1).turns") == pytest.approx(1.0, abs=0.01)
    p.evaluate("() => window.__xt.markBuy(1, 16, 4050)")
    p.evaluate("() => window.__xt.markSell(1, 8, 4170)")
    assert p.evaluate("() => window.__xt.gearState(1).turns") == pytest.approx(1.5, abs=0.01)
    # plan = 0 时不给轮次
    assert p.evaluate("() => window.__xt.gearTurns(16, 0)") == 0


# ---------------------------------------------------------------- 5 卖出校验

def test_s10_reject_sell_over_net(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    assert p.evaluate("() => window.__xt.markSell(1, 20, 4170)") is False
    m = mark(p, 1)
    assert m["sold"] == 0 and m["proceeds"] == 0


def test_s11_reject_sell_on_build_gear(env):
    """第 3 档不在 swingGears 里 → 拒绝（引擎层拦，不只是 UI 置灰）。"""
    p = env().page
    p.evaluate("() => window.__xt.markBuy(3, 48, 3993)")
    assert p.evaluate("() => window.__xt.markSell(3, 10, 4100)") is False
    assert mark(p, 3)["sold"] == 0


def test_s12_allow_sell_after_adding_gear(env):
    p = env().page
    assert p.evaluate("() => window.__xt.setSwingGears([1, 2, 3])") == [1, 2, 3]
    p.evaluate("() => window.__xt.markBuy(3, 48, 3993)")
    assert p.evaluate("() => window.__xt.markSell(3, 10, 4100)") is True
    assert mark(p, 3)["sold"] == 10


def test_s13_swing_gears_validation(env):
    p = env().page
    assert p.evaluate("() => window.__xt.normSwing([0, 9, 'a'])") == [1, 2]
    assert p.evaluate("() => window.__xt.normSwing([3, 1, 3, '2'])") == [1, 2, 3]
    assert p.evaluate("() => window.__xt.normSwing(null)") == [1, 2]
    # 越界值被剔掉，剩下的保留
    assert p.evaluate("() => window.__xt.normSwing([2, 7])") == [2]


def test_s14_empty_swing_is_allowed(env):
    """空数组 = 关闭循环档，全部按建仓档处理，不是回默认。"""
    p = env().page
    assert p.evaluate("() => window.__xt.setSwingGears([])") == []
    assert p.evaluate("() => window.__xt.isSwingGear(1)") is False
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    assert p.evaluate("() => window.__xt.markSell(1, 6, 4170)") is False


# ---------------------------------------------------------------- 6 卖出不动三层

def test_s15_sell_does_not_touch_gear_prices(env):
    p = env().page
    before = [r["price"] for r in p.evaluate("() => window.__xt.gearRows()")]
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    p.evaluate("() => window.__xt.markSell(1, 16, 4170)")
    after = [r["price"] for r in p.evaluate("() => window.__xt.gearRows()")]
    assert after == before


def test_s16_sell_does_not_touch_plan(env):
    p = env().page
    before = p.evaluate("() => window.__xt.planOf()")
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    p.evaluate("() => window.__xt.markSell(1, 16, 4170)")
    assert p.evaluate("() => window.__xt.planOf()") == before


def test_s17_sell_does_not_touch_ledger(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    p.evaluate("() => window.__xt.markSell(1, 16, 4170)")
    assert p.evaluate("() => window.__xt.state().cfg.buys.length") == 0
    assert p.evaluate("() => window.__xt.state().cfg.sells.length") == 0
    assert p.evaluate("() => window.__xt.holdStats().grams") == 0


# ---------------------------------------------------------------- 7 汇总口径

def test_s18_summary_bought_is_net(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    p.evaluate("() => window.__xt.markSell(1, 6, 4170)")
    s = p.evaluate("() => window.__xt.planSummary()")
    assert s["bought"] == 10
    assert s["pending"] == 190
    assert s["grossBought"] == 16
    assert s["soldTotal"] == 6


def test_s19_summary_realized_matches_gear(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    p.evaluate("() => window.__xt.markSell(1, 6, 4170)")
    s = p.evaluate("() => window.__xt.planSummary()")
    g = p.evaluate("() => window.__xt.gearState(1).realized")
    assert s["realizedTotal"] == pytest.approx(g, abs=0.001)


def test_s20_done_flips_back_after_sell(env):
    p = env().page
    for i, g in [(1, 16), (2, 24), (3, 48), (4, 72), (5, 24), (6, 16)]:
        p.evaluate("(a) => window.__xt.markBuy(a[0], a[1])", [i, g])
    assert p.evaluate("() => window.__xt.planSummary().done") is True
    p.evaluate("() => window.__xt.markSell(1, 1, 4170)")
    assert p.evaluate("() => window.__xt.planSummary().done") is False


def test_s21_ledger_hold_unchanged_by_sell(env):
    """mix 的 heldG 取自账本；卖出不动账本，所以持仓克重与成本都不变。"""
    p = env().page
    before = p.evaluate("() => window.__xt.holdStats()")
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    p.evaluate("() => window.__xt.markSell(1, 16, 4170)")
    after = p.evaluate("() => window.__xt.holdStats()")
    assert after["grams"] == before["grams"]
    assert after["avg"] == before["avg"]


# ---------------------------------------------------------------- 8 持久化

def test_s22_four_numbers_persist(env):
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    p.evaluate("() => window.__xt.markSell(1, 6, 4170)")
    p.wait_for_timeout(1500)                     # 等防抖推送落到云端
    assert e.gh.cfg["gearMarks"]["1"]["sold"] == 6

    p.reload(wait_until="domcontentloaded")
    p.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    p.wait_for_timeout(500)
    m = mark(p, 1)
    assert m["g"] == 16 and m["sold"] == 6
    assert m["proceeds"] == pytest.approx(6 / OZ * 4170, abs=0.05)
    assert p.evaluate("() => window.__xt.gearState(1).bought") == 10
