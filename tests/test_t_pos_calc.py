# -*- coding: utf-8 -*-
"""持仓页三件事：账户区去重、盈亏试算卡、底仓输入框前导零。

1. 账户卡片顶部原来另起一行「全账户克重 / 持仓成本 / 浮动盈亏」，
   与上方持仓汇总的「持仓克重 / 持仓成本 / 浮动盈亏大字」是同一组数，一个屏里读两遍。
2. 持仓汇总下方补一张盈亏试算：距回本单价还差多少钱、成本 +5/10/15/20% 各赚多少。
3. 底仓从 0 直接改成 90，数字框会变成 "090"，再输一位就是 "0900"。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conftest import CFG_NORMAL

COST = 972.01
GRAM = 328.8848


def cfg(gram=GRAM, cost=COST, keep=0):
    c = dict(CFG_NORMAL)
    c["stopMode"] = False
    c["keepGram"] = keep
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


def ctx(p):
    return p.evaluate("""() => {
        var S = window.__xt.state();
        var h = window.__xt.holdStats();
        return {nowCny: S.price * S.fx / 31.1035, avg: h.avg, grams: h.grams,
                comeback: Number(S.cfg.comeback)};
    }""")


def test_e1_account_card_has_no_duplicate_summary(make_env):
    """账户区不再重复持仓汇总的三个数；全账户口径只出现在持仓汇总卡。"""
    e, p = pos(make_env, cfg())
    assert p.eval_on_selector_all("#acctBox .acsum", "els => els.length") == 0
    # 持仓汇总卡上仍然有这三个数
    it = p.eval_on_selector_all("#posBox .posmini .it", "els => els.map(e => e.innerText)")
    labels = [x.split("\n")[0] for x in it]
    assert "持仓克重" in labels and "持仓成本" in labels, labels
    # 账户行本身还在
    assert p.eval_on_selector_all("#acctBox .acrow", "els => els.length") >= 1


def test_e2_account_footnote_still_claims_nothing_about_header(make_env):
    """脚注原来解释"卡片顶部三个数"，卡片头删掉后这句要跟着改。"""
    e, p = pos(make_env, cfg())
    foot = p.inner_text("#acctBox .acfoot")
    assert "顶部" not in foot, foot


def test_e3_pnl_calc_card_exists_below_summary(make_env):
    """持仓汇总下方有盈亏试算卡，且在账户区之前。"""
    e, p = pos(make_env, cfg())
    assert p.eval_on_selector_all("#posBox .pnlcalc", "els => els.length") == 1
    order = p.evaluate("""() => {
        var a = document.querySelector('#posBox .pnlcalc');
        var b = document.querySelector('#acctBox');
        return a.getBoundingClientRect().top <= b.getBoundingClientRect().top;
    }""")
    assert order


def test_e4_gap_to_breakeven_is_per_gram(make_env):
    """距回本单价还差多少：回本价 − 现价，单位元/克。"""
    e, p = pos(make_env, cfg())
    d = ctx(p)
    back = d["avg"] + d["comeback"]
    want = back - d["nowCny"]
    assert want > 0, d
    txt = p.inner_text("#posBox .pnlcalc")
    assert ("%.2f" % want) in txt, (want, txt)
    assert "%.2f" % back in txt            # 回本价也露出来，不然不知道涨到哪


def test_e5_profit_targets_are_cost_based(make_env):
    """5/10/15/20% 盈利金额 = 持仓成本 × 百分比；价位 = 均价 × (1+百分比)。"""
    e, p = pos(make_env, cfg())
    d = ctx(p)
    cost = d["grams"] * d["avg"]
    for pct in (5, 10, 15, 20):
        n = p.eval_on_selector_all(
            "#posBox .pnlcalc .pci[data-p='%d'] b" % pct,
            "els => els.length ? els[0].innerText : ''")
        want = round(cost * pct / 100)
        plain = n.replace(",", "").replace("¥", "").strip()
        assert plain.isdigit(), (pct, n)
        assert abs(int(plain) - want) <= 1, (pct, plain, want)
        px = p.eval_on_selector_all(
            "#posBox .pnlcalc .pci[data-p='%d'] em" % pct, "els => els[0].innerText")
        assert ("%.2f" % (d["avg"] * (1 + pct / 100))) in px, (pct, px)


def test_e6_profit_amounts_are_red(make_env):
    """盈利金额用红色（A 股习惯），不用绿。"""
    e, p = pos(make_env, cfg())
    cls = p.eval_on_selector_all("#posBox .pnlcalc .pci b", "els => els.map(e => e.className)")
    assert cls and all("red" in c for c in cls), cls


def test_e7_keep_input_strips_leading_zero(make_env):
    """底仓 0 → 90：value 不能停在 "090"。"""
    e = make_env(cfg=cfg(), width=430, height=1600)
    p = e.page
    p.wait_for_function("() => window.__xt.ready", timeout=8000)
    p.evaluate("() => window.__xt.gotoTab('set')")
    p.wait_for_timeout(300)
    p.fill("#setKeep", "0")
    p.type("#setKeep", "90")
    v = p.eval_on_selector("#setKeep", "e => e.value")
    assert v == "90", v


def test_e8_keep_input_keeps_decimal(make_env):
    """规范前导零不能把 0.5 吃掉。"""
    e = make_env(cfg=cfg(), width=430, height=1600)
    p = e.page
    p.wait_for_function("() => window.__xt.ready", timeout=8000)
    p.evaluate("() => window.__xt.gotoTab('set')")
    p.wait_for_timeout(300)
    p.fill("#setKeep", "")
    p.type("#setKeep", "0.5")
    assert p.eval_on_selector("#setKeep", "e => e.value") == "0.5"


# === v6.64：已回本后的「距回本」栏 ===
# 同一栏，未回本说「还差 X 元/克」，已回本就该说「已盈利 X 元/克」，
# 基数是成本价（真正的盈利），不是回本价（回本价已经含了回本加点那一段）。

def test_f1_back_above_shows_profit_per_gram(make_env):
    """现价高于回本价：不再说「还差」，改为「已盈利 X 元/克」。"""
    e, p = pos(make_env, cfg(cost=800.0))
    d = ctx(p)
    back = d["avg"] + d["comeback"]
    assert d["nowCny"] > back, d
    want = d["nowCny"] - d["avg"]
    box = p.inner_text("#posBox .pnlcalc .pcgap")
    assert "还差" not in box, box
    assert "已盈利" in box, box
    assert ("%.2f" % want) in box, (want, box)


def test_f2_back_above_keeps_breakeven_visible(make_env):
    """换成「已盈利」之后，回本价这个参照点还得留着，否则不知道从哪算起的。"""
    e, p = pos(make_env, cfg(cost=800.0))
    d = ctx(p)
    box = p.inner_text("#posBox .pnlcalc .pcgap")
    assert ("%.2f" % (d["avg"] + d["comeback"])) in box, box
    assert ("%.2f" % d["nowCny"]) in box, box


def test_f3_back_above_position_amount_is_profit(make_env):
    """右侧「全仓」跟着变成已赚金额 = (现价 − 均价) × 克重。"""
    import re
    e, p = pos(make_env, cfg(cost=800.0))
    d = ctx(p)
    want = (d["nowCny"] - d["avg"]) * d["grams"]
    txt = p.inner_text("#posBox .pnlcalc .pcgap .pg")
    assert "已赚" in txt, txt
    nums = [int(x.replace(",", "")) for x in re.findall(r"[\d,]+", txt) if x.strip(",")]
    assert any(abs(n - want) <= 1 for n in nums), (want, txt)


def test_f4_back_above_no_need_rise_hint(make_env):
    """已回本还写「需涨 x%」，是在教人涨多少才回本 —— 已经回本了。"""
    e, p = pos(make_env, cfg(cost=800.0))
    box = p.inner_text("#posBox .pnlcalc .pcgap")
    assert "需涨" not in box, box
    assert "较成本" in box, box     # 换成"比成本高多少"，这才是已回本时该给的参照


def test_f5_exactly_at_back_counts_as_back(make_env):
    """现价正好等于回本价：算已回本，盈利恰好是回本加点那一段。"""
    e, p = pos(make_env, cfg(cost=800.0))
    p.evaluate("""() => {
        var S = window.__xt.state();
        var now = S.price * S.fx / 31.1035;
        var cb = Number(S.cfg.comeback);
        if(!isFinite(cb)) cb = 10;
        S.cfg.buys[0].priceCny = now - cb;
        window.__xt.compute(); window.__xt.refreshAll();
    }""")
    p.wait_for_timeout(250)
    d = ctx(p)
    box = p.inner_text("#posBox .pnlcalc .pcgap")
    assert "已盈利" in box, box
    assert ("%.2f" % d["comeback"]) in box, (d["comeback"], box)


def test_f6_below_back_still_says_gap(make_env):
    """未回本的老行为不能被误伤。"""
    e, p = pos(make_env, cfg())
    d = ctx(p)
    assert d["nowCny"] < d["avg"] + d["comeback"], d
    box = p.inner_text("#posBox .pnlcalc .pcgap")
    assert "还差" in box, box
    assert "已盈利" not in box, box
