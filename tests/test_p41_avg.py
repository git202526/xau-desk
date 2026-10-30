"""v6.18 单引擎 · 目标均价导向（原 v6.12「档位双逻辑」重写）。

语义反转：不再「改高点去凑目标」，而是「高点钉死在 0.87H、拉档距去凑目标」。

  p_i = 0.87·H − (i−1)·d
  d   = (0.87·H − T) / ī          ī = 2.56（默认分布）

基准：H=4800、T=3950、W=200 → 0.87H = 4176、d = 88.28125、第 4 档 3911.16。
"""
import json
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H_STRUCT = 4800.0
T = 3950.0
BAND = 20.0
TOTAL = 200

P1 = 0.87 * H_STRUCT          # 4176
D_3950 = (P1 - T) / 2.56      # 88.28125

def bars_calm(n=240, px=PRICE, amp=24.0):
    """ATR14 ≈ 48 美元的日线序列：档距按 α×ATR 标定时才有意义。

    合成 K 线的默认振幅只有 21 美元（ATR≈20），它会让 d=88 直接撞上
    α=3.5 的上限，测不出「反解命中」这条主线。
    """
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


BARS = bars_calm()

CFG_T = dict(CFG_EMPTY, **{"H_BASE": H_STRUCT, "TOTAL": TOTAL,
                           "avgTarget": T, "avgBand": BAND})
CFG_NO_T = dict(CFG_EMPTY, **{"H_BASE": H_STRUCT, "TOTAL": TOTAL})
CFG_AUTO = dict(CFG_EMPTY, **{"TOTAL": TOTAL})


def open_plan(page):
    page.click('.tabbar button[data-tab="plan"]')
    page.wait_for_timeout(280)


def rows(page):
    return page.evaluate("() => window.__xt.gearRows()")


def row(rs, name):
    for r in rs:
        if r["name"] == name:
            return r
    raise AssertionError("找不到档位 %s：%s" % (name, json.dumps(rs, ensure_ascii=False)))


def meta(page):
    return page.evaluate("() => window.__xt.gearMeta()")


def base(page):
    return page.evaluate("() => window.__xt.gearBase()")


def avgcard(page):
    return page.evaluate("""() => {
        const b = document.getElementById('avgCard');
        if (!b || !b.textContent.trim()) return null;
        const c = b.querySelector('circle[r="3.8"]');
        return {
            text: b.textContent.replace(/\\s+/g, ' ').trim(),
            dot: c ? c.getAttribute('fill') : null,
            svg: b.querySelectorAll('svg').length,
        };
    }""")


# ---------------------------------------------------------------- 反解

def test_av1_span_from_target(make_env):
    """目标 → 档距：一步除法，没有迭代、没有二分。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    b = base(p)
    assert b["d"] == pytest.approx(D_3950, abs=1e-6)
    assert b["spanSrc"] == "target"
    assert b["target"] == T
    assert b["he"] == H_STRUCT


def test_av14_span_moves_with_target(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    d1 = base(p)["d"]
    p.evaluate("() => window.__xt.setTargetT(3900)")
    d2 = base(p)["d"]
    assert d2 > d1, "目标越低档距越大"
    assert d2 == pytest.approx((P1 - 3900) / 2.56, abs=1e-6)
    p.evaluate("() => window.__xt.setTargetT(3950)")
    assert base(p)["d"] == pytest.approx(d1, abs=1e-6)


def test_av2_fourth_gear_price(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_plan(p)
    rs = rows(p)
    assert row(rs, "第1档")["price"] == pytest.approx(P1, abs=0.01)
    assert row(rs, "第4档")["price"] == pytest.approx(P1 - 3 * D_3950, abs=0.02)   # 3911.16
    assert row(rs, "第6档")["price"] == pytest.approx(P1 - 5 * D_3950, abs=0.02)


def test_av3_plan_avg_six_gears(make_env):
    """六档全部计入均价（旧版只算 1–4 档）。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    a = p.evaluate("() => window.__xt.planAvg(4800, null)")
    assert a == pytest.approx(T, abs=0.05)
    assert meta(p)["avgAll"] == pytest.approx(T, abs=0.05)


def test_av4_weights_drive_plan(make_env):
    """计划克重 = 权重 × 总仓，与已买、与目标都无关。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    plan = p.evaluate("() => window.__xt.planOf()")
    assert [round(x, 4) for x in plan] == [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]
    p.evaluate("() => window.__xt.setTargetT(4000)")
    assert [round(x, 4) for x in p.evaluate("() => window.__xt.planOf()")] \
        == [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]


def test_av5_no_target_uses_alpha(make_env):
    """目标留空 = 按波动率排（α=1），等价于旧版「结构基准」。"""
    env = make_env(CFG_NO_T, bars=BARS)
    p = env.page
    atr = p.evaluate("() => window.__xt.atrUsd()")
    b = base(p)
    assert b["target"] is None
    assert b["spanSrc"] in ("atr", "fallback")
    assert b["d"] == pytest.approx(atr, abs=0.02)
    assert b["alpha"] == pytest.approx(1.0, abs=1e-9)
    assert row(rows(p), "第1档")["price"] == pytest.approx(P1, abs=0.01)


def test_av16_target_keeps_takes_levels(make_env):
    """设目标只动档位价，不动止盈计划与关键位。"""
    env = make_env(CFG_NO_T, bars=BARS)
    p = env.page
    open_plan(p)
    before = p.evaluate("() => window.__xt.state().takes")
    lv0 = p.evaluate("() => (window.__xt.state().levels || []).length")
    p.evaluate("() => window.__xt.setTargetT(3950)")
    p.wait_for_timeout(280)
    assert p.evaluate("() => window.__xt.state().takes") == before
    assert p.evaluate("() => (window.__xt.state().levels || []).length") == lv0


def test_av6_target_keeps_records(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    n0 = p.evaluate("() => (window.__xt.state().cfg.buys || []).length")
    p.evaluate("() => window.__xt.setTargetT(3900)")
    p.evaluate("() => window.__xt.setAlpha(1.4)")
    p.wait_for_timeout(280)
    assert p.evaluate("() => (window.__xt.state().cfg.buys || []).length") == n0
    assert p.evaluate("() => (window.__xt.state().cfg.sells || []).length") == 0
    assert p.evaluate("() => window.__xt.holdStats().grams") == 0


def test_av7_target_persists(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    p.evaluate("() => window.__xt.setTargetT(3900)")
    p.wait_for_timeout(1300)
    assert env.gh.puts > 0
    assert p.evaluate("() => window.__xt.state().cfg.avgTarget") == 3900
    p.reload(wait_until="domcontentloaded")
    p.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    p.wait_for_function("() => window.__xt.gearBase().target == 3900", timeout=6000)
    assert base(p)["d"] == pytest.approx((P1 - 3900) / 2.56, abs=0.02)


def test_av8_no_high_no_target_still_ladders(make_env):
    """高点和目标都没有：用自动高点 + α=1，页面不空转。"""
    env = make_env(CFG_AUTO, bars=BARS)
    p = env.page
    rs = rows(p)
    assert len([r for r in rs if not r["warn"]]) == 6
    assert row(rs, "第1档")["price"] > 0
    b = base(p)
    assert b["highManual"] is False
    assert b["he"] > 0
    assert b["target"] is None


def test_av11_gram_scales_with_total(make_env):
    """总仓变 → 克重等比，价格一个都不动。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    pr0 = [r["price"] for r in rows(p)]
    plan0 = p.evaluate("() => window.__xt.planOf()")
    p.evaluate("() => { window.__xt.state().TOTAL = 400; window.__xt.compute(); window.__xt.refreshAll(); }")
    p.wait_for_timeout(280)
    plan1 = p.evaluate("() => window.__xt.planOf()")
    assert [round(x, 4) for x in plan1] == [round(x * 2, 4) for x in plan0]
    assert [r["price"] for r in rows(p)] == pr0


def test_av12_held_better_than_target(make_env):
    """已持仓成本优于目标 → 明确提示「已达标」，不给错误的档距。"""
    cny = 4000 * 7.10 / 31.1035
    c = dict(CFG_T, buys=[{"id": "b1", "name": "底仓", "gram": 199,
                           "priceCny": cny, "time": "2026-09-01T02:00:00.000Z",
                           "note": "", "gear": None}])
    env = make_env(c, bars=BARS)
    p = env.page
    assert base(p)["hint"] == "done"
    open_plan(p)
    assert "已持仓成本优于目标" in avgcard(p)["text"]


def test_av9_card_hit(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_plan(p)
    m = meta(p)
    assert m["hit"] is True
    assert m["avgAll"] == pytest.approx(T, abs=0.05)
    card = avgcard(p)
    assert "命中" in card["text"]
    assert card["dot"] == "#3FBF7F"


def test_av10_card_off_target(make_env):
    """持仓成本太高、剩余额度换不回目标 → 档距撞上限，卡片明确「做不到」。"""
    cny = 4700 * 7.10 / 31.1035
    c = dict(CFG_T, buys=[{"id": "b1", "name": "高位仓", "gram": 60,
                           "priceCny": cny, "time": "2026-09-01T02:00:00.000Z",
                           "note": "", "gear": None}])
    env = make_env(c, bars=BARS)
    p = env.page
    open_plan(p)
    m = meta(p)
    assert m["hit"] is False
    assert m["hint"] == "cap"
    card = avgcard(p)
    assert "做不到" in card["text"], card["text"]
    assert card["dot"] == "#E0913F"


@pytest.mark.parametrize("width", [320, 390])
def test_av13_narrow_layout(make_env, width):
    env = make_env(CFG_T, width=width)
    p = env.page
    open_plan(p)
    box = p.evaluate("""() => {
        const c = document.getElementById('avgCard');
        const r = c.getBoundingClientRect();
        const doc = document.documentElement;
        return {w: Math.round(r.width), right: Math.round(r.right),
                scroll: doc.scrollWidth, win: window.innerWidth,
                hasBtn: !!c.querySelector('button')};
    }""")
    assert box["hasBtn"] is True
    assert box["scroll"] <= box["win"] + 1, box
    assert box["right"] <= box["win"] + 1, box


def test_av15_persist_across_reload(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    p.evaluate("() => window.__xt.setTargetT(3920)")
    p.wait_for_timeout(1300)
    p.reload(wait_until="domcontentloaded")
    p.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    p.wait_for_function("() => window.__xt.gearBase().target == 3920", timeout=6000)
    open_plan(p)
    assert "3920" in avgcard(p)["text"]
