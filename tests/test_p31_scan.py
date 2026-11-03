"""P31 · 档距扫描、区间可设、去预算上限。

三域：
    s1–s8  扫描引擎：5 组档距、首档价不变、溢价率、参数回落、确定性
    r1–r6  区间：90/180/365/全部/自动、非日线强制全量
    u1–u6  界面：5 行 5 列、当前角标、点行切换、百分比图例、窄屏、口径文案
"""
import pytest

from test_p19_2_back import cfg, goto_struct, make_bars

OZ = 31.1035
SCALES = [0.6, 0.8, 1.0, 1.2, 1.4]


@pytest.fixture
def env(make_env):
    def _f(bars=None, width=390, height=844, **kw):
        # width/height 必须给 make_env，不能塞进 cfg —— 否则 320 宽的用例实际跑在 390 上
        return make_env(cfg=cfg(**kw), bars=bars if bars is not None else make_bars(),
                        width=width, height=height)
    return _f


def scan(p, opts=None):
    if opts is None:
        return p.evaluate("() => window.__xt.btScan('1d')")
    return p.evaluate("(o) => window.__xt.btScan('1d', o)", opts)


def back(p, opts=None):
    if opts is None:
        return p.evaluate("() => window.__xt.btBack('1d')")
    return p.evaluate("(o) => window.__xt.btBack('1d', o)", opts)


# ---------- 扫描引擎 ----------

def test_s1_five_scales(env):
    """5 行，系数依次 0.6 / 0.8 / 1.0 / 1.2 / 1.4。"""
    p = env().page
    rows = scan(p)["rows"]
    assert [r["scale"] for r in rows] == SCALES


def test_s2_first_gear_price_fixed(env):
    """五组首档价完全相同 —— 第 1 档锚在高点上，与档距无关。"""
    p = env().page
    g0 = [r["r"]["meta"]["gear0"] for r in scan(p)["rows"]]
    assert max(g0) == pytest.approx(min(g0), rel=1e-9), g0
    assert g0[0] > 0


def test_s3_pitch_strictly_increasing(env):
    """档距严格递增，相邻差 = 原始档距 × 0.2（步长固定）。"""
    p = env().page
    rows = scan(p)["rows"]
    pit = [r["r"]["meta"]["pitch"] for r in rows]
    assert all(pit[i] < pit[i + 1] for i in range(4)), pit
    base = rows[2]["r"]["meta"]["pitchBase"]
    for i in range(4):
        assert (pit[i + 1] - pit[i]) == pytest.approx(base * 0.2, rel=0.01), (i, pit)


def test_s4_full_cost_cross_page(env):
    """档距 ×1.0 那组的满仓成本 == 建仓阶梯页的满仓成本（跨页对账）。

    realloc 默认关闭，两边都用纯权重计划，所以必须同数。
    """
    p = env().page
    r = back(p)
    p.evaluate("() => window.__xt.gotoTab('plan')")
    p.wait_for_timeout(320)
    page_cost = p.evaluate("() => window.__xt.state().gearMeta.fullCost")
    assert r["meta"]["fullCost"] == pytest.approx(page_cost, rel=0.005), (
        r["meta"]["fullCost"], page_cost)


def test_s5_all_triggered_invest_equals_full_cost(env):
    """十档全触发时，实际花费 ≈ 满仓成本（不存在预算截断）。"""
    p = env(startDrop=5, endDrop=8).page
    r = back(p)
    s, m = r["stats"], r["meta"]
    assert s["trigGears"] == s["validGears"], s
    assert s["invest"] == pytest.approx(m["fullCost"], rel=0.005)


def test_s6_premium_formula(env):
    """溢价率 = 花费 ÷ (成交克重 × 首日价 ÷ 31.1035) − 1。"""
    p = env().page
    r = back(p)
    s, m = r["stats"], r["meta"]
    assert s["holdGram"] > 0
    base = s["holdGram"] * m["firstClose"] / OZ
    want = (s["invest"] / base - 1) * 100
    got = p.evaluate("() => window.__xt.btPrem(window.__xt.btBack('1d'))")
    assert got == pytest.approx(want, rel=0.005)


def test_s7_illegal_scale_falls_back(env):
    """非法档距系数（0 / 负数 / NaN / 超界）一律回落，不把档位算飞。"""
    p = env().page
    for bad, want in ((0, 1), (-3, 1), (99, 2), (0.1, 0.5)):
        got = back(p, {"pitchScale": bad})["meta"]["pitchScale"]
        assert got == pytest.approx(want), (bad, got)


def test_s8_deterministic(env):
    """同参数跑两次，5 组结果逐值相同。"""
    p = env().page
    a, b = scan(p), scan(p)
    for x, y in zip(a["rows"], b["rows"]):
        assert x["r"]["stats"] == y["r"]["stats"]
        assert x["r"]["meta"] == y["r"]["meta"]


# ---------- 区间 ----------

def test_r1_365d(env):
    """365 天：交易根数 365，起点 = 总根数 − 365。"""
    p = env(bars=make_bars(n=500)).page
    r = back(p, {"range": "365d"})
    assert r["meta"]["range"]["roots"] == 365
    assert r["meta"]["range"]["kind"] == "365d"


def test_r2_90d(env):
    """90 天：交易根数 90。"""
    p = env(bars=make_bars(n=500)).page
    r = back(p, {"range": "90d"})
    assert r["meta"]["range"]["roots"] == 90


def test_r3_all_default(env):
    """全部：起点仍是预热后的第 60 根，与改前一致。"""
    p = env().page
    r = back(p)
    assert r["meta"]["range"]["kind"] == "all"
    assert r["meta"]["range"]["roots"] == 200      # 260 − 60


def test_r4_auto_without_buys(env):
    """自动但账本里没有买入记录 → 退化为全部，并明写原因。"""
    p = env().page
    r = back(p, {"range": "auto"})
    assert r["meta"]["range"]["roots"] == 200
    assert "没有买入记录" in (r["meta"]["range"]["note"] or "")


def test_r5_auto_uses_first_buy(env):
    """自动 + 有买入记录 → 起点落在首次建仓那天（±1 根）。"""
    p = env().page
    ts = p.evaluate("() => window.__xt.state().kl['1d'][100].t")
    p.evaluate("""(ts) => {
      var S = window.__xt.state();
      S.cfg.buys = [{ id:"b1", name:"首笔", gram:10, priceCny:900,
                      time:new Date(ts).toISOString(), gear:null }];
    }""", ts)
    r = p.evaluate("""() => window.__xt.btRangeOf(
      window.__xt.state().kl['1d'], '1d', {range:'auto'})""")
    assert abs(r["i0"] - 100) <= 1, r
    assert r["kind"] == "auto"


def test_r6_non_daily_forced_all(env):
    """周线 / 小时线一律固定全量 —— 「90 天」在 220 周或 42 天的尺度上是骗人的。"""
    p = env().page
    w = p.evaluate("""() => window.__xt.btRangeOf(
      window.__xt.state().kl['1w'] || [], '1w', {range:'90d'})""")
    h = p.evaluate("""() => window.__xt.btRangeOf(
      window.__xt.state().kl['1h'] || [], '1h', {range:'90d'})""")
    assert w["kind"] == "all" and h["kind"] == "all"
    assert w["i0"] == 60 and h["i0"] == 60


# ---------- 界面 ----------

def test_u1_scan_table_five_cols(env):
    """扫描表 5 行，表头 5 列。"""
    p = env().page
    goto_struct(p)
    head = p.eval_on_selector_all("#bbPeers .srh span", "els => els.map(e => e.innerText.trim())")
    assert head == ["系数", "首档", "触发", "花费", "溢价"], head
    assert p.eval_on_selector_all("#bbPeers .sr", "els => els.length") == 5


def test_u2_current_badge(env):
    """只有 ×1.0 那行带「当前」角标，且只有一行。"""
    p = env().page
    goto_struct(p)
    txt = p.eval_on_selector_all("#bbPeers .sr .sc", "els => els.map(e => e.innerText.trim())")
    assert sum(1 for t in txt if "当前" in t) == 1, txt
    assert "1.0" in txt[2] and "当前" in txt[2], txt


def test_u3_click_row_switches_detail(env):
    """点 ×1.4 那行 → 下方逐档表的深档价变低（档距被拉开）。"""
    p = env().page
    goto_struct(p)
    before = p.eval_on_selector_all("#bbGears .bbg .gp",
                                    "els => els.map(e => e.innerText.trim())")
    p.click('#bbPeers [data-s="1.4"]')
    p.wait_for_timeout(400)
    after = p.eval_on_selector_all("#bbGears .bbg .gp",
                                   "els => els.map(e => e.innerText.trim())")
    assert before[0] == after[0], "首档价不随档距变"
    assert before[-1] != after[-1], (before, after)


def test_u4_legend_percent(env):
    """图例用百分比，不再出现金额 —— 三组同克重下金额曲线会重合，没有信息量。"""
    p = env().page
    goto_struct(p)
    leg = p.eval_on_selector("#bbLeg", "e => e.innerText")
    assert "%" in leg
    assert "$" not in leg, leg


def test_u5_narrow_320(env):
    """320 宽：扫描表 5 列不溢出，整页不横向滚。"""
    p = env(width=320).page
    goto_struct(p)
    ov = p.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert ov <= 0, "横向溢出 %spx" % ov
    assert p.eval_on_selector_all("#bbPeers .sr", "els => els.length") == 5


def test_u6_no_budget_cap_stated(env):
    """卡上明写「无预算上限」「按档位价成交」。"""
    p = env().page
    goto_struct(p)
    txt = p.eval_on_selector("#bbNote", "e => e.innerText")
    assert "无预算上限" in txt, txt
    assert "按档位价成交" in txt, txt
