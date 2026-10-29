"""S1：关键位数据源扩展（前日/前周高低收、整数关口）与 ATR 自适应窗口。"""
import math

import pytest

TABS_JS = """() => {
  const X = window.__xt, S = X.state();
  const lv = X.buildLevels();
  const wide = X.buildLevels(12);
  const plain = X.buildLevels(45, { extended: false });
  const ext = X.buildLevels(45);
  const d = X.scaled('1d');          // 页面内部就是按缩放后的价计算
  const prev = d[d.length - 2];
  const sc = S.scale || 1;
  return {
    price: S.price,
    atrPct: X.atrPctOf(),
    win: X.levelWindow(),
    meta: document.getElementById('klMeta').textContent,
    labels: lv.map(l => l.label),
    dists: lv.map(l => l.dist),
    widths: lv.map(l => l.win),
    wideLabels: wide.map(l => l.label),
    widePairs: wide.map(l => [l.label, l.price]),
    raw: lv.map(l => l.raw || []),
    wideRaw: wide.map(l => l.raw || []),
    prev: { h: prev.h, l: prev.l, c: prev.c },
    scale: sc,
    plainExtCount: plain.filter(l => /整数 |前日|前周/.test(l.label)).length,
    extCount: ext.filter(l => /整数 |前日|前周/.test(l.label)).length,
    takes: (S.takes || []).map(t => t.name),
    prices: lv.map(l => l.price),
  };
}"""


@pytest.fixture
def probe(make_env):
    env = make_env()
    return env.page.evaluate(TABS_JS)


def test_window_follows_atr(probe):
    """窗口 = clamp(3.5×ATR%, 2.5, 12)，且列表严格落在窗口内。"""
    atr, win = probe["atrPct"], probe["win"]
    expect = min(12, max(2.5, atr * 3.5))
    assert abs(win - expect) < 0.01, "窗口不是按 ATR 算的：atr=%.2f win=%.2f" % (atr, win)
    assert win <= 12.0 and win >= 2.5
    assert max(abs(d) for d in probe["dists"]) < win, probe["dists"]
    assert "±%.1f%%" % win in probe["meta"], probe["meta"]


def test_prev_day_levels_present(probe):
    """前日高/低必须出现在列表里，且价格贴着真实前日 K 线。"""
    labels = "|".join(probe["labels"])
    assert "前日高" in labels
    assert "前日低" in labels

    prev = probe["prev"]
    # 同簇会取均值，所以校验「原始价位」而不是合并后的均价
    flat = [p for group in probe["raw"] for p in group]
    for name, price in (("前日高", prev["h"]), ("前日低", prev["l"]), ("前日收", prev["c"])):
        assert any(abs(p - price) < 0.02 for p in flat), \
            "%s 原始价位缺失：期望 %.1f" % (name, price)


def test_prev_week_levels(probe):
    """宽窗口下必须给出前周高/低（窄窗口里可能被过滤，属正常）。"""
    labels = "|".join(probe["wideLabels"])
    assert "前周高" in labels, probe["wideLabels"]
    assert "前周低" in labels, probe["wideLabels"]


def test_prev_week_matches_manual_aggregation(probe):
    """前周高低必须等于上一自然周（UTC 周一起）的实际最高/最低。"""
    import datetime

    from conftest import _bars

    bars = _bars("1d")
    t = [b[0] for b in bars]
    hi = [float(b[2]) for b in bars]
    lo = [float(b[3]) for b in bars]

    def week_key(ms):
        dt = datetime.datetime.utcfromtimestamp(ms / 1000)
        return dt.date() - datetime.timedelta(days=dt.weekday())

    last_week = week_key(t[-1])
    key = None
    want_hi = want_lo = None
    for i in range(len(t) - 1, -1, -1):
        k = week_key(t[i])
        if k >= last_week:
            continue
        if key is None:
            key = k
        if k != key:
            break
        want_hi = hi[i] if want_hi is None else max(want_hi, hi[i])
        want_lo = lo[i] if want_lo is None else min(want_lo, lo[i])

    sc = probe["scale"]
    flat = [p for group in probe["wideRaw"] for p in group]
    assert any(abs(p - want_hi * sc) < 0.02 for p in flat), \
        "前周高对不上，期望 %.1f，原始价位 %s" % (want_hi * sc, sorted(flat)[-6:])
    assert any(abs(p - want_lo * sc) < 0.02 for p in flat), \
        "前周低对不上，期望 %.1f" % (want_lo * sc)


def test_round_number_levels(probe):
    """整数关口必须以 50 的倍数生成（合并后的均值不再满足，故查原始价位）。"""
    flat = [p for group in probe["raw"] for p in group]
    rounds = [p for p in flat if abs(p % 50) < 0.01 or abs(p % 50 - 50) < 0.01]
    assert rounds, "没有任何 50 倍数关口：%s" % flat
    assert any("整数 $" in l for l in probe["labels"]), probe["labels"]


def test_take_plan_ignores_extended_levels(probe):
    """扩展位只能进展示列表，不能混进止盈阶梯（否则已有减仓记录会错位）。"""
    assert probe["extCount"] > 0, "扩展位没生效"
    assert probe["plainExtCount"] == 0, "止盈候选里混进了扩展位"


def test_take_plan_snapshot(probe):
    """止盈阶梯在固定行情下保持原样。"""
    assert probe["takes"] == [
        "回本位", "前高", "延伸 127.2%", "整数 $4200",
        "延伸 200%", "+15%", "+20%", "+25%", "+30%",
    ], probe["takes"]


def test_levels_are_not_duplicated(probe):
    """聚类仍生效：相邻位价差不得小于 0.3%。"""
    prices = sorted(probe["prices"])
    for a, b in zip(prices, prices[1:]):
        assert (b - a) / a >= 0.003, "出现重复位：%.2f / %.2f" % (a, b)


def test_render_meta_and_list_consistent(make_env):
    """页面上的「N 个」必须等于实际渲染的行数。"""
    page = make_env().page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(250)
    n = page.eval_on_selector_all("#klist .kl:not(.now)", "els => els.length")
    meta = page.inner_text("#klMeta")
    assert str(n) in meta, "%s 与实际 %d 行不一致" % (meta, n)
