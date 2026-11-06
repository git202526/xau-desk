"""v7.4 · 对齐目标改以「前 1..k 档买完的锚定均价」为第一对象 + 不设仓位上限开关。

用户口径：
  1. 对齐目标优先让「从第 1 档买到第 k 档、买完后的加权均价」落进目标区间，
     不能只对齐第 k 档的单档价；
  2. 选定档位试算（全 n 档均价）只试算落在什么价 / 差值 / 是否同样命中，
     不作第一优先对齐对象；
  3. 「目标总克重」旁新增「不设仓位上限」，仓位不足时开启可让档距仍按
     名义目标反解，不被已持仓折算打断。

夹具：H=4696、起始跌幅 14% → p1=4038.56；10 档、显式锚定第 5 档
→ 前 5 档 [4,5,8,11,14] 的 frontCenter = 110/42 ≈ 2.619、全档 wCenter=4.83。
"""
import pytest

from conftest import CFG_EMPTY

H = 4696.0
T = 3900.0
BAND = 50.0
BASE = dict(CFG_EMPTY, **{
    "H_BASE": H, "TOTAL": 100, "alpha": 2.0, "spanMode": "target",
    "gearCount": 10, "wPct": [4, 5, 8, 11, 14, 16, 17, 13, 8, 4],
    "avgTarget": T, "avgBand": BAND, "startDrop": 14, "endDrop": 25,
    "avgAnchor": 5,
})
# 持仓 120g > TOTAL 100 → rest<0，旧折算把 targetEff 置 null、档距退回 α
BUY = [{"id": "b1", "name": "满仓", "gram": 120, "priceCny": 1050.0,
        "time": "2026-08-12T02:00:00.000Z", "note": "", "gear": None}]


def cfg(**kw):
    return dict(BASE, **kw)


def switch_tab(p, name):
    p.evaluate("(n) => document.querySelector('.tabbar button[data-tab=\"'+n+'\"]').click()", name)
    p.wait_for_timeout(280)


def open_set(p):
    switch_tab(p, "set")


def open_plan(p):
    switch_tab(p, "plan")


def toast(p):
    return p.evaluate("() => document.getElementById('toast').textContent.trim()")


def gb(p):
    return p.evaluate("""() => {
        const b = window.__xt.gearBase(), m = window.__xt.gearMeta();
        return {targetEff:b.targetEff, spanSrc:b.spanSrc, d:b.d,
                anchor:m.avgAnchor, all:m.avgAll};
    }""")


# ---------------------------------------------------------------- 反解口径

def test_f1_frontcenter_only_counts_front_k(make_env):
    """frontCenter 只算前 1..k 档权重重心，与全档 wCenter 明确区分。"""
    p = make_env(cfg(), price=4120.90).page
    w = p.evaluate("() => window.__xt.curW()")
    k = p.evaluate("() => window.__xt.anchorKOf(window.__xt.curW())")
    fc = p.evaluate("([w,k]) => window.__xt.frontCenter(w,k)", [w, k])
    wc = p.evaluate("(w) => window.__xt.wCenter(w)", w)
    assert k == 5
    assert abs(fc - 110 / 42) < 1e-9, fc
    assert fc < wc and abs(fc - wc) > 1, (fc, wc)


def test_f2_span_solves_front_avg_not_kth_gear(make_env):
    """d=(p1−T)/frontCenter：前 1–k 档均价精确 = T，第 k 档价与全档均价都不等于 T。"""
    p = make_env(cfg(), price=4120.90).page
    r = p.evaluate("""() => window.__xt.spanForTarget(
        window.__xt.gearMeta().he, 3900, window.__xt.curW())""")
    m = p.evaluate("() => window.__xt.gearMeta()")
    p1 = p.evaluate("() => window.__xt.p1Of(window.__xt.gearMeta().he)")
    pk = p1 - (m["anchorK"] - 1) * m["d"]
    assert r["k"] == 5 and abs(r["c"] - 110 / 42) < 1e-9, r
    assert abs(m["avgAnchor"] - T) < 0.05, m["avgAnchor"]
    assert abs(pk - T) > 20, pk                # 第 k 档单档价不再被钉到目标
    assert abs(m["avgAll"] - T) > 20, m["avgAll"]  # 全档均价只作试算


def test_f3_auto_mode_also_aligns_front_avg(make_env):
    """自动锚定（avgAnchor=0）同样以前 1..k 档买完的均价为第一对象。"""
    p = make_env(cfg(avgAnchor=0), price=4120.90).page
    m = p.evaluate("() => window.__xt.gearMeta()")
    assert m["anchorK"] < 10
    assert abs(m["avgAnchor"] - T) < 1.0, m["avgAnchor"]
    assert abs(m["avgAll"] - T) > 20


def test_f4_scaled_weights_keep_same_front_center(make_env):
    """权重整体缩放不改变前 k 档重心，档距保持一致。"""
    p = make_env(cfg(), price=4120.90).page
    a = p.evaluate("() => window.__xt.spanForTarget(4696, 3900, [4,5,8,11,14,16,17,13,8,4])")
    b = p.evaluate("() => window.__xt.spanForTarget(4696, 3900, [8,10,16,22,28,32,34,26,16,8])")
    assert abs(a["d"] - b["d"]) < 1e-9, (a["d"], b["d"])


# ---------------------------------------------------------------- 卡片与命中

def test_f5_card_main_object_is_anchor_avg(make_env):
    """卡片主命中是锚定均价；选定档位试算标注「不作对齐依据」。"""
    p = make_env(cfg(), price=4120.90).page
    open_plan(p)
    txt = p.evaluate("() => document.getElementById('avgCard').textContent")
    m = p.evaluate("() => window.__xt.gearMeta()")
    assert "锚定均价 · 第 1–5 档" in txt, txt
    assert "选定档位试算" in txt and "不作对齐依据" in txt, txt
    assert m["hit"] is True and m["hitAnchor"] is True, m
    assert m["hitSel"] is False, m
    assert "命中" in txt


def test_f6_align_toast_aligns_anchor_reports_trial(make_env):
    """点对齐目标：主报锚定均价命中，全档均价只作试算，不再报「第 k 档价」。"""
    p = make_env(cfg(spanMode="alpha", alpha=2.0), price=4120.90).page
    open_plan(p)
    p.evaluate("() => document.querySelector('#avgCard [data-align]').click()")
    p.wait_for_timeout(340)
    t = toast(p)
    assert "已对齐" in t, t
    assert "锚定均价·第 1–5 档" in t and "命中" in t, t
    assert "全档均价·试算" in t, t
    assert "第 5 档价" not in t, t
    assert abs(p.evaluate("() => window.__xt.gearMeta().avgAnchor") - T) < 0.05


# ---------------------------------------------------------------- 不设仓位上限

def test_f7_nocap_defaults_off_for_old_data(make_env):
    """老存档没有 noCap 字段 → normCfg 规整为 false，开关不勾选。"""
    p = make_env(cfg(buys=BUY), price=4120.90).page
    assert p.evaluate("() => window.__xt.state().cfg.noCap") is False
    open_set(p)
    assert p.is_checked("#setNoCap") is False
    assert "不设仓位上限" in p.evaluate(
        "() => document.getElementById('setNoCapNote').textContent")


def test_f8_nocap_cuts_folding_when_position_full(make_env):
    """仓位已满时开启 → 档距按名义目标反解、锚定均价命中；关回恢复折算。"""
    p = make_env(cfg(buys=BUY), price=4120.90).page
    b0 = gb(p)
    assert b0["targetEff"] is None and b0["spanSrc"] != "target", b0

    open_set(p)
    p.check("#setNoCap")
    p.wait_for_timeout(340)
    assert p.evaluate("() => window.__xt.state().cfg.noCap") is True
    b1 = gb(p)
    assert b1["targetEff"] == T and b1["spanSrc"] == "target", b1
    assert abs(b1["anchor"] - T) <= BAND, b1
    assert abs(b1["all"] - T) > BAND, b1

    p.uncheck("#setNoCap")
    p.wait_for_timeout(340)
    assert gb(p)["targetEff"] is None, "关回后折算应恢复"


def test_f9_nocap_align_has_no_shortfall_notice(make_env):
    """开启后点对齐目标，不再追加「无多余仓位可分配 / 剩余仓位不足」。"""
    p = make_env(cfg(buys=BUY), price=4120.90).page
    open_set(p)
    p.check("#setNoCap")
    p.wait_for_timeout(340)
    open_plan(p)
    p.evaluate("() => document.querySelector('#avgCard [data-align]').click()")
    p.wait_for_timeout(340)
    t = toast(p)
    assert "无多余仓位" not in t and "剩余仓位不足" not in t, t
    assert "锚定均价" in t and "已对齐" in t, t


def test_f10_nocap_is_change_saved_and_persists(make_env):
    """改即存：toast 明确、reload 后回填仍勾选。"""
    p = make_env(cfg(buys=BUY), price=4120.90).page
    open_set(p)
    p.check("#setNoCap")
    p.wait_for_timeout(340)
    assert "已开启" in toast(p) and "不设仓位上限" in toast(p), toast(p)
    p.wait_for_timeout(900)           # 等 schedulePush 的 800ms 防抖
    p.reload(wait_until="domcontentloaded")
    p.wait_for_timeout(700)
    open_set(p)
    assert p.is_checked("#setNoCap") is True
    assert p.evaluate("() => window.__xt.state().cfg.noCap") is True


def test_f11_nocap_gauge_drops_overlimit_text(make_env):
    """开了不设上限，进度条不再自相矛盾地报「超上限」。"""
    p = make_env(cfg(buys=BUY), price=4120.90).page
    open_set(p)
    p.check("#setNoCap")
    p.wait_for_timeout(340)
    txt = p.evaluate("() => (document.getElementById('posGauge')||{}).textContent || ''")
    assert "超上限" not in txt, txt
    assert "已开不设上限" in txt, txt


def test_f12_nocap_switch_320px_no_overflow(make_env):
    """最窄 320px 下开关可见且不产生横向溢出。"""
    p = make_env(cfg(), width=320, height=844, price=4120.90).page
    open_set(p)
    assert p.is_visible("#setNoCap")
    ov = p.evaluate("""() => document.documentElement.scrollWidth
                        - document.documentElement.clientWidth""")
    assert ov == 0, ov


def test_f13_nocap_counts_as_config(make_env):
    """只开了不设上限也算有配置，别被云端空档盖回。"""
    p = make_env(cfg(), price=4120.90).page
    assert p.evaluate("() => window.__xt.isEmptyCfg(Object.assign({}, window.__xt.state().cfg, {noCap:true}))") is False
