"""v7.6 P2：结构仲裁 + 六形态标签（T103–T108 / T111）。

约束（计划书 §5.3 / §5.4）：
  - `patternTags` 只出标签，**绝不参与** dominant 判定（T108 反向证明）
  - `arbitrateStructure` 是唯一仲裁出口：state 答形态，dominant 答谁主导
  - dominant 用**日线 ADX**，trend_state 用 **4H ADX**，两者分字段
  - 跳空 > 0.8 ATR 时压过一切，且旧 box.valid 必须置 false

本文件先红后绿：__xt.v76.patternTags / arbitrate 尚未实现，红在接口缺失。
"""
import math

DAY = 86400000
START = 1793000000000
WED_OPEN = 1794362400000       # 2026-11-11 02:00 UTC，周三盘中（开市）


def wrap(mids, half=6.0, step=DAY, start=START):
    """把中轴序列包成 K 线（h/l 固定外扩 half，保证 ATR 稳定可算）。"""
    out = []
    for i, m in enumerate(mids):
        o = m - half * 0.4
        c = m + half * 0.4
        out.append([start + i * step, "%.2f" % o, "%.2f" % (m + half),
                    "%.2f" % (m - half), "%.2f" % c, "10"])
    return out


def band(u_fn, l_fn, n=30, ox=0.6, step=DAY, start=START):
    """按上下沿函数生成收敛/扩散序列，h/l 贴住上下沿。"""
    out = []
    for i in range(n):
        u, l = u_fn(i), l_fn(i)
        mid, half = (u + l) / 2.0, (u - l) / 2.0
        o = mid - half * ox
        c = mid + half * ox
        out.append([start + i * step, "%.2f" % o, "%.2f" % (mid + half),
                    "%.2f" % (mid - half), "%.2f" % c, "10"])
    return out


def flat_box(n=30, half=2.0, step=DAY, start=START):
    """真正可判定的水平箱：上行 10 根 → 顶 4 根 → 下行 10 根 → 底 4 根。

    每根振幅很小（4 美元），ATR≈5，箱宽 60 > 2 ATR；上下沿各触碰 4 次、跨度 24 根。
    """
    mids = []
    for i in range(10):
        mids.append(4000 + 5.6 * i)
    mids += [4056] * 2
    for i in range(10):
        mids.append(4056 - 5.6 * (i + 1))
    mids += [4000] * 2
    return wrap(mids, half=half, step=step, start=start)


def zig(pivots, per_leg=5, half=6.0, step=DAY, start=START):
    """折线序列：pivots 为拐点价位，每段线性插值（峰值只出现一根，便于摆动点识别）。"""
    mids = []
    for k in range(len(pivots) - 1):
        a, b = pivots[k], pivots[k + 1]
        for j in range(per_leg):
            mids.append(a + (b - a) * j / float(per_leg))
    mids.append(pivots[-1])
    return wrap(mids, half=half, step=step, start=start)


def v76(page, script, arg=None):
    has = page.evaluate("() => !!(window.__xt && window.__xt.v76)")
    assert has, "__xt.v76 未实现（v7.6 接口契约）"
    return page.evaluate(script, arg) if arg is not None else page.evaluate(script)


def tags(page, bars):
    return v76(page, """b => (window.__xt.v76.patternTags(b)||[]).map(function(t){
      return t.status ? (t.name + '（' + t.status + '）') : t.name;
    })""", bars)


def arb(page, **kw):
    return v76(page, "o => window.__xt.v76.arbitrate(o)", kw)


# ---------------------------------------------------------------- T103 接口
def test_t103_pattern_tags_contract(make_env):
    """返回数组、最多 2 个；dominant 只在主结构枚举内取值。"""
    page = make_env().page
    tri = band(lambda i: 4088 - 1.2 * i, lambda i: 4010 + 1.0 * i)
    t = tags(page, tri)
    assert isinstance(t, list) and 1 <= len(t) <= 2, t

    r = arb(page, bars_1d=flat_box(), bars_4h=flat_box(), now=WED_OPEN)
    assert r["dominant"] in ("通道为主", "水平箱体", "双结构并存", "事件跳空", "无结构"), r
    assert r["state"] in ("跳空", "通道", "箱体", "双结构", "过渡", "无结构"), r
    assert r["confidence"] in ("High", "Medium", "Low"), r


# ---------------------------------------------------------------- T104 三角形
def test_t104_symmetrical_triangle(make_env):
    """高点下压 + 低点抬升 + 末段振幅 < 首段 60% → 收敛（对称）三角。"""
    bars = band(lambda i: 4088 - 1.2 * i, lambda i: 4010 + 1.0 * i)
    t = tags(make_env().page, bars)
    assert any("三角" in x or "收敛" in x for x in t), t


def test_t104b_ascending_triangle(make_env):
    """高点持平 + 低点抬升 → 上升三角。"""
    bars = band(lambda i: 4050.0, lambda i: 4002 + 1.3 * i)
    t = tags(make_env().page, bars)
    assert any("上升三角" in x for x in t), t


def test_t104c_descending_triangle(make_env):
    """低点持平 + 高点下压 → 下降三角。"""
    bars = band(lambda i: 4050 - 1.3 * i, lambda i: 4004.0)
    t = tags(make_env().page, bars)
    assert any("下降三角" in x for x in t), t


# ---------------------------------------------------------------- T105 喇叭形
def test_t105_broadening(make_env):
    """高点抬升 + 低点下移 + 末段振幅 > 首段 1.4 倍 → 喇叭形。"""
    bars = band(lambda i: 4028 + 2.2 * i, lambda i: 4028 - 2.2 * i)
    t = tags(make_env().page, bars)
    assert any("喇叭" in x for x in t), t


# ---------------------------------------------------------------- T106 旗形
def test_t106_flag(make_env):
    """旗杆 6 根同向位移 > 3 ATR，旗面 10 根振幅 < 1.5 ATR、回撤 < 50%。"""
    pole = [4000 + 9.0 * i for i in range(7)]            # 4000 → 4054
    flag = [4054 - 0.6 * i for i in range(10)]           # 缓慢回落 4054 → 4048.6
    bars = wrap(pole + flag, half=4.0)
    t = tags(make_env().page, bars)
    assert any("旗形" in x for x in t), t


# ---------------------------------------------------------------- T107 头肩
def test_t107_head_shoulders_broken(make_env):
    """左肩 4050 / 头 4075 / 右肩 4050，收盘跌破颈线 4022 → 已破颈线。"""
    bars = zig([4020, 4050, 4022, 4075, 4022, 4050, 4000])
    t = tags(make_env().page, bars)
    assert any("头肩" in x for x in t), t
    assert any("已破颈线" in x for x in t), t


def test_t107b_head_shoulders_pending(make_env):
    """同样的头肩，但收盘仍在颈线上方 → 待确认，不能提前喊破位。"""
    bars = zig([4020, 4050, 4022, 4075, 4022, 4050, 4030])
    t = tags(make_env().page, bars)
    assert any("头肩" in x for x in t), t
    assert any("待确认" in x for x in t), t


# ---------------------------------------------------------------- T108 反向证明
def test_t108_patterns_never_touch_dominant(make_env):
    """关掉形态识别，dominant / box 上下沿 / channel.valid 必须逐字相等。"""
    bars = band(lambda i: 4088 - 1.2 * i, lambda i: 4010 + 1.0 * i)
    doc = {"updated": 1794150000000, "src": "dukascopy", "symbol": "XAUUSD",
           "price_type": "bid", "sanitized": {"synthetic": 0, "dup": 0},
           "stats": {}, "gaps": {}, "kl": {"1d": bars, "4h": flat_box()}}
    env = make_env(xau=doc)
    page = env.page
    page.wait_for_timeout(600)
    a = v76(page, "o => window.__xt.v76.snapshot(o)", {"now": WED_OPEN})
    b = v76(page, "o => window.__xt.v76.snapshot(o)",
            {"now": WED_OPEN, "noPatterns": True})
    sa, sb = a["structure"], b["structure"]
    assert sa["dominant"] == sb["dominant"], (sa, sb)
    assert sa["state"] == sb["state"] and sa["confidence"] == sb["confidence"]
    assert sa["box"]["upper"] == sb["box"]["upper"]
    assert sa["box"]["lower"] == sb["box"]["lower"]
    assert sa["channel"]["valid"] == sb["channel"]["valid"]
    assert sb["extended_patterns"] == []
    assert sa["extended_patterns"] != [], "形态没命中，反向证明无意义"


# ---------------------------------------------------------------- T111 跳空
def test_t111_gap_overrides_box(make_env):
    """日线开盘跳空 1.2 ATR → state=跳空、dominant=事件跳空、旧箱体作废。"""
    base = flat_box()
    prev_close = float(base[-1][4])
    atr = 5.0
    gap_open = prev_close + 1.2 * atr
    last = [base[-1][0] + DAY, "%.2f" % gap_open, "%.2f" % (gap_open + 3),
            "%.2f" % (gap_open - 3), "%.2f" % (gap_open + 1), "10"]
    bars = base + [last]

    page = make_env().page
    r = arb(page, bars_1d=bars, bars_4h=flat_box(), now=WED_OPEN)
    assert r["state"] == "跳空", r
    assert r["dominant"] == "事件跳空", r
    assert r["confidence"] == "High", r
    assert r["box"]["valid"] is False, r
    assert r["gap_atr"] >= 0.8, r


def test_t111b_gap_visible_in_snapshot(make_env):
    """同一根跳空日线走真实注入路径：snapshot() 必须给出跳空结论。"""
    base = flat_box()
    prev_close = float(base[-1][4])
    gap_open = prev_close + 6.0
    bars = base + [[base[-1][0] + DAY, "%.2f" % gap_open, "%.2f" % (gap_open + 3),
                    "%.2f" % (gap_open - 3), "%.2f" % (gap_open + 1), "10"]]
    doc = {"updated": 1794150000000, "src": "dukascopy", "symbol": "XAUUSD",
           "price_type": "bid", "sanitized": {"synthetic": 0, "dup": 0},
           "stats": {}, "gaps": {}, "kl": {"1d": bars, "4h": flat_box()}}
    env = make_env(xau=doc)
    page = env.page
    page.wait_for_timeout(600)
    r = v76(page, "o => window.__xt.v76.snapshot(o)", {"now": WED_OPEN})
    assert r["structure"]["state"] == "跳空", r["structure"]
    assert r["structure"]["dominant"] == "事件跳空", r["structure"]
    assert r["structure"]["box"]["valid"] is False, r["structure"]


# ---------------------------------------------------------------- 周期分栏
def test_t109_periods_are_labelled(make_env):
    """4H 与日线 ADX 分字段存放，文案必须带周期，不允许无标注混用。"""
    page = make_env().page
    r = arb(page, bars_1d=flat_box(), bars_4h=zig([4000, 4200, 4100, 4300, 4150, 4350]),
            now=WED_OPEN)
    assert "adx_1d" in r and "adx_4h" in r, r
    assert isinstance(r["adx_1d"], (int, float)) and isinstance(r["adx_4h"], (int, float)), r
    assert r["trend_state"] in ("趋势", "过渡", "震荡", "数据不足"), r
