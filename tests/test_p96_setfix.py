"""v7.4 设置页局部 bug 回归：全部来自真实页面复现，不涉及信息架构改造。

七条原始现象（改前探针实测）：
  1 点「保存参数」把 spanMode 无条件写成 alpha，刚设的目标均价方式当场作废
  2 「终止跌幅」在非区间方式下改了不影响档距，toast 却报「已更新」
  3 终止跌幅小于起始跌幅时被静默夹取（填 10 → 变 14），无解释
  4 切档数把自定义权重全量冲掉，切回也回不来（注释却承诺「不推倒用户已调的值」）
  5 配额下沉关闭后，模式/比例按钮仍可点、仍写盘、仍弹「已切到等比」
  6 运行说明写「日线回溯 800 根」，实际 limit:1000
  7 α 与目标均价仍各自「改即存」并负责切模式（保存按钮不再越权）
"""
import pytest

from conftest import CFG_EMPTY


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=4700.0, TOTAL=300, alpha=2.44,
             spanMode="target", avgTarget=4000.0, avgBand=20.0,
             startDrop=13, endDrop=25)
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    def _f(**kw):
        width = kw.pop("width", 390)
        return make_env(cfg=cfg(**kw), price=4030.0, width=width, height=844)
    return _f


def open_set(page):
    page.click('.tabbar button[data-tab="set"]')
    page.wait_for_timeout(350)


def state(page, expr):
    return page.evaluate("() => " + expr)


def toast(page):
    return page.evaluate("() => document.getElementById('toast').textContent.trim()")


# ---------------------------------------------------------------- 1 保存不改档距方式

@pytest.mark.parametrize("mode", ["target", "range", "alpha"])
def test_f1_save_keeps_span_mode(env, mode):
    """改一个无关字段点保存，档距方式必须原样保留。"""
    p = env(spanMode=mode).page
    open_set(p)
    p.fill("#setTotal", "350")
    p.click("#btnSaveSet")
    p.wait_for_timeout(250)
    assert state(p, "window.__xt.state().cfg.spanMode") == mode
    assert state(p, "window.__xt.state().cfg.TOTAL") == 350


def test_f2_target_survives_save(env):
    """填目标均价 → 保存，方式仍是 target 且目标真的参与了反解。"""
    p = env().page
    open_set(p)
    p.fill("#setAvgT", "3900")
    p.locator("#setTotal").click()
    p.wait_for_timeout(200)
    p.click("#btnSaveSet")
    p.wait_for_timeout(250)
    assert state(p, "window.__xt.state().cfg.spanMode") == "target"
    assert state(p, "window.__xt.state().cfg.avgTarget") == 3900
    assert state(p, "window.__xt.gearBase().spanSrc") == "target"


def test_f3_alpha_change_still_switches_mode(env):
    """α 输入框改值仍由自己的 change 负责切到 alpha（保存按钮不再越权，但不能丢能力）。"""
    p = env().page
    open_set(p)
    p.fill("#setAlpha", "1.5")
    p.dispatch_event("#setAlpha", "change")
    p.wait_for_timeout(250)
    assert state(p, "window.__xt.state().cfg.alpha") == 1.5
    assert state(p, "window.__xt.state().cfg.spanMode") == "alpha"


# ---------------------------------------------------------------- 2/3 起止跌幅反馈

def test_f4_clamped_end_drop_explains(env):
    """终止填 10：夹到 14，toast 说清原因，输入框与 cfg 一致。"""
    p = env().page
    open_set(p)
    p.fill("#setEndDrop", "10")
    p.locator("#setTotal").click()
    p.wait_for_timeout(250)
    assert state(p, "window.__xt.state().cfg.endDrop") == 14
    assert p.input_value("#setEndDrop") == "14"
    t = toast(p)
    assert "大于起始跌幅" in t and "14%" in t, t


def test_f5_end_drop_inactive_mode_explains(env):
    """目标均价方式下改终止跌幅：明确告知不参与计算，切到区间后立刻生效。"""
    p = env().page
    open_set(p)
    p.fill("#setEndDrop", "45")
    p.locator("#setTotal").click()
    p.wait_for_timeout(250)
    t = toast(p)
    assert "用不到" in t and "区间" in t, t
    assert state(p, "window.__xt.state().cfg.spanMode") == "target"
    assert state(p, "window.__xt.gearMeta().d") is not None

    p.click('#setSpanMode [data-span="range"]')
    p.wait_for_timeout(300)
    assert state(p, "window.__xt.state().cfg.endDrop") == 45
    assert "都参与计算" in p.text_content("#setDropNote")


def test_f6_setspan_clamp_flag_and_consistency(env):
    """底层 setSpan：越界夹取返回标记，cfg 与 endDropOf() 不再各说各话。"""
    p = env().page
    r = p.evaluate("() => window.__xt.setSpan(undefined, 20, 15)")
    assert r["endClamped"] is True
    assert r["startDrop"] == 20 and r["endDrop"] == 21
    assert state(p, "window.__xt.state().cfg.endDrop") == 21
    assert state(p, "window.__xt.endDropOf()") == 21


def test_f7_drop_note_follows_mode(env):
    """说明行跟着档距方式走：非区间说「终止是最大深度（越界会压）」，区间说「两个值都参与」。

    v7.4.2 §3.3.1 起终止跌幅是全局上限，旧文案「切到区间后生效」已不成立（P0）。
    """
    p = env(spanMode="alpha").page
    open_set(p)
    note = p.text_content("#setDropNote")
    assert "定第 1 档" in note and "最大深度" in note
    assert "切到「区间」后生效" not in note
    p.click('#setSpanMode [data-span="range"]')
    p.wait_for_timeout(300)
    note2 = p.text_content("#setDropNote")
    assert "都参与计算" in note2 and "上限" in note2


def test_f8_no_target_says_fallback_without_switching(env):
    """清空目标均价：说明暂按波动率，但不擅自改掉用户选的方式。"""
    p = env().page
    open_set(p)
    p.fill("#setAvgT", "")
    p.locator("#setTotal").click()
    p.wait_for_timeout(250)
    p.click("#btnSaveSet")
    p.wait_for_timeout(250)
    assert state(p, "window.__xt.state().cfg.spanMode") == "target"
    assert "暂按波动率" in p.text_content("#setSpanModeLive")


# ---------------------------------------------------------------- 4 权重不丢

def test_f9_custom_weight_survives_gear_count_round_trip(env):
    """6 档自定义 → 8 档 → 切回 6 档，原值原样回来。"""
    p = env().page
    custom = [5, 5, 5, 5, 5, 75]
    p.evaluate("(w) => window.__xt.setWPct(w)", custom)
    p.wait_for_timeout(150)
    p.evaluate("() => window.__xt.setGearCount(8)")
    p.wait_for_timeout(150)
    assert state(p, "window.__xt.state().cfg.wPct.slice()") == [5, 7, 11, 16, 20, 18, 14, 9]
    p.evaluate("() => window.__xt.setGearCount(6)")
    p.wait_for_timeout(150)
    assert state(p, "window.__xt.state().cfg.wPct.slice()") == custom


def test_f10_untouched_weight_still_uses_template(env):
    """没改过权重时，切档数仍按模板补齐（老行为不变）。"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.wait_for_timeout(150)
    assert state(p, "window.__xt.state().cfg.wPct.slice()") == [4, 5, 8, 11, 14, 16, 17, 13, 8, 4]


def test_f11_reset_weight_clears_snapshot(env):
    """「恢复默认」要真的回默认：切走再切回不能再冒出旧的自定义值。"""
    p = env().page
    p.evaluate("(w) => window.__xt.setWPct(w)", [5, 5, 5, 5, 5, 75])
    p.wait_for_timeout(120)
    p.evaluate("() => window.__xt.setGearCount(8)")
    p.evaluate("() => window.__xt.setGearCount(6)")
    p.evaluate("() => window.__xt.resetW()")
    p.wait_for_timeout(150)
    assert state(p, "window.__xt.state().cfg.wPct.slice()") == [8, 12, 24, 36, 12, 8]
    p.evaluate("() => window.__xt.setGearCount(8)")
    p.evaluate("() => window.__xt.setGearCount(6)")
    p.wait_for_timeout(150)
    assert state(p, "window.__xt.state().cfg.wPct.slice()") == [8, 12, 24, 36, 12, 8]


# ---------------------------------------------------------------- 5 配额下沉关闭态

def test_f12_realloc_off_blocks_sub_controls(env):
    """关着时点模式/比例：不写盘、给可操作提示、控件有禁用态。"""
    p = env(realloc={"on": False, "mode": "near", "ratio": 0.7, "maxDrop": 3}).page
    open_set(p)
    before = state(p, "JSON.stringify(window.__xt.state().cfg.realloc)")
    assert "off" in p.get_attribute("#setReMode", "class")
    p.click('#setReMode [data-m="prop"]')
    p.wait_for_timeout(200)
    assert "已关闭" in toast(p)
    p.click('#setReRatio [data-r="0.5"]')
    p.wait_for_timeout(200)
    assert state(p, "JSON.stringify(window.__xt.state().cfg.realloc)") == before


def test_f13_realloc_on_sub_controls_still_work(env):
    """开着时点模式/比例照常写入（原有能力不丢）。"""
    p = env(realloc={"on": True, "mode": "near", "ratio": 0.7, "maxDrop": 3}).page
    open_set(p)
    p.click('#setReMode [data-m="prop"]')
    p.wait_for_timeout(250)
    assert state(p, "window.__xt.state().cfg.realloc.mode") == "prop"
    assert "off" not in p.get_attribute("#setReMode", "class")


# ---------------------------------------------------------------- 6 文档一致性

def test_f14_runtime_note_matches_kline_limit(env):
    """运行说明的根数与实际 KL_SPEC 一致。"""
    p = env().page
    txt = p.text_content("#mtWrap")
    assert "1000 根" in txt and "220 根" in txt, txt
    assert "800 根" not in txt
    cfg_spec = state(p, "window.__xt.KL_SPEC ? window.__xt.KL_SPEC.map(x=>x.limit) : null")
    assert cfg_spec is None or 1000 in cfg_spec


def test_f15_setnav_still_scrolls_on_320(env):
    """速览条保持既有横滑设计（信息架构未动），页面本身不横向溢出。"""
    p = env(width=320).page
    open_set(p)
    d = p.evaluate("""() => {
        const b = document.getElementById('setNav');
        return {sw: b.scrollWidth, cw: b.clientWidth,
                page: document.documentElement.scrollWidth, iw: innerWidth};
    }""")
    assert d["page"] <= d["iw"], d
    assert d["sw"] > d["cw"], d     # 可横滑
