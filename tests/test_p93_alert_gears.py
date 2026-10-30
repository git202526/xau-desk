"""P9-3 预警档位可选 · cfg.alertGears 与到价提示过滤。

验：默认全档、可只开部分、可全关（合法）、垃圾值回全开、越界档号被裁、
改档数后自动收缩、trackChanges 只对勾选档位落 gearHit。

口径：H=4700、d=48 → 4089 / 4041 / 3993 / 3945 / 3897 / 3849。
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


def set_px(p, px):
    p.evaluate("(v) => { window.__xt.state().price = v; window.__xt.compute(); }", px)
    p.wait_for_timeout(120)


def hits(p):
    return p.evaluate("() => Object.keys(window.__xt.state().cfg.gearHit || {})")


# ---------- 清洗与判定 ----------

def test_a1_default_all(env):
    """不配 alertGears → 全部档位都预警。"""
    p = env().page
    assert p.evaluate("() => window.__xt.alertGearsOf()") == [1, 2, 3, 4, 5, 6]
    assert p.evaluate("() => window.__xt.alertOn(4)") is True


def test_a2_partial(env):
    """只开 3、4 → 其它档不预警。"""
    p = env().page
    p.evaluate("() => window.__xt.setAlertGears([3, 4])")
    assert p.evaluate("() => window.__xt.alertGearsOf()") == [3, 4]
    assert p.evaluate("() => window.__xt.alertOn(3)") is True
    assert p.evaluate("() => window.__xt.alertOn(1)") is False


def test_a3_all_off_is_legal(env):
    """空数组 = 全关，是合法意图，不回落全开。"""
    p = env().page
    p.evaluate("() => window.__xt.setAlertGears([])")
    assert p.evaluate("() => window.__xt.alertGearsOf()") == []
    assert p.evaluate("() => window.__xt.alertOn(1)") is False


def test_a4_garbage_back_to_all(env):
    """全是垃圾值 → 回全部，不能悄悄关掉功能。"""
    p = env().page
    p.evaluate("() => window.__xt.setAlertGears(['x', -3, 99])")
    assert p.evaluate("() => window.__xt.alertGearsOf()") == [1, 2, 3, 4, 5, 6]


def test_a5_dedupe_and_sort(env):
    """重复与乱序都归一。"""
    p = env().page
    p.evaluate("() => window.__xt.setAlertGears([4, 2, 4, 2, 6])")
    assert p.evaluate("() => window.__xt.alertGearsOf()") == [2, 4, 6]


def test_a6_shrink_on_count_change(env):
    """10 档只开 9 → 切回 6 档，第 9 档被裁掉，其余保留。"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.evaluate("() => window.__xt.setAlertGears([3, 9])")
    assert p.evaluate("() => window.__xt.alertGearsOf()") == [3, 9]
    p.evaluate("() => window.__xt.setGearCount(6)")
    p.wait_for_timeout(200)
    assert p.evaluate("() => window.__xt.alertGearsOf()") == [3]


# ---------- 触发 ----------

def test_a7_only_selected_gear_hits(env):
    """只开第 5 档，价格跌穿 1–5 档 → 只有第 5 档留下 gearHit。"""
    p = env(price=3860.0).page
    p.evaluate("() => window.__xt.setAlertGears([5])")
    p.evaluate("() => { const s = window.__xt.state(); delete s.cfg.gearHit; }")
    set_px(p, 3840.0)
    got = hits(p)
    assert got == ["第5档"], got


def test_a8_all_off_no_hit(env):
    """全关后跌穿所有档位，一条 gearHit 都不产生。"""
    p = env(price=3860.0).page
    p.evaluate("() => window.__xt.setAlertGears([])")
    p.evaluate("() => { const s = window.__xt.state(); delete s.cfg.gearHit; }")
    set_px(p, 3800.0)
    assert hits(p) == []


def test_a9_default_still_hits_all(env):
    """默认配置下行为与 P8 一致：跌穿即全档命中。"""
    p = env(price=3860.0).page
    p.evaluate("() => { const s = window.__xt.state(); delete s.cfg.gearHit; }")
    set_px(p, 3800.0)
    got = sorted(hits(p))
    # 3800 已跌穿全部 6 档（最深的第 6 档 3849），默认配置下六档全应命中
    assert got == sorted(["第1档", "第2档", "第3档", "第4档", "第5档", "第6档"])


# ---------- UI ----------

def test_u1_grid_default_all_on(env):
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    n = p.evaluate("() => document.querySelectorAll('#setAlertGrid [data-al]').length")
    lit = p.evaluate("""() => document.querySelectorAll('#setAlertGrid [aria-pressed="true"]').length""")
    assert n == 6 and lit == 6


def test_u2_toggle_off(env):
    """点掉第 1 档 → 该格变暗、文案变「已开 5 / 6」。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    p.click('#setAlertGrid [data-al="1"]')
    p.wait_for_timeout(400)
    assert p.evaluate("""() => document.querySelector('#setAlertGrid [data-al="1"]').getAttribute('aria-pressed')""") == "false"
    assert "5 / 6" in p.inner_text("#setAlertLive")


def test_u3_all_off_text(env):
    """逐个关完 → 提示文案说明「深档兜底不受影响」。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    for i in range(1, 7):
        p.click('#setAlertGrid [data-al="%d"]' % i)
        p.wait_for_timeout(160)
    assert "全部到价提示" in p.inner_text("#setAlertLive")
    assert p.evaluate("() => window.__xt.alertGearsOf()") == []


def test_u4_grid_follows_count(env):
    """切 10 档后预警胶囊也变 10 个。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(400)
    p.click('#setGearCount [data-gc="10"]')
    p.wait_for_timeout(450)
    assert p.evaluate("() => document.querySelectorAll('#setAlertGrid [data-al]').length") == 10
