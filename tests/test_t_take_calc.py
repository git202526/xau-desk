# -*- coding: utf-8 -*-
"""止盈阶梯摘要：清仓均价卡 + 试算盈亏卡。

两条口径（改渲染前先读）：
1. 清仓均价 = 全部执行位（止盈 + 止损）的加权卖出价，等于 Σ(价×克) / Σ克。
   只在「止盈与止损同时在」时单独成卡 —— 纯止盈 / 纯止损时它就是上面那个均价。
2. 试算只算执行位：到价待核销还没成交，算进「已实现」就是无中生有。
   盈亏 = 回笼 − 成本×克数；红盈绿亏（A 股习惯）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conftest import CFG_NORMAL

COST = 972.01
GRAM = 328.8848


def cfg(cost=COST, gram=GRAM, stop=True):
    c = dict(CFG_NORMAL)
    c["stopMode"] = stop
    c["keepGram"] = 0
    c["takePlan"] = {}
    c["buys"] = [{"id": "b1", "name": "建仓", "gram": gram, "priceCny": cost,
                  "time": "2026-08-12T02:00:00.000Z", "note": "", "gear": None, "acct": "a0"}]
    return c


def takes(p):
    return p.evaluate("""() => {
        var t = window.__xt.state().takes;
        return {
          calc: t.calc, avgSell: t.avgSell, cutCount: t.cutCount,
          execGrams: t.execGrams, planGrams: t.planGrams, holdGrams: t.holdGrams,
          profit: t.profit, stop: t.stop,
          picked: t.picked.map(function(m){ return {n:m.name, p:m.cnyVal, c:m.cut}; })
        };
    }""")


def cards(p):
    return p.eval_on_selector_all("#takeBox .tplan .bi", "els => els.map(e => e.innerText)")


def pos(make_env, c, width=430):
    e = make_env(cfg=c, width=width, height=1600)
    p = e.page
    p.wait_for_function("() => window.__xt.state().takes", timeout=8000)
    p.evaluate("() => window.__xt.gotoTab('pos')")
    p.wait_for_timeout(300)
    return e, p


def test_d1_mixed_shows_clear_avg_card(make_env):
    """止盈 + 止损同时在：多一张「清仓均价」，且等于 Σ(价×克)/Σ克。"""
    e, p = pos(make_env, cfg(stop=True))
    t = takes(p)
    assert t["profit"]["grams"] > 0.05 and t["stop"]["grams"] > 0.05, t
    cs = cards(p)
    assert len(cs) == 4, cs
    assert "清仓均价" in cs[3], cs
    want = sum(m["p"] * m["c"] for m in t["picked"]) / sum(m["c"] for m in t["picked"])
    assert abs(t["avgSell"] - want) < 1e-6
    assert abs(float(cs[3].split("\n")[-1].replace(",", "")) - round(want)) <= 1


def test_d2_pure_profit_has_no_clear_card(make_env):
    """只做止盈：三张卡，不重复摆一个和「止盈均价」一样的数。"""
    e, p = pos(make_env, cfg(stop=False))
    t = takes(p)
    assert t["stop"]["grams"] < 0.05, t
    cs = cards(p)
    assert len(cs) == 3, cs
    assert not any("清仓均价" in x for x in cs), cs


def test_d3_calc_matches_plan(make_env):
    """试算 = 回笼 − 成本；回笼 = Σ(价×克)；百分比按这批卖出的成本算。"""
    e, p = pos(make_env, cfg(stop=True))
    t = takes(p)
    c = t["calc"]
    picked = t["picked"]
    proceeds = sum(m["p"] * m["c"] for m in picked)
    grams = sum(m["c"] for m in picked)
    assert abs(c["proceeds"] - proceeds) < 1e-6
    assert abs(c["costBase"] - COST * grams) < 1e-6
    assert abs(c["pnl"] - (proceeds - COST * grams)) < 1e-6
    assert abs(c["pnlPct"] - (proceeds / (COST * grams) - 1) * 100) < 1e-9
    assert abs(c["leftGrams"] - (GRAM - grams)) < 1e-3
    # 只有执行位参与，到价待核销不能算进来
    assert abs(grams - t["execGrams"]) < 1e-9


def test_d4_calc_card_renders_red_when_profit(make_env):
    e, p = pos(make_env, cfg(stop=True))
    c = takes(p)["calc"]
    assert c["pnl"] > 0
    txt = p.inner_text("#takeBox .tcalc")
    assert "按计划执行后" in txt and "已实现盈亏" in txt
    assert "卖出回笼" in txt
    b = p.eval_on_selector("#takeBox .tcalc .tcr:nth-child(3) b", "e => e.className")
    assert "red" in b, b
    # 金额与百分比都要在
    assert "+¥" in p.inner_text("#takeBox .tcalc .tcr:nth-child(3)")


def test_d5_calc_card_green_when_loss(make_env):
    """成本抬到 2000：全部执行位都在成本下方，净额是亏的，颜色必须变绿。"""
    e, p = pos(make_env, cfg(cost=2000.0, stop=True))
    c = takes(p)["calc"]
    assert c["pnl"] < 0, c
    cls = p.eval_on_selector("#takeBox .tcalc .tcr:nth-child(3) b", "e => e.className")
    assert "green" in cls, cls
    txt = p.inner_text("#takeBox .tcalc .tcr:nth-child(3)")
    assert "\u2212¥" in txt, txt


def test_d6_no_plan_no_calc_card(make_env):
    """底仓盖住全部持仓：没有执行位，试算卡不出现，也不能崩。"""
    c = cfg(stop=False)
    c["keepGram"] = GRAM           # 底仓 = 全部持仓
    e, p = pos(make_env, c)
    t = takes(p)
    assert t["cutCount"] == 0, t
    assert t["calc"]["proceeds"] == 0
    assert p.eval_on_selector_all("#takeBox .tcalc", "els => els.length") == 0
    assert p.eval_on_selector_all("#takeBox .tplan .bi", "els => els.length") == 2


def test_d8_partial_plan_not_called_liquidation(make_env):
    """留了底仓还写「清仓均价」是把话说满：改为「计划均价」，数值不变。"""
    c = cfg(stop=True)
    c["keepGram"] = 100
    e, p = pos(make_env, c)
    t = takes(p)
    assert t["profit"]["grams"] > 0.05 and t["stop"]["grams"] > 0.05, t
    assert t["planGrams"] < t["holdGrams"] - 0.1
    cs = cards(p)
    assert len(cs) == 4, cs
    assert "计划均价" in cs[3] and "清仓" not in cs[3], cs


def test_d7_narrow_360_four_cards_no_overflow(make_env):
    """360 宽四张卡不能溢出容器（横向滚动条 = 布局坏了）。"""
    e, p = pos(make_env, cfg(stop=True), width=360)
    assert len(cards(p)) == 4
    over = p.evaluate("""() => {
        var b = document.querySelector('#takeBox .tplan');
        var r = b.getBoundingClientRect();
        var bad = [];
        Array.prototype.forEach.call(b.children, function(k){
          var q = k.getBoundingClientRect();
          if(q.right > r.right + 0.5 || q.left < r.left - 0.5) bad.push(k.innerText);
        });
        return {bad:bad, scroll:b.scrollWidth > b.clientWidth + 1};
    }""")
    assert over["bad"] == [] and not over["scroll"], over
