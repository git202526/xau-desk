"""P14-3 设置页折叠：云端与「数据备份与维护」默认收起。

这两块加起来约 1100px（云端卡 842 + 运行说明与三个按钮 270），接好云端之后几乎不再打开，
却一直把设置页撑到 4 屏多。折叠后：

  · 折叠头本身就是 sech，右侧显示接入状态（未接入 / 云端已同步 / 云端异常）
  · 「展开 / 收起」是 40px 高的 act 按钮，aria-expanded 同步
  · 状态只存内存，刷新回到收起（与档位表 ladderExpand 一致）
  · 任何要用户看到云端表单的入口（顶栏同步条、未接入警告条）先展开再滚
"""
import pytest

from test_p11_weight_sum import cfg, BARS


def cfg10(**kw):
    c = cfg(gearCount=10, wPct=[4, 5, 8, 11, 14, 16, 17, 13, 8, 4], TOTAL=310, planOnly=True,
            swingGears=[1, 2], realloc={"on": True, "mode": "near", "ratio": 1, "maxDrop": 3})
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    def _f(c=None, **kw):
        base = dict(c) if c is not None else cfg10()
        mk = {}
        for k in list(kw.keys()):
            if k in ("width", "height", "price", "gh_on", "reduced_motion", "tz", "calendar"):
                mk[k] = kw.pop(k)
        base.update(kw)
        mk.setdefault("bars", BARS)
        return make_env(cfg=base, **mk)
    return _f


def open_set(page):
    page.click('.tabbar button[data-tab="set"]')
    page.wait_for_timeout(500)


def state(page):
    return page.evaluate("""() => {
        const w = document.getElementById('ghWrap'), m = document.getElementById('mtWrap');
        const gb = document.getElementById('ghToggle'), mb = document.getElementById('mtToggle');
        return {
            ghHidden: !!w.hidden, mtHidden: !!m.hidden,
            ghBtn: gb.textContent.trim(), mtBtn: mb.textContent.trim(),
            ghAria: gb.getAttribute('aria-expanded'), mtAria: mb.getAttribute('aria-expanded'),
            ghState: (document.getElementById('ghFoldState') || {}).textContent || '',
            total: document.getElementById('panel-set').scrollHeight,
        };
    }""")


# ---------------------------------------------------------------- 默认与切换

def test_f1_default_folded(env):
    """进设置页时两块都收起，云端表单不可见。"""
    p = env().page
    open_set(p)
    s = state(p)
    assert s["ghHidden"] is True and s["mtHidden"] is True, s
    assert s["ghBtn"] == "展开" and s["mtBtn"] == "展开", s
    assert p.is_visible("#ghToken") is False
    assert p.is_visible("#btnExport") is False


def test_f2_expand_cloud(env):
    """点展开：云端表单出现，按钮变「收起」，aria 同步。"""
    p = env().page
    open_set(p)
    p.click("#ghToggle")
    p.wait_for_timeout(250)
    s = state(p)
    assert s["ghHidden"] is False and s["ghBtn"] == "收起", s
    assert s["ghAria"] == "true", s
    assert p.is_visible("#ghToken") is True


def test_f3_collapse_again(env):
    """再点一次收回，且不影响另一块。"""
    p = env().page
    open_set(p)
    p.click("#ghToggle")
    p.wait_for_timeout(200)
    p.click("#ghToggle")
    p.wait_for_timeout(200)
    s = state(p)
    assert s["ghHidden"] is True and s["ghBtn"] == "展开", s
    assert s["mtHidden"] is True, s


def test_f4_expand_maintain(env):
    """维护区展开后能看到导出 / 导入 / 重置。"""
    p = env().page
    open_set(p)
    p.click("#mtToggle")
    p.wait_for_timeout(250)
    assert p.is_visible("#btnExport") is True
    assert p.is_visible("#btnImport") is True
    assert p.is_visible("#btnReset") is True
    assert state(p)["mtBtn"] == "收起"


def test_f5_two_folds_independent(env):
    """两块各管各的，展开一块不会连带另一块。"""
    p = env().page
    open_set(p)
    p.click("#mtToggle")
    p.wait_for_timeout(200)
    s = state(p)
    assert s["mtHidden"] is False and s["ghHidden"] is True, s


# ---------------------------------------------------------------- 折叠头状态

def test_f6_state_offline(env):
    """未接入时折叠头写「未接入」。"""
    p = env(gh_on=False).page
    open_set(p)
    assert state(p)["ghState"] == "未接入"


def test_f7_state_online(env):
    """接好云端时折叠头直接写同步状态，不用展开也看得见。"""
    p = env(gh_on=True).page
    open_set(p)
    p.wait_for_timeout(600)
    assert state(p)["ghState"] in ("云端已同步", "待同步", "同步中"), state(p)


# ---------------------------------------------------------------- 入口自动展开

def test_f8_syncbar_opens_cloud(env):
    """点顶栏同步条：进入设置并自动展开云端、滚到位。"""
    p = env(gh_on=False).page
    p.wait_for_timeout(400)
    p.click("#syncBar")
    p.wait_for_timeout(900)
    s = state(p)
    assert s["ghHidden"] is False, s
    v = p.evaluate("""() => { const r = document.getElementById('ghRepo').getBoundingClientRect();
        return {top: Math.round(r.top), vh: window.innerHeight}; }""")
    assert 0 <= v["top"] < v["vh"], v


def test_f9_warn_btn_opens_cloud(env):
    """未接入警告条上的按钮：同样先展开。"""
    p = env(gh_on=False).page
    p.wait_for_timeout(500)
    if not p.is_visible("#ghWarnBtn"):
        pytest.skip("当前分辨率下未显示警告条")
    p.click("#ghWarnBtn")
    p.wait_for_timeout(900)
    assert state(p)["ghHidden"] is False
    assert p.is_visible("#ghToken") is True


# ---------------------------------------------------------------- 高度与稳定性

def test_f10_page_height(env):
    """10 档 + 计划模式（最长配置）下，设置页总高 ≤ 3000px。"""
    p = env(width=390, height=844).page
    open_set(p)
    h = state(p)["total"]
    assert h <= 3000, h


def test_f11_fold_not_persisted(env):
    """折叠状态只存内存：刷新后回到收起。"""
    p = env().page
    open_set(p)
    p.click("#ghToggle")
    p.wait_for_timeout(200)
    assert state(p)["ghHidden"] is False
    p.reload()
    p.wait_for_timeout(1200)
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(500)
    assert state(p)["ghHidden"] is True


def test_f12_no_overflow_320(env):
    """320 宽：折叠头与展开后的内容都不横向溢出。"""
    p = env(width=320, height=844).page
    open_set(p)
    p.click("#ghToggle")
    p.click("#mtToggle")
    p.wait_for_timeout(300)
    over = p.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    assert over <= 1, over


def test_f13_toggle_tap_height(env):
    """展开按钮高度 ≥ 40px。"""
    p = env(width=320).page
    open_set(p)
    h = p.evaluate("() => Math.round(document.getElementById('ghToggle').getBoundingClientRect().height)")
    assert h >= 40, h
