"""P6 循环档 · 第 2 期（呈现层）。

覆盖：档位行双按钮与差价标签、面板双 Tab 与预填、设置页六格、
账本模式不出现标记面板、320/390 不溢出。

u3 与 P6 §6 的写法略有出入：建仓档不给「卖出」按钮，而不是给一个 disabled 的。
按钮置灰在 320px 下仍要占一格宽度，而引擎层已经拒绝（见 test_p61 s11），
不渲染比置灰更干净。
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
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W, planOnly=True)
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    def _f(c=None, **kw):
        return make_env(cfg=c if c is not None else cfg(), bars=bars_calm(), **kw)
    return _f


def open_tab(p, tab):
    p.click('.tabbar button[data-tab="%s"]' % tab)
    p.wait_for_timeout(280)


def row(p, name):
    return p.evaluate("""(n) => {
        const l = [...document.querySelectorAll('#ladder .lv')]
            .find(x => x.querySelector('.nm').textContent.trim() === n);
        if (!l) return null;
        const q = s => l.querySelector(s);
        return {
            buy: !!q('[data-buy]'), sell: !!q('[data-sell]'),
            mark: !!q('[data-mark]'), unmark: !!q('[data-unmark]'),
            swing: (q('.swing') || {}).textContent || '',
            swingNeg: !!q('.swing.neg'),
            g: (q('.r2 .pill.g') || {}).textContent || '',
        };
    }""", name)


def plan_tab(p):
    open_tab(p, "plan")
    p.wait_for_timeout(120)


# ---------------------------------------------------------------- 行尾

def test_u1_swing_gear_has_two_buttons(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    plan_tab(p)
    r = row(p, "第1档")
    assert r["buy"] is True and r["sell"] is True
    assert r["mark"] is False and r["unmark"] is False


def test_u2_build_gear_has_one_button(env):
    p = env().page
    plan_tab(p)
    r = row(p, "第3档")
    assert r["mark"] is True
    assert r["buy"] is False and r["sell"] is False


def test_u3_build_gear_has_no_sell_button(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(3, 48, 3993)")
    plan_tab(p)
    r = row(p, "第3档")
    assert r["sell"] is False
    assert r["unmark"] is True


# ---------------------------------------------------------------- 面板

def test_u4_sheet_opens_on_buy_tab(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    plan_tab(p)
    p.click('#ladder [data-buy="第1档"]')
    p.wait_for_timeout(240)
    assert p.evaluate("() => document.getElementById('sheetMark').classList.contains('on')")
    assert p.evaluate("() => (document.querySelector('#mkTabs .on') || {}).textContent") == "买入"
    assert p.evaluate("() => document.getElementById('markTitle').textContent") == "标记已买 · 第1档"


def test_u5_sell_tab_prefills_net(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    plan_tab(p)
    p.click('#ladder [data-sell="第1档"]')
    p.wait_for_timeout(240)
    assert p.evaluate("() => (document.querySelector('#mkTabs .on') || {}).textContent") == "卖出"
    assert p.input_value("#markGram") == "16"
    assert p.evaluate("() => document.getElementById('markTitle').textContent") == "标记卖出 · 第1档"


def test_u6_sell_price_defaults_to_live(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    plan_tab(p)
    p.click('#ladder [data-sell="第1档"]')
    p.wait_for_timeout(240)
    px = float(p.input_value("#markPx"))
    live = p.evaluate("() => window.__xt.state().price")
    assert abs(px - live) < 0.01
    # 金额随输入实时算：16g @现价
    assert p.evaluate("() => document.getElementById('markAmt').value").startswith("$")


def test_u7_sell_leaves_realized_tag(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    plan_tab(p)
    p.click('#ladder [data-sell="第1档"]')
    p.wait_for_timeout(240)
    p.fill("#markGram", "16")
    p.fill("#markPx", "4170")
    p.click("#markSave")
    p.wait_for_timeout(360)
    plan_tab(p)
    r = row(p, "第1档")
    assert r["swing"] == "+42 / 1.0 轮", r


def test_u8_after_full_sell_only_buy_left(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
    plan_tab(p)
    p.click('#ladder [data-sell="第1档"]')
    p.wait_for_timeout(240)
    p.fill("#markGram", "16")
    p.fill("#markPx", "4170")
    p.click("#markSave")
    p.wait_for_timeout(360)
    plan_tab(p)
    r = row(p, "第1档")
    assert r["buy"] is True and r["sell"] is False      # 没有可卖的了
    assert r["g"] == "待补 16g"                          # 额度已释放
    assert r["swing"] == "+42 / 1.0 轮"                  # 历史差价仍留着


# ---------------------------------------------------------------- 设置页

def test_u9_swing_grid_defaults_to_1_2(env):
    p = env().page
    open_tab(p, "set")
    on = p.evaluate("() => [...document.querySelectorAll('#setSwingGrid .swg.on')].map(b => b.textContent)")
    assert on == ["1", "2"]


def test_u10_uncheck_gear2_makes_it_build_gear(env):
    p = env().page
    p.evaluate("() => window.__xt.markBuy(2, 24, 4041)")
    open_tab(p, "set")
    p.click('#setSwingGrid [data-sw="2"]')
    p.wait_for_timeout(340)
    assert p.evaluate("() => window.__xt.swingOf()") == [1]
    plan_tab(p)
    r = row(p, "第2档")
    assert r["sell"] is False
    assert r["unmark"] is True


def test_u11_deep_gear_shows_warning(env):
    p = env().page
    open_tab(p, "set")
    p.click('#setSwingGrid [data-sw="3"]')
    p.wait_for_timeout(340)
    txt = p.evaluate("() => document.getElementById('setSwingLive').textContent")
    assert "建仓主力" in txt
    assert p.evaluate("() => document.getElementById('setSwingLive').classList.contains('warn')")


def test_u12_empty_swing_shows_hint(env):
    p = env().page
    open_tab(p, "set")
    p.click('#setSwingGrid [data-sw="1"]')
    p.click('#setSwingGrid [data-sw="2"]')
    p.wait_for_timeout(340)
    assert p.evaluate("() => window.__xt.swingOf()") == []
    assert "已关闭循环档" in p.evaluate("() => document.getElementById('setSwingLive').textContent")


# ---------------------------------------------------------------- 边界

def test_u13_ledger_mode_has_no_mark_sheet(env):
    """账本模式已经有 sells[]，再叠一层会让「卖了几次」有两个答案（P6 §5.4）。"""
    e = env(c=dict(cfg(), planOnly=False))
    p = e.page
    plan_tab(p)
    p.click('#ladder [data-gear="第1档"] .price')
    p.wait_for_timeout(300)
    assert p.evaluate("() => document.getElementById('sheetMark').classList.contains('on')") is False


def test_u14_no_overflow_on_both_widths(env):
    for w in (390, 320):
        e = env(width=w)
        p = e.page
        p.evaluate("() => window.__xt.markBuy(1, 16, 4089)")
        p.evaluate("() => window.__xt.markSell(1, 16, 4170)")
        plan_tab(p)
        assert p.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth"), \
            "%dpx 档位表横向溢出" % w
