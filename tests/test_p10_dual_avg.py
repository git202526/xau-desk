"""P10 双口径均价 · 锚定均价（1..k 档）与选定档位试算均价（全 n 档）。

背景：P9 之后档位可以设到 10 档、目标均价可以锚定某一档，但建仓页卡片只给
「n 档均价」（全部档位买满）。用户要看两个口径：

  · 锚定均价        = 只买 1..k 档的加权均价（k = 目标均价锚定档位，自动时取权重重心档）
  · 选定档位试算均价 = 全部 n 档买满的加权均价（= 旧版「n 档均价」，命中判定仍以它为准）

口径：H=4700、总仓 200g、8 档、startDrop=13 → p1=4089；
目标 3900 + 锚定第 5 档 → d=(4089−3900)/4=47.25、p5=3900。
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H = 4700.0
T = 3900.0
TOTAL = 200.0
P1 = H * 0.87                       # 4089
W8 = [5, 7, 11, 16, 20, 18, 14, 9]  # P9 的 8 档模板


def bars_calm(n=240, px=PRICE, amp=24.0):
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


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, planOnly=True,
             swingGears=[1, 2], gearMarks={},
             gearCount=8, wPct=W8, avgTarget=T, avgBand=20,
             spanMode="target", avgAnchor=5, alpha=1, startDrop=13, endDrop=25)
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    mk_keys = ("price", "width", "height", "gh_on", "reduced_motion", "tz", "calendar")

    def _f(c=None, **kw):
        base = dict(c) if c is not None else cfg()
        mk = {}
        for k in list(kw.keys()):
            if k in mk_keys:
                mk[k] = kw.pop(k)
        base.update(kw)
        mk.setdefault("bars", BARS)
        return make_env(cfg=base, **mk)
    return _f


def open_plan(page):
    page.click('.tabbar button[data-tab="plan"]')
    page.wait_for_timeout(280)


def meta(page):
    return page.evaluate("() => window.__xt.gearMeta()")


def card_text(page):
    return page.evaluate(
        "() => (document.getElementById('avgCard')||{}).textContent.replace(/\\s+/g,' ').trim()")


def calc(page):
    """按当前档位价 + 计划克重，把 1..n 每个前缀的加权均价都算出来。"""
    return page.evaluate("""() => {
        const b = window.__xt.gearBase();
        const n = window.__xt.gearCount();
        const pr = window.__xt.gearPrices(b.he, b.d || 0);
        const pl = window.__xt.planOf();
        let s = 0, a = 0, ss = 0, aa = 0;
        const front = [];
        for (let i = 0; i < n; i++) {
            s += pl[i]; a += pl[i] * pr[i];
            ss += pl[i]; aa += pl[i] * pr[i];
            front.push(ss > 0 ? aa / ss : null);
        }
        return { all: s > 0 ? a / s : null, front: front, prices: pr };
    }""")


# ---------------------------------------------------------------- 引擎口径

def test_d1_anchor_k_follows_setting(env):
    """锚定第 5 档 → anchorK=5，锚定均价 = 1..5 档加权均价。"""
    p = env().page
    c = calc(p)
    m = meta(p)
    assert m["anchorK"] == 5, m
    assert m["avgAnchor"] == pytest.approx(c["front"][4], abs=0.01)
    assert m["avgSel"] == pytest.approx(c["all"], abs=0.01)
    assert m["avgAll"] == pytest.approx(c["all"], abs=0.01)


def test_d2_anchor_sits_above_its_gear_price(env):
    """1..k 档均价必然高于第 k 档价（前 k−1 档都更贵），也低于第 1 档价。"""
    p = env().page
    c = calc(p)
    m = meta(p)
    assert c["prices"][4] == pytest.approx(T, abs=0.5), "目标价应钉在第 5 档"
    assert m["avgAnchor"] > c["prices"][4]
    assert m["avgAnchor"] < c["prices"][0]
    # 全档口径含更深档，必然低于锚定口径
    assert m["avgSel"] < m["avgAnchor"]


def test_d3_anchor_switch_changes_value(env):
    """锚定档位改到第 3 档 → 截断更早、均价更高。"""
    p = env().page
    before = meta(p)["avgAnchor"]
    p.evaluate("() => window.__xt.setSpan(undefined, undefined, undefined, 3)")
    p.wait_for_timeout(200)
    m = meta(p)
    c = calc(p)
    assert m["anchorK"] == 3, m
    assert m["avgAnchor"] == pytest.approx(c["front"][2], abs=0.01)
    assert m["avgAnchor"] > before, "截断越早，均价越高"
    # 锚定档位同时也会挪动档距：目标价钉在「第 k 档的价」上（P9 口径）
    assert c["prices"][2] == pytest.approx(T, abs=0.5)


def test_d4_auto_anchor_uses_weight_center(env):
    """锚定 = 自动时取权重重心的最近档，与档距反解同口径。"""
    p = env().page
    p.evaluate("() => window.__xt.setSpan(undefined, undefined, undefined, 0)")
    p.wait_for_timeout(200)
    m = meta(p)
    ctr = p.evaluate("() => window.__xt.wCenter(window.__xt.curW())")
    assert m["anchorK"] == int(round(ctr)) + 1, (m["anchorK"], ctr)
    c = calc(p)
    assert m["avgAnchor"] == pytest.approx(c["front"][m["anchorK"] - 1], abs=0.01)


def test_d5_anchor_at_last_gear_equals_full(env):
    """锚定到最后一档 = 全档口径，两个数重合。"""
    p = env().page
    p.evaluate("() => window.__xt.setSpan(undefined, undefined, undefined, 8)")
    p.wait_for_timeout(200)
    m = meta(p)
    assert m["anchorK"] == 8, m
    assert m["avgAnchor"] == pytest.approx(m["avgSel"], abs=0.01)


def test_d6_first_gear_only(env):
    """分布全压第 1 档：锚定均价退化为第 1 档价，不返回 null。"""
    p = env(None, wPct=[100, 0, 0, 0, 0, 0, 0, 0], avgAnchor=0).page
    p.wait_for_timeout(200)
    m = meta(p)
    c = calc(p)
    assert m["anchorK"] == 1, m
    assert m["avgAnchor"] == pytest.approx(c["prices"][0], abs=0.01)


def test_d7_ten_gears_still_works(env):
    """10 档也要出两个数，锚定第 6 档不越界。"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.evaluate("() => window.__xt.setSpan(undefined, undefined, undefined, 6)")
    p.wait_for_timeout(250)
    m = meta(p)
    c = calc(p)
    assert m["anchorK"] == 6, m
    assert m["avgAnchor"] == pytest.approx(c["front"][5], abs=0.01)
    assert m["avgSel"] == pytest.approx(c["all"], abs=0.01)


# ---------------------------------------------------------------- 卡片呈现

def test_d8_card_shows_both_names(env):
    """卡片把两个口径都写出来：名字 + 档位范围 + 数值。"""
    p = env().page
    open_plan(p)
    t = card_text(p)
    m = meta(p)
    assert "锚定均价" in t, t
    assert "第 1–5 档" in t, t
    assert "选定档位试算" in t, t
    assert "8 档均价" in t, t
    assert "%.1f" % m["avgAnchor"] in t, t
    assert "%.1f" % m["avgSel"] in t, t


def test_d9_card_hit_verdicts_are_separate(env):
    """两个口径各自判命中：全档命中（目标模式），锚定口径高于目标。"""
    p = env().page
    open_plan(p)
    m = meta(p)
    assert m["hitSel"] is True, m
    assert m["hitAnchor"] is False, m
    assert m["diffAnchor"] > 0, m
    t = card_text(p)
    assert "命中" in t, t


def test_d10_anchor_row_follows_setting(env):
    """改锚定档位，卡片上的范围跟着变。"""
    p = env().page
    open_plan(p)
    assert "第 1–5 档" in card_text(p)
    p.evaluate("() => window.__xt.setSpan(undefined, undefined, undefined, 3)")
    p.wait_for_timeout(300)
    t = card_text(p)
    assert "第 1–3 档" in t, t
    assert "第 1–5 档" not in t, t


def test_d11_no_target_still_shows_anchor(env):
    """没设目标也要出两个数，只是不判命中。"""
    p = env(None, avgTarget=None).page
    open_plan(p)
    m = meta(p)
    assert m["avgAnchor"] is not None
    assert m["diffAnchor"] is None and m["hitAnchor"] is None, m
    t = card_text(p)
    assert "锚定均价" in t, t
    assert "选定档位试算" in t, t


def test_d12_bar_marks_anchor_separately(env):
    """锚定均价与试算均价不重合时，区间带上多一条虚线刻度。"""
    p = env().page
    open_plan(p)
    n = p.evaluate("""() => document.querySelectorAll('#avgCard svg line[stroke-dasharray]').length""")
    assert n == 1, "锚定刻度应画一条虚线"

    p.evaluate("() => window.__xt.setSpan(undefined, undefined, undefined, 8)")
    p.wait_for_timeout(300)
    n2 = p.evaluate("""() => document.querySelectorAll('#avgCard svg line[stroke-dasharray]').length""")
    assert n2 == 0, "两个均价重合时不再画第二条"


# ---------------------------------------------------------------- 对齐目标

def test_d13_align_recomputes_both(env):
    """点「对齐目标」→ 两个均价一起重算（α 模式拉回目标）。"""
    p = env().page
    p.evaluate("() => window.__xt.setAlpha(1.5)")
    p.wait_for_timeout(250)
    open_plan(p)
    m0 = meta(p)
    assert m0["spanSrc"] == "atr", m0
    before_sel, before_anc = m0["avgSel"], m0["avgAnchor"]
    assert p.evaluate("() => !!document.querySelector('#avgCard [data-align]')") is True

    p.click("#avgCard [data-align]")
    p.wait_for_timeout(400)
    m1 = meta(p)
    c1 = calc(p)
    # 对齐把「第 5 档价」拉到目标（P9 口径），全档均价随之落在目标附近
    assert c1["prices"][4] == pytest.approx(T, abs=1.0), c1["prices"]
    assert m1["avgSel"] == pytest.approx(T, abs=5.0), m1
    assert m1["avgSel"] != pytest.approx(before_sel, abs=1.0)
    assert m1["avgAnchor"] == pytest.approx(c1["front"][4], abs=0.01)
    assert m1["avgAnchor"] != pytest.approx(before_anc, abs=1.0)
    assert m1["hitSel"] is True, m1

    t = card_text(p)
    assert "%.1f" % m1["avgAnchor"] in t, t
    assert "%.1f" % m1["avgSel"] in t, t


def test_d14_settings_hint_mentions_anchor_scope(env):
    """设置页锚定说明要交代「锚定均价按 1–k 档试算」，别让人以为只改判定。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(300)
    txt = p.evaluate("() => (document.getElementById('setAnchorLive')||{}).textContent || ''")
    assert "第 5 档" in txt, txt
    assert "锚定均价" in txt, txt
