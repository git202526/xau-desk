"""P33 · 多周期共振引擎（v7.0）。

契约（对应 PRD §3 / §7、开发计划书 §三）：

  · 五条条件分属 4 维，两两 φ 最大 ≤ 0.70，有效独立因子 ≥ 2.8
  · 三态分布不再退化成两态：震荡占比 20–45%
  · 第 3 条必须是「高点抬高 **且** 低点抬高」成对判定
  · 第 2 条按 ATR 归一：波动放大 10 倍后同样的价差不再命中
  · 第 4 条看 MACD 柱、第 5 条 ADX≥25
  · 方向：日线定；周线与 4h 同时反向才作废（二对一）
  · 置信：dir=rg 时恒 0，单周期 ≤40，逆周线 ≤60
  · 1h 不投票

合成序列的生成参数（drift 0.06%/根、日波动 1.0%、90 根换一次状态）是照 PAXG
日线的量级定的：真实数据 1000 根实测 φ 最大 0.67、有效因子 2.94、震荡 34.9%。
参数过强（0.12%/根）会把趋势两维压成同一件事，φ 升到 0.80 —— 那是行情单边，
不是引擎退化，但也说明测试要用贴近标的的波动，而不是随便一条直线。
"""
import math

import pytest

from conftest import CFG_EMPTY

PX = 4072.4
STEP = 24 * 3600 * 1000
T0 = 1761696000000


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=4700.0, TOTAL=200)
    c.update(kw)
    return c


@pytest.fixture
def page(make_env):
    return make_env(cfg=cfg(), price=PX).page


# 带状态切换的随机游走：0=上行段 1=下行段 2=横盘段，按 seg 根轮换。
# 用 Lehmer LCG（48271）保证跨机器逐位一致。
GEN = """
(o) => {
  var n = o.n, s = o.seed || 12345;
  function rnd(){ s = (s * 48271) % 2147483647; return s / 2147483647; }
  var c = 4000, bars = [], t = 1761696000000 - n * 86400000;
  for(var i = 0; i < n; i++){
    var reg = Math.floor(i / o.seg) % 3;
    var drift = reg === 0 ? o.d : reg === 1 ? -o.d : 0;
    var prev = c;
    c = c * (1 + drift + (rnd() - 0.5) * o.v);
    var pad = c * (0.002 + rnd() * 0.003);
    bars.push({ t: t + i * 86400000, o: prev, h: Math.max(prev, c) + pad,
                l: Math.min(prev, c) - pad, c: c, v: 100 });
  }
  return bars;
}
"""

JUDGE_ALL = """
(arr) => {
  var out = [];
  for(var i = 60; i < arr.length; i++){
    var r = window.__xt.judgeAt('1d', i, arr);
    out.push({ st: r.state, adx: r.adx.adx, up: r.up, dn: r.dn, upD: r.upD });
  }
  return out;
}
"""

STRUCT = """
(c) => {
  var p = {};
  ['1w','1d','4h','1h'].forEach(function(k){
    if(c[k]) p[k] = { state:c[k], upN:3, dnN:1,
                      adx:{ adx:c.adx || 36, pdi:25, mdi:12 }, bars:900,
                      rsi:55, rsiState:'中性', ma20:4000, c:4010 };
  });
  return window.__xt.structCore(p);
}
"""


def phi(x, y):
    n = len(x)
    a = sum(1 for p, q in zip(x, y) if p and q)
    b = sum(1 for p, q in zip(x, y) if p and not q)
    c = sum(1 for p, q in zip(x, y) if not p and q)
    d = n - a - b - c
    den = math.sqrt((a + b) * (c + d) * (a + c) * (b + d))
    return 0 if den == 0 else (a * d - b * c) / den


def gen(page, **kw):
    o = {"n": 800, "seed": 12345, "d": 0.0006, "v": 0.010, "seg": 90}
    o.update(kw)
    return page.evaluate(GEN, o)


def judge_all(page, arr):
    return page.evaluate(JUDGE_ALL, arr)


def struct_of(page, spec):
    return page.evaluate(STRUCT, spec)


def mk_bars(points, leg=15):
    """按转折点线性插值出 K 线，每条腿 leg 根。

    末根落在最后一条腿的中间段，这样最后一个转折点右侧始终有足够的确认根数
    （摆点要左右各 3 根）。最后一个点不要写成「回到前一个点」，否则尾部方向反了，
    低点会被抬成上行结构 —— e3 第一版就踩在这上面。
    """
    out = []
    t = T0 - (len(points) * leg) * STEP
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        for j in range(leg):
            c = a + (b - a) * j / float(leg)
            out.append({"t": t, "o": c, "h": c + 1.5, "l": c - 1.5, "c": c, "v": 100})
            t += STEP
    return out


def test_e1_dimension_independence(page):
    """五条条件两两 φ ≤ 0.70、有效独立因子 ≥ 2.8（旧版 5+5 是 2.35）。"""
    out = judge_all(page, gen(page))
    cols = list(zip(*[r["up"] for r in out]))
    mx, pair = 0.0, None
    for a in range(5):
        for b in range(a + 1, 5):
            v = abs(phi(cols[a], cols[b]))
            if v > mx:
                mx, pair = v, (a + 1, b + 1)
    assert mx <= 0.70, ("φ 过大", mx, pair)

    m = [[phi(cols[a], cols[b]) for b in range(5)] for a in range(5)]
    import numpy as np
    ev = sorted(np.linalg.eigvalsh(np.array(m)), reverse=True)
    eff = sum(ev) ** 2 / sum(x * x for x in ev)
    assert eff >= 2.8, ("有效因子不足", eff)


def test_e2_three_states(page):
    """三态不再退化成两态：震荡占比落在 20–45%。"""
    out = judge_all(page, gen(page))
    n = len(out)
    rg = sum(1 for r in out if r["st"] == "rg") / n
    up = sum(1 for r in out if r["st"] == "up") / n
    dn = sum(1 for r in out if r["st"] == "dn") / n
    assert 0.20 <= rg <= 0.45, ("震荡占比", rg, up, dn)
    assert up > 0.15 and dn > 0.10, (up, dn)


def test_e3_struct_needs_both(page):
    """结构条件成对：只抬高点不抬低点 → 上行侧 false；两者同抬才 true。"""
    # 高点 4100→4120→4140→4160 抬高，低点 3890→3880→3870→3860 下移：区间扩张
    expand = mk_bars([3900, 4100, 3890, 4120, 3880, 4140, 3870, 4160, 3860, 3875])
    r = page.evaluate("(a) => window.__xt.judgeAt('1d', a.length - 1, a)", expand)
    assert r["up"][2] is False, r["upD"][2]
    assert r["dn"][2] is False, "高点抬高时不该同时给出下行结构"

    # 高点 4100→4120→4150、低点 3910→3930→3960 同步抬高：对照组必须 true
    rise = mk_bars([3900, 4100, 3910, 4120, 3930, 4150, 3960, 3975])
    r2 = page.evaluate("(a) => window.__xt.judgeAt('1d', a.length - 1, a)", rise)
    assert r2["up"][2] is True, r2["upD"][2]


def _slope_bars(pad, n=80, start=4000.0, step=0.1):
    out = []
    c = start
    for i in range(n):
        prev = c
        c = prev + step
        out.append({"t": T0 - (n - i) * STEP, "o": prev, "h": c + pad,
                    "l": c - pad, "c": c, "v": 100})
    return out


def test_e4_slope_normalized(page):
    """斜率按 ATR 归一：同样的价差，波动放大 10 倍后不再命中。"""
    small = page.evaluate("(a) => window.__xt.judgeAt('1d', a.length - 1, a)", _slope_bars(0.5))
    big = page.evaluate("(a) => window.__xt.judgeAt('1d', a.length - 1, a)", _slope_bars(5.0))
    assert small["up"][1] is True, small["upD"][1]
    assert big["up"][1] is False, big["upD"][1]


def test_e5_momentum(page):
    """动量条件看 MACD 柱：末段急跌后第 4 条必须 false 且数值带负号。"""
    pts = [{"t": T0 - (60 - i) * STEP, "o": 4000 + i, "h": 4002 + i, "l": 3998 + i,
            "c": 4000 + i, "v": 100} for i in range(60)]
    p0 = 4059
    for i in range(1, 21):
        c = p0 - i * 2.0
        pts.append({"t": pts[-1]["t"] + STEP, "o": c + 2.0, "h": c + 2.4,
                    "l": c - 2.4, "c": c, "v": 100})
    r = page.evaluate("(a) => window.__xt.judgeAt('1d', a.length - 1, a)", pts)
    assert r["up"][3] is False, r["upD"][3]
    assert r["dn"][3] is True, r["upD"][3]
    assert "-" in r["upD"][3], r["upD"][3]


def test_e6_strength_threshold(page):
    """强度阈值提到 25：ADX 落在 [20,25) 时第 5 条必须 false（旧版为 true）。"""
    out = judge_all(page, gen(page))
    band = [r for r in out if 20 <= r["adx"] < 25]
    assert band, "合成序列里应能找到 ADX 20~25 的样本"
    for r in band:
        assert r["up"][4] is False, r["adx"]
        assert r["dn"][4] is False, r["adx"]


def test_e7_veto(page):
    """日线多、周线空、4h 空 → 二对一否决：dir=rg、shape=veto、conf=0。"""
    s = struct_of(page, {"1w": "dn", "1d": "up", "4h": "dn", "1h": "dn"})
    assert s["dir"] == "rg", s
    assert s["shape"] == "veto", s
    assert s["conf"] == 0, s


def test_e8_four_h_does_not_veto(page):
    """4h 单独反向只是回调：dir 仍为日线方向，shape=diverge。"""
    s = struct_of(page, {"1w": "up", "1d": "up", "4h": "dn", "1h": "dn"})
    assert s["dir"] == "up", s
    assert s["shape"] == "diverge", s


def test_e9_counter_week(page):
    """逆周线：shape=counter 且 confidence 封顶 60。"""
    s = struct_of(page, {"1w": "dn", "1d": "up", "4h": "up", "1h": "up"})
    assert s["shape"] == "counter", s
    assert s["dir"] == "up", s
    assert s["conf"] <= 60, s


def test_e10_range_conf_zero(page):
    """日线震荡 → conf=0、confStr>0；总览写「强度 x」而不是「置信 0」。"""
    s = struct_of(page, {"1w": "up", "1d": "rg", "4h": "up", "adx": 32})
    assert s["conf"] == 0, s
    assert s["confStr"] > 0, s
    assert s["shape"] == "notrend", s

    txt = page.evaluate("""(s) => {
      window.__xt.patchState({ struct: s });
      window.__xt.renderVerdict();
      return { num: document.getElementById('cfNum').textContent.trim(),
               fill: document.getElementById('cfFill').style.width };
    }""", s)
    assert txt["num"] == "强度 %d" % s["confStr"], txt
    assert txt["fill"] == "%d%%" % s["confStr"], txt


def test_e11_single_period_cap(page):
    """只有日线有效 → 置信 ≤ 40（旧版能到 50）。"""
    s = struct_of(page, {"1d": "up", "adx": 36})
    assert s["valid"] == 1, s
    assert s["conf"] <= 40, s


def test_e12_one_hour_not_voting(page):
    """1h 反向不影响 dir / same / valid，只改 trig；日线震荡时 trig=na。"""
    a = struct_of(page, {"1w": "up", "1d": "up", "4h": "up", "1h": "up"})
    b = struct_of(page, {"1w": "up", "1d": "up", "4h": "up", "1h": "dn"})
    assert (a["dir"], a["same"], a["valid"], a["conf"]) == \
           (b["dir"], b["same"], b["valid"], b["conf"]), (a, b)
    assert a["trig"] == "on" and b["trig"] == "off", (a["trig"], b["trig"])

    c = struct_of(page, {"1w": "up", "1d": "rg", "4h": "up", "1h": "up"})
    assert c["trig"] == "na", c


# —— 震荡态的展示口径（v7.0 遗留项）：条件未满 3 条不等于 0 条 ——

def conds(hit):
    """造 5 条条件，前 hit 条为真。"""
    return [{"k": "条件%d" % (i + 1), "v": i < hit, "d": "说明%d" % (i + 1)}
            for i in range(5)]


def struct_p(up=2, dn=1, state="rg"):
    """四个周期都给全字段，只为跑渲染：日线取指定 state 与命中条数。"""
    def one(st, u, d):
        return {"state": st, "upN": u, "dnN": d, "adx": {"adx": 22, "pdi": 20, "mdi": 18},
                "bars": 900, "upC": conds(u), "dnC": conds(d),
                "c": 4010, "ma20": 4000, "rsi": 55, "rsiState": "中性"}
    return {"1w": one("up", 3, 1), "1d": one(state, up, dn),
            "4h": one("up", 3, 1), "1h": one("up", 3, 1)}


def test_e13_range_card_shows_majority_side(page):
    """日线震荡时卡片写多数侧的 n/5，不写 0/5（旧版 fmt 固定给 0）。"""
    for up, dn, want in ((2, 1, "2/5"), (1, 2, "2/5"), (2, 2, "2/5")):
        txt = page.evaluate("""(p) => {
          window.__xt.setStruct(p);
          const c = Array.from(document.querySelectorAll('.pcard'))
            .find(e => e.querySelector('.k').textContent === '日线');
          return c.querySelector('.m').textContent;
        }""", struct_p(up, dn))
        assert txt.startswith(want), (up, dn, txt)
        assert not txt.startswith("0/5"), (up, dn, txt)


def test_e14_range_reasons_use_majority_side(page):
    """日线震荡时依据区按多数侧列，并给出「均未达 3 条」的说明。"""
    out = page.evaluate("""(p) => {
      window.__xt.setStruct(p);
      const rows = Array.from(document.querySelectorAll('#reasons .rz'));
      return { first: rows[0].textContent.replace(/\\s+/g, ' ').trim(),
               ok: rows.slice(1, 6).filter(e => e.querySelector('.ok').classList.contains('y')).length,
               detail: rows.length };
    }""", struct_p(up=2, dn=1))
    assert "震荡" in out["first"], out
    assert "上行 2 条" in out["first"] and "下行 1 条" in out["first"], out
    assert "上行侧" in out["first"], out
    # 展示上行侧：五条里应有 2 条命中
    assert out["ok"] == 2, out
    # 1 行震荡说明 + 5 条条件 + 1 行反向汇总
    assert out["detail"] == 7, out

    # 下行占多数时翻过来，命中的是下行侧
    out2 = page.evaluate("""(p) => {
      window.__xt.setStruct(p);
      const rows = Array.from(document.querySelectorAll('#reasons .rz'));
      return { first: rows[0].textContent.replace(/\\s+/g, ' ').trim(),
               ok: rows.slice(1, 6).filter(e => e.querySelector('.ok').classList.contains('y')).length };
    }""", struct_p(up=1, dn=2))
    assert "下行侧" in out2["first"], out2
    assert out2["ok"] == 2, out2

    # 反向对照：有方向时不写震荡说明，首行就是第一条条件
    out3 = page.evaluate("""(p) => {
      window.__xt.setStruct(p);
      const rows = Array.from(document.querySelectorAll('#reasons .rz'));
      return { first: rows[0].textContent.replace(/\\s+/g, ' ').trim(), n: rows.length };
    }""", struct_p(up=3, dn=1, state="up"))
    assert "震荡" not in out3["first"], out3
    assert out3["n"] == 6, out3
