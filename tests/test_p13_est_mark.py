"""P13 「估」字角标：区分真实成交成本与按档位价估出来的成本。

P6 §12.4 留下的一句话：「`cost = 0` 与真实成交价在视觉上未区分」。
早期（P5 之前）的已买入标记只存 `{g, at}`，没有 `cost` 字段；`gearCost()` 遇到这种标记会
拿**档位价**折一个成本出来（否则 realized 差价直接崩）。数字是对的，但它不是真实成交价，
用户看到行尾「+42 / 1.0 轮」会以为那是实盘差价。

这一期只加标记，不动任何计算：

  · `S.gears[i].costEst` = 有净持仓 且 `cost <= 0`
  · 该档「已买入」旁边多一枚灰色「估」胶囊，带 title 说明
  · 一旦这一档有过一次带价买入（`markBuy` 会把历史克重按档位价折进 cost），估字自动消失

口径：H=4700、档距 48 → 第 1 档 4089 / 第 2 档 4041，现价 4030（第 1 档已跌破）。
"""
import pytest

from conftest import CFG_EMPTY

H = 4700.0
W = 200.0
PX = 4030.0
P1 = 4089.0
P2 = 4041.0


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W, planOnly=True,
             swingGears=[1, 2], gearMarks={})
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
    page.wait_for_timeout(350)


def rows(page):
    return page.evaluate("""() => [...document.querySelectorAll('#ladder .lv')].map(l => ({
        name: l.querySelector('.nm').textContent.trim(),
        pills: [...l.querySelectorAll('.r2 .pill')].map(p => p.textContent.trim()),
        est: !!l.querySelector('.r2 .pill.est'),
        estTitle: (l.querySelector('.r2 .pill.est') || {}).title || '',
        estCls: (l.querySelector('.r2 .pill.est') || {}).className || '',
    }))""")


def row(page, name):
    for r in rows(page):
        if r["name"] == name:
            return r
    raise AssertionError("找不到档位 " + name)


# ---------------------------------------------------------------- 标记出现 / 不出现

def test_e1_old_mark_gets_est(env):
    """老标记只有 {g, at}，没有 cost → 标「估」。"""
    p = env(gearMarks={"1": {"g": 16, "at": 1700000000000}}).page
    open_plan(p)
    r = row(p, "第1档")
    assert r["est"] is True, r
    assert "估" in r["pills"], r


def test_e2_cost_mark_no_est(env):
    """带 cost 的真实标记 → 不标「估」。"""
    p = env(gearMarks={
        "1": {"g": 16, "at": 1700000000000},                       # 老标记，会标
        "2": {"g": 24, "cost": 24 * P2 * 0.0321, "at": 1700000000000},  # 有成本，不标
    }).page
    open_plan(p)
    assert row(p, "第1档")["est"] is True
    assert row(p, "第2档")["est"] is False, row(p, "第2档")
    assert "估" not in row(p, "第2档")["pills"]


def test_e3_no_mark_no_est(env):
    """没买过的档位不出现「估」——它连「已买入」都没有。"""
    p = env(gearMarks={"1": {"g": 16, "at": 1700000000000}}).page
    open_plan(p)
    r = row(p, "第4档")
    assert r["est"] is False
    assert "已买入" not in r["pills"]


def test_e4_ledger_mode_no_est(env):
    """账本模式（非计划模式）没有 gearMarks，一律不标。"""
    p = env(planOnly=False, gearsDone=["第1档"],
            gearMarks={"1": {"g": 16, "at": 1700000000000}}).page
    open_plan(p)
    assert row(p, "第1档")["est"] is False, rows(p)


def test_e5_zero_gram_no_est(env):
    """g=0 的空壳标记不标「估」（本来也不该有已买入）。"""
    p = env(gearMarks={"1": {"g": 0, "cost": 0, "at": 1700000000000}}).page
    open_plan(p)
    r = row(p, "第1档")
    assert r["est"] is False, r
    assert "已买入" not in r["pills"]


# ---------------------------------------------------------------- 视觉与契约

def test_e6_label_unchanged(env):
    """「已买入」文字不变，估是独立的邻居胶囊（老断言 '已买入' in pills 不受影响）。"""
    p = env(gearMarks={"1": {"g": 16, "at": 1700000000000}}).page
    open_plan(p)
    r = row(p, "第1档")
    assert "已买入" in r["pills"], r
    assert r["pills"].count("估") == 1, r
    # 估必须紧挨在已买入后面，而不是跑到 %/克重后面
    i = r["pills"].index("已买入")
    assert r["pills"][i + 1] == "估", r


def test_e7_est_has_title(env):
    """「估」有 hover 说明，且样式是独立的 est 类（不是 p-done）。"""
    p = env(gearMarks={"1": {"g": 16, "at": 1700000000000}}).page
    open_plan(p)
    r = row(p, "第1档")
    assert "估算" in r["estTitle"], r["estTitle"]
    assert "est" in r["estCls"]
    assert "p-done" not in r["estCls"]


def test_e8_est_color(env):
    """「估」用 amber 色，和「已买入」的蓝区分开（390 宽下也要能看见）。"""
    p = env(gearMarks={"1": {"g": 16, "at": 1700000000000}}, width=390).page
    open_plan(p)
    col = p.evaluate("""() => getComputedStyle(document.querySelector('#ladder .pill.est')).color""")
    assert col == "rgb(232, 163, 61)", col


# ---------------------------------------------------------------- 估会自动消失

def test_e9_markbuy_clears_est(env):
    """老标记再买一次：markBuy 把历史克重按档位价折进 cost，估字消失。"""
    p = env(gearMarks={"1": {"g": 16, "at": 1700000000000}}).page
    open_plan(p)
    assert row(p, "第1档")["est"] is True
    ok = p.evaluate("() => window.__xt.markBuy(1, 5, %f)" % P1)
    assert ok is True
    p.wait_for_timeout(350)
    r = row(p, "第1档")
    assert r["est"] is False, r
    cost = p.evaluate("() => Number(window.__xt.state().cfg.gearMarks['1'].cost)")
    assert cost > 0


def test_e10_gearcost_still_estimates(env):
    """引擎层的估算行为不变：没有 cost 仍返回按档位价折出来的正数。"""
    p = env().page
    v0 = p.evaluate("() => window.__xt.gearCost(1, {g: 16})")
    assert v0 > 0
    v1 = p.evaluate("() => window.__xt.gearCost(1, {g: 16, cost: 60000})")
    assert v1 == pytest.approx(60000)


def test_e11_ten_gears(env):
    """10 档时同样只在老标记上标估，不串档。"""
    p = env(gearCount=10, wPct=[4, 5, 8, 11, 14, 16, 17, 13, 8, 4],
            gearMarks={"3": {"g": 8, "at": 1700000000000},
                       "7": {"g": 6, "cost": 20000, "at": 1700000000000}}).page
    open_plan(p)
    p.wait_for_timeout(250)
    assert row(p, "第3档")["est"] is True
    assert row(p, "第7档")["est"] is False
