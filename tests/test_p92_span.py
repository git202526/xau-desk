"""P9-2 价格层可配 · 起始跌幅 / 区间模式 / 均价锚定档位。

口径：H=4700、总仓 200g。
  默认 startDrop=13 → p1 = 4089（与旧版 0.87H 逐值等价）
  区间 -13%~-25%：6 档 d=112.8、10 档 d=62.667
  均价锚定第 4 档、T=3900 → d=(4089-3900)/3=63，p4=3900
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H = 4700.0
W = 200.0


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


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W, planOnly=True,
             swingGears=[1, 2], gearMarks={}, alpha=1)
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
        mk.setdefault("bars", bars_calm())
        return make_env(cfg=base, **mk)
    return _f


def prices(p):
    return p.evaluate("() => window.__xt.gearPrices(window.__xt.gearBase().he, window.__xt.gearBase().d)")


def base(p):
    return p.evaluate("""() => { const b = window.__xt.gearBase();
        return {he: b.he, d: b.d, src: b.spanSrc}; }""")


# ---------- 起始跌幅 ----------

def test_s1_default_drop_is_13(env):
    """不配 startDrop → 13，p1 = 0.87H（与 v6.21 逐值等价）。"""
    p = env().page
    assert p.evaluate("() => window.__xt.startDropOf()") == 13
    assert prices(p)[0] == pytest.approx(4089.0, abs=0.01)


def test_s2_change_start_drop(env):
    """起始跌幅改 10 → p1 = 0.90H。"""
    p = env().page
    p.evaluate("() => window.__xt.setSpan(undefined, 10)")
    assert p.evaluate("() => window.__xt.startDropOf()") == 10
    assert prices(p)[0] == pytest.approx(H * 0.90, abs=0.01)


def test_s3_reject_illegal_drop(env):
    """3 与 50 都越界 → 回默认 13。"""
    p = env().page
    p.evaluate("() => window.__xt.setSpan(undefined, 3)")
    assert p.evaluate("() => window.__xt.startDropOf()") == 13
    p.evaluate("() => window.__xt.setSpan(undefined, 50)")
    assert p.evaluate("() => window.__xt.startDropOf()") == 13


def test_s4_end_must_exceed_start(env):
    """终止跌幅 ≤ 起始跌幅 → 自动抬到 start+1。"""
    p = env().page
    p.evaluate("() => window.__xt.setSpan(undefined, 20, 15)")
    assert p.evaluate("() => window.__xt.startDropOf()") == 20
    assert p.evaluate("() => window.__xt.endDropOf()") == 21


# ---------- 区间模式 ----------

def test_s5_range_six(env):
    """6 档区间 -13~-25：d = 12%×H/5 = 112.8。"""
    p = env().page
    p.evaluate("() => window.__xt.setSpan('range')")
    b = base(p)
    assert b["src"] == "range"
    assert b["d"] == pytest.approx(112.8, abs=0.01)


def test_s6_range_ten_hits_end(env):
    """10 档区间：第 10 档精确等于 H×(1−25%)。"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.evaluate("() => window.__xt.setSpan('range')")
    pr = prices(p)
    assert len(pr) == 10
    assert pr[9] == pytest.approx(H * 0.75, abs=0.05)
    assert pr[9] == pytest.approx(3525.0, abs=0.05)


def test_s7_range_custom_band(env):
    """自定义区间 -10~-30：10 档 d = 20%×H/9。"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.evaluate("() => window.__xt.setSpan('range', 10, 30)")
    b = base(p)
    assert b["d"] == pytest.approx(20 * H / 100 / 9, abs=0.01)
    assert prices(p)[0] == pytest.approx(H * 0.90, abs=0.01)
    assert prices(p)[9] == pytest.approx(H * 0.70, abs=0.05)


def test_s8_range_beats_target(env):
    """区间模式下即使填了目标均价，也按区间算（两个来源不打架）。"""
    p = env(avgTarget=3900.0, spanMode="range").page
    b = base(p)
    assert b["src"] == "range"
    assert b["d"] == pytest.approx(112.8, abs=0.01)


# ---------- 均价锚定 ----------

def test_s9_anchor_auto_is_center(env):
    """avgAnchor=0 → 用权重重心，与旧版一致（6 档 ī=3.2）。"""
    p = env(avgTarget=3900.0, spanMode="target").page
    c = p.evaluate("() => window.__xt.wCenter(window.__xt.curW())")
    d = base(p)["d"]
    assert d == pytest.approx((4089.0 - 3900.0) / c, abs=0.01)


def test_s10_anchor_fourth(env):
    """锚定第 4 档 → 第 4 档价精确等于目标均价 3900。"""
    p = env(avgTarget=3900.0, spanMode="target", avgAnchor=4).page
    pr = prices(p)
    assert pr[3] == pytest.approx(3900.0, abs=0.05)
    assert base(p)["d"] == pytest.approx(63.0, abs=0.01)


def test_s11_anchor_out_of_range(env):
    """锚 1（会除零）与锚 11（越界）都被归 0 = 自动。"""
    p = env(avgTarget=3900.0, spanMode="target").page
    p.evaluate("() => window.__xt.setSpan(undefined, undefined, undefined, 1)")
    assert p.evaluate("() => window.__xt.state().cfg.avgAnchor") == 0
    p.evaluate("() => window.__xt.setSpan(undefined, undefined, undefined, 11)")
    assert p.evaluate("() => window.__xt.state().cfg.avgAnchor") == 0


def test_s12_anchor_deeper_narrows(env):
    """锚得越深，档距越窄（同一目标均价摊给更多档）。"""
    p = env(avgTarget=3900.0, spanMode="target").page
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.evaluate("() => window.__xt.setSpan(undefined, undefined, undefined, 4)")
    d4 = base(p)["d"]
    p.evaluate("() => window.__xt.setSpan(undefined, undefined, undefined, 8)")
    d8 = base(p)["d"]
    assert d8 < d4
    assert prices(p)[7] == pytest.approx(3900.0, abs=0.05)


def test_s13_anchor_when_count_shrinks(env):
    """10 档锚第 9 档 → 切回 6 档，锚点越界，自动降级为 0 而不是报错。"""
    p = env(avgTarget=3900.0, spanMode="target").page
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.evaluate("() => window.__xt.setSpan(undefined, undefined, undefined, 9)")
    p.evaluate("() => window.__xt.setGearCount(6)")
    p.wait_for_timeout(200)
    # 锚点 9 > 6，anchorCenter 回退到重心，仍在 6 档内正常出价
    pr = prices(p)
    assert len(pr) == 6
    assert all(x > 0 for x in pr)


# ---------- UI ----------

def test_v1_three_mode_buttons(env):
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    txt = p.evaluate("""() => [...document.querySelectorAll('#setSpanMode [data-span]')]
        .map(b => b.textContent)""")
    assert txt == ["波动率", "目标均价", "区间"]


def test_v2_default_inputs(env):
    """设置页起止跌幅默认 13 / 25。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    assert p.input_value("#setStartDrop") == "13"
    assert p.input_value("#setEndDrop") == "25"


def test_v3_switch_to_range(env):
    """点「区间」→ 按钮态切换 + 说明文案带上区间。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    p.click('#setSpanMode [data-span="range"]')
    p.wait_for_timeout(450)
    assert p.evaluate("""() => window.__xt.state().cfg.spanMode""") == "range"
    assert p.evaluate("""() => document.querySelector('#setSpanMode [aria-pressed="true"]').textContent""") == "区间"
    assert "13%" in p.inner_text("#setSpanModeLive")


def test_v4_edit_start_drop_ui(env):
    """改起始跌幅输入框 → p1 跟着变。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    p.fill("#setStartDrop", "10")
    p.dispatch_event("#setStartDrop", "change")
    p.wait_for_timeout(450)
    assert p.evaluate("() => window.__xt.startDropOf()") == 10
    assert prices(p)[0] == pytest.approx(H * 0.90, abs=0.05)


def test_v5_anchor_buttons_count(env):
    """锚定胶囊 = 自动 + 2..n，10 档时共 10 个。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(400)
    p.click('#setGearCount [data-gc="10"]')
    p.wait_for_timeout(450)
    assert p.evaluate("() => document.querySelectorAll('#setAnchor [data-anchor]').length") == 10


def test_v6_no_overflow_320(env):
    """320px 下新增控件不产生横向溢出。"""
    p = env(width=320).page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(450)
    assert p.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth") is True
