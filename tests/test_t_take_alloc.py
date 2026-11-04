# -*- coding: utf-8 -*-
"""止盈阶梯：止盈/止损判断引擎与候选位分配引擎的规则校验。

这一组用例锁的是「计划量」这条线 —— 计划减多少克，必须真的能减出来：
1) 计划总量不得超过可减持仓（原来是 Math.round 上浮 + 每单 Math.max(0.1) 叠加，
   0.15 克持仓能计划出 0.7 克，摘要自己都写「占持仓 467%」）；
2) 历史计划到价后要跟当前持仓对账（持仓 5 克、旧计划 28.3 克，提示减 28.3 克，
   点开预填 28.3，保存被拒）；
3) 12% 首单保底不能落在成本下方的止损位上（那是把最大份额压在最亏的价位）。
另带两条：候选位聚类均价必须是真算术平均；到价文案不能出现「已越过 -3.2%」。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import CFG_NORMAL  # noqa: E402

COST = 972.01


def _env(make_env, gram, keep=0, stop=True, plan=None):
    cfg = dict(CFG_NORMAL)
    cfg["stopMode"] = stop
    cfg["keepGram"] = keep
    cfg["takePlan"] = plan or {}
    cfg["buys"] = [{"id": "b1", "name": "建仓", "gram": gram, "priceCny": COST,
                    "time": "2026-08-12T02:00:00.000Z", "note": "",
                    "gear": None, "acct": "a0"}]
    return make_env(cfg=cfg)


def _ready(e):
    e.page.wait_for_function("() => window.__xt.state().takes", timeout=8000)
    return e.page


def _inject(p, plan):
    p.evaluate("""(plan) => {
        var s = window.__xt.state();
        var o = {};
        Object.keys(plan).forEach(function(k){
          o[k] = {cut: plan[k][0], price: plan[k][1], at: Date.now()};
        });
        s.cfg.takePlan = o;
        window.__xt.compute();
        window.__xt.refreshAll();
    }""", plan)
    p.wait_for_timeout(250)


# ---------------------------------------------------------------- 总量守恒

def test_a1_alloc_never_exceeds_holding(make_env):
    """任意持仓下：执行克重之和 == 计划克重，且不超过可减持仓。"""
    p = _ready(_env(make_env, 10))
    rows = p.evaluate("""() => {
        var s = window.__xt.state(), out = [], vals = [];
        for(var i=1;i<=60;i++) vals.push(Math.round(i*5)/100);      // 0.05 ~ 3.00
        [5.15, 10.15, 28.3, 100.05, 328.8848, 1000.7].forEach(function(v){ vals.push(v); });
        vals.forEach(function(g){
          s.cfg.keepGram = 0;
          s.cfg.takePlan = {};
          s.cfg.buys = [{id:"b1", name:"x", gram:g, priceCny:972.01,
                         time:"2026-08-12T02:00:00.000Z", note:"", gear:null, acct:"a0"}];
          s.cfg.stopMode = true;
          window.__xt.compute();
          var t = s.takes || [], sum = 0, mn = 99, n = 0;
          t.forEach(function(x){
            if(x.role !== "cut") return;
            sum += x.cut; n++; if(x.cut < mn) mn = x.cut;
          });
          out.push({g:g, plan:s.takes.planGrams, sum:Math.round(sum*1000)/1000,
                    min:n?mn:null, n:n, cmt:s.takes.summary});
        });
        return out;
    }""")

    over = [r for r in rows if r["sum"] > r["g"] + 1e-9]
    assert over == [], "计划克重超过持仓：%s" % [(r["g"], r["sum"]) for r in over][:6]

    drift = [r for r in rows if abs(r["sum"] - r["plan"]) > 1e-9]
    assert drift == [], "分配总量与 planGrams 不一致：%s" % \
        [(r["g"], r["plan"], r["sum"]) for r in drift][:6]

    tiny = [r for r in rows if r["n"] and r["min"] < 0.1 - 1e-9]
    assert tiny == [], "出现小于 0.1g 的无法执行的减仓单：%s" % \
        [(r["g"], r["min"]) for r in tiny][:6]


def test_a2_plan_rounds_down_to_lot(make_env):
    """计划量向下取整到 0.1g：1.05 不得变成 1.1（超出持仓）。"""
    p = _ready(_env(make_env, 1.05))
    got = p.evaluate("() => window.__xt.state().takes.planGrams")
    assert abs(got - 1.0) < 1e-9, "持仓 1.05g 应计划 1.0g，实际 %s" % got

    p.evaluate("""() => {
        var s = window.__xt.state();
        s.cfg.keepGram = 0; s.cfg.takePlan = {};
        s.cfg.buys = [{id:"b1", name:"x", gram:328.8848, priceCny:972.01,
                       time:"2026-08-12T02:00:00.000Z", note:"", gear:null, acct:"a0"}];
        window.__xt.compute();
    }""")
    got = p.evaluate("() => window.__xt.state().takes.planGrams")
    assert abs(got - 328.8) < 1e-9, "持仓 328.8848g 应计划 328.8g，实际 %s" % got


def test_a3_alloc_units_is_exact(make_env):
    """份额分配是整数运算：和恰好等于总额，每份至少 1 个 0.1g 单位。"""
    p = _ready(_env(make_env, 10))
    rows = p.evaluate("""() => {
        var cases = [[2,[1,1],-1], [5,[3,2,2,1],-1], [100,[7,11,13,17,19,23],-1],
                     [3288,[10,20,30,40,50,60,70],-1], [10,[9,1],1], [3,[1,1,1,1,1],-1],
                     [1,[5,5,5],-1],
                     /* 均权 + 非整除：按权重四舍五入会凑不出总额，
                        最后一位会被挤成 0 —— 必须由「给后面留份额」的约束兜住 */
                     [10,[1,1,1,1,1,1],-1], [9,[1,1,1,1,1,1],-1], [13,[1,1,1,1,1,1,1],-1]];
        return cases.map(function(c){
          var u = window.__xt.allocUnits(c[0], c[1], c[2]);
          var s = 0; u.forEach(function(x){ s += x; });
          return {total:c[0], want:c[1].length, got:u, sum:s,
                  min: u.length ? Math.min.apply(null, u) : 0};
        });
    }""")
    for r in rows:
        assert r["sum"] == r["total"], "份额和 %s != 总额 %s（%s）" % (r["sum"], r["total"], r["got"])
        assert r["min"] >= 1, "出现 0 份额：%s" % (r["got"],)
        assert len(r["got"]) <= r["want"], "份额数超过位数：%s" % (r["got"],)


def test_a4_bonus_goes_to_first_profit_not_stop(make_env):
    """12% 首单保底只给第一个成本上方位，止损位不吃保底。"""
    p = _ready(_env(make_env, 10))
    stop_first = p.evaluate("() => window.__xt.allocUnits(100, [1,100,100,100], 1)")
    assert stop_first[0] == 1, "止损位不该吃到 12%% 保底，实际 %s" % stop_first[0]
    assert stop_first[1] >= 12, "首个盈利位应有 12%% 保底，实际 %s" % stop_first[1]

    profit_first = p.evaluate("() => window.__xt.allocUnits(100, [100,1,1,1], 0)")
    assert profit_first[0] >= 12, "首位即盈利位时应享受保底，实际 %s" % profit_first[0]


# ------------------------------------------------------------ 到价与持仓对账

def test_b1_hit_cut_capped_by_holding(make_env):
    """持仓缩到 5g 后，28.3g 的旧计划只能提示减 5g，且页面文案同步。"""
    p = _ready(_env(make_env, 5.0))
    _inject(p, {"低位·斐波78.6%": (28.3, 855.90)})

    rows = p.evaluate("""() => (window.__xt.state().takes || [])
        .filter(t => t.role === "hit").map(t => ({cut:t.cut, action:t.action}))""")
    assert len(rows) == 1, "应保留 1 条到价提示，实际 %s" % rows
    assert abs(rows[0]["cut"] - 5.0) < 1e-9, "到价克重应被持仓封顶为 5g，实际 %s" % rows[0]["cut"]

    txt = p.inner_text("#takeBox")
    assert "28.3" not in txt, "页面仍在提示 28.3g：%s" % txt


def test_b2_hit_rows_share_remaining_holding(make_env):
    """多条到价计划分摊剩余持仓，合计不得超过持仓。"""
    p = _ready(_env(make_env, 30.0))
    _inject(p, {"低位A": (26.0, 856.61), "低位B": (28.3, 855.90)})

    rows = p.evaluate("""() => (window.__xt.state().takes || [])
        .filter(t => t.role === "hit").map(t => t.cut)""")
    assert len(rows) == 2, "两条都该出现（合计仍在持仓内），实际 %s" % rows
    assert abs(sum(rows) - 30.0) < 1e-9, "分摊后应恰好吃掉 30g 持仓，实际 %s" % rows


def test_b3_hit_cut_never_exceeds_avail(make_env):
    """持仓 8g、单条旧计划 28.3g：封顶 8g，不再出现超卖提示。"""
    p = _ready(_env(make_env, 8.0))
    _inject(p, {"高位旧计划": (28.3, 900.0)})
    rows = p.evaluate("""() => (window.__xt.state().takes || [])
        .filter(t => t.role === "hit").map(t => t.cut)""")
    assert rows and abs(sum(rows) - 8.0) < 1e-9, "应封顶到 8g，实际 %s" % rows


# ---------------------------------------------------------------- 聚类均值

def test_c1_cluster_price_is_arithmetic_mean(make_env):
    """合并簇的价位必须是簇内各原始位的算术平均。"""
    p = _ready(_env(make_env, 10))
    bad = p.evaluate("""() => {
        var lv = window.__xt.buildLevels(45, {extended:false}), out = [];
        lv.forEach(function(m){
          var raw = m.raw || [];
          if(raw.length < 2) return;
          var s = 0; raw.forEach(function(x){ s += x; });
          if(Math.abs(s/raw.length - m.price) > 1e-6)
            out.push({label:m.label, price:m.price, avg:s/raw.length, raw:raw});
        });
        return out;
    }""")
    assert bad == [], "聚类均价不是算术平均：%s" % bad


# ------------------------------------------------------------------ 文案

def test_c2_passed_level_has_no_double_negative(make_env):
    """已越过的位写「已越过 3.2%」，不能是「已越过 -3.2%」。"""
    p = _ready(_env(make_env, 10))
    _inject(p, {"已穿过位": (5.0, 900.0)})
    txt = p.inner_text("#takeBox")
    assert "已越过 -" not in txt, "到价文案重复负号：%s" % txt
    assert "已越过 3.2%" in txt, "缺少应有的越位幅度：%s" % txt
