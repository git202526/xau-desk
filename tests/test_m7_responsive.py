"""M7 自适应回归：任何一档常见手机宽度、任何一个 Tab，都不得横向溢出。

本组用例针对线上反馈「除总览外其它 Tab 溢出屏幕」而建：
根因是顶栏 brand / 现价 / 状态三块都不能收缩，多出一个现价按钮就把整页顶宽。
"""
import pytest

TABS = ["ov", "struct", "plan", "pos", "sig", "set"]
WIDTHS = [320, 360, 375, 390, 412, 430]

OVERFLOW_JS = """() => {
  const vw = document.documentElement.clientWidth;
  const bad = [];
  /* 横向滚动容器（设置页速览条 .setnav）里的子元素天然会超出容器可视区，
     这不是页面溢出：只要滚动容器本身在视口内，就跳过它的后代。
     页面整体的 scrollWidth 检查仍然照做（见 assert_clean 第一条）。 */
  const inScroller = el => {
    let p = el.parentElement;
    while (p && p !== document.body) {
      const cs = getComputedStyle(p);
      if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') {
        const pr = p.getBoundingClientRect();
        if (pr.left >= -1 && pr.right <= vw + 1) return true;
      }
      p = p.parentElement;
    }
    return false;
  };
  document.querySelectorAll('body *').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return;
    if (getComputedStyle(el).position === 'fixed') return;
    if (inScroller(el)) return;
    if (r.right > vw + 1 || r.left < -1) {
      bad.push(el.tagName.toLowerCase() + '#' + (el.id || '') +
               '.' + String(el.className || '').slice(0, 24).trim() +
               '[' + Math.round(r.left) + ',' + Math.round(r.right) + ']');
    }
  });
  return {
    vw: vw,
    scrollW: document.documentElement.scrollWidth,
    bad: bad.slice(0, 8),
  };
}"""

GEOM_JS = """() => {
  const vw = document.documentElement.clientWidth;
  const brand = document.querySelector('.topbar .brand').getBoundingClientRect();
  const tpx = document.getElementById('tpx').getBoundingClientRect();
  const stat = document.querySelector('.topbar .stat').getBoundingClientRect();
  return {
    vw: vw,
    brandRight: Math.round(brand.right),
    tpxLeft: Math.round(tpx.left),
    tpxRight: Math.round(tpx.right),
    statRight: Math.round(stat.right),
    statLeft: Math.round(stat.left),
  };
}"""


def open_tab(page, name):
    page.click('.tabbar button[data-tab="%s"]' % name)
    page.wait_for_timeout(130)


def assert_clean(page, label):
    r = page.evaluate(OVERFLOW_JS)
    assert r["scrollW"] <= r["vw"] + 1, "%s 横向溢出：scrollWidth=%s vw=%s" % (label, r["scrollW"], r["vw"])
    assert r["bad"] == [], "%s 有元素越界：%s" % (label, r["bad"])


@pytest.mark.parametrize("tab", TABS)
def test_every_tab_fits_every_phone_width(make_env, tab):
    """6 档宽度 × 6 个 Tab 全组合，页面宽度必须等于视口宽度。"""
    env = make_env(width=390, height=800)
    page = env.page
    for w in WIDTHS:
        page.set_viewport_size({"width": w, "height": 800})
        page.wait_for_timeout(120)
        open_tab(page, tab)
        assert_clean(page, "%dpx / %s" % (w, tab))


def test_topbar_keeps_spare_room(make_env):
    """顶栏三块之间必须留余量，不能刚好卡满——否则换字体、系统字号一变就顶破。"""
    env = make_env(width=390, height=800)
    page = env.page
    open_tab(page, "struct")
    for w in WIDTHS:
        page.set_viewport_size({"width": w, "height": 800})
        page.wait_for_timeout(120)
        g = page.evaluate(GEOM_JS)
        assert g["tpxRight"] <= w - 8, "%dpx 现价贴边：%s" % (w, g)
        assert g["statRight"] <= w - 8, "%dpx 状态列贴边：%s" % (w, g)
        assert g["tpxLeft"] - g["brandRight"] >= 8, "%dpx 品牌与现价之间无余量：%s" % (w, g)


def test_topbar_survives_wider_font(make_env):
    """把状态与品牌文字拉宽 10%，仍不得溢出（模拟不同系统字体）。"""
    env = make_env(width=320, height=800)
    page = env.page
    open_tab(page, "struct")
    page.add_style_tag(content=".brand h1,.brand .sub,#connTxt,#syncTxt{letter-spacing:1.1px}")
    page.wait_for_timeout(120)
    assert_clean(page, "320px + 宽字体")
    page.set_viewport_size({"width": 360, "height": 800})
    page.wait_for_timeout(120)
    assert_clean(page, "360px + 宽字体")


def test_long_status_text_truncates_not_overflows(make_env):
    """同步/连接状态文案变长时，必须用省略号收敛，不能撑宽页面。"""
    env = make_env(width=320, height=800)
    page = env.page
    open_tab(page, "struct")
    # 注入与取样必须在同一帧内完成：syncTxt 是渲染产物，跨帧会被「已同步 HH:MM」覆盖回去，
    # 那样量到的就是短文本，测试会误报（v6.16 及以前就跑挂过）。
    info = page.evaluate("""() => {
      const s = document.getElementById('syncTxt');
      const c = document.getElementById('connTxt');
      s.textContent = '已载入云端 128 条记录'.repeat(6);
      c.textContent = '行情连接已断开，正在重试'.repeat(6);
      void document.body.offsetWidth;              // 强制同步 layout，抢在下一帧前面
      const vw = document.documentElement.clientWidth;
      const bad = [];
      document.querySelectorAll('body *').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return;
        if (getComputedStyle(el).position === 'fixed') return;
        if (r.right > vw + 1 || r.left < -1) bad.push(el.tagName.toLowerCase() + '#' + (el.id || ''));
      });
      return {
        vw: vw, scrollW: document.documentElement.scrollWidth, bad: bad.slice(0, 8),
        ssw: s.scrollWidth, scw: s.clientWidth, len: s.textContent.length,
      };
    }""")
    assert info["len"] == 78, info                       # 长文本确实还在，没被渲染覆盖
    assert info["scrollW"] <= info["vw"] + 1, info        # 页面整体不横向溢出
    assert info["bad"] == [], info                        # 没有任何元素越界
    assert info["ssw"] > info["scw"] + 1, "长文案没有触发省略号，说明是被硬撑开的"


def _text_lines(page, sel):
    """用 Range 数文本实际占几行：折行会得到多个 rect。"""
    return page.eval_on_selector(sel, """e => {
      const r = document.createRange();
      r.selectNodeContents(e);
      return r.getClientRects().length;
    }""")


def test_section_head_single_line_on_narrow(make_env):
    """320px 下「买入记录 + 记一笔」这类标题行必须各占一行，不能折成两行。"""
    env = make_env(width=320, height=800)
    page = env.page
    open_tab(page, "pos")
    assert _text_lines(page, "#btnAddBuy") == 1, "「+ 记一笔」折行了"
    assert _text_lines(page, "#panel-pos .sech h2") <= 2, "标题文字折行"
    h = page.eval_on_selector("#panel-pos .sech h2", "e => e.getBoundingClientRect().height")
    assert h <= 26, "标题行高度 %.1f，说明折行了" % h


def test_brand_subtitle_adapts(make_env):
    """手机只留版本号（省宽），桌面恢复完整描述。"""
    env = make_env(width=390, height=800)
    page = env.page
    assert page.eval_on_selector(".brand .sdesc", "e => getComputedStyle(e).display") == "none"
    assert page.eval_on_selector("#verTag", "e => e.offsetWidth > 0") is True

    page.set_viewport_size({"width": 768, "height": 900})
    page.wait_for_timeout(150)
    assert page.eval_on_selector(".brand .sdesc", "e => getComputedStyle(e).display") != "none"
    assert_clean(page, "768px")


def test_desktop_widths_no_overflow(make_env):
    """桌面与横屏平板同样不许溢出。"""
    env = make_env(width=390, height=800)
    page = env.page
    for w in (768, 1024, 1440):
        page.set_viewport_size({"width": w, "height": 900})
        page.wait_for_timeout(150)
        for t in ("ov", "struct", "pos", "set"):
            open_tab(page, t)
            assert_clean(page, "%dpx / %s" % (w, t))
