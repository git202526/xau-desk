"""P14-1 设置页拆成三张卡 + 标题摘要。

原来「建仓参数」是一张 2285px 的长卡，12 组控件从头排到尾，只有一个「档位与高点」小标题。
这一期只挪 DOM、不改任何 id：

  · #cardPos   仓位：总克重 / 底仓 / 计划减仓 / 成本下方减仓
  · #cardShape 档位形态：高点 / 目标 / 容差 / 档距方式 / α / 起止跌幅 / 锚定 / 档数
  · #cardExec  分布与执行：分布 / 循环档 / 预警 / 兜底 / 下沉 / 计划模式 / 保存按钮

每张卡的 sech 标题里带一行只读摘要（`.cnt`），不划进去也知道现在是什么配置。

注意：拆卡不会变短（三组卡头多 118px），高度指标归 P14-3 的折叠。
"""
import pytest

from test_p11_weight_sum import cfg, BARS


@pytest.fixture
def env(make_env):
    def _f(c=None, **kw):
        base = dict(c) if c is not None else cfg()
        mk = {}
        for k in list(kw.keys()):
            if k in ("width", "height", "price", "gh_on", "reduced_motion", "tz", "calendar"):
                mk[k] = kw.pop(k)
        base.update(kw)
        mk.setdefault("bars", BARS)
        return make_env(cfg=base, **mk)
    return _f


def open_set(page):
    page.click('.tabbar button[data-tab="set"]')
    page.wait_for_timeout(450)


def body(page):
    return page.evaluate("""() => {
        const set = document.getElementById('panel-set');
        const inCard = id => {
            const el = document.getElementById(id);
            const card = el && el.closest('.card');
            return card ? card.id : null;
        };
        const ids = ['setTotal','setKeep','setCut','setStop',
                     'setHigh','setHighLive','btnHighAuto','setAvgT','setAvgB','setSpanMode',
                     'setAlpha','setSpanLive','setStartDrop','setEndDrop','setAnchor','setGearCount',
                     'setWGrid','setWLab','setSwingGrid','setAlertGrid','setDripDays','setReOn',
                     'setPlanOnly','btnSaveSet'];
        const own = {};
        ids.forEach(i => { own[i] = inCard(i); });
        return {
            order: [...set.querySelectorAll('.sech h2')].map(h => h.textContent.trim().split(' ')[0]),
            cards: [...set.querySelectorAll('.card')].map(c => c.id || '(gh)'),
            own: own,
            sums: {
                pos: (document.getElementById('sumPos') || {}).textContent || '',
                shape: (document.getElementById('sumShape') || {}).textContent || '',
                exec: (document.getElementById('sumExec') || {}).textContent || '',
            },
            total: set.scrollHeight,
            vw: window.innerWidth,
        };
    }""")


# ---------------------------------------------------------------- 结构

def test_l1_three_cards_in_order(env):
    """三张卡按「仓位 → 档位形态 → 分布与执行」排，提醒与云端跟在后面。"""
    p = env().page
    open_set(p)
    d = body(p)
    cards = [c for c in d["cards"] if c != "(gh)"]
    assert cards[:3] == ["cardPos", "cardShape", "cardExec"], d["cards"]
    assert d["order"][:3] == ["仓位", "档位形态", "分布与执行"], d["order"]


def test_l2_pos_owns(env):
    """仓位卡只装仓位四项。"""
    p = env().page
    open_set(p)
    own = body(p)["own"]
    for i in ("setTotal", "setKeep", "setCut", "setStop"):
        assert own[i] == "cardPos", (i, own[i])


def test_l3_shape_owns(env):
    """形态卡装高点/目标/档距/锚定/档数。"""
    p = env().page
    open_set(p)
    own = body(p)["own"]
    for i in ("setHigh", "setHighLive", "btnHighAuto", "setAvgT", "setAvgB", "setSpanMode",
              "setAlpha", "setSpanLive", "setStartDrop", "setEndDrop", "setAnchor", "setGearCount"):
        assert own[i] == "cardShape", (i, own[i])


def test_l4_exec_owns(env):
    """执行卡装分布/循环/预警/兜底/下沉/计划模式，保存按钮也在这一张。"""
    p = env().page
    open_set(p)
    own = body(p)["own"]
    for i in ("setWGrid", "setWLab", "setSwingGrid", "setAlertGrid", "setDripDays",
              "setReOn", "setPlanOnly", "btnSaveSet"):
        assert own[i] == "cardExec", (i, own[i])


def test_l5_grid_and_lab_same_card(env):
    """grid 与 lab 必须同卡（applyCols 靠两者配对，分开会让 10 档对齐失效）。"""
    p = env(gearCount=10, wPct=[4, 5, 8, 11, 14, 16, 17, 13, 8, 4]).page
    open_set(p)
    d = body(p)
    assert d["own"]["setWGrid"] == d["own"]["setWLab"] == "cardExec"


def test_l6_ten_gears_labels_aligned(env):
    """10 档时输入框与档号标签逐格 x 一致（P11 遗留的回归保护）。"""
    p = env(gearCount=10, wPct=[4, 5, 8, 11, 14, 16, 17, 13, 8, 4]).page
    open_set(p)
    xs = p.evaluate("""() => {
        const g = [...document.querySelectorAll('#setWGrid input')].map(e => Math.round(e.getBoundingClientRect().left));
        const l = [...document.querySelectorAll('#setWLab span')].map(e => Math.round(e.getBoundingClientRect().left));
        return {g, l};
    }""")
    assert xs["g"] == xs["l"], xs


# ---------------------------------------------------------------- 摘要

def test_l7_pos_summary(env):
    """仓位摘要：目标克重 + 已持仓（无持仓时不写「已持」）。"""
    p = env(TOTAL=310).page
    open_set(p)
    s = body(p)["sums"]["pos"]
    assert "310g" in s, s
    assert "已持" not in s, s


def test_l8_shape_summary(env):
    """形态摘要：档数 + 档距方式 + 首末档价。"""
    p = env(gearCount=8, wPct=[5, 7, 11, 16, 20, 18, 14, 9]).page
    open_set(p)
    s = body(p)["sums"]["shape"]
    assert s.startswith("8 档"), s
    assert "→" in s, s
    prices = p.evaluate("() => window.__xt.state().gears.filter(g => !g.warn).map(g => Math.round(g.price))")
    assert str(prices[0]) in s and str(prices[-1]) in s, (s, prices)


def test_l9_shape_summary_follows_alpha(env):
    """切到 α 方式：摘要显示 α 数值而不是「目标」。"""
    p = env().page
    open_set(p)
    p.evaluate("() => window.__xt.setAlpha(1.4)")
    p.wait_for_timeout(350)
    s = body(p)["sums"]["shape"]
    assert "α" in s and "1.40" in s, s


def test_l10_shape_summary_follows_count(env):
    """切到 10 档：摘要立刻变「10 档」，不用等刷新页面。"""
    p = env(gearCount=8, wPct=[5, 7, 11, 16, 20, 18, 14, 9]).page
    open_set(p)
    assert body(p)["sums"]["shape"].startswith("8 档")
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.wait_for_timeout(400)
    s = body(p)["sums"]["shape"]
    assert s.startswith("10 档"), s


def test_l11_exec_summary(env):
    """执行摘要：计划标记 + 合计百分比 + 循环档数。"""
    p = env(planOnly=True, swingGears=[1, 2]).page
    open_set(p)
    s = body(p)["sums"]["exec"]
    assert s.startswith("计划"), s
    assert "合计 100%" in s, s
    assert "循环 2" in s, s


def test_l12_exec_summary_over_100(env):
    """权重超 100%：摘要显示真实合计（与输入框一致）。

    wPct 长度必须等于档数，否则 normW 按严格长度语义回模板（合计又会是 100）。
    """
    p = env(gearCount=8, wPct=[4, 5, 8, 11, 14, 16, 50, 50]).page
    open_set(p)
    s = body(p)["sums"]["exec"]
    assert "合计 158%" in s, s


def test_l13_exec_summary_ledger(env):
    """账本模式不写「计划」前缀。"""
    p = env(planOnly=False).page
    open_set(p)
    s = body(p)["sums"]["exec"]
    assert not s.startswith("计划"), s
    assert "合计" in s, s


def test_l14_summary_no_overflow(env):
    """320 宽：摘要不撑破卡片（超长由 ellipsis 处理，不允许横向溢出）。"""
    p = env(gearCount=10, wPct=[4, 5, 8, 11, 14, 16, 17, 13, 8, 4], width=320).page
    open_set(p)
    ok = p.evaluate("""() => [...document.querySelectorAll('#panel-set .sech')].every(s => {
        const h = s.querySelector('h2');
        return s.scrollWidth <= s.clientWidth + 1 && h.scrollWidth <= h.clientWidth + 1;
    })""")
    assert ok is True
