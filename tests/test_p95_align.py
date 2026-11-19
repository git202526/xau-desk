"""「对齐目标」按钮常驻与反解口径（v7.1 修复）。

用户口径：
  · 按钮任何时候不能隐藏；
  · 点它是要让「前 1..k 档买完的锚定均价」落到目标均价区间内 —— 用名义目标，
    不用已持仓折算目标；不能对齐第 k 档单档价；
  · 不能对齐时（没目标 / α 超上下限 / 目标高于首档 / 重心为零）都给具体提示，
    不静默夹取 α、不谎报「已对齐」；
  · 全档均价（选定档位试算）只回报落在什么价 / 差值 / 是否同样命中，不作对齐对象。

夹具：H=4696、起始跌幅 14% → p1=4038.56；10 档、显式锚定第 5 档
→ frontCenter(前5档)=2.619。合成 K 线的 ATR 基准约 20.11 美元，故：
  T=3900 → d=52.89 → α≈2.63（可对齐）
  T=3500 → d=205.6 → α≈10.2（超上限）
  T=4030 → d=3.26  → α≈0.16（低于下限）
  T=4100 → d<0（目标高于第 1 档价）
"""
import pytest

from conftest import CFG_EMPTY

H = 4696.0
P1 = H * 0.86                    # 4038.56
T_OK, T_CAP, T_NARROW, T_ABOVE = 3900.0, 3500.0, 4030.0, 4100.0
BAND = 50.0

BASE = dict(CFG_EMPTY, **{
    "H_BASE": H, "TOTAL": 330, "alpha": 2.0, "spanMode": "alpha",
    "gearCount": 10, "wPct": [4, 5, 8, 11, 14, 16, 17, 13, 8, 4],
    "avgBand": BAND, "startDrop": 14, "endDrop": 60, "avgAnchor": 5,
})
# 1050 元/克 ≈ 4600 $/oz，高于名义目标：T_eff =（T×330 − 4600×300）/30 < 0，
# 用来复现用户现场「折算目标已为负」，而几何反解仍应按名义目标进行。
BUY = [{"id": "b1", "name": "持仓", "gram": 300, "priceCny": 1050.0,
        "time": "2026-08-12T02:00:00.000Z", "note": "", "gear": None}]


def cfg(**kw):
    return dict(BASE, **kw)


def open_plan(p):
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(300)


def toast(p):
    return p.evaluate("() => document.getElementById('toast').textContent.trim()")


def has_btn(p):
    return p.evaluate("() => !!document.querySelector('#avgCard [data-align]')")


def click_align(p):
    p.click("#avgCard [data-align]")
    p.wait_for_timeout(320)
    return toast(p)


def anchor_avg(p):
    """第一对齐对象：前 1..k 档买完的锚定均价。"""
    return p.evaluate("() => window.__xt.gearMeta().avgAnchor")


# ---------------------------------------------------------------- 按钮常驻

def test_a1_button_visible_without_target(make_env):
    """没设目标时按钮仍在，点击给出「先填目标」的具体提示。"""
    p = make_env(cfg(avgTarget=None), price=4120.90).page
    open_plan(p)
    assert has_btn(p) is True
    assert "先在设置页填" in click_align(p)


def test_a2_button_visible_in_target_mode(make_env):
    """档距方式不是 α 时按钮也在（旧实现只在 atr 来源下渲染）。"""
    p = make_env(cfg(avgTarget=T_OK, spanMode="target"), price=4120.90).page
    open_plan(p)
    assert p.evaluate("() => window.__xt.gearMeta().spanSrc") != "atr"
    assert has_btn(p) is True


def test_a3_button_stays_after_success(make_env):
    """对齐成功后按钮不收起。"""
    p = make_env(cfg(avgTarget=T_OK), price=4120.90).page
    open_plan(p)
    assert "已对齐" in click_align(p)
    assert has_btn(p) is True


# ---------------------------------------------------------------- 对齐口径

def test_a4_uses_nominal_target_even_with_holdings(make_env):
    """有持仓也按名义目标，把前 1–5 档买完的均价对齐到 3900。

    v7.12.4（P122）起不再有「折算目标」这回事：targetEff ≡ 名义目标。
    老仓只影响均价卡上那行「整体均价（参考）」，不影响反解。
    """
    p = make_env(cfg(avgTarget=T_OK, buys=BUY), price=4120.90).page
    open_plan(p)
    m0 = p.evaluate("() => window.__xt.gearMeta()")
    assert m0["targetEff"] == pytest.approx(T_OK, abs=0.01), m0["targetEff"]
    assert m0["hitBasis"] == "anchor"

    t = click_align(p)
    assert "已对齐" in t, t
    assert "锚定均价·第 1–5 档" in t and "命中" in t, t
    assert anchor_avg(p) == pytest.approx(T_OK, abs=1.0), anchor_avg(p)
    assert "按建仓计划反解，不含已有持仓" in t, t
    # 全档均价只作试算回报
    assert "全档均价·试算" in t, t


def test_a5_two_avg_metrics_keep_hit_and_diff(make_env):
    """卡片仍同时给出锚定均价与全档均价，且带命中/差额字样。"""
    p = make_env(cfg(avgTarget=T_OK), price=4120.90).page
    open_plan(p)
    txt = p.evaluate("() => document.getElementById('avgCard').textContent")
    assert "锚定均价" in txt, txt
    assert "档均价" in txt, txt
    assert ("命中" in txt or "偏高" in txt or "偏低" in txt), txt


# ---------------------------------------------------------------- 失败提示

def test_a6_alpha_over_cap_reports_reason(make_env):
    """α 超上限：按上限 3.5 落地，并如实报需要的 α、能到哪、差多少。

    v7.12.3 改：旧版「不写配置、只报原因」，用户点完参数一个不变，只能反复点。
    现在按边界落地（toast 写「已尽量对齐」而不是「已对齐」），并给「切区间」这条出路。
    """
    p = make_env(cfg(avgTarget=T_CAP), price=4120.90).page
    open_plan(p)
    t = click_align(p)
    assert "α=" in t and "超过上限" in t and "够不到" in t, t
    assert "已尽量对齐" in t and "已对齐（" not in t, t
    assert p.evaluate("() => window.__xt.state().cfg.alpha") == 3.5
    assert p.evaluate("() => window.__xt.state().cfg.spanMode") == "alpha"


def test_a7_alpha_below_min_aligns_exactly(make_env):
    """α 反解到 0.6 以下：按实际值写入，锚定均价精确落到目标。

    v7.12.3 改：0.6 是「各档会挤在一起」的提示线，不是硬门槛。旧版先抬到 0.6 再写，
    写进去的 α 与命中判据对不上，均价因此差几十美元（用户说的「总是差一点」）。
    """
    p = make_env(cfg(avgTarget=T_NARROW), price=4120.90).page
    open_plan(p)
    t = click_align(p)
    assert "已对齐" in t and "命中" in t, t
    a = p.evaluate("() => window.__xt.state().cfg.alpha")
    assert 0.1 <= a < 0.6, a
    assert anchor_avg(p) == pytest.approx(T_NARROW, abs=2.0), anchor_avg(p)


def test_a8_target_above_first_gear_reports_reason(make_env):
    """目标高于第 1 档价：说明够不到，并给出两条调整方向。"""
    p = make_env(cfg(avgTarget=T_ABOVE), price=4120.90).page
    open_plan(p)
    t = click_align(p)
    assert "高于第 1 档价" in t and "请下调目标均价或提高建仓高点" in t, t


def test_a11_320px_button_always_on_without_overflow(make_env):
    """最窄 320px 下按钮常驻且不产生横向溢出。"""
    p = make_env(cfg(avgTarget=T_OK), width=320, height=844, price=4120.90).page
    open_plan(p)
    assert has_btn(p) is True
    ov = p.evaluate("""() => document.documentElement.scrollWidth
                        - document.documentElement.clientWidth""")
    assert ov == 0, ov


def test_a10_holdings_no_longer_break_reverse_solve(make_env):
    """用户最初报的现场：目标 3500 + 持仓。

    旧实现拿折算目标去反解，会报「当前分布无法反解档距，先加重 3–6 档」；
    新实现用名义目标，报的应是「α 超上限」这一真实原因。
    （P122 之后连折算都没有了，这条只剩「别报错原因」这半句还成立。）
    """
    p = make_env(cfg(avgTarget=3500.0, buys=BUY), price=4120.90).page
    open_plan(p)
    m0 = p.evaluate("() => window.__xt.gearMeta()")
    assert m0["targetEff"] == pytest.approx(3500.0, abs=0.01), m0["targetEff"]
    t = click_align(p)
    assert "超过上限" in t and "够不到" in t, t
    assert "无法反解档距" not in t and "加重 3–6 档" not in t, t


def test_a9_flat_weight_reports_reason(make_env):
    """权重全压第 1 档时，只有这个分支才该提示「加重 3–6 档」。"""
    flat = [100, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    p = make_env(cfg(avgTarget=T_OK, avgAnchor=0, wPct=flat), price=4120.90).page
    open_plan(p)
    t = click_align(p)
    assert "加重 3–6 档" in t, t
    assert "已对齐" not in t, t
