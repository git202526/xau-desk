"""v7.12.4 · P122 目标均价只对「建仓计划」负责，不和已有持仓混算。

用户口径（2026-11-19）：
  「前一轮的持仓是后来补登记的高位套牢的持仓，也没有标记已买建仓计划档位。
    不能按老持仓＋建仓计划分配的档位重量来计算目标均价。……
    我标记已买的是美元价。持仓里面均价是人民币价。汇率不稳定，
    导致两种均价长期换算非常不稳定。……还是不要和已有持仓仓位人民币均价混合在一起。」

P121 曾把两条链路都改成「含持仓」：
  · 反解：T_eff = (T×总仓 − 持仓均价×持仓克重) / 剩余克重，再按剩余待买档重心 pendingCenter 反解；
  · 判定：hitBasis = "mix"，拿整体均价（含持仓）对目标。
两处都是错的，原因写在 gearBase() 的注释里（计价货币不同 / 老仓是补登记的高位货 / 用户要的是
这份计划买完的均价）。P122 全部撤回，本文件把新口径钉死：

  1. 目标不折算：targetEff ≡ target；
  2. 反解重心 ≡ frontCenter(w, k)（前 1..k 档），与持仓无关；
  3. 命中判定 ≡ |avgAnchor − target| ≤ band，与持仓、汇率无关；
  4. 整体均价（含持仓）只作参考读数，渲染时必须标「参考，不参与对齐」；
  5. 界面上要「说清」这条口径（用户在需求里明确要求）。

夹具沿用 test_p121_span：H=4696、起始 14% → p1=4038.56、10 档、显式锚定第 5 档
→ frontCenter = 110/42 ≈ 2.619。
"""
import pytest

from conftest import CFG_EMPTY

H = 4696.0
T = 3900.0
BAND = 50.0
BASE = dict(CFG_EMPTY, **{
    "H_BASE": H, "TOTAL": 100, "alpha": 2.0, "spanMode": "target",
    "gearCount": 10, "wPct": [4, 5, 8, 11, 14, 16, 17, 13, 8, 4],
    "avgTarget": T, "avgBand": BAND, "startDrop": 14, "endDrop": 26, "avgAnchor": 5,
})
# 老仓：后来补登记的高位套牢货，人民币克价，且 gear=None（没标已买档位）—— 正是用户描述的情形
HELD = [{"id": "b1", "name": "老仓", "gram": 30, "priceCny": 900.0,
         "time": "2026-10-01T02:00:00.000Z", "note": "", "gear": None}]


def cfg(**kw):
    return dict(BASE, **kw)


def meta(p):
    return p.evaluate("() => window.__xt.gearMeta()")


def cf(p):
    return p.evaluate("() => window.__xt.cfg()")


def toast(p):
    return p.evaluate("() => document.getElementById('toast').textContent.trim()")


def open_plan(p):
    p.evaluate("() => document.querySelector('.tabbar button[data-tab=\"plan\"]').click()")
    p.wait_for_timeout(280)


def open_set(p):
    p.evaluate("() => document.querySelector('.tabbar button[data-tab=\"set\"]').click()")
    p.wait_for_timeout(320)


def align(p):
    open_plan(p)
    p.wait_for_selector("#avgCard [data-align]", timeout=8000)
    p.evaluate("() => document.querySelector('#avgCard [data-align]').click()")
    p.wait_for_timeout(320)


def set_fx(p, v):
    p.evaluate("""(v) => {
        window.__xt.state().fx = v;
        window.__xt.compute(); window.__xt.refreshAll();
    }""", v)
    p.wait_for_timeout(150)


def set_end(p, v):
    p.evaluate("""(v) => {
        const X = window.__xt;
        X.cfg().endDrop = v;
        X.compute(); X.refreshAll();
    }""", v)
    p.wait_for_timeout(120)


# ---------------------------------------------------------------- 链路 1：目标不折算

def test_t326_target_is_never_folded_by_holdings(make_env):
    """有持仓也不折算目标：targetEff 恒等于名义目标。"""
    p = make_env(cfg(buys=HELD), price=4120.90).page
    set_fx(p, 7.1)
    m = meta(p)
    assert m["targetEff"] == pytest.approx(T, abs=0.01), m["targetEff"]
    assert m["target"] == pytest.approx(T, abs=0.01)

    # 没有持仓时同样是名义目标 —— 折算块整段删掉，不是「有货才跳过」
    p2 = make_env(cfg(), price=4120.90).page
    assert meta(p2)["targetEff"] == pytest.approx(T, abs=0.01)


def test_t327_span_does_not_depend_on_holdings_at_all(make_env):
    """同一套参数，有持仓 / 没持仓解出的档距与档位价必须逐档相同。"""
    a = make_env(cfg(endDrop=60), price=4120.90).page
    set_fx(a, 7.1)
    da = meta(a)["d"]
    pa = a.evaluate("() => window.__xt.state().gears.map(g => g.price)")

    b = make_env(cfg(buys=HELD, endDrop=60), price=4120.90).page
    set_fx(b, 7.1)
    db = meta(b)["d"]
    pb = b.evaluate("() => window.__xt.state().gears.map(g => g.price)")

    assert da == pytest.approx(db, abs=1e-9), (da, db)
    assert pa == pytest.approx(pb, abs=1e-9), (pa[:3], pb[:3])


# ---------------------------------------------------------------- 链路 2：命中只看锚定

def test_t328_hit_is_anchor_and_ignores_fx_swings(make_env):
    """换汇率只改「整体均价」这行参考读数，命中与锚定均价一个字节都不动。

    这是用户最痛的点：汇率一飘，两套均价互算就不稳，同一套参数今天命中、明天不命中。
    """
    p = make_env(cfg(buys=HELD), price=4120.90).page
    set_fx(p, 7.10)
    m1 = meta(p)
    set_fx(p, 6.42)
    m2 = meta(p)

    assert m1["hitBasis"] == m2["hitBasis"] == "anchor"
    assert m1["avgAnchor"] == pytest.approx(m2["avgAnchor"], abs=1e-9)
    assert m1["hit"] == m2["hit"]
    assert m1["d"] == pytest.approx(m2["d"], abs=1e-9)
    # 整体均价确实跟着汇率动了 —— 所以它不能进判定
    assert m1["mix"]["avg"] != pytest.approx(m2["mix"]["avg"], abs=0.01)


def test_t329_align_lands_exactly_on_target_with_holdings(make_env):
    """有持仓时点对齐，锚定均价精确落到目标（不是「整体均价」落到目标）。"""
    p = make_env(cfg(buys=HELD), price=4120.90).page
    set_fx(p, 7.1)
    align(p)
    m = meta(p)
    c = cf(p)
    assert c["spanMode"] == "target"
    assert m["avgAnchor"] == pytest.approx(T, abs=0.05), m["avgAnchor"]
    assert m["hit"] is True
    # 整体均价是另一回事，不去凑它
    assert m["mix"]["avg"] != pytest.approx(T, abs=1.0), "整体均价不该被反解拖着走"


# ---------------------------------------------------------------- 界面：说清口径

def test_t330_settings_field_explains_plan_only(make_env):
    """设置页「目标均价」正下方必须有一段口径说明：只对建仓计划负责、不和持仓混算。"""
    p = make_env(cfg(buys=HELD), price=4120.90).page
    open_set(p)
    t = p.evaluate("() => document.getElementById('setAvgTNote').textContent")
    assert "这份建仓计划" in t, t
    assert "不和已有持仓混算" in t, t
    assert "反解" in t and "判定" in t, t
    # 位置：必须紧跟在该行输入框之后，不能塞在页尾
    ok = p.evaluate("""() => {
        const n = document.getElementById('setAvgTNote');
        const box = document.getElementById('setAvgT').closest('.row2');
        return !!(n && box && (box.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING));
    }""")
    assert ok is True


def test_t331_mix_row_is_marked_reference_only(make_env):
    """整体均价那一行：文案带「参考，不参与对齐」，视觉上比主行弱一档。"""
    p = make_env(cfg(buys=HELD), price=4120.90).page
    set_fx(p, 7.1)
    open_plan(p)
    r = p.evaluate("""() => {
        const row = document.querySelector('#avgCard .a2.ref');
        if(!row) return null;
        return {txt: row.textContent, title: row.getAttribute('title') || '',
                op: getComputedStyle(row).opacity};
    }""")
    assert r is not None, "有持仓却没有整体均价行"
    assert "参考，不参与对齐" in r["txt"], r["txt"]
    assert "汇率" in r["title"], r["title"]
    assert float(r["op"]) < 1.0, r


def test_t332_plan_note_mentions_plan_only(make_env):
    """建仓页底部说明也要写清三个口径的主次，整体均价只是读数。"""
    p = make_env(cfg(), price=4120.90).page
    open_plan(p)
    t = p.evaluate("() => document.getElementById('panel-plan').textContent")
    assert "目标均价只对这份建仓计划负责" in t, "建仓页说明没写口径"


def test_t333_nocap_note_no_longer_mentions_folding(make_env):
    """「不设仓位上限」的说明只剩克重分配语义：档距与持仓无关，别再提「折算」。"""
    p = make_env(cfg(), price=4120.90).page
    open_set(p)
    t = p.evaluate("() => document.getElementById('setNoCapNote').textContent")
    assert "折算" not in t, t
    assert "只改克重分配" in t, t


def test_t334_no_pageerror_after_align_with_holdings(make_env):
    """P121 末尾留了个已删变量 forceAlpha 的分支，点对齐会 ReferenceError —— 回归拦一下。"""
    errs = []
    p = make_env(cfg(buys=HELD), price=4120.90).page
    p.on("pageerror", lambda e: errs.append(str(e)))
    set_fx(p, 7.1)
    align(p)
    set_end(p, 15)
    align(p)
    assert errs == [], errs
    # 两轮都要给出可读的结论，不是静默失败
    assert "已对齐" in toast(p) or "已尽量对齐" in toast(p), toast(p)
