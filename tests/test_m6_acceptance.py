"""M6 验收：计划书 §9 的 T1–T12，全视口跑通。"""
import os

TABS = ["ov", "struct", "plan", "pos", "sig", "set"]
VIEWPORTS = [(360, 780), (390, 844), (430, 932), (768, 1024), (1280, 900)]
SHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mockups", "v62")


def test_t1_first_screen_is_overview(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    assert page.eval_on_selector_all(".panel.on", "els => els.map(e => e.id)") == ["panel-ov"]
    assert page.eval_on_selector(".tabbar", "e => Math.round(e.getBoundingClientRect().bottom)") == 844


def test_t2_switch_all_tabs(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    for key in TABS:
        page.click('.tabbar button[data-tab="%s"]' % key)
        page.wait_for_timeout(80)
        assert page.eval_on_selector_all(".panel.on", "els => els.map(e => e.id)") == ["panel-" + key]
        assert page.eval_on_selector(".tabbar button.on", "e => e.dataset.tab") == key
        cur = page.eval_on_selector_all(
            '.tabbar button[aria-current="page"]', "els => els.map(e => e.dataset.tab)")
        assert cur == [key]


def test_t3_scroll_top_after_switch(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    page.click('.tabbar button[data-tab="set"]')
    page.evaluate("window.scrollTo(0, 900)")
    page.wait_for_timeout(100)
    page.click('.tabbar button[data-tab="pos"]')
    page.wait_for_timeout(150)
    assert page.evaluate("window.scrollY") == 0


def test_t4_reclick_keeps_dom(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    page.click('.tabbar button[data-tab="pos"]')
    page.evaluate("window.scrollTo(0, 400); window.__p = document.getElementById('panel-pos')")
    page.wait_for_timeout(100)
    page.click('.tabbar button[data-tab="pos"]')
    page.wait_for_timeout(400)
    assert page.evaluate("window.scrollY") == 0
    assert page.evaluate("window.__p === document.getElementById('panel-pos')")


def test_t5_deep_link(make_env):
    env = make_env(width=390, height=844, hash_="#pos")
    assert env.page.eval_on_selector(".tabbar button.on", "e => e.dataset.tab") == "pos"


def test_t6_posbar_flash(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    page.click("#posBar")
    page.wait_for_timeout(260)
    assert page.eval_on_selector("#posBox", "e => e.classList.contains('flash-card')") is True


def test_t7_badge_matches_todo(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    buys = page.eval_on_selector_all("#todoList .tic.k-buy", "els => els.length")
    assert buys >= 1
    assert page.eval_on_selector("#bdPlan", "e => e.textContent") == str(buys)


def test_t8_no_overflow_all_viewports(make_env):
    for w, h in VIEWPORTS:
        env = make_env(width=w, height=h)
        over = env.page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert over <= 1, "%sx%s 横向溢出 %s" % (w, h, over)


def test_t9_overview_one_screen(make_env):
    env = make_env(width=390, height=844)
    h = env.page.eval_on_selector("#panel-ov", "e => e.getBoundingClientRect().height")
    assert h <= 800, h


def test_t10_keyboard(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    page.click('.tabbar button[data-tab="set"]')
    # P14-3 起云端表单默认收起，先展开再聚焦（断言与判定标准不变）
    page.click("#ghToggle")
    page.wait_for_timeout(150)
    page.focus("#ghToken")
    page.wait_for_timeout(100)
    assert page.evaluate("document.body.classList.contains('kb')") is True
    page.evaluate("document.activeElement.blur()")
    page.wait_for_timeout(200)
    assert page.evaluate("document.body.classList.contains('kb')") is False


def test_t11_no_cloud_write_on_switch(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    page.wait_for_timeout(3000)
    base = env.gh.puts
    for key in TABS + TABS:
        page.click('.tabbar button[data-tab="%s"]' % key)
        page.wait_for_timeout(40)
    page.wait_for_timeout(1200)
    assert env.gh.puts == base


def test_t12_empty_holdings(make_env):
    from conftest import CFG_EMPTY
    env = make_env(width=390, height=844, cfg=CFG_EMPTY)
    page = env.page
    for sel in ("#pbGram", "#pbAvg", "#pbPnl"):
        assert page.eval_on_selector(sel, "e => e.textContent.trim()") == "—"
    page.click("#posBar")
    page.wait_for_timeout(200)
    assert page.eval_on_selector(".tabbar button.on", "e => e.dataset.tab") == "pos"


def test_every_panel_has_content(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    for key in TABS:
        page.click('.tabbar button[data-tab="%s"]' % key)
        page.wait_for_timeout(120)
        n = page.eval_on_selector("#panel-" + key, "e => e.innerText.trim().length")
        assert n > 80, "%s 页面内容过少：%s" % (key, n)


def test_desktop_tabbar_centered(make_env):
    env = make_env(width=1280, height=900)
    box = env.page.eval_on_selector(".tabbar", "e => { const r = e.getBoundingClientRect(); return [r.left, r.width] }")
    assert abs(box[1] - 560) < 1
    assert abs(box[0] - (1280 - 560) / 2) < 1


def test_screenshots_all_tabs(make_env):
    os.makedirs(SHOT_DIR, exist_ok=True)
    env = make_env(width=390, height=844)
    page = env.page
    for key in TABS:
        page.click('.tabbar button[data-tab="%s"]' % key)
        page.wait_for_timeout(350)
        page.screenshot(path=os.path.join(SHOT_DIR, "390-%s.png" % key))
    env.close()
    env2 = make_env(width=768, height=1024)
    env2.page.click('.tabbar button[data-tab="pos"]')
    env2.page.wait_for_timeout(350)
    env2.page.screenshot(path=os.path.join(SHOT_DIR, "768-pos.png"))
    assert len(os.listdir(SHOT_DIR)) >= 7
