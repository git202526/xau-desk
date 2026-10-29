"""S2：前瞻位（周线枢轴、斐波扩展、MA5/MA10）——数值必须与公式严格一致。"""
import datetime

import pytest

from conftest import _bars

PROBE_JS = """() => {
  const X = window.__xt, S = X.state(), d = X.scaled('1d');
  const near = X.buildLevels(), wide = X.buildLevels(12);
  const plain = X.buildLevels(45, { extended: false });
  const items = w => [].concat.apply([], (w || []).map(l => l.items || []));
  const tail = d.slice(-60);
  let hi = -Infinity, lo = Infinity;
  tail.forEach(b => { if (b.h > hi) hi = b.h; if (b.l < lo) lo = b.l; });
  return {
    scale: S.scale || 1,
    nearItems: items(near),
    wideItems: items(wide),
    nearN: near.length,
    wideN: wide.length,
    widePrices: wide.map(l => l.price),
    fib: { hi: hi, lo: lo, rng: hi - lo },
    trendUp: d[d.length-1].c >= d[Math.max(0, d.length-20)].c,
    plainProactive: items(plain).filter(i => /周枢|扩展 |^MA(5|10)$/.test(i.n)).length,
  };
}"""


def _bars_utc():
    out = []
    for b in _bars("1d"):
        out.append({
            "t": b[0], "h": float(b[2]), "l": float(b[3]), "c": float(b[4]),
            "date": datetime.datetime.fromtimestamp(b[0] / 1000, datetime.timezone.utc).date(),
        })
    return out


def _monday(d):
    return d - datetime.timedelta(days=d.weekday())


def _last_week(bars):
    last_key = _monday(bars[-1]["date"])
    prev = [b for b in bars if _monday(b["date"]) < last_key]
    if not prev:
        return None
    key = _monday(prev[-1]["date"])
    week = [b for b in prev if _monday(b["date"]) == key]
    return {
        "hi": max(b["h"] for b in week),
        "lo": min(b["l"] for b in week),
        "c": week[-1]["c"],
    }


@pytest.fixture
def probe(make_env):
    return make_env().page.evaluate(PROBE_JS)


def _find(items, name):
    hits = [i["p"] for i in items if i["n"] == name]
    assert hits, "没找到 %s：%s" % (name, [i["n"] for i in items])
    return hits[0]


def test_week_pivot_r1_formula(probe):
    """周枢 R1 = 2P − 上周最低，P = (H+L+C)/3。"""
    w = _last_week(_bars_utc())
    sc = probe["scale"]
    P = (w["hi"] + w["lo"] + w["c"]) / 3
    want = (2 * P - w["lo"]) * sc
    got = _find(probe["wideItems"], "周枢 R1")
    assert abs(got - want) < 0.05, "周枢 R1 期望 %.2f，实际 %.2f" % (want, got)


def test_week_pivot_s1_formula(probe):
    """周枢 S1 = 2P − 上周最高。"""
    w = _last_week(_bars_utc())
    sc = probe["scale"]
    P = (w["hi"] + w["lo"] + w["c"]) / 3
    want = (2 * P - w["hi"]) * sc
    got = _find(probe["wideItems"], "周枢 S1")
    assert abs(got - want) < 0.05, "周枢 S1 期望 %.2f，实际 %.2f" % (want, got)


def test_fib_extension_127(probe):
    """扩展 127.2% = 区间高 + 0.272×区间幅度（上升趋势）。"""
    assert probe["trendUp"] is True, "这组数据应为上升趋势"
    fib = probe["fib"]
    want = fib["hi"] + fib["rng"] * 0.272
    got = _find(probe["wideItems"], "扩展 127.2%")
    assert abs(got - want) < 0.05, "扩展 127.2%% 期望 %.2f，实际 %.2f" % (want, got)
    assert got > fib["hi"], "上升趋势的扩展目标必须高于区间高"


def test_fib_extension_161(probe):
    """扩展 161.8% = 区间高 + 0.618×区间幅度，且高于 127.2%。"""
    fib = probe["fib"]
    want = fib["hi"] + fib["rng"] * 0.618
    got = _find(probe["wideItems"], "扩展 161.8%")
    assert abs(got - want) < 0.05, "扩展 161.8%% 期望 %.2f，实际 %.2f" % (want, got)
    assert got > _find(probe["wideItems"], "扩展 127.2%")


def test_ma5_ma10_match_sma(probe):
    """MA5 / MA10 必须等于最近 5 / 10 根收盘均价（按 scale 折算）。"""
    bars = _bars_utc()
    sc = probe["scale"]
    for n, name in ((5, "MA5"), (10, "MA10")):
        want = sum(b["c"] for b in bars[-n:]) / n * sc
        got = _find(probe["wideItems"], name)
        assert abs(got - want) < 0.05, "%s 期望 %.2f，实际 %.2f" % (name, want, got)


def test_proactive_levels_stay_out_of_take_plan(probe):
    """周枢/扩展/短线均线只做展示，不能进止盈候选。"""
    assert probe["plainProactive"] == 0


def test_level_count_grew_after_extension(probe):
    """扩展后宽窗口下的位数量应明显多于 v6.4 的 11 个。"""
    assert probe["wideN"] >= 13, "宽窗口只有 %d 个位" % probe["wideN"]
    assert probe["nearN"] >= 6


def test_no_duplicate_prices_after_extension(probe):
    """新增来源不能撞出重复位：相邻价差仍要 ≥ 0.3%。"""
    prices = sorted(probe["widePrices"])   # 用聚类后的价，items 里是合并前的原始价
    for a, b in zip(prices, prices[1:]):
        assert (b - a) / a >= 0.003, "重复位 %.2f / %.2f" % (a, b)
