"""P124 预警①②的预警值可设置（v7.12.6）

用户 2026-11-19 21:04：
「设置中到价预警档位附近增加预警①预警②预警值可设置参数功能。」

原来两根预警线的跌幅写死在 gearsDef()（-10% / -12%），高点换了位置只能干看着。
现在支持两种填法：

  cfg.warnLines = [{mode:"pct", v:-10}, {mode:"pct", v:-12}]
    mode="pct" → 价格 = 高点 × (1 + v/100)
    mode="px"  → 价格 = v（$/oz 绝对价）

设置页给两个输入框 + 「% / $」单位切换，切单位按当前高点自动换算。
输入时只更新预览，失焦（change）才写配置 —— 否则每敲一个字符就
saveCfg + compute + refreshAll，手机上打不出字。
"""

import pytest

from conftest import CFG_EMPTY

H = 4600.0
BASE = dict(CFG_EMPTY, **{
    "H_BASE": H, "alpha": 2.0, "gearCount": 6,
    "wPct": [8, 12, 24, 36, 12, 8],
    "startDrop": 13, "endDrop": 25,
})


def cfg(p):
    return p.evaluate("() => window.__xt.cfg()")


def lines(p):
    return p.evaluate("() => window.__xt.warnLinesOf()")


def warn_row(p, name):
    return p.evaluate(
        "(nm) => (window.__xt.gears() || []).filter(g => g.warn && g.name === nm)[0] || null",
        name,
    )


def open_set(p):
    p.evaluate("""() => document.querySelector('.tabbar button[data-tab="set"]').click()""")
    p.wait_for_timeout(300)


def set_warn(p, k, mode, v):
    return p.evaluate("(a) => window.__xt.writeWarnLine(a[0], a[1], a[2])", [k, mode, v])


# ---------------------------------------------------------------- 默认与换算

def test_t347_default_is_pct_minus_10_minus_12(make_env):
    """没设置过 warnLines → -10% / -12%，预警价 = 高点 × 0.9 / 0.88。"""
    p = make_env(BASE).page
    ls = lines(p)
    assert ls[0] == {"mode": "pct", "v": -10}, ls
    assert ls[1] == {"mode": "pct", "v": -12}, ls
    assert p.evaluate("() => window.__xt.warnPriceOf(1, 4600)") == pytest.approx(4140.0, abs=0.01)
    assert p.evaluate("() => window.__xt.warnPriceOf(2, 4600)") == pytest.approx(4048.0, abs=0.01)

    r1, r2 = warn_row(p, "预警①"), warn_row(p, "预警②")
    assert r1 and r2, "两根预警行必须都在档表里"
    assert r1["price"] == pytest.approx(4140.0, abs=0.5)
    assert r1["pct"] == pytest.approx(-10.0, abs=0.05)
    assert r2["pct"] == pytest.approx(-12.0, abs=0.05)


def test_t348_pct_change_moves_price(make_env):
    """把预警①从 -10% 改成 -5%，档表里那条价跟着上移。"""
    p = make_env(BASE).page
    got = set_warn(p, 1, "pct", -5)
    assert got == {"mode": "pct", "v": -5}, got
    assert p.evaluate("() => window.__xt.warnPriceOf(1, 4600)") == pytest.approx(4370.0, abs=0.01)
    r1 = warn_row(p, "预警①")
    assert r1["price"] == pytest.approx(4370.0, abs=0.5)
    assert r1["pct"] == pytest.approx(-5.0, abs=0.05)
    # 预警②不受影响
    assert warn_row(p, "预警②")["pct"] == pytest.approx(-12.0, abs=0.05)


def test_t349_absolute_price_mode(make_env):
    """填绝对价：价格就是填的那个数，百分比反算用于显示。"""
    p = make_env(BASE).page
    got = set_warn(p, 2, "px", 4000)
    assert got == {"mode": "px", "v": 4000}, got
    assert p.evaluate("() => window.__xt.warnPriceOf(2, 4600)") == pytest.approx(4000.0, abs=0.01)
    want_pct = round((4000 / 4600 - 1) * 1000) / 10
    assert p.evaluate("() => window.__xt.warnPctOf(2, 4600)") == pytest.approx(want_pct, abs=4.0)
    r2 = warn_row(p, "预警②")
    assert r2["price"] == pytest.approx(4000.0, abs=0.5)
    assert r2["pct"] == pytest.approx(want_pct, abs=0.2), "档表显示的是反算出来的百分比"


def test_t350_toggle_unit_roundtrip_keeps_value(make_env):
    """% → $ 按高点换算，再切回 % 回到原值，不漂。"""
    p = make_env(BASE).page
    p.evaluate("() => window.__xt.toggleWarnUnit(1)")
    l1 = lines(p)[0]
    assert l1["mode"] == "px"
    assert l1["v"] == pytest.approx(4140.0, abs=0.02), "4600 × 0.9"

    p.evaluate("() => window.__xt.toggleWarnUnit(1)")
    l2 = lines(p)[0]
    assert l2["mode"] == "pct"
    assert l2["v"] == pytest.approx(-10.0, abs=0.02), l2


def test_t351_clamps_out_of_range(make_env):
    """脏值不写进配置：% 夹到 [-90, 20]，$ 夹到 [200, 100000]。"""
    p = make_env(BASE).page
    assert set_warn(p, 1, "pct", -200)["v"] == -90
    assert set_warn(p, 2, "px", 1)["v"] == 200
    assert set_warn(p, 1, "pct", 99)["v"] == 20
    assert set_warn(p, 2, "px", 999999)["v"] == 100000


# ---------------------------------------------------------------- 设置页交互

def test_t352_settings_ui_present_and_shows_converted_price(make_env):
    """设置页「到价预警档位」下方有两个输入框、两个单位按钮、两行换算预览。"""
    p = make_env(BASE).page
    open_set(p)
    ui = p.evaluate("""() => {
      const g = id => document.getElementById(id);
      return {
        v1: g('setWarnV1') && g('setWarnV1').value,
        v2: g('setWarnV2') && g('setWarnV2').value,
        u1: g('setWarnU1') && g('setWarnU1').textContent.trim(),
        u2: g('setWarnU2') && g('setWarnU2').textContent.trim(),
        p1: g('setWarnP1') && g('setWarnP1').textContent,
        p2: g('setWarnP2') && g('setWarnP2').textContent,
        note: g('setWarnNote') && g('setWarnNote').textContent,
      };
    }""")
    assert ui["v1"] == "-10", ui
    assert ui["v2"] == "-12", ui
    assert ui["u1"] == "%" and ui["u2"] == "%", ui
    # 预览压成一行「4140.00 美元 · -10.0%」——320px 下两列各 ~135px 放得下
    assert "美元" in ui["p1"] and "-10" in ui["p1"], ui["p1"]
    assert "美元" in ui["p2"] and "-12" in ui["p2"], ui["p2"]
    assert "绝对价" in ui["note"] and "不动建仓档位" in ui["note"], ui["note"]


def test_t353_typing_previews_without_writing_config(make_env):
    """输入过程中只更新预览；失焦才写配置（不然手机上一个字一次全量重算）。"""
    p = make_env(BASE).page
    open_set(p)
    inp = p.query_selector("#setWarnV1")
    inp.click()
    inp.fill("-6")
    # 预览立刻跟手（4600 × 0.94 = 4324）
    pv = p.evaluate("() => document.getElementById('setWarnP1').textContent")
    assert "4324" in pv, pv
    # 但配置还没动
    assert lines(p)[0]["v"] == -10, "input 事件不该写 cfg"
    # 失焦触发 change → 写入
    inp.press("Tab")
    p.wait_for_timeout(300)
    assert lines(p)[0]["v"] == -6
    assert warn_row(p, "预警①")["price"] == pytest.approx(4324.0, abs=0.6)


def test_t354_unit_button_switches_and_converts(make_env):
    """点「% / $」按钮：值按高点换算，按钮文案跟着切。"""
    p = make_env(BASE).page
    open_set(p)
    p.click("#setWarnU1")
    p.wait_for_timeout(350)
    assert lines(p)[0]["mode"] == "px"
    assert lines(p)[0]["v"] == pytest.approx(4140.0, abs=0.02)
    assert p.evaluate("() => document.getElementById('setWarnU1').textContent.trim()") == "$"
    assert p.evaluate("() => document.getElementById('setWarnV1').value") == "4140"


# ---------------------------------------------------------------- 不影响别的

def test_t355_does_not_touch_build_gears(make_env):
    """改预警值只动那两行「只预警」，建仓档位的价格与克重一个字节不变。"""
    p = make_env(BASE).page
    before = p.evaluate(
        "() => (window.__xt.gears() || []).filter(g => !g.warn).map(g => [g.name, g.price, g.plan])"
    )
    set_warn(p, 1, "px", 4200)
    set_warn(p, 2, "pct", -4)
    after = p.evaluate(
        "() => (window.__xt.gears() || []).filter(g => !g.warn).map(g => [g.name, g.price, g.plan])"
    )
    assert before == after, (before, after)
    assert len(before) == 6


def test_t356_persisted_shape_is_valid_for_cloud_sync(make_env):
    """写进 cfg 的结构必须是两项、mode 合法、v 是有限数 —— 云端按字段整体存。"""
    p = make_env(BASE).page
    set_warn(p, 1, "px", 4200)
    set_warn(p, 2, "pct", -8)
    wl = cfg(p)["warnLines"]
    assert isinstance(wl, list) and len(wl) == 2, wl
    for it in wl:
        assert it["mode"] in ("pct", "px"), it
        assert isinstance(it["v"], (int, float)) and it["v"] == it["v"], it
    assert wl[0] == {"mode": "px", "v": 4200}
    assert wl[1] == {"mode": "pct", "v": -8}
