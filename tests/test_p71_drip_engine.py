"""P7 深档兜底 · 第 1 期（引擎层，UI 不动）。

验：默认值与越界回退、dripAt 清洗、计时只在「下一个待买档」上存在、
到期判定六道闸门、克重与金额、尾数保护、执行后重置、循环档/账本模式/关闭开关三道边界。

数值口径：H = 4700、总仓 200g、alpha = 1 → 档距 48 美元，
档位 4089 / 4041 / 3993 / 3945 / 3897 / 3849，计划 [16, 24, 48, 72, 24, 16]。
默认现价 4072.40 → 第 1 档刚跌破 0.41%，是「刚到价」档。
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H = 4700.0
W = 200.0
P1 = 4089.0
OZ = 31.1035
DAY = 86400000


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
    """默认：全部六档都是建仓档（swingGears=[]），计划模式。"""
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W, planOnly=True, swingGears=[], gearMarks={})
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    """cfg 字段（swingGears / planOnly / dripDays ...）进 cfg，其余交给 make_env。
    make_env 自己的参数只有这几个，别把 cfg 字段漏进去（会 TypeError）。"""
    mk_keys = ("price", "width", "height", "gh_on", "reduced_motion", "tz", "calendar")

    def _f(c=None, **kw):
        base = dict(c) if c is not None else cfg()
        mk = {}
        for k in list(kw.keys()):
            if k in mk_keys:
                mk[k] = kw.pop(k)
        base.update(kw)
        mk.setdefault("bars", bars_calm())
        return make_env(cfg=base, **mk)
    return _f


def backdate(p, i, days):
    """把某档计时推到 N 天前，并重算（tickDrip 会保留已存在的计时）。"""
    p.evaluate(
        """(a) => {
            var s = window.__xt.state();
            s.cfg.dripAt = s.cfg.dripAt || {};
            s.cfg.dripAt[String(a.i)] = Date.now() - a.ms;
            window.__xt.compute();
        }""",
        {"i": i, "ms": days * DAY},
    )


# ---------------------------------------------------------------- 1 默认值

def test_d1_defaults_from_empty_cfg(env):
    """空 cfg 读出 30 / 0.5 / 3，且首次 compute 就给下一个待买档起表。"""
    p = env().page
    c = p.evaluate("() => window.__xt.state().cfg")
    assert c["dripDays"] == 30
    assert c["dripPct"] == 0.5
    assert c["dripMaxPremium"] == 3
    assert "1" in c["dripAt"], c["dripAt"]        # 第 1 档是下一个待买档，已在计时
    assert len(c["dripAt"]) == 1


def test_d2_days_bounds(env):
    """5 天太短、999 天太长 → 回默认 30；45 保留；0 是合法值（关闭）。"""
    p = env().page
    st = p.evaluate("""() => {
        const r = {};
        r.low  = window.__xt.setDrip(5, 0.5, 3).days;
        r.high = window.__xt.setDrip(999, 0.5, 3).days;
        r.mid  = window.__xt.setDrip(45, 0.5, 3).days;
        r.zero = window.__xt.setDrip(0, 0.5, 3).days;
        r.neg  = window.__xt.setDrip(-10, 0.5, 3).days;
        return r;
    }""")
    assert st == {"low": 30, "high": 30, "mid": 45, "zero": 0, "neg": 30}


def test_d3_pct_and_premium_bounds(env):
    """比例 0.05 / 2 越界回 0.5；溢价 -1 / 50 越界回 3。"""
    p = env().page
    st = p.evaluate("""() => {
        const r = {};
        r.p1 = window.__xt.setDrip(30, 0.05, 3).pct;
        r.p2 = window.__xt.setDrip(30, 2, 3).pct;
        r.p3 = window.__xt.setDrip(30, 0.3, 3).pct;
        r.q1 = window.__xt.setDrip(30, 0.5, -1).prem;
        r.q2 = window.__xt.setDrip(30, 0.5, 50).prem;
        r.q3 = window.__xt.setDrip(30, 0.5, 5).prem;
        return r;
    }""")
    assert st == {"p1": 0.5, "p2": 0.5, "p3": 0.3, "q1": 3, "q2": 3, "q3": 5}


# ---------------------------------------------------------------- 2 清洗

def test_d4_clean_drip_at(env):
    """越界档号、非数、负数、未来时间全部剔除；合法的保留。"""
    p = env().page
    out = p.evaluate("""() => {
        const now = Date.now();
        return window.__xt.cleanDripAt({
            "1": 1761753600000, "7": 1761753600000, "0": 1761753600000,
            "2": "abc", "3": -5, "4": now + 9999999, "5": now - 1000
        });
    }""")
    assert list(out.keys()) == ["1", "5"], out
    assert out["1"] == 1761753600000


def test_d5_clean_drip_at_garbage_is_empty(env):
    """null / 数组 / 字符串一律回空对象，不抛异常。"""
    p = env().page
    assert p.evaluate("() => window.__xt.cleanDripAt(null)") == {}
    assert p.evaluate("() => window.__xt.cleanDripAt([1,2])") == {}
    assert p.evaluate("() => window.__xt.cleanDripAt('x')") == {}


# ---------------------------------------------------------------- 3 谁是下一个

def test_d6_next_is_first_build_gear(env):
    """全建仓档、未买 → 第 1 档。"""
    p = env().page
    assert p.evaluate("() => window.__xt.dripNext()") == 1


def test_d7_next_skips_swing_gears(env):
    """1/2 档是循环档 → 下一个待买建仓档是第 3 档。"""
    p = env(swingGears=[1, 2]).page
    assert p.evaluate("() => window.__xt.dripNext()") == 3


def test_d8_next_skips_filled_gear(env):
    """1 档买满 → 轮到第 2 档；计时也从 1 挪到 2。"""
    p = env().page
    p.evaluate("() => window.__xt.markGear(1, 16)")
    assert p.evaluate("() => window.__xt.dripNext()") == 2
    at = p.evaluate("() => window.__xt.state().cfg.dripAt")
    assert list(at.keys()) == ["2"], at


def test_d9_all_filled_clears_clock(env):
    """六档全买满 → 没有下一个，计时表清空。"""
    p = env().page
    p.evaluate("""() => {
        [16,24,48,72,24,16].forEach((g, k) => window.__xt.markGear(k + 1, g));
    }""")
    assert p.evaluate("() => window.__xt.dripNext()") is None
    assert p.evaluate("() => window.__xt.state().cfg.dripAt") == {}


# ---------------------------------------------------------------- 4 计时

def test_d10_only_one_gear_holds_the_clock(env):
    """手动塞一个 4 档的旧计时 → 下次 compute 只留当前队首（1 档）。"""
    p = env().page
    p.evaluate("""() => {
        const s = window.__xt.state();
        s.cfg.dripAt = {"4": Date.now() - 40 * 86400000};
        window.__xt.compute();
    }""")
    at = p.evaluate("() => window.__xt.state().cfg.dripAt")
    assert list(at.keys()) == ["1"], at


def test_d11_clock_not_reset_by_price_tick(env):
    """同一档继续在等，价格来一次 tick 不会把计时重置。"""
    p = env().page
    backdate(p, 1, 20)
    before = p.evaluate("() => window.__xt.state().cfg.dripAt['1']")
    p.evaluate("() => window.__xt.compute()")
    after = p.evaluate("() => window.__xt.state().cfg.dripAt['1']")
    assert after == before


def test_d12_ledger_mode_clears_clock(env):
    """账本模式没有「计划待补」，计时表必须为空。"""
    p = env(planOnly=False).page
    assert p.evaluate("() => window.__xt.state().cfg.dripAt") == {}
    assert p.evaluate("() => window.__xt.dripInfo(1).why") == "ledger"


def test_d13_zero_days_closes_feature(env):
    """dripDays = 0 → 关闭；计时表清空，why = off。"""
    p = env(dripDays=0).page
    assert p.evaluate("() => window.__xt.state().cfg.dripAt") == {}
    info = p.evaluate("() => window.__xt.dripInfo(1)")
    assert info["why"] == "off"
    assert info["canPrompt"] is False


# ---------------------------------------------------------------- 5 判定

def test_d14_not_due_yet(env):
    """刚起表，days≈0 < 30 → wait，不给提示。"""
    p = env().page
    info = p.evaluate("() => window.__xt.dripInfo(1)")
    assert info["why"] == "wait"
    assert info["canPrompt"] is False
    assert info["armed"] is True
    assert info["days"] < 1


def test_d15_due_and_within_premium(env):
    """等 40 天、溢价 −0.41%（低于档位价）→ 可以提示。"""
    p = env().page
    backdate(p, 1, 40)
    info = p.evaluate("() => window.__xt.dripInfo(1)")
    assert info["canPrompt"] is True, info
    assert info["why"] == ""
    assert info["days"] == pytest.approx(40, abs=0.05)
    assert info["premium"] == pytest.approx((PRICE / P1 - 1) * 100, abs=0.01)


def test_d16_premium_blocks_chasing(env):
    """现价 4220 高出第 1 档 3.2% > 容忍 3% → 不提示（追高不如等）。"""
    p = env(price=4220.0).page
    backdate(p, 1, 40)
    info = p.evaluate("() => window.__xt.dripInfo(1)")
    assert info["why"] == "premium", info
    assert info["canPrompt"] is False
    assert info["premium"] > 3


def test_d17_premium_boundary_inclusive(env):
    """溢价正好 3.0% 时仍然提示（容忍线是「不超过」）。"""
    px = P1 * 1.03
    p = env(price=px).page
    backdate(p, 1, 40)
    info = p.evaluate("() => window.__xt.dripInfo(1)")
    assert info["premium"] == pytest.approx(3.0, abs=0.01)
    assert info["canPrompt"] is True, info


def test_d18_swing_gear_never_drips(env):
    """循环档走 P6 的买卖，兜底跳过它。"""
    p = env(swingGears=[1]).page
    backdate(p, 1, 40)
    info = p.evaluate("() => window.__xt.dripInfo(1)")
    assert info["why"] == "swing"
    assert p.evaluate("() => window.__xt.doDrip(1)") is False


def test_d19_not_next_gear(env):
    """第 2 档还在排队（第 1 档没买），即使它自己有计时也不提示。"""
    p = env().page
    p.evaluate("""() => {
        const s = window.__xt.state();
        s.cfg.dripAt = {"2": Date.now() - 40 * 86400000};
        window.__xt.compute();
    }""")
    assert p.evaluate("() => window.__xt.dripInfo(2).why") == "not-next"


# ---------------------------------------------------------------- 6 克重与金额

def test_d20_gram_is_half_of_pending(env):
    """待补 16g × 0.5 = 8g；金额按克换算成美元。"""
    p = env().page
    backdate(p, 1, 40)
    info = p.evaluate("() => window.__xt.dripInfo(1)")
    assert info["pending"] == pytest.approx(16, abs=0.01)
    assert info["gram"] == pytest.approx(8, abs=0.01)
    assert info["amt"] == pytest.approx(8 / OZ * PRICE, abs=0.02)


def test_d21_tail_protection(env):
    """待补 1.2g，50% 只有 0.6g → 一次清掉 1.2g，不留碎片。"""
    p = env().page
    p.evaluate("() => window.__xt.markGear(1, 14.8)")
    backdate(p, 1, 40)
    info = p.evaluate("() => window.__xt.dripInfo(1)")
    assert info["pending"] == pytest.approx(1.2, abs=0.01)
    assert info["gram"] == pytest.approx(1.2, abs=0.01)


# ---------------------------------------------------------------- 7 执行

def test_d22_execute_buys_half_and_resets_clock(env):
    """点一次：买入 8g、待补剩 8g、计时重置为当下。"""
    p = env().page
    backdate(p, 1, 40)
    assert p.evaluate("() => window.__xt.doDrip(1)") is True
    st = p.evaluate("() => window.__xt.gearState(1)")
    assert st["bought"] == pytest.approx(8, abs=0.01)
    assert st["pending"] == pytest.approx(8, abs=0.01)
    days = p.evaluate("() => window.__xt.dripInfo(1).days")
    assert days < 0.01
    # 剩下 8g 继续等 30 天，不会马上再弹一次
    assert p.evaluate("() => window.__xt.dripInfo(1).canPrompt") is False


def test_d23_execute_refused_when_not_due(env):
    """没到期就点 → 返回 false，一笔不买。"""
    p = env().page
    assert p.evaluate("() => window.__xt.doDrip(1)") is False
    assert p.evaluate("() => window.__xt.gearState(1).bought") == 0


def test_d24_execute_writes_cost_at_market_price(env):
    """兜底买入按现价记成本，不按档位价（否则 realized 会虚高）。"""
    p = env().page
    backdate(p, 1, 40)
    p.evaluate("() => window.__xt.doDrip(1)")
    m = p.evaluate("() => window.__xt.state().cfg.gearMarks['1']")
    assert m["g"] == pytest.approx(8, abs=0.01)
    assert m["cost"] == pytest.approx(8 / OZ * PRICE, abs=0.05)


# ---------------------------------------------------------------- 8 边界

def test_d25_no_price_no_prompt(env):
    """现价拿不到 → 溢价算不出，不提示，也不崩。"""
    p = env(price=0).page
    backdate(p, 1, 40)
    info = p.evaluate("() => window.__xt.dripInfo(1)")
    assert info["premium"] is None
    assert info["canPrompt"] is False
    assert info["why"] in ("no-price", "wait")


def test_d26_bad_gear_index(env):
    """越界档号直接回 reason，不抛异常。"""
    p = env().page
    assert p.evaluate("() => window.__xt.dripInfo(0).why") == "bad-gear"
    assert p.evaluate("() => window.__xt.dripInfo(9).why") == "bad-gear"
    assert p.evaluate("() => window.__xt.doDrip(9)") is False


def test_d27_legacy_cfg_gets_defaults(env):
    """老数据没有 drip 四字段 → 读出来就是默认，行为一致。"""
    c = cfg()
    for k in ("dripDays", "dripPct", "dripMaxPremium", "dripAt"):
        c.pop(k, None)
    p = env(c=c).page
    got = p.evaluate("""() => {
        const c = window.__xt.state().cfg;
        return {d: c.dripDays, p: c.dripPct, q: c.dripMaxPremium};
    }""")
    assert got == {"d": 30, "p": 0.5, "q": 3}


def test_d28_plan_sum_unchanged(env):
    """兜底不改计划克重：六档仍是 [16,24,48,72,24,16]。"""
    p = env().page
    plan = p.evaluate("() => window.__xt.planOf()")
    assert plan == pytest.approx([16, 24, 48, 72, 24, 16], abs=0.01)
