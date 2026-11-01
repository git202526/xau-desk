"""P16 · 减仓摊派：卖出不指定档位，按各档净持占比摊到标记。

需求口径（用户确认）：
1. 档位表保留「已持」= 净持（累计买入 − 累计卖出）
2. 卖出释放的可买重量按配额重新分配，落到各档待补
3. 已持超过计划的档（溢出）不参与下沉

摊完的效果：净持变小 → 待补变大 → 空出来的量按原权重分布回各档。
"""
import math

import pytest

from conftest import CFG_EMPTY

H = 4700.0
TOTAL = 200.0
PX = 4030.0
BASE = [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]     # 权重 8:12:24:36:12:8


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


def marks(grams=None):
    """按 BASE 或指定的每档克重造标记（g 是累计买入）"""
    gs = list(grams) if grams else list(BASE)
    out = {}
    for i, g in enumerate(gs, 1):
        if g <= 0:
            continue
        out[str(i)] = {"g": g, "cost": 0, "sold": 0, "proceeds": 0, "at": 1}
    return out


def cfg(gm, **kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, planOnly=True,
             swingGears=[1, 2], gearMarks=gm,
             realloc={"on": False, "mode": "near", "ratio": 1, "maxDrop": 3})
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    def _f(gm=None, **kw):
        base = cfg(gm if gm is not None else marks(), **kw)
        base["bars"] = bars_calm()
        return make_env(cfg=base, price=PX)
    return _f


def state(p, i):
    return p.evaluate("(i) => window.__xt.gearState(i)", i)


def pends(p):
    return p.evaluate("() => [1,2,3,4,5,6].map(i => window.__xt.gearState(i).pending)")


def net(p):
    return p.evaluate("() => [1,2,3,4,5,6].map(i => window.__xt.gearState(i).bought)")


# ---------- 摊派本身 ----------

def test_a1_full_hold_sell_half(env):
    """六档全买满 200g，卖 100g → 各档净持减半，待补均分 100g。"""
    e = env()
    p = e.page
    # P21 契约变更：返回值由「摊到的档数」变成 { lot, hit }，档数改读 .hit
    assert p.evaluate("() => window.__xt.allotSell(100, 4000).hit") == 6
    got = [round(x, 2) for x in net(p)]
    assert got == [8, 12, 24, 36, 12, 8]
    pend = [round(x, 2) for x in pends(p)]
    assert pend == [8, 12, 24, 36, 12, 8]
    assert sum(pend) == pytest.approx(100, abs=0.05)


def test_a2_pending_sum_equals_released(env):
    """Σ待补 = 卖出量 —— 释放的仓位没有凭空多出或少掉。"""
    e = env()
    p = e.page
    before = sum(pends(p))
    assert before == pytest.approx(0, abs=0.05)
    p.evaluate("() => window.__xt.allotSell(60, 4000)")
    assert sum(pends(p)) == pytest.approx(60, abs=0.05)


def test_a3_only_first_gear_held(env):
    """只买满第 1 档 16g，卖 8g → 全落在第 1 档，其余档待补不变。"""
    e = env(gm=marks([16, 0, 0, 0, 0, 0]))
    p = e.page
    assert p.evaluate("() => window.__xt.allotSell(8, 4000).hit") == 1
    assert state(p, 1)["soldG"] == pytest.approx(8)
    assert state(p, 1)["pending"] == pytest.approx(8)
    # 其余档仍是「一克没买」，待补 = 计划
    assert state(p, 2)["pending"] == pytest.approx(24)
    assert state(p, 2)["pending"] == pytest.approx(24)


def test_a4_cannot_sell_more_than_held(env):
    """卖得比持仓多时，只按总净持封顶，不会把 sold 顶到 g 以上。"""
    e = env(gm=marks([16, 0, 0, 0, 0, 0]))
    p = e.page
    p.evaluate("() => window.__xt.allotSell(50, 4000)")
    assert state(p, 1)["soldG"] == pytest.approx(16, abs=0.05)
    assert state(p, 1)["bought"] == pytest.approx(0, abs=0.05)


def test_a5_rounding_exact(env):
    """33.3g 摊六档，尾差要补齐，合计精确到 0.1。"""
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.allotSell(33.3, 4000)")
    parts = p.evaluate("() => [1,2,3,4,5,6].map(i => window.__xt.gearState(i).soldG)")
    assert sum(parts) == pytest.approx(33.3, abs=0.05)


def test_a6_split_by_gram_weights(env):
    """纯函数：按 8:12:24:36:12:8 分 100g。"""
    e = env()
    p = e.page
    got = p.evaluate("() => window.__xt.splitByGram(100, [8,12,24,36,12,8])")
    assert [round(x, 2) for x in got] == [8, 12, 24, 36, 12, 8]


# ---------- 溢出 ----------

def test_b1_over_bought_shows_over(env):
    """第 1 档买 30g、计划 16g → 溢出 14g。"""
    e = env(gm=marks([30, 24, 48, 72, 24, 16]))
    p = e.page
    st = state(p, 1)
    assert st["bought"] == pytest.approx(30)
    assert st["over"] == pytest.approx(14, abs=0.05)
    assert st["pending"] == pytest.approx(0, abs=0.05)


def test_b2_over_gear_takes_no_topup(env):
    """溢出的档不接收下沉额度：realloc 的接收方里不出现第 1 档。"""
    e = env(gm=marks([30, 24, 48, 72, 24, 16]),
            realloc={"on": True, "mode": "near", "ratio": 1, "maxDrop": 3})
    p = e.page
    inn = p.evaluate("() => window.__xt.reallocInfo().inn.map(x => x.i)")
    assert 1 not in inn


def test_b3_over_gear_keeps_pending_zero(env):
    """溢出档无论价格怎么跌，待补都保持 0（它已经买超了）。"""
    e = env(gm=marks([30, 24, 48, 72, 24, 16]))
    p = e.page
    assert state(p, 1)["pending"] == pytest.approx(0, abs=0.05)


# ---------- 与账本的关系 ----------

def test_c1_sell_record_has_no_gear(env):
    """账本里的减仓记录仍不带档位 —— 摊派只发生在标记层。"""
    e = env()
    p = e.page
    p.evaluate("""() => {
      var c = window.__xt.state().cfg;
      c.sells = [{id:'s1', name:'减仓', gram:100, priceCny:900,
                  time:new Date().toISOString(), note:''}];
    }""")
    rec = p.evaluate("() => window.__xt.state().cfg.sells[0]")
    assert "gear" not in rec
    assert "alloc" not in rec


def test_c2_mark_unchanged_by_sell_record_typed(env):
    """只写记录、不摊标记时，档位净持不变（两条轨各管各的）。"""
    e = env()
    p = e.page
    before = net(p)
    p.evaluate("""() => {
      var c = window.__xt.state().cfg;
      c.sells = [{id:'s1', name:'减仓', gram:100, priceCny:900,
                  time:new Date().toISOString(), note:''}];
      window.__xt.compute();
    }""")
    assert net(p) == before
