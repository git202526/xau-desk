"""P17 设置页收尾：未保存可见 / 保存可达 / 档数归位

三个实测问题：
  1. 保存按钮在 y≈2500，而它作用的字段在 y≈173，够不着
  2. 改了总仓切走再回来，输入框显示新值但 cfg 没变 —— 「假装生效」
  3. 「档位数量」离它重置的「档位分布」1.8 屏

契约：
  · 5 个保存项（TOTAL / keepGram / H_BASE / avgTarget / avgBand）与 cfg 不一致 = 脏
  · 脏项 > 0 且保存按钮不在视野 → 浮出提示条，计数 = 脏项数
  · 点浮条 = 调原来的保存逻辑
  · 切 tab 不回退输入框，脏状态保留
  · 浮条是 fixed 浮层，不改变设置页高度
  · 档位数量移到卡 C 首项，与档位分布同屏
"""
import pytest

from conftest import CFG_EMPTY


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=4700.0, TOTAL=300)
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    mk_keys = ("price", "width", "height", "gh_on", "reduced_motion", "tz", "calendar")

    def _f(**kw):
        mk = {}
        for k in list(kw.keys()):
            if k in mk_keys:
                mk[k] = kw.pop(k)
        mk.setdefault("price", 4030.0)
        return make_env(cfg=cfg(**kw), **mk)
    return _f


def open_set(page):
    page.click('.tabbar button[data-tab="set"]')
    page.wait_for_timeout(400)


def state(page):
    return page.evaluate("""() => {
        const bar = document.getElementById('dirtyBar');
        const t = document.getElementById('setTotal');
        const btn = document.getElementById('btnSaveSet');
        const r = btn.getBoundingClientRect();
        return {
            hidden: bar.hidden,
            num: document.getElementById('dirtyNum').textContent.trim(),
            totalVal: t.value,
            totalDirty: t.classList.contains('dirty'),
            btnInView: r.bottom > 0 && r.top < window.innerHeight,
            panelH: document.getElementById('panel-set').offsetHeight,
            sw: bar.scrollWidth, cw: bar.clientWidth,
            barBottom: bar.getBoundingClientRect().bottom,
            vh: window.innerHeight,
        };
    }""")


def blur(page):
    """输入框聚焦会让底栏上移隐藏（iOS 键盘让位），点 tab 之前先失焦。"""
    page.evaluate("() => document.activeElement && document.activeElement.blur()")
    page.wait_for_timeout(250)


def type_total(page, v):
    page.fill("#setTotal", str(v))
    blur(page)


# ---------------------------------------------------------------- 脏值与浮条

def test_s1_clean_initially(env):
    """刚进设置页没有脏值，浮条不渲染。"""
    p = env().page
    open_set(p)
    s = state(p)
    assert s["hidden"] is True, s
    assert s["totalDirty"] is False, s


def test_s2_dirty_shows_bar(env):
    """改总仓 → 浮条出现，计数 1，输入框描橙。"""
    p = env().page
    open_set(p)
    type_total(p, 500)
    s = state(p)
    assert s["hidden"] is False, s
    assert s["num"] == "1", s
    assert s["totalDirty"] is True, s


def test_s3_two_items(env):
    """同时改总仓和高点 → 计数 2。"""
    p = env().page
    open_set(p)
    type_total(p, 500)
    p.fill("#setHigh", "4800")
    blur(p)
    assert state(p)["num"] == "2", state(p)


def test_s4_save_clears(env):
    """点浮条保存 → cfg 同步、浮条消失。"""
    p = env().page
    open_set(p)
    type_total(p, 500)
    assert state(p)["hidden"] is False
    p.click("#dirtySave")
    p.wait_for_timeout(400)
    s = state(p)
    assert s["hidden"] is True, s
    assert p.evaluate("() => window.__xt.state().cfg.TOTAL") == 500
    assert p.evaluate("() => window.__xt.state().TOTAL") == 500


def test_s5_tab_roundtrip_keeps_input(env):
    """切走再回来：输入框仍是 500，浮条仍在（不再假装生效）。"""
    p = env().page
    open_set(p)
    type_total(p, 500)
    p.click('.tabbar button[data-tab="ov"]')
    p.wait_for_timeout(300)
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(400)
    s = state(p)
    assert s["totalVal"] == "500", s
    assert s["hidden"] is False, s
    assert p.evaluate("() => window.__xt.state().cfg.TOTAL") == 300


# ---------------------------------------------------------------- 不该误判成脏

def test_s6_auto_keep_not_dirty(env):
    """底仓没设过（显示自动值）不算脏。"""
    p = env(buys=[{"id": "b1", "name": "买入", "gram": 100, "priceCny": 900,
                   "time": "2026-10-01T02:00:00.000Z", "note": ""}]).page
    open_set(p)
    s = state(p)
    assert s["hidden"] is True, s


def test_s7_empty_avg_not_dirty(env):
    """目标均价留空（按波动率）不算脏。"""
    p = env().page
    open_set(p)
    p.fill("#setAvgT", "")
    blur(p)
    assert state(p)["hidden"] is True, state(p)


def test_s8_band_default_not_dirty(env):
    """容差显示 20、cfg 没存过 → 也不算脏。"""
    p = env().page
    open_set(p)
    assert p.evaluate("() => document.getElementById('setAvgB').value") == "20"
    assert state(p)["hidden"] is True


def test_s9_band_changed_is_dirty(env):
    """容差改成 30 才算脏。"""
    p = env().page
    open_set(p)
    p.fill("#setAvgB", "30")
    blur(p)
    assert state(p)["num"] == "1", state(p)


# ---------------------------------------------------------------- 位置与布局

def test_s10_gear_count_before_dist(env):
    """档位数量在卡 C 首项，与档位分布同屏（390 宽下 y 差 ≤ 260px）。"""
    p = env(width=390).page
    open_set(p)
    d = p.evaluate("""() => {
        const gc = document.getElementById('setGearCount').getBoundingClientRect();
        const wg = document.getElementById('setWGrid').getBoundingClientRect();
        const cardC = document.getElementById('cardExec');
        return { dy: Math.abs(wg.top - gc.top), first: cardC.querySelector('.wgrid').id };
    }""")
    assert d["dy"] <= 260, d
    assert d["first"] == "setGearCount", d


def test_s11_bar_is_overlay(env):
    """浮条是 fixed 浮层：出现前后设置页高度不变。"""
    p = env().page
    open_set(p)
    h0 = state(p)["panelH"]
    type_total(p, 500)
    h1 = state(p)["panelH"]
    assert h1 == h0, (h0, h1)


def test_s12_narrow_fits(env):
    """320 宽下浮条不溢出、不压在 tabbar 上。"""
    p = env(width=320).page
    open_set(p)
    type_total(p, 500)
    s = state(p)
    assert s["hidden"] is False
    assert s["sw"] <= s["cw"] + 1, s
    assert s["barBottom"] <= s["vh"] - 40, s
