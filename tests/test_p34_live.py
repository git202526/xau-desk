"""v6.9 报价/读秒/顶栏对齐 的回归用例。

覆盖：
  1. 读秒每秒连续递增（不再 5 秒一跳）
  2. 读秒与「最后一次成功取价」的真实年龄一致
  3. 单次取数失败不再误标「滞后 0m」
  4. 顶栏三块（报价 / 读秒 / 载入时间）等高、同右边界、报价与读秒同行
  5. 320px 窄屏不横向溢出，且读秒自动省略文字只留秒数
  6. 现价桥接：锚点 + PAXG 位移，两次现货价之间报价仍会动
  7. 桥接拒绝异常跳变（>0.5%）
  8. 涨跌幅以昨收为基准，而不是上一次轮询价
"""
import json
import re
import time

import pytest

from conftest import PRICE, BASE


def txt(page, sel):
    return page.eval_on_selector(sel, "e => e.textContent.trim()")


def secs(s):
    m = re.search(r"(\d+)s$", s)
    assert m, s
    return int(m.group(1))


def freeze_spot(page):
    """把现货源断掉：轮询失败不会刷新 lastOk，读秒才能稳定观察。"""
    page.route("**/api.gold-api.com/**", lambda r: r.abort())


def wait_secs(page, lo, timeout_ms=4000):
    """等到读秒真正重绘到 lo 秒以上再取样。

    固定 sleep 一段再读，在整包并跑、CPU 被抢时会读到「上一帧」（页面刚
    boot 时渲染的 0s），增量就会凭空多出两秒。这里等到 DOM 与新状态一致为止。
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    last = None
    while time.monotonic() < deadline:
        last = secs(txt(page, "#connTxt"))
        if last >= lo:
            return last
        page.wait_for_timeout(50)
    raise AssertionError("读秒没有涨到 %d 秒，最后读到 %r" % (lo, last))


# ---------- 读秒 ----------

def test_r1_seconds_tick_every_second(make_env):
    env = make_env()
    page = env.page
    freeze_spot(page)
    page.evaluate("() => { const S = window.__xt.state(); S.lastOk = Date.now() - 2000; S.fail = 1; }")
    a = wait_secs(page, 1)
    t0 = time.monotonic()
    page.wait_for_timeout(2000)
    b = secs(txt(page, "#connTxt"))
    dt = time.monotonic() - t0
    # 读秒每 1 秒一跳（旧实现 5 秒才刷新一次，b == a）；
    # 增量还要与真实流逝时间对齐，误差不超过取整 + 一次调度抖动
    assert b > a, (a, b)
    assert abs((b - a) - dt) <= 1.6, (a, b, round(dt, 2))


def test_r2_seconds_match_real_age(make_env):
    env = make_env()
    page = env.page
    freeze_spot(page)
    page.evaluate("() => { const S = window.__xt.state(); S.lastOk = Date.now() - 7000; S.fail = 0; }")
    page.wait_for_timeout(1100)
    v = secs(txt(page, "#connTxt"))
    assert 6 <= v <= 10, v
    assert txt(page, "#connLb") == "实时"
    assert "live" in page.eval_on_selector("#connDot", "e => e.className")


def test_r3_single_failure_keeps_live_label(make_env):
    """一次取数失败不应把状态打成「滞后 0m」——这是旧实现对 fail 的误用。"""
    env = make_env()
    page = env.page
    page.evaluate("() => { const S = window.__xt.state(); S.lastOk = Date.now() - 4000; S.fail = 1; }")
    page.wait_for_timeout(1100)
    body = txt(page, "#connTxt")
    assert "m" not in body or body.endswith("s"), body
    assert txt(page, "#connLb") == "实时", txt(page, "#connLb")
    assert "stale" not in page.eval_on_selector("#connDot", "e => e.className")


def test_r4_long_silence_degrades(make_env):
    """真的很久没取到价，才降级为滞后 / 中断。"""
    env = make_env()
    page = env.page
    page.evaluate("() => { const S = window.__xt.state(); S.lastOk = Date.now() - 200000; }")
    page.wait_for_timeout(1100)
    assert txt(page, "#connLb") == "滞后"
    assert txt(page, "#connTxt").endswith("m")
    page.evaluate("() => { const S = window.__xt.state(); S.lastOk = Date.now() - 900000; }")
    page.wait_for_timeout(1100)
    assert txt(page, "#connTxt") == "中断"
    assert "down" in page.eval_on_selector("#connDot", "e => e.className")


# ---------- 顶栏对齐 ----------

def _rects(page):
    return page.evaluate("""() => {
      const r = s => { const e = document.querySelector(s); const b = e.getBoundingClientRect();
                       return {t:b.top, b:b.bottom, l:b.left, r:b.right, w:b.width, h:b.height,
                               d:getComputedStyle(e).display}; };
      return { tpx:r('#tpx'), conn:r('#conn'), sync:r('#syncBar'),
               bar:r('.topbar'), sw:document.documentElement.scrollWidth, iw:window.innerWidth };
    }""")


@pytest.mark.parametrize("w", [320, 390, 430])
def test_r5_header_alignment(make_env, w):
    env = make_env(width=w, height=800)
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(250)
    g = _rects(page)
    # 报价与读秒同一行、同高
    assert abs(g["tpx"]["t"] - g["conn"]["t"]) <= 1, g
    assert abs(g["tpx"]["h"] - g["conn"]["h"]) <= 1, g
    # 两行右边界对齐（载入时间不缩进）
    assert abs(g["conn"]["r"] - g["sync"]["r"]) <= 1, g
    # 三项都不越界
    assert g["conn"]["r"] <= w - 8, g
    assert g["sync"]["r"] <= w - 8, g
    assert g["tpx"]["l"] >= 0 and g["tpx"]["r"] <= w, g
    assert g["sw"] <= g["iw"], g


def test_r6_narrow_hides_label(make_env):
    env = make_env(width=320, height=800)
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(250)
    assert page.eval_on_selector("#connLb", "e => getComputedStyle(e).display") == "none"
    # 报价本身不能被截断
    assert page.eval_on_selector("#tpx", "e => e.scrollWidth <= e.clientWidth + 1") is True
    # 读秒数字仍完整可见
    assert page.eval_on_selector("#connTxt", "e => e.scrollWidth <= e.clientWidth + 1") is True


# ---------- 现价桥接 ----------

def _price(page):
    return page.evaluate("() => window.__xt.state().price")


def _ticker(page, price):
    page.route("**/ticker/price**", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"symbol": "PAXGUSDT", "price": "%.2f" % price})))


def test_r7_tick_bridges_between_spot(make_env):
    env = make_env()
    page = env.page
    assert abs(_price(page) - PRICE) < 1e-6

    # 第一次逐笔：只补锚点，不改价
    _ticker(page, 4050.00)
    page.evaluate("() => window.__xt.refreshTick()")
    page.wait_for_timeout(400)
    assert abs(_price(page) - PRICE) < 1e-6

    # 代币涨 3 美元：显示价按同样位移跟涨，溢价不被算进去
    _ticker(page, 4053.00)
    page.evaluate("() => window.__xt.refreshTick()")
    page.wait_for_timeout(400)
    assert abs(_price(page) - (PRICE + 3)) < 0.011, _price(page)
    assert abs(float(txt(page, "#pNow").replace(",", "")) - (PRICE + 3)) < 0.011


def test_r8_tick_rejects_jump(make_env):
    env = make_env()
    page = env.page
    _ticker(page, 4050.00)
    page.evaluate("() => window.__xt.refreshTick()")
    page.wait_for_timeout(350)
    _ticker(page, 4053.00)
    page.evaluate("() => window.__xt.refreshTick()")
    page.wait_for_timeout(350)
    base = _price(page)
    # 突然蹦 3%：不跟，价格保持
    _ticker(page, 4170.00)
    page.evaluate("() => window.__xt.refreshTick()")
    page.wait_for_timeout(350)
    assert abs(_price(page) - base) < 1e-6


def test_r9_tick_does_not_touch_spot_anchor(make_env):
    """桥接只改显示价，不改锚点；下一次现货刷新后回到现货价。"""
    env = make_env()
    page = env.page
    _ticker(page, 4050.00)
    page.evaluate("() => window.__xt.refreshTick()")
    page.wait_for_timeout(300)
    a1 = page.evaluate("() => window.__xt.state().anchor")
    assert a1 and abs(a1["spot"] - PRICE) < 1e-6
    page.evaluate("() => window.__xt.refreshPrice()")
    page.wait_for_timeout(600)
    assert abs(_price(page) - PRICE) < 1e-6


# ---------- 涨跌基准 ----------

def test_r10_delta_uses_prev_close(make_env):
    env = make_env()
    page = env.page
    pc = page.evaluate("() => window.__xt.prevDayClose()")
    assert pc and pc > 0
    d = page.evaluate("""() => {
      const t = document.querySelector('#pDelta').textContent.replace(/[^0-9.\\-]/g, '');
      return parseFloat(t);
    }""")
    assert abs(d - (PRICE - pc)) < 0.02, (d, PRICE - pc)


def test_r11_delta_is_ratio_based_not_tick_based(make_env):
    """涨跌幅口径：现价 / 昨收 - 1，不是「现价 / 上一次轮询价 - 1」。

    昨收跟着缩放系数走，所以这里校验百分比，而不是绝对值：
    跳价 1 美元：若基准错当成上一口，百分比会是 1/4082≈0.02%；
    按昨收算则是当日涨跌（mock 数据约 0.19%），两者差一个数量级。
    """
    env = make_env()
    page = env.page
    page.evaluate("(p) => window.__xt.tick(p)", PRICE + 1)
    page.wait_for_timeout(200)
    got = page.evaluate("""() => {
      const t = document.querySelector('#pDelta').textContent;
      const m = t.match(/\\(([+-]?[\\d.]+)%\\)/);
      return m ? parseFloat(m[1]) : null;
    }""")
    st = page.evaluate("() => ({p: window.__xt.state().price, pc: window.__xt.state().prevClose})")
    want = (st["p"] / st["pc"] - 1) * 100
    assert got is not None
    assert abs(got - want) < 0.02, (got, want)
    tick_pct = 1 / (PRICE + 1) * 100
    assert abs(got - tick_pct) > 0.05, (got, tick_pct)   # 不是「上一口」口径
