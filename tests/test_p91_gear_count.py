"""P9-1 档位可配置 · 引擎 n 化 + 设置页切换。

验：档数 6–10 可切、档名/价格/计划长度跟随、模板按档数匹配、已买标记跨档数保留、
清洗函数上限放宽、非法档数被拒、老存档缺省仍为 6。

口径：H=4700、d=48 → p1=4089、p10=3657；总仓 200g。
10 档模板 [4,5,8,11,14,16,17,13,8,4] → 计划 [8,10,16,22,28,32,34,26,16,8]。
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H = 4700.0
W = 200.0
TPL6 = [8, 12, 24, 36, 12, 8]
TPL10 = [4, 5, 8, 11, 14, 16, 17, 13, 8, 4]


def bars_calm(n=240, px=PRICE, amp=24.0):
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


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W, planOnly=True,
             swingGears=[1, 2], gearMarks={}, alpha=1)
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
        return make_env(cfg=base, **mk)
    return _f


def plan(p):
    return p.evaluate("() => window.__xt.planOf()")


def prices(p):
    return p.evaluate("() => window.__xt.gearPrices(window.__xt.gearBase().he, window.__xt.gearBase().d)")


def seq(p):
    return p.evaluate("() => window.__xt.gearSeq()")


# ---------- 默认态 ----------

def test_g1_default_is_six(env):
    """不配 gearCount → 6，planOf 长度 6，权重仍是 P5 的默认值。"""
    p = env().page
    assert p.evaluate("() => window.__xt.gearCount()") == 6
    assert p.evaluate("() => window.__xt.curW()") == TPL6
    assert plan(p) == pytest.approx([16, 24, 48, 72, 24, 16], abs=0.01)


def test_g2_seq_follows_count(env):
    p = env().page
    assert seq(p) == ["第1档", "第2档", "第3档", "第4档", "第5档", "第6档"]
    p.evaluate("() => window.__xt.setGearCount(10)")
    s = seq(p)
    assert len(s) == 10 and s[6] == "第7档" and s[9] == "第10档"


def test_g3_price_len_and_last(env):
    """10 档：价格序列 10 个，第 10 档 = p1 − 9d。"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(10)")
    pr = prices(p)
    assert len(pr) == 10
    assert pr[0] == pytest.approx(4089.0, abs=0.01)
    assert pr[9] == pytest.approx(4089.0 - 9 * 48, abs=0.01)


# ---------- 切换 ----------

def test_g4_switch_to_ten(env):
    """6 → 10：计划长度变 10、总量仍 200g、按 10 档模板分配。"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(10)")
    pl = plan(p)
    assert len(pl) == 10
    assert sum(pl) == pytest.approx(W, abs=0.05)
    assert pl == pytest.approx([8, 10, 16, 22, 28, 32, 34, 26, 16, 8], abs=0.05)


def test_g5_template_matches_count(env):
    """每一档数都有专属模板，且总和为 100。"""
    p = env().page
    for n in (6, 7, 8, 9, 10):
        w = p.evaluate("(n) => window.__xt.gearTemplate(n)", n)
        assert len(w) == n, n
        assert sum(w) == 100, (n, w)


def test_g6_switch_back(env):
    """10 → 6：回到 6 档模板，长度与总量都复原。"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.evaluate("() => window.__xt.setGearCount(6)")
    pl = plan(p)
    assert len(pl) == 6
    assert pl == pytest.approx([16, 24, 48, 72, 24, 16], abs=0.01)


def test_g7_reject_illegal_count(env):
    """5 与 11 都被拒，档数保持原值。"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(5)")
    assert p.evaluate("() => window.__xt.gearCount()") == 6
    p.evaluate("() => window.__xt.setGearCount(11)")
    assert p.evaluate("() => window.__xt.gearCount()") == 6


def test_g8_old_save_without_count(env):
    """老存档没有 gearCount 字段 → 仍是 6，不炸。"""
    p = env(gearCount=None).page
    assert p.evaluate("() => window.__xt.gearCount()") == 6
    assert len(plan(p)) == 6


def test_g9_count_persists(env):
    """切到 8 档后，本地快照与云端推送体里都带着 gearCount=8。
    （测试里的云端 mock 不回写 PUT，所以查快照而不是 reload。）"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(8)")
    p.wait_for_timeout(300)
    snap = p.evaluate("""() => JSON.parse(localStorage.getItem('wb_xau_desk_snap') || '{}')""")
    assert snap.get("cfg", {}).get("gearCount") == 8
    assert snap["cfg"]["wPct"] == [5, 7, 11, 16, 20, 18, 14, 9]


# ---------- 执行层跟着放宽 ----------

def test_g10_mark_seventh_gear(env):
    """10 档下第 7 档可以买（6 档时会被直接拒）。"""
    p = env().page
    assert p.evaluate("() => window.__xt.markBuy(7, 10, 3800)") is False
    p.evaluate("() => window.__xt.setGearCount(10)")
    assert p.evaluate("() => window.__xt.markBuy(7, 10, 3800)") is True
    assert p.evaluate("() => window.__xt.gearState(7).bought") == 10


def test_g11_clean_marks_upper_bound(env):
    """清洗上限跟档数走：6 档拒 "10"，10 档接受 "10"。"""
    p = env().page
    assert p.evaluate("""() => Object.keys(window.__xt.cleanGearMarks(
        {"10": {g: 5}, "11": {g: 5}}))""") == []
    p.evaluate("() => window.__xt.setGearCount(10)")
    assert p.evaluate("""() => Object.keys(window.__xt.cleanGearMarks(
        {"10": {g: 5}, "11": {g: 5}}))""") == ["10"]


def test_g12_marks_survive_switch(env):
    """10 档买第 8 档 → 切 6 档 → 切回 10 档，标记与克重都还在。"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.evaluate("() => window.__xt.markBuy(8, 12, 3700)")
    p.evaluate("() => window.__xt.setGearCount(6)")
    p.wait_for_timeout(150)
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.wait_for_timeout(150)
    assert p.evaluate("() => window.__xt.gearState(8).bought") == 12


def test_g13_drip_accepts_deep_gear(env):
    """dripAt 清洗上限跟档数：10 档下 "9" 保留。"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(10)")
    n = p.evaluate("""() => { const o = window.__xt.cleanDripAt({"9": Date.now()}); return Object.keys(o).length; }""")
    assert n == 1


def test_g14_summary_covers_all(env):
    """10 档未买时，planSummary 的 pending 总和 = 总仓 200g。"""
    p = env().page
    p.evaluate("() => window.__xt.setGearCount(10)")
    s = p.evaluate("() => window.__xt.planSummary()")
    assert s["pending"] == pytest.approx(W, abs=0.1)
    assert s["bought"] == 0


def test_g15_realloc_conserves_on_ten(env):
    """10 档 + 配额下沉开启时，池子仍然守恒（sum(eff) == sum(base)）。"""
    p = env(realloc={"on": True, "mode": "near", "ratio": 1, "maxDrop": 3}).page
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.evaluate("() => window.__xt.setState && 1")
    p.wait_for_timeout(120)
    eff = plan(p)
    base = p.evaluate("() => window.__xt.planBase()")
    assert len(eff) == 10 and len(base) == 10
    assert sum(eff) == pytest.approx(sum(base), abs=0.05)


# ---------- UI ----------

def test_u1_five_buttons(env):
    """设置页有 6/7/8/9/10 五个档数按钮。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    btns = p.evaluate("""() => [...document.querySelectorAll('#setGearCount [data-gc]')]
        .map(b => b.textContent)""")
    assert btns == ["6", "7", "8", "9", "10"]


def test_u2_click_ten(env):
    """点 10 → 按钮态切换、权重格子变 10 个、标签同步。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(420)
    p.click('#setGearCount [data-gc="10"]')
    p.wait_for_timeout(420)
    on = p.evaluate("""() => document.querySelector('#setGearCount [aria-pressed="true"]').textContent""")
    assert on == "10"
    assert p.evaluate("() => document.querySelectorAll('#setWGrid [data-w]').length") == 10
    assert p.evaluate("() => document.querySelectorAll('#setWLab span').length") == 10
    assert p.evaluate("() => document.querySelectorAll('#setSwingGrid [data-sw]').length") == 10


def test_u3_ladder_rows(env):
    """10 档：默认折叠成「当前档 ±4」，点开后能看到第 10 档。
    （P9-4 引入折叠，P9-1 时这条断言的是 10 行全显示。）"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(400)
    p.click('#setGearCount [data-gc="10"]')
    p.wait_for_timeout(450)
    p.evaluate("() => window.scrollTo(0, 0)")
    p.wait_for_timeout(150)
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(500)
    names = p.evaluate("""() => [...document.querySelectorAll('#ladder .lv .nm')]
        .map(x => x.textContent.trim()).filter(Boolean)""")
    assert "第1档" in names
    assert "第10档" not in names, "当前档 2 → 只显示 1~6，7~10 应折叠"
    fold = p.query_selector("#ladder [data-fold]")
    assert fold is not None
    fold.click()
    p.wait_for_timeout(400)
    names2 = p.evaluate("""() => [...document.querySelectorAll('#ladder .lv .nm')]
        .map(x => x.textContent.trim()).filter(Boolean)""")
    assert "第10档" in names2
    assert len([n for n in names2 if n.startswith("第")]) == 10


def test_u4_no_overflow_320(env):
    """320px 下 10 格权重网格不产生横向溢出。"""
    p = env(width=320).page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(400)
    p.click('#setGearCount [data-gc="10"]')
    p.wait_for_timeout(450)
    assert p.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth") is True


def test_u5_weight_edit_on_ten(env):
    """10 档下改第 7 格权重，planOf 跟着变且总量不变。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(400)
    p.click('#setGearCount [data-gc="10"]')
    p.wait_for_timeout(450)
    p.fill('#setWGrid [data-w="6"]', "40")
    p.dispatch_event('#setWGrid [data-w="6"]', "change")
    p.wait_for_timeout(450)
    pl = plan(p)
    assert pl[6] > 60          # 权重 40% → 明显高于模板的 34g
    assert sum(pl) == pytest.approx(W, abs=0.1)


def test_u6_switch_toast(env):
    """切换档数给 toast 反馈。"""
    p = env().page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(400)
    p.click('#setGearCount [data-gc="9"]')
    p.wait_for_timeout(320)
    t = p.evaluate("() => { const x = document.getElementById('toast'); return x ? x.textContent : ''; }")
    assert "9 档" in t
