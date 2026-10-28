"""M3 逻辑层：切页、hash、回顶、锚点高亮、键盘让位、切页不写云端。"""
import pytest

TABS = ["ov", "struct", "plan", "pos", "sig", "set"]


def active_tab(page):
    return page.eval_on_selector(".tabbar button.on", "e => e.dataset.tab")


def visible_panels(page):
    return page.eval_on_selector_all(".panel.on", "els => els.map(e => e.id)")


def test_default_is_overview(make_env):
    env = make_env()
    assert active_tab(env.page) == "ov"
    assert visible_panels(env.page) == ["panel-ov"]
    # 首次打开允许没有 hash；点过 Tab 之后必须写 hash
    assert env.page.evaluate("location.hash") in ("", "#ov")
    assert env.page.evaluate("document.body.classList.contains('tab-ov')") is True
    env.page.click('.tabbar button[data-tab="pos"]')
    env.page.wait_for_timeout(60)
    assert env.page.evaluate("location.hash") == "#pos"


def test_click_each_tab(make_env):
    env = make_env()
    page = env.page
    for key in TABS:
        page.click('.tabbar button[data-tab="%s"]' % key)
        page.wait_for_timeout(60)
        assert active_tab(page) == key
        assert visible_panels(page) == ["panel-" + key]
        assert page.evaluate("location.hash") == "#" + key
        # aria 只允许当前项为 page
        cur = page.eval_on_selector_all(
            ".tabbar button", "els => els.filter(e => e.getAttribute('aria-current') === 'page').map(e => e.dataset.tab)")
        assert cur == [key]


def test_scroll_resets_on_switch(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="set"]')
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(120)
    assert page.evaluate("window.scrollY") > 100
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(120)
    assert page.evaluate("window.scrollY") == 0


def test_reclick_current_tab_scrolls_top_without_rebuild(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(80)
    page.evaluate("window.scrollTo(0, 500)")
    page.wait_for_timeout(80)
    page.evaluate("window.__marker = document.getElementById('panel-struct')")
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(400)
    assert page.evaluate("window.scrollY") == 0
    assert page.evaluate("window.__marker === document.getElementById('panel-struct')") is True


def test_hash_restores_tab(make_env):
    env = make_env(hash_="#pos")
    assert active_tab(env.page) == "pos"
    assert visible_panels(env.page) == ["panel-pos"]


def test_invalid_hash_falls_back_to_overview(make_env):
    env = make_env(hash_="#nope")
    assert active_tab(env.page) == "ov"


def test_manual_hashchange_switches(make_env):
    env = make_env()
    env.page.evaluate("location.hash = '#sig'")
    env.page.wait_for_timeout(200)
    assert active_tab(env.page) == "sig"
    assert visible_panels(env.page) == ["panel-sig"]


def test_posbar_jumps_and_flashes(make_env):
    env = make_env()
    page = env.page
    page.evaluate("""() => {
      window.__flash = 0;
      const box = document.getElementById('posBox');
      new MutationObserver(ms => {
        ms.forEach(m => { if(m.target.classList.contains('flash-card')) window.__flash++; });
      }).observe(box, { attributes:true, attributeFilter:['class'] });
    }""")
    page.click("#posBar")
    page.wait_for_timeout(350)
    assert active_tab(page) == "pos"
    assert page.evaluate("location.hash") == "#pos"
    assert page.evaluate("window.__flash") >= 1
    page.wait_for_timeout(1100)
    assert page.eval_on_selector("#posBox", "e => e.classList.contains('flash-card')") is False


def test_anchor_scrolls_target_into_view(make_env):
    env = make_env()
    page = env.page
    page.evaluate("window.__xt.gotoTab('set', {anchor:'setBase'})")
    page.wait_for_timeout(250)
    assert active_tab(page) == "set"
    box = page.eval_on_selector("#setBase", "e => { const r = e.getBoundingClientRect(); return [r.top, r.bottom] }")
    assert 0 <= box[0] and box[1] <= 844 + 1, box


def test_topbar_price_returns_overview(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="pos"]')
    page.wait_for_timeout(80)
    assert page.eval_on_selector("#tpx", "e => getComputedStyle(e).display") != "none"
    page.click("#tpx")
    page.wait_for_timeout(80)
    assert active_tab(page) == "ov"
    assert page.eval_on_selector("#tpx", "e => getComputedStyle(e).display") == "none"


def test_keyboard_shrinks_tabbar(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="set"]')
    page.focus("#setBase")
    page.wait_for_timeout(120)
    assert page.evaluate("document.body.classList.contains('kb')") is True
    page.evaluate("document.getElementById('setBase').blur()")
    page.wait_for_timeout(200)
    assert page.evaluate("document.body.classList.contains('kb')") is False


def test_switch_does_not_write_cloud(make_env):
    env = make_env()
    page = env.page
    # 等首次价格到达引发的状态写入落定，再测「切 Tab 是否额外写云端」
    page.wait_for_timeout(3000)
    base = env.gh.puts
    for key in TABS + TABS:
        page.click('.tabbar button[data-tab="%s"]' % key)
        page.wait_for_timeout(40)
    page.wait_for_timeout(1200)
    assert env.gh.puts == base, "切 Tab 触发了云端写入：%s -> %s" % (base, env.gh.puts)
