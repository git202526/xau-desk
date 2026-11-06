"""P18 · 执行复盘：逐档「计划价 vs 成交均价」，四态执行，每克拉低成本。

这一期只做读：不写存量数据、不动任何现有渲染函数。批一先落数据层
（`avgOz` 字段 + `touchScanIn/touchScan` + `rvRows/rvSum`），批二才接 DOM。

三条容易踩的口径，测试逐条锁住：

1. **成交均价与标记面板同数**（b3）—— 两处都走 `gearCost()`，一处给 price 一处不给，
   结果必须一样，否则同一档在阶梯上和面板上会显示两个均价
2. **估算档不进加权**（b4 / b5）—— 估算档的均价恒等于档位价，混进加权会把真实偏差稀释成 0
3. **触及判定必须用 scaled("1d")**（b9）—— 档位价来自 `computeHEff()`，它读的也是 scaled；
   用原始序列会差一个 `S.scale` 倍。这条与 P19-1 的「必须用原始序列」相反，两者并存不矛盾
"""
import math

import pytest

from conftest import CFG_EMPTY

H = 4700.0
TOTAL = 200.0
PX = 4030.0
OZ = 31.1035
# 6 档 · 200g 的计划克重（权重 8/12/24/36/12/8）
PLAN = [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]


def usd(gram, oz):
    """克重 × 美元/盎司 → 美元金额（标记的 cost / proceeds 都是这个量纲）"""
    return round(gram * oz / OZ, 2)


def mkmarks(items, fx=0.0):
    """items: {档号: (g, sold, 成本$/oz, 卖出$/oz)}，缺省项按 0 处理。

    P27：传 fx 时同时写 costCny / proceedsCny（按该汇率记账）。
    不传则只写美元字段 —— 「人民币未填」也是要测的一种状态。
    """
    out = {}
    for k, v in items.items():
        g, sold, cost_oz, sell_oz = (list(v) + [0, 0, 0, 0])[:4]
        cost = usd(g, cost_oz) if cost_oz else 0
        proc = usd(sold, sell_oz) if sell_oz else 0
        out[str(k)] = {
            "g": g, "sold": sold, "at": 1,
            "cost": cost, "proceeds": proc,
            "costCny": round(cost * fx, 2) if fx else 0,
            "proceedsCny": round(proc * fx, 2) if fx else 0,
        }
    return out


def bars_range(low, n=240, close=PX):
    """收盘从 close-15 缓升到 close，所有最低价压在 low —— 用来控「区间最低」。

    收盘末值必须等于传入的 price，否则 S.scale ≠ 1，触及判定会被缩放一次。
    """
    step = 24 * 3600 * 1000
    now = 1761696000000
    out = []
    for i in range(n):
        c = close - 15 + i * 15 / (n - 1)
        t = int(now - (n - 1 - i) * step)
        out.append([t, "%.2f" % (c - 1), "%.2f" % (c + 8), "%.2f" % low,
                    "%.2f" % c, "12.5", t + step - 1, "0", 0, "0", "0", "0"])
    return out


def cfg(**kw):
    # TOTAL=400：让 200g 未标记「底仓」不再是满仓（planBudget = 总仓 − 未标记持仓 = 200），
    # 档位计划与状态照常渲染；b11 仍用 book=200 对账。满仓→计划全 0 的空态由 b13 的 p2 单独覆盖。
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=400, gearMarks={}, swingGears=[1, 2],
             buys=[{"id": "b0", "name": "底仓", "gram": 200, "priceCny": 900,
                    "time": "2026-08-01T02:00:00.000Z", "note": "", "gear": None}])
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    def _f(mk=None, bars=None, price=PX, **kw):
        c = cfg(**kw)
        if mk is not None:
            c["gearMarks"] = mk
        return make_env(cfg=c, price=price, bars=bars if bars is not None else bars_range(4000))
    return _f


def rv(p):
    return p.evaluate("() => window.__xt.rvRows()")


def rows_of(p):
    return rv(p)["rows"]


def by_idx(p, i):
    return [r for r in rows_of(p) if r["idx"] == i][0]


def rsum(p):
    return p.evaluate("() => window.__xt.rvSum(window.__xt.rvRows().rows)")


# ---------- 批一：数据层 ----------

def test_b3_avg_matches_mark_panel(env):
    """成交均价与标记面板的「买入均价」同数（同一档两处取数一致）。"""
    p = env(mk=mkmarks({1: (16, 0, 4050, 0)})).page
    r = by_idx(p, 1)
    assert r["avgOz"] == pytest.approx(4050, abs=0.01)
    assert r["est"] is False
    assert r["dev"] == pytest.approx((r["avgOz"] / r["price"] - 1) * 100, abs=1e-6)

    p.evaluate("() => window.__xt.openMarkSheet('第1档','sell')")
    p.wait_for_timeout(200)
    txt = p.eval_on_selector("#markNote", "e => e.innerText")
    assert "$4050/oz" in txt, txt


def test_b4_all_estimated_no_dev(env):
    """全 costEst → 偏差全无（None），不会算出 0.00% 这种假精度。"""
    mk = {}
    for i, g in enumerate(PLAN, 1):
        mk[str(i)] = {"g": g, "sold": 0, "cost": 0, "proceeds": 0, "at": 1}
    p = env(mk=mk).page
    rows = rows_of(p)
    assert len(rows) == 6
    for r in rows:
        assert r["est"] is True, r
        assert r["avgOz"] is not None            # 估出来的均价仍然给（按档位价）
        assert r["dev"] is None, r
    s = rsum(p)
    assert s["devAll"] is None
    assert s["graded"] == 0


def test_b5_estimated_not_weighted(env):
    """1 档真实 +5%、其余 5 档估算 → 合计仍 +5%，不被稀释成 +0.83%。"""
    p0 = env().page                       # 先读一次档位价，避免硬编码
    p1_price = by_idx(p0, 1)["price"]
    mk = mkmarks({1: (16, 0, p1_price * 1.05, 0)})
    for i, g in enumerate(PLAN[1:], 2):
        mk[str(i)] = {"g": g, "sold": 0, "cost": 0, "proceeds": 0, "at": 1}
    p = env(mk=mk).page
    s = rsum(p)
    assert s["graded"] == 1
    assert s["devAll"] == pytest.approx(5.0, abs=0.01)
    assert s["pAvg"] == pytest.approx(p1_price, abs=0.01)
    assert s["rAvg"] == pytest.approx(p1_price * 1.05, abs=0.01)


def test_b6_bought_and_sold_out_is_turned(env):
    """买过又全卖 → 「已周转」，不是「待补 · 到过价」。"""
    p = env(mk=mkmarks({1: (8, 8, 4000, 4600)})).page
    r = by_idx(p, 1)
    assert r["gross"] == 8
    assert r["net"] == 0
    assert r["pending"] == pytest.approx(r["plan"])
    assert r["state"] == "turned"
    assert r["touch"] is None                 # 已周转的档不跑扫描


def test_b7_price_below_range_is_far(env):
    """档位价低于区间最低 → 「待补 · 未到价」，且一根都没触及。"""
    p = env(bars=bars_range(4000)).page
    rows = rows_of(p)
    low = 4000
    below = [r for r in rows if r["price"] < low]
    above = [r for r in rows if r["price"] >= low]
    assert below and above, [(r["idx"], r["price"]) for r in rows]
    for r in below:
        assert r["state"] == "far", r
        assert r["touch"]["hit"] == 0
        assert r["touch"]["low"] >= low - 0.5
    for r in above:
        assert r["state"] == "missed", r


def test_b8_price_touched_in_range_is_missed(env):
    """档位价高于区间最低 → 「待补 · 到过价」，触及根数 = 全部日线。"""
    p = env(bars=bars_range(3000)).page
    rows = rows_of(p)
    for r in rows:
        assert r["price"] > 3000, r
        assert r["state"] == "missed", r
        assert r["touch"]["hit"] == r["touch"]["n"] == 240
        assert r["touch"]["lastT"] == r["touch"]["to"]


def test_b9_touch_uses_scaled_series(env):
    """触及判定必须走 scaled：原始序列差一个 S.scale，结论会反过来。

    构造：日线收盘 3000、现价 4200 → S.scale = 1.4，缩放后最低价 4200。
    同一个 4000 的价位：按原始序列「到过」，按缩放序列「没到过」。
    """
    raw = bars_range(3000, close=3000)
    p = env(bars=raw, price=4200).page
    sl = p.evaluate("() => window.__xt.scaled('1d')[0].l")
    assert sl == pytest.approx(4200, abs=1.0)          # 3000 × S.scale(=1.4)

    got = p.evaluate("() => window.__xt.touchScan(4000)")
    assert got["low"] == pytest.approx(4200, abs=1.0)   # 4200 = 3000 × 1.4
    assert got["hit"] == 0                              # 缩放后没到过

    # touchScanIn 读的是 S.kl 里的对象（{t,l,…}），接口给的原始数组要自己转一次
    raw_hit = p.evaluate(
        "(b) => window.__xt.touchScanIn(b.map(r => ({t:r[0], l:Number(r[3])})), 4000).hit",
        raw)
    assert raw_hit == 240                                # 原始序列每一根都「到过」
    # touchScan(px) 与 touchScanIn(scaled('1d'), px) 是同一个数
    same = p.evaluate("""() => {
      var a = window.__xt.touchScan(4000);
      var b = window.__xt.touchScanIn(window.__xt.scaled('1d'), 4000);
      return a.hit === b.hit && a.low === b.low;
    }""")
    assert same is True


def test_b10_per_gram_lowering(env):
    """每克拉低 = Σrealized(¥) / 净持，单位 ¥/g。

    P27 契约变更：原来是「再折回 $/oz」，人民币口径下这一圈没必要 ——
    直接 ¥/g 更好读。数值上 = 600 × 7.10 / 31.1035。
    """
    p = env(mk=mkmarks({1: (16, 8, 4000, 4600), 2: (16, 8, 4000, 4600)}, fx=7.10)).page
    s = rsum(p)
    assert s["net"] == pytest.approx(16, abs=0.01)
    # 两档各卖 8g，每克赚 600 美元/盎司 → 摊到 16g 净持上还是每克 600 美元
    assert s["realized"] == pytest.approx(usd(16, 600) * 7.10, abs=0.5)
    assert s["perGram"] == pytest.approx(600 * 7.10 / OZ, abs=0.5)
    assert s["cnyKnown"] is True
    assert s["turns"] > 0


def test_b11_no_net_no_per_gram(env):
    """净持为 0 → 每克拉低为空，不出现 Infinity。"""
    p = env(mk=mkmarks({1: (8, 8, 4000, 4600), 2: (8, 8, 4000, 4600)}, fx=7.10)).page
    s = rsum(p)
    assert s["net"] == pytest.approx(0, abs=0.01)
    assert s["perGram"] is None
    assert s["realized"] > 0


def test_b12_recon_gap_equals_manual_marks(env):
    """对账差额 = 档位累计 − 账本累计（手动标记不写账本）。"""
    p0 = env(mk=mkmarks({i: (g, 0, 4000, 0) for i, g in enumerate(PLAN, 1)})).page
    s0 = rsum(p0)
    assert s0["gross"] == pytest.approx(200, abs=0.01)
    assert s0["book"] == pytest.approx(200, abs=0.01)
    assert s0["gross"] - s0["book"] == pytest.approx(0, abs=0.05)

    # 手动多标一整档（+16g），账本仍是 200g
    mk = mkmarks({i: (g, 0, 4000, 0) for i, g in enumerate(PLAN, 1)})
    mk["1"]["g"] = 32
    p1 = env(mk=mk).page
    s1 = rsum(p1)
    assert s1["book"] == pytest.approx(200, abs=0.01)
    assert s1["gross"] - s1["book"] == pytest.approx(16, abs=0.05)


# ---------- 批二：渲染与折叠 ----------

def rvtext(p):
    return p.eval_on_selector("#posReview", "e => e.innerText")


def rvcount(p, sel):
    return p.eval_on_selector_all("#posReview " + sel, "els => els.length")


def open_review(p):
    """点标题行展开。标题行只能靠 data-rv 找 —— sanitizeHtml 会剥掉 id。"""
    p.eval_on_selector('#posReview [data-rv="h"]', "e => e.click()")
    p.wait_for_timeout(220)


def test_b1_collapsed_by_default(env):
    """默认收起：卡片只有标题行，一条明细都不出。"""
    p = env(mk=mkmarks({1: (16, 0, 4000, 0)})).page
    assert rvcount(p, ".rv-list") == 0
    assert rvcount(p, ".rv-g") == 0
    assert rvcount(p, '[data-rv="h"]') == 1
    txt = rvtext(p)
    assert "执行复盘" in txt
    assert "待补" in txt, txt

    open_review(p)
    assert rvcount(p, ".rv-g") == 6
    assert "累计买入" in rvtext(p)


def test_b2_no_scan_while_collapsed(env):
    """收起时一次都不扫；展开才扫。计数内置在 touchScanIn 里，不靠 monkey-patch。"""
    p = env(mk=mkmarks({1: (16, 0, 4000, 0)})).page
    assert p.evaluate("() => window.__xt.rvScans()") == 0
    p.evaluate("() => window.__xt.refreshAll()")
    p.wait_for_timeout(120)
    assert p.evaluate("() => window.__xt.rvScans()") == 0

    open_review(p)
    assert p.evaluate("() => window.__xt.rvScans()") > 0


def test_b4b_estimated_shows_no_zero_percent(env):
    """全估算时页面上一个百分比都不该出现（偏差位是「—」）。"""
    mk = {}
    for i, g in enumerate(PLAN, 1):
        mk[str(i)] = {"g": g, "sold": 0, "cost": 0, "proceeds": 0, "at": 1}
    p = env(mk=mk).page
    open_review(p)
    txt = rvtext(p)
    assert "0.00%" not in txt, txt
    assert "全部为估算，无法计算偏差" in txt, txt
    assert "—" in txt


def test_b13_detail_without_marks(env):
    """没标记也照常出六档明细（六档各差多少本身就是信息），表尾补一句引导。"""
    p = env().page
    open_review(p)
    assert rvcount(p, ".rv-g") == 6
    txt = rvtext(p)
    assert "还没有买入标记" in txt, txt
    assert "未成交" in txt

    # 计划克重全 0 才走空态。TOTAL=0 不行 —— totalGrams() 对 ≤0 回落 100，
    # 权重全 0 也会被 normW 换成模板；真正能走到这条路的是「没有高点」（无 K 线也无手填高点）。
    p2 = env(H_BASE=None, bars=[]).page
    open_review(p2)
    assert rvcount(p2, ".rv-g") == 0
    assert "目标总克重" in rvtext(p2)


def test_b14_no_nan_in_text(env):
    """展开后文本里不出现 NaN / Infinity / undefined。"""
    mk = mkmarks({1: (8, 8, 0, 0), 2: (16, 16, 4000, 4600), 3: (0.5, 0, 0, 0)})
    p = env(mk=mk).page
    open_review(p)
    txt = rvtext(p)
    for bad in ("NaN", "Infinity", "undefined", "null"):
        assert bad not in txt, (bad, txt)


def test_b16_warn_rows_excluded(env):
    """预警行不进逐档表；plan = 0 的档状态是「—」，不是「待补 · 未到价」。"""
    p = env(wPct=[0, 20, 20, 20, 20, 20]).page
    open_review(p)
    txt = rvtext(p)
    assert "预警①" not in txt and "预警②" not in txt, txt
    assert rvcount(p, ".rv-g") == 6
    r1 = by_idx(p, 1)
    assert r1["plan"] == 0
    assert r1["state"] == "none"
    assert "—" in txt


# ---------- 批三：样式收口 ----------

def test_b15_no_horizontal_overflow(env):
    """320 / 390 两档展开后整页不横向溢出（10 档是最挤的情况）。"""
    for w in (320, 390):
        p = env(mk=mkmarks({1: (16, 0, 4050, 0), 3: (48, 8, 4000, 4600)}),
                width=w, gearCount=10).page
        open_review(p)
        over = p.evaluate("""() => ({
          de: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          bd: document.body.scrollWidth - document.body.clientWidth
        })""")
        assert over["de"] <= 1, (w, over)
        assert over["bd"] <= 1, (w, over)
        assert rvcount(p, ".rv-g") == 10


def test_b15b_three_honesty_notes(env):
    """PRD §八 三条诚实边界必须真的出现在页面上（不能只写在文档里）。"""
    p = env(mk=mkmarks({1: (16, 0, 4050, 0)})).page
    open_review(p)
    txt = rvtext(p)
    assert "统计区间" in txt and "根日线" in txt, txt
    assert "档位价按当前参数计算" in txt, txt
    assert "不代表历史上没到过" in txt, txt

    # K 线不足（< 30 根）时换成「未做触及判定」，不假装有结论。
    # 空态那条由 b13 覆盖，这里只管「有档位但没足够 K 线」。
    p3 = env(mk=mkmarks({1: (16, 0, 4050, 0)}), bars=bars_range(4000, n=20)).page
    open_review(p3)
    assert "K 线不足，未做触及判定" in rvtext(p3)
