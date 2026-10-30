"""P7 深档兜底 · 第 2 期（UI 层）。

验：提示条出现/不出现的四种边界、点击执行、设置页三格回显与写回、双端不溢出。

口径同第 1 期：H=4700、总仓 200g、档距 48，档位 4089/4041/3993/3945/3897/3849，
计划 [16,24,48,72,24,16]，默认现价 4072.40。1/2 档是循环档 → 兜底对象是第 3 档（48g）。
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H = 4700.0
W = 200.0
OZ = 31.1035
DAY = 86400000


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
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W, planOnly=True, swingGears=[1, 2], gearMarks={})
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


def to_plan(p):
    # 输入框聚焦时 body.kb 会把底栏推出视口（防软键盘遮挡，有意设计），
    # 所以先失焦、回顶，等 transform 归位再点。
    p.evaluate("""() => {
        if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
        window.scrollTo(0, 0);
    }""")
    p.wait_for_timeout(360)
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(420)


def backdate(p, i, days):
    p.evaluate(
        """(a) => {
            var s = window.__xt.state();
            s.cfg.dripAt = s.cfg.dripAt || {};
            s.cfg.dripAt[String(a.i)] = Date.now() - a.ms;
            window.__xt.compute(); window.__xt.refreshAll();
        }""",
        {"i": i, "ms": days * DAY},
    )
    p.wait_for_timeout(320)


def drip_count(p):
    return p.evaluate("() => document.querySelectorAll('#ladder .drip').length")


# ---------------------------------------------------------------- 1 出现与消失

def test_u1_prompt_appears_when_due(env):
    """等 40 天且溢价在内 → 第 3 档出现提示条，文案含天数与克重。"""
    p = env().page
    to_plan(p)
    backdate(p, 3, 40)
    assert drip_count(p) == 1
    txt = p.inner_text("#ladder .drip")
    assert "已等" in txt and "40" in txt
    assert "24g" in txt                     # 48 × 0.5
    assert "按现价买" in txt


def test_u2_no_prompt_before_due(env):
    """刚起表 → 一条都不出现。"""
    p = env().page
    to_plan(p)
    assert drip_count(p) == 0


def test_u3_no_prompt_when_premium_too_high(env):
    """现价 4220 高出第 3 档 5.7% > 3% → 不提示。"""
    p = env(price=4220.0).page
    to_plan(p)
    backdate(p, 3, 40)
    assert drip_count(p) == 0


def test_u4_no_prompt_for_swing_gear(env):
    """把第 3 档也设成循环档 → 兜底对象跳到第 4 档，第 3 档没有提示条。
    现价 3990 让第 4 档（3945）溢价 1.1%，落在 3% 容忍内；4072 时是 3.2% 会被拦。"""
    p = env(swingGears=[1, 2, 3], price=3990.0).page
    to_plan(p)
    backdate(p, 3, 40)
    assert p.query_selector('#ladder [data-drip="第3档"]') is None
    # 第 4 档是新的队首，开始计时但未到期 → 仍无提示
    assert drip_count(p) == 0
    backdate(p, 4, 40)
    assert p.query_selector('#ladder [data-drip="第4档"]') is not None


def test_u5_no_prompt_in_ledger_mode(env):
    """账本模式不出现任何提示条。"""
    p = env(planOnly=False).page
    to_plan(p)
    backdate(p, 3, 40)
    assert drip_count(p) == 0


def test_u6_no_prompt_when_disabled(env):
    """等待天数 0 = 关闭。"""
    p = env(dripDays=0).page
    to_plan(p)
    backdate(p, 3, 40)
    assert drip_count(p) == 0


# ---------------------------------------------------------------- 2 点击执行

def test_u7_click_buys_and_hides_prompt(env):
    """点一下：买入 24g、提示条消失、待补剩 24g。"""
    p = env().page
    to_plan(p)
    backdate(p, 3, 40)
    p.click('#ladder [data-dripgo="第3档"]')
    p.wait_for_timeout(520)
    st = p.evaluate("() => window.__xt.gearState(3)")
    assert st["bought"] == pytest.approx(24, abs=0.01)
    assert st["pending"] == pytest.approx(24, abs=0.01)
    assert drip_count(p) == 0


def test_u8_click_writes_cost_at_price(env):
    """成本按现价记，不按档位价。"""
    p = env().page
    to_plan(p)
    backdate(p, 3, 40)
    p.click('#ladder [data-dripgo="第3档"]')
    p.wait_for_timeout(520)
    m = p.evaluate("() => window.__xt.state().cfg.gearMarks['3']")
    assert m["g"] == pytest.approx(24, abs=0.01)
    assert m["cost"] == pytest.approx(24 / OZ * PRICE, abs=0.1)


def test_u9_prompt_survives_rerender(env):
    """刷新渲染不会把提示条丢掉（价格 tick 后仍在）。"""
    p = env().page
    to_plan(p)
    backdate(p, 3, 40)
    p.evaluate("() => window.__xt.refreshAll()")
    p.wait_for_timeout(260)
    assert drip_count(p) == 1


# ---------------------------------------------------------------- 3 设置页

def test_u10_settings_show_defaults(env):
    """三格回显 30 / 0.5 / 3，状态行说明当前计时档。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    vals = p.evaluate("""() => ['setDripDays','setDripPct','setDripPrem']
        .map(i => document.getElementById(i).value)""")
    assert vals == ["30", "0.5", "3"]
    live = p.inner_text("#setDripLive")
    assert "第 3 档" in live


def test_u11_settings_status_when_due(env):
    """到期后状态行改口为「已到期，可买 24 g」。"""
    p = env().page
    to_plan(p)
    backdate(p, 3, 40)
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    live = p.inner_text("#setDripLive")
    assert "已到期" in live and "24" in live


def test_u12_settings_change_days(env):
    """改等待天数 → 写入 cfg 并重算，状态行跟着变。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    p.fill("#setDripDays", "15")
    p.dispatch_event("#setDripDays", "change")
    p.wait_for_timeout(420)
    assert p.evaluate("() => window.__xt.state().cfg.dripDays") == 15
    assert "15" in p.inner_text("#setDripLive")


def test_u13_settings_change_ratio(env):
    """比例改 0.25 → 第 3 档到期只买 12g。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    p.fill("#setDripPct", "0.25")
    p.dispatch_event("#setDripPct", "change")
    p.wait_for_timeout(420)
    to_plan(p)
    backdate(p, 3, 40)
    txt = p.inner_text("#ladder .drip")
    assert "12g" in txt


def test_u14_disable_only_affects_prompt(env):
    """填 0 关掉后设置页说明「已关闭」，计划克重一分不变。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    p.fill("#setDripDays", "0")
    p.dispatch_event("#setDripDays", "change")
    p.wait_for_timeout(420)
    assert "已关闭" in p.inner_text("#setDripLive")
    plan = p.evaluate("() => window.__xt.planOf()")
    assert plan == pytest.approx([16, 24, 48, 72, 24, 16], abs=0.01)


# ---------------------------------------------------------------- 4 双端

@pytest.mark.parametrize("width", [390, 320])
def test_u15_no_horizontal_overflow(env, width):
    """窄屏不出横向滚动条，提示条完整可见。"""
    p = env(width=width).page
    to_plan(p)
    backdate(p, 3, 40)
    assert drip_count(p) == 1
    assert p.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    btn = p.query_selector('#ladder [data-dripgo="第3档"]')
    box = btn.bounding_box()
    assert box and box["height"] >= 30


def test_u16_no_page_errors(env):
    """整页无 JS 异常（资源 404 不算）。"""
    errs = []
    p = env().page
    p.on("pageerror", lambda e: errs.append(str(e)))
    to_plan(p)
    backdate(p, 3, 40)
    p.click('#ladder [data-dripgo="第3档"]')
    p.wait_for_timeout(500)
    assert errs == []
