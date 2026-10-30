"""v6.10 行情标签（今开/昨收/涨跌/涨跌幅）与四周期标签不换行。

口径按参考行情软件：
    涨跌 = 现价 - 昨收
    涨跌幅 = 涨跌 / 昨收
    今开 / 最高 / 最低 取当日（UTC 日线）那一根，不是滚动 24 小时
"""
import pytest

from conftest import PRICE


def money(v):
    return "%.2f" % v


def hero(page):
    return page.evaluate("""() => {
      const st = window.__xt.dayStats();
      const g = id => { const e = document.getElementById(id);
                        return { t: e.textContent.trim(), c: e.className }; };
      return { st: st, p: window.__xt.state().price, pc: window.__xt.state().prevClose,
               open: g('pOpen'), prev: g('pPrev'), high: g('pHigh'), low: g('pLow'),
               amp: g('pAmp'), delta: g('pDelta') };
    }""")


def cls_of(v, ref):
    return "" if ref is None or v is None or v == ref else ("up" if v > ref else "dn")


# ---------- 口径 ----------

def test_l1_open_and_prev_from_daily_bar(make_env):
    env = make_env()
    st = hero(env.page)["st"]
    assert st["open"] and st["prev"] and st["high"] and st["low"]
    assert st["high"] >= st["open"] and st["low"] <= st["open"]


def test_l2_labels_match_daily_bar(make_env):
    env = make_env()
    page = env.page
    h = hero(page)
    assert h["open"]["t"] == money(h["st"]["open"]), h
    assert h["prev"]["t"] == money(h["st"]["prev"]), h
    assert h["high"]["t"] == money(h["st"]["high"]), h
    assert h["low"]["t"] == money(h["st"]["low"]), h


def test_l3_delta_and_pct_use_prev_close(make_env):
    env = make_env()
    page = env.page
    h = hero(page)
    prev = h["st"]["prev"]
    # prevClose 与 dayStats 的昨收必须同源，否则涨跌幅和「昨收」标签会对不上
    assert abs(h["pc"] - prev) < 1e-6, (h["pc"], prev)
    diff = h["p"] - prev
    pct = diff / prev * 100
    want = "{}{} ({}{:.2f}%)".format("+" if diff > 0 else "", money(diff),
                                    "+" if diff > 0 else "", pct)
    assert h["delta"]["t"] == want, (h["delta"]["t"], want)


def test_l4_amplitude_uses_prev_close(make_env):
    env = make_env()
    h = hero(env.page)
    st = h["st"]
    want = (st["high"] - st["low"]) / st["prev"] * 100
    assert h["amp"]["t"] == "%.2f%%" % want, (h["amp"]["t"], want)


def test_l5_ohl_colored_against_prev_close(make_env):
    env = make_env()
    h = hero(env.page)
    st = h["st"]
    for key, v in (("open", st["open"]), ("high", st["high"]), ("low", st["low"])):
        assert h[key]["c"] == ("num " + cls_of(v, st["prev"])).strip(), (key, h[key], st["prev"])


def test_l6_delta_color_follows_price(make_env):
    env = make_env()
    page = env.page
    h = hero(page)
    prev = h["st"]["prev"]
    page.evaluate("(v) => window.__xt.setPrice(v)", prev + 9)
    page.wait_for_timeout(150)
    assert "up" in hero(page)["delta"]["c"]
    page.evaluate("(v) => window.__xt.setPrice(v)", prev - 9)
    page.wait_for_timeout(150)
    assert "dn" in hero(page)["delta"]["c"]


# ---------- 四周期标签不换行 ----------

@pytest.mark.parametrize("w", [320, 360, 390, 430])
def test_l7_period_tags_single_line(make_env, w):
    env = make_env(width=w, height=800)
    page = env.page
    r = page.evaluate("""() => {
      const els = [...document.querySelectorAll('#vTags .vtag')];
      return {
        n: els.length,
        tops: els.map(e => Math.round(e.getBoundingClientRect().top)),
        over: els.map(e => e.scrollWidth - e.clientWidth),
        txt: els.map(e => e.textContent.trim()),
        rowW: Math.round(document.querySelector('#vTags').getBoundingClientRect().width),
        sw: document.documentElement.scrollWidth, iw: innerWidth
      };
    }""")
    assert r["n"] == 4, r
    # 四个标签同一行：top 只有一个值
    assert len(set(r["tops"])) == 1, r
    # 每个标签文字完整，没有被省略号截断
    assert all(o <= 1 for o in r["over"]), r
    for name in ("周线", "日线", "4小时", "1小时"):
        assert any(t.startswith(name) for t in r["txt"]), (name, r["txt"])
    assert r["sw"] <= r["iw"], r


def test_l8_period_tags_update_not_wrap(make_env):
    """切换结构时标签会重渲染，重渲染后仍然一行。"""
    env = make_env()
    page = env.page
    page.evaluate("() => window.__xt.refreshAll()")
    page.wait_for_timeout(200)
    tops = page.eval_on_selector_all("#vTags .vtag",
                                     "els => els.map(e => Math.round(e.getBoundingClientRect().top))")
    assert len(set(tops)) == 1, tops


# ---------- 指标区排布 ----------

@pytest.mark.parametrize("w", [320, 390])
def test_l9_meta_grid_two_rows_no_overflow(make_env, w):
    env = make_env(width=w, height=800)
    page = env.page
    r = page.evaluate("""() => {
      const box = document.querySelector('.hmeta');
      const els = [...box.children];
      return {
        n: els.length,
        rows: [...new Set(els.map(e => Math.round(e.getBoundingClientRect().top)))].length,
        over: els.map(e => e.scrollWidth - e.clientWidth),
        labels: els.map(e => e.textContent.trim().split(/\\s/)[0]),
        sw: document.documentElement.scrollWidth, iw: innerWidth
      };
    }""")
    assert r["n"] == 6, r
    assert r["rows"] == 2, r
    assert r["labels"] == ["今开", "昨收", "振幅", "最高", "最低", "ATR14"], r
    assert all(o <= 1 for o in r["over"]), r
    assert r["sw"] <= r["iw"], r
