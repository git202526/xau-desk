"""P8 配额重分配 · 第 2 期（UI 层）。

验：行内下沉标签、弹药行汇总、点标签出明细、设置页开关/模式/比例三件套、
关闭后恢复原样、账本模式不出现、双端不溢出。

口径同第 1 期：H=4700、总仓 200g、档距 48，现价 4030 → 第 1 档（4089）失效，
16g 挪给第 2 档（4041，刚好在现价上方 0.3%）。
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H = 4700.0
W = 200.0
PX = 4030.0
BASE = [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]


def bars_calm(n=240, px=PX, amp=24.0):
    now = 1761696000000
    step = 24 * 3600 * 1000
    raw = [px * 0.93 + i * (px * 0.07 / (n - 1)) + math.sin(i / 9.0) * 11 for i in range(n)]
    adj = raw[-1] - px
    out = []
    for i in range(n):
        c = raw[i] - adj
        t = int(now - (n - 1 - i) * step)
        out.append([t, "%.2f" % (c - 4), "%.2f" % (c + amp), "%.2f" % (c - amp),
                    "%.2f" % c, "12.5", t + step - 1, "0", 0, "0", "0", "0"])
    return out


def cfg(realloc=None, **kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W, planOnly=True,
             swingGears=[1, 2], gearMarks={},
             realloc=realloc if realloc is not None else
             {"on": True, "mode": "near", "ratio": 1, "maxDrop": 3})
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    mk_keys = ("price", "width", "height", "gh_on", "reduced_motion", "tz", "calendar")

    def _f(c=None, **kw):
        base = dict(c) if c is not None else cfg()
        mk = {}
        for k in list(kw.keys()):
            if k in mk_keys:
                mk[k] = kw.pop(k)
        base.update(kw)
        mk.setdefault("bars", bars_calm())
        mk.setdefault("price", PX)
        return make_env(cfg=base, **mk)
    return _f


def to_plan(p):
    p.evaluate("""() => {
        if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
        window.scrollTo(0, 0);
    }""")
    p.wait_for_timeout(340)
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(460)


def to_set(p):
    p.evaluate("""() => {
        if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
        window.scrollTo(0, 0);
    }""")
    p.wait_for_timeout(340)
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(460)


def shifts(p):
    return p.evaluate("""() => [...document.querySelectorAll('#ladder .shift')]
        .map(x => ({gear: x.closest('.body').dataset.gear, txt: x.textContent.trim()}))""")


# ---------------------------------------------------------------- 1 行内标签

def test_v1_tags_on_donor_and_receiver(env):
    """第 1 档 −16 下沉出，第 2 档 +16 下沉入。"""
    p = env().page
    to_plan(p)
    got = shifts(p)
    assert got == [{"gear": "第1档", "txt": "−16 下沉出"},
                   {"gear": "第2档", "txt": "+16 下沉入"}], got


def test_v2_no_tags_when_off(env):
    """关掉开关：一条标签都没有。"""
    p = env(realloc={"on": False}).page
    to_plan(p)
    assert shifts(p) == []


def test_v3_no_tags_when_nothing_dead(env):
    """现价 4072.40 时一档都没跌破 → 无标签。"""
    p = env(price=PRICE).page
    to_plan(p)
    assert shifts(p) == []


def test_v4_ammo_line_reports_pool(env):
    """弹药进度行带上「下沉 16g」。"""
    p = env().page
    to_plan(p)
    txt = p.inner_text("#ammoTxt")
    assert "下沉 16g" in txt, txt


def test_v5_ammo_line_clean_when_off(env):
    """关掉后弹药行只剩克重，不带下沉字样。"""
    p = env(realloc={"on": False}).page
    to_plan(p)
    assert "下沉" not in p.inner_text("#ammoTxt")


def test_v6_plan_text_reflects_shift(env):
    """第 2 档计划从 24 变 40，第 1 档变 0。"""
    p = env().page
    to_plan(p)
    row1 = p.evaluate("""() => [...document.querySelectorAll('#ladder .body')]
        .find(x => x.dataset.gear === '第1档').querySelector('.plan').textContent""")
    row2 = p.evaluate("""() => [...document.querySelectorAll('#ladder .body')]
        .find(x => x.dataset.gear === '第2档').querySelector('.plan').textContent""")
    assert row1 == "计划 0g"
    assert row2 == "计划 40g"


# ---------------------------------------------------------------- 2 点标签

def test_v7_tap_shift_shows_detail(env):
    """点标签弹说明，含来源、去向与「自动恢复」。"""
    p = env().page
    to_plan(p)
    p.click('#ladder [data-shift="第1档"]')
    p.wait_for_timeout(300)
    box = p.evaluate("() => { const t = document.getElementById('toast'); return {on: t.classList.contains('on'), txt: t.textContent}; }")
    assert box["on"] is True
    assert "下沉" in box["txt"] and "自动恢复" in box["txt"], box["txt"]


def test_v8_tap_does_not_open_gear_sheet(env):
    """点标签不能顺带把档位详情面板弹出来（冒泡要拦住）。"""
    p = env().page
    to_plan(p)
    p.click('#ladder [data-shift="第2档"]')
    p.wait_for_timeout(320)
    assert p.evaluate("() => document.getElementById('sheet').classList.contains('on')") is False


# ---------------------------------------------------------------- 3 设置页

def test_v9_settings_echo(env):
    """开关打勾、就近高亮、100% 高亮。"""
    p = env().page
    to_set(p)
    st = p.evaluate("""() => ({
        on: document.getElementById('setReOn').checked,
        mode: [...document.querySelectorAll('#setReMode .swg')].map(x => x.dataset.m + ':' + x.classList.contains('on')),
        ratio: [...document.querySelectorAll('#setReRatio .swg')].map(x => x.dataset.r + ':' + x.classList.contains('on'))
    })""")
    assert st["on"] is True
    assert "near:true" in st["mode"] and "prop:false" in st["mode"]
    assert "1:true" in st["ratio"]


def test_v10_settings_status_line(env):
    """状态行写清「已下沉 16 g：第1档 → 第2档」。"""
    p = env().page
    to_set(p)
    live = p.inner_text("#setReLive")
    assert "已下沉 16" in live and "第1档" in live and "第2档" in live, live


def test_v11_switch_mode_to_prop(env):
    """点「等比」→ cfg 写入 prop，标签还在但计划数值按权重摊。"""
    p = env(price=3920.0).page
    to_set(p)
    p.click('#setReMode [data-m="prop"]')
    p.wait_for_timeout(460)
    assert p.evaluate("() => window.__xt.state().cfg.realloc.mode") == "prop"
    plan = p.evaluate("() => window.__xt.planOf()")
    assert plan[3] == pytest.approx(72 + 48 * 72 / 112, abs=0.05)


def test_v12_switch_ratio(env):
    """点 50% → 只下沉一半：第 1 档留 8，第 2 档 32。"""
    p = env().page
    to_set(p)
    p.click('#setReRatio [data-r="0.5"]')
    p.wait_for_timeout(460)
    plan = p.evaluate("() => window.__xt.planOf()")
    assert plan == pytest.approx([8, 32, 48, 72, 24, 16], abs=0.02)


def test_v13_uncheck_restores(env):
    """取消勾选 → 标签消失、计划回原样、状态行说「已关闭」。"""
    p = env().page
    to_set(p)
    p.click("#setReOn")
    p.wait_for_timeout(460)
    assert "已关闭" in p.inner_text("#setReLive")
    to_plan(p)
    assert shifts(p) == []
    assert p.evaluate("() => window.__xt.planOf()") == pytest.approx(BASE, abs=0.01)


def test_v14_ledger_mode_hides_everything(env):
    """账本模式：没有标签，设置页说明「不生效」。"""
    p = env(planOnly=False).page
    to_plan(p)
    assert shifts(p) == []
    to_set(p)
    assert "不生效" in p.inner_text("#setReLive")


# ---------------------------------------------------------------- 4 双端

@pytest.mark.parametrize("width", [390, 320])
def test_v15_no_overflow(env, width):
    """两档都带标签时窄屏不溢出。"""
    p = env(width=width).page
    to_plan(p)
    assert len(shifts(p)) == 2
    assert p.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")


def test_v16_no_page_error(env):
    """切页、点标签、改设置，全程无 JS 异常。"""
    errs = []
    p = env().page
    p.on("pageerror", lambda e: errs.append(str(e)))
    to_plan(p)
    p.click('#ladder [data-shift="第1档"]')
    p.wait_for_timeout(260)
    to_set(p)
    p.click('#setReMode [data-m="prop"]')
    p.wait_for_timeout(400)
    p.click('#setReRatio [data-r="0.7"]')
    p.wait_for_timeout(400)
    assert errs == []
