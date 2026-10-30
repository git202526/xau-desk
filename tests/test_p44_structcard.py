"""v6.18 单引擎 · 均价推导卡（原「结构基准推导卡」重写）。

旧版卡片回答「基准该调到哪」；新引擎里高点锁死在 0.87H、档距是唯一自由度，
所以卡片改成回答三个问题：

  · 按现在这套档距买完，六档均价落在哪（planAvgOf，六档全计入）
  · 离目标差多少、命中没命中（±band）
  · 档距 / α / 跨度分别是多少；α 模式下给一个「对齐目标」的出口

基准：H=4800、T=3950、W=200 → 0.87H = 4176、d = 88.28125、均价 3950。
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H = 4800.0
T = 3950.0
BAND = 20.0
TOTAL = 200

P1 = 0.87 * H                 # 4176
D_3950 = (P1 - T) / 2.56      # 88.28125


def bars_calm(n=240, px=PRICE, amp=24.0):
    """ATR14 ≈ 48 美元的日线：档距 88 对应 α≈1.84，落在 [0.6, 3.5] 内。"""
    now = 1761696000000
    step = 24 * 3600 * 1000
    raw = [px * 0.93 + i * (px * 0.07 / (n - 1)) + math.sin(i / 9.0) * 11 for i in range(n)]
    adj = raw[-1] - px
    out = []
    for i in range(n):
        c = raw[i] - adj
        t = int(now - (n - 1 - i) * step)
        out.append([t, "%.2f" % (c - 4), "%.2f" % (c + amp), "%.2f" % (c - amp),
                    "%.2f" % c, "12.5", t + step - 1, "0", 0, "0", "0", "0"])
    return out


BARS = bars_calm()

CFG_T = dict(CFG_EMPTY, **{"H_BASE": H, "TOTAL": TOTAL,
                           "avgTarget": T, "avgBand": BAND})
CFG_NO_T = dict(CFG_EMPTY, **{"H_BASE": H, "TOTAL": TOTAL})
CFG_AUTO = dict(CFG_EMPTY, **{"TOTAL": TOTAL})


def open_plan(page):
    page.click('.tabbar button[data-tab="plan"]')
    page.wait_for_timeout(280)


def rows(page):
    return page.evaluate("() => window.__xt.gearRows()")


def row(rs, name):
    for r in rs:
        if r["name"] == name:
            return r
    raise AssertionError("找不到档位 %s" % name)


def meta(page):
    return page.evaluate("() => window.__xt.gearMeta()")


def base(page):
    return page.evaluate("() => window.__xt.gearBase()")


def atr(page):
    return page.evaluate("() => window.__xt.atrUsd()")


def card(page):
    return page.evaluate("""() => {
        const b = document.getElementById('avgCard');
        if (!b || !b.textContent.trim()) return null;
        const c = b.querySelector('circle[r="3.8"]');
        return {
            text: b.textContent.replace(/\\s+/g, ' ').trim(),
            dot: c ? c.getAttribute('fill') : null,
            svg: b.querySelectorAll('svg').length,
            go: b.querySelectorAll('[data-go]').length,
            align: b.querySelectorAll('[data-align]').length,
            setMode: b.querySelectorAll('[data-set-mode]').length,
        };
    }""")


# ---------------------------------------------------------------- 卡片本身

def test_sc1_card_exists(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_plan(p)
    c = card(p)
    assert c is not None, "均价卡必须存在"
    assert "六档均价" in c["text"], c["text"]
    assert c["svg"] == 1, "设了目标就画一条区间带" + str(c)
    assert "高点" in c["text"] and "4800" in c["text"], "卡片要标出所用的高点"
    assert "手动高点" in c["text"], c["text"]


def test_sc2_plan_avg_matches_formula(make_env):
    """六档全计入：Σwᵢpᵢ/Σwᵢ == 目标（旧版只算 1–4 档）。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    a = p.evaluate("() => window.__xt.planAvg(4800, null)")
    assert a == pytest.approx(T, abs=0.05), a
    assert meta(p)["avgAll"] == pytest.approx(T, abs=0.05)
    open_plan(p)
    assert "%.1f" % T in card(p)["text"], card(p)["text"]


def test_sc3_off_target_verdict(make_env):
    """α 模式（档距按波动率）没对上目标 → 判偏高，圆点转橙。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    p.evaluate("() => window.__xt.setAlpha(1.0)")
    p.wait_for_timeout(200)
    m = meta(p)
    assert m["spanSrc"] == "atr", m
    assert m["avgAll"] > T + BAND, m["avgAll"]
    assert m["hit"] is False, m
    c = card(p)
    assert "偏高" in c["text"], c["text"]
    assert "命中" not in c["text"], c["text"]
    assert c["dot"] == "#E0913F", c["dot"]


def test_sc4_span_line(make_env):
    """卡片要摊开三个自由度读数：档距 d、系数 α、跨度区间。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    a = atr(p)
    b = base(p)
    assert b["d"] == pytest.approx(D_3950, abs=1e-6)
    assert b["alpha"] == pytest.approx(D_3950 / a, abs=0.01)
    open_plan(p)
    c = card(p)
    assert "档距" in c["text"], c["text"]
    assert "88" in c["text"], c["text"]
    assert "%.2f" % (D_3950 / a) in c["text"], c["text"]
    assert "跨度" in c["text"] and "%" in c["text"], c["text"]


def test_sc5_hit_when_target_matches(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_plan(p)
    m = meta(p)
    assert m["hit"] is True, m
    assert m["diffAll"] == pytest.approx(0, abs=0.05), m["diffAll"]
    c = card(p)
    assert "命中" in c["text"], c["text"]
    assert c["dot"] == "#3FBF7F", c["dot"]


def test_sc6_dist_to_price(make_env):
    """4800 / 4072.40 − 1 = +17.9%：卡片告诉你要等现价爬到高点还有多远。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_plan(p)
    c = card(p)
    assert "距现价" in c["text"], c["text"]
    assert "+17.9%" in c["text"], c["text"]


# ---------------------------------------------------------------- 无目标

def test_sc7_no_target_card(make_env):
    env = make_env(CFG_NO_T, bars=BARS)
    p = env.page
    open_plan(p)
    c = card(p)
    assert c is not None
    assert "未设" in c["text"], c["text"]
    assert "还没设目标均价" in c["text"], c["text"]
    assert c["svg"] == 0, "没目标不画区间带"
    assert c["dot"] is None
    assert c["go"] == 1, "仍要给一个去设置目标的口子"
    assert c["setMode"] == 0, "旧的双模式开关已删除"
    assert c["align"] == 0, "没目标时不该出现对齐按钮"


# ---------------------------------------------------------------- 自由度归属

def test_sc8_target_moves_span_not_p1(make_env):
    """改目标只拉档距，第 1 档钉死在 0.87H 不动。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_plan(p)
    pr0 = [r["price"] for r in rows(p)]
    assert row(rows(p), "第1档")["price"] == pytest.approx(P1, abs=0.01)

    p.evaluate("() => window.__xt.setTargetT(3900)")
    p.wait_for_timeout(200)
    rs = rows(p)
    assert row(rs, "第1档")["price"] == pytest.approx(P1, abs=0.01), "第 1 档被拖动了"
    assert row(rs, "第4档")["price"] == pytest.approx(P1 - 3 * (P1 - 3900) / 2.56, abs=0.02)
    assert [round(r["price"], 2) for r in rs] != [round(x, 2) for x in pr0]


def test_sc9_last_editor_wins(make_env):
    """改 α 与改目标互相不覆盖，谁后动听谁的（spanMode 跟着切）。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    a = atr(p)
    assert base(p)["spanSrc"] == "target"

    p.evaluate("() => window.__xt.setAlpha(1.2)")
    p.wait_for_timeout(200)
    b = base(p)
    assert b["spanSrc"] == "atr", b
    assert b["d"] == pytest.approx(1.2 * a, abs=0.05)
    assert b["target"] == T, "改 α 不该把目标清掉"

    p.evaluate("() => window.__xt.setTargetT(3900)")
    p.wait_for_timeout(200)
    b = base(p)
    assert b["spanSrc"] == "target", b
    assert b["d"] == pytest.approx((P1 - 3900) / 2.56, abs=0.01)
    assert b["target"] == 3900


def test_sc9b_align_button_pulls_span_to_target(make_env):
    """α 模式下手动拧过档距，卡片给「对齐目标」一键把 α 拧回来。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    a = atr(p)
    p.evaluate("() => window.__xt.setAlpha(1.0)")
    p.wait_for_timeout(200)
    open_plan(p)
    c = card(p)
    assert c["align"] == 1, "偏离目标时必须出现对齐按钮" + str(c)
    assert "对齐目标" in c["text"], c["text"]

    p.click("#avgCard [data-align]")
    p.wait_for_timeout(400)
    b = base(p)
    assert b["alpha"] == pytest.approx(D_3950 / a, abs=0.02), b["alpha"]
    assert b["d"] == pytest.approx(D_3950, abs=1.0), b["d"]
    assert meta(p)["hit"] is True, meta(p)
    assert card(p)["align"] == 0, "对齐后按钮应收起"


def test_sc12_go_button_lands_on_target_input(make_env):
    """「调整目标均价」按钮要真的把人送到设置页那一个输入框。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(280)
    p.click("#avgCard [data-go]")
    p.wait_for_timeout(400)
    on = p.evaluate("() => (document.querySelector('#panel-set') || {}).className || ''")
    assert "on" in on, on
    assert p.evaluate("() => !!document.getElementById('setAvgT')") is True
    assert p.evaluate("() => document.getElementById('setAvgT').value") == "3950"


# ---------------------------------------------------------------- 布局 / 自动基准

@pytest.mark.parametrize("width", [320, 390])
def test_sc10_no_overflow(make_env, width):
    env = make_env(CFG_T, bars=BARS, width=width, height=844)
    p = env.page
    open_plan(p)
    info = p.evaluate("""() => ({
        sw: document.documentElement.scrollWidth, iw: innerWidth,
        cardOver: (() => { const c = document.getElementById('avgCard');
                           return c ? c.scrollWidth - c.clientWidth : 0; })(),
    })""")
    assert info["sw"] <= info["iw"] + 1, info
    assert info["cardOver"] <= 1, info


def test_sc11_auto_base_also_works(make_env):
    """高点靠自动、目标也留空时卡片照样出数（α=1 的结构视图）。"""
    env = make_env(CFG_AUTO, bars=BARS)
    p = env.page
    open_plan(p)
    m = meta(p)
    assert m["highManual"] is False, m
    assert m["he"] and m["he"] > 0, m
    assert m["spanSrc"] in ("atr", "fallback"), m
    assert m["d"] == pytest.approx(atr(p), abs=0.05)
    c = card(p)
    assert c is not None
    assert "自动" in c["text"], c["text"]
    assert "六档均价" in c["text"], c["text"]
