"""M1 结构层：面板、总览、底栏、顶栏价格节点的存在性与归属。"""
import pytest

TABS = ["ov", "struct", "plan", "pos", "sig", "set"]


def test_overview_panel_is_first_and_active(make_env):
    env = make_env()
    page = env.page
    ids = page.eval_on_selector_all(".panel", "els => els.map(e => e.id)")
    assert ids[0] == "panel-ov", "总览必须是第一个面板，实际 %s" % ids
    assert page.eval_on_selector("#panel-ov", "e => e.classList.contains('on')")
    assert page.eval_on_selector_all(".panel.on", "els => els.length") == 1


def test_overview_contains_hero_posbar_todo(make_env):
    env = make_env()
    page = env.page
    assert page.eval_on_selector("#panel-ov", "e => !!e.querySelector('.hero')")
    assert page.eval_on_selector("#panel-ov", "e => !!e.querySelector('#posBar')")
    assert page.eval_on_selector("#panel-ov", "e => !!e.querySelector('#todoBox')")
    # 仓位条三格 + 箭头
    assert page.eval_on_selector_all("#posBar .c", "els => els.length") == 3
    for ident in ("pbGram", "pbAvg", "pbPnl"):
        assert page.eval_on_selector("#" + ident, "e => e.tagName") is not None
    assert page.eval_on_selector("#posBar", "e => e.tagName.toLowerCase()") == "button"


def test_tabbar_six_items_in_order(make_env):
    env = make_env()
    page = env.page
    assert page.eval_on_selector_all(".tabbar", "els => els.length") == 1
    tabs = page.eval_on_selector_all(".tabbar button", "els => els.map(e => e.dataset.tab)")
    assert tabs == TABS, tabs
    # 每项都有内联 SVG 图标与文字，不用 emoji
    for i in range(6):
        btn = page.locator(".tabbar button").nth(i)
        assert btn.locator("svg").count() == 1
        assert btn.locator("span").inner_text().strip() != ""


def test_tabbar_outside_wrap(make_env):
    env = make_env()
    page = env.page
    inside = page.eval_on_selector(
        ".wrap", "w => !!w.querySelector('.tabbar')")
    assert inside is False, "底栏必须脱离 .wrap，避免被内容宽度约束"


def test_topbar_price_present(make_env):
    env = make_env()
    assert env.page.eval_on_selector(".topbar #tpx", "e => !!e") is True


def test_badges_attached_to_right_tabs(make_env):
    """角标结构归位；空数据时应为隐藏态（内容由 M4 的 renderBadges 填充）。"""
    from conftest import CFG_EMPTY
    env = make_env(cfg=CFG_EMPTY)
    page = env.page
    for bid, tab in (("bdPlan", "plan"), ("bdPos", "pos"), ("bdSet", "set")):
        assert page.eval_on_selector('.tabbar button[data-tab="%s"] #%s' % (tab, bid), "e => !!e") is True
        assert page.eval_on_selector("#" + bid, "e => e.hidden") is True
