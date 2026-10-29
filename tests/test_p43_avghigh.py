"""v6.17 建仓高点统一（原 v6.14「均价模式自定义高点」的升级版）。

口径：全站只剩一个高点 cfg.H_BASE（$/oz，= 第 1 档价），两种模式共用。
  H_BASE 留空 → 自动：结构模式取 D60 自动基准；目标均价模式由目标反解 avgBase(T)
  H_BASE 填了 → 锁死：两种模式都按它排，均价由档位推出来（1–4 档 = H × 0.848286）
  rev 始终保留反解值，用来对照「高点顶到多少」
"""
import json

import pytest

from conftest import BASE, CFG_EMPTY

H_STRUCT = 4800.0
T = 3950.0
BAND = 20.0
TOTAL = 200
REV = 4656.45          # avgBase(3950)
K14 = 0.848286         # 1–4 档加权均价系数

# 均价模式 + 高点留空 → 反解
CFG_AVG = dict(CFG_EMPTY, **{
    "TOTAL": TOTAL,
    "gearMode": "avg", "avgTarget": T, "avgBand": BAND,
})


def cfg_high(high, **kw):
    """均价模式 + 手动高点。"""
    c = dict(CFG_AVG, H_BASE=high)
    c.update(kw)
    return c


def open_plan(page):
    page.click('.tabbar button[data-tab="plan"]')
    page.wait_for_timeout(280)


def open_set(page):
    page.click('.tabbar button[data-tab="set"]')
    page.wait_for_timeout(280)


def rows(page):
    return page.evaluate("""() => [...document.querySelectorAll('#ladder .lv')].map(l => ({
        name: l.querySelector('.nm').textContent.trim(),
        price: parseFloat(l.querySelector('.price').textContent.replace(/,/g, '')),
        g: (l.querySelectorAll('.r2 .pill.g')[0] || {}).textContent || '',
    }))""")


def row(rs, name):
    for r in rs:
        if r["name"] == name:
            return r
    raise AssertionError("找不到档位 %s" % name)


def meta(page):
    return page.evaluate("() => window.__xt.gearMeta()")


def txt(page, sel):
    return page.evaluate("(s) => (document.querySelector(s) || {}).textContent || ''", sel)


def avgcard(page):
    return page.evaluate("""() => {
        const b = document.getElementById('avgCard');
        if (!b || !b.textContent.trim()) return null;
        const c = b.querySelector('circle[r="3.8"]');
        return {
            text: b.textContent.replace(/\\s+/g, ' ').trim(),
            dot: c ? c.getAttribute('fill') : null,
            sw: b.scrollWidth, cw: b.clientWidth,
        };
    }""")


# ---------- 基准来源 ----------


def test_ah1_manual_high_wins(make_env):
    env = make_env(cfg_high(4800))
    page = env.page
    open_plan(page)
    m = meta(page)
    assert m["mode"] == "avg", m
    assert m["highManual"] is True, m
    assert abs(m["he"] - 4800) < 0.01, m
    assert abs(m["rev"] - REV) < 0.05, m


def test_ah2_ladder_follows_manual_high(make_env):
    env = make_env(cfg_high(4800))
    page = env.page
    open_plan(page)
    rs = rows(page)
    # 4800 × (1-13%) = 4176；× (1-16%) = 4032
    assert abs(row(rs, "第1档")["price"] - 4176) < 0.05, rs
    assert abs(row(rs, "第4档")["price"] - 4032) < 0.05, rs
    # 与结构基准 4800 时完全一致：锁高点就是把锚换回 H
    assert abs(row(rs, "第6档")["price"] - 3936) < 0.05, rs


def test_ah3_blank_high_keeps_reverse_solving(make_env):
    """留空必须还是反解，不能因为加了字段就改了默认行为。"""
    env = make_env(CFG_AVG)
    page = env.page
    open_plan(page)
    m = meta(page)
    assert m["highManual"] is False, m
    assert abs(m["he"] - REV) < 0.05, m
    assert abs(row(rows(page), "第4档")["price"] - 3911.42) < 0.05, rows(page)


def test_ah4_implied_avg_from_high(make_env):
    """锁高点后，1–4 档均价由档位推出来：4800 × 0.848286 = 4071.8。"""
    env = make_env(cfg_high(4800))
    page = env.page
    open_plan(page)
    m = meta(page)
    assert abs(m["avg14"] - 4800 * K14) < 0.5, m
    assert abs(m["avg14"] - 4071.8) < 0.5, m


def test_ah5_off_target_is_flagged(make_env):
    """高点 4800 明显高于反解 4656，均价必然偏高：整体均价判不命中。"""
    env = make_env(cfg_high(4800))
    page = env.page
    open_plan(page)
    m = meta(page)
    assert m["mix"] is not None, m
    assert m["mix"]["avg"] > T + BAND, m["mix"]
    assert m["hit"] is False, m
    card = avgcard(page)
    assert card is not None
    assert card["dot"] == "#E0913F", card
    assert "偏高" in card["text"], card["text"]


def test_ah6_high_equal_reverse_hits(make_env):
    """高点正好等于反解值 → 与留空时等价，命中。"""
    env = make_env(cfg_high(round(REV, 2)))
    page = env.page
    open_plan(page)
    m = meta(page)
    assert m["highManual"] is True, m
    assert m["hit"] is True, m
    card = avgcard(page)
    assert card["dot"] == "#3FBF7F", card
    assert "命中" in card["text"], card["text"]


# ---------- 卡片与来源条 ----------


def test_ah7_source_hint_shows_high(make_env):
    env = make_env(cfg_high(4800))
    page = env.page
    open_plan(page)
    hint = txt(page, "#gearSrcBox .gshint")
    assert "手动" in hint, hint
    assert "4800" in hint, hint
    assert "反解需" in hint, hint
    ladder_meta = txt(page, "#ladderMeta")
    assert "手动" in ladder_meta, ladder_meta
    assert "4800" in ladder_meta, ladder_meta


def test_ah8_card_shows_gap_and_reset(make_env):
    env = make_env(cfg_high(4800))
    page = env.page
    open_plan(page)
    card = avgcard(page)
    assert "高点" in card["text"] and "手动" in card["text"], card["text"]
    assert "反解" in card["text"], card["text"]
    assert page.eval_on_selector_all("#avgCard [data-avg-sync]", "els => els.length") == 1


def test_ah9_reset_button_restores_reverse(make_env):
    env = make_env(cfg_high(4800))
    page = env.page
    open_plan(page)
    page.click("#avgCard [data-avg-sync]")
    page.wait_for_timeout(1500)          # saveCfg 有 800ms 防抖
    assert meta(page)["highManual"] is False, meta(page)
    assert meta(page)["he"] < 4700, meta(page)
    assert env.gh.cfg.get("H_BASE") is None, env.gh.cfg.get("H_BASE")
    # 第 4 档回到反解价
    assert abs(row(rows(page), "第4档")["price"] - 3911.42) < 0.05, rows(page)


# ---------- 设置页 ----------


def test_ah10_save_high_from_settings(make_env):
    env = make_env(CFG_AVG)
    page = env.page
    open_set(page)
    # 「当前生效」回显：反解值 + 来源
    live = page.eval_on_selector("#setHighLive", "e => e.value")
    assert live.startswith("4656"), live
    assert "反解" in live, live
    page.fill("#setHigh", "5000")
    page.click("#btnSaveSet")
    page.wait_for_timeout(1500)
    assert env.gh.cfg.get("H_BASE") == 5000, env.gh.cfg.get("H_BASE")
    open_plan(page)
    assert abs(meta(page)["he"] - 5000) < 0.01, meta(page)
    assert abs(row(rows(page), "第4档")["price"] - 4200) < 0.05, rows(page)


def test_ah11_reject_out_of_range(make_env):
    env = make_env(CFG_AVG)
    page = env.page
    open_set(page)
    page.fill("#setHigh", "200")
    page.click("#btnSaveSet")
    page.wait_for_timeout(400)
    hint = txt(page, "#toast")
    assert "高点" in hint, hint
    assert env.gh.cfg.get("H_BASE") in (None, 0), env.gh.cfg.get("H_BASE")
    assert page.evaluate("() => window.__xt.state().cfg.H_BASE") is None


def test_ah12_reset_button_in_settings(make_env):
    env = make_env(cfg_high(4800))
    page = env.page
    open_set(page)
    assert page.eval_on_selector("#setHigh", "e => e.value") == "4800"
    page.click("#btnHighAuto")
    page.wait_for_timeout(1500)
    assert env.gh.cfg.get("H_BASE") is None
    assert page.eval_on_selector("#setHigh", "e => e.value") == ""


def test_ah13_one_high_shared_by_both_modes(make_env):
    """核心需求：不分成两个高点。结构模式下填的值，切到均价模式原样沿用，档位价不动。"""
    env = make_env(cfg_high(4800, gearMode="struct"))
    page = env.page
    open_plan(page)
    m = meta(page)
    assert m["mode"] == "struct", m
    assert abs(m["he"] - 4800) < 0.01, m
    assert abs(row(rows(page), "第4档")["price"] - 4032) < 0.05, rows(page)

    page.click('#gearSrcBox [data-gmode="avg"]')
    page.wait_for_timeout(1500)
    m2 = meta(page)
    assert m2["mode"] == "avg", m2
    assert abs(m2["he"] - 4800) < 0.01, m2               # 高点没被悄悄改成反解值
    assert m2["highManual"] is True, m2
    assert abs(row(rows(page), "第4档")["price"] - 4032) < 0.05, rows(page)
    assert env.gh.cfg.get("H_BASE") == 4800, env.gh.cfg.get("H_BASE")

    page.click('#gearSrcBox [data-gmode="struct"]')
    page.wait_for_timeout(1500)
    m3 = meta(page)
    assert m3["mode"] == "struct", m3
    assert abs(m3["he"] - 4800) < 0.01, m3


# ---------- 边界 ----------


def test_ah14_high_without_target_still_ladders(make_env):
    """只有高点、没目标：仍按高点排档位，卡片给出提示而不是空转。"""
    env = make_env(dict(CFG_EMPTY, **{
        "TOTAL": TOTAL, "H_BASE": H_STRUCT,
        "gearMode": "avg", "avgTarget": None,
    }))
    page = env.page
    open_plan(page)
    m = meta(page)
    assert m["mode"] == "avg", m
    assert m["target"] is None, m
    assert abs(m["he"] - 4800) < 0.01, m
    card = avgcard(page)
    assert card is not None, "没目标也要有卡片"
    assert "没有目标均价" in card["text"] or "还没填目标均价" in card["text"], card["text"]
    assert page.eval_on_selector_all("#avgCard [data-go]", "els => els.length") == 1


def test_ah15_no_overflow_320(make_env):
    env = make_env(cfg_high(4800), width=320, height=844)
    page = env.page
    open_plan(page)
    info = page.evaluate("""() => {
        const h = document.querySelector('#gearSrcBox .gshint');
        const c = document.getElementById('avgCard');
        return {
            sw: document.documentElement.scrollWidth, iw: innerWidth,
            hintOver: h ? h.scrollWidth - h.clientWidth : 0,
            cardOver: c ? c.scrollWidth - c.clientWidth : 0,
        };
    }""")
    assert info["sw"] <= info["iw"], info
    assert info["hintOver"] <= 1, info
    assert info["cardOver"] <= 1, info


def test_ah16_persist_across_reload(make_env):
    env = make_env(cfg_high(4800))
    page = env.page
    page.evaluate("(v) => window.__xt.setHigh(v)", 4900)
    page.wait_for_timeout(1500)
    assert env.gh.cfg.get("H_BASE") == 4900, env.gh.cfg.get("H_BASE")
    page.goto(BASE + "/index.html?xt=1")
    page.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    page.wait_for_timeout(1200)
    assert page.evaluate("() => window.__xt.state().cfg.H_BASE") == 4900


def test_ah17_legacy_avghigh_migrates(make_env):
    """老数据只有 avgHigh（v6.16 及以前）：升级后高点迁到 H_BASE，行为不变，旧字段清零。"""
    cfg = dict(CFG_EMPTY, **{
        "TOTAL": TOTAL, "gearMode": "avg", "avgTarget": T, "avgBand": BAND,
        "avgHigh": 4850,
    })
    env = make_env(cfg)
    page = env.page
    open_plan(page)
    m = meta(page)
    assert m["highManual"] is True, m
    assert abs(m["he"] - 4850) < 0.01, m
    assert page.evaluate("() => window.__xt.state().cfg.avgHigh") is None
    assert page.evaluate("() => window.__xt.state().cfg.H_BASE") == 4850
    assert abs(row(rows(page), "第4档")["price"] - 4074) < 0.05, rows(page)   # 4850 × 0.84


def test_ah18_auto_high_differs_by_mode(make_env):
    """同一个「留空」状态，两种模式各自动取值：结构取 D60，均价取反解。"""
    env = make_env(dict(CFG_EMPTY, **{
        "TOTAL": TOTAL, "gearMode": "struct", "avgTarget": T, "avgBand": BAND,
    }))
    page = env.page
    open_plan(page)
    struct_he = meta(page)["he"]
    page.click('#gearSrcBox [data-gmode="avg"]')
    page.wait_for_timeout(1500)
    m = meta(page)
    assert m["mode"] == "avg", m
    assert m["highManual"] is False, m
    assert abs(m["he"] - REV) < 0.05, m
    assert abs(m["he"] - struct_he) > 1, (m["he"], struct_he)
