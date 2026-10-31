"""M2 样式层：底栏固定贴底、安全区、桌面胶囊、仓位条与角标尺寸。"""


def style(page, sel, prop):
    return page.eval_on_selector(sel, "(e,p) => getComputedStyle(e).getPropertyValue(p)", prop)


def test_tabbar_fixed_to_bottom(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    assert style(page, ".tabbar", "position") == "fixed"
    assert style(page, ".tabbar", "bottom") == "0px"
    assert style(page, ".tabbar", "z-index") == "60"
    box = page.eval_on_selector(".tabbar", "e => { const r = e.getBoundingClientRect(); return [r.left, r.width, r.bottom] }")
    assert box[0] == 0 and abs(box[1] - 390) < 1, box
    assert abs(box[2] - 844) < 1, box
    assert abs(page.eval_on_selector(".tabbar", "e => e.getBoundingClientRect().height") - 56) < 1


def test_body_reserves_space_for_tabbar(make_env):
    env = make_env(width=390, height=844)
    pad = style(env.page, "body", "padding-bottom")
    assert pad.startswith("76px"), pad


def test_tab_hit_areas(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    sizes = page.eval_on_selector_all(
        ".tabbar button", "els => els.map(e => { const r = e.getBoundingClientRect(); return [r.width, r.height] })")
    for w, h in sizes:
        assert w >= 60, sizes
        assert h >= 44, sizes


def test_active_indicator_and_color(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    active = page.eval_on_selector(".tabbar button.on", "e => getComputedStyle(e).color")
    idle = page.eval_on_selector(".tabbar button:not(.on)", "e => getComputedStyle(e).color")
    assert active == "rgb(212, 162, 76)", active
    assert idle == "rgb(101, 121, 147)", idle
    on_op = page.eval_on_selector(".tabbar button.on", "e => getComputedStyle(e, ':before').opacity")
    off_op = page.eval_on_selector(".tabbar button:not(.on)", "e => getComputedStyle(e, ':before').opacity")
    assert float(on_op) > 0.9 and float(off_op) < 0.1
    w = page.eval_on_selector(".tabbar button.on", "e => getComputedStyle(e, ':before').width")
    h = page.eval_on_selector(".tabbar button.on", "e => getComputedStyle(e, ':before').height")
    assert w == "18px" and h == "2px"


def test_posbar_metrics(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    h = page.eval_on_selector("#posBar", "e => e.getBoundingClientRect().height")
    assert 68 <= h <= 70, h
    cols = page.eval_on_selector_all("#posBar .c", "els => els.map(e => e.getBoundingClientRect().width)")
    assert len(cols) == 3
    # 1px 分隔线计入宽度，允许 2px 以内误差
    assert max(cols) - min(cols) <= 2, cols
    # 390 - wrap 左右 28 - 边框 2 - 箭头 18 - 箭头右边距 8
    assert abs(sum(cols) - (390 - 28 - 2 - 18 - 8)) < 3, cols
    assert style(page, "#posBar", "cursor") == "pointer"


def test_badge_style(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    page.eval_on_selector("#bdPlan", "e => { e.hidden = false; e.textContent = '2' }")
    box = page.eval_on_selector("#bdPlan", "e => { const r = e.getBoundingClientRect(); return [r.width, r.height] }")
    assert box[1] == 15, box
    assert style(page, "#bdPlan", "background-color") == "rgb(229, 72, 77)"


def test_prefers_reduced_motion_kills_animation(make_env):
    env = make_env(width=390, height=844, reduced_motion=True)
    name = env.page.eval_on_selector("#panel-ov", "e => getComputedStyle(e).animationName")
    dur = env.page.eval_on_selector("#panel-ov", "e => getComputedStyle(e).animationDuration")
    assert name == "none", name
    assert float(dur.rstrip("s")) <= 0.001, dur


def test_desktop_floating_pill(make_env):
    env = make_env(width=768, height=1024)
    page = env.page
    box = page.eval_on_selector(".tabbar", "e => { const r = e.getBoundingClientRect(); return [r.left, r.width, r.bottom] }")
    assert abs(box[1] - 560) < 1, box
    assert abs(box[0] - 104) < 1, box
    assert abs(box[2] - (1024 - 14)) < 1, box
    assert style(page, ".tabbar", "border-radius") == "18px"
    assert style(page, "body", "padding-bottom").startswith("96px")


def test_tpx_hidden_on_overview(make_env):
    env = make_env(width=390, height=844)
    page = env.page
    page.evaluate("document.body.classList.add('tab-ov')")
    assert page.eval_on_selector("#tpx", "e => getComputedStyle(e).display") == "none"
    page.evaluate("document.body.classList.remove('tab-ov')")
    assert page.eval_on_selector("#tpx", "e => getComputedStyle(e).display") != "none"


def test_tabbar_not_covering_last_content(make_env):
    """页面滚到底时，最后一条内容不能被底栏遮住。"""
    env = make_env(width=390, height=844)
    page = env.page
    page.click('.tabbar button[data-tab="set"]')
    # P14-3 起维护区默认收起；不展开的话 #btnReset 的 rect 全是 0，这条会退化成空断言
    page.click("#mtToggle")
    page.wait_for_timeout(200)
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(150)
    gap = page.evaluate("""() => {
      const last = document.querySelector('#btnReset').getBoundingClientRect();
      const bar = document.querySelector('.tabbar').getBoundingClientRect();
      return bar.top - last.bottom;
    }""")
    assert gap >= 0, "底栏压住了最后一个按钮，间隙 %s" % gap


def test_no_horizontal_overflow(make_env):
    for w, h in ((360, 780), (390, 844), (430, 932), (768, 1024)):
        env = make_env(width=w, height=h)
        over = env.page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert over <= 1, "宽度 %s 溢出 %s" % (w, over)
