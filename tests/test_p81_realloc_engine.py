"""P8 配额重分配 · 第 1 期（引擎层）。

验：开关关闭时 planOf 与 planBase 逐值相等、失效判定与滞回、同角色内互挪、
就近/等比两种分配、比例与落点保护、无接收方时撤销、守恒、账本模式不生效。

口径：H=4700、总仓 200g、档距 48 → 档位 4089 / 4041 / 3993 / 3945 / 3897 / 3849，
计划 [16, 24, 48, 72, 24, 16]。默认现价 4072.40：
  1 档失效线 4089×0.99 = 4048.11 → 4072.40 高于它，一档都不失效。
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H = 4700.0
W = 200.0
BASE = [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]
P1, P2, P3, P4 = 4089.0, 4041.0, 3993.0, 3945.0


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


def cfg(realloc=None, **kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W, planOnly=True,
             swingGears=[1, 2], gearMarks={},
             realloc=realloc if realloc is not None else {"on": True, "mode": "near", "ratio": 1, "maxDrop": 3})
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
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


def set_px(p, px):
    p.evaluate("(v) => { window.__xt.state().price = v; window.__xt.compute(); }", px)
    p.wait_for_timeout(80)


def plan(p):
    return p.evaluate("() => window.__xt.planOf()")


def pbase(p):
    return p.evaluate("() => window.__xt.planBase()")


# ---------------------------------------------------------------- 1 开关与等价

def test_r1_off_equals_base(env):
    """默认关闭：planOf 与 planBase 逐值相等（老行为一字不改）。"""
    p = env(realloc={"on": False}).page
    a = plan(p)
    b = pbase(p)
    assert a == pytest.approx(b, abs=1e-9)
    assert a == pytest.approx(BASE, abs=0.01)


def test_r2_on_but_nothing_dead(env):
    """开着但现价高于全部失效线 → 不动。"""
    p = env().page
    assert p.evaluate("() => window.__xt.reallocInfo().out") == []
    assert plan(p) == pytest.approx(BASE, abs=0.01)


def test_r3_default_cfg_is_off(env):
    """老数据没有 realloc 字段 → 读出默认关闭。"""
    c = cfg()
    c.pop("realloc", None)
    p = env(c=c).page
    r = p.evaluate("() => window.__xt.state().cfg.realloc")
    assert r == {"on": False, "mode": "near", "ratio": 1, "maxDrop": 3}


# ---------------------------------------------------------------- 2 基本重分配

def test_r4_swing_pool_moves_to_next_swing(env):
    """现价 4030 跌破第 1 档 → 16g 挪给同角色的第 2 档（24 → 40）。"""
    p = env(price=4030.0).page
    got = plan(p)
    assert got == pytest.approx([0, 40, 48, 72, 24, 16], abs=0.01)


def test_r5_sum_conserved(env):
    """池子必须分完：总和恒等于 200。"""
    p = env(price=4030.0).page
    assert sum(plan(p)) == pytest.approx(W, abs=0.02)


def test_r6_no_receiver_keeps_original(env):
    """第 2 档已买满 → 循环档没有接收方 → 整组不挪。"""
    p = env(price=4030.0).page
    p.evaluate("() => window.__xt.markGear(2, 24)")
    assert plan(p) == pytest.approx(BASE, abs=0.01)


def test_r7_build_pool_moves_down(env):
    """现价 3920 跌破第 3 档 → 48g 给第 4 档（72 → 120），循环档不动。"""
    p = env(price=3920.0).page
    got = plan(p)
    assert got[0] == pytest.approx(16, abs=0.01)      # 1 档没失效？4030 才算
    assert got[2] == pytest.approx(0, abs=0.01)
    assert got[3] == pytest.approx(120, abs=0.01)
    assert sum(got) == pytest.approx(W, abs=0.02)


def test_r8_roles_do_not_mix(env):
    """第 1 档失效时，建仓档（3–6）克重一分不动。"""
    p = env(price=4030.0).page
    got = plan(p)
    assert got[2:] == pytest.approx(BASE[2:], abs=0.01)


def test_r9_overbought_gear_donates_nothing(env):
    """第 1 档超买（买 20 > 计划 16）→ 可捐额为 0，不产生负数。"""
    p = env(price=4030.0).page
    p.evaluate("() => window.__xt.markBuy(1, 20)")
    assert plan(p) == pytest.approx(BASE, abs=0.01)


# ---------------------------------------------------------------- 3 比例

def test_r10_ratio_half(env):
    """比例 0.5：第 1 档留 8g，第 2 档 24 → 32。"""
    p = env(price=4030.0, realloc={"on": True, "mode": "near", "ratio": 0.5, "maxDrop": 3}).page
    assert plan(p) == pytest.approx([8, 32, 48, 72, 24, 16], abs=0.01)


def test_r11_ratio_bounds(env):
    """比例 0.05 越界回 1；0.3 保留。"""
    p = env().page
    assert p.evaluate("() => window.__xt.setRealloc({ratio: 0.05}).ratio") == 1
    assert p.evaluate("() => window.__xt.setRealloc({ratio: 0.3}).ratio") == 0.3


def test_r12_only_pending_is_donated(env):
    """第 1 档已买 10g → 只捐待补的 6g，第 2 档 24 → 30。"""
    p = env(price=4030.0).page
    p.evaluate("() => window.__xt.markBuy(1, 10)")
    got = plan(p)
    assert got[0] == pytest.approx(10, abs=0.01)
    assert got[1] == pytest.approx(30, abs=0.01)


# ---------------------------------------------------------------- 4 两种模式

def test_r13_near_gives_all_to_closest(env):
    """就近：48g 全压第 4 档（72 → 120），5/6 档不动。"""
    p = env(price=3920.0, realloc={"on": True, "mode": "near", "ratio": 1, "maxDrop": 3}).page
    got = plan(p)
    assert got[3] == pytest.approx(120, abs=0.01)
    assert got[4] == pytest.approx(24, abs=0.01)
    assert got[5] == pytest.approx(16, abs=0.01)


def test_r14_prop_splits_by_weight(env):
    """等比：48g 按 72:24:16 摊给 4/5/6 档。"""
    p = env(price=3920.0, realloc={"on": True, "mode": "prop", "ratio": 1, "maxDrop": 3}).page
    got = plan(p)
    assert got[3] == pytest.approx(72 + 48 * 72 / 112, abs=0.02)
    assert got[4] == pytest.approx(24 + 48 * 24 / 112, abs=0.02)
    assert got[5] == pytest.approx(16 + 48 * 16 / 112, abs=0.02)
    assert sum(got) == pytest.approx(W, abs=0.02)


def test_r15_landing_guard_falls_back_to_prop(env):
    """落点太深 → 就近退化为等比。
    现价 3950：第 3 档（3993）失效；第 4 档买满，接收方是第 5 档（3897，比现价低 1.3%）。
    maxDrop=0.5 时 1.3% 算「太远」，改按 24:16 摊给 5/6 档。"""
    p = env(price=3950.0, realloc={"on": True, "mode": "near", "ratio": 1, "maxDrop": 0.5}).page
    p.evaluate("() => window.__xt.markGear(4, 72)")
    got = plan(p)
    assert got[2] == pytest.approx(0, abs=0.01)
    assert got[4] == pytest.approx(24 + 48 * 24 / 40, abs=0.02)      # 52.8，不是 72
    assert got[5] == pytest.approx(16 + 48 * 16 / 40, abs=0.02)


# ---------------------------------------------------------------- 5 滞回

def test_r16_hysteresis(env):
    """4045 失效（线在 4048.11）→ 4070 仍失效 → 4080 才恢复（退出线 4076.7）。"""
    p = env(realloc={"on": True, "mode": "near", "ratio": 1, "maxDrop": 3}).page
    set_px(p, 4045.0)
    assert plan(p)[0] == pytest.approx(0, abs=0.01)
    set_px(p, 4070.0)
    assert plan(p)[0] == pytest.approx(0, abs=0.01), "0.5% 的回弹不该立刻恢复"
    set_px(p, 4080.0)
    assert plan(p) == pytest.approx(BASE, abs=0.01)


def test_r17_revive_is_full_restore(env):
    """价格涨回档位上方 → planOf 完全回到 planBase，不留残余。"""
    p = env(realloc={"on": True, "mode": "near", "ratio": 1, "maxDrop": 3}).page
    set_px(p, 4030.0)
    assert plan(p)[0] == 0
    set_px(p, 4095.0)
    assert plan(p) == pytest.approx(pbase(p), abs=1e-9)


# ---------------------------------------------------------------- 6 边界

def test_r18_ledger_mode_reallocs(env):
    """P15 需求反转：账本模式同样下沉 —— 买不到的理由和计划模式一模一样，
    只是「已买」从买卖记录汇总。旧契约是「账本模式不生效」，按新需求改写：
    仍然要求「真的挪了」且「总量守恒」，不比原来松。"""
    p = env(planOnly=False).page
    set_px(p, 4030.0)
    eff = plan(p)
    assert eff != pytest.approx(pbase(p), abs=1e-9), "账本模式必须真的重分配"
    assert sum(eff) == pytest.approx(sum(pbase(p)), abs=0.01), "池子必须分完"
    assert eff[0] == pytest.approx(0, abs=0.01), "跌破的第 1 档不该再留待补"


def test_r19_no_price(env):
    """没有现价 → 直接返回原始计划。"""
    p = env(price=0).page
    assert plan(p) == pytest.approx(BASE, abs=0.01)


def test_r20_all_dead_no_move(env):
    """全部档位都跌破 → 每个角色都没有接收方 → 一克不挪。"""
    p = env(price=3600.0).page
    assert plan(p) == pytest.approx(BASE, abs=0.01)


def test_r21_off_switch_restores_immediately(env):
    """关掉开关立刻回原样，不需要撤销操作。"""
    p = env(price=4030.0).page
    assert plan(p)[1] == pytest.approx(40, abs=0.01)
    p.evaluate("() => window.__xt.setRealloc({on: false})")
    assert plan(p) == pytest.approx(BASE, abs=0.01)


def test_r22_ratio_and_maxdrop_bounds(env):
    """maxDrop 越界回 3；0.8 保留。"""
    p = env().page
    assert p.evaluate("() => window.__xt.setRealloc({maxDrop: 0.1}).maxDrop") == 3
    assert p.evaluate("() => window.__xt.setRealloc({maxDrop: 50}).maxDrop") == 3
    assert p.evaluate("() => window.__xt.setRealloc({maxDrop: 0.8}).maxDrop") == 0.8


# ---------------------------------------------------------------- 7 明细与守恒

def test_r23_info_lists_pairs(env):
    """明细：out 记第 1 档捐 16，inn 记第 2 档收 16，pool = 16。"""
    p = env(price=4030.0).page
    info = p.evaluate("() => window.__xt.reallocInfo()")
    assert info["out"] == [{"i": 1, "g": 16}]
    assert info["inn"] == [{"i": 2, "g": 16}]
    assert info["pool"] == pytest.approx(16, abs=0.01)


def test_r24_sum_conserved_across_prices(env):
    """六组价格下总和恒为 200（池子不会凭空多出或丢失克重）。"""
    p = env().page
    for px in (4100.0, 4050.0, 4030.0, 3980.0, 3920.0, 3800.0):
        set_px(p, px)
        assert sum(plan(p)) == pytest.approx(W, abs=0.05), px


def test_r25_filled_gear_not_receiver(env):
    """已买满的档不当接收方：第 1 档失效、第 2 档满 → 不挪。"""
    p = env(price=4030.0).page
    p.evaluate("() => window.__xt.markGear(2, 24)")
    info = p.evaluate("() => window.__xt.reallocInfo()")
    assert info["inn"] == [] and info["out"] == []
