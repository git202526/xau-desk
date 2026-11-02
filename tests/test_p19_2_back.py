"""P19-2 · 策略回测（D1 主策略 + D2 逐档触及）。

固定档位、逐根推进：每根只用 `close` 计权益、`low` 判建仓触发、`high` 判循环档卖出。
三者同源一根 K 线，不假设盘中先后顺序 —— 这是「回测」和「实盘」唯一的语义差别，
所以页面上必须写明「不含盘中路径」。

**与 P19-1 同一个原则：用原始 `S.kl[k]`，不 `scaled()``（全比较都是价格 vs 价格）。
与 P18 的 `touchScan` 用 scaled 相反，两处各自正确，不要「统一」。
"""
import math

import pytest

from conftest import CFG_EMPTY

H = 5000.0
TOTAL = 200.0
# range 模式下的档位：p1 = H×0.87 = 4350，p6 = H×0.75 = 3750，步长 120
GEARS = [4350.0, 4230.0, 4110.0, 3990.0, 3870.0, 3750.0]
PLAN = [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]

OZ = 31.1035


def pxg(usd_oz):
    """$/oz → $/g。P28 前回测直接拿「克 × $/oz」，金额放大 31.1 倍。"""
    return usd_oz / OZ


def make_bars(n=260, peak=H, trough=4100.0, rally=None, i0=60, amp=12.0, flat=False):
    """前 i0 根爬升到 peak（决定 H），之后跌到 trough，可选再反弹到 rally。

    low/high 各留 amp 的余量：low 决定建仓触发，high 决定循环档卖出。
    """
    step = 24 * 3600 * 1000
    now = 1761696000000
    closes = []
    for i in range(i0):
        closes.append(4200 + (peak - 4200) * i / (i0 - 1))
    rest = n - i0
    m = rest if rally is None else rest // 2
    if flat:
        # 起点就地横盘：预算按起点收盘价算，档位价却都在起点之上 → 必然有买不起的档
        closes.extend([trough] * rest)
        rest = 0
    for k in range(rest):
        if rally is None or k < m:
            t = k / max(1, m - 1)
            closes.append(peak + (trough - peak) * t)
        else:
            t = (k - m) / max(1, rest - m - 1)
            closes.append(trough + (rally - trough) * t)
    out = []
    for i, c in enumerate(closes):
        t = int(now - (n - 1 - i) * step)
        o = closes[i - 1] if i else c
        out.append([t, "%.2f" % o, "%.2f" % (c + amp), "%.2f" % (c - amp),
                    "%.2f" % c, "12.5", t + step - 1, "0", 0, "0", "0", "0"])
    return out


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, gearMarks={}, swingGears=[2, 3],
             spanMode="range", startDrop=13, endDrop=25)
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    def _f(bars=None, width=390, height=844, **kw):
        # width/height 必须给 make_env，不能塞进 cfg —— 否则 320 宽的用例实际跑在 390 上
        return make_env(cfg=cfg(**kw), bars=bars if bars is not None else make_bars(),
                        width=width, height=height)
    return _f


def back(p, k="1d", opts=None):
    if opts is None:
        return p.evaluate("(k) => window.__xt.btBack(k)", k)
    return p.evaluate("(a) => window.__xt.btBack(a[0], a[1])", [k, opts])


def prices(p):
    return [r["price"] for r in back(p)["gear"]]


# ---------- 批一：计算层 ----------

def test_b1_triggers_three_gears(env):
    """跌到 4100：只触发前 3 档（3990 那档没到）。"""
    p = env().page
    r = back(p)
    assert "err" not in r, r
    assert [round(x) for x in prices(p)] == [round(x) for x in GEARS]
    assert r["stats"]["trigGears"] == 3
    assert [g["done"] for g in r["gear"]] == [True, True, True, False, False, False]
    assert r["stats"]["validGears"] == 6


def test_b2_invest_matches_hand_calc(env):
    """投入 = Σ 计划克重 × 档位价($/g)，误差 < 0.5%。

    P28 契约变更：旧断言是 `PLAN × GEARS`（克 × $/oz），量纲不成立，
    金额被放大 31.1035 倍。正确写法是先把 $/oz 转 $/g 再乘克重。
    """
    p = env().page
    got = back(p)["stats"]["invest"]
    want = sum(PLAN[i] * pxg(GEARS[i]) for i in range(3))
    assert got == pytest.approx(want, rel=0.005), (got, want)


def test_b3_avg_cost_matches_hand_calc(env):
    """均价 = 投入 ÷ 累计买入克重（分母是买入量，不是期末持仓），单位 $/g。

    P28：分子同样是「克 ÷ 31.1035 × $/oz」，否则均价会是 $/oz 的三十分之一。
    """
    p = env().page
    s = back(p)["stats"]
    want = sum(PLAN[i] * pxg(GEARS[i]) for i in range(3)) / sum(PLAN[:3])
    assert s["avgCost"] == pytest.approx(want, rel=0.005)
    assert s["holdGram"] == pytest.approx(sum(PLAN[:3]), abs=0.01)


def test_b4_three_peers_same_budget(env):
    """三组对照的本金完全相等 —— 不相等就不是「同本金比收益」。

    P28 契约变更：本金 = 总克重 ÷ 31.1035 × 起点价（美元）。
    旧断言 `TOTAL * H` 是「克 × $/oz」，把量纲错误固定成了期望值。
    """
    p = env().page
    r = back(p)
    b = r["meta"]["budget"]
    assert b == pytest.approx(TOTAL * pxg(H), rel=0.02)
    assert r["peers"]["dca"]["budget"] == pytest.approx(b)
    assert r["peers"]["lump"]["budget"] == pytest.approx(b)
    assert r["peers"]["dca"]["invest"] == pytest.approx(b)
    assert r["peers"]["lump"]["invest"] == pytest.approx(b)


def test_b5_loop_off_no_extra_trades(env):
    """循环档关闭：买入笔数 = 触发档数。"""
    p = env().page
    r = back(p, "1d", {"loop": False, "realloc": False, "fee": 0})
    assert r["stats"]["trigCount"] == r["stats"]["trigGears"]
    assert r["stats"]["soldGram"] == 0
    assert r["stats"]["realized"] == 0


def test_b6_loop_on_sells_swing_gears(env):
    """循环档打开且反弹摸到上一档：笔数不变（一轮一次）、持仓减少、已实现 > 0。"""
    bars = make_bars(trough=4100.0, rally=4450.0)
    p = env(bars=bars).page
    off = back(p, "1d", {"loop": False})["stats"]
    on = back(p, "1d", {"loop": True})["stats"]

    assert on["trigCount"] == off["trigCount"]          # 卖出不算「再买一次」
    assert on["soldGram"] > 0
    assert on["holdGram"] < off["holdGram"]
    assert on["realized"] > 0
    assert on["cash"] > off["cash"]


def test_b7_nothing_triggered(env):
    """一档都没碰到：不抛错、投入 0、收益率为空而不是 0。"""
    bars = make_bars(trough=4500.0)          # 最低价 4488 > 最高档 4350
    p = env(bars=bars).page
    r = back(p)
    assert "err" not in r
    assert r["stats"]["trigGears"] == 0
    assert r["stats"]["invest"] == 0
    # 一分钱没花：期末总资产 = 预算，收益 0（不是 None —— 分母是预算，永远有值）
    assert r["stats"]["endV"] == pytest.approx(r["meta"]["budget"])
    assert r["stats"]["pnl"] == pytest.approx(0, abs=0.01)
    assert r["stats"]["pnlPct"] == pytest.approx(0, abs=1e-9)
    assert r["stats"]["avgCost"] is None
    assert len(r["equip"]) > 0


def test_b8_too_few_bars(env):
    """不足 70 根 → err=no-data，不给半张结果。"""
    p = env(bars=make_bars(n=69)).page
    r = back(p)
    assert r.get("err") == "no-data"
    assert r["n"] == 69


def test_b9_budget_shortage(env):
    """起点已经在档位带之下（gap down）且档距很窄 → 钱不够买满，有跳过、现金不为负。

    预算按「起点收盘价 × 总克重」算，档位价却高于起点价时才会不够 —— 这也是真实会遇到的情形：
    金价一波急跌后打开回测，前面几档都是「已经跌破但没钱买」。
    """
    bars = make_bars(trough=4150.0, amp=4.0, flat=True)
    p = env(bars=bars, startDrop=5, endDrop=8).page
    r = back(p)
    s = r["stats"]
    assert r["gear"][-1]["price"] > 4150, r["gear"]      # 六档全在起点价之上
    assert s["skipped"] > 0, s
    assert s["cash"] >= -1e-6
    assert s["usage"] <= 1.0 + 1e-6


def test_b10_deterministic(env):
    """同参数跑两次，所有数字逐值相同。"""
    p = env().page
    a = back(p)
    b = back(p)
    for k in ("stats", "meta", "gear", "peers", "flags"):
        assert a[k] == b[k], k
    assert a["equip"] == b["equip"]


def test_b11_weight_zero_gear_not_bought(env):
    """极端档距（10 档 · endDrop 60）下深档价格仍为正；权重 0 的档不买入但仍计入分母。"""
    p = env(gearCount=10, startDrop=5, endDrop=60,
            wPct=[0, 10, 10, 10, 10, 10, 10, 10, 15, 15]).page
    r = back(p)
    assert r["stats"]["validGears"] == 10
    assert all(g["price"] > 0 for g in r["gear"]), r["gear"]
    # 第 1 档价格 4750 高于区间最低价 —— 若有权重它必然触发
    assert r["gear"][0]["price"] > 4100
    assert r["gear"][0]["plan"] == 0
    assert r["gear"][0]["done"] is False
    assert r["stats"]["trigCount"] == sum(1 for g in r["gear"] if g["done"])


def test_b12_realloc_plan_default_unchanged(env):
    """reallocPlan 不传 ctx 时仍读真实标记（已买满的档不会被当成可捐）。"""
    # 六档全部买满 → 实盘口径下没有任何可捐额度
    mk = {str(i + 1): {"g": g, "sold": 0, "cost": g * 4000 / 31.1035,
                       "proceeds": 0, "at": 1}
          for i, g in enumerate(PLAN)}
    p = env(gearMarks=mk).page
    out = p.evaluate("""() => {
      var S = window.__xt.state();
      var base = window.__xt.planOf();            // 先取原始计划（此刻 realloc 还是关的）
      S.cfg.realloc = {on:true, mode:"near", ratio:1, maxDrop:20};
      S.reallocDead = undefined;
      var a = window.__xt.reallocPlan(base);            // 不传 ctx：读真实标记
      S.reallocDead = undefined;
      var b = window.__xt.reallocPlan(base, {           // 传 ctx：回测口径，谁都没买过
        buyOf:function(){ return 0; }, dead:{}, price:S.price
      });
      return {base:base, a:a, b:b};
    }""")
    # 第 6 档已买满 → 实盘口径没有任何可捐额度，整组不动
    assert out["a"] == out["base"]
    # 回测口径（谁都没买过）→ 跌破档的克重被挪到更近的档，总量守恒
    assert out["b"] != out["base"]
    assert sum(out["b"]) == pytest.approx(sum(out["base"]), abs=0.01)
    assert any(out["b"][i] < out["base"][i] for i in range(6)), out


# ---------- 批二：回测卡片 ----------

def goto_struct(p):
    p.click('.tabbar button[data-tab="struct"]')
    p.wait_for_timeout(320)


def bbtxt(p, sel):
    return p.eval_on_selector("#" + sel, "e => e.innerText")


def test_c1_card_position(env):
    """卡片在判定回溯之后、通道之前。"""
    p = env().page
    goto_struct(p)
    ok = p.evaluate("""() => {
      var a = document.getElementById('btCard');
      var b = document.getElementById('btBackCard');
      var c = document.getElementById('chChips');
      if(!a || !b || !c) return 'missing';
      var ab = a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING;
      var bc = b.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING;
      return !!(ab && bc);
    }""")
    assert ok is True, ok


def test_c2_stats_render_clean(env):
    """指标区数字齐全，且没有 undefined / NaN。"""
    p = env().page
    goto_struct(p)
    txt = bbtxt(p, "bbStats")
    assert "触发档位" in txt and "期末市值" in txt and "最大回撤" in txt, txt
    assert "3 / 6" in txt, txt
    for bad in ("NaN", "undefined", "Infinity", "null"):
        assert bad not in txt, (bad, txt)
    assert p.eval_on_selector_all("#bbStats .bs", "els => els.length") == 5


def test_c3_peers_three_rows_same_budget(env):
    """对照表 3 行，本金列三行完全相等。"""
    p = env().page
    goto_struct(p)
    rows = p.eval_on_selector_all("#bbPeers .pr", """els => els.map(e =>
      Array.from(e.children).map(c => c.innerText.trim()))""")
    assert len(rows) == 3, rows
    assert [r[0] for r in rows] == ["本策略", "定投", "一次性"]
    budgets = [r[1] for r in rows]
    assert budgets[0] == budgets[1] == budgets[2], budgets
    assert budgets[0].startswith("$")


def test_c4_curve_downsampled(env):
    """折线点数 ≤ 210（740 根日线要抽点，否则移动端几千个节点）。"""
    p = env().page
    goto_struct(p)
    n = p.evaluate("""() => {
      var ls = document.querySelectorAll('#bbEquip polyline');
      if(!ls.length) return -1;
      var pts = ls[0].getAttribute('points').trim().split(/\\s+/);
      return pts.length;
    }""")
    assert n > 10, n
    assert n <= 210, n
    assert p.eval_on_selector_all("#bbEquip polyline", "e => e.length") == 2


def test_c5_gear_rows_include_zero(env):
    """逐档表行数 = 建仓档数，0 次的档也在。

    P28 契约变更：`.bgc`（一行横滚 chip）改为 `.bbg`（逐档一行表格）。
    原因是 chip 只写得出「档1 0 次」，写不出这一档挂在哪个价、配多少克 ——
    而「能不能和阶梯页对上」正是这次要解决的问题。
    """
    p = env().page
    goto_struct(p)
    rows = p.eval_on_selector_all("#bbGears .bbg", "els => els.map(e => e.innerText.trim())")
    assert len(rows) == 6, rows
    assert any("0 次" in c for c in rows), rows
    assert any("次" in c and "0 次" not in c for c in rows), rows
    # 每行都要有价格与克重，否则「对照」无从谈起
    assert all("$" in c for c in rows), rows
    assert all("g" in c for c in rows), rows


def test_c6_narrow_screen(env):
    """320 宽：整页不横向溢出，且六行档位表都落在视口内。

    历史上这里踩过两次：先是计划书写「chip 横向滚动不换行」，实测 320 宽要滚 340px，
    后三档根本看不见；改成换行后，P28 又从 chip 换成表格。判据始终是「每一条都在视口里」，
    不绑定具体控件形态。
    """
    p = env(width=320).page
    goto_struct(p)
    over = p.evaluate("""() => ({
      de: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      bd: document.body.scrollWidth - document.body.clientWidth
    })""")
    assert over["de"] <= 1, over
    assert over["bd"] <= 1, over
    outside = p.evaluate("""() => {
      var vw = document.documentElement.clientWidth, bad = [];
      document.querySelectorAll('#bbGears .bbg').forEach(function(e){
        var r = e.getBoundingClientRect();
        if(r.right > vw + 1 || r.left < -1) bad.push(e.innerText.trim());
      });
      return bad;
    }""")
    assert outside == [], outside


def test_c7_no_trigger(env):
    """一档没碰到：给解释文案，对照表只留两行对照组。"""
    p = env(bars=make_bars(trough=4500.0)).page
    goto_struct(p)
    txt = p.eval_on_selector("#btBackCard", "e => e.innerText")
    assert "区间内未触及任何档位" in txt, txt
    rows = p.eval_on_selector_all("#bbPeers .pr", "els => els.length")
    assert rows == 2
    # 无成交也要有曲线：现金不动，曲线是平的
    assert p.eval_on_selector_all("#bbEquip polyline", "e => e.length") == 2


# ---------- 批三：交互与边界 ----------

def meta_txt(p):
    return p.eval_on_selector("#bbMeta", "e => e.innerText")


def set_opts(p, loop=False, realloc=False, fee=0):
    p.evaluate("""(o) => {
      var S = window.__xt.state();
      S.cfg.btOpts = o;
      window.__xt.refreshAll();
    }""", {"loop": loop, "realloc": realloc, "fee": fee})
    p.wait_for_timeout(220)


def test_d1_hour_period_unit(env):
    """切到 1 小时：单位变「根」，头部跟着变。"""
    p = env().page
    goto_struct(p)
    p.click('#bbChips button[data-b="1h"]')
    p.wait_for_timeout(320)
    m = meta_txt(p)
    assert "根" in m, m
    assert "1 小时" not in m
    assert p.eval_on_selector('#bbChips button[data-b="1h"]', "e => e.className") == "on"


def test_d2_week_period_unit(env):
    """切到周线：单位变「周」。"""
    p = env().page
    goto_struct(p)
    p.click('#bbChips button[data-b="1w"]')
    p.wait_for_timeout(320)
    assert "周" in meta_txt(p), meta_txt(p)


def test_d3_switch_matches_direct_call(env):
    """开关切出来的结果 = 直接以该配置调 btBack —— 不依赖历史状态。

    用「跌完再反弹」的行情：循环档只有在反弹摸到上一档时才会卖出，
    单边下跌的行情里开不开都一样，测不出状态依赖。
    """
    bars = make_bars(trough=4100.0, rally=4450.0)
    p = env(bars=bars).page
    goto_struct(p)
    set_opts(p, loop=False)
    first = p.evaluate("() => window.__xt.btBack('1d', {loop:false,realloc:false,fee:0}).stats")
    set_opts(p, loop=True)
    on = p.evaluate("() => window.__xt.btBack('1d', {loop:true,realloc:false,fee:0}).stats")
    set_opts(p, loop=False)
    set_opts(p, loop=True)
    again = p.evaluate("() => window.__xt.btBack('1d', {loop:true,realloc:false,fee:0}).stats")
    assert on == again
    assert on != first
    # 页面上的数字也跟着这次配置走
    txt = bbtxt(p, "bbStats")
    assert "NaN" not in txt and "undefined" not in txt


def test_d4_opts_persist(env):
    """开关写进 cfg 并落云端：刷新后仍是开。"""
    p = env().page
    goto_struct(p)
    p.check("#bbLoop")
    # saveCfg 的云推送有 800ms 防抖，等它真的 PUT 出去再刷新
    p.wait_for_timeout(1400)
    assert p.evaluate("() => window.__xt.state().cfg.btOpts.loop") is True

    p.reload(wait_until="domcontentloaded")
    p.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    p.wait_for_timeout(600)
    assert p.evaluate("() => window.__xt.state().cfg.btOpts.loop") is True
    p.click('.tabbar button[data-tab="struct"]')
    p.wait_for_timeout(300)
    assert p.is_checked("#bbLoop") is True


def test_d5_budget_hint(env):
    """预算不足要说出来，不能只把次数藏在一个数字里。"""
    bars = make_bars(trough=4150.0, amp=4.0, flat=True)
    p = env(bars=bars, startDrop=5, endDrop=8).page
    goto_struct(p)
    txt = p.eval_on_selector("#bbNote", "e => e.innerText")
    assert "因预算不足" in txt, txt


def test_d6_fixed_note(env):
    """恒显示说明：数据源与计价单位必须在脸上，不只在文档里。"""
    p = env().page
    goto_struct(p)
    txt = p.eval_on_selector("#bbNote", "e => e.innerText")
    assert "PAXG" in txt and "美元" in txt, txt
    assert "不含盘中路径" in txt, txt
