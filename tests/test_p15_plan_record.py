"""P15 · 计划模式也能记账（bug 修复）。

起因：`openSheet()` 在计划模式下一句 `toast("计划模式不记账")` 直接返回，
于是「持仓汇总」和「买入记录」永远是空的 —— 而建仓页的说明又让用户
「买了到持仓页记一笔」，两边自相矛盾。

修法：放开记账；记账表单在计划模式下多一个「关联档位」下拉，
保存后用 syncMarksFromBuys() 把 buys 对账回 gearMarks：
  · 有记账的档，克重与成本按记录重算（改一笔、删一笔都会跟着变）
  · 没记账的档，手工标记原样保留
"""
import math

import pytest

from conftest import CFG_EMPTY

H = 4700.0
W = 200.0
PX = 4030.0
PLAN = [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]


def bars_calm(n=240, px=PX, amp=24.0):
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
        mk.setdefault("bars", bars_calm())
        mk.setdefault("price", PX)
        return make_env(cfg=base, **mk)
    return _f


def to_pos(p):
    p.evaluate("""() => {
        if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
        window.scrollTo(0, 0);
    }""")
    p.wait_for_timeout(320)
    p.click('.tabbar button[data-tab="pos"]')
    p.wait_for_timeout(420)


def buys(p):
    return p.evaluate("() => (window.__xt.state().cfg.buys || [])")


def marks(p):
    return p.evaluate("() => (window.__xt.state().cfg.gearMarks || {})")


def record(p, gear="第2档", gram=24, price=905):
    """打开「记一笔」→ 选档 → 填克重单价 → 保存。"""
    p.click("#btnAddBuy")
    p.wait_for_timeout(260)
    if gear:
        p.select_option("#gGear", gear)
    p.fill('[name="g_gram"]', str(gram))
    p.fill('[name="g_price"]', str(price))
    p.click("#btnSave")
    p.wait_for_timeout(420)


# ---------------------------------------------------------------- 1 能打开

def test_b1_sheet_opens(env):
    """计划模式点「+ 记一笔」：弹表单，不再弹「计划模式不记账」。"""
    p = env().page
    to_pos(p)
    p.click("#btnAddBuy")
    p.wait_for_timeout(300)
    assert "on" in p.get_attribute("#sheet", "class")
    assert "不记账" not in p.inner_text("#toast")


def test_b2_gear_select(env):
    """计划模式下表单出现「关联档位」下拉，选项 = 档数 + 1（含「不关联」）。"""
    p = env().page
    to_pos(p)
    p.click("#btnAddBuy")
    p.wait_for_timeout(300)
    assert p.evaluate("() => document.getElementById('gearLinkFld').style.display") != "none"
    opts = p.evaluate("() => [...document.querySelectorAll('#gGear option')].map(o => o.value)")
    assert opts[0] == ""
    assert opts[1:] == ["第%d档" % i for i in range(1, 7)]
    assert "待补" in p.inner_text("#gGear")


def test_b3_ledger_mode_hides_select(env):
    """账本模式不显示这个下拉（档位归属对账本模式没意义）。"""
    p = env(cfg(planOnly=False)).page
    to_pos(p)
    p.click("#btnAddBuy")
    p.wait_for_timeout(300)
    assert p.evaluate("() => document.getElementById('gearLinkFld').style.display") == "none"


# ---------------------------------------------------------------- 2 记账

def test_b4_writes_buys(env):
    """保存后真的写进买入记录，标签用档名。"""
    p = env().page
    to_pos(p)
    record(p)
    rows = buys(p)
    assert len(rows) == 1
    assert rows[0]["gram"] == 24
    assert rows[0]["priceCny"] == 905
    assert rows[0]["gear"] == "第2档"
    assert rows[0]["name"] == "第2档"


def test_b5_syncs_marks(env):
    """同一笔同步到档位：克重、成本、来源标记都对上。"""
    p = env().page
    to_pos(p)
    record(p)
    m = marks(p)["2"]
    assert m["g"] == 24
    assert m["cost"] > 0
    assert m["fromBuys"] == 1


def test_b6_gear_state(env):
    """档位状态跟着变：第 2 档计划 24g 买满 → 待补 0。"""
    p = env().page
    to_pos(p)
    record(p)
    st = p.evaluate("() => window.__xt.gearState(2)")
    assert st["bought"] == pytest.approx(24, abs=0.05)
    assert st["pending"] == pytest.approx(0, abs=0.05)


def test_b7_pos_summary(env):
    """持仓汇总不再是「还没有买入记录」，能算出克重与均价。"""
    p = env().page
    to_pos(p)
    before = p.inner_text("#posBox")
    assert "还没有买入记录" in before
    record(p)
    after = p.inner_text("#posBox")
    assert "还没有买入记录" not in after
    assert "24.0 g" in after


def test_b8_buy_list(env):
    """买入记录列表出现这条。"""
    p = env().page
    to_pos(p)
    record(p, gram=12, price=910)
    txt = p.inner_text("#buyList")
    assert "12" in txt and "910.00" in txt


# ---------------------------------------------------------------- 3 对账

def test_b9_unlinked_keeps_marks_empty(env):
    """选「不关联」：只记持仓，档位一点不动。"""
    p = env().page
    to_pos(p)
    record(p, gear=None, gram=10, price=900)
    assert len(buys(p)) == 1
    assert marks(p) == {}


def test_b10_edit_updates_marks(env):
    """改大克重 → 档位那份跟着变大（记录是唯一口径）。"""
    p = env().page
    to_pos(p)
    record(p, gram=10, price=900)
    assert marks(p)["2"]["g"] == 10
    rid = buys(p)[0]["id"]
    p.click('#buyList [data-buy="%s"]' % rid)
    p.wait_for_timeout(300)
    p.fill('[name="g_gram"]', "30")
    p.click("#btnSave")
    p.wait_for_timeout(420)
    assert marks(p)["2"]["g"] == pytest.approx(30, abs=0.05)
    assert marks(p)["2"]["fromBuys"] == 1


def test_b12_delete_clears_marks(env):
    """删除这笔 → 档位那份一起撤掉（fromBuys 的残留清理）。"""
    p = env().page
    to_pos(p)
    record(p, gram=24, price=905)
    assert "2" in marks(p)
    rid = buys(p)[0]["id"]
    p.evaluate("() => { window.confirm = () => true; }")
    p.click('#buyList [data-buy="%s"]' % rid)
    p.wait_for_timeout(300)
    p.click("#btnDelBuy")
    p.wait_for_timeout(420)
    assert len(buys(p)) == 0
    assert "2" not in marks(p)


def test_b13_manual_mark_kept(env):
    """没记账的档，手工标记原样保留 —— 对账只碰有记录的档。"""
    p = env(cfg(gearMarks={"5": {"g": 20, "cost": 0, "sold": 0, "proceeds": 0,
                                 "at": 1700000000000}})).page
    to_pos(p)
    record(p, gear="第2档", gram=24, price=905)
    m = marks(p)
    assert m["5"]["g"] == 20
    assert "2" in m


# ---------------------------------------------------------------- 4 账本模式与切换

def test_b14_ledger_mode_untouched(env):
    """账本模式记账：只写买卖记录，不碰 gearMarks（老行为不变）。"""
    p = env(cfg(planOnly=False)).page
    to_pos(p)
    record(p, gear=None, gram=10, price=900)
    assert len(buys(p)) == 1
    assert marks(p) == {}


def test_b15_switch_to_plan_syncs(env):
    """账本模式里记过账（带档位），切到计划模式时自动对账，档位不会一片空白。"""
    p = env(cfg(planOnly=False, buys=[
        {"id": "b9", "name": "第3档", "gram": 48, "priceCny": 880.0,
         "time": "2026-10-01T02:00:00.000Z", "note": "", "gear": "第3档"}])).page
    assert marks(p) == {}
    p.evaluate("() => window.__xt.setPlanOnly(true)")
    p.wait_for_timeout(400)
    m = marks(p)
    assert m["3"]["g"] == 48
    assert m["3"]["fromBuys"] == 1


def test_b16_no_page_error(env):
    """开表单、选档、保存、再编辑，全程无 JS 异常。"""
    errs = []
    p = env().page
    p.on("pageerror", lambda e: errs.append(str(e)))
    to_pos(p)
    record(p, gram=24, price=905)
    rid = buys(p)[0]["id"]
    p.click('#buyList [data-buy="%s"]' % rid)
    p.wait_for_timeout(280)
    p.click("#sheetClose")
    p.wait_for_timeout(200)
    assert errs == [], errs


@pytest.mark.parametrize("width", [390, 320])
def test_b17_no_overflow(env, width):
    """表单在窄屏不撑宽页面。"""
    p = env(width=width).page
    to_pos(p)
    p.click("#btnAddBuy")
    p.wait_for_timeout(320)
    assert p.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")


def test_b18_from_mark_sheet(env):
    """标记面板点「改成记一笔」：档位带过去、克重带过去，直接就能保存。"""
    p = env().page
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(460)
    p.click('#ladder [data-mark="第3档"]')
    p.wait_for_timeout(280)
    p.fill("#markGram", "18")
    p.click("#markToBuy")
    p.wait_for_timeout(320)
    assert "on" in p.get_attribute("#sheet", "class")
    assert "on" not in p.get_attribute("#sheetMark", "class")
    assert p.input_value("#gGear") == "第3档"
    assert p.input_value('[name="g_gram"]') == "18"
    p.fill('[name="g_price"]', "880")
    p.click("#btnSave")
    p.wait_for_timeout(420)
    assert marks(p)["3"]["g"] == 18
    assert buys(p)[0]["gear"] == "第3档"


def test_b19_mark_sheet_still_light(env):
    """「确认买入」仍然只标记、不写账本（P4 的老契约不能被这期带歪）。"""
    p = env().page
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(460)
    p.click('#ladder [data-mark="第4档"]')
    p.wait_for_timeout(280)
    p.fill("#markGram", "20")
    p.click("#markSave")
    p.wait_for_timeout(380)
    assert marks(p)["4"]["g"] == 20
    assert "fromBuys" not in marks(p)["4"]
    assert len(buys(p)) == 0


# ---------------------------------------------------------------- 5 重置

def test_b20_reset_clears_marks(env):
    """「重置档位状态」连 gearMarks 一起清 —— 弹窗写着「清空全部…档位状态」，
    以前只清了 buys/sells/gearsDone，档位还挂着「已买入」。"""
    p = env(cfg(gearMarks={"1": {"g": 16, "cost": 0, "sold": 0, "proceeds": 0,
                                 "at": 1700000000000}})).page
    assert "1" in marks(p)
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    if p.locator("#mtToggle").count():
        p.click("#mtToggle")
        p.wait_for_timeout(280)
    p.evaluate("() => { window.confirm = () => true; }")
    p.click("#btnReset")
    p.wait_for_timeout(520)
    assert marks(p) == {}
    assert p.evaluate("() => window.__xt.gearState(1).bought") == 0
