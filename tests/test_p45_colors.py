"""v6.16 盈亏配色：盈利红、亏损绿。

注意这里测的是「金额盈亏」，与行情涨跌色共用同一套变量：
  --up  #E5484D  → rgb(229, 72, 77)
  --dn  #2FA36B  → rgb(47, 163, 107)
"""
import pytest

from conftest import CFG_EMPTY

RED = "rgb(229, 72, 77)"
GREEN = "rgb(47, 163, 107)"

# 现价 4072.40 × 7.10 / 31.1035 ≈ 929.61 元/克
PROFIT = dict(CFG_EMPTY, **{"H_BASE": 4800, "TOTAL": 100, "buys": [
    {"id": "b1", "name": "低位", "gram": 40, "priceCny": 880.0,
     "time": "2026-09-01T02:00:00.000Z", "note": "", "gear": None}]})
LOSS = dict(CFG_EMPTY, **{"H_BASE": 4800, "TOTAL": 100, "buys": [
    {"id": "b1", "name": "高位", "gram": 40, "priceCny": 1000.0,
     "time": "2026-09-01T02:00:00.000Z", "note": "", "gear": None}]})
SOLD = dict(CFG_EMPTY, **{
    "H_BASE": 4800, "TOTAL": 100,
    "buys": [{"id": "b1", "name": "低位", "gram": 100, "priceCny": 880.0,
              "time": "2026-09-01T02:00:00.000Z", "note": "", "gear": None}],
    "sells": [{"id": "s1", "name": "止盈", "gram": 40, "priceCny": 950.0,
               "time": "2026-10-01T02:00:00.000Z", "note": "", "gear": None}],
})


def color(page, sel):
    return page.eval_on_selector(sel, "e => getComputedStyle(e).color")


def open_pos(page):
    page.click('.tabbar button[data-tab="pos"]')
    page.wait_for_timeout(320)


def big(page):
    return page.evaluate("""() => {
        const b = document.querySelector('#posBox .pnlbig');
        return b ? { t: b.textContent.trim(), cls: b.className,
                     c: getComputedStyle(b).color } : null;
    }""")


def item(page, label):
    return page.evaluate("""(lb) => {
        const it = [...document.querySelectorAll('#posBox .posmini .it')]
            .find(e => e.querySelector('span').textContent.indexOf(lb) === 0);
        if (!it) return null;
        const b = it.querySelector('b');
        return { t: b.textContent.trim(), cls: b.className, c: getComputedStyle(b).color };
    }""", label)


# ---------- 总览仓位条 ----------


def test_co1_posbar_profit_red(make_env):
    env = make_env(PROFIT)
    page = env.page
    v = page.eval_on_selector("#pbPnl", "e => ({t:e.textContent.trim(), cls:e.className, c:getComputedStyle(e).color})")
    assert v["t"].startswith("+"), v
    assert "up" in v["cls"], v
    assert v["c"] == RED, v


def test_co2_posbar_loss_green(make_env):
    env = make_env(LOSS)
    page = env.page
    v = page.eval_on_selector("#pbPnl", "e => ({t:e.textContent.trim(), cls:e.className, c:getComputedStyle(e).color})")
    assert v["t"].startswith("-"), v
    assert "dn" in v["cls"], v
    assert v["c"] == GREEN, v


# ---------- 持仓页汇总 ----------


def test_co3_big_number_profit_red(make_env):
    env = make_env(PROFIT)
    page = env.page
    open_pos(page)
    b = big(page)
    assert b is not None, "找不到浮动盈亏大字"
    assert b["t"].startswith("+"), b
    assert "red" in b["cls"], b
    assert b["c"] == RED, b


def test_co4_big_number_loss_green(make_env):
    env = make_env(LOSS)
    page = env.page
    open_pos(page)
    b = big(page)
    assert b["t"].startswith("-"), b
    assert "green" in b["cls"], b
    assert b["c"] == GREEN, b


def test_co5_return_rate_matches(make_env):
    env = make_env(PROFIT)
    page = env.page
    open_pos(page)
    it = item(page, "收益率")
    assert it is not None, "找不到收益率"
    assert "red" in it["cls"], it
    assert it["c"] == RED, it


def test_co6_return_rate_loss_green(make_env):
    env = make_env(LOSS)
    page = env.page
    open_pos(page)
    it = item(page, "收益率")
    assert "green" in it["cls"], it
    assert it["c"] == GREEN, it


def test_co7_realized_profit_red(make_env):
    """已减仓的实现盈亏同样是「盈利红」。"""
    env = make_env(SOLD)
    page = env.page
    open_pos(page)
    it = item(page, "已减")
    assert it is not None, "找不到已减实现"
    assert it["t"].startswith("+"), it
    assert it["c"] == RED, it


# ---------- 不误伤行情色 ----------


def test_co8_quote_colors_unchanged(make_env):
    """行情涨跌仍按同一套变量：涨红跌绿，不能被这次改动带偏。"""
    env = make_env(PROFIT)
    page = env.page
    v = page.evaluate("""() => {
        const e = document.getElementById('pDelta');
        return { cls: e.className, c: getComputedStyle(e).color };
    }""")
    assert "up" in v["cls"], v
    assert v["c"] == RED, v


def test_co9_empty_state_neutral(make_env):
    env = make_env(dict(CFG_EMPTY))
    page = env.page
    open_pos(page)
    # 空态是纯文本提示，不该出现红绿
    assert page.eval_on_selector_all("#posBox .red, #posBox .green", "els => els.length") == 0
