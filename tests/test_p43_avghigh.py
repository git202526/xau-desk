"""v6.18 单引擎 · 高点与目标的关系（原 v6.17「建仓高点统一」重写）。

全站只剩一个高点 cfg.H_BASE（$/oz），它**永远等于第 1 档价 × 1/0.87**：
  · 填了 → 第 1 档 = 0.87 × H，锁死
  · 留空 → 取自动高点（D60）
目标不再改高点，只改档距 d；两者互不覆盖（§4.6「最后编辑者优先」）。
"""
import json
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H_STRUCT = 4800.0
T = 3950.0
BAND = 20.0
TOTAL = 200
P1 = 0.87 * H_STRUCT          # 4176
D_3950 = (P1 - T) / 2.56      # 88.28125


def bars_calm(n=240, px=PRICE, amp=24.0):
    """ATR14 ≈ 48 美元。合成序列默认振幅只有 21 美元，档距容易撞 α 上限。"""
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


BARS = bars_calm()

# 目标 + 手动高点
CFG_T = dict(CFG_EMPTY, **{"H_BASE": H_STRUCT, "TOTAL": TOTAL,
                           "avgTarget": T, "avgBand": BAND})
# 目标 + 高点留空（自动）
CFG_AUTO = dict(CFG_EMPTY, **{"TOTAL": TOTAL, "avgTarget": T, "avgBand": BAND})
# 只有高点
CFG_H = dict(CFG_EMPTY, **{"H_BASE": H_STRUCT, "TOTAL": TOTAL})


def cfg_high(high, **kw):
    c = dict(CFG_T, H_BASE=high)
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


def base(page):
    return page.evaluate("() => window.__xt.gearBase()")


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


# ---------------------------------------------------------------- 高点来源

def test_ah1_manual_high_wins(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_plan(p)
    b = base(p)
    assert b["highManual"] is True
    assert b["he"] == H_STRUCT
    assert row(rows(p), "第1档")["price"] == pytest.approx(P1, abs=0.01)
    assert b["auto"]["h"] != H_STRUCT, "自动值不该被当成手动值"


def test_ah2_ladder_follows_manual_high(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_plan(p)
    p.evaluate("() => window.__xt.setHigh(5000)")
    p.wait_for_timeout(300)
    r = rows(p)
    assert row(r, "第1档")["price"] == pytest.approx(4350, abs=0.01)
    # 档距跟着目标反解，随高点抬高而变大
    assert base(p)["d"] == pytest.approx((4350 - T) / 2.56, abs=0.02)
    assert row(r, "第4档")["price"] == pytest.approx(4350 - 3 * base(p)["d"], abs=0.05)


def test_ah3_blank_high_uses_auto(make_env):
    """留空 → 自动高点（D60），不是反解旧高点。"""
    env = make_env(CFG_AUTO, bars=BARS)
    p = env.page
    b = base(p)
    assert b["highManual"] is False
    assert b["he"] == pytest.approx(b["auto"]["h"], abs=0.01)
    assert b["he"] == pytest.approx(PRICE, abs=1.0)
    assert row(rows(p), "第1档")["price"] == pytest.approx(0.87 * b["he"], abs=0.02)


def test_ah4_implied_avg_from_high(make_env):
    """高点锁死 + α 定档距 → 均价由档位推出来：4176 − 72×2.56。"""
    env = make_env(CFG_H, bars=BARS)
    p = env.page
    p.evaluate("() => window.__xt.setAlpha(1.5)")
    p.wait_for_timeout(300)
    m = meta(p)
    assert base(p)["d"] == pytest.approx(1.5 * 48, abs=0.15)
    assert m["avgAll"] == pytest.approx(4176 - 1.5 * 48 * 2.56, abs=1.0)
    assert m["target"] is None


def test_ah5_off_target_is_flagged(make_env):
    """档距按 α=0.8 排 → 均价明显高于目标，判不命中。"""
    env = make_env(CFG_H, bars=BARS)
    p = env.page
    p.evaluate("() => window.__xt.setAlpha(0.8)")
    p.evaluate("() => window.__xt.setTargetT(3950)")
    p.evaluate("() => window.__xt.setAlpha(0.8)")     # 最后编辑的是 α
    p.wait_for_timeout(320)
    m = meta(p)
    assert m["avgAll"] > T + 30
    assert m["hit"] is False
    assert m["spanSrc"] == "atr"


def test_ah6_high_equal_reverse_hits(make_env):
    """把 α 调到正好命中：d = 88.28 → α = 1.84。"""
    env = make_env(CFG_H, bars=BARS)
    p = env.page
    p.evaluate("() => window.__xt.setAlpha(1.84)")
    p.evaluate("() => window.__xt.setTargetT(3950)")
    p.evaluate("() => window.__xt.setAlpha(1.84)")
    p.wait_for_timeout(320)
    m = meta(p)
    assert m["avgAll"] == pytest.approx(T, abs=1.5)
    assert m["hit"] is True


def test_ah7_source_hint_shows_high(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_plan(p)
    t = txt(p, "#gearSrcBox")
    assert "4,800" in t or "4800" in t, t
    assert "手动" in t, t
    assert "档距" in t, t
    assert "88" in t, t          # d = 88.28


def test_ah8_card_shows_gap_to_price(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_plan(p)
    card = avgcard(p)
    assert "距现价" in card["text"], card["text"]
    assert "+" in card["text"]


def test_ah9_clear_high_follows_auto(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    p.evaluate("() => window.__xt.setHigh(null)")
    p.wait_for_timeout(300)
    b = base(p)
    assert b["highManual"] is False
    assert b["he"] == pytest.approx(PRICE, abs=1.0)


def test_ah10_save_high_from_settings(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_set(p)
    p.fill("#setHigh", "4900")
    p.click("#btnSaveSet")
    p.wait_for_timeout(340)
    assert p.evaluate("() => window.__xt.state().cfg.H_BASE") == 4900
    assert base(p)["he"] == 4900


def test_ah11_reject_out_of_range(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_set(p)
    p.fill("#setHigh", "900")
    p.click("#btnSaveSet")
    p.wait_for_timeout(320)
    assert p.evaluate("() => window.__xt.state().cfg.H_BASE") == H_STRUCT
    assert p.evaluate("() => document.getElementById('toast').textContent").strip() != ""


def test_ah12_reset_button_in_settings(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_set(p)
    p.click("#btnHighAuto")
    p.wait_for_timeout(320)
    assert p.evaluate("() => window.__xt.state().cfg.H_BASE") is None
    assert p.evaluate("() => window.__xt.gearBase().highManual") is False


def test_ah13_one_high_shared_by_both_spans(make_env):
    """核心：高点只有一份。改 α 不动高点，改目标也不动高点。"""
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    open_plan(p)
    h0 = base(p)["he"]
    pr1 = row(rows(p), "第1档")["price"]

    p.evaluate("() => window.__xt.setAlpha(2.0)")
    p.wait_for_timeout(280)
    assert base(p)["he"] == h0
    assert row(rows(p), "第1档")["price"] == pytest.approx(pr1, abs=0.01)
    assert base(p)["spanSrc"] == "atr"

    p.evaluate("() => window.__xt.setTargetT(3900)")
    p.wait_for_timeout(280)
    assert base(p)["he"] == h0
    assert row(rows(p), "第1档")["price"] == pytest.approx(pr1, abs=0.01)
    assert base(p)["spanSrc"] == "target"


def test_ah14_high_without_target_still_ladders(make_env):
    """只有高点、没目标：仍按高点排档位，档距按 α。"""
    env = make_env(CFG_H, bars=BARS)
    p = env.page
    open_plan(p)
    r = rows(p)
    assert len(r) == 8
    assert row(r, "第1档")["price"] == pytest.approx(P1, abs=0.01)
    b = base(p)
    assert b["target"] is None and b["spanSrc"] in ("atr", "fallback")
    card = avgcard(p)
    assert "还没设目标均价" in card["text"], card["text"]


@pytest.mark.parametrize("width", [320, 390])
def test_ah15_no_overflow_320(make_env, width):
    env = make_env(CFG_T, width=width, bars=BARS)
    p = env.page
    open_set(p)
    box = p.evaluate("""() => ({
        scroll: document.documentElement.scrollWidth,
        win: window.innerWidth,
        grid: (() => { const g = document.getElementById('setWGrid');
                       const c = g.closest('.card').getBoundingClientRect();
                       return [...g.querySelectorAll('input')].some(i => {
                           const r = i.getBoundingClientRect();
                           return r.right > c.right + 0.5 || r.left < c.left - 0.5; }); })(),
    })""")
    assert box["scroll"] <= box["win"] + 1, box
    assert box["grid"] is False, box


def test_ah16_persist_across_reload(make_env):
    env = make_env(CFG_T, bars=BARS)
    p = env.page
    p.evaluate("() => window.__xt.setHigh(4950)")
    p.wait_for_timeout(1300)
    p.reload(wait_until="domcontentloaded")
    p.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    p.wait_for_function("() => window.__xt.gearBase().he == 4950", timeout=6000)
    assert p.evaluate("() => window.__xt.state().cfg.H_BASE") == 4950


def test_ah17_legacy_avghigh_migrates(make_env):
    """老数据只有 avgHigh（v6.16 及以前）：升级后迁到 H_BASE，旧字段清零。"""
    c = dict(CFG_EMPTY, TOTAL=TOTAL, avgHigh=4800, avgTarget=T)
    env = make_env(c, bars=BARS)
    p = env.page
    assert p.evaluate("() => window.__xt.state().cfg.H_BASE") == 4800
    assert p.evaluate("() => window.__xt.state().cfg.avgHigh") is None
    assert base(p)["he"] == 4800


def test_ah18_auto_high_has_single_value(make_env):
    """留空时只有一种自动值：不会因为有无目标而算出两个高点。"""
    env = make_env(CFG_AUTO, bars=BARS)
    p = env.page
    h_with = base(p)["he"]
    p.evaluate("() => window.__xt.setTargetT(3950)")
    p.wait_for_timeout(280)
    assert base(p)["he"] == pytest.approx(h_with, abs=0.01)
    assert base(p)["he"] == pytest.approx(PRICE, abs=1.0)
    assert base(p)["auto"]["win"] in ("D60", "D20")
