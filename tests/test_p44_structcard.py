"""v6.15 结构基准下的推导卡片。

结构模式不换档位价，只回答两个问题：
  · 按这套基准买完，1–4 档均价落在哪（= H × 0.848286）
  · 要让它落在目标上，基准该调到哪（= avgBase(T)）
"""
import json

import pytest

from conftest import CFG_EMPTY

H = 4800.0
T = 3950.0
BAND = 20.0
TOTAL = 200
K14 = 0.848286
A14 = H * K14               # 4071.8
SUGGEST = 4656.45           # avgBase(3950)

STRUCT_PRICES = [4320.0, 4224.0, 4176.0, 4128.0, 4080.0, 4032.0, 3984.0, 3936.0]

CFG_ST = dict(CFG_EMPTY, **{"H_BASE": H, "TOTAL": TOTAL})
CFG_ST_T = dict(CFG_EMPTY, **{
    "H_BASE": H, "TOTAL": TOTAL, "gearMode": "struct",
    "avgTarget": T, "avgBand": BAND,
})


def open_plan(page):
    page.click('.tabbar button[data-tab="plan"]')
    page.wait_for_timeout(280)


def rows(page):
    return page.evaluate("""() => [...document.querySelectorAll('#ladder .lv')].map(l => ({
        name: l.querySelector('.nm').textContent.trim(),
        price: parseFloat(l.querySelector('.price').textContent.replace(/,/g, '')),
    }))""")


def meta(page):
    return page.evaluate("() => window.__xt.gearMeta()")


def card(page):
    return page.evaluate("""() => {
        const b = document.getElementById('avgCard');
        if (!b || !b.textContent.trim()) return null;
        const c = b.querySelector('circle[r="3.8"]');
        return {
            text: b.textContent.replace(/\\s+/g, ' ').trim(),
            dot: c ? c.getAttribute('fill') : null,
            svg: b.querySelectorAll('svg').length,
            over: b.scrollWidth - b.clientWidth,
        };
    }""")


# ---------- 有目标 ----------


def test_sc1_card_exists_in_struct(make_env):
    env = make_env(CFG_ST_T)
    page = env.page
    open_plan(page)
    c = card(page)
    assert c is not None, "结构模式也要有推导卡"
    assert "1–4 档均价" in c["text"], c["text"]
    assert c["svg"] == 1, c


def test_sc2_plan_avg_matches_formula(make_env):
    env = make_env(CFG_ST_T)
    page = env.page
    open_plan(page)
    m = meta(page)
    assert m["mode"] == "struct", m
    assert abs(m["avg14"] - A14) < 0.5, m["avg14"]
    assert ("%.1f" % A14) in card(page)["text"], card(page)["text"]


def test_sc3_off_target_verdict(make_env):
    """4071.8 对 3950 是偏高 121.8，必然不命中。"""
    env = make_env(CFG_ST_T)
    page = env.page
    open_plan(page)
    c = card(page)
    assert "偏高" in c["text"], c["text"]
    assert "命中" not in c["text"], c["text"]
    assert c["dot"] == "#E0913F", c["dot"]


def test_sc4_suggest_base_shown(make_env):
    env = make_env(CFG_ST_T)
    page = env.page
    open_plan(page)
    m = meta(page)
    assert abs(m["suggest"] - SUGGEST) < 0.05, m["suggest"]
    c = card(page)
    assert "高点应为" in c["text"], c["text"]
    assert "4656" in c["text"], c["text"]
    # 现高点 4800 比建议值 4656 高 144
    assert "现在高 144" in c["text"], c["text"]


def test_sc5_hit_when_target_matches(make_env):
    """目标就填成 4071.8，应判命中，圆点转绿。"""
    cfg = dict(CFG_ST_T, avgTarget=round(A14, 1))
    env = make_env(cfg)
    page = env.page
    open_plan(page)
    c = card(page)
    assert "命中" in c["text"], c["text"]
    assert c["dot"] == "#3FBF7F", c["dot"]


def test_sc6_dist_to_price(make_env):
    env = make_env(CFG_ST_T)
    page = env.page
    open_plan(page)
    c = card(page)
    # 4800 / 4072.40 - 1 = +17.9%
    assert "距现价" in c["text"], c["text"]
    assert "+17.9%" in c["text"], c["text"]


# ---------- 无目标 ----------


def test_sc7_no_target_still_costs_card(make_env):
    env = make_env(CFG_ST)
    page = env.page
    open_plan(page)
    c = card(page)
    assert c is not None
    assert "未设" in c["text"], c["text"]
    assert "还没设目标均价" in c["text"], c["text"]
    assert c["svg"] == 0, "没目标不画区间带"
    assert c["dot"] is None
    assert page.eval_on_selector_all("#avgCard [data-go]", "els => els.length") == 1
    assert page.eval_on_selector_all("#avgCard [data-set-mode]", "els => els.length") == 0


# ---------- 不改档位 ----------


def test_sc8_prices_untouched(make_env):
    env = make_env(CFG_ST_T)
    page = env.page
    open_plan(page)
    rs = rows(page)
    assert len(rs) == 8, rs
    for r, want in zip(rs, STRUCT_PRICES):
        assert abs(r["price"] - want) < 0.05, (r, want)


def test_sc9_switch_keeps_manual_high(make_env):
    """共用一个高点：手动填了 4800，切到目标均价模式后档位价一个都不动。"""
    env = make_env(CFG_ST_T)
    page = env.page
    open_plan(page)
    page.click("#avgCard [data-set-mode]")
    page.wait_for_timeout(1500)          # saveCfg 有 800ms 防抖
    m = meta(page)
    assert m["mode"] == "avg", m
    assert m["highManual"] is True, m
    assert abs(m["he"] - H) < 0.01, m
    assert env.gh.cfg.get("gearMode") == "avg", env.gh.cfg.get("gearMode")
    assert env.gh.cfg.get("H_BASE") == H, env.gh.cfg.get("H_BASE")
    rs = rows(page)
    for r, want in zip(rs, STRUCT_PRICES):
        assert abs(r["price"] - want) < 0.05, (r, want)


def test_sc9b_switch_to_avg_uses_reverse_when_auto(make_env):
    """高点留空时切到均价模式：第 4 档换成反解价 3911.4。"""
    env = make_env(dict(CFG_EMPTY, **{"TOTAL": TOTAL, "gearMode": "struct", "avgTarget": T, "avgBand": BAND}))
    page = env.page
    open_plan(page)
    page.click("#avgCard [data-set-mode]")
    page.wait_for_timeout(1500)
    m = meta(page)
    assert m["mode"] == "avg", m
    assert m["highManual"] is False, m
    assert abs(m["he"] - SUGGEST) < 0.05, m
    r4 = [r for r in rows(page) if r["name"] == "第4档"][0]
    assert abs(r4["price"] - 3911.42) < 0.05, r4


def test_sc12_use_suggest_as_high(make_env):
    """卡上的「用 4656 作高点」按钮：一把把建议值写成高点，档位随即切换，均价回到目标。"""
    env = make_env(CFG_ST_T)
    page = env.page
    open_plan(page)
    page.click("#avgCard [data-use-suggest]")
    page.wait_for_timeout(1500)
    m = meta(page)
    assert m["highManual"] is True, m
    assert abs(m["he"] - 4656) < 0.6, m
    assert m["hit"] is True, m
    c = card(page)
    assert "命中" in c["text"], c["text"]
    assert c["dot"] == "#3FBF7F", c["dot"]


# ---------- 布局 ----------


def test_sc10_no_overflow_320(make_env):
    env = make_env(CFG_ST_T, width=320, height=844)
    page = env.page
    open_plan(page)
    info = page.evaluate("""() => ({
        sw: document.documentElement.scrollWidth, iw: innerWidth,
        cardOver: (() => { const c = document.getElementById('avgCard');
                           return c ? c.scrollWidth - c.clientWidth : 0; })(),
    })""")
    assert info["sw"] <= info["iw"], info
    assert info["cardOver"] <= 1, info


def test_sc11_auto_base_also_works(make_env):
    """基准靠自动（没填 H_BASE）时卡片也要出数，不能只认手动基准。"""
    env = make_env(dict(CFG_EMPTY, **{"TOTAL": TOTAL, "avgTarget": T, "avgBand": BAND}))
    page = env.page
    open_plan(page)
    m = meta(page)
    assert m["mode"] == "struct", m
    assert m["he"] and m["he"] > 0, m
    assert m["avg14"] and abs(m["avg14"] - m["he"] * K14) < 0.5, m
    c = card(page)
    assert c is not None
    assert "自动" in c["text"], c["text"]
