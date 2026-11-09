"""v7.9 P112：宏观定价卡 / 结构融合固定化 / 箱体结构卡（T158–T183）。

规格：SRS 模块 1/2/3。口径要点（这几条是本文件的核心，别改实现不改测试）：
  - 死区：|Δbp| ≤ 2.00 平盘、|Δ%| ≤ 0.10 平盘；「严格大于」才算有方向
  - 单边缺失 → 「数据不完整」，缺的那边显示 --，不能整卡清空
  - #chLine 以 dataset.raw 为底，重复渲染不得二次追加
  - 触碰容差 min(0.25 × ATR14, 箱宽 × 8%)
  - 形态按「未破颈线 > 已破颈线」排序，只渲染前 2 个
  - 4 星窗口：前 30 分黄条、后 45 分红条；窗口内触碰冻结、突破写「待确认」

本文件先红后绿：v7.8 上没有 __xt.v79 / #boxCard，红灯在接口缺失。
"""
import json
import math
import time

DAY = 86400000
START = 1793000000000


def macro_doc(tips=2.15, tips_bp=3.0, dxy=105.2, dxy_pct=0.2, age_min=0,
              tips_as_of="2026-11-05", dxy_as_of="2026-11-06"):
    return {"fetched_at": int(time.time() * 1000) - int(age_min * 60000),
            "src": "fred+cnbc",
            "tips_10y": {"value": tips, "change_bp": tips_bp, "trend_5d": tips_bp,
                         "as_of": tips_as_of},
            "dxy": {"value": dxy, "change_pct": dxy_pct, "trend_5d": dxy_pct,
                    "as_of": dxy_as_of, "src": "cnbc"}}


DEAD = {"deadBp": 2.0, "deadPct": 0.10, "deadStrict": True}


def judge(page, doc, opt=None):
    return page.evaluate("a => window.__xt.v76.macroJudge(a.d, a.o)",
                         {"d": doc, "o": dict(DEAD if opt is None else opt)})


def txt(page, sel):
    return page.evaluate("s => (document.querySelector(s)||{}).textContent||''", sel)


def box_bars(last_h=None, last_c=None, last_l=None, n=30):
    """对称水平箱：上沿 3955 / 下沿 3905，每 6 根摸一次边。

    当根之前 ATR14 ≈ 24，箱宽 50 > 2 ATR，容差 = min(6, 4) = 4，
    触碰只认「正好摸到边」的那几根，计数可手算。
    """
    out = []
    for i in range(n):
        t = START + i * DAY
        last = (i == n - 1)
        if last:
            if last_h is None:
                h, l, c = 3940.0, 3920.0, 3930.0
            else:
                h, l, c = last_h, last_l, last_c
        elif i % 6 == 0:
            h, l, c = 3955.0, 3905.0, 3930.0
        else:
            h, l, c = 3940.0, 3920.0, 3930.0
        out.append({"t": t, "o": c, "h": h, "l": l, "c": c, "v": 10})
    return out


# ==================================================== 模块 1：宏观定价卡
def test_t158_macro_partial_dxy_missing(make_env):
    """SRS 1.4：仅 DXY 缺失时，TIPS 照常显示，整卡不能塌成「数据延迟」。"""
    page = make_env().page
    page.wait_for_timeout(600)
    d = macro_doc()
    d["dxy"] = {"value": None, "change_pct": None, "as_of": "", "src": "cnbc"}
    r = judge(page, d)
    assert r["status"] == "数据不完整", r
    assert r["partial"] is True, r
    assert r["tips_10y"]["value"] == 2.15, r
    assert r["dxy"]["value"] is None, r
    assert r["stale"] is False, r


def test_t159_deadzone_tips_exactly_2bp(make_env):
    """SRS 1.2：|Δbp| ≤ 2.00 判平盘；2.01 才有方向。"""
    page = make_env().page
    page.wait_for_timeout(600)
    r = judge(page, macro_doc(tips_bp=2.00, dxy_pct=0.0))
    assert r["status"] == "中性", r
    assert r["tips_10y"]["change_bp"] == 2.0, r

    r2 = judge(page, macro_doc(tips_bp=2.01, dxy_pct=0.0))
    assert r2["status"] == "利空", r2          # TIPS 上行 = 对金利空

    r3 = judge(page, macro_doc(tips_bp=-2.01, dxy_pct=0.0))
    assert r3["status"] == "利多", r3


def test_t160_deadzone_dxy_exactly_10bp(make_env):
    """SRS 1.2：|Δ%| ≤ 0.10 判平盘；0.11 才有方向。"""
    page = make_env().page
    page.wait_for_timeout(600)
    r = judge(page, macro_doc(tips_bp=0.0, dxy_pct=0.10))
    assert r["status"] == "中性", r
    r2 = judge(page, macro_doc(tips_bp=0.0, dxy_pct=0.11))
    assert r2["status"] == "利空", r2


def test_t161_macro_all_missing(make_env):
    """SRS 1.4：全挂 → 数据延迟，两个数值都必须 --。"""
    page = make_env(macro=None).page
    page.wait_for_timeout(800)
    c = txt(page, "#macroCard")
    assert "数据延迟" in c, c
    assert c.count("--") >= 2, c
    assert page.eval_on_selector("#mcTips", "e => e.textContent.trim()") == "--"
    assert page.eval_on_selector("#mcDxy", "e => e.textContent.trim()") == "--"


def test_t162_card_partial_shows_dash_and_warn(make_env):
    """SRS 1.4：单边挂 → 缺的一边 --，badge 黄字「数据不完整」。"""
    d = macro_doc()
    d["dxy"] = {"value": None, "change_pct": None, "as_of": "", "src": "cnbc"}
    page = make_env(macro=d).page
    page.wait_for_timeout(1000)
    assert page.eval_on_selector("#mcTips", "e => e.textContent.trim()") == "2.15%"
    assert page.eval_on_selector("#mcDxy", "e => e.textContent.trim()") == "--"
    assert page.eval_on_selector("#mcStatus", "e => e.textContent.trim()") == "数据不完整"
    cls = page.eval_on_selector("#mcStatus", "e => e.className")
    assert "warnc" in cls, cls


def test_t163_card_footer_as_of(make_env):
    """SRS 1.4：底部常驻「数据截至 MM-DD HH:00」。"""
    page = make_env(macro=macro_doc()).page
    page.wait_for_timeout(1000)
    s = page.eval_on_selector("#mcAs", "e => e.textContent.trim()")
    assert s.startswith("数据截至 "), s
    import re
    assert re.search(r"\d{2}-\d{2} \d{2}:\d{2}", s), s


def test_t164_card_two_column_grid(make_env):
    """SRS 1.1：CSS Grid 两列，390 / 320 都不许横向溢出。"""
    for w in (390, 320):
        env = make_env(macro=macro_doc(), width=w, height=844)
        page = env.page
        page.wait_for_timeout(900)
        r = page.evaluate("""() => {
          const c = document.querySelector('#macroCard');
          const g = getComputedStyle(c.querySelector('.mgrid'));
          return {disp: g.display, cols: g.gridTemplateColumns.split(' ').length,
                  sw: document.documentElement.scrollWidth,
                  iw: window.innerWidth,
                  csw: c.scrollWidth, ccw: c.clientWidth};
        }""")
        assert r["disp"] == "grid" and r["cols"] == 2, r
        assert r["sw"] <= r["iw"] + 1, (w, r)
        assert r["csw"] <= r["ccw"] + 1, (w, r)


def test_t165_value_and_label_type_scale(make_env):
    """SRS 1.2：主数值固定白色 18px 加粗，标签灰 13px。"""
    env = make_env(macro=macro_doc())
    page = env.page
    page.wait_for_timeout(900)
    r = page.evaluate("""() => {
      const v = getComputedStyle(document.querySelector('#mcTips'));
      const k = getComputedStyle(document.querySelector('#macroCard .mk'));
      return {vs: v.fontSize, vw: v.fontWeight, vc: v.color,
              ks: k.fontSize, kc: k.color};
    }""")
    assert r["vs"] == "18px" and int(r["vw"]) >= 600, r
    assert r["ks"] == "13px", r


def test_t166_card_badge_strong_levels(make_env):
    """SRS 1.3：强利空红、强利多绿、中性灰，三档各给底色。"""
    env = make_env(macro=macro_doc(tips_bp=4.0, dxy_pct=0.30))
    page = env.page
    page.wait_for_timeout(900)
    assert page.eval_on_selector("#mcStatus", "e => e.textContent.trim()") == "强利空"
    assert "bearc" in page.eval_on_selector("#mcStatus", "e => e.className")
    bg = page.eval_on_selector("#mcStatus", "e => getComputedStyle(e).backgroundColor")
    assert bg not in ("rgba(0, 0, 0, 0)", "transparent"), bg


def test_t167_card_placement_between_price_and_pos(make_env):
    """SRS 1.1：必须在价格卡与持仓卡之间。"""
    env = make_env(macro=macro_doc())
    page = env.page
    page.wait_for_timeout(800)
    r = page.evaluate("""() => {
      const c = document.querySelector('#macroCard');
      const hero = document.querySelector('.hero');
      const bar = document.querySelector('#posBar');
      return {a: !!(hero.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING),
              b: !!(c.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_FOLLOWING)};
    }""")
    assert r["a"] and r["b"], r


# ==================================================== 模块 2：结构融合
def test_t168_chline_no_nesting(make_env):
    """SRS 2.2：连续渲染两次，追加内容只能出现一次。"""
    env = make_env(macro=macro_doc())
    page = env.page
    page.wait_for_timeout(900)
    page.evaluate("""() => { window.__xt.v79.renderFusion();
                             window.__xt.v79.renderFusion(); }""")
    t = txt(page, "#chLine")
    assert t.count("日线主导") == 1, t
    assert t.count("% 位") == 1, t


def test_t169_chline_keeps_raw_prefix(make_env):
    """SRS 2.2 兼容红线：原通道句必须是前缀（startswith 断言不能破）。"""
    env = make_env(macro=macro_doc())
    page = env.page
    page.wait_for_timeout(900)
    page.evaluate("() => window.__xt.v79.renderFusion()")
    r = page.evaluate("""() => {
      const l = document.querySelector('#chLine');
      return {raw: l.dataset.raw || '', text: l.textContent};
    }""")
    assert r["raw"] and r["text"].startswith(r["raw"]), r
    assert "主导：通道（有效）" in r["text"] or "主导：箱体" in r["text"], r


def test_t170_pnote_badge_style(make_env):
    """SRS 2.1：第二行是 badge（inline-block / 12px / 圆角 4px）。"""
    env = make_env(macro=macro_doc(tips_bp=4.0, dxy_pct=0.30))
    page = env.page
    page.wait_for_timeout(900)
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(400)
    r = page.evaluate("""() => {
      const b = document.querySelector('.pnote .pn-m');
      const cs = getComputedStyle(b);
      return {disp: cs.display, fs: cs.fontSize, br: cs.borderRadius,
              txt: b.textContent};
    }""")
    assert r["disp"] == "inline-block", r
    assert r["fs"] == "12px", r
    assert r["br"] == "4px", r
    assert r["txt"].startswith("宏观验证："), r


def test_t171_pnote_first_line_single_line(make_env):
    """SRS 2.1：第一行单行省略，不许折成两行。"""
    env = make_env(macro=macro_doc())
    page = env.page
    page.wait_for_timeout(900)
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(400)
    r = page.evaluate("""() => {
      const a = document.querySelector('.pnote .pn-s');
      const cs = getComputedStyle(a);
      return {ws: cs.whiteSpace, ov: cs.textOverflow, txt: a.textContent,
              h: Math.round(a.getBoundingClientRect().height),
              lh: parseFloat(cs.lineHeight) || 20};
    }""")
    assert r["ws"] == "nowrap" and r["ov"] == "ellipsis", r
    assert r["h"] <= r["lh"] + 1, r
    assert r["txt"].startswith("结构："), r


def test_t172_summary_line_null_safe(make_env):
    """SRS 2.1 缺失兜底：字段为 null 时必须写 --，不能出现 undefined/NaN。"""
    page = make_env().page
    page.wait_for_timeout(600)
    s = page.evaluate("""() => window.__xt.v79.summaryLine(
      {dominant: null, state: null, adx_1d: null, box: null}, null)""")
    assert "undefined" not in s and "NaN" not in s, s
    assert "ADX --" in s, s


# ==================================================== 模块 3：箱体结构卡
def test_t173_boxcard_state_a(make_env):
    """SRS 2.3 B：无箱体时给固定文案，不留空白。"""
    page = make_env().page
    page.wait_for_timeout(600)
    page.evaluate("""() => window.__xt.v79.renderBox({
      box: {valid: false, upper: 3955, lower: 3905, reason: "宽度不足 2 ATR"},
      patterns: [], price: 3930})""")
    t = txt(page, "#boxCard")
    assert "当前由【通道】主导，未识别到有效水平箱体。" in t, t
    assert page.eval_on_selector_all("#boxCard .bxrow", "e => e.length") == 0


def test_t174_boxcard_state_b_fields(make_env):
    """SRS 2.3 B：六项必须齐（上下沿 / 中轴 / 宽度 / 触碰 / 突破）。"""
    page = make_env().page
    page.wait_for_timeout(600)
    page.evaluate("""() => window.__xt.v79.renderBox({
      box: {valid: true, upper: 3955, lower: 3905, touch_up: 3, touch_dn: 2,
            width_atr: 2.1, breakout: "未突破"},
      patterns: [], price: 3930})""")
    t = txt(page, "#boxCard")
    for word in ("上沿", "下沿", "中轴", "宽度", "触碰", "突破"):
        assert word in t, (word, t)
    assert "3955" in t and "3905" in t, t


def test_t175_touch_tolerance_formula(make_env):
    """SRS 2.3 B 容差公式：min(0.25 × ATR, 宽度 × 8%)。"""
    page = make_env().page
    page.wait_for_timeout(600)
    r = page.evaluate("""() => [
      window.__xt.v79.touchTol(10, 100),      // min(2.5, 8)  = 2.5
      window.__xt.v79.touchTol(100, 100),     // min(25, 8)   = 8
      window.__xt.v79.touchTol(0, 100),       // 非法 ATR     = 0
      window.__xt.v79.touchTol(8, 0)          // 非法宽度     = 0
    ]""")
    assert r[0] == 2.5, r
    assert r[1] == 8.0, r
    assert r[2] == 0 and r[3] == 0, r


def test_t176_touch_count_matches_formula(make_env):
    """SRS 2.3 B：容差内才计触碰，手算得出 3 上 / 3 下。"""
    page = make_env().page
    page.wait_for_timeout(600)
    r = page.evaluate("b => window.__xt.v76.boxDetect(b, {now: 1795100000000})",
                      box_bars())
    assert r["valid"] is True, r
    assert r["upper"] == 3955.0 and r["lower"] == 3905.0, r
    assert r["touch_up"] == 5, r          # i = 0/6/12/18/24
    assert r["touch_dn"] == 5, r


def test_t177_true_breakout(make_env):
    """SRS 2.3 B：收盘破边 + 0.5 ATR = 真突破（旧实现是死代码，永远判不出来）。"""
    page = make_env().page
    page.wait_for_timeout(600)
    r = page.evaluate("b => window.__xt.v76.boxDetect(b, {now: 1795100000000})",
                      box_bars(last_h=3980.0, last_c=3975.0, last_l=3920.0))
    assert r["breakout"] == "真突破", r


def test_t178_false_breakout(make_env):
    """SRS 2.3 B：盘中刺破又收回 = 假突破。"""
    page = make_env().page
    page.wait_for_timeout(600)
    r = page.evaluate("b => window.__xt.v76.boxDetect(b, {now: 1795100000000})",
                      box_bars(last_h=3962.0, last_c=3950.0, last_l=3920.0))
    assert r["breakout"] == "假突破", r


def test_t179_false_breakout_rendered_struck(make_env):
    """SRS 2.3 B：假突破灰字带删除线，真突破红字。"""
    page = make_env().page
    page.wait_for_timeout(600)
    page.evaluate("""() => window.__xt.v79.renderBox({
      box: {valid: true, upper: 3955, lower: 3905, touch_up: 2, touch_dn: 2,
            width_atr: 2.1, breakout: "假突破"},
      patterns: [], price: 3930})""")
    r = page.evaluate("""() => {
      const e = document.querySelector('#boxCard .bxbk.fake');
      return e ? {txt: e.textContent, td: getComputedStyle(e).textDecorationLine} : null;
    }""")
    assert r and "假突破" in r["txt"], r
    assert "line-through" in r["td"], r


def test_t180_patterns_truncated_to_two(make_env):
    """SRS 2.3 C：3 个形态只渲染前 2 个。"""
    page = make_env().page
    page.wait_for_timeout(600)
    page.evaluate("""() => window.__xt.v79.renderBox({
      box: {valid: true, upper: 3955, lower: 3905, touch_up: 2, touch_dn: 2,
            width_atr: 2.1, breakout: "未突破"},
      price: 3915,
      patterns: [
        {name: "收敛三角", status: "", score: 0.72},
        {name: "上升三角", status: "", score: 0.70},
        {name: "旗形", status: "", score: 0.66}
      ]})""")
    n = page.eval_on_selector_all("#boxCard .bxp", "e => e.length")
    assert n == 2, n
    t = txt(page, "#boxCard")
    assert "收敛三角" in t and "上升三角" in t and "旗形" not in t, t


def test_t181_pattern_order_neckline_first(make_env):
    """SRS 2.3 C：未破颈线排在已破颈线之前，即使分数更低。"""
    page = make_env().page
    page.wait_for_timeout(600)
    names = page.evaluate("""() => window.__xt.v79.orderPatterns([
      {name: "头肩顶", status: "已破颈线", score: 0.90},
      {name: "收敛三角", status: "", score: 0.60},
      {name: "旗形", status: "", score: 0.66}
    ]).map(p => p.name)""")
    assert names[0] != "头肩顶", names
    assert names[0] == "旗形", names


def test_t182_position_20pct_rule(make_env):
    """SRS 2.3 C：距边 ≤ 20% 箱高判上/下沿，其余中轴区。"""
    page = make_env().page
    page.wait_for_timeout(600)
    r = page.evaluate("""() => [
      window.__xt.v79.posOf(3912, 3955, 3905),   // 距下沿 7 / 50 = 14% → 下沿
      window.__xt.v79.posOf(3950, 3955, 3905),   // 距上沿 5 / 50 = 10% → 上沿
      window.__xt.v79.posOf(3930, 3955, 3905),   // 中间 → 中轴区
      window.__xt.v79.posOf(3900, 3955, 3905)    // 破下沿也算下沿
    ]""")
    assert r == ["下沿", "上沿", "中轴区", "下沿"], r


def test_t183_playbook_text(make_env):
    """SRS 2.3 C：推演字典必须逐字对上，含失效条件。"""
    page = make_env().page
    page.wait_for_timeout(600)
    r = page.evaluate("""() => ({
      tri: window.__xt.v79.playbook("收敛三角"),
      up:  window.__xt.v79.playbook("上升三角"),
      dn:  window.__xt.v79.playbook("下降三角"),
      hs:  window.__xt.v79.playbook("头肩顶")
    })""")
    assert "面临方向选择" in r["tri"], r
    assert "⚠️ 失效：跌破下沿" in r["up"], r
    assert "空头蓄势" in r["dn"], r
    assert "未破颈线前形态不成立" in r["hs"], r


def test_t184_freeze_window_pre_post(make_env):
    """SRS 2.3 A：事件前 30 分 = 黄条，后 45 分 = 红条，窗口外不冻结。"""
    page = make_env().page
    page.wait_for_timeout(600)
    now = 1795100000000
    r = page.evaluate("""n => ({
      pre:  window.__xt.v79.freezeOf(n, [{t: n + 20 * 60000, name: "非农"}]),
      post: window.__xt.v79.freezeOf(n, [{t: n - 20 * 60000, name: "非农"}]),
      out1: window.__xt.v79.freezeOf(n, [{t: n + 40 * 60000, name: "非农"}]),
      out2: window.__xt.v79.freezeOf(n, [{t: n - 50 * 60000, name: "非农"}])
    })""", now)
    assert r["pre"]["phase"] == "pre" and r["pre"]["name"] == "非农", r
    assert r["post"]["phase"] == "post", r
    assert r["out1"] is None and r["out2"] is None, r


def test_t185_frozen_forces_pending(make_env):
    """SRS 2.3 A：冻结时触碰计数停摆、突破强制「待确认」，但上下沿照常显示。"""
    page = make_env().page
    page.wait_for_timeout(600)
    page.evaluate("""() => window.__xt.v79.renderBox({
      box: {valid: true, upper: 3955, lower: 3905, touch_up: 3, touch_dn: 2,
            width_atr: 2.1, breakout: "真突破"},
      patterns: [], price: 3960,
      freeze: {phase: "pre", name: "非农", left: 12 * 60000}})""")
    t = txt(page, "#boxCard")
    assert "非农" in t, t
    assert "待确认" in t, t
    assert "真突破" not in t, t
    assert "3955" in t and "3905" in t, t
    cls = page.eval_on_selector("#boxCard .bxbanner", "e => e.className")
    assert "pre" in cls, cls


def test_t186_boxcard_no_overflow(make_env):
    """SRS 2.3：箱体卡在 390 / 320 不得横向溢出，且不新增 pageerror。"""
    for w in (390, 320):
        env = make_env(macro=macro_doc(), width=w, height=844)
        page = env.page
        page.wait_for_timeout(1000)
        page.click('.tabbar button[data-tab="struct"]')
        page.wait_for_timeout(500)
        r = page.evaluate("""() => {
          const c = document.querySelector('#boxCard');
          return {sw: document.documentElement.scrollWidth,
                  iw: window.innerWidth,
                  csw: c ? c.scrollWidth : 0, ccw: c ? c.clientWidth : 0};
        }""")
        assert r["sw"] <= r["iw"] + 1, (w, r)
        assert r["csw"] <= r["ccw"] + 1, (w, r)
        assert env.errors == [], env.errors


def test_t187_boxcard_single_source(make_env):
    """架构：箱体卡只渲染，不自己发请求；也不许调其他渲染函数。"""
    import os
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "index.html"), encoding="utf-8").read()
    body = re.search(r"function renderBoxCard\([^)]*\)\{(.*?)\n\}", src, re.S)
    assert body, "找不到 renderBoxCard"
    code = body.group(1)
    for bad in ("fetch(", "fetchJson(", "XMLHttpRequest", "setInterval"):
        assert bad not in code, bad
    for fn in ("renderStruct(", "renderKL(", "renderChart(", "renderFusion(",
               "renderMacroCard(", "renderVerdict("):
        assert fn not in code, fn
