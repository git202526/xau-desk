"""v7.6 P5：事件窗口防呆 + 事件雷达自检误报修复（T115–T118）。

两个真问题（P5 前实测复现）：
  1) 自检用 `cat` 正则判「是不是数值事件」，把央行决议 / 美联储讲话当成数值指标，
     它们天然没有前值·预期，于是「数值事件有前值/预期」与「未公布已给双情景」双双误报红。
  2) 地缘规则的正则只有伊朗 / 中东 / 战争，`俄乌局势升级` 落到「其他 · 未收录」，
     自检反而绿——误报的反面是漏报。

修法：`evJudge` 输出 `kind:"num"|"event"`，自检按 kind 分支判；补齐地缘词表。
事件窗口：未来 24h 内有 ≥4 星事件时，结构置信降一档、note 写明，**state / dominant 不动**。
"""
import datetime as dt

DAY = 86400000
START = 1793000000000
WED_OPEN = 1794362400000


def wrap(mids, half=2.0, step=DAY, start=START):
    out, prev = [], None
    for i, m in enumerate(mids):
        o = m - half * 0.4 if prev is None else prev
        out.append([start + i * step, "%.2f" % o, "%.2f" % (m + half),
                    "%.2f" % (m - half), "%.2f" % m, "10"])
        prev = m
    return out


BOX_P = [4000, 4015, 4030, 4045, 4058, 4058, 4045, 4030, 4015, 4000]


def box_wave(n=70, half=2.0):
    return wrap([BOX_P[i % len(BOX_P)] for i in range(n)], half)


def xau_doc(bars):
    return {"updated": 1794150000000, "src": "dukascopy", "symbol": "XAUUSD",
            "price_type": "bid", "sanitized": {"synthetic": 0, "dup": 0},
            "stats": {}, "gaps": {}, "kl": {"1d": bars, "4h": bars}}


def judge(page, **kw):
    return page.evaluate("e => window.__xt.evJudge(e)", kw)


def check(page, rows):
    return page.evaluate("r => window.__xt.evCheck(r)", rows)


def stat(rows):
    return {c["k"]: c["ok"] for c in rows}


# ---------------------------------------------------------------- T115 kind
def test_t115_kind_split(make_env):
    """央行决议 / 讲话 / 购金是事件；非农与 10Y 收益率是数值。"""
    page = make_env().page
    cases = [
        ({"name": "美联储利率决议", "stars": 5, "src": "官方日程"}, "event"),
        ({"name": "美联储主席鲍威尔讲话", "stars": 4}, "event"),
        ({"name": "中国央行黄金储备", "stars": 4}, "event"),
        ({"name": "美国非农就业人口", "stars": 5, "fc": "6.5", "pv": "2.9", "av": "16.5"}, "num"),
        ({"name": "美国10年期国债收益率", "stars": 4, "fc": "4.1", "pv": "4.2", "av": "4.3"}, "num"),
    ]
    for ev, want in cases:
        r = judge(page, **ev)
        assert r["kind"] == want, (ev["name"], r["kind"], want, r["cat"], r["sp"])


# ---------------------------------------------------------------- T116 误报
def test_t116_check_no_false_positive(make_env):
    """FOMC + 地缘 + 非农混排：10 项自检必须全绿。"""
    page = make_env().page
    ts = int(dt.datetime.now().timestamp() * 1000) + 3600000
    rows = [
        judge(page, name="美联储利率决议", ts=ts, stars=5, src="官方日程"),
        judge(page, name="俄乌局势升级", ts=ts, stars=4, src="实时日历"),
        judge(page, name="美国非农就业人口", ts=ts, stars=5, fc="6.5", pv="2.9", av="16.5"),
    ]
    c = stat(check(page, rows))
    assert len(c) == 10, c
    bad = [k for k, v in c.items() if not v]
    assert bad == [], bad


def test_t116b_check_still_catches(make_env):
    """放宽不等于放水：数值事件缺前值/预期、待公布没双情景，仍然必须报红。"""
    page = make_env().page
    a = judge(page, name="美国非农就业人口", stars=5, fc="", pv="", av="16.5")
    c1 = stat(check(page, [a]))
    assert c1["数值事件有前值/预期"] is False, c1

    b = judge(page, name="美国非农就业人口", stars=5, fc="6.5", pv="2.9", av="")
    b["logic"] = "待公布，等数据。"
    c2 = stat(check(page, [b]))
    assert c2["数值待公布已给双情景"] is False, c2


# ---------------------------------------------------------------- T117 不臆测
def test_t117_event_never_fabricates_diff(make_env):
    """事件类凭空给「超预期」而 av 为空 → 自检必须报红。"""
    page = make_env().page
    r = judge(page, name="美联储利率决议", ts=int(dt.datetime.now().timestamp()*1000)+3600000,
              stars=5, src="官方日程")
    assert r["kind"] == "event" and r["av"] == ""
    r["diff"] = "超预期"
    c = stat(check(page, [r]))
    assert c["无公布值不臆测"] is False, c


# ---------------------------------------------------------------- T118 地缘
def test_t118_geo_keywords(make_env):
    """俄乌 / 红海 这类真实地缘必须命中 geo，而不是「其他 · 未收录」。"""
    page = make_env().page
    for name in ("俄乌局势升级", "红海航运遇袭", "中东冲突扩大"):
        r = judge(page, name=name, stars=4)
        assert r["cat"] == "地缘" and r["sp"] == "geo", (name, r["cat"], r["sp"])
        assert r["verdict"] == "拉锯", (name, r["verdict"])
        assert "避险脉冲" in r["logic"] and "实际利率上行" in r["logic"], r["logic"]


# ---------------------------------------------------------------- T119 窗口
def test_t119_event_window_lowers_conf(make_env):
    """窗口内只降置信、不改结论；state / dominant 逐字不变。"""
    page = make_env().page
    bars = box_wave()
    ev = [{"name": "美联储利率决议", "hours": 2, "stars": 5}]
    a = page.evaluate("""o => window.__xt.v76.arbitrate(
        {bars_1d:o.b, bars_4h:o.b, adx_1d:33, adx_4h:33})""", {"b": bars})
    b = page.evaluate("""o => window.__xt.v76.arbitrate(
        {bars_1d:o.b, bars_4h:o.b, adx_1d:33, adx_4h:33, event_window:o.e})""",
                      {"b": bars, "e": ev})
    assert a["confidence"] == "High", a
    assert b["confidence"] == "Medium", b
    assert b["event_window_flag"] is True, b
    assert b["state"] == a["state"] and b["dominant"] == a["dominant"], (a, b)
    assert "事件窗口" in b["note"] and "美联储利率决议" in b["note"], b["note"]


def test_t119b_no_event_keeps_conf(make_env):
    """空窗口不降档、不加字。"""
    page = make_env().page
    bars = box_wave()
    a = page.evaluate("""o => window.__xt.v76.arbitrate(
        {bars_1d:o.b, bars_4h:o.b, adx_1d:33, adx_4h:33})""", {"b": bars})
    b = page.evaluate("""o => window.__xt.v76.arbitrate(
        {bars_1d:o.b, bars_4h:o.b, adx_1d:33, adx_4h:33, event_window:[]})""", {"b": bars})
    assert b["confidence"] == a["confidence"] == "High", (a, b)
    assert b["event_window_flag"] is False
    assert "事件窗口" not in b["note"]


# ---------------------------------------------------------------- T120 UI
def test_t120_pnote_shows_window(make_env):
    """未来 30 分钟有 5 星事件：.pnote 必须写出来，且不影响通道原句。"""
    import time as _t
    soon = int(_t.time() * 1000) + 30 * 60000
    cal = {"updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
           .isoformat().replace("+00:00", "Z"),
           "week": "w", "count": 1,
           # loadCalendar 少于 3 条直接判不可用（防止半张日历冒充实时源），
           # 所以这里补齐 3 条；只有 CPI 那条进事件窗口
           "events": [{"t": soon, "ccy": "USD", "name": "美国10月CPI年率",
                       "en": "CPI y/y", "impact": 5, "fc": "3.1", "pv": "3.0", "src": "ff"},
                      {"t": soon + 3600000, "ccy": "USD", "name": "美国初请失业金人数",
                       "en": "Unemployment Claims", "impact": 4, "fc": "198K", "pv": "194K",
                       "src": "ff"},
                      {"t": soon + 7200000, "ccy": "EUR", "name": "欧元区零售销售月率",
                       "en": "Retail Sales m/m", "impact": 2, "fc": "0.4%", "pv": "0.6%",
                       "src": "ff"}]}
    env = make_env(xau=xau_doc(box_wave()), calendar=cal)
    page = env.page
    page.wait_for_function("() => window.__xt.state().cal.ok === true", timeout=6000)
    page.wait_for_timeout(300)
    s = page.evaluate("() => window.__xt.v76.snapshot().structure")
    assert s["event_window_flag"] is True, s.get("event_window")
    pn = page.evaluate("() => (document.querySelector('.pnote')||{}).textContent||''")
    assert "事件窗口" in pn and "CPI" in pn, pn
    ch = page.evaluate("() => (document.querySelector('#chLine')||{}).textContent||''")
    assert "% 位" in ch, ch          # 通道原句没被覆盖
    assert env.errors == [], env.errors
