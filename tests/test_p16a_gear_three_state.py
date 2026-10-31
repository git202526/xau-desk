"""P16a 档位三态：已买 / 已买满 / 已买超

背景：v6.30 里 `done` 只有「买满」一个含义，标签也只写「已买入」一种，
买了 8/16g、16/16g、24/16g 三种完全不同的状态，界面上只有两种标签。

契约（本文件锁定）：
  · overG > 0.05                     → 「已买超」(p-over, 琥珀)
  · overG <= 0.05 且 pending <= 0.05 → 「已买满」(p-done, 蓝)
  · boughtG > 0.05 且 pending > 0.05 → 「已买」(p-part, 灰)
  · boughtG <= 0.05                  → 沿用价格态（已跌破/刚到价/逼近/未到）

判断顺序必须是 超 → 满 → 部分。

口径：H=4700、TOTAL=200、权重 8/12/24/36/12/8 → 计划 16/24/48/72/24/16，
档位价 4089/4041/3993/3945/3897/3849，现价 4030（第 1 档已跌破）。
"""
import pytest

from conftest import CFG_EMPTY

H = 4700.0
W = 200.0
PX = 4030.0


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W, planOnly=True, gearMarks={})
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
        mk.setdefault("price", PX)
        return make_env(cfg=base, **mk)
    return _f


def open_plan(page):
    page.click('.tabbar button[data-tab="plan"]')
    page.wait_for_timeout(300)


def rows(page):
    return page.evaluate("""() => [...document.querySelectorAll('#ladder .lv')].map(l => ({
        name: l.querySelector('.nm').textContent.trim(),
        cls: l.className,
        pills: [...l.querySelectorAll('.r2 .pill')].map(p => p.textContent.trim()),
        gpill: (l.querySelectorAll('.r2 .pill.g')[0] || {}).textContent || '',
        chip: (l.querySelectorAll('.r2 .pill').length
               ? [...l.querySelectorAll('.r2 .pill')].map(p => p.className).join('|') : ''),
    }))""")


def row(page, name):
    for r in rows(page):
        if r["name"] == name:
            return r
    raise AssertionError("找不到档位 " + name)


HOLD = ("已买", "已买满", "已买超")


def hold_of(r):
    for h in ("已买超", "已买满", "已买"):
        if h in r["pills"]:
            return h
    return None


# ---------------------------------------------------------------- 三态判定

def test_a1_partial_is_bought(env):
    """买 8 / 计划 48 → 「已买」，不是「已买满」。"""
    p = env(gearMarks={"3": {"g": 8, "cost": 8 * 3993, "at": 1700000000000}}).page
    open_plan(p)
    r = row(p, "第3档")
    assert hold_of(r) == "已买", r
    assert "8 / 48g" in r["gpill"], r


def test_a2_full_is_full(env):
    """买满 48 / 48 → 「已买满」。"""
    p = env(gearMarks={"3": {"g": 48, "cost": 48 * 3993, "at": 1700000000000}}).page
    open_plan(p)
    r = row(p, "第3档")
    assert hold_of(r) == "已买满", r
    assert "48 / 48g" in r["gpill"], r


def test_a3_over_is_over(env):
    """买 60 / 计划 48 → 「已买超」，不是「已买满」。"""
    p = env(gearMarks={"3": {"g": 60, "cost": 60 * 3993, "at": 1700000000000}}).page
    open_plan(p)
    r = row(p, "第3档")
    assert hold_of(r) == "已买超", r
    assert "60 / 48g" in r["gpill"], r


def test_a4_over_has_budget_warning(env):
    """买超时行尾仍挂「超预算」，克重栏写实际 / 计划。"""
    p = env(gearMarks={"3": {"g": 61, "cost": 61 * 3993, "at": 1700000000000}}).page
    open_plan(p)
    r = row(p, "第3档")
    assert "超预算" in r["pills"], r
    assert r["gpill"].startswith("61 / 48"), r


def test_a5_price_dimension_survives(env):
    """只买 8/48g 且价格已跌破 → 标签「已买」，但行样式仍是跌破态（两个维度并存）。"""
    p = env(gearMarks={"1": {"g": 8, "cost": 8 * 4089, "at": 1700000000000}}).page
    open_plan(p)
    r = row(p, "第1档")
    assert hold_of(r) == "已买", r
    assert "s-dead" in r["cls"], r


def test_a6_untouched_keeps_price_label(env):
    """没买过的档不出现任何持仓态标签。"""
    p = env().page
    open_plan(p)
    for nm in ("第1档", "第5档"):
        r = row(p, nm)
        assert hold_of(r) is None, r
    assert "已跌破" in row(p, "第1档")["pills"]


# ---------------------------------------------------------------- 与其它功能共存

def test_a7_est_survives_three_states(env):
    """「估」在三态下都在：它只说成本是估的，与买没买满无关。"""
    p = env(gearMarks={
        "1": {"g": 8, "at": 1700000000000},        # 部分，无 cost
        "2": {"g": 24, "at": 1700000000000},       # 满，无 cost
        "3": {"g": 60, "at": 1700000000000},       # 超，无 cost
    }).page
    open_plan(p)
    for nm, want in (("第1档", "已买"), ("第2档", "已买满"), ("第3档", "已买超")):
        r = row(p, nm)
        assert hold_of(r) == want, r
        assert "估" in r["pills"], r


def test_a8_ledger_mode_same_states(env):
    """账本模式（无 gearMarks）也走三态，不依赖 planOnly。"""
    p = env(planOnly=False, gearsDone=[],
            buys=[{"id": "b1", "name": "第3档", "gram": 8, "priceCny": 900,
                   "time": "2026-10-01T02:00:00.000Z", "note": "", "gear": "第3档"},
                  {"id": "b2", "name": "第4档", "gram": 72, "priceCny": 900,
                   "time": "2026-10-02T02:00:00.000Z", "note": "", "gear": "第4档"}],
            gearMarks={}).page
    open_plan(p)
    assert hold_of(row(p, "第3档")) == "已买", rows(p)
    assert hold_of(row(p, "第4档")) == "已买满", rows(p)
