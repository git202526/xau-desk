"""M4 总览渲染：仓位条、空态、角标、顶栏价格、统一刷新入口。"""
import os
import re

PRICE_CNY = 4072.40 * 7.10 / 31.1035


def txt(page, sel):
    return page.eval_on_selector(sel, "e => e.textContent.trim()")


def test_posbar_values(make_env):
    env = make_env()
    page = env.page
    assert txt(page, "#pbGram") == "60.0g"
    assert txt(page, "#pbAvg").startswith("919.9")
    pnl = page.eval_on_selector("#pbPnl", "e => e.textContent.trim()")
    assert pnl.startswith("+"), pnl
    expect = (PRICE_CNY - 55195 / 60) * 60
    assert abs(float(pnl.replace("+", "").replace(",", "")) - expect) < 2, (pnl, expect)
    assert page.eval_on_selector("#pbPnl", "e => e.classList.contains('up')") is True


def test_posbar_empty_state(make_env):
    from conftest import CFG_EMPTY
    env = make_env(cfg=CFG_EMPTY)
    page = env.page
    for i in ("#pbGram", "#pbAvg", "#pbPnl"):
        assert txt(page, i) == "—", i
    assert page.eval_on_selector("#posBar", "e => e.getAttribute('aria-label')").startswith("还没建仓")


def test_badges_count_pending_buys(make_env):
    env = make_env()
    page = env.page
    assert page.eval_on_selector("#bdPlan", "e => e.hidden") is False
    assert txt(page, "#bdPlan") == "3"
    assert page.eval_on_selector("#bdPos", "e => e.hidden") is True
    assert page.eval_on_selector("#bdSet", "e => e.hidden") is True
    # 切到建仓页后，自己的角标不再显示
    page.click('.tabbar button[data-tab="plan"]')
    page.wait_for_timeout(80)
    assert page.eval_on_selector("#bdPlan", "e => getComputedStyle(e).display") == "none"


def test_badges_hidden_when_empty(make_env):
    from conftest import CFG_EMPTY
    env = make_env(cfg=CFG_EMPTY)
    for bid in ("#bdPlan", "#bdPos", "#bdSet"):
        assert env.page.eval_on_selector(bid, "e => e.hidden") is True


def test_topbar_price_value_and_color(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="pos"]')
    page.wait_for_timeout(80)
    assert txt(page, "#tpx") == "4,072.40" or txt(page, "#tpx") == "4072.40", txt(page, "#tpx")
    cls = page.eval_on_selector("#tpx", "e => e.className")
    assert "num" in cls


def test_refresh_all_updates_posbar(make_env):
    env = make_env()
    page = env.page
    page.evaluate("""() => {
      const S = window.__xt.state();
      S.cfg.buys.push({id:'bx', name:'补仓', gram:40, priceCny:1000, time:new Date().toISOString()});
      window.__xt.compute();
      window.__xt.refreshAll();
    }""")
    page.wait_for_timeout(150)
    assert txt(page, "#pbGram") == "100.0g"
    assert txt(page, "#pbAvg").startswith("952.0")


def test_renderers_do_not_call_each_other():
    """架构约束：渲染函数之间禁止互调，避免递归。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "index.html"), encoding="utf-8").read()
    names = ["renderHero", "renderVerdict", "renderOv", "renderTodo", "renderBadges",
             "renderStruct", "renderKL", "renderLadder", "renderPos", "renderBuys",
             "renderTake", "renderEvents", "renderFeed", "renderStat"]
    for fn in names:
        body = re.search(r"function %s\([^)]*\)\{(.*?)\n\}" % fn, src, re.S)
        assert body, "找不到函数 %s" % fn
        calls = [n for n in names if n != fn and (n + "(") in body.group(1)]
        assert not calls, "%s 内部调用了 %s，破坏单向依赖" % (fn, calls)


def test_overview_fits_one_screen(make_env):
    env = make_env(width=390, height=844)
    h = env.page.eval_on_selector("#panel-ov", "e => e.getBoundingClientRect().height")
    assert h <= 800, "总览高度 %s，超出一屏" % h
