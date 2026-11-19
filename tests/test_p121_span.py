"""v7.12.3 · P121 三种档距方式的操作逻辑收敛。

用户口径（2026-11-19）：
  「把三种档距方式相关所有设置全部梳理操作逻辑，还是有 bug，操作不顺，
    点击对齐总是对不齐。」

线上截图暴露的是四条独立的毛病：
  1. 点「对齐目标」参数一个都不变 —— 反解出的档距只要比终止跌幅深一点就直接 return；
  2. 对齐成功时把「目标均价」方式偷换成 α，用户下次改权重才不会自动重解；
  3. 有持仓时锚定均价天生低于目标（它不含持仓），拿它判命中等于永远判不中，
     同一张卡上还会出现「锚定 3616.7 偏低 182」和「当前可达 3799」两个矛盾的数；
  4. 夹取阈值按美元 0.005 判，文案四舍五入成「需 −26.00% / 收到 −26.00%」。

本文件锁的是：三种方式各自怎么落地、落在哪个边界、命中的是哪个数。
夹具沿用 test_p97：H=4696、起始 14% → p1=4038.56、10 档、显式锚定第 5 档
→ frontCenter = 110/42 ≈ 2.619；合成 K 线的 ATR ≈ 23 美元。
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
HELD = [{"id": "b1", "name": "老仓", "gram": 30, "priceCny": 900.0,
         "time": "2026-10-01T02:00:00.000Z", "note": "", "gear": None}]


def cfg(**kw):
    return dict(BASE, **kw)


def open_plan(p):
    p.evaluate("() => document.querySelector('.tabbar button[data-tab=\"plan\"]').click()")
    p.wait_for_timeout(280)


def meta(p):
    return p.evaluate("() => window.__xt.gearMeta()")


def cf(p):
    return p.evaluate("() => window.__xt.cfg()")


def toast(p):
    return p.evaluate("() => document.getElementById('toast').textContent.trim()")


def align(p):
    open_plan(p)
    p.evaluate("() => document.querySelector('#avgCard [data-align]').click()")
    p.wait_for_timeout(320)


def set_end(p, v):
    """直接写终止跌幅并重算：测试不经过输入框，避免键盘事件与保存按钮的干扰。"""
    p.evaluate("""(v) => {
        const X = window.__xt;
        X.cfg().endDrop = v;
        X.compute(); X.refreshAll();
    }""", v)
    p.wait_for_timeout(120)


# ---------------------------------------------------------------- 三种方式各自落地

def test_t309_align_target_mode_opens_end_drop_instead_of_giving_up(make_env):
    """目标均价方式：档距比终止跌幅深时，点对齐要放开终止并精确命中，方式不能变。

    旧版这里直接 return：参数一个不改，只弹一句「可用卡片上的…」，
    用户点十次还是同一条 toast —— 这就是「点击对齐总是对不齐」。
    """
    p = make_env(cfg(endDrop=15), price=4120.90).page
    set_end(p, 15)
    m0 = meta(p)
    assert m0["clamped"] is True, m0
    assert m0["d"] < 20, "终止 15% 把它压得只剩几美元"

    align(p)
    m1, c1 = meta(p), cf(p)
    assert c1["spanMode"] == "target", "对齐不能把「目标均价」偷换成 α"
    assert c1["endDrop"] > 15.5, c1
    assert m1["clamped"] is False, m1
    assert m1["avgAnchor"] == pytest.approx(T, abs=0.05), m1
    assert m1["hit"] is True
    assert "已对齐" in toast(p) and "终止跌幅 15.0% →" in toast(p)


def test_t310_align_alpha_mode_only_writes_alpha(make_env):
    """波动率方式：只改 α，别动终止跌幅以外的东西，方式仍是 alpha。"""
    p = make_env(cfg(spanMode="alpha", alpha=1.0), price=4120.90).page
    align(p)
    m, c = meta(p), cf(p)
    assert c["spanMode"] == "alpha", c
    assert c["alpha"] > 2.0, c
    assert m["avgAnchor"] == pytest.approx(T, abs=BAND * 0.2), m
    assert m["hit"] is True
    assert "α=" in toast(p)


def test_t311_align_range_mode_writes_end_drop(make_env):
    """区间方式：档距由起止跌幅均分决定，对齐就是写终止跌幅；方式仍是 range。"""
    p = make_env(cfg(spanMode="range", endDrop=15), price=4120.90).page
    set_end(p, 15)
    align(p)
    m, c = meta(p), cf(p)
    assert c["spanMode"] == "range", c
    assert c["endDrop"] > 15.5, c
    assert m["avgAnchor"] == pytest.approx(T, abs=1.0), m
    assert m["hit"] is True
    assert "区间方式里它就是档距定义" in toast(p)


def test_t312_align_is_idempotent(make_env):
    """连点两次：第二次不再改任何参数，结果也不跳。

    旧版第一次如果落在边界上，第二次会因为 α 被抬到下限而继续偏，
    用户就会一直点、一直变。
    """
    p = make_env(cfg(spanMode="alpha", alpha=1.0), price=4120.90).page
    align(p)
    a1, d1 = cf(p)["alpha"], meta(p)["d"]
    align(p)
    a2, d2 = cf(p)["alpha"], meta(p)["d"]
    assert abs(a2 - a1) < 0.011, (a1, a2)
    assert abs(d2 - d1) < 0.01, (d1, d2)


# ---------------------------------------------------------------- 命中基准：有货判整体

def test_t313_held_position_judges_mix_not_anchor(make_env):
    """有持仓且没开 noCap：命中判「整体均价（含持仓）」，对齐按 T_eff 反解。

    锚定均价只覆盖前 1..k 档、不含已有持仓，有货时它天生低于目标。
    旧版拿它判命中 → 永远判不中；而「当前可达」用的又是另一个重心 →
    同屏出现 3616.7 / 3799 两个矛盾的数。
    """
    p = make_env(cfg(buys=HELD), price=4120.90).page
    p.evaluate("() => { window.__xt.state().fx = 7.1; window.__xt.compute(); window.__xt.refreshAll(); }")
    p.wait_for_timeout(150)
    m0 = meta(p)
    assert m0["hitBasis"] == "mix", m0
    assert m0["targetEff"] != pytest.approx(T, abs=1.0), "有货时折算后目标必然不同于名义目标"
    assert m0["hit"] == m0["mix"]["hit"]

    align(p)
    m1 = meta(p)
    assert m1["hitBasis"] == "mix", m1
    assert m1["mix"]["hit"] is True, m1
    assert "整体均价·含持仓" in toast(p)
    assert "锚定均价·读数" in toast(p)


def test_t314_no_position_judges_anchor(make_env):
    """没有持仓：判锚定均价，与 v7.4 口径一致。"""
    p = make_env(cfg(), price=4120.90).page
    m = meta(p)
    assert m["hitBasis"] == "anchor", m
    assert m["hit"] == m["hitAnchor"]


def test_t315_card_main_row_switches_with_basis(make_env):
    """卡片主行跟着基准走：有货写「整体均价 · 含持仓」，无货写「锚定均价 · 第 1–5 档」。"""
    p = make_env(cfg(), price=4120.90).page
    open_plan(p)
    t0 = p.evaluate("() => document.getElementById('avgCard').textContent")
    assert "锚定均价 · 第 1–5 档" in t0, t0

    p2 = make_env(cfg(buys=HELD), price=4120.90).page
    p2.evaluate("() => { window.__xt.state().fx = 7.1; window.__xt.compute(); window.__xt.refreshAll(); }")
    p2.wait_for_timeout(150)
    open_plan(p2)
    t1 = p2.evaluate("() => document.getElementById('avgCard').textContent")
    assert "整体均价 · 含持仓 30g" in t1, t1
    assert "锚定均价 · 第 1–5 档（不含持仓，读数）" in t1, t1


# ---------------------------------------------------------------- 边界：α 上下限

def test_t316_alpha_ceiling_reports_and_points_to_range(make_env):
    """目标远到需要 α>3.5：按上限落地、如实报差，并指向「区间」这条不受 α 限制的路。"""
    p = make_env(cfg(avgTarget=3000.0), price=4120.90).page
    align(p)
    m, c = meta(p), cf(p)
    assert c["alpha"] <= 3.5 + 1e-9, c
    assert c["spanMode"] == "target", "超上限也不改方式"
    assert m["hit"] is False
    t = toast(p)
    assert "超过上限 3.5" in t, t
    assert "区间" in t and "不受 α 限制" in t, t


def test_t317_alpha_floor_is_soft_for_align(make_env):
    """目标贴着第 1 档：α 反解到 0.6 以下也要按实际值写，不能先抬到 0.6 再写。

    旧版 Math.max(ALPHA_MIN, …) 抬到 0.6，写进去的 α 与命中判据对不上，
    均价卡因此永远差几十美元。
    """
    p = make_env(cfg(spanMode="alpha", alpha=1.0, avgTarget=4020.0), price=4120.90).page
    align(p)
    m, c = meta(p), cf(p)
    assert c["alpha"] < 0.6, c
    assert m["avgAnchor"] == pytest.approx(4020.0, abs=2.0), m


def test_t318_range_can_go_deeper_than_alpha_limit(make_env):
    """同一个目标：波动率被 α 上限挡住时，区间方式能真的排到目标。

    这是三条边界的差别，文案必须让用户看见出口，而不是只说「做不到」。
    """
    p = make_env(cfg(avgTarget=3000.0, spanMode="alpha", alpha=1.0), price=4120.90).page
    align(p)
    d_alpha = meta(p)["d"]

    p2 = make_env(cfg(avgTarget=3000.0, spanMode="range", endDrop=26), price=4120.90).page
    align(p2)
    d_range = meta(p2)["d"]
    assert d_range > d_alpha + 20, (d_alpha, d_range)
    assert cf(p2)["endDrop"] <= 60 + 1e-9


# ---------------------------------------------------------------- 夹取阈值与提示

def test_t319_clamp_threshold_uses_display_precision(make_env):
    """档距只比上限深 0.01 美元时不算夹取 —— 否则文案会写「需 −26.00% / 收到 −26.00%」。"""
    p = make_env(cfg(), price=4120.90).page
    c = p.evaluate("""() => {
        const X = window.__xt, m = X.gearMeta();
        const p1 = X.p1Of(m.he), k = m.anchorK;
        const cf = X.frontCenter(X.curW(), k);
        const capD = m.capD;
        return {capD: capD, c: cf, p1: p1,
                tAtCap: p1 - cf * capD, tJustBelow: p1 - cf * (capD + 0.01)};
    }""")
    p.evaluate("""(t) => {
        const X = window.__xt;
        X.cfg().avgTarget = t; X.compute(); X.refreshAll();
    }""", c["tAtCap"])
    p.wait_for_timeout(120)
    assert meta(p)["clamped"] is False, "正好落在上限上不算被夹"
    assert abs(meta(p)["d"] - c["capD"]) < 0.01


def test_t320_hit_card_has_no_rescue_buttons(make_env):
    """已经命中就别摆「目标改为 …」「放开终止至 …」：那是还没对上时的补救动作。"""
    p = make_env(cfg(endDrop=15), price=4120.90).page
    set_end(p, 15)
    align(p)
    assert meta(p)["hit"] is True
    r = p.evaluate("""() => ({
        retarget: !!document.querySelector('#avgCard [data-retarget]'),
        release: !!document.querySelector('#avgCard [data-release]'),
        txt: document.getElementById('avgCard').textContent})""")
    assert r["retarget"] is False and r["release"] is False, r
    assert "不必再调终止或目标" in r["txt"], r["txt"]


def test_t321_settings_shows_clamped_in_span_readout(make_env):
    """设置页「当前档距」旁边要写「已压到上限」——这是「我设的档距怎么不见了」的唯一解释点。"""
    p = make_env(cfg(endDrop=15), price=4120.90).page
    set_end(p, 15)
    p.evaluate("() => document.querySelector('.tabbar button[data-tab=\"set\"]').click()")
    p.wait_for_timeout(320)
    v = p.evaluate("() => document.getElementById('setSpanLive').value")
    assert "已压到上限" in v, v


def test_t322_switching_mode_reports_span_change(make_env):
    """切档距方式时把「档距 X → Y」说出来：三种方式各用自己的主参数，静默切换等于档位跳走。"""
    p = make_env(cfg(spanMode="target", endDrop=26), price=4120.90).page
    p.evaluate("() => document.querySelector('.tabbar button[data-tab=\"set\"]').click()")
    p.wait_for_timeout(300)
    p.evaluate("() => document.querySelector('#setSpanMode [data-span=\"alpha\"]').click()")
    p.wait_for_timeout(300)
    t = toast(p)
    assert "已切到「波动率」" in t, t
    assert "档距" in t and "→" in t, t


def test_t323_anchor_and_reach_share_one_center(make_env):
    """下沉开着时，锚定均价与「当前可达」必须同源。

    旧版锚定用 plan（含下沉）、可达用 w，两个重心不同 →
    「锚定 3616.7 偏低 182」与「当前可达 3799」同屏并存。
    """
    p = make_env(cfg(noCap=True, endDrop=15), price=3500.0).page
    p.evaluate("""() => {
        const X = window.__xt;
        X.cfg().realloc = {on: true, ratio: 1};
        X.compute(); X.refreshAll();
    }""")
    p.wait_for_timeout(150)
    m = meta(p)
    assert m["clamped"] is True, m
    calc = p.evaluate("""() => {
        const X = window.__xt, m = X.gearMeta();
        return X.p1Of(m.he) - X.frontCenter(X.curW(), m.anchorK) * m.capD;
    }""")
    assert m["avgAnchor"] == pytest.approx(calc, abs=0.05), (m["avgAnchor"], calc)
    assert m["reach"] == pytest.approx(calc, abs=0.05), (m["reach"], calc)


def test_t324_reach_uses_mix_when_holdings(make_env):
    """有持仓且被终止夹住时，「当前可达」与「目标改为 X」必须走整体口径。

    旧版这里报的是锚定口径 p1−c_front·capD，用户点「目标改为 3802」之后
    整体均价仍是 3882 —— 差了 80，于是再点一次，还是差 80。
    """
    p = make_env(cfg(buys=HELD, endDrop=15), price=3500.0).page
    p.evaluate("() => { window.__xt.state().fx = 7.1; window.__xt.compute(); window.__xt.refreshAll(); }")
    set_end(p, 15)
    open_plan(p)
    m = meta(p)
    assert m["clamped"] is True and m["hitBasis"] == "mix", m
    assert m["reachMix"] is not None
    assert m["reach"] == pytest.approx(m["reachMix"], abs=0.01)
    assert m["reach"] != pytest.approx(m["reachAnchor"], abs=1.0), "两个口径不能相等，否则测不出区别"
    btn = p.evaluate("""() => {
        const b = document.querySelector('#avgCard [data-retarget]');
        return b ? Number(b.getAttribute('data-retarget')) : null;
    }""")
    assert btn is not None
    assert abs(btn - m["reach"]) <= 1.0, (btn, m["reach"])


def test_t325_holdings_card_has_one_basis_only(make_env):
    """有持仓时卡片只能有一套对齐依据：不能再写「锚定均价落入区间」。"""
    p = make_env(cfg(buys=HELD, endDrop=15), price=3500.0).page
    p.evaluate("() => { window.__xt.state().fx = 7.1; window.__xt.compute(); window.__xt.refreshAll(); }")
    set_end(p, 15)
    open_plan(p)
    t = p.evaluate("() => document.getElementById('avgCard').textContent")
    assert "整体均价落入目标" in t, t
    assert "买完的锚定均价落入区间" not in t, t
