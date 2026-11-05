"""v6.18 单引擎重写 · 第 2 期（呈现层）。

覆盖：计划模式开关、档位标记面板、撤销、弹药进度、超买提示、
射程与 40 日触及概率、设置页新控件（α / 六格 / 恢复默认）、320 窄屏不溢出。
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H = 4700.0
W = 200
D_3900 = 73.828125


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
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W)
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    def _f(c=None, **kw):
        return make_env(cfg=c if c is not None else cfg(), bars=bars_calm(), **kw)
    return _f


def save_mark(p, amt="20000"):
    """P27：金额必填。这些用例只关心克重与入口，填一个正数即可。"""
    if not p.input_value("#markAmt"):
        p.fill("#markAmt", amt)
    p.click("#markSave")


def open_tab(page, tab):
    page.click('.tabbar button[data-tab="%s"]' % tab)
    page.wait_for_timeout(260)


def row_text(page, name):
    return page.evaluate("""(n) => {
        const l = [...document.querySelectorAll('#ladder .lv')]
            .find(x => x.querySelector('.nm').textContent.trim() === n);
        if (!l) return null;
        return {
            g: (l.querySelector('.r2 .pill.g') || {}).textContent || '',
            plan: (l.querySelector('.plan') || {}).textContent || '',
            price: (l.querySelector('.price') || {}).textContent || '',
            circ: (l.querySelector('.circ') || {}).textContent || '',
            mark: !!l.querySelector('[data-mark]'),
            unmark: !!l.querySelector('[data-unmark]'),
            dim: l.classList.contains('markDone'),
        };
    }""", name)


def enable_plan(page):
    """P20 契约变更：`#setPlanOnly` 随模式合并被删除，进计划页即可。"""
    open_tab(page, "plan")


def toast_text(page):
    return page.evaluate("""() => {
        const t = document.getElementById('toast');
        return t ? t.textContent.trim() : '';
    }""")


# ---------------------------------------------------------------- 1 开关与价格

def test_marks_available_without_mode(env):
    """档位一开始就有标记入口，开合面板不动档位价。

    P20 契约变更：原名 `test_plan_toggle_shows_mark_and_keeps_prices`，守的是
    「开计划模式才出现标记按钮」。开关随模式删除，改为直接核对入口与价格不变。
    """
    e = env()
    p = e.page
    open_tab(p, "plan")
    before = p.evaluate("() => window.__xt.gearRows()")
    r4 = row_text(p, "第4档")
    assert r4["mark"] is True, r4
    assert r4["plan"] == "计划 72g"
    assert r4["g"] == "待补 72g"

    p.evaluate("() => window.__xt.openMarkSheet('第4档','buy')")
    p.wait_for_timeout(220)
    p.evaluate("() => window.__xt.closeMarkSheet()")
    p.wait_for_timeout(220)
    after = p.evaluate("() => window.__xt.gearRows()")
    assert [r["price"] for r in after] == [r["price"] for r in before], "开合面板不该动档位价"
    assert p.evaluate("() => document.querySelector('#ladderMeta .planTag')") is None


# ---------------------------------------------------------------- 2 标记 20 < 72

def test_mark_prefills_plan_then_shows_pending(env):
    e = env()
    p = e.page
    enable_plan(p)
    p.click('#ladder [data-mark="第4档"]')
    p.wait_for_timeout(220)
    assert p.evaluate("() => document.getElementById('sheetMark').classList.contains('on')")
    assert p.input_value("#markGram") == "72"

    p.fill("#markGram", "20")
    save_mark(p)
    p.wait_for_timeout(300)
    r4 = row_text(p, "第4档")
    assert r4["g"] == "20 / 72g", r4
    assert r4["dim"] is True
    assert r4["unmark"] is True


# ---------------------------------------------------------------- 3 标记不动邻档

def test_mark_does_not_touch_neighbors(env):
    e = env()
    p = e.page
    enable_plan(p)
    p.click('#ladder [data-mark="第3档"]')
    p.wait_for_timeout(200)
    p.fill("#markGram", "10")
    save_mark(p)
    p.wait_for_timeout(300)

    r3 = row_text(p, "第3档")
    assert r3["g"] == "10 / 48g", r3
    r4 = row_text(p, "第4档")
    assert r4["plan"] == "计划 72g"
    assert r4["g"] == "待补 72g"
    assert r4["price"].replace(",", "") == "3945.00"      # 默认 α=1：4089 − 3×48


# ---------------------------------------------------------------- 4 撤销

def test_unmark_restores_and_toasts(env):
    e = env()
    p = e.page
    enable_plan(p)
    p.click('#ladder [data-mark="第3档"]')
    p.wait_for_timeout(200)
    p.fill("#markGram", "10")
    save_mark(p)
    p.wait_for_timeout(300)
    before = {r["name"]: r["pending"] for r in p.evaluate("() => window.__xt.gearRows()") if not r["warn"]}

    p.click('#ladder [data-unmark="第3档"]')
    p.wait_for_timeout(320)
    assert "恢复待补" in toast_text(p)
    r3 = row_text(p, "第3档")
    assert r3["g"] == "待补 48g", r3
    assert r3["mark"] is True and r3["unmark"] is False

    after = {r["name"]: r["pending"] for r in p.evaluate("() => window.__xt.gearRows()") if not r["warn"]}
    assert after["第4档"] == before["第4档"]
    assert after["第5档"] == before["第5档"]
    assert after["第6档"] == before["第6档"]


# ---------------------------------------------------------------- 5 累计口径

def test_second_buy_accumulates(env):
    """P6 起面板是【增量】语义：已买 10g 后再填 30g → 40g（不是 30g）。
    默认值也从「累计已买」改成「待补」= 48 − 10 = 38（P6 §5.2）。"""
    e = env()
    p = e.page
    enable_plan(p)
    p.click('#ladder [data-mark="第3档"]')
    p.wait_for_timeout(200)
    p.fill("#markGram", "10")
    save_mark(p)
    p.wait_for_timeout(320)
    # 已标记的档再点行（不点撤销）→ 面板带出待补克重，可改
    p.click('#ladder [data-gear="第3档"] .price')
    p.wait_for_timeout(260)
    assert p.input_value("#markGram") == "38"
    p.fill("#markGram", "30")
    save_mark(p)
    p.wait_for_timeout(320)
    r3 = row_text(p, "第3档")
    assert r3["g"] == "40 / 48g", r3
    assert p.evaluate("() => window.__xt.gearState(3).bought") == 40


# ---------------------------------------------------------------- 6 不进账本

def test_mark_does_not_write_ledger(env):
    e = env()
    p = e.page
    open_tab(p, "plan")
    n0 = p.evaluate("() => (window.__xt.state().cfg.buys || []).length")
    avg0 = p.evaluate("() => window.__xt.holdStats().avg")
    enable_plan(p)
    p.click('#ladder [data-mark="第4档"]')
    p.wait_for_timeout(200)
    p.fill("#markGram", "20")
    save_mark(p)
    p.wait_for_timeout(300)
    assert p.evaluate("() => (window.__xt.state().cfg.buys || []).length") == n0
    assert p.evaluate("() => window.__xt.holdStats().avg") == avg0
    assert p.evaluate("() => (window.__xt.state().cfg.sells || []).length") == 0


# ---------------------------------------------------------------- 7 刷新后仍在

def test_marks_survive_reload(env):
    e = env()
    p = e.page
    enable_plan(p)
    p.click('#ladder [data-mark="第3档"]')
    p.wait_for_timeout(200)
    p.fill("#markGram", "12")
    save_mark(p)
    p.wait_for_timeout(1400)          # 等云端推送落地
    assert e.gh.puts > 0

    p.reload(wait_until="domcontentloaded")
    p.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    p.wait_for_function("() => window.__xt.planSummary().bought > 11", timeout=6000)
    assert p.evaluate("() => window.__xt.planSummary().bought") == pytest.approx(12, abs=0.01)
    open_tab(p, "plan")
    assert row_text(p, "第3档")["g"] == "12 / 48g"


# ---------------------------------------------------------------- 8 保存设置后标记不动

def test_marks_survive_settings_save(env):
    """保存一次设置参数，标记与净持都原样留着。

    P20 契约变更：这一条原名 `test_switch_back_to_ledger_keeps_marks`，
    守的是「切回账本模式后标记被无视（bought 归 0）」。模式合并后那个维度不存在了，
    但「保存设置不许动标记」这件事仍然值得守 —— 改成保存一次参数再核对。
    """
    e = env()
    p = e.page
    enable_plan(p)
    p.click('#ladder [data-mark="第4档"]')
    p.wait_for_timeout(200)
    p.fill("#markGram", "20")
    save_mark(p)
    p.wait_for_timeout(300)

    open_tab(p, "set")
    p.click("#btnSaveSet")
    p.wait_for_timeout(320)
    open_tab(p, "plan")
    assert row_text(p, "第4档")["mark"] is False
    assert p.evaluate("() => Object.keys(window.__xt.state().cfg.gearMarks || {}).length") == 1
    assert p.evaluate("() => window.__xt.gearState(4).bought") == 20
    assert row_text(p, "第4档")["g"] == "20 / 72g"


# ---------------------------------------------------------------- 9 弹药进度

def test_ammo_uses_plan_not_ledger(env):
    e = env()
    p = e.page
    enable_plan(p)
    p.click('#ladder [data-mark="第3档"]')
    p.wait_for_timeout(200)
    p.fill("#markGram", "10")
    save_mark(p)
    p.wait_for_timeout(300)
    assert p.evaluate("() => document.getElementById('ammoTxt').textContent.trim()") == "10 / 200 克"
    assert p.evaluate("() => window.__xt.holdStats().grams") == 0
    # P22 起标题行不再报待补（与「档位执行 10 / 200 克」是同一个数），也不报高点
    # （与下一行「档位来源」重复）。数没丢，只是搬到该在的位置。
    meta = p.evaluate("() => document.getElementById('ladderMeta').textContent")
    assert "待补" not in meta and "高点" not in meta, meta
    assert p.evaluate("() => window.__xt.planSummary().pending") == pytest.approx(190, abs=0.01)


# ---------------------------------------------------------------- 10 超买

def test_overbuy_is_flagged(env):
    e = env()
    p = e.page
    enable_plan(p)
    p.click('#ladder [data-mark="第3档"]')
    p.wait_for_timeout(200)
    p.fill("#markGram", "58")
    save_mark(p)
    p.wait_for_timeout(300)
    r3 = row_text(p, "第3档")
    assert r3["g"] == "58 / 48g", r3
    assert "已超预算 10" in p.evaluate("() => document.getElementById('ladderMeta').textContent")
    assert p.evaluate("() => window.__xt.planSummary().overBudget") == pytest.approx(10, abs=0.01)


# ---------------------------------------------------------------- 11-13 概率

def test_hit_prob_table_is_verbatim(env):
    p = env().page
    # 只有四个实测点，取「不超过目标跌幅的最大档」
    assert p.evaluate("() => window.__xt.hitProb(8, 40).p") == pytest.approx(13.6)
    assert p.evaluate("() => window.__xt.hitProb(13, 40).p") == pytest.approx(5.5)
    assert p.evaluate("() => window.__xt.hitProb(14, 40).p") == pytest.approx(5.5)   # 取 13 档
    assert p.evaluate("() => window.__xt.hitProb(20, 40).p") == pytest.approx(2.0)   # 取 19 档
    assert p.evaluate("() => window.__xt.hitProb(8, 10).p") == pytest.approx(3.9)


def test_hit_prob_below_table_does_not_fabricate(env):
    p = env().page
    r = p.evaluate("() => window.__xt.hitProb(7.7, 40)")
    assert r["below"] is True and r["p"] == pytest.approx(13.6)
    r2 = p.evaluate("() => window.__xt.hitProb(3.1, 40)")
    assert r2["below"] is True
    assert p.evaluate("() => window.__xt.hitProb(0, 40)") is None


def test_reach_and_row_probability(env):
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.setTargetT(3900)")
    p.evaluate("() => window.__xt.tick(4030)")
    p.wait_for_timeout(300)

    ri = p.evaluate("() => window.__xt.reachInfo()")
    assert ri["total"] == 6
    assert ri["k"] == 2, ri                    # 第 1 档已跌破，射程 2/6
    assert ri["dd"] == pytest.approx(7.70, abs=0.06)   # 到第 6 档 3720
    assert ri["prob"]["p"] == pytest.approx(13.6, abs=0.05)
    assert ri["prob"]["below"] is True         # 7.7 < 8，表内无点

    open_tab(p, "plan")
    r6 = row_text(p, "第6档")
    assert "40日触及" in r6["circ"], r6


def test_reach_all_broken(env):
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.setTargetT(3900)")
    p.evaluate("() => window.__xt.tick(3600)")
    p.wait_for_timeout(300)
    ri = p.evaluate("() => window.__xt.reachInfo()")
    assert ri["k"] == 6 and ri["next"] is None
    assert ri["dd"] == 0
    card = p.evaluate("() => (document.getElementById('avgCard') || {}).textContent || ''")
    assert "已全部跌破" in card or "射程 6/6" in card


# ---------------------------------------------------------------- 14 设置页

def test_settings_alpha_and_grid(env):
    e = env()
    p = e.page
    open_tab(p, "set")
    assert p.input_value("#setAlpha") == "1.00"
    assert p.evaluate("() => [...document.querySelectorAll('#setWGrid [data-w]')].map(i => i.value)") \
        == ["8", "12", "24", "36", "12", "8"]
    assert "16 / 24 / 48 / 72 / 24 / 16" in p.evaluate(
        "() => document.getElementById('setWLive').textContent")

    p.fill("#setAlpha", "1.5")
    p.dispatch_event("#setAlpha", "change")
    p.wait_for_timeout(350)
    assert p.evaluate("() => window.__xt.state().cfg.spanMode") == "alpha"
    assert p.evaluate("() => window.__xt.gearBase().d") == pytest.approx(1.5 * 47.98, abs=0.7)

    p.fill("#setWGrid [data-w='3']", "50")
    p.dispatch_event("#setWGrid [data-w='3']", "change")
    p.wait_for_timeout(350)
    assert p.evaluate("() => window.__xt.state().cfg.wPct") == [8, 12, 24, 50, 12, 8]
    assert "→ " in p.evaluate("() => document.getElementById('setWLive').textContent")


def test_settings_reset_weights(env):
    e = env()
    p = e.page
    open_tab(p, "set")
    p.fill("#setWGrid [data-w='0']", "30")
    p.dispatch_event("#setWGrid [data-w='0']", "change")
    p.wait_for_timeout(320)
    assert p.evaluate("() => window.__xt.state().cfg.wPct")[0] == 30
    p.click("#btnWReset")
    p.wait_for_timeout(350)
    assert p.evaluate("() => window.__xt.state().cfg.wPct") == [8, 12, 24, 36, 12, 8]
    assert p.evaluate("() => [...document.querySelectorAll('#setWGrid [data-w]')].map(i => i.value)") \
        == ["8", "12", "24", "36", "12", "8"]


# ---------------------------------------------------------------- 15 窄屏

@pytest.mark.parametrize("width", [320, 390])
def test_settings_fit_narrow_screen(env, width):
    e = env(width=width)
    p = e.page
    open_tab(p, "set")
    p.wait_for_timeout(200)
    box = p.evaluate("""() => {
        const g = document.getElementById('setWGrid');
        const items = [...g.querySelectorAll('input')].map(i => i.getBoundingClientRect());
        const card = g.closest('.card').getBoundingClientRect();
        const span = document.getElementById('setSpanLive');
        return {
            overflow: items.some(r => r.right > card.right + 0.5 || r.left < card.left - 0.5),
            widths: items.map(r => Math.round(r.width)),
            /* 数字得看得全：旧样式被 .fld input 的 12px 内边距盖掉，320 下「36」只剩「3」 */
            clipped: [...g.querySelectorAll('input')]
                .filter(i => i.scrollWidth > i.clientWidth + 1).map(i => i.value),
            spanClipped: !!span && span.scrollWidth > span.clientWidth + 1,
            docScroll: document.documentElement.scrollWidth,
            win: window.innerWidth,
        };
    }""")
    assert box["overflow"] is False, box
    assert min(box["widths"]) >= 34, box
    assert box["clipped"] == [], box
    assert box["spanClipped"] is False, box
    assert box["docScroll"] <= box["win"] + 1, box


# ---------------------------------------------------------------- 16 卡片口径

def test_card_shows_span_and_alpha(env):
    e = env()
    p = e.page
    open_tab(p, "plan")
    p.evaluate("() => window.__xt.setTargetT(3900)")
    p.wait_for_timeout(320)
    txt = p.evaluate("() => document.getElementById('avgCard').textContent.replace(/\\s+/g,' ')")
    assert "档距" in txt and "74" in txt, txt
    assert "α" in txt and "1.54" in txt, txt
    assert "跨度" in txt and "-13.0" in txt and "-20.9" in txt, txt
    assert "目标 3900" in txt, txt


def test_align_button_snaps_alpha_to_target(env):
    e = env()
    p = e.page
    open_tab(p, "plan")
    p.evaluate("() => window.__xt.setTargetT(3900)")
    p.evaluate("() => window.__xt.setAlpha(2.0)")
    p.wait_for_timeout(320)
    assert p.evaluate("() => window.__xt.gearMeta().avgAll") == pytest.approx(3843.2, abs=1.0)
    assert p.evaluate("() => !!document.querySelector('#avgCard [data-align]')") is True

    p.click("#avgCard [data-align]")
    p.wait_for_timeout(380)
    assert p.evaluate("() => window.__xt.state().cfg.spanMode") == "alpha"
    assert p.evaluate("() => window.__xt.state().cfg.alpha") == pytest.approx(1.54, abs=0.02)
    assert p.evaluate("() => window.__xt.gearMeta().avgAll") == pytest.approx(3900, abs=1.0)
    assert p.evaluate("() => !!document.querySelector('#avgCard [data-align]')") is True, \
        "按钮常驻：对齐后不收起"
    assert "已对齐" in p.evaluate(
        "() => document.getElementById('toast').textContent")
