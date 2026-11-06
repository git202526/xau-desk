"""本轮本地改动 · 建仓各档计划按「剩余可用仓位」分配，不再写死目标总克重。

用户场景：设置了持仓上限（目标总克重）、登记过已有持仓但**未标记买在哪个档位**，
仓位已经不足，建仓计划却仍按 TOTAL 全额分配各档克重，Σ待补远大于真实剩余。

根因：planBase() 用 B = totalGrams()，而「已买多少」只从 gearMarks 读（boughtOf）；
未标档的持仓无处可扣，既不进任何档、也不从预算里扣。

口径（planBudget）：
  B = noCap ? TOTAL : max(0, TOTAL − 未标记档位的已持仓)
  未标记部分 = Σ buys 中 gear 为空（None/""/缺省）的克重；已分配到档位的买入
  （buy.gear 有值，或手动标记 gearMarks）由各档 pending = plan − bought 自然扣，不双重扣减。
  totalGrams() 本身不变：仓位进度 / 超上限判定 / 满仓成本仍按原口径。
"""
import pytest

from conftest import CFG_EMPTY

H = 4800.0
W = [8, 12, 24, 36, 12, 8]
BASE = dict(CFG_EMPTY, **{
    "H_BASE": H, "TOTAL": 100, "alpha": 2.44, "spanMode": "alpha",
    "gearCount": 6, "wPct": W, "startDrop": 13, "endDrop": 25,
    "avgTarget": None,
})


def buy(g, gear=None):
    return [{"id": "b1", "name": "已有持仓", "gram": g, "priceCny": 905.0,
             "time": "2026-08-12T02:00:00.000Z", "note": "", "gear": gear}]


def cfg(**kw):
    return dict(BASE, **kw)


def snap(p):
    return p.evaluate("""() => {
      const x = window.__xt;
      const rows = x.gearRows().filter(r => r.idx > 0);
      const pb = x.planBase(), sm = x.planSummary(), m = x.gearMeta() || {};
      const sum = (f) => rows.reduce((a, r) => a + (Number(r[f]) || 0), 0);
      return {
        budget: x.planBudget(), held: x.holdStats().grams, total: x.state().TOTAL,
        noCap: !!x.state().cfg.noCap,
        pb: pb, sumPlan: pb.reduce((a, b) => a + b, 0),
        sumRowPlan: sum('plan'), sumPending: sum('pending'), sumBought: sum('boughtG'),
        smBudget: sm.budget, smPlanned: sm.planned, smPending: sm.pending,
        avgAll: m.avgAll, avgAnchor: m.avgAnchor
      };
    }""")


def switch_tab(p, name):
    p.evaluate("(n) => document.querySelector('.tabbar button[data-tab=\"'+n+'\"]').click()", name)
    p.wait_for_timeout(250)


# ---------- 1. 用户主场景：未标档持仓先扣预算 ----------

def test_b1_unmarked_holdings_cut_budget(make_env):
    """TOTAL=100 / 持仓 60g 全未标档 → 预算 40，Σ计划 = Σ待补 = 40（旧行为是 100）。"""
    d = snap(make_env(cfg(buys=buy(60))).page)
    assert d["held"] == 60 and d["total"] == 100
    assert d["budget"] == pytest.approx(40, abs=0.01), d
    assert d["sumPlan"] == pytest.approx(40, abs=0.01), d
    assert d["sumRowPlan"] == pytest.approx(40, abs=0.01), d
    assert d["sumPending"] == pytest.approx(40, abs=0.01), d
    assert d["sumBought"] == 0


def test_b2_near_full_position(make_env):
    """用户原话「仓位已经不足」：80g 已登记 → 只按剩余 20g 分配。"""
    d = snap(make_env(cfg(buys=buy(80))).page)
    assert d["budget"] == pytest.approx(20, abs=0.01), d
    assert d["sumPending"] == pytest.approx(20, abs=0.01), d
    assert d["pb"] == pytest.approx([1.6, 2.4, 4.8, 7.2, 2.4, 1.6], abs=0.01), d["pb"]


def test_b3_no_holdings_keeps_full_budget(make_env):
    """零回归：无持仓时预算 = TOTAL，与改动前逐值相同。"""
    d = snap(make_env(cfg()).page)
    assert d["held"] == 0
    assert d["budget"] == 100 and d["smBudget"] == 100
    assert d["pb"] == pytest.approx([8, 12, 24, 36, 12, 8], abs=0.01), d["pb"]
    assert d["sumPending"] == pytest.approx(100, abs=0.01)


def test_b4_weight_ratio_preserved(make_env):
    """预算缩放只等比缩小，各档相对权重不变。"""
    d = snap(make_env(cfg(buys=buy(60))).page)
    s = sum(W)
    for i, wi in enumerate(W):
        assert d["pb"][i] == pytest.approx(d["budget"] * wi / s, abs=0.01), (i, d["pb"])


# ---------- 2. 已标档 / 混合 / 超仓 / 不设上限 ----------

def test_b5_marked_holdings_not_double_deducted(make_env):
    """已分配到档位的持仓（buy.gear 有值）不扣预算：预算仍是 TOTAL；
    60g 全挂第1档，由各档 bought/pending 自然体现，预算不变（否则会双重扣减）。"""
    d = snap(make_env(cfg(buys=buy(60, "第1档"))).page)
    assert d["held"] == 60
    assert d["budget"] == 100, d                      # 已标档不扣预算
    assert d["sumBought"] == pytest.approx(60, abs=0.01), d


def test_b6_mixed_only_unmarked_part_is_deducted(make_env):
    """混合：60g 中 40g 未标档、20g 已挂第1档 → 只扣未标档部分，预算 100 − 40 = 60。"""
    d = snap(make_env(cfg(buys=[
        dict(id="b1", name="未标档", gram=40, priceCny=905.0,
             time="2026-08-12T02:00:00.000Z", note="", gear=None),
        dict(id="b2", name="已标档", gram=20, priceCny=905.0,
             time="2026-08-12T02:00:00.000Z", note="", gear="第1档")])).page)
    assert d["held"] == 60
    assert d["budget"] == pytest.approx(60, abs=0.01), d
    assert d["sumPlan"] == pytest.approx(60, abs=0.01), d


def test_b7_nocap_restores_full_budget(make_env):
    """开「不设仓位上限」= 不受 TOTAL 约束，预算回到 100，与 v7.4 语义一致。"""
    d = snap(make_env(cfg(buys=buy(60), noCap=True)).page)
    assert d["noCap"] is True
    assert d["budget"] == 100 and d["sumPlan"] == pytest.approx(100, abs=0.01), d
    assert d["pb"] == pytest.approx([8, 12, 24, 36, 12, 8], abs=0.01), d["pb"]


def test_b8_over_cap_clamps_to_zero(make_env):
    """持仓已超上限（120g）→ 预算夹到 0，各档计划 0，不出现负数。"""
    d = snap(make_env(cfg(buys=buy(120))).page)
    assert d["budget"] == 0, d
    assert all(x >= 0 for x in d["pb"]) and d["sumPlan"] == 0, d
    assert d["sumPending"] == 0, d


# ---------- 3. 不被牵动的部分 ----------

def test_b9_avg_prices_unchanged_when_holdings_added(make_env):
    """缩放的是克重比例，均价（全档 / 锚定）不变 —— 对齐目标链路不受影响。"""
    p = make_env(cfg()).page
    before = p.evaluate("() => ({a:window.__xt.gearMeta().avgAll, "
                        "b:window.__xt.gearMeta().avgAnchor})")
    p.evaluate("""() => {
      const x = window.__xt;
      x.state().cfg.buys.push({id:'b1', name:'已有持仓', gram:60,
        priceCny:905.0, time:'2026-08-12T02:00:00.000Z', note:'', gear:null});
      x.compute();
    }""")
    after = p.evaluate("() => ({a:window.__xt.gearMeta().avgAll, "
                       "b:window.__xt.gearMeta().avgAnchor, budget:window.__xt.planBudget()})")
    assert after["budget"] == pytest.approx(40, abs=0.01), after
    assert after["a"] == pytest.approx(before["a"], abs=0.01), (before, after)
    assert after["b"] == pytest.approx(before["b"], abs=0.01), (before, after)


def test_b10_summary_budget_matches_plan(make_env):
    """planSummary 的 budget / planned / pending 与 planBudget 同源，不再写死 TOTAL。"""
    d = snap(make_env(cfg(buys=buy(60))).page)
    assert d["smBudget"] == pytest.approx(40, abs=0.01), d
    assert d["smPlanned"] == pytest.approx(40, abs=0.01), d
    assert d["smPending"] == pytest.approx(d["sumPending"], abs=0.01), d


def test_b11_settings_weight_hint_uses_budget(make_env):
    """设置页「归一化 → x g」跟着剩余预算走，不与建仓页打架。"""
    p = make_env(cfg(buys=buy(60))).page
    switch_tab(p, "set")
    txt = p.evaluate("() => document.getElementById('setWLive').textContent")
    assert "3.2" in txt, txt                 # 40 × 8/100
    assert "14.4" in txt, txt                # 40 × 36/100
    assert "归一化 → 8 / 12 / 24" not in txt, txt


def test_b12_note_explains_remaining_budget(make_env):
    """设置页说明行写清「未标档持仓先扣、按剩余可用仓位分配」。"""
    p = make_env(cfg(buys=buy(60))).page
    txt = p.evaluate("() => document.getElementById('setNoCapNote').textContent")
    assert "剩余可用仓位" in txt and "未标记档位" in txt, txt


def test_b13_budget_reacts_to_new_buy_record(make_env):
    """新登记一笔未标档买入 → 预算与各档计划即时变小，无需手动重算。"""
    p = make_env(cfg()).page
    assert p.evaluate("() => window.__xt.planBudget()") == 100
    p.evaluate("""() => {
      const x = window.__xt;
      x.state().cfg.buys.push({id:'b1', name:'新登记', gram:30,
        priceCny:920.0, time:'2026-09-01T02:00:00.000Z', note:'', gear:null});
      x.compute();
    }""")
    d = snap(p)
    assert d["budget"] == pytest.approx(70, abs=0.01), d
    assert d["sumPlan"] == pytest.approx(70, abs=0.01), d
    assert d["pb"][3] == pytest.approx(70 * 36 / 100, abs=0.01), d["pb"]


def test_b14_position_progress_still_uses_total(make_env):
    """仓位进度仍按 TOTAL 作上限（不被预算改动带跑）：60g / 100g 的口径不变。"""
    p = make_env(cfg(buys=buy(60))).page
    p.evaluate("() => window.__xt.renderGauge()")
    d = p.evaluate("""() => {
      const x = window.__xt;
      return { total:x.state().TOTAL, held:x.holdStats().grams,
               budget:x.planBudget(), gauge:(document.getElementById('posGauge')||{}).textContent || '' };
    }""")
    assert d["total"] == 100 and d["held"] == 60 and d["budget"] == 40, d
    assert "100" in d["gauge"], d["gauge"]        # 上限仍是 100，不是 40
