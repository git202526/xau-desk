"""P9-4 档位表折叠 · 当前档 ±4。

验：6 档不折叠（9 个以内全显示）、10 档默认折叠、点开/收起、切换档数复位、
价格跌破深处时窗口跟着下移、320px 不溢出。

口径：α 模式、H=4700、d=48 → 10 档 4089 / 4041 / … / 3657。
默认现价 4072.4 → 只跌破第 1 档，当前档 = 2 → 窗口 1~6。
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


def to_plan(p):
    p.evaluate("() => window.scrollTo(0, 0)")
    p.wait_for_timeout(140)
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(480)


def names(p):
    return p.evaluate("""() => [...document.querySelectorAll('#ladder .lv .nm')]
        .map(x => x.textContent.trim()).filter(x => x && x.startsWith('第'))""")


def set_count(p, n):
    p.evaluate("(v) => window.__xt.setGearCount(v)", n)
    p.wait_for_timeout(400)


# ---------- 6 档不折叠 ----------

def test_f1_six_not_folded(env):
    """6 档 ≤ 9，全部显示，没有折叠条。"""
    p = env().page
    to_plan(p)
    assert p.query_selector("#ladder [data-fold]") is None
    assert len(names(p)) == 6


def test_f2_window_six_is_full(env):
    p = env().page
    w = p.evaluate("() => window.__xt.ladderWindow()")
    assert w["folded"] is False and w["from"] == 1 and w["to"] == 6


# ---------- 10 档折叠 ----------

def test_f3_ten_folds(env):
    """10 档、当前档 2 → 窗口 1~6，隐藏 7~10。"""
    p = env().page
    set_count(p, 10)
    to_plan(p)
    w = p.evaluate("() => window.__xt.ladderWindow()")
    assert w["folded"] is True
    assert (w["from"], w["to"], w["cur"]) == (1, 6, 2)
    assert names(p) == ["第1档", "第2档", "第3档", "第4档", "第5档", "第6档"]
    assert p.query_selector("#ladder [data-fold]") is not None


def test_f4_fold_bar_text(env):
    """折叠条文案写明被隐藏的档号范围 7–10。"""
    p = env().page
    set_count(p, 10)
    to_plan(p)
    txt = p.inner_text("#ladder [data-fold]")
    assert "7–10" in txt or "第 7–10 档" in txt


def test_f5_expand_and_collapse(env):
    """点折叠条展开到 10 行；点「收起」回到 6 行。"""
    p = env().page
    set_count(p, 10)
    to_plan(p)
    p.click("#ladder [data-fold]")
    p.wait_for_timeout(400)
    assert len(names(p)) == 10
    p.click('#ladder [data-fold="off"]')
    p.wait_for_timeout(400)
    assert len(names(p)) == 6


def test_f6_deep_price_moves_window(env):
    """价格跌到 3600（跌破全部 10 档）→ 窗口下移到 6~10，第 1 档被折叠。"""
    p = env(price=3600.0).page
    set_count(p, 10)
    to_plan(p)
    w = p.evaluate("() => window.__xt.ladderWindow()")
    assert w["cur"] == 10
    assert (w["from"], w["to"]) == (6, 10)
    got = names(p)
    assert "第10档" in got
    assert "第1档" not in got


def test_f7_count_switch_resets_expand(env):
    """展开后切档数 → 自动收起（10→8 时窗口按新档数重算）。"""
    p = env().page
    set_count(p, 10)
    to_plan(p)
    p.click("#ladder [data-fold]")
    p.wait_for_timeout(380)
    assert p.evaluate("() => !!window.__xt.state().ladderExpand") is True
    set_count(p, 6)
    assert p.evaluate("() => !!window.__xt.state().ladderExpand") is False


def test_f8_no_overflow_320(env):
    """320px 下折叠条不产生横向溢出。"""
    p = env(width=320).page
    set_count(p, 10)
    to_plan(p)
    assert p.query_selector("#ladder [data-fold]") is not None
    assert p.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth") is True
