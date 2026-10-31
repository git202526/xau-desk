"""P15 · 账本模式的配额下沉（bug 修复）。

起因：`planOf()` 里挂着 `&& planOnly`，账本模式直接返回原始计划，
设置页还写着「账本模式下不生效」。但账本模式买不到的理由和计划模式一模一样
（价格已经跌穿这一档），区别只在「已买」从买卖记录汇总。

口径同 P8：H=4700、总仓 200g、档距 48 → 4089 / 4041 / 3993 / 3945 / 3897 / 3849，
计划 [16, 24, 48, 72, 24, 16]，现价 4030 → 第 1 档失效，16g 挪给第 2 档。
"""
import math

import pytest

from conftest import CFG_EMPTY

H = 4700.0
W = 200.0
PX = 4030.0
BASE = [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]
OFF = {"on": False, "mode": "near", "ratio": 1, "maxDrop": 3}


def bars_calm(n=240, px=PX, amp=24.0):
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
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W, planOnly=False,
             swingGears=[1, 2], gearMarks={},
             realloc={"on": True, "mode": "near", "ratio": 1, "maxDrop": 3})
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
        mk.setdefault("price", PX)
        return make_env(cfg=base, **mk)
    return _f


def plan(p):
    return p.evaluate("() => window.__xt.planOf()")


def pbase(p):
    return p.evaluate("() => window.__xt.planBase()")


def to_plan(p):
    p.evaluate("""() => {
        if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
        window.scrollTo(0, 0);
    }""")
    p.wait_for_timeout(320)
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(460)


def to_set(p):
    p.evaluate("""() => {
        if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
        window.scrollTo(0, 0);
    }""")
    p.wait_for_timeout(320)
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(460)


def shifts(p):
    return p.evaluate("""() => [...document.querySelectorAll('#ladder .shift')]
        .map(x => ({gear: x.getAttribute('data-shift'), txt: x.textContent}))""")


# ---------------------------------------------------------------- 1 引擎

def test_l1_off_equals_base(env):
    """开关关闭时账本模式与原始计划逐值相等（默认关闭，老行为不变）。"""
    p = env(cfg(realloc=OFF)).page
    assert plan(p) == pytest.approx(pbase(p), abs=1e-9)


def test_l2_ledger_reallocs(env):
    """开关开着且第 1 档跌破 → 账本模式真的重分配，且池子分完（守恒）。"""
    p = env().page
    eff, base = plan(p), pbase(p)
    assert eff != pytest.approx(base, abs=1e-9)
    assert sum(eff) == pytest.approx(sum(base), abs=0.01)
    assert eff[0] == pytest.approx(0, abs=0.01), "跌破的第 1 档不该再留待补"
    assert eff[1] > base[1], "第 2 档应该收到额度"


def test_l3_same_as_plan_mode(env):
    """两边都还没买过时，账本模式与计划模式的分配结果逐值相同。"""
    a = env(cfg(planOnly=False)).page
    b = env(cfg(planOnly=True)).page
    assert plan(a) == pytest.approx(plan(b), abs=1e-9)


def test_l4_bought_comes_from_ledger(env):
    """账本模式的「已买」确实来自买卖记录：第 1 档买满了，跌破也没有额度可让。"""
    p = env(cfg(buys=[{"id": "b1", "name": "第1档", "gram": 16, "priceCny": 900.0,
                       "time": "2026-10-01T02:00:00.000Z", "note": "", "gear": "第1档"}])).page
    assert plan(p) == pytest.approx(pbase(p), abs=1e-9)


def test_l5_rebound_restores(env):
    """现价回到第 1 档上方 → 不下沉，计划回原样。"""
    p = env(price=4095.0).page
    assert plan(p) == pytest.approx(pbase(p), abs=1e-9)


# ---------------------------------------------------------------- 2 界面

def test_l6_shifts_rendered(env):
    """档位行同时出现「下沉出」与「下沉入」两枚标签。"""
    p = env().page
    to_plan(p)
    s = shifts(p)
    assert len(s) == 2, s
    assert any("下沉出" in x["txt"] for x in s)
    assert any("下沉入" in x["txt"] for x in s)


def test_l7_ammo_pool(env):
    """弹药进度行标出下沉克重。"""
    p = env().page
    to_plan(p)
    assert "下沉 16g" in p.inner_text("#ammoTxt")


def test_l8_set_live(env):
    """设置页给出下沉明细，不再说「账本模式下不生效」。"""
    p = env().page
    to_set(p)
    live = p.inner_text("#setReLive")
    assert "不生效" not in live
    assert "已下沉 16 g" in live
    assert "第1档" in live and "第2档" in live


def test_l9_nav_chip(env):
    """速览条 chip 显示「下沉开」而不是「—」。"""
    p = env().page
    to_set(p)
    items = p.evaluate("() => [...document.querySelectorAll('#setNav button')].map(b => b.textContent)")
    assert items[4] == "下沉开", items


# ---------------------------------------------------------------- 3 双端

@pytest.mark.parametrize("width", [390, 320])
def test_l10_no_overflow(env, width):
    """账本模式带两枚标签时窄屏不溢出。"""
    p = env(width=width).page
    to_plan(p)
    assert len(shifts(p)) == 2
    assert p.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
