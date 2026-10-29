"""v6.12 档位双逻辑：目标均价导向。

核心口径（见 docs/开发计划书-P4-均价导向与事件日历.md 第 1 章）：
  r̄ = Σ(w_i × r_i)/Σw_i  (i = 1..4 档) = -15.171429%
  H_avg = T / (1 + r̄/100)
  1–4 档按计划成交的加权均价 = H_avg × (1 + r̄/100) = T
"""
import json

import pytest

from conftest import BASE, CFG_EMPTY, PRICE

H_STRUCT = 4800.0
T = 3950.0
BAND = 20.0
TOTAL = 200

# 结构基准 4800 下的档位价：预警 4320 / 4224，第1–6档 4176 / 4128 / 4080 / 4032 / 3984 / 3936
STRUCT_PRICES = [4320.0, 4224.0, 4176.0, 4128.0, 4080.0, 4032.0, 3984.0, 3936.0]

CFG_STRUCT = dict(CFG_EMPTY, **{"H_BASE": H_STRUCT, "TOTAL": TOTAL})
# v6.17 起高点与模式共用：均价模式要「反解」，H_BASE 就必须留空
CFG_AVG = dict(CFG_EMPTY, **{
    "TOTAL": TOTAL,
    "gearMode": "avg", "avgTarget": T, "avgBand": BAND,
})
CFG_NO_T = dict(CFG_EMPTY, **{"TOTAL": TOTAL})
CFG_AUTO_STRUCT = dict(CFG_EMPTY, **{
    "TOTAL": TOTAL, "gearMode": "struct", "avgTarget": T, "avgBand": BAND,
})


def open_plan(page):
    page.click('.tabbar button[data-tab="plan"]')
    page.wait_for_timeout(280)


def rows(page):
    return page.evaluate("""() => [...document.querySelectorAll('#ladder .lv')].map(l => ({
        name: l.querySelector('.nm').textContent.trim(),
        price: parseFloat(l.querySelector('.price').textContent.replace(/,/g, '')),
        plan: (l.querySelector('.plan') || {}).textContent || '',
        g: (l.querySelectorAll('.r2 .pill.g')[0] || {}).textContent || '',
        dist: (l.querySelector('.dist') || {}).textContent || '',
    }))""")


def row(rs, name):
    for r in rs:
        if r["name"] == name:
            return r
    raise AssertionError("找不到档位 %s：%s" % (name, json.dumps(rs, ensure_ascii=False)))


def meta(page):
    return page.evaluate("() => window.__xt.gearMeta()")


def txt(page, sel):
    return page.evaluate("(s) => (document.querySelector(s) || {}).textContent || ''", sel)


def mode_of(page):
    return page.evaluate("() => window.__xt.gearBase().mode")


def avgcard(page):
    return page.evaluate("""() => {
        const b = document.getElementById('avgCard');
        if (!b || !b.textContent.trim()) return null;
        const c = b.querySelector('circle[r="3.8"]');
        return {
            text: b.textContent.replace(/\\s+/g, ' ').trim(),
            dot: c ? c.getAttribute('fill') : null,
            svg: b.querySelectorAll('svg').length,
        };
    }""")


# ---------- 公式 ----------

def test_av1_avg_base(make_env):
    env = make_env(CFG_STRUCT)
    v = env.page.evaluate("() => window.__xt.avgBase(3950)")
    assert abs(v - 4656.45) < 0.05, v


def test_av14_avg_base_moves_with_target(make_env):
    env = make_env(CFG_STRUCT)
    a = env.page.evaluate("() => window.__xt.avgBase(3950)")
    b = env.page.evaluate("() => window.__xt.avgBase(4000)")
    assert abs(b - 4715.39) < 0.05, b
    # 目标抬高 50，基准同比抬高约 58.9（1/0.848286）
    assert abs((b - a) - 50 / 0.848286) < 0.1, (a, b)


def test_av2_fourth_gear_price(make_env):
    env = make_env(CFG_AVG)
    page = env.page
    open_plan(page)
    r = row(rows(page), "第4档")
    assert abs(r["price"] - 3911.42) < 0.05, r
    # 第 1 档同步换锚
    assert abs(row(rows(page), "第1档")["price"] - 4051.11) < 0.05, rows(page)


def test_av3_plan_avg_main(make_env):
    env = make_env(CFG_AVG)
    v = env.page.evaluate(
        "() => window.__xt.planAvg(window.__xt.avgBase(3950), ['第1档','第2档','第3档','第4档'])")
    assert abs(v - 3950.0) < 0.5, v


def test_av4_plan_avg_all(make_env):
    env = make_env(CFG_AVG)
    v = env.page.evaluate("() => window.__xt.planAvg(window.__xt.avgBase(3950), null)")
    assert abs(v - 3917.47) < 0.5, v
    # 全 6 档必然低于目标：5/6 档是超跌追加
    assert v < 3950, v


# ---------- 回归：结构模式不动 ----------

def test_av5_struct_mode_unchanged(make_env):
    env = make_env(CFG_STRUCT)
    page = env.page
    open_plan(page)
    rs = rows(page)
    assert len(rs) == 8, rs
    for r, want in zip(rs, STRUCT_PRICES):
        assert abs(r["price"] - want) < 0.05, (r, want)
    # v6.15 起结构模式也有推导卡（需求变更）：本用例没设目标，所以只给落点、不画区间带
    card = avgcard(page)
    assert card is not None, "结构模式应有推导卡"
    assert "高点" in card["text"] and "1–4 档均价" in card["text"], card["text"]
    assert card["dot"] is None, "没设目标就不该有落点圆点"
    assert card["svg"] == 0, "没设目标就不画带状图"


def test_av16_avg_mode_keeps_takes_levels(make_env):
    env = make_env(dict(CFG_STRUCT, **{
        "buys": [{"id": "b1700000000009", "name": "首笔", "gram": 40, "priceCny": 900.0,
                  "time": "2026-09-01T02:00:00.000Z", "note": "", "gear": None}],
        "keepGram": 10,
    }))
    page = env.page
    page.wait_for_timeout(300)
    before = page.evaluate("""() => ({
        takes: (window.__xt.state().takes || []).map(t => [t.price, t.pct, t.gram]),
        levels: window.__xt.buildLevels(45, {extended:false}).map(l => l.price),
        stop: window.__xt.state().stopLine || null,
    })""")
    page.evaluate("() => { window.__xt.state().cfg.avgTarget = 3950; window.__xt.setGearMode('avg'); }")
    page.wait_for_timeout(300)
    after = page.evaluate("""() => ({
        takes: (window.__xt.state().takes || []).map(t => [t.price, t.pct, t.gram]),
        levels: window.__xt.buildLevels(45, {extended:false}).map(l => l.price),
        stop: window.__xt.state().stopLine || null,
    })""")
    assert mode_of(page) == "avg"
    assert after["takes"] == before["takes"], (before["takes"], after["takes"])
    assert after["levels"] == before["levels"]
    assert after["stop"] == before["stop"]


# ---------- 切换 ----------

def test_av6_switch_keeps_records(make_env):
    cfg = dict(CFG_STRUCT, **{
        "avgTarget": T,
        "gearsDone": ["第1档"],
        "buys": [{"id": "b1700000000011", "name": "第1档", "gram": 16, "priceCny": 830.0,
                  "time": "2026-09-10T02:00:00.000Z", "note": "", "gear": "第1档"}],
    })
    env = make_env(cfg)
    page = env.page
    open_plan(page)
    before = page.evaluate("""() => {
        const s = window.__xt.state();
        return { buys: (s.cfg.buys||[]).length, sells: (s.cfg.sells||[]).length,
                 grams: window.__xt.holdStats().grams,
                 done: (s.cfg.gearsDone||[]).slice().sort() };
    }""")
    page.evaluate("() => window.__xt.setGearMode('avg')")
    page.wait_for_timeout(350)
    after = page.evaluate("""() => {
        const s = window.__xt.state();
        return { buys: (s.cfg.buys||[]).length, sells: (s.cfg.sells||[]).length,
                 grams: window.__xt.holdStats().grams,
                 done: (s.cfg.gearsDone||[]).slice().sort() };
    }""")
    assert mode_of(page) == "avg"
    assert after == before, (before, after)
    # 已买入标记不因换锚而消失
    assert "已买" in row(rows(page), "第1档")["g"]


def test_av7_switch_updates_and_saves(make_env):
    env = make_env(CFG_AUTO_STRUCT)
    page = env.page
    open_plan(page)
    p0 = row(rows(page), "第4档")["price"]
    puts0 = env.gh.puts
    page.evaluate("() => { window.__xt.state().cfg.avgTarget = 3950; window.__xt.setGearMode('avg'); }")
    page.wait_for_timeout(1500)   # saveCfg 有 800ms 防抖
    p1 = row(rows(page), "第4档")["price"]
    # 高点留空：结构模式用 D60 自动高点，切到均价模式换成反解高点，档位价必须跟着换
    assert abs(p1 - 3911.42) < 0.05, (p0, p1)
    assert abs(p0 - p1) > 50, (p0, p1)
    assert env.gh.puts > puts0, (puts0, env.gh.puts)
    assert env.gh.cfg.get("gearMode") == "avg", env.gh.cfg.get("gearMode")


def test_av8_no_target_refuses_switch(make_env):
    """目标与高点都没有 → 均价模式没有锚，按钮禁用、强制切也切不过去。"""
    env = make_env(CFG_NO_T)
    page = env.page
    open_plan(page)
    btn = page.query_selector('#gearSrcBox button[data-gmode="avg"]')
    assert btn is not None
    assert btn.get_attribute("disabled") is not None
    # 点不动（按钮禁用），模式仍是结构
    page.evaluate("() => window.__xt.setGearMode('avg')")
    page.wait_for_timeout(250)
    assert mode_of(page) == "struct"
    assert "目标均价" in txt(page, "#toast")

    # v6.17：只要有手动高点，没目标也能切——高点本身就是锚
    page.evaluate("() => window.__xt.setHigh(4800)")
    page.wait_for_timeout(1500)
    page.evaluate("() => window.__xt.setGearMode('avg')")
    page.wait_for_timeout(300)
    assert mode_of(page) == "avg"
    assert abs(row(rows(page), "第4档")["price"] - 4032) < 0.05, rows(page)


# ---------- 克重与额度 ----------

def test_av11_gram_scales_by_plan(make_env):
    cfg = dict(CFG_AVG, **{"gearsDone": ["第1档"]})
    env = make_env(cfg)
    page = env.page
    open_plan(page)
    rs = rows(page)
    # 均价模式：gram = plan × (remain/total)，无持仓时 remain=total → 等于 plan
    assert "待买 24.0g" in row(rs, "第2档")["g"], row(rs, "第2档")
    assert "待买 80.0g" in row(rs, "第4档")["g"], row(rs, "第4档")

    env2 = make_env(dict(cfg, **{"buys": [
        {"id": "b1700000000021", "name": "零散", "gram": 50, "priceCny": 900.0,
         "time": "2026-09-01T02:00:00.000Z", "note": "", "gear": None}]}))
    page2 = env2.page
    open_plan(page2)
    # remain=150, k=0.75 → 第4档 60g（结构模式会给 150×40/92=65.2g）
    assert "待买 60.0g" in row(rows(page2), "第4档")["g"], rows(page2)


def test_av12_short_quota_hint(make_env):
    cfg = dict(CFG_AVG, **{"buys": [
        {"id": "b1700000000031", "name": "已建", "gram": 100, "priceCny": 900.0,
         "time": "2026-09-01T02:00:00.000Z", "note": "", "gear": None}]})
    env = make_env(cfg)
    page = env.page
    open_plan(page)
    m = meta(page)
    assert m["short"] is True, m
    assert abs(m["needMain"] - 140.0) < 0.2, m["needMain"]
    card = avgcard(page)
    assert card is not None
    assert "买不满" in card["text"], card["text"]


# ---------- 校验卡 ----------

def test_av9_card_hit(make_env):
    env = make_env(CFG_AVG)
    page = env.page
    open_plan(page)
    card = avgcard(page)
    assert card is not None, "均价模式应显示校验卡"
    assert card["svg"] == 1
    assert card["dot"] == "#3FBF7F", card["dot"]
    assert "命中" in card["text"], card["text"]
    assert "3,950" in card["text"] or "3950" in card["text"], card["text"]


def test_av10_card_off_target(make_env):
    """理论均价恒等于目标，所以偏离只可能来自「已持仓成本」。

    这里手上有 100g 成本 950 元/克（≈$4161/oz），后续档位再便宜也拉不回 3950，
    整体均价必然偏高——这正是校验卡要暴露的情况。
    """
    cfg = dict(CFG_AVG, **{"buys": [
        {"id": "b1700000000041", "name": "高位建仓", "gram": 100, "priceCny": 950.0,
         "time": "2026-09-01T02:00:00.000Z", "note": "", "gear": None}]})
    env = make_env(cfg)
    page = env.page
    open_plan(page)
    card = avgcard(page)
    assert card is not None
    assert card["dot"] == "#E0913F", card["dot"]
    assert "偏高" in card["text"], card["text"]
    assert "命中" not in card["text"], card["text"]
    m = meta(page)
    assert m["mix"]["avg"] > 4000, m["mix"]
    assert m["mix"]["hit"] is False


# ---------- 布局与持久化 ----------

def test_av13_narrow_layout(make_env):
    for w in (320, 360):
        env = make_env(CFG_AVG, width=w)
        page = env.page
        open_plan(page)
        page.wait_for_timeout(200)
        info = page.evaluate("""() => {
            const seg = document.querySelector('#gearSrcBox .seg');
            const bs = [...document.querySelectorAll('#gearSrcBox .seg button')];
            const card = document.getElementById('avgCard');
            return {
                sw: document.documentElement.scrollWidth, iw: innerWidth,
                tops: [...new Set(bs.map(b => Math.round(b.getBoundingClientRect().top)))],
                clip: bs.map(b => b.scrollWidth - b.clientWidth),
                segOver: seg.scrollWidth - seg.clientWidth,
                cardOver: card ? card.scrollWidth - card.clientWidth : 0,
            };
        }""")
        assert info["sw"] <= info["iw"] + 1, (w, info)
        assert len(info["tops"]) == 1, (w, info["tops"])
        assert max(info["clip"]) <= 1, (w, info["clip"])
        assert info["segOver"] <= 1 and info["cardOver"] <= 1, (w, info)
        env.close()


def test_av15_persist_across_reload(make_env):
    env = make_env(CFG_AUTO_STRUCT)
    page = env.page
    open_plan(page)
    page.evaluate("() => { window.__xt.state().cfg.avgTarget = 3950; window.__xt.setGearMode('avg'); }")
    page.wait_for_timeout(1500)   # saveCfg 有 800ms 防抖
    assert env.gh.cfg.get("avgTarget") == 3950
    page.goto(BASE + "/index.html?xt=1")
    page.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    page.wait_for_timeout(900)
    assert mode_of(page) == "avg"
    m = meta(page)
    assert abs(m["target"] - 3950) < 0.01 and abs(m["band"] - 20) < 0.01, m
    open_plan(page)
    assert abs(row(rows(page), "第4档")["price"] - 3911.42) < 0.05
