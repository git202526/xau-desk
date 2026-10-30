"""P3-1（v6.6）回归通道：算法正确性、SVG 结构、边界降级、与既有计算零耦合。"""
import math

import pytest

DAY = 86400000
T0 = 1761696000000


def series(slope=0.0, n=90, noise=0.0, base=4000.0, half=2.0):
    """确定性 OHLC：线性斜率 + 正弦噪声。"""
    out = []
    for i in range(n):
        c = base + slope * i + noise * math.sin(i * 1.7)
        out.append({"t": T0 + i * DAY, "o": c, "h": c + half,
                    "l": c - half, "c": c, "v": 10})
    return out


def ch(page, bars, price=None):
    return page.evaluate("""([bars, price]) => {
      const r = window.__xt.channelOf(bars, 90, price);
      if (!r) return null;
      const o = {};
      for (const k in r) { if (k !== 'bars') o[k] = r[k]; }
      return o;
    }""", [bars, price])


@pytest.fixture
def page(make_env):
    env = make_env()
    env.page.click('.tabbar button[data-tab="struct"]')
    env.page.wait_for_timeout(250)
    return env.page


def test_ch1_uptrend_is_up(page):
    r = ch(page, series(slope=0.8, noise=0.2))
    assert r is not None
    assert r["dir"] == "up", r
    assert r["b"] > 0
    assert r["slopeAtr"] > 0.015


def test_ch2_downtrend_is_down(page):
    r = ch(page, series(slope=-0.8, noise=0.2))
    assert r["dir"] == "down", r
    assert r["b"] < 0


def test_ch3_flat_is_flat(page):
    r = ch(page, series(slope=0.0, noise=0.3))
    assert r["dir"] == "flat", r
    assert abs(r["slopeAtr"]) < 0.015


def test_ch4_channel_contains_bars(page):
    bars = series(slope=0.8, noise=0.2)
    r = ch(page, bars)
    # 通道是斜的：每根要跟「它自己那一根」的轨比，不能跟现价处的水平线比
    upOff, dnOff = r["up"] - r["mid"], r["dn"] - r["mid"]
    inside_u = inside_l = inside_both = 0
    for i, b in enumerate(bars):
        u = r["a"] + r["b"] * i + upOff
        l = r["a"] + r["b"] * i + dnOff
        iu, il = b["h"] <= u + 1e-6, b["l"] >= l - 1e-6
        inside_u += iu
        inside_l += il
        inside_both += (iu and il)
    n = len(bars)
    assert inside_u / n >= 0.88, inside_u
    assert inside_l / n >= 0.88, inside_l
    assert inside_both / n >= 0.78, inside_both


def test_ch5_position_matches_formula(page):
    bars = series(slope=0.8, noise=0.2)
    last = bars[-1]["c"]
    r = ch(page, bars, last)
    expect = (last - r["dn"]) / (r["up"] - r["dn"])
    assert abs(r["pos"] - expect) < 0.001, (r["pos"], expect)
    assert 0.0 <= r["pos"] <= 1.0
    # 贴中轨的价位应当落在 0.5 附近
    mid = (r["up"] + r["dn"]) / 2
    r2 = ch(page, bars, mid)
    assert abs(r2["pos"] - 0.5) < 0.02, r2["pos"]


def test_ch6_min_half_width(page):
    bars = series(slope=0.0, noise=0.0, half=0.05)
    r = ch(page, bars)
    assert r is not None
    assert r["widthAtr"] >= 1.19, r["widthAtr"]
    assert r["up"] - r["dn"] >= 1.19 * r["atr"]


def test_ch7_deterministic(page):
    bars = series(slope=0.5, noise=0.4)
    a = ch(page, bars)
    b = ch(page, bars)
    for k in ("up", "dn", "mid", "b", "a", "slopeAtr", "widthAtr", "r2"):
        assert a[k] == b[k], k


def test_ch8_short_data_degrades(page):
    small = series(slope=0.3, n=30)
    assert ch(page, small) is None
    page.evaluate("""(small) => {
      const S = window.__xt.state();
      S.kl['1d'] = small.map(b => Object.assign({}, b));
      window.__xt.renderChart();
    }""", small)
    page.wait_for_timeout(120)
    line = page.eval_on_selector("#chLine", "e => e.textContent")
    assert "数据不足" in line, line
    assert page.eval_on_selector("#chSvg", "e => e.innerHTML.length") == 0


def test_ch9_svg_structure_and_width(page):
    assert page.eval_on_selector_all("#chSvg g.chc", "els => els.length") == 90
    for sel in (".chband", ".chup", ".chmid", ".chdn", ".chnow"):
        assert page.eval_on_selector("#chSvg " + sel, "e => !!e") is True, sel
    assert page.eval_on_selector("#chMeta", "e => e.textContent").startswith("日线")
    line = page.eval_on_selector("#chLine", "e => e.textContent")
    assert "通道" in line and "% 位" in line, line

    # 320px 宽：图不撑破容器，页面无横向溢出
    page.set_viewport_size({"width": 320, "height": 780})
    page.wait_for_timeout(150)
    over = page.evaluate("""() => {
      const svg = document.getElementById('chSvg');
      const card = document.querySelector('.chcard');
      return {
        sw: document.documentElement.scrollWidth,
        iw: window.innerWidth,
        svgW: svg.getBoundingClientRect().width,
        cardW: card.getBoundingClientRect().width,
      };
    }""")
    assert over["sw"] <= over["iw"] + 1, over
    assert over["svgW"] <= over["cardW"] + 1, over


def test_ch10_does_not_touch_levels_or_takes(page):
    src = open(__file__.replace("tests/test_p31_channel.py", "index.html"),
               encoding="utf-8").read()
    import re
    body = re.search(r"function buildLevels\([^)]*\)\{(.*?)\n\}", src, re.S).group(1)
    assert "channelOf" not in body, "buildLevels 不得依赖通道计算"
    body2 = re.search(r"function computeTakes\([^)]*\)\{(.*?)\n\}", src, re.S).group(1)
    assert "channelOf" not in body2, "computeTakes 不得依赖通道计算"

    same = page.evaluate("""() => {
      const S = window.__xt.state();
      const before = JSON.stringify({l: S.levels, t: S.takes});
      window.__xt.renderChart();
      window.__xt.renderChart();
      const after = JSON.stringify({l: S.levels, t: S.takes});
      const fresh = JSON.stringify(window.__xt.buildLevels(12));
      return {same: before === after, levels: fresh === JSON.stringify(S.levels)};
    }""")
    assert same["same"] is True
    assert same["levels"] is True


def test_ch11_period_switch(page):
    stamp = page.evaluate("() => window.__xt.state().cfg.updatedAt || 0")
    before = page.eval_on_selector_all("#chSvg g.chc", "els => els.length")
    page.click('#chChips button[data-p="4h"]')
    page.wait_for_timeout(200)
    meta = page.eval_on_selector("#chMeta", "e => e.textContent")
    assert meta.startswith("4小时"), meta
    assert page.eval_on_selector('#chChips button[data-p="4h"]',
                                 "e => e.classList.contains('on')") is True
    after = page.eval_on_selector_all("#chSvg g.chc", "els => els.length")
    assert before == 90 and after == 90
    # 切回日线
    page.click('#chChips button[data-p="1d"]')
    page.wait_for_timeout(150)
    assert page.eval_on_selector("#chMeta", "e => e.textContent").startswith("日线")
    # 只改内存，不写云端
    assert page.evaluate("() => window.__xt.state().cfg.updatedAt || 0") == stamp
