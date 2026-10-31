"""P16 通道图的周期窗口：日线 / 4 小时 / 1 小时，窗口按周期分别设

契约：
  · 三个周期 chip；1d 默认
  · 窗口 CH_WIN = {1d:90, 4h:90, 1h:180}
  · 1h 不能用 90 根 —— 只覆盖 4 天，小时级通道展不开；180 根 ≈ 7.5 个交易日
  · 180 根在 308px 绘图区里是 1.71px/根，蜡烛必须仍然画得出来（复检用 .chc 数量）
  · 数据不足（< 40 根）时不抛错，提示「数据不足」
"""
import pytest

from conftest import CFG_EMPTY, _bars


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=4700.0, TOTAL=200)
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    mk_keys = ("price", "width", "height", "gh_on", "reduced_motion", "tz", "calendar", "bars")

    def _f(**kw):
        mk = {}
        for k in list(kw.keys()):
            if k in mk_keys:
                mk[k] = kw.pop(k)
        mk.setdefault("price", 4030.0)
        return make_env(cfg=cfg(**kw), **mk)
    return _f


def open_struct(page):
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(1200)


def chan(page):
    return page.evaluate("""() => {
        const S = window.__xt.state();
        return {
            n: (S.chan || {}).n || 0,
            meta: document.getElementById('chMeta').textContent.trim(),
            chips: [...document.querySelectorAll('#chChips button')]
                     .map(b => ({p: b.getAttribute('data-p'), on: b.className === 'on'})),
            candles: document.querySelectorAll('#chSvg .chc').length,
            line: document.getElementById('chLine').textContent.trim(),
            sw: document.getElementById('chChips').scrollWidth,
            cw: document.getElementById('chChips').clientWidth,
        };
    }""")


def test_c1_three_chips_default_daily(env):
    """三个 chip，默认日线，窗口 90 根。"""
    p = env().page
    open_struct(p)
    c = chan(p)
    assert [x["p"] for x in c["chips"]] == ["1d", "4h", "1h"], c
    assert c["chips"][0]["on"] is True, c
    assert c["n"] == 90, c
    assert "日线 · 90 根" in c["meta"], c
    assert c["candles"] == 90, c


def test_c2_four_hour_keeps_90(env):
    """4 小时仍是 90 根。"""
    p = env().page
    open_struct(p)
    p.click('#chChips button[data-p="4h"]')
    p.wait_for_timeout(500)
    c = chan(p)
    assert c["n"] == 90, c
    assert "4小时 · 90 根" in c["meta"], c
    assert c["candles"] == 90, c


def test_c3_hour_uses_180(env):
    """1 小时用 180 根，不是 90。"""
    p = env().page
    open_struct(p)
    p.click('#chChips button[data-p="1h"]')
    p.wait_for_timeout(500)
    c = chan(p)
    assert c["n"] == 180, c
    assert "1小时 · 180 根" in c["meta"], c
    assert c["candles"] == 180, c


def test_c4_hour_chip_active(env):
    """点过 1 小时之后高亮转移到它，且文案跟着改。"""
    p = env().page
    open_struct(p)
    p.click('#chChips button[data-p="1h"]')
    p.wait_for_timeout(500)
    c = chan(p)
    assert [x["on"] for x in c["chips"]] == [False, False, True], c
    assert c["line"].startswith("1小时"), c


def test_c5_short_data_no_throw(env):
    """只有 30 根时不抛错，给「数据不足」。"""
    errs = []
    p = env(bars=_bars("1h", 30)).page
    p.on("pageerror", lambda e: errs.append(str(e)))
    open_struct(p)
    p.click('#chChips button[data-p="1h"]')
    p.wait_for_timeout(600)
    c = chan(p)
    assert "数据不足" in c["line"], c
    assert c["candles"] == 0, c
    assert errs == [], errs


def test_c6_chips_fit_narrow(env):
    """390 宽下三个 chip 一行放得下，不横向溢出。"""
    p = env(width=390).page
    open_struct(p)
    c = chan(p)
    assert c["sw"] <= c["cw"] + 1, c
