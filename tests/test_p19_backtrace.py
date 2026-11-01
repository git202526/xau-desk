"""P19-1 判定回溯 · 计算层

契约：
  · 预热 209 根：序列长度 = 总根数 − 209（日线 800 → 591）
  · 区间起止 = 第 209 根与末根的时间戳
  · 同一份数据重算两次，序列逐值相同
  · 回溯用原始报价，不随汇率缩放系数变化
  · 数据不足 210 根返回 None，不给半张图
"""
import pytest

from conftest import CFG_EMPTY, _bars


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=4700.0, TOTAL=200)
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    mk_keys = ("price", "width", "height", "gh_on", "reduced_motion", "tz", "calendar", "bars")

    def _f(**kw):
        mk = {}
        for k in list(kw.keys()):
            if k in mk_keys:
                mk[k] = kw.pop(k)
        mk.setdefault("price", 4030.0)
        return make_env(cfg=cfg(**kw), **mk)
    return _f


def wait_ready(page, k="1d"):
    page.wait_for_function("(k) => !!window.__xt.btWindow(k)", arg=k, timeout=20000)


def win(page, k="1d"):
    return page.evaluate("(k) => window.__xt.btWindow(k)", k)


def seq(page, k="1d"):
    return page.evaluate("(k) => window.__xt.btSeq(k)", k)


def test_b3_window_len(env):
    """日线 800 根 → 可回溯 591 个点（800 − 209）。"""
    bars = _bars("1d", 800)
    p = env(bars=bars).page
    wait_ready(p)
    w = win(p)
    assert w["total"] == 800, w
    assert w["n"] == 591, w

    s = seq(p)
    assert len(s) == 591, len(s)


def test_b4_window_range(env):
    """区间起止必须正好是第 209 根（索引 209）与最后一根。"""
    bars = _bars("1d", 800)
    p = env(bars=bars).page
    wait_ready(p)
    w = win(p)
    assert w["from"] == bars[209][0], (w["from"], bars[209][0])
    assert w["to"] == bars[-1][0], (w["to"], bars[-1][0])

    s = seq(p)
    assert s[0]["t"] == bars[209][0], s[0]
    assert s[-1]["t"] == bars[-1][0], s[-1]


def test_b11_reproducible(env):
    """同一份数据重算两次，序列必须逐值相同（清缓存后再算也一样）。"""
    p = env(bars=_bars("1d", 800)).page
    wait_ready(p)
    a = seq(p)
    p.evaluate("() => window.__xt.btClear()")
    b = seq(p)
    assert a == b
    assert len(a) > 0


def test_b12_scale_invariant(env):
    """汇率缩放系数变化不得影响回溯序列。

    结构页当前判定走缩放后的价格，回溯刻意用原始报价。五条条件都是大小关系 /
    比例，缩放不改真假 —— 这条锁住「将来改判定逻辑时不小心引入缩放依赖」。
    """
    p = env(bars=_bars("1d", 800)).page
    wait_ready(p)
    a = seq(p)

    p.evaluate("""() => {
        window.__xt.state().scale = 1.3;
        window.__xt.btClear();
    }""")
    b = seq(p)
    assert a == b

    p.evaluate("""() => {
        window.__xt.state().scale = 1;
        window.__xt.btClear();
    }""")


def test_b13_insufficient(env):
    """只有 180 根时返回 None，页面不崩。"""
    p = env(bars=_bars("1d", 180)).page
    p.wait_for_timeout(1500)
    assert win(p) is None
    assert seq(p) is None
    # 页面仍然活着（__xt 在、能取到状态）
    assert p.evaluate("() => !!window.__xt.state()") is True


def test_b3a_perf(env):
    """591 次判定必须在可感知延迟内跑完，否则要改成分帧。

    阈值放到 800ms 是给并发负载留余量：实测在本机约 100ms，
    只要不到这个量级就不引入异步复杂度。
    """
    p = env(bars=_bars("1d", 800)).page
    wait_ready(p)
    ms = p.evaluate("""() => {
        window.__xt.btClear();
        const t0 = performance.now();
        window.__xt.btRun('1d');
        return performance.now() - t0;
    }""")
    print("\n[perf] 591 x judgeOne = %.1f ms" % ms)
    assert ms < 800, ms


def open_struct(page):
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(1200)


def card(page):
    return page.evaluate("""() => {
        const c = document.getElementById('btCard');
        if(!c) return null;
        const rects = Array.from(c.querySelectorAll('.btseg'));
        const spans = Array.from(c.querySelectorAll('#btDist span'));
        return {
            rects: rects.length,
            wSum: rects.reduce((a, r) => a + (+r.getAttribute('width') || 0), 0),
            cls: rects.map(r => r.getAttribute('class')),
            firstW: rects.length ? +rects[0].getAttribute('width') : 0,
            firstN: rects.length ? +rects[0].getAttribute('data-n') : 0,
            fill0: rects.length ? getComputedStyle(rects[0]).fill : '',
            dist: spans.map(s => ({
                cls: s.className,
                n: +s.querySelector('b').textContent,
                pct: +(s.textContent.match(/(\\d+)%/) || [0, 0])[1],
            })),
            rows: Array.from(c.querySelectorAll('#btSw .r')).map(r => r.textContent.trim()),
            more: !!document.getElementById('btMore'),
            meta: document.getElementById('btMeta').textContent.trim(),
        };
    }""")


def test_b1_place(env):
    """卡片位置：判定依据之后、通道之前。"""
    p = env(bars=_bars("1d", 800)).page
    wait_ready(p)
    open_struct(p)
    pos = p.evaluate("""() => {
        const r = document.getElementById('reasons');
        const c = document.getElementById('btCard');
        const ch = document.getElementById('chChips');
        return {
            afterReason: !!(r.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING),
            beforeChan: !!(c.compareDocumentPosition(ch) & Node.DOCUMENT_POSITION_FOLLOWING),
        };
    }""")
    assert pos["afterReason"] is True, pos
    assert pos["beforeChan"] is True, pos


def test_b2_default_period(env):
    """默认日线，chip 高亮在日线。"""
    p = env(bars=_bars("1d", 800)).page
    wait_ready(p)
    open_struct(p)
    on = p.evaluate("""() => {
        const b = document.querySelector('#btChips button.on');
        return b ? b.getAttribute('data-k') : null;
    }""")
    assert on == "1d"
    assert card(p)["meta"] == "591 天"


def test_b5_segments_merged(env):
    """色带按连续段合并：段数远小于点数，但宽度之和仍等于 n × 1.5。"""
    p = env(bars=_bars("1d", 800)).page
    wait_ready(p)
    open_struct(p)
    c = card(p)
    n = len(seq(p))
    assert c["rects"] < n, c["rects"]
    assert c["rects"] >= 1
    assert abs(c["wSum"] - n * 1.5) < 0.01, (c["wSum"], n * 1.5)


def test_b6_segment_color(env):
    """段颜色与序列一致：首段 class 对应首点 state，且计算色为红 / 绿 / 灰。"""
    p = env(bars=_bars("1d", 800)).page
    wait_ready(p)
    open_struct(p)
    c = card(p)
    s0 = seq(p)[0]["state"]
    assert c["cls"][0] == "btseg " + s0, (c["cls"][0], s0)

    fills = {"up": "rgb(229, 72, 77)", "dn": "rgb(47, 163, 107)", "rg": "rgb(101, 121, 147)"}
    assert c["fill0"] == fills[s0], (c["fill0"], s0)


def test_b7_distribution(env):
    """三档天数之和 = 序列长度，占比之和 = 100。"""
    p = env(bars=_bars("1d", 800)).page
    wait_ready(p)
    open_struct(p)
    c = card(p)
    n = len(seq(p))
    assert len(c["dist"]) == 3, c["dist"]
    assert sum(x["n"] for x in c["dist"]) == n, c["dist"]
    assert sum(x["pct"] for x in c["dist"]) == 100, c["dist"]
    assert [x["cls"] for x in c["dist"]] == ["up", "rg", "dn"]


def test_b8_switches(env):
    """切换记录条数 = 状态变化次数；超过 5 条时只渲染 5 条 + 展开按钮。"""
    p = env(bars=_bars("1d", 800)).page
    wait_ready(p)
    open_struct(p)
    info = p.evaluate("""() => {
        const st = window.__xt.btStats(window.__xt.btRun('1d'));
        return {
            n: st.switches.length,
            rows: document.querySelectorAll('#btSw .r').length,
            more: !!document.getElementById('btMore'),
        };
    }""")
    assert info["n"] > 0, info
    assert info["rows"] == min(5, info["n"]), info
    assert info["more"] is (info["n"] > 5), info

    if info["n"] > 5:
        p.click("#btMore")
        p.wait_for_timeout(300)
        assert p.evaluate("() => document.querySelectorAll('#btSw .r').length") == info["n"]


def test_b9_switch_date_matches_band(env):
    """最近一条切换记录的日期，必须等于最后一次状态变化的日期。"""
    p = env(bars=_bars("1d", 800)).page
    wait_ready(p)
    open_struct(p)
    ok = p.evaluate("""() => {
        const st = window.__xt.btStats(window.__xt.btRun('1d'));
        if(!st.switches.length) return null;
        const t = st.switches[st.switches.length - 1].t;
        const d = new Date(t);
        const mmdd = String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
        const ymd = d.getFullYear() + '-' + mmdd;
        const first = document.querySelector('#btSw .r span').textContent.trim();
        return { first: first, mmdd: mmdd, ymd: ymd };
    }""")
    assert ok is not None
    assert ok["first"] in (ok["mmdd"], ok["ymd"]), ok


def test_b10_pick(env):
    """点色带任一段：出现该日三态 + 命中 n/5，并且只有一段被选中。"""
    p = env(bars=_bars("1d", 800)).page
    wait_ready(p)
    open_struct(p)
    p.click("#btStrip .btseg >> nth=0")
    p.wait_for_timeout(250)
    info = p.evaluate("""() => {
        const pk = document.getElementById('btPick');
        return {
            hidden: pk.hidden,
            txt: pk.textContent.trim(),
            sel: document.querySelectorAll('.btseg.sel').length,
        };
    }""")
    assert info["hidden"] is False, info
    assert "命中" in info["txt"], info
    assert "/5" in info["txt"], info
    assert any(x in info["txt"] for x in ("上行", "震荡", "下行")), info
    assert info["sel"] == 1, info


def test_b14_narrow(env):
    """320 宽：色带内部横滚，不得把整页撑宽。"""
    p = env(bars=_bars("1d", 800), width=320).page
    wait_ready(p)
    open_struct(p)
    m = p.evaluate("""() => ({
        sw: document.documentElement.scrollWidth,
        cw: document.documentElement.clientWidth,
        stripScroll: document.getElementById('btStrip').scrollWidth,
        stripClient: document.getElementById('btStrip').clientWidth,
    })""")
    assert m["sw"] <= m["cw"] + 1, m
    assert m["stripScroll"] > m["stripClient"], m


def test_b15_period_switch(env):
    """切周期：单位、天数、提示文案、周线像素都要跟着变。"""
    bars = {
        "1w": _bars("1w", 220),
        "1d": _bars("1d", 800),
        "4h": _bars("4h", 750),
        "1h": _bars("1h", 1000),
    }
    p = env(bars=bars).page
    wait_ready(p)
    open_struct(p)

    p.click('#btChips button[data-k="1h"]')
    p.wait_for_timeout(500)
    h1 = p.evaluate("""() => ({
        meta: document.getElementById('btMeta').textContent.trim(),
        note: document.getElementById('btNote').textContent.trim(),
        n: document.querySelectorAll('.btseg').length,
    })""")
    assert h1["meta"] == "791 根", h1
    assert "33 天" in h1["note"], h1
    # 小时级一天内可能切换多次，记录必须带时间，否则同一天出现两行互相矛盾
    rows = p.evaluate("() => Array.from(document.querySelectorAll('#btSw .r span')).map(x => x.textContent.trim())")
    assert rows and all(":" in r for r in rows), rows

    p.click('#btChips button[data-k="1w"]')
    p.wait_for_timeout(500)
    w1 = p.evaluate("""() => {
        const rects = Array.from(document.querySelectorAll('.btseg'));
        return {
            meta: document.getElementById('btMeta').textContent.trim(),
            note: document.getElementById('btNote').textContent.trim(),
            wSum: rects.reduce((a, r) => a + (+r.getAttribute('width') || 0), 0),
        };
    }""")
    assert w1["meta"] == "11 周", w1
    assert "10 周" in w1["note"], w1
    assert abs(w1["wSum"] - 11 * 8) < 0.01, w1
    # 11 个点铺不满一屏，不该再提示「可拖动」
    assert p.evaluate("() => document.getElementById('btDrag').hidden") is True


def test_b16_drag_hint(env):
    """色带可横滚时必须给提示。

    首屏渲染时 panel-struct 还是 display:none，色带宽度算出来是 0，
    提示会被误判成「不需要」——所以切页后要重算一次。
    """
    p = env(bars=_bars("1d", 800)).page
    wait_ready(p)
    open_struct(p)
    p.wait_for_timeout(500)
    d = p.evaluate("""() => {
        const d = document.getElementById('btDrag');
        const s = document.getElementById('btStrip');
        return { hidden: d.hidden, sw: s.scrollWidth, cw: s.clientWidth };
    }""")
    assert d["sw"] > d["cw"], d
    assert d["hidden"] is False, d
