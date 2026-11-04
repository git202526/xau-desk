# -*- coding: utf-8 -*-
"""盈利测算：独立卡片 / 六档两行三列 / 命中才变红。

1. 从「持仓汇总」卡片内部搬出来，在它下方独立成卡（原来套在 #posBox 里，
   跟着汇总卡的内边距走，视觉上像是汇总的附属信息）。
2. 档位从 4 个（5/10/15/20%）扩到 6 个（加 25/30%），排两行三列。
3. 浮动盈亏收益率贴近某档（±2 个百分点）时，只有那一档变红 + 卡片红底脉冲；
   其余一律金色。全红等于没重点 —— 六张都红谁也看不出现在站在哪。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conftest import CFG_NORMAL

COST = 972.01
GRAM = 328.8848

GOLD = "rgb(212, 162, 76)"     # --gold: #D4A24C
RED = "rgb(226, 96, 79)"       # #E2604F


def cfg(cost=COST, gram=GRAM):
    c = dict(CFG_NORMAL)
    c["stopMode"] = False
    c["keepGram"] = 0
    c["takePlan"] = {}
    c["comeback"] = 10
    c["buys"] = [{"id": "b1", "name": "建仓", "gram": gram, "priceCny": cost,
                  "time": "2026-08-12T02:00:00.000Z", "note": "", "gear": None, "acct": "a0"}]
    return c


def pos(make_env, c, width=430):
    e = make_env(cfg=c, width=width, height=1600)
    p = e.page
    p.wait_for_function("() => window.__xt.state().takes", timeout=8000)
    p.evaluate("() => window.__xt.gotoTab('pos')")
    p.wait_for_timeout(300)
    return e, p


def pctv(p):
    """当前浮动盈亏收益率（%），与卡片判断命中用的是同一个数。"""
    return p.evaluate("""() => {
        var S = window.__xt.state(), h = window.__xt.holdStats();
        var now = S.price * S.fx / 31.1035;
        return (now / h.avg - 1) * 100;
    }""")


def cost_for(want_pct, p):
    """让收益率正好等于 want_pct 的成本价。"""
    now = p.evaluate("""() => {
        var S = window.__xt.state();
        return S.price * S.fx / 31.1035;
    }""")
    return now / (1 + want_pct / 100.0)


def test_g1_card_is_separate_from_summary(make_env):
    """独立成卡：不在 #posBox 内部，位置紧接汇总卡、在账户卡之前。"""
    e, p = pos(make_env, cfg())
    assert p.eval_on_selector_all("#calcBox", "els => els.length") == 1
    assert p.eval_on_selector_all("#posBox #calcBox", "els => els.length") == 0
    order = p.evaluate("""() => {
        var a = document.querySelector('#posBox').getBoundingClientRect();
        var c = document.querySelector('#calcBox').getBoundingClientRect();
        var b = document.querySelector('#acctBox').getBoundingClientRect();
        return a.bottom <= c.top + 1 && c.bottom <= b.top + 1;
    }""")
    assert order


def test_g2_title_renamed_to_profit_measure(make_env):
    """标题「盈亏试算」→「盈利测算」。"""
    e, p = pos(make_env, cfg())
    t = p.inner_text("#calcBox .pch")
    assert "盈利测算" in t, t
    assert "盈亏试算" not in t, t


def test_g3_six_steps_in_three_columns(make_env):
    """六档：5/10/15/20/25/30%，两行三列。"""
    e, p = pos(make_env, cfg())
    steps = p.eval_on_selector_all("#calcBox .pci", "els => els.map(e => e.dataset.p)")
    assert steps == ["5", "10", "15", "20", "25", "30"], steps
    cols = p.eval_on_selector(
        "#calcBox .pcgrid", "e => getComputedStyle(e).gridTemplateColumns.split(' ').length")
    assert cols == 3, cols


def test_g4_near_level_turns_red(make_env):
    """收益率在 +10% 档 ±2 内：只有那档变红。"""
    p0 = make_env(cfg=cfg(cost=800.0), width=430, height=1600).page
    p0.wait_for_function("() => window.__xt.ready", timeout=8000)
    cost = cost_for(10.0, p0)
    e, p = pos(make_env, cfg(cost=round(cost, 2)))
    v = pctv(p)
    assert abs(v - 10) <= 2, v
    on = p.eval_on_selector_all("#calcBox .pci.on", "els => els.map(e => e.dataset.p)")
    assert on == ["10"], on
    assert p.eval_on_selector("#calcBox .pci[data-p='10'] b",
                              "e => getComputedStyle(e).color") == RED


def test_g5_card_has_pulse_animation(make_env):
    """命中的卡片要有过渡色闪动，不是死板的红底。"""
    p0 = make_env(cfg=cfg(cost=800.0), width=430, height=1600).page
    p0.wait_for_function("() => window.__xt.ready", timeout=8000)
    cost = cost_for(10.0, p0)
    e, p = pos(make_env, cfg(cost=round(cost, 2)))
    anim = p.eval_on_selector("#calcBox .pci.on", "e => getComputedStyle(e).animationName")
    assert anim != "none", anim
    bg = p.eval_on_selector("#calcBox .pci.on", "e => getComputedStyle(e).backgroundColor")
    assert bg.startswith("rgba"), bg


def test_g6_between_levels_all_gold(make_env):
    """收益率落在两档中间（12.5%）：一张都不红，金额与价位全金。"""
    p0 = make_env(cfg=cfg(cost=800.0), width=430, height=1600).page
    p0.wait_for_function("() => window.__xt.ready", timeout=8000)
    cost = cost_for(12.5, p0)
    e, p = pos(make_env, cfg(cost=round(cost, 2)))
    v = pctv(p)
    assert all(abs(v - k) > 2 for k in (5, 10, 15, 20, 25, 30)), v
    assert p.eval_on_selector_all("#calcBox .pci.on", "els => els.length") == 0
    amt = p.eval_on_selector_all("#calcBox .pci b", "els => els.map(e => getComputedStyle(e).color)")
    px = p.eval_on_selector_all("#calcBox .pci em", "els => els.map(e => getComputedStyle(e).color)")
    assert set(amt) == {GOLD}, amt
    assert set(px) == {GOLD}, px


def test_g7_loss_state_all_gold(make_env):
    """亏损态（默认 -4.4%）不可能命中正档位，全金。"""
    e, p = pos(make_env, cfg())
    v = pctv(p)
    assert v < 0, v
    assert p.eval_on_selector_all("#calcBox .pci.on", "els => els.length") == 0


def test_g8_hint_explains_distance(make_env):
    """卡片底部说清当前浮动与目标档，才知道为什么红/为什么不红。"""
    e, p = pos(make_env, cfg())
    hint = p.inner_text("#calcBox .pcnear")
    assert "浮动" in hint, hint
    assert "+5% 档" in hint, hint      # 亏损态：下一个档就是 5%


def test_g9_amounts_use_six_steps(make_env):
    """25% / 30% 的金额 = 持仓成本 × 百分比。"""
    e, p = pos(make_env, cfg())
    d = p.evaluate("""() => {
        var h = window.__xt.holdStats();
        return {avg: h.avg, grams: h.grams};
    }""")
    cost = d["avg"] * d["grams"]
    for k in (25, 30):
        n = p.eval_on_selector("#calcBox .pci[data-p='%d'] b" % k, "e => e.innerText")
        want = round(cost * k / 100)
        plain = n.replace(",", "").replace("¥", "").strip()
        assert plain.isdigit(), (k, n)
        assert abs(int(plain) - want) <= 1, (k, plain, want)


def test_g10_hidden_when_no_holding(make_env):
    """没有买入记录时这张卡不出现，别留一个空框。"""
    p = make_env(cfg=dict(cfg(), buys=[]), width=430, height=1600).page
    p.wait_for_function("() => window.__xt.ready", timeout=8000)
    p.evaluate("() => window.__xt.gotoTab('pos')")
    p.wait_for_timeout(300)
    display = p.eval_on_selector("#calcBox", "e => getComputedStyle(e).display")
    assert display == "none", display
