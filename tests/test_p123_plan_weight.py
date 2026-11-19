"""v7.12.5 · P123 均价口径与档表克重同源（锚定档位铺满时两个数必须相等）。

用户反馈（2026-11-19 20:17，附截图）：
  「这又是什么bug？我假设瞄定第1-6档，全档试算均价也是6档，为什么两个值不一致？」
  —— 卡片上「锚定均价 · 第 1–6 档 3601.4」与「选定档位试算 · 全 6 档均价 3531.7」。

这不是显示问题，是两个函数读了两套克重：
  · avgAnchor ← avgOfFront(pr, planBase(), k)   未下沉的名义计划
  · avgAll    ← planAvgOf(pr,  planOf())        含配额下沉的生效计划
锚定档位铺到最后一档时，「第 1–n 档」与「全 n 档」是同一个集合，数学上必须相等。
P121 只把锚定均价切到了 planBase（为了与反解重心 w 同源），全档试算留在 plan，
于是同一张卡给出两个数。实测现场：pb=[3.93,5.89,11.79,17.68,5.89,3.93] /
pl=[3.93,5.89,0,0,0,39.29] → 3875.96 vs 3776.88，差 99 美元。

P123 定稿：一律按生效计划 plan（含下沉）算 ——
  1. 下沉是本脚本自己算出来的可执行克重（浅档买不到，钱挪给深档），不是外来扰动；
  2. 档表显示的、用户会照买的，就是 plan；均价不跟着它，卡上就是买不到的价；
  3. 反解重心同步改为 frontCenter(plan, k)，并在 gearBase 里迭代取稳定点，
     否则点对齐落地后按实际克重算仍差一截（这是「点对齐总对不上」的最后一段）。
"""
import pytest

from conftest import CFG_EMPTY

H = 4696.0
# 现价 3718.65 已跌破第 3–5 档（档价 3912/3848/3785），下沉把这三档的额度全挪给第 6 档
PRICE = 3718.65
HELD = [{"id": "b1", "name": "老仓", "gram": 329.4848, "priceCny": 900.0,
         "time": "2026-10-01T02:00:00.000Z", "note": "", "gear": None}]
BASE = dict(CFG_EMPTY, **{
    "H_BASE": H, "TOTAL": 378.6, "alpha": 2.0, "spanMode": "target",
    "gearCount": 6, "wPct": [8, 12, 24, 36, 12, 8],
    "avgTarget": 3960.0, "avgBand": 50, "startDrop": 14, "endDrop": 27.5, "avgAnchor": 6,
    "realloc": {"on": True, "ratio": 1, "mode": "near", "maxDrop": 8},
})


def cfg(**kw):
    base = dict(BASE, buys=HELD)
    base.update(kw)
    return base


def meta(p):
    return p.evaluate("() => window.__xt.gearMeta()")


def open_plan(p):
    p.evaluate("() => document.querySelector('.tabbar button[data-tab=\"plan\"]').click()")
    p.wait_for_timeout(300)


def align(p):
    open_plan(p)
    p.wait_for_selector("#avgCard [data-align]", timeout=8000)
    p.evaluate("() => document.querySelector('#avgCard [data-align]').click()")
    p.wait_for_timeout(420)


def by_plan(p):
    """按「生效计划」在 JS 里独立重算一遍锚定均价与全档均价（不依赖 meta）。"""
    return p.evaluate("""() => {
      const X = window.__xt, m = X.gearMeta(), pl = X.planOf();
      const p1 = X.p1Of(m.he), pr = pl.map((_, i) => p1 - i * m.d);
      const k = m.anchorK || pl.length;
      const wsum = (arr) => arr.reduce((a, b) => a + b, 0);
      const front = pl.slice(0, k);
      const aAnc = wsum(front) > 0
        ? front.reduce((s, g, i) => s + g * pr[i], 0) / wsum(front) : null;
      const aAll = wsum(pl) > 0
        ? pl.reduce((s, g, i) => s + g * pr[i], 0) / wsum(pl) : null;
      return { plan: pl.map(x => Math.round(x * 1000) / 1000),
               aAnc, aAll, k, n: pl.length, shift: m.planShift };
    }""")


# ---------------------------------------------------------------- 核心：同集合必须同数

def test_t335_full_anchor_equals_all_gear(make_env):
    """锚定档位 = 全部档时，锚定均价与全档均价必须严格相等。

    这是用户报的那条：同一个集合不该有两个数。
    """
    p = make_env(cfg(), price=PRICE).page
    p.wait_for_timeout(350)
    m = meta(p)
    assert m["anchorK"] == 6 == 6, m["anchorK"]
    assert m["avgAnchor"] is not None
    assert m["avgAnchor"] == pytest.approx(m["avgAll"], abs=1e-9), (
        "锚定 6 档与全 6 档是同一个集合，不许差 %s" % (m["avgAnchor"] - m["avgAll"]))


def test_t336_price_uses_effective_plan(make_env):
    """均价按生效计划（含下沉）加权，不是名义计划。

    现场：名义均价 3875.96、生效计划均价 3776.88；档表会按后者买，卡片就必须报后者。
    """
    # 目标 3600 才会把档距拉到贴住现价、触发下沉（3960 时档价全在现价之上，不动）
    p = make_env(cfg(avgTarget=3600.0), price=PRICE).page
    p.wait_for_timeout(350)
    m, calc = meta(p), by_plan(p)
    assert calc["shift"] > 1, ("这一场必须真的发生下沉", calc["shift"])
    assert m["avgAnchor"] == pytest.approx(calc["aAnc"], abs=0.05), (m["avgAnchor"], calc["aAnc"])
    assert m["avgAll"] == pytest.approx(calc["aAll"], abs=0.05)

    # 名义计划算出来的是另一个数：证明两套克重确实不同，且卡片报的是生效计划
    nominal = p.evaluate("""() => {
      const X = window.__xt, m = X.gearMeta(), pb = X.planBase();
      const p1 = X.p1Of(m.he), pr = pb.map((_, i) => p1 - i * m.d);
      const s = pb.reduce((a, b) => a + b, 0);
      return s > 0 ? pb.reduce((acc, g, i) => acc + g * pr[i], 0) / s : null;
    }""")
    assert abs(nominal - m["avgAnchor"]) > 20, ("两套克重应当明显不同", nominal, m["avgAnchor"])


def test_t337_card_merges_rows_when_anchor_covers_all(make_env):
    """锚定铺满全部档时，卡片只留一行锚定均价，不再并列「选定档位试算」。

    两行同数是「看起来像 bug」的根源；合并后标签直接写「全 6 档」。
    """
    p = make_env(cfg(), price=PRICE).page
    open_plan(p)
    t = p.evaluate("() => document.getElementById('avgCard').textContent")
    assert "锚定均价 · 全 6 档" in t, t[:400]
    assert "选定档位试算" not in t, "同一个集合不该再并列一行"
    assert "第 1–6 档" not in t, "铺满时应写「全 6 档」，不写区间"


def test_t338_card_keeps_two_rows_when_anchor_is_partial(make_env):
    """锚定只到第 4 档时，两行照常保留 —— 这时是两个不同的集合，两个数本来就该不同。"""
    p = make_env(cfg(avgAnchor=4), price=PRICE).page
    open_plan(p)
    t = p.evaluate("() => document.getElementById('avgCard').textContent")
    assert "锚定均价 · 第 1–4 档" in t, t[:400]
    assert "选定档位试算 · 全 6 档均价" in t, t[:400]
    m = meta(p)
    assert m["avgAnchor"] != pytest.approx(m["avgAll"], abs=0.5), "前 4 档与全 6 档本就不同"


def test_t339_shift_notice_is_visible(make_env):
    """下沉生效时卡片要说清「均价按下沉后的克重算」，否则用户手算会再次对不上。"""
    p = make_env(cfg(avgTarget=3600.0), price=PRICE).page
    open_plan(p)
    t = p.evaluate("() => document.getElementById('avgCard').textContent")
    assert "配额下沉把" in t and "均价按下沉后的克重计算" in t, t[-500:]


# ---------------------------------------------------------------- 对齐仍然精确

def test_t340_align_hits_exactly_with_realloc_on(make_env):
    """下沉开着点对齐：锚定均价精确落到目标（残差 < 1 美元），不是「差一点」。"""
    p = make_env(cfg(), price=PRICE).page
    align(p)
    m = meta(p)
    assert m["clamped"] is False, m
    assert m["avgAnchor"] == pytest.approx(m["target"], abs=1.0), (m["avgAnchor"], m["target"])
    assert m["hit"] is True, m
    # 两个口径同时命中，且仍然同数
    assert m["avgAll"] == pytest.approx(m["avgAnchor"], abs=1e-9)


def test_t341_align_is_idempotent_with_realloc_on(make_env):
    """再点一次不能变号：迭代如果没收敛，第二次点击会继续移动档距。"""
    p = make_env(cfg(), price=PRICE).page
    align(p)
    d1 = meta(p)["d"]
    align(p)
    d2 = meta(p)["d"]
    assert d2 == pytest.approx(d1, abs=1e-6), (d1, d2)


def test_t342_reach_matches_align_under_realloc(make_env):
    """「当前可达」与「点对齐能到的位置」同源：都按生效计划的前 k 档重心。"""
    # 终止跌幅 15% 会先夹住档距（α 还没封顶），这时才有「当前可达」读数
    c = cfg(avgTarget=3600.0, endDrop=15.0)
    p = make_env(c, price=PRICE).page
    open_plan(p)
    m = meta(p)
    if not m["clamped"]:
        pytest.skip("这一场没被终止跌幅夹住，可达读数不参与")
    calc = p.evaluate("""() => {
      const X = window.__xt, m = X.gearMeta();
      return X.p1Of(m.he) - m.cFront * m.capD;
    }""")
    assert m["reach"] == pytest.approx(calc, abs=0.05), (m["reach"], calc)


# ---------------------------------------------------------------- 关闭下沉 / 边界

def test_t343_off_keeps_invariant(make_env):
    """关掉下沉：两数仍相等，且均价回到名义权重（这时 plan ≡ planBase）。"""
    p = make_env(cfg(realloc={"on": False}), price=PRICE).page
    p.wait_for_timeout(300)
    m = meta(p)
    assert m["avgAnchor"] == pytest.approx(m["avgAll"], abs=1e-9)
    same = p.evaluate("""() => {
      const X = window.__xt, pb = X.planBase(), pl = X.planOf();
      return pb.every((v, i) => Math.abs(v - pl[i]) < 1e-9);
    }""")
    assert same, "关掉下沉后生效计划必须等于名义计划"


def test_t344_half_ratio_keeps_invariant(make_env):
    """下沉比例 50%：克重变了，两个均价仍必须相等（不变式与参数无关）。"""
    p = make_env(cfg(realloc={"on": True, "ratio": 0.5, "mode": "near", "maxDrop": 8}),
                 price=PRICE).page
    p.wait_for_timeout(300)
    m = meta(p)
    assert m["avgAnchor"] == pytest.approx(m["avgAll"], abs=1e-9)


def test_t345_no_pageerror_on_align_loop(make_env):
    """重心迭代不许把页面跑崩（gearBase ↔ reallocPlan 曾经是环）。"""
    errs = []
    p = make_env(cfg(), price=PRICE).page
    p.on("pageerror", lambda e: errs.append(str(e)))
    align(p)
    align(p)
    p.evaluate("() => { window.__xt.compute(); window.__xt.refreshAll(); }")
    p.wait_for_timeout(200)
    assert errs == [], errs
