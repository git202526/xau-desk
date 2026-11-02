"""P25 · 判定回溯逐条依据。

PRD-P19 §九 #2 写着「本期做轻量版（三态 + 命中条数），完整指标留后续」。
`judgeOne()` 其实一直在算完整的 5+5 条 `{k,v,d}`，只是 `btRun()` 存序列时把它们丢了，
所以点某一天只能看到「命中 4/5（三个条件名）」——看不到没命中的那条差多少。

本期补上：点格子 → 五条逐条（✓/✗ + 数值）+ 指标行。
逐条的 d 不塞进 seq 缓存（800 天 × 10 条字符串会把缓存撑到 MB 级），点击时现算一次。
"""
import math

import pytest

from conftest import CFG_EMPTY

PX = 4030.0


def _bars(n=320, px=PX):
    """从 px 下方 18% 爬到 px：末段必然是上行，便于找 up 日。"""
    now = 1761696000000
    step = 24 * 3600 * 1000
    raw = [px * 0.82 + i * (px * 0.18 / (n - 1)) + math.sin(i / 9.0) * 11 for i in range(n)]
    adj = raw[-1] - px
    out = []
    for i in range(n):
        c = raw[i] - adj
        t = int(now - (n - 1 - i) * step)
        out.append([t, "%.2f" % (c - 4), "%.2f" % (c + 24), "%.2f" % (c - 24),
                    "%.2f" % c, "12.5", t + step - 1, "0", 0, "0", "0", "0"])
    return out


def _cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=4700, TOTAL=200)
    c.update(kw)
    return c


@pytest.fixture
def p_struct(make_env):
    env = make_env(cfg=_cfg(), price=PX, bars=_bars())
    p = env.page
    p.click('.tabbar button[data-tab="struct"]')
    p.wait_for_timeout(260)
    return p


def pick(p, idx):
    p.evaluate("(i) => window.__xt.btShowPick(i)", idx)
    p.wait_for_timeout(180)


def find_state(p, st):
    return p.evaluate("""(s) => {
      var seq = window.__xt.btRun('1d') || [];
      for(var i = 0; i < seq.length; i++) if(seq[i].state === s) return i;
      return -1;
    }""", st)


def test_y1_five_rows(p_struct):
    """点某一天：五条条件逐条列出。"""
    p = p_struct
    pick(p, 40)
    assert p.eval_on_selector_all("#btPick .btpk-row", "e => e.length") == 5


def test_y2_marks_match_hits(p_struct):
    """✓ 的个数与摘要里的「命中 n/5」一致。"""
    p = p_struct
    pick(p, 40)
    ok = p.eval_on_selector_all("#btPick .btpk-row.ok", "e => e.length")
    seq = p.evaluate("""(i) => { var s = window.__xt.btRun('1d')[i];
      return {hits: s.hits, names: s.names}; }""", 40)
    assert ok == seq["hits"], (ok, seq)


def test_y3_numbers_come_from_judge(p_struct):
    """页面上的数值说明与 judgeOne 返回的 d 逐字相等（防自己拼字符串）。"""
    p = p_struct
    pick(p, 40)
    out = p.evaluate("""(i) => {
      var seq = window.__xt.btRun('1d'), s = seq[i];
      var d = window.__xt.btDetail(i);
      var side = s.state === 'up' ? d.upC : s.state === 'dn' ? d.dnC
               : (d.upN >= d.dnN ? d.upC : d.dnC);
      var dom = Array.from(document.querySelectorAll('#btPick .btpk-row .d'))
                     .map(function(e){ return e.innerText; });
      return {want: side.map(function(x){ return x.d; }), got: dom};
    }""", 40)
    assert out["got"] == out["want"], out


def test_y4_up_day_uses_up_side(p_struct):
    """上行日显示上行侧的条件名。"""
    p = p_struct
    i = find_state(p, "up")
    assert i >= 0, "合成行情应至少有一个上行日"
    pick(p, i)
    out = p.evaluate("""(i) => {
      var d = window.__xt.btDetail(i);
      return {want: d.upC.map(function(x){ return x.k; }),
              got: Array.from(document.querySelectorAll('#btPick .btpk-row .k'))
                        .map(function(e){ return e.innerText; })};
    }""", i)
    assert out["got"] == out["want"], out


def test_y5_range_day_picks_majority_side(p_struct):
    """震荡日取命中较多的一侧（命中数相同取上行侧）。

    UI 级构造震荡日不稳定，这里直接对纯函数下断言：
    给一份 upN=3 / dnN=1 的假 detail，必须出现上行侧条件名、不能出现下行侧。
    """
    p = p_struct
    html = p.evaluate("""() => {
      function mk(pre){ var a = []; for(var i=1;i<=5;i++)
        a.push({k:pre+i, v:i<=3, d:'x'+i}); return a; }
      var d = {upC: mk('UP'), dnC: mk('DN'), upN:3, dnN:1,
               rsi:55.2, rsiState:'中性', atr:18.4};
      return window.__xt.btPickHtml({t:0, state:'rg', hits:3, names:[]}, d);
    }""")
    assert "UP1" in html and "DN1" not in html, html


def test_y6_meta_line(p_struct):
    """指标行：另一侧命中数 + RSI + ATR。"""
    p = p_struct
    pick(p, 40)
    txt = p.eval_on_selector("#btPick .btpk-meta", "e => e.innerText")
    assert "另一侧" in txt and "RSI" in txt and "ATR" in txt, txt


def test_y7_reclick_replaces(p_struct):
    """再点另一格：内容整体替换，不叠加。"""
    p = p_struct
    pick(p, 40)
    first = p.eval_on_selector("#btPick", "e => e.innerText")
    pick(p, 90)
    second = p.eval_on_selector("#btPick", "e => e.innerText")
    assert p.eval_on_selector_all("#btPick .btpk-row", "e => e.length") == 5
    assert second != first


def test_y8_period_switch_hides(p_struct):
    """切周期 → 详情区重新隐藏（原行为不能破）。"""
    p = p_struct
    pick(p, 40)
    assert p.eval_on_selector("#btPick", "e => e.hidden") is False
    p.evaluate("() => window.__xt.setBtPeriod('4h')")
    p.wait_for_timeout(220)
    assert p.eval_on_selector("#btPick", "e => e.hidden") is True


def test_y9_narrow_no_overflow(make_env):
    """320 宽：详情区不横向溢出。"""
    env = make_env(cfg=_cfg(), price=PX, bars=_bars(), width=320)
    p = env.page
    p.click('.tabbar button[data-tab="struct"]')
    p.wait_for_timeout(260)
    pick(p, 40)
    over = p.evaluate("""() => ({
      de: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      bd: document.body.scrollWidth - document.body.clientWidth,
      bad: Array.from(document.querySelectorAll('#btPick .btpk-row')).filter(function(e){
        var r = e.getBoundingClientRect();
        return r.right > document.documentElement.clientWidth + 1;
      }).length
    })""")
    assert over["de"] <= 1, over
    assert over["bd"] <= 1, over
    assert over["bad"] == 0, over


def test_y10_no_placeholders(p_struct):
    """详情里不出现 NaN / undefined / Infinity（null 允许出现在未成交的占位里）。"""
    p = p_struct
    pick(p, 40)
    txt = p.eval_on_selector("#btPick", "e => e.innerText")
    for bad in ("NaN", "undefined", "Infinity"):
        assert bad not in txt, (bad, txt)
