"""P11 档位分布合计：百分比是相对权重，超 100% 要看得见、能一键归一。

用户场景：10 档，把后四格都改成 50，合计 258%，界面只给一行「归一化 → …g」，
看不出已经超了 158%，也不知道该不该手动凑回 100。

口径：
  · 输入值 = 相对权重，合计不必等于 100（引擎本来就按 Σw 折算）
  · 但界面必须显示「合计 X%（超/缺 Y%）」，超了标橙
  · 「归一化到 100%」按比例缩放、最大余数法取整，保证整数且恰好 100
"""
import pytest

from conftest import CFG_EMPTY, PRICE

TOTAL = 200.0


def bars_calm(n=240, px=PRICE, amp=24.0):
    import math
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
    c = dict(CFG_EMPTY, H_BASE=4700, TOTAL=TOTAL, planOnly=True,
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
        mk.setdefault("bars", BARS)
        return make_env(cfg=base, **mk)
    return _f


def open_set(page):
    page.click('.tabbar button[data-tab="set"]')
    page.wait_for_timeout(320)


def wlive(page):
    return page.evaluate("() => document.getElementById('setWLive').textContent")


def wcls(page):
    return page.evaluate("() => document.getElementById('setWLive').className")


def wsum(page):
    return page.evaluate("() => window.__xt.curW().reduce((a, b) => a + Number(b || 0), 0)")


# ---------------------------------------------------------------- 合计提示

def test_w1_default_shows_100(env):
    p = env().page
    open_set(p)
    t = wlive(p)
    assert "合计 100%" in t, t
    assert "✓" in t, t
    assert "→ " in t, "归一化预览必须保留" + t
    assert "warn" not in wcls(p)


def test_w2_over_100_flags_immediately(env):
    """输入框还在编辑（没 change）就得看到「超了多少」。"""
    p = env().page
    open_set(p)
    p.fill("#setWGrid [data-w='0']", "50")   # 8→50，合计 142
    t = wlive(p)
    assert "合计 142%" in t, t
    assert "超 42%" in t, t
    assert "warn" in wcls(p), wcls(p)


def test_w3_under_100_flags(env):
    p = env().page
    open_set(p)
    p.fill("#setWGrid [data-w='0']", "0")    # 8→0，合计 92
    t = wlive(p)
    assert "合计 92%" in t, t
    assert "缺 8%" in t, t
    assert "warn" in wcls(p)


def test_w4_commit_keeps_relative_weights(env):
    """落盘不改数值：超过 100 也是相对权重，引擎按 Σw 折算。"""
    p = env().page
    open_set(p)
    p.fill("#setWGrid [data-w='0']", "50")
    p.dispatch_event("#setWGrid [data-w='0']", "change")
    p.wait_for_timeout(320)
    assert p.evaluate("() => window.__xt.state().cfg.wPct") == [50, 12, 24, 36, 12, 8]
    # 计划克重仍等于总仓（归一化在 planBase 里做）
    plan = p.evaluate("() => window.__xt.planBase()")
    assert sum(plan) == pytest.approx(TOTAL, abs=0.01), plan
    assert "合计 142%" in wlive(p)


# ---------------------------------------------------------------- 一键归一化

def test_w5_normalize_lands_exactly_100(env):
    p = env().page
    open_set(p)
    p.fill("#setWGrid [data-w='0']", "50")
    p.dispatch_event("#setWGrid [data-w='0']", "change")
    p.wait_for_timeout(320)
    p.click("#btnWNorm")
    p.wait_for_timeout(350)
    w = p.evaluate("() => window.__xt.curW()")
    assert sum(w) == 100, w
    assert all(isinstance(x, int) or float(x).is_integer() for x in w), w
    assert w[0] == max(w), "最大的那档归一化后仍应最大" + str(w)
    assert "合计 100%" in wlive(p), wlive(p)


def test_w6_normalize_fixes_ten_gears(env):
    """10 档、后四格 50（合计 258%）→ 一键回到 100，量级关系不变。"""
    p = env(cfg(gearCount=10, wPct=[4, 5, 8, 11, 14, 16, 17, 13, 8, 4])).page
    open_set(p)
    for i in range(6, 10):
        p.fill("#setWGrid [data-w='%d']" % i, "50")
    p.dispatch_event("#setWGrid [data-w='9']", "change")
    p.wait_for_timeout(320)
    assert "合计 258%" in wlive(p), wlive(p)

    p.click("#btnWNorm")
    p.wait_for_timeout(400)
    w = p.evaluate("() => window.__xt.curW()")
    assert len(w) == 10 and sum(w) == 100, w
    assert w[9] >= 18, "50 在 258 里占 19.4%，取整后不应缩水太多" + str(w)
    assert w[0] <= 3, "4 在 258 里占 1.6%，应落成 1-2" + str(w)
    assert "合计 100%" in wlive(p)


def test_w7_normalize_at_100_is_noop(env):
    p = env().page
    open_set(p)
    before = p.evaluate("() => window.__xt.curW()")
    p.evaluate("() => window.__xt.normalizeW()")
    p.wait_for_timeout(250)
    assert p.evaluate("() => window.__xt.curW()") == before


def test_w8_plan_still_sums_to_total_after_normalize(env):
    p = env().page
    open_set(p)
    p.fill("#setWGrid [data-w='0']", "90")
    p.dispatch_event("#setWGrid [data-w='0']", "change")
    p.wait_for_timeout(300)
    p.click("#btnWNorm")
    p.wait_for_timeout(350)
    plan = p.evaluate("() => window.__xt.planBase()")
    assert sum(plan) == pytest.approx(TOTAL, abs=0.01), plan
    assert len(plan) == 6


# ---------------------------------------------------------------- 排版

@pytest.mark.parametrize("width", [320, 390])
def test_w9_no_overflow(env, width):
    p = env(width=width).page
    open_set(p)
    p.fill("#setWGrid [data-w='0']", "50")
    p.wait_for_timeout(200)
    info = p.evaluate("""() => ({
        sw: document.documentElement.scrollWidth, iw: innerWidth,
        row: (() => { const r = document.getElementById('btnWNorm').parentElement;
                      return r.scrollWidth - r.clientWidth; })(),
    })""")
    assert info["sw"] <= info["iw"] + 1, info
    assert info["row"] <= 1, info
