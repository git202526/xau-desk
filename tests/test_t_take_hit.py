# -*- coding: utf-8 -*-
"""止盈阶梯：已到价待核销的那几笔，必须算进「按计划执行后」的总账。

用户现场（v6.62）：底仓留 0、持仓 328.8848g、成本 972.01，
止盈阶梯却只写「计划减 274.5g（占持仓 83%）」，试算卡写「卖出后剩余 54.385g」。

54.385 正是两笔到价待核销（28.3 + 26）的和。它们不是"不卖"，是"价已到、
还没在账本里核销"，本轮的新计划必须给它们让出额度 —— 否则会减出持仓以外的量。
所以：

* 覆盖率要按「新计划 + 到价待核销」算，两者之和才是真正要卖的量；
* 试算卡是"按计划执行后"的钱账，漏掉这两笔，就会凭空多出几十克卖不掉的剩余；
* 均价同理，只有把两笔低位算进去才是真的清仓均价。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conftest import CFG_NORMAL

COST = 972.01
GRAM = 328.8848
HITS = {"旧计划·低位 A": {"cut": 28.3, "price": 855.90},
        "旧计划·低位 B": {"cut": 26.0, "price": 856.61}}


def cfg(hits=True, keep=0):
    c = dict(CFG_NORMAL)
    c["stopMode"] = True
    c["keepGram"] = keep
    c["takePlan"] = {}
    if hits:
        for nm, v in HITS.items():
            c["takePlan"][nm] = {"cut": v["cut"], "price": v["price"], "at": 9999999999999}
    c["buys"] = [{"id": "b1", "name": "建仓", "gram": GRAM, "priceCny": COST,
                  "time": "2026-08-12T02:00:00.000Z", "note": "", "gear": None, "acct": "a0"}]
    return c


def takes(p):
    return p.evaluate("""() => {
        var t = window.__xt.state().takes;
        return {
          planGrams: t.planGrams, hitGrams: t.hitGrams, allGrams: t.allGrams,
          holdGrams: t.holdGrams, avgSell: t.avgSell, calc: t.calc,
          summary: t.summary, cutCount: t.cutCount,
          hits: t.filter(function(m){ return m.role === "hit"; })
                  .map(function(m){ return {n:m.name, p:m.cnyVal, c:m.cut}; }),
          picked: t.picked.map(function(m){ return {n:m.name, p:m.cnyVal, c:m.cut}; })
        };
    }""")


def pos(make_env, c, width=430):
    e = make_env(cfg=c, width=width, height=1600)
    p = e.page
    p.wait_for_function("() => window.__xt.state().takes", timeout=8000)
    p.evaluate("() => window.__xt.gotoTab('pos')")
    p.wait_for_timeout(300)
    return e, p


def test_h1_new_plan_plus_hits_covers_holding(make_env):
    """底仓 0：新计划 274.5 + 到价 54.3 才等于持仓；单看新计划的百分比是误导。"""
    e, p = pos(make_env, cfg())
    t = takes(p)
    assert len(t["hits"]) == 2, t["hits"]
    assert abs(t["hitGrams"] - 54.3) < 0.05, t["hitGrams"]
    assert abs(t["allGrams"] - GRAM) < 0.15, (t["planGrams"], t["hitGrams"], t["allGrams"])
    assert t["allGrams"] / t["holdGrams"] > 0.999


def test_h2_calc_counts_pending_hits(make_env):
    """试算必须把两笔到价算进回笼与已实现，否则"卖出后剩余"根本不存在。"""
    e, p = pos(make_env, cfg())
    t = takes(p)
    c = t["calc"]
    want = sum(m["p"] * m["c"] for m in t["picked"]) + sum(m["p"] * m["c"] for m in t["hits"])
    assert abs(c["proceeds"] - want) < 1e-6, (c["proceeds"], want)
    want_g = sum(m["c"] for m in t["picked"]) + sum(m["c"] for m in t["hits"])
    assert abs(c["costBase"] - COST * want_g) < 1e-6
    assert abs(c["leftGrams"] - max(0.0, GRAM - want_g)) < 1e-3, c
    assert c["leftGrams"] < 0.1, c        # 底仓 0 = 卖光，只剩不足一手的零头


def test_h3_avg_sell_includes_hits(make_env):
    """两笔低位拉低均价：清仓均价不能用只含高位的那批来充数。"""
    e, p = pos(make_env, cfg())
    t = takes(p)
    a = sum(m["p"] * m["c"] for m in t["picked"])
    b = sum(m["p"] * m["c"] for m in t["hits"])
    ga = sum(m["c"] for m in t["picked"])
    gb = sum(m["c"] for m in t["hits"])
    assert abs(t["avgSell"] - (a + b) / (ga + gb)) < 1e-6, t["avgSell"]


def test_h4_summary_and_meta_explain_pending(make_env):
    """摘要与副标题要说清「另外还有几笔到价待核销」，否则用户以为只能减 83%。"""
    e, p = pos(make_env, cfg())
    t = takes(p)
    assert "到价" in t["summary"], t["summary"]
    meta = p.inner_text("#takeMeta")
    assert "到价" in meta and "54.3" in meta.replace("54.30", "54.3"), meta


def test_h5_calc_card_no_ghost_remainder(make_env):
    """修前剩 54.385g —— 那是"卖不掉"的幽灵余额；修后只剩不足一手的零头。"""
    e, p = pos(make_env, cfg())
    assert takes(p)["calc"]["leftGrams"] < 0.1
    txt = p.inner_text("#takeBox .tcalc")
    assert "54.385" not in txt, txt
    assert ("不足 1 手" in txt) or ("已全部卖出" in txt), txt


def test_h6_keep_floor_still_kept(make_env):
    """留底仓 100：全部执行后剩余必须是 100，不能被到价那两笔挤掉。"""
    e, p = pos(make_env, cfg(keep=100))
    t = takes(p)
    assert abs(t["calc"]["leftGrams"] - 100) < 0.15, t["calc"]
