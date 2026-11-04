# -*- coding: utf-8 -*-
"""成本下方减仓开关：关掉后，历史遗留的低位计划也不能再提示减仓。

真实现场：持仓均价 972.01 元/克，现价约 856 元/克（浮亏），
设置里「允许在成本下方减仓」未勾选，但止盈页仍出现
「855.90 元/克 · 到价 · 减 28.3g」这类可点击的减仓按钮 —— 一点就是亏损卖出。
根因：S.cfg.takePlan 里存在开关打开时留下的低位条目，而生成「到价」行时
只判 p.price <= nowCny，没判该位是否在成本线之下。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import CFG_NORMAL  # noqa: E402

COST = 972.01


def _env_with_position(make_env, stop_mode):
    # 一笔建仓，均价与现场一致；克重取截图里的 328.88g
    cfg = dict(CFG_NORMAL)
    cfg["stopMode"] = stop_mode
    cfg["buys"] = [{"id": "b1", "name": "建仓", "gram": 328.88,
                    "priceCny": COST, "time": "2026-08-12T02:00:00.000Z",
                    "note": "", "gear": None, "acct": "a0"}]
    cfg["keepGram"] = 0
    return make_env(cfg=cfg)


def _inject_take_plan(p):
    p.evaluate("""() => {
        var s = window.__xt.state();
        s.cfg.takePlan = {
          "低位·斐波78.6%": {cut: 28.3, price: 855.90, at: Date.now()},
          "低位·整数4000":  {cut: 26.0, price: 856.61, at: Date.now()}
        };
        window.__xt.compute();      // S.takes 只在 compute() 里重算
        window.__xt.refreshAll();   // 再走一遍渲染，断言同时覆盖数据与 DOM
    }""")


def test_t1_stop_off_hides_below_cost_hit(make_env):
    """开关关闭：成本下方的历史计划位不得出现在「到价」提示里。"""
    e = _env_with_position(make_env, stop_mode=False)
    p = e.page
    p.wait_for_function("() => window.__xt.state().takes", timeout=8000)
    _inject_take_plan(p)
    p.wait_for_timeout(400)

    rows = p.evaluate("""() => (window.__xt.state().takes || [])
        .filter(t => t.role === "hit")
        .map(t => ({p: t.cnyVal, cut: t.cut, below: t.cnyVal < %f}))""" % COST)
    assert rows == [], "开关关闭时仍提示成本下方减仓：%s" % rows

    # 数据层干净还不够：页面上不能再出现可点的「到价 · 减 Xg」
    hits = p.eval_on_selector_all("#takeBox .hitnow", "els => els.length")
    assert hits == 0, "页面上仍有 %d 条到价减仓提示" % hits


def test_t2_stop_off_has_no_below_cost_cut(make_env):
    """开关关闭：本轮计划里也不得有低于成本价的执行位。"""
    e = _env_with_position(make_env, stop_mode=False)
    p = e.page
    p.wait_for_function("() => window.__xt.state().takes", timeout=8000)
    _inject_take_plan(p)
    p.wait_for_timeout(400)

    bad = p.evaluate("""() => (window.__xt.state().takes || [])
        .filter(t => t.role === "cut" && t.cnyVal < %f)
        .map(t => ({p: t.cnyVal, cut: t.cut}))""" % COST)
    assert bad == [], "开关关闭时计划里仍分配了成本下方克重：%s" % bad


def test_t3_stop_on_keeps_below_cost(make_env):
    """开关打开：低位计划位应照常提示，证明上面的过滤只针对关闭态。"""
    e = _env_with_position(make_env, stop_mode=True)
    p = e.page
    p.wait_for_function("() => window.__xt.state().takes", timeout=8000)
    _inject_take_plan(p)
    p.wait_for_timeout(400)

    rows = p.evaluate("""() => (window.__xt.state().takes || [])
        .filter(t => t.role === "hit").map(t => t.cnyVal)""")
    assert len(rows) == 2, "开关打开时应保留两条低位到价提示，实际 %s" % rows
    # 反向验证：同一个选择器在打开态必须能数到 2 条，否则 t1 的 0 可能是选择器失效
    hits = p.eval_on_selector_all("#takeBox .hitnow", "els => els.length")
    assert hits == 2, "打开态页面上应有 2 条到价提示，实际 %d" % hits
