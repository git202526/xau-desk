"""P27 · 标记已买的双币口径。

起因：标记面板上的「本次金额」是**算出来的**（`克重 ÷ 31.1035 × $/oz`），
所以系统里根本没有「我实际花了多少人民币」这个事实，只有一个能被今天的汇率随时重算的美元数。
8 月买的 200g，11 月改一次汇率，成本就变了。

本期把一次成交拆成三个互不推导的事实：克重、当时的 `$/oz`、实付人民币。
人民币成为所有「钱」的唯一口径；美元价只用来和档位价比「买贵了多少」。

两组关键边界：
- `costCny = 0` 表示「不知道花了多少钱」，`gearRealized` 返回 **null 而不是 0**
  —— 0 会被读成「不赚不亏」，两个结论必须分得开。
- 老数据不猜汇率：账本记录的 `priceCny` 是元/克，克 × 元/克 = 元，能精确补；
  纯手工标记补不出来，留「—」。
"""
import math

import pytest

from conftest import CFG_EMPTY

H = 4700.0
TOTAL = 200.0
PX = 4030.0
OZ = 31.1035
BASE = [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]


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


def usd(gram, oz):
    return round(gram * oz / OZ, 2)


def mark(g, cost_cny=0, sold=0, proceeds_cny=0, cost_oz=0, sell_oz=0):
    return {"g": g, "cost": usd(g, cost_oz) if cost_oz else 0,
            "costCny": cost_cny,
            "sold": sold, "proceeds": usd(sold, sell_oz) if sell_oz else 0,
            "proceedsCny": proceeds_cny, "at": 1}


def all_marks(unit_cny=900.0):
    """六档全买满，人民币单价统一 —— 让合计类断言有干净的基数。"""
    return {str(i): mark(g, round(g * unit_cny, 2)) for i, g in enumerate(BASE, 1)}


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, swingGears=[1, 2],
             gearMarks={"1": mark(16, 16000, cost_oz=4000)})
    c.update(kw)
    c["bars"] = c.get("bars") or bars_calm()
    return c


@pytest.fixture
def env(make_env):
    def _f(c=None, price=PX, **kw):
        return make_env(cfg=c if c is not None else cfg(**kw), price=price)
    return _f


def marks_raw(p):
    return p.evaluate("() => JSON.parse(JSON.stringify(window.__xt.state().cfg.gearMarks || {}))")


def gear(p, i=1):
    return p.evaluate("(i) => window.__xt.gearState(i)", i)


def go_plan(p):
    """档位表在「计划」页（tab=plan），不在持仓页。"""
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(300)


def go_pos(p):
    p.click('.tabbar button[data-tab="pos"]')
    p.wait_for_timeout(300)


def open_sheet(p, name="第1档"):
    go_plan(p)
    p.click('#ladder .body[data-gear="%s"]' % name)
    p.wait_for_timeout(280)
    return p


def toast_text(p):
    return p.eval_on_selector("#toast", "e => e.textContent")


# ---------------------------------------------------------------- 1 写入

def test_w1_buy_keeps_both_currencies(env):
    """一次买入同时记下两种币种：人民币是用户填的，美元由 $/oz 推导。"""
    p = env(gearMarks={}).page
    p.evaluate("() => window.__xt.markBuy(1, 26.4, 3919.37, 23952)")
    p.wait_for_timeout(220)
    m = marks_raw(p)["1"]
    assert m["costCny"] == pytest.approx(23952, abs=0.01)
    # 26.4g ÷ 31.1035 × 3919.37 = 3326.68 美元 —— 只作参考，不进任何人民币统计
    assert m["cost"] == pytest.approx(usd(26.4, 3919.37), abs=0.02)


def test_w2_two_unit_prices_do_not_interfere(env):
    """两套均价各算各的：¥/g 用 costCny，$/oz 用 cost。"""
    p = env(gearMarks={}).page
    p.evaluate("() => window.__xt.markBuy(1, 26.4, 3919.37, 23952)")
    p.wait_for_timeout(200)
    m = marks_raw(p)["1"]
    assert m["costCny"] / m["g"] == pytest.approx(907.27, abs=0.02)
    assert m["cost"] / m["g"] * OZ == pytest.approx(3919.37, abs=3)


def test_w3_buy_without_amount_continues_unit_price(env):
    """不传金额时按**本档历史单价**延续，不按当前汇率补。"""
    p = env(gearMarks={"1": mark(10, 10000)}).page
    p.evaluate("() => window.__xt.markBuy(1, 10, 4000)")
    p.wait_for_timeout(220)
    m = marks_raw(p)["1"]
    assert m["g"] == pytest.approx(20, abs=0.01)
    assert m["costCny"] == pytest.approx(20000, abs=0.01)   # 1000/g × 20g


def test_w4_buy_without_amount_and_no_history_stays_unknown(env):
    """本档从没填过金额 → costCny 保持 0。汇率有值也**绝不用它兜**。"""
    p = env(gearMarks={}).page
    fx = p.evaluate("() => Number(window.__xt.state().fx)")
    assert fx > 0                         # 汇率是有的，故意不用
    p.evaluate("() => window.__xt.markBuy(1, 16, 4000)")
    p.wait_for_timeout(220)
    m = marks_raw(p)["1"]
    assert m["g"] == pytest.approx(16, abs=0.01)
    assert m["costCny"] == 0
    assert m["cost"] > 0                  # 美元那边照常推导，不影响人民币口径


def test_w5_amount_accumulates_to_cent(env):
    """两次买入的人民币金额精确累加到 0.01。"""
    p = env(gearMarks={}).page
    p.evaluate("() => window.__xt.markBuy(1, 5, 4000, 10000.11)")
    p.wait_for_timeout(150)
    p.evaluate("() => window.__xt.markBuy(1, 5, 4000, 10000.22)")
    p.wait_for_timeout(200)
    assert marks_raw(p)["1"]["costCny"] == pytest.approx(20000.33, abs=0.001)


# ---------------------------------------------------------------- 2 已实现

def test_r1_realized_is_cny(env):
    """已实现 = 到账 − 卖掉那部分的成本。"""
    p = env().page
    v = p.evaluate("""() => window.__xt.gearRealized(
      {g:100, sold:40, costCny:90000, proceedsCny:40000})""")
    assert v == pytest.approx(4000, abs=0.01)     # 40000 − 40×900


def test_r2_unknown_cost_returns_null_not_zero(env):
    """成本没填 → null。「不知道」不能显示成「不赚不亏」。"""
    p = env().page
    v = p.evaluate("""() => window.__xt.gearRealized(
      {g:100, sold:40, costCny:0, proceedsCny:40000})""")
    assert v is None


def test_r3_known_cost_but_no_proceeds_is_zero(env):
    """成本已知、卖出没填到账 → 0（可判定「没填卖出金额」）。"""
    p = env().page
    v = p.evaluate("""() => window.__xt.gearRealized(
      {g:100, sold:40, costCny:90000, proceedsCny:0})""")
    assert v == 0


# ---------------------------------------------------------------- 3 面板

def test_u1_amount_input_is_editable_number(env):
    """金额框：可编辑、数字键盘、带 ¥ 标签。"""
    p = open_sheet(env().page)
    assert p.eval_on_selector("#markAmt", "e => e.hasAttribute('readonly')") is False
    assert p.eval_on_selector("#markAmt", "e => e.type") == "number"
    lbl = p.eval_on_selector("#markAmtLb", "e => e.innerText")
    assert "¥" in lbl, lbl


def test_u2_blank_amount_is_rejected(env):
    """金额必填：留空保存 → toast 说明，标记一点没变。"""
    p = open_sheet(env().page)
    before = marks_raw(p)
    p.fill("#markAmt", "")
    p.click("#markSave")
    p.wait_for_timeout(300)
    assert "实付金额" in toast_text(p)
    assert marks_raw(p) == before
    assert p.eval_on_selector("#sheetMark", "e => e.classList.contains('on')") is True


def test_u3_amount_follows_gram_by_unit_price(env):
    """改克重 → 金额按本档单价重算（16g × ¥1000/g = 16000）。"""
    p = open_sheet(env().page)
    assert float(p.eval_on_selector("#markAmt", "e => e.value")) == pytest.approx(16000, abs=0.5)
    p.fill("#markGram", "8")
    p.wait_for_timeout(200)
    assert float(p.eval_on_selector("#markAmt", "e => e.value")) == pytest.approx(8000, abs=0.5)


def test_u4_manual_amount_is_not_overwritten(env):
    """用户自己填过金额后，改克重不覆盖 —— 那是真实扣款，不是推导值。"""
    p = open_sheet(env().page)
    p.fill("#markAmt", "5000")
    p.wait_for_timeout(150)
    p.fill("#markGram", "4")
    p.wait_for_timeout(200)
    assert float(p.eval_on_selector("#markAmt", "e => e.value")) == pytest.approx(5000, abs=0.01)


def test_u5_implied_fx_warns_only_on_deviation(env):
    """隐含汇率只在偏得离谱时提示；正常值（含 3% 点差）完全不占位。

    阈值是 10% 而不是 2%：购汇时点与点差本来就有 2~4% 的差异，
    2% 会让提示在正常数据上常驻 —— 那就等于没有提示。
    """
    p = open_sheet(env().page)
    gram = float(p.eval_on_selector("#markGram", "e => e.value"))
    px = float(p.eval_on_selector("#markPx", "e => e.value"))
    usd_amt = gram / OZ * px

    p.fill("#markAmt", str(round(usd_amt * 7.10)))
    p.wait_for_timeout(200)
    assert p.eval_on_selector("#markFxHint", "e => e.textContent").strip() == ""

    # 正常的换汇点差（-3%）不该触发
    p.fill("#markAmt", str(round(usd_amt * 7.10 * 0.97)))
    p.wait_for_timeout(200)
    assert p.eval_on_selector("#markFxHint", "e => e.textContent").strip() == ""

    # 把美元金额填进人民币框 → 隐含汇率 ≈ 1.00
    p.fill("#markAmt", str(round(usd_amt)))
    p.wait_for_timeout(200)
    txt = p.eval_on_selector("#markFxHint", "e => e.textContent")
    assert "隐含汇率" in txt and "确认金额不是美元" in txt, txt


# ---------------------------------------------------------------- 4 迁移

def test_m1_ledger_backfills_cny_without_fx(env):
    """账本记录的 priceCny 是元/克：克 × 元/克 = 元，汇率没到也能补准。

    v2 只往**已有的标记**上补 costCny，不重算 g / cost / sold ——
    否则用户手动撤销过的档位会从记录里重新长出来。
    """
    old_mark = {"g": 10, "cost": 2000, "sold": 0, "proceeds": 0, "at": 1}
    c = cfg(gearMarks={"1": dict(old_mark)}, schemaVersion=0, migratedV7=True,
            buys=[{"id": "b0", "name": "底仓", "gram": 10, "priceCny": 900.5,
                   "time": "2026-08-01T02:00:00.000Z", "note": "", "gear": "第1档"}])
    p = env(c=c).page
    p.evaluate("""() => {
      var S = window.__xt.state();
      S.fx = 0;                       // 汇率没到
      S.cfg.gearMarks["1"].costCny = 0;
      window.__xt.migrateMarksV8();
    }""")
    p.wait_for_timeout(200)
    m = marks_raw(p)["1"]
    assert m["costCny"] == pytest.approx(9005, abs=0.01)    # 10 × 900.5
    assert m["g"] == 10 and m["cost"] == 2000               # 执行进度一个字节没动


def test_m2_pure_marks_stay_unknown(env):
    """纯手工标记推不出人民币成本 → 留 0，界面标「¥未填」。"""
    p = env(gearMarks={"1": mark(16, 0, cost_oz=4000)}).page
    assert gear(p, 1)["realized"] is None
    go_pos(p)
    assert "¥未填" in p.eval_on_selector("#ladder", "e => e.innerText")


def test_m3_schema_1_to_3_and_idempotent(env):
    """schemaVersion 1 → 3（v2 补成本、v3 建账户）；连跑三次标记一个字节都不变。"""
    c = cfg(schemaVersion=1, migratedV7=True,
            gearMarks={"1": {"g": 16, "cost": 2000, "sold": 0, "proceeds": 0, "at": 1}},
            buys=[{"id": "b0", "name": "底仓", "gram": 10, "priceCny": 900,
                   "time": "2026-08-01T02:00:00.000Z", "note": "", "gear": "第1档"}])
    p = env(c=c).page
    assert p.evaluate("() => window.__xt.state().cfg.schemaVersion") == 3
    assert marks_raw(p)["1"]["costCny"] > 0          # v2 已经补过

    first = marks_raw(p)
    for _ in range(3):
        p.evaluate("() => window.__xt.runMigrations()")
        p.wait_for_timeout(120)
    assert marks_raw(p) == first


# ---------------------------------------------------------------- 5 关联口径

def test_c1_ladder_tag_has_currency(env):
    """阶梯表循环档标签必须是 ¥，不能是一个没有币种的裸数字。"""
    p = env(gearMarks={"1": mark(24, 21600, sold=8, proceeds_cny=8000, cost_oz=4000,
                                 sell_oz=4400)}).page
    go_plan(p)
    tag = p.eval_on_selector("#ladder .swing", "e => e.innerText")
    assert "¥" in tag, tag
    assert "$" not in tag, tag
    assert "800" in tag, tag


def test_c2_review_footer_is_cny_per_gram(env):
    """复盘页脚：已实现 ¥、每克拉低 ¥/g，不再出现 $/oz。"""
    mk = all_marks()
    mk["1"]["sold"] = 8
    mk["1"]["proceedsCny"] = 8000
    mk["1"]["proceeds"] = usd(8, 4400)
    p = env(gearMarks=mk).page
    go_pos(p)
    p.eval_on_selector('#posReview [data-rv="h"]', "e => e.click()")
    p.wait_for_timeout(260)
    txt = p.eval_on_selector("#posReview", "e => e.innerText")
    assert "已实现" in txt and "¥" in txt, txt
    assert "每克拉低" in txt and "/g" in txt, txt
    assert "$/oz" not in txt, txt


def test_c3_allot_splits_cny_by_gram(env):
    """摊派卖出：人民币按各档摊到的克重比例分下去，总额精确。"""
    p = env(gearMarks=all_marks(900)).page
    out = p.evaluate("() => window.__xt.allotSell(100, 4000, 90000)")
    p.wait_for_timeout(200)
    mk = marks_raw(p)
    total = sum(v["proceedsCny"] for v in mk.values())
    assert total == pytest.approx(90000, abs=0.05)
    # 100g 按 16/24/48/72/24/16 的比例摊 → 8/12/24/36/12/8
    assert mk["1"]["proceedsCny"] == pytest.approx(7200, abs=0.05)
    assert out["hit"] == 6


def test_c4_unallot_restores_cny(env):
    """反摊：人民币同步退回摊派前（误差 < 0.02）。"""
    p = env(gearMarks=all_marks(900)).page
    p.evaluate("""() => {
      var r = window.__xt.allotSell(100, 4000, 90000);
      window.__xt.unallotSell(r.lot, 100);
    }""")
    p.wait_for_timeout(220)
    mk = marks_raw(p)
    assert sum(v["proceedsCny"] for v in mk.values()) == pytest.approx(0, abs=0.02)
    assert sum(v["sold"] for v in mk.values()) == pytest.approx(0, abs=0.02)


def test_c5_drip_amount_is_cny_or_null(env):
    """兜底预估金额 = 本档单价 × 克重；本档没填单价 → null，不编数字。"""
    p = env(gearMarks={"1": mark(8, 8000)}, dripDays=30).page
    p.evaluate("""() => {
      var S = window.__xt.state();
      S.cfg.dripAt = {"1": Date.now() - 40 * 86400000};
      window.__xt.compute();
    }""")
    info = p.evaluate("() => window.__xt.dripInfo(1)")
    assert info["gram"] == pytest.approx(4, abs=0.01)       # 待补 8g × 50%
    assert info["amt"] == pytest.approx(4000, abs=0.5)      # 1000/g × 4g

    p2 = env(gearMarks={"1": mark(8, 0, cost_oz=4000)}, dripDays=30).page
    p2.evaluate("""() => {
      var S = window.__xt.state();
      S.cfg.dripAt = {"1": Date.now() - 40 * 86400000};
      window.__xt.compute();
    }""")
    assert p2.evaluate("() => window.__xt.dripInfo(1).amt") is None


# ---------------------------------------------------------------- 6 布局

def test_l1_narrow_panel(env):
    """320 宽：面板不横向溢出，金额框整个在视口内。"""
    p = open_sheet(env(width=320).page)
    over = p.evaluate("""() => ({
      de: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      bd: document.body.scrollWidth - document.body.clientWidth
    })""")
    assert over["de"] <= 1, over
    assert over["bd"] <= 1, over
    box = p.eval_on_selector("#markAmt", """e => {
      const r = e.getBoundingClientRect();
      return {l: r.left, r: r.right, vw: document.documentElement.clientWidth};
    }""")
    assert box["l"] >= -1 and box["r"] <= box["vw"] + 1, box
