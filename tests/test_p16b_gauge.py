"""P16b 仓位进度卡：当前持仓 / 持仓上限 + 百分比 + 20 段绿橙红进度条

契约：
  · 位置在持仓页最上，`#posBox` 之前
  · 数据只取账本（holdStats().grams），不取档位标记
  · 20 段色块，点亮段数 = round(pct/100*20)，夹在 0..20
  · 百分比 <60 绿 / 60–85 橙 / ≥85 更红 / >100 红
  · 底行「X g / Y g」+「还能买 Z g」/「超上限 Z g」/「刚好到上限」
"""
import pytest

from conftest import CFG_EMPTY

TOTAL = 200.0


def cfg(**kw):
    c = dict(CFG_EMPTY, TOTAL=TOTAL, buys=[], sells=[], H_BASE=4700.0)
    c.update(kw)
    return c


def buys(*grams, price=900.0):
    return [{"id": "b%d" % (i + 1), "name": "买入", "gram": g, "priceCny": price,
             "time": "2026-10-%02dT02:00:00.000Z" % (i + 1), "note": ""}
            for i, g in enumerate(grams)]


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
        mk.setdefault("price", 4030.0)
        return make_env(cfg=base, **mk)
    return _f


def open_pos(page):
    page.click('.tabbar button[data-tab="pos"]')
    page.wait_for_timeout(300)


def gauge(page):
    return page.evaluate("""() => {
        const b = document.querySelector('#posGauge');
        if(!b) return null;
        return {
            text: b.innerText.replace(/\\s+/g, ' ').trim(),
            segs: b.querySelectorAll('.gbar i').length,
            on: b.querySelectorAll('.gbar i.on').length,
            pctCls: (b.querySelector('.gg-h b') || {}).className || '',
            order: [...document.querySelectorAll('#panel-pos .card')].map(x => x.id),
            sw: b.scrollWidth, cw: b.clientWidth,
        };
    }""")


# ---------------------------------------------------------------- 百分比与段数

def test_b1_empty(env):
    """没有记录：0.0%，20 段全暗。"""
    p = env().page
    open_pos(p)
    g = gauge(p)
    assert "0.0%" in g["text"], g
    assert g["segs"] == 20, g
    assert g["on"] == 0, g
    assert "0 g / 200 g" in g["text"], g


def test_b2_half(env):
    """100 / 200 → 50.0%，亮 10 段。"""
    p = env(buys=buys(100)).page
    open_pos(p)
    g = gauge(p)
    assert "50.0%" in g["text"], g
    assert g["on"] == 10, g
    assert "还能买 100 g" in g["text"], g


def test_b3_full(env):
    """200 / 200 → 100.0%，20 段全亮。"""
    p = env(buys=buys(120, 80)).page
    open_pos(p)
    g = gauge(p)
    assert "100.0%" in g["text"], g
    assert g["on"] == 20, g
    assert "刚好到上限" in g["text"], g


def test_b4_over(env):
    """250 / 200 → 125.0%，全亮，底行报超上限。"""
    p = env(buys=buys(150, 100)).page
    open_pos(p)
    g = gauge(p)
    assert "125.0%" in g["text"], g
    assert g["on"] == 20, g
    assert "超上限 50 g" in g["text"], g
    assert "over" in g["pctCls"], g


def test_b5_follows_limit(env):
    """同一持仓，上限改成 400 → 25.0%，亮 5 段。"""
    p = env(buys=buys(100), TOTAL=400).page
    open_pos(p)
    g = gauge(p)
    assert "25.0%" in g["text"], g
    assert g["on"] == 5, g


# ---------------------------------------------------------------- 位置与适配

def test_b6_before_posbox(env):
    """卡片排在持仓汇总之前（先看几成仓，再看盈亏）。"""
    p = env(buys=buys(100)).page
    open_pos(p)
    order = gauge(p)["order"]
    assert order[:2] == ["posGauge", "posBox"], order


def test_b7_empty_does_not_throw(env):
    """无记录时持仓汇总卡走空状态，进度卡照样渲染。"""
    errs = []
    p = env().page
    p.on("pageerror", lambda e: errs.append(str(e)))
    open_pos(p)
    g = gauge(p)
    assert g is not None and g["segs"] == 20, g
    assert errs == [], errs


def test_b8_narrow_no_overflow(env):
    """390 宽下不横向溢出，百分比完整可见。"""
    p = env(buys=buys(125), width=390).page
    open_pos(p)
    g = gauge(p)
    assert g["sw"] <= g["cw"] + 1, g
    assert "62.5%" in g["text"], g
