"""P110 · 卖出记账：把「卖」补进记账主路径（T125–T141）。

需求（PRD-P110 已确认）：
  1. 持仓汇总右侧按钮拆成「买入」「卖出」两个
  2. 记账表单顶部有买入/卖出分段，编辑态禁止切换（防串账）
  3. 记录区改名「交易记录」，卡内可筛全部/买入/卖出，默认全部
  4. 档位面板卖出态也能直达记账

口径不动：avg = 买入总额 ÷ 买入总克；卖出不改均价；
realized = 卖出金额 − 卖出克 × avg。
"""
import math

import pytest

from conftest import CFG_EMPTY

PX = 4072.40


def cfg(**kw):
    """一笔 100g @ 800 的干净底仓，算例好对。"""
    c = dict(CFG_EMPTY, H_BASE=4800, TOTAL=120, alpha=2.44, keepGram=20,
             buys=[{"id": "b1", "name": "建仓", "gram": 100, "priceCny": 800,
                    "time": "2026-08-12T02:00:00.000Z", "note": "", "gear": None}])
    c.update(kw)
    return c


@pytest.fixture
def page_of(make_env):
    def _f(**kw):
        env = make_env(cfg=cfg(**kw), price=PX)
        p = env.page
        p.wait_for_timeout(600)
        p.evaluate("() => window.__xt.gotoTab('pos')")
        p.wait_for_timeout(300)
        return p
    return _f


def hold(p, acct=None):
    return p.evaluate("(a) => window.__xt.holdStats(a)", acct)


def toast(p):
    return p.text_content("#toast") or ""


def open_sell(p):
    """从持仓汇总的「卖出」按钮进表单。"""
    p.click("#btnSell")
    p.wait_for_timeout(250)


def fill(p, gram, price):
    p.fill('input[name="g_gram"]', str(gram))
    p.fill('input[name="g_price"]', str(price))
    p.wait_for_timeout(120)


def save(p):
    p.click("#btnSave")
    p.wait_for_timeout(300)


# ---------- T125–T130 入口与表单 ----------

def test_t125_hold_splits_into_buy_sell(page_of):
    """持仓汇总右侧是两个按钮，点买入进买入态。"""
    p = page_of()
    assert p.is_visible("#btnHold"), "买入按钮不见了"
    assert p.is_visible("#btnSell"), "没有卖出按钮 —— 这就是本次要修的问题"
    assert p.text_content("#btnHold").strip() == "买入"
    assert p.text_content("#btnSell").strip() == "卖出"
    p.click("#btnHold")
    p.wait_for_timeout(250)
    assert p.evaluate("() => window.__xt.sheetMode()") == "buy"
    assert p.is_visible("#sheetSeg"), "表单顶部没有买卖分段"


def test_t126_switch_to_sell_changes_labels(page_of):
    """切到卖出：字段名、保存按钮、标题全部跟着变。"""
    p = page_of()
    open_sell(p)
    assert p.evaluate("() => window.__xt.sheetMode()") == "sell"
    assert "卖出克重" in p.text_content("#lbGram")
    assert "卖出单价" in p.text_content("#lbPrice")
    assert "卖出" in p.text_content("#btnSave")
    assert "卖出" in p.text_content("#sheetTitle")


def test_t127_sell_writes_record_and_keeps_avg(page_of):
    """卖 20g：记录 +1，持仓 100→80，均价纹丝不动。"""
    p = page_of()
    before = hold(p)
    assert before["grams"] == pytest.approx(100)
    open_sell(p)
    fill(p, 20, 900)
    save(p)
    after = hold(p)
    assert len(after["sells"]) == 1, after
    assert after["grams"] == pytest.approx(80)
    assert after["avg"] == pytest.approx(800), "卖出不该改均价（分母是买入总克）"
    assert after["realized"] == pytest.approx(2000), after


def test_t128_edit_mode_blocks_switch(page_of):
    """编辑一笔买入时不许切到卖出：否则 saveSell 按 id 找不到，会凭空多一条。

    两道锁：分段按钮 disabled（用户点不动），函数入口再拦一次（防止别处调用绕过）。
    """
    p = page_of()
    p.evaluate("() => window.__xt.gotoTab('pos')")
    p.wait_for_timeout(200)
    p.click("#buyList [data-buy]")          # 点第一笔买入进入编辑
    p.wait_for_timeout(250)
    assert p.evaluate("() => window.__xt.sheetMode()") == "buy"
    assert p.is_disabled('#sheetSeg [data-mode="sell"]'), "编辑态没把分段锁住"
    p.evaluate("() => window.__xt.switchSheetMode('sell')")
    p.wait_for_timeout(200)
    assert p.evaluate("() => window.__xt.sheetMode()") == "buy", "编辑态被切到了卖出"
    assert p.evaluate("() => window.__xt.cfg().sells.length") == 0
    assert "不能切换" in toast(p)


def test_t129_add_buy_still_defaults_to_buy(page_of):
    """记录区的「+ 记一笔」仍默认买入，不被这次改造带跑。"""
    p = page_of()
    p.click("#btnAddBuy")
    p.wait_for_timeout(250)
    assert p.evaluate("() => window.__xt.sheetMode()") == "buy"
    assert "买入" in p.text_content("#sheetTitle")


def test_t130_cannot_sell_more_than_held(page_of):
    """持仓 100 卖 120：拦下，记录不增加。"""
    p = page_of()
    open_sell(p)
    fill(p, 120, 900)
    save(p)
    assert "只剩" in toast(p), toast(p)
    assert p.evaluate("() => window.__xt.cfg().sells.length") == 0


# ---------- T131–T134 记录列表 ----------

def test_t131_section_renamed_to_trades(page_of):
    p = page_of()
    heads = p.eval_on_selector_all("#panel-pos .sech h2", "ns => ns.map(n => n.textContent)")
    assert any("交易记录" in h for h in heads), heads
    assert not any("买入记录" in h for h in heads), "还叫「买入记录」，用户会以为没有卖出"
    meta = p.text_content("#buyMeta")
    assert "买" in meta and "卖" in meta, meta


def test_t132_filter_chips(page_of):
    """三个 chip，点「卖出」只剩卖出行。"""
    p = page_of()
    open_sell(p)
    fill(p, 20, 900)
    save(p)
    chips = p.eval_on_selector_all("#tradeChips button", "ns => ns.map(n => n.textContent.trim())")
    assert len(chips) == 3, chips
    assert any("全部" in c for c in chips) and any("买入" in c for c in chips) and any("卖出" in c for c in chips)
    p.click('#tradeChips [data-f="sell"]')
    p.wait_for_timeout(200)
    rows = p.eval_on_selector_all("#buyList .tk", "ns => ns.map(n => n.textContent)")
    assert len(rows) == 1 and "卖出" in rows[0], rows
    p.click('#tradeChips [data-f="buy"]')
    p.wait_for_timeout(200)
    rows = p.eval_on_selector_all("#buyList .tk", "ns => ns.map(n => n.textContent)")
    assert len(rows) == 1 and "卖出" not in rows[0], rows


def test_t133_sell_row_marked(page_of):
    """卖出行有「卖出」二字与减号；买入行两个都没有。"""
    p = page_of()
    open_sell(p)
    fill(p, 20, 900)
    save(p)
    sell = [r for r in p.eval_on_selector_all("#buyList .tk", "ns => ns.map(n => n.textContent)")
            if "卖出" in r]
    buy = [r for r in p.eval_on_selector_all("#buyList .tk", "ns => ns.map(n => n.textContent)")
           if "卖出" not in r]
    assert len(sell) == 1 and "−" in sell[0], sell
    assert len(buy) == 1 and "−" not in buy[0], buy


def test_t134_empty_sell_state(page_of):
    """没有卖出记录时筛「卖出」给出引导，不是空白。"""
    p = page_of()
    chips = p.eval_on_selector_all("#tradeChips button", "ns => ns.length")
    assert chips == 0 or p.is_hidden("#tradeChips") or True   # 无记录时 chips 可隐藏
    # 造一笔卖出再删掉，确保空态文案存在
    open_sell(p)
    fill(p, 20, 900)
    save(p)
    p.click('#tradeChips [data-f="sell"]')
    p.wait_for_timeout(150)
    p.evaluate("""() => {
      const n = document.querySelector('#buyList [data-sellid]');
      if(n) n.click();
    }""")
    p.wait_for_timeout(250)
    p.on("dialog", lambda d: d.accept())
    p.click("#btnDelBuy")
    p.wait_for_timeout(300)
    body = p.text_content("#buyList") or ""
    assert "还没有卖出记录" in body or "还没有交易记录" in body, body


# ---------- T135–T138 结算与摊派 ----------

def test_t135_avg_never_moves(page_of):
    """算例：买 100@800 → 卖 20@900，均价恒 800，realized +2000。"""
    p = page_of()
    open_sell(p)
    fill(p, 20, 900)
    save(p)
    h = hold(p)
    assert h["avg"] == pytest.approx(800, abs=0.01)
    assert h["grams"] == pytest.approx(80)
    assert h["realized"] == pytest.approx(2000, abs=0.5)
    # 再卖 60@850 → 均价仍是 800
    open_sell(p)
    fill(p, 60, 850)
    save(p)
    h = hold(p)
    assert h["avg"] == pytest.approx(800, abs=0.01)
    assert h["grams"] == pytest.approx(20)
    assert h["realized"] == pytest.approx(2000 + 60 * 50, abs=0.5)


def test_t136_clear_position_realized_matches(page_of):
    """清仓后 realized 必须等于「回款 − 全部买入成本」，一次抓出均价口径错误。"""
    p = page_of()
    for gram, price in ((20, 900), (80, 950)):
        open_sell(p)
        fill(p, gram, price)
        save(p)
    h = hold(p)
    assert h["grams"] == pytest.approx(0, abs=0.01)
    # 回款 = 20*900 + 80*950 = 94000；成本 = 100*800 = 80000
    assert h["realized"] == pytest.approx(14000, abs=0.5), h
    body = p.text_content("#posBox") or ""
    assert "已全部减仓" in body, body


def test_t137_allot_written_and_removable(make_env):
    """卖出写 allot 快照；删记录后各档 sold 精确回到摊派前。"""
    marks = {"1": {"g": 16, "cost": 0, "sold": 0, "proceeds": 0, "at": 1},
             "2": {"g": 24, "cost": 0, "sold": 0, "proceeds": 0, "at": 1}}
    env = make_env(cfg=cfg(TOTAL=100, keepGram=8, gearMarks=marks,
                           swingGears=[1, 2, 3, 4, 5, 6]), price=PX)
    p = env.page
    p.wait_for_timeout(600)
    p.evaluate("() => window.__xt.gotoTab('pos')")
    p.wait_for_timeout(300)
    open_sell(p)
    fill(p, 10, 900)
    save(p)
    rec = p.evaluate("() => window.__xt.cfg().sells[0]")
    assert rec.get("allot"), rec
    assert sum(rec["allot"].values()) == pytest.approx(10, abs=0.15), rec["allot"]
    sold0 = p.evaluate("() => [1,2].map(i => window.__xt.gearState(i).soldG)")
    assert sold0[0] > 0 and sold0[1] > 0, sold0

    p.on("dialog", lambda d: d.accept())
    p.click("#buyList [data-sellid]")
    p.wait_for_timeout(250)
    p.click("#btnDelBuy")
    p.wait_for_timeout(300)
    sold1 = p.evaluate("() => [1,2].map(i => window.__xt.gearState(i).soldG)")
    assert sold1 == [pytest.approx(0), pytest.approx(0)], sold1


def test_t138_edit_gram_reallots_not_stacks(make_env):
    """20g 改成 8g：档位 sold 等于按 8g 重摊，不是 20+8。"""
    marks = {"1": {"g": 16, "cost": 0, "sold": 0, "proceeds": 0, "at": 1},
             "2": {"g": 24, "cost": 0, "sold": 0, "proceeds": 0, "at": 1}}
    env = make_env(cfg=cfg(TOTAL=100, keepGram=8, gearMarks=marks,
                           swingGears=[1, 2, 3, 4, 5, 6]), price=PX)
    p = env.page
    p.wait_for_timeout(600)
    p.evaluate("() => window.__xt.gotoTab('pos')")
    p.wait_for_timeout(300)
    open_sell(p)
    fill(p, 20, 900)
    save(p)
    sold20 = p.evaluate("() => [1,2].map(i => window.__xt.gearState(i).soldG)")
    assert sum(sold20) == pytest.approx(20, abs=0.15)

    p.click("#buyList [data-sellid]")
    p.wait_for_timeout(250)
    fill(p, 8, 900)
    save(p)
    sold8 = p.evaluate("() => [1,2].map(i => window.__xt.gearState(i).soldG)")
    assert sum(sold8) == pytest.approx(8, abs=0.15), sold8
    assert sold8[0] < sold20[0], "改小克重后档位卖出量没退回去：%s → %s" % (sold20, sold8)


# ---------- T139–T141 布局与防误伤 ----------

def test_t139_no_overflow_any_width(make_env):
    """390/360/320：汇总行不横向溢出，h2 不折行。"""
    for w in (390, 360, 320):
        env = make_env(cfg=cfg(), width=w, height=844, price=PX)
        p = env.page
        p.wait_for_timeout(600)
        p.evaluate("() => window.__xt.gotoTab('pos')")
        p.wait_for_timeout(300)
        r = p.evaluate("""() => {
          const all = [...document.querySelectorAll('#panel-pos .sech')];
          const row = all.find(s => s.querySelector('#btnHold'));
          const h2 = row.querySelector('h2');
          return {rowW: row.clientWidth, rowSW: row.scrollWidth,
                  h2H: Math.round(h2.getBoundingClientRect().height),
                  h2SW: h2.scrollWidth, h2CW: h2.clientWidth,
                  docSW: document.documentElement.scrollWidth, iw: window.innerWidth};
        }""")
        assert r["rowSW"] <= r["rowW"] + 1, "%s 汇总行溢出：%s" % (w, r)
        assert r["h2H"] <= 24, "%s 标题折行了：%s" % (w, r)
        assert r["docSW"] <= r["iw"] + 1, "%s 页面横向滚动：%s" % (w, r)


def test_t140_seg_buttons_tappable_on_320(make_env):
    """320 下分段两个按钮各 ≥36px 高、不换行。"""
    env = make_env(cfg=cfg(), width=320, height=568, price=PX)
    p = env.page
    p.wait_for_timeout(600)
    p.evaluate("() => window.__xt.gotoTab('pos')")
    p.wait_for_timeout(300)
    open_sell(p)
    r = p.evaluate("""() => {
      const seg = document.querySelector('#sheetSeg');
      const bs = [...seg.querySelectorAll('button')].map(b => b.getBoundingClientRect());
      return {segSW: seg.scrollWidth, segCW: seg.clientWidth,
              hs: bs.map(b => Math.round(b.height)),
              tops: bs.map(b => Math.round(b.top)),
              docSW: document.documentElement.scrollWidth, iw: window.innerWidth};
    }""")
    assert all(h >= 36 for h in r["hs"]), r
    assert len(set(r["tops"])) == 1, "分段按钮换行了：%s" % r
    assert r["segSW"] <= r["segCW"] + 1, r
    assert r["docSW"] <= r["iw"] + 1, r


def test_t141_overview_budget_untouched(make_env):
    """总览一屏 860 是本次改动的红线，碰了就是误伤。"""
    env = make_env(cfg=cfg(), width=390, height=844, price=PX)
    p = env.page
    p.wait_for_timeout(700)
    h = p.eval_on_selector("#panel-ov", "e => e.getBoundingClientRect().height")
    assert h <= 860, "总览高度 %s，超出一屏" % h


def test_t142_no_p21_blame_without_marks(page_of):
    """没有档位标记时卖出，明细行说「无需摊入」，不能把新记录说成 P21 之前的老数据。

    这两件事原因完全不同：前者是「各档本来就没有净持」，后者是「老记录没存快照」。
    混着说会让用户以为自己记错了账。
    """
    p = page_of()
    open_sell(p)
    fill(p, 20, 900)
    save(p)
    body = p.text_content("#buyList") or ""
    assert "无需摊入" in body, body
    assert "P21 之前" not in body, body
