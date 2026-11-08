"""v7.6 P1：XAU/USD 数据层测试（T100 / T101 / T102 / T112 / T114）。

全部围绕真实伦敦金特性：
  - 休市填充根（v=0 且四价相等）必须被剔除
  - 周末断层必须在图上断开，不能用横线连
  - 每日结算停盘时结构冻结但不清空
  - lastT 取清洗后末根，否则周一增量会从假数据续

这些用例现在应当全红（`__xt.v76` 尚未实现），红在「接口未实现」而不是断言写错。
"""
import json

HOUR = 3600_000

# 2026-11-06（周五）21:00 UTC 收盘；2026-11-08（周日）22:00 UTC 开盘
FRI_CLOSE = 1793998800000      # 2026-11-06 21:00 UTC
SUN_OPEN = 1794175200000       # 2026-11-08 22:00 UTC
WED_OPEN = 1794362400000       # 2026-11-11 02:00 UTC（周三盘中）
WED_SETTLE = 1794436200000     # 2026-11-11 22:30 UTC = 纽约 17:30 结算停盘
SAT_NOON = 1794052800000       # 2026-11-07 12:00 UTC（周六）


def bars_1h(n=120, start=1793000000000, base=4000.0):
    """连续 1h 序列，无填充根。"""
    out = []
    for i in range(n):
        t = start + i * HOUR
        o = base + i * 0.5
        out.append([t, "%.3f" % o, "%.3f" % (o + 1.2),
                    "%.3f" % (o - 1.2), "%.3f" % (o + 0.6), "10"])
    return out


def with_fillers(bars, idxs):
    """把指定下标替换成休市填充根（与 Dukascopy 周日文件里的完全同形）。"""
    out = [list(b) for b in bars]
    for i in idxs:
        t = out[i][0]
        out[i] = [t, "4000.000", "4000.000", "4000.000", "4000.000", "0"]
    return out


def weekend_gap_1h():
    """周五收盘 → 周一开盘，中间 48 小时没有根（真实 XAU 周末）。"""
    out = []
    for i in range(48):                       # 周五 00:00–23:00 之前取前段
        t = FRI_CLOSE - (48 - i) * HOUR
        out.append([t, "4000.000", "4001.000", "3999.000", "4000.500", "10"])
    for i in range(48):                       # 周日 22:00 之后
        t = SUN_OPEN + i * HOUR
        out.append([t, "4010.000", "4011.000", "4009.000", "4010.500", "10"])
    return out


def xau_doc(bars, period="1h"):
    from collections import OrderedDict
    return {
        "updated": 1794150000000,
        "src": "dukascopy",
        "symbol": "XAUUSD",
        "price_type": "bid",
        "window": {"from": "2026-06-12", "to": "2026-11-08",
                   "days_ok": 146, "days_empty": 1, "failed_n": 0, "failed": []},
        "sanitized": {"synthetic": 0, "dup": 0},
        "stats": {period: {"bars": len(bars), "gaps": 0, "missing": 0}},
        "gaps": {period: []},
        "kl": {period: bars},
    }


def v76(page, script, arg=None):
    """调 __xt.v76.*；未实现时给出明确失败信息。"""
    has = page.evaluate("() => !!(window.__xt && window.__xt.v76)")
    assert has, "__xt.v76 未实现（v7.6 接口契约）"
    return page.evaluate(script, arg) if arg is not None else page.evaluate(script)


# ---------------------------------------------------------------- T100 清洗
def test_t100_filler_bars_are_dropped(make_env):
    """48 根填充根必须被剔除，且不能改变剩余序列的 ATR 结构。"""
    clean = bars_1h(120)
    dirty = with_fillers(clean, range(40, 88))          # 中间 48 根假数据
    env = make_env()
    page = env.page

    r = v76(page, """b => {
      const o = window.__xt.v76.sanitizeBars(b, '1h');
      return {n:o.bars.length, synthetic:o.synthetic, dup:o.dup,
              first:o.bars[0][0], last:o.bars[o.bars.length-1][0]};
    }""", dirty)

    assert r["synthetic"] == 48, r
    assert r["n"] == 72, r
    assert r["first"] == clean[0][0] and r["last"] == clean[-1][0], r

    # 清洗后的 ATR 必须与「本来就没有假数据」一致，误差 < 1%
    atr = page.evaluate("""b => {
      const s = window.__xt.v76.sanitizeBars(b,'1h').bars;
      const a = window.__xt.v76.atrOf(s, 14, s.length-1);
      return a;
    }""", dirty)
    ref = page.evaluate("""b => {
      const a = window.__xt.v76.atrOf(b, 14, b.length-1);
      return a;
    }""", clean)
    assert atr and ref and abs(atr - ref) / ref < 0.01, (atr, ref)


def test_t100b_no_filler_no_change(make_env):
    """干净序列不允许被误删——判据必须四条同时满足。"""
    env = make_env()
    r = v76(env.page, """b => {
      const o = window.__xt.v76.sanitizeBars(b,'1h');
      return {n:o.bars.length, synthetic:o.synthetic};
    }""", bars_1h(60))
    assert r["synthetic"] == 0 and r["n"] == 60, r


# ---------------------------------------------------------------- T101 断层
def test_t101_weekend_gap_breaks_chart(make_env):
    """跨周末：gap 记为 session，通道带必须分段，不能一条线连过去。"""
    bars = weekend_gap_1h()
    env = make_env(xau=xau_doc(bars))
    page = env.page
    page.wait_for_timeout(600)

    r = v76(page, """() => {
      const s = window.__xt.v76.snapshot();
      const g = s.sanitized;
      const polys = document.querySelectorAll('#chSvg polygon').length;
      return {gaps:g.gaps, missing:g.missing||0, polys:polys};
    }""")
    assert r["gaps"] >= 1, r
    assert r["missing"] == 0, r
    assert r["polys"] >= 2, "通道带未按缺口分段：%s" % r


def test_t101b_no_gap_no_split(make_env):
    """连续序列不应被切段。"""
    env = make_env(xau=xau_doc(bars_1h(80)))
    page = env.page
    page.wait_for_timeout(600)
    r = v76(page, """() => ({
      gaps: window.__xt.v76.snapshot().sanitized.gaps,
      polys: document.querySelectorAll('#chSvg polygon').length
    })""")
    assert r["gaps"] == 0 and r["polys"] >= 1, r


# ---------------------------------------------------------------- T102 停盘
def test_t102_daily_settlement_freezes_box(make_env):
    """北京 06:30（冬令时）= UTC 22:30 = 纽约 17:30，属每日结算停盘。"""
    env = make_env(xau=xau_doc(bars_1h(120)))
    page = env.page

    closed = v76(page, "t => window.__xt.v76.marketOpenAt(t)", WED_SETTLE)
    open_ = v76(page, "t => window.__xt.v76.marketOpenAt(t)", WED_OPEN)

    assert closed["open"] is False, closed
    assert open_["open"] is True, open_

    r = v76(page, """o => {
      const s = window.__xt.v76.snapshot(o);
      return {frozen:s.structure.box.frozen, evaluated:s.structure.box.evaluated,
              chline:(document.querySelector('#chLine')||{}).textContent||''};
    }""", {"now": WED_SETTLE})
    assert r["frozen"] is True, r
    assert r["evaluated"] is False, r
    assert r["chline"].strip() not in ("", "—"), "休市时不允许清空通道文字"


def test_t102b_weekend_closed(make_env):
    env = make_env()
    r = v76(env.page, "t => window.__xt.v76.marketOpenAt(t)", SAT_NOON)
    assert r["open"] is False and "休" in r["label"], r


# ---------------------------------------------------------------- T112 lastT
def test_t112_lastt_uses_clean_last_bar(make_env):
    """末根是填充根时，lastT 必须回退到最后一根真实根，否则周一增量会从假数据续。"""
    clean = bars_1h(60)
    dirty = with_fillers(clean, range(55, 60))          # 末尾 5 根是假数据
    env = make_env(xau=xau_doc(dirty))
    page = env.page
    page.wait_for_timeout(600)

    st = page.evaluate("() => window.__xt.klState()['1h']")
    assert st["lastT"] == clean[54][0], "lastT=%s 应为真实末根 %s" % (st["lastT"], clean[54][0])
    assert st["n"] == 55, st


# ---------------------------------------------------------------- T114 降级
def test_t114_falls_back_to_paxg(make_env):
    """xau/klines.json 404 → 降级 PAXG，且必须标注，不能冒充 XAU。"""
    env = make_env(xau=None, bars=bars_1h(80))
    page = env.page
    page.wait_for_timeout(700)

    r = v76(page, """() => ({
      src: window.__xt.v76.klSrc(),
      note: (document.querySelector('#klSrcNote')||{}).textContent||'',
      price: (document.querySelector('#pNow')||{}).textContent||''
    })""")
    assert r["src"] == "paxg", r
    assert "PAXG" in r["note"], r
    assert r["price"].strip() not in ("", "--"), r


def test_t114b_uses_xau_when_available(make_env):
    env = make_env(xau=xau_doc(bars_1h(80)))
    page = env.page
    page.wait_for_timeout(600)
    r = v76(page, """() => ({
      src: window.__xt.v76.klSrc(),
      note: (document.querySelector('#klSrcNote')||{}).textContent||''
    })""")
    assert r["src"] == "dukascopy", r
    assert "PAXG" not in r["note"], r
