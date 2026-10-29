"""S4：成交量密集区（POC/VAH/VAL）与未回补缺口。"""
import pytest

from conftest import _bars

UNIT_JS = """() => {
  const X = window.__xt;
  const mk = (h, l, c, v) => ({ h: h, l: l, c: c, v: v === undefined ? 1 : v });

  // 100 根堆在 4000-4010，20 根散在 4100-4110
  const concentrated = [];
  for (let i = 0; i < 100; i++) concentrated.push(mk(4000 + i % 10 + 1, 4000 + i % 10, 4000 + i % 10 + 0.5, 2));
  for (let i = 0; i < 20; i++) concentrated.push(mk(4100 + i % 10 + 1, 4100 + i % 10, 4100 + i % 10 + 0.5, 1));

  const zeroVol = [];
  for (let i = 0; i < 120; i++) zeroVol.push(mk(4000 + i % 10, 3999 + i % 10, 4000, 0));

  const gapUp = [mk(100, 95, 98), mk(112, 108, 110)];
  const gapDown = [mk(100, 95, 98), mk(90, 86, 88)];
  const filled = [mk(100, 95, 98), mk(112, 108, 110), mk(109, 99, 105)];

  return {
    vp: X.volumeProfile(concentrated),
    vpZero: X.volumeProfile(zeroVol),
    up: X.findGaps(gapUp, 40, 2),
    down: X.findGaps(gapDown, 40, 2),
    filled: X.findGaps(filled, 40, 2),
  };
}"""

LEVELS_JS = """() => {
  const X = window.__xt;
  const items = w => [].concat.apply([], (w || []).map(l => l.items || []));
  const wide = X.buildLevels(12);
  const plain = X.buildLevels(45, { extended: false });
  return {
    wide: items(wide).map(i => ({ n: i.n, p: i.p })),
    plainVolGap: items(plain).filter(i => /POC|VAH|VAL|缺口/.test(i.n)).length,
  };
}"""


@pytest.fixture
def unit(make_env):
    return make_env().page.evaluate(UNIT_JS)


@pytest.fixture
def levels(make_env):
    return make_env().page.evaluate(LEVELS_JS)


def test_poc_sits_in_dense_zone(unit):
    vp = unit["vp"]
    assert vp is not None
    assert 4000 <= vp["poc"] <= 4010, "POC 没落在密集区：%s" % vp
    assert vp["val"] <= vp["poc"] <= vp["vah"], vp


def test_volume_profile_needs_volume(unit):
    assert unit["vpZero"] is None


def test_gap_up_detected(unit):
    assert unit["up"] == [{"lo": 100, "hi": 108}], unit["up"]


def test_gap_down_detected(unit):
    assert unit["down"] == [{"lo": 90, "hi": 95}], unit["down"]


def test_filled_gap_dropped(unit):
    assert unit["filled"] == [], unit["filled"]


def test_volume_levels_in_list(levels):
    names = "|".join(i["n"] for i in levels["wide"])
    assert "POC" in names and "VAH" in names and "VAL" in names, names


def test_volume_and_gap_not_in_take_plan(levels):
    assert levels["plainVolGap"] == 0


def test_gap_levels_show_up_for_gapping_market(make_env):
    """真实缺口行情下，缺口上下沿必须进列表，且价格等于跳空边界。"""
    bars = _bars("1d")
    prev_high = float(bars[-2][2])
    f = 1.025
    last = bars[-1]
    bars[-1] = [
        last[0],
        round(float(last[1]) * f, 2), round(float(last[2]) * f, 2),
        round(float(last[3]) * f, 2), round(float(last[4]) * f, 2),
        last[5], last[6], last[7], last[8], last[9], last[10], last[11],
    ]
    gap_lo = prev_high
    gap_hi = float(bars[-1][3])
    price = round(float(bars[-1][4]), 2)

    page = make_env(bars=bars, price=price).page
    data = page.evaluate("""() => {
      const X = window.__xt;
      const items = w => [].concat.apply([], (w || []).map(l => l.items || []));
      return items(X.buildLevels(12)).map(i => ({ n: i.n, p: i.p }));
    }""")
    gaps = [i for i in data if "缺口" in i["n"]]
    assert gaps, "有跳空却没有缺口位：%s" % [i["n"] for i in data]
    want = {round(gap_lo, 2), round(gap_hi, 2)}
    got = {round(g["p"], 2) for g in gaps}
    assert got == want, "缺口边界对不上，期望 %s，实际 %s" % (want, got)
