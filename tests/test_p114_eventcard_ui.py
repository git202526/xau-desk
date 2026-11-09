"""v7.10 P114：事件防呆卡 / 箱体推演兜底 / 宏观卡信息丰满 / 排版分行（T202–T219）。

四件事，来源是用户转来的 v7.9.1 验收 SRS。逐条对照后做了三处调整（都写在
`docs/开发计划书 §27`）：

1. **事件卡优先级**：SRS 把「日历接口挂掉」排在事件之前。实际是反的——
   日历主备全挂时 `computeEvents()` 仍会给出内置 FOMC/非农，如果这时只显示
   「日历数据延迟」，等于在真正的数据公布前 15 分钟告诉用户「请手动留意」。
   改为：**有具体事件 > 日历不可用 > 无事件**。
2. **时间戳格式**：SRS 写 `MM-DD HH:00`，但宏观是日频数据（TIPS 是美东 T-1、
   DXY 是日涨跌），没有「几点」可言。沿用 v7.9.1 的 `MM-DD 收盘`，
   只把版式改成单独一行。
3. **「正常」状态下 24h 内有远期事件**：SRS 文案是「当前无 4 星重大事件」，
   但有事件时这句就是假的。改为写出最近事件与还剩几小时。

本文件先红后绿：v7.9.1 上没有 `#evCard`、`eventCardOf`，红灯在接口缺失。
"""
import json
import time

DAY = 86400000
DEAD = {"deadBp": 2.0, "deadPct": 0.10, "deadStrict": True}


def now_ms():
    return int(time.time() * 1000)


def cal(events, updated_at=None):
    """一份可用的日历：≥3 条事件才会被 loadCalendar 接受。"""
    return {"events": events,
            "updatedAt": updated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "week": ""}


def ev(name, t, impact=4, ccy="USD"):
    return {"t": int(t), "name": name, "impact": impact, "ccy": ccy,
            "fc": "", "pv": "", "av": ""}


def filler(t, n=3):
    """两条低星事件，只为凑够「≥3 条」这个门槛，不进 4 星窗口。"""
    return [ev("低星占位%d" % i, t + (i + 1) * 3 * DAY, impact=2) for i in range(n)]


def macro_doc(tips=2.15, tips_bp=3.0, dxy=105.2, dxy_pct=0.2, age_min=0,
              tips_as_of=None, dxy_as_of=None):
    if tips_as_of is None:
        tips_as_of = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 2 * 86400))
    if dxy_as_of is None:
        dxy_as_of = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 1 * 86400))
    return {"fetched_at": int(time.time() * 1000) - int(age_min * 60000),
            "src": "fred+cnbc",
            "tips_10y": {"value": tips, "change_bp": tips_bp, "trend_5d": tips_bp,
                         "as_of": tips_as_of},
            "dxy": {"value": dxy, "change_pct": dxy_pct, "trend_5d": dxy_pct,
                    "as_of": dxy_as_of, "src": "cnbc"}}


def struct(page):
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(500)


def txt(page, sel):
    return page.evaluate("s => ((document.querySelector(s)||{}).textContent||'')", sel)


# ==================================================== 任务 1：事件防呆卡
def test_t202_no_event_shows_normal(make_env):
    """T1：日历可用但 24h 内没有 4 星事件 → 灰色「正常」，文案照 SRS。"""
    page = make_env(calendar=cal(filler(now_ms()))).page
    page.wait_for_timeout(1200)
    struct(page)
    r = page.evaluate("""() => {
      const c = document.querySelector('#evCard');
      if(!c) return {miss:true};
      const t = c.querySelector('.ev-title'), b = c.querySelector('.ev-content');
      return {t: t.textContent.trim(), b: b.textContent.trim(),
              tc: getComputedStyle(t).color, bc: getComputedStyle(b).color,
              bg: getComputedStyle(c).backgroundColor,
              h: Math.round(c.getBoundingClientRect().height)};
    }""")
    assert not r.get("miss"), "缺少 #evCard"
    assert "事件防呆：正常" in r["t"], r
    assert "当前无 4 星重大事件" in r["b"], r
    assert r["h"] > 1, r                        # 常驻，不是 display:none
    assert r["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), r


def test_t203_pre_30min_warning(make_env):
    """T2：距公布 15 分钟 → 黄色预警 + 倒计时分钟数。"""
    page = make_env(calendar=cal([ev("美国核心CPI", now_ms() + 15 * 60000)] +
                                 filler(now_ms()))).page
    page.wait_for_timeout(1200)
    struct(page)
    r = page.evaluate("""() => {
      const c = document.querySelector('#evCard');
      const t = c.querySelector('.ev-title'), b = c.querySelector('.ev-content');
      return {t: t.textContent.trim(), b: b.textContent.trim(),
              tc: getComputedStyle(t).color, bg: getComputedStyle(c).backgroundColor};
    }""")
    assert "事件预警" in r["t"] and "美国核心CPI" in r["t"], r
    assert "剩 15 分钟" in r["b"], r
    assert "暂停开仓" in r["b"], r
    assert r["tc"] == "rgb(250, 173, 20)", r


def test_t204_post_45min_silent(make_env):
    """已公布 10 分钟 → 红色静默，不写「剩 X 分钟」。"""
    page = make_env(calendar=cal([ev("美国非农就业", now_ms() - 10 * 60000)] +
                                 filler(now_ms()))).page
    page.wait_for_timeout(1200)
    struct(page)
    r = page.evaluate("""() => {
      const c = document.querySelector('#evCard');
      const t = c.querySelector('.ev-title'), b = c.querySelector('.ev-content');
      return {t: t.textContent.trim(), b: b.textContent.trim(),
              tc: getComputedStyle(t).color};
    }""")
    assert "数据公布中" in r["t"], r
    assert "暂停突破判定" in r["b"], r
    assert "剩" not in r["b"], r
    assert r["tc"] == "rgb(255, 77, 79)", r


def test_t205_calendar_down_shows_delay(make_env):
    """日历主备全挂、且内置事件也不在窗口内 → 黄色「数据延迟」。"""
    page = make_env().page                      # calendar 默认 404
    page.wait_for_timeout(1200)
    struct(page)
    st = page.evaluate("""() => {
      const f = window.__xt.v710.eventCardOf;
      return f(Date.now(), {events: [], calOk: false});
    }""")
    assert st["state"] == "delay", st
    assert "日历数据延迟" in st["title"], st
    assert "防呆机制降级" in st["body"], st


def test_t206_event_beats_delay(make_env):
    """有具体 4 星事件时，即使日历不可用也要报事件——防呆不能因为源挂了就静默。"""
    page = make_env().page
    page.wait_for_timeout(1000)
    now = now_ms()
    st = page.evaluate("""a => window.__xt.v710.eventCardOf(a.now, {
      events:[{t:a.now + 12*60000, name:'美联储利率决议', stars:5}], calOk:false})""",
                       {"now": now})
    assert st["state"] == "pre", st
    assert "美联储利率决议" in st["title"] and "剩 12 分钟" in st["body"], st


def test_t207_eventcard_placement(make_env):
    """位置：必须在箱体卡正上方。"""
    page = make_env(calendar=cal(filler(now_ms()))).page
    page.wait_for_timeout(1200)
    struct(page)
    r = page.evaluate("""() => {
      const e = document.querySelector('#evCard');
      const b = document.querySelector('#boxCard');
      if(!e || !b) return {miss:true};
      return {before: !!(e.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)};
    }""")
    assert not r.get("miss"), r
    assert r["before"], r


def test_t208_eventcard_never_hidden(make_env):
    """常驻：四种状态都不许 display:none 或零高度。"""
    page = make_env(calendar=cal(filler(now_ms()))).page
    page.wait_for_timeout(1200)
    struct(page)
    for name, o in (("normal", {}), ("delay", {"calOk": False})):
        h = page.evaluate("""a => {
          window.__xt.v710.renderEventCard(a);
          const c = document.querySelector('#evCard');
          return Math.round(c.getBoundingClientRect().height);
        }""", o)
        assert h >= 40, (name, h)


# ==================================================== 任务 2：箱体推演兜底
def test_t209_no_box_no_pattern_follows_trend(make_env):
    """T3：无箱体 + 无形态 → 「顺势跟随为主」。"""
    page = make_env().page
    page.wait_for_timeout(900)
    struct(page)
    page.evaluate("""() => window.__xt.v79.renderBox({
      box:{valid:false, reason:'日线 ADX > 25，趋势中不判箱体'},
      patterns:[], atr:20, price:3950})""")
    page.wait_for_timeout(200)
    s = txt(page, "#boxCard")
    assert "辅助标签" in s, s
    assert "顺势跟随为主" in s, s
    assert "留意通道上下沿" in s, s


def test_t210_box_no_pattern_range_play(make_env):
    """有箱体 + 无形态 → 「高抛低吸为主」。"""
    page = make_env().page
    page.wait_for_timeout(900)
    struct(page)
    page.evaluate("""() => window.__xt.v79.renderBox({
      box:{valid:true, upper:3955, lower:3905, touch_up:3, touch_dn:2,
           width_atr:2.1, breakout:'未突破'},
      patterns:[], atr:24, price:3930})""")
    page.wait_for_timeout(200)
    s = txt(page, "#boxCard")
    assert "高抛低吸为主" in s, s
    assert "留意边界突破" in s, s


def test_t211_patterns_still_render(make_env):
    """有形态时照旧渲染推演，兜底文案不能顶掉真形态。"""
    page = make_env().page
    page.wait_for_timeout(900)
    struct(page)
    page.evaluate("""() => window.__xt.v79.renderBox({
      box:{valid:true, upper:3955, lower:3905, touch_up:3, touch_dn:2,
           width_atr:2.1, breakout:'未突破'},
      patterns:[{name:'收敛三角', status:'', score:0.72}], atr:24, price:3912})""")
    page.wait_for_timeout(200)
    s = txt(page, "#boxCard")
    assert "收敛三角" in s and "推演" in s, s
    assert "高抛低吸为主" not in s, s


def test_t212_pattern_area_grey_style(make_env):
    """分支 B 文案 13px 灰字。"""
    page = make_env().page
    page.wait_for_timeout(900)
    struct(page)
    page.evaluate("""() => window.__xt.v79.renderBox({
      box:{valid:false, reason:'x'}, patterns:[], atr:20, price:3950})""")
    page.wait_for_timeout(200)
    r = page.evaluate("""() => {
      const el = document.querySelector('#boxCard .bxnone');
      if(!el) return {miss:true};
      const cs = getComputedStyle(el);
      return {fs: cs.fontSize, col: cs.color};
    }""")
    assert not r.get("miss"), r
    assert r["fs"] == "13px", r


# ==================================================== 任务 3：宏观卡信息丰满
def test_t213_macro_ripple_flat(make_env):
    """T4：死区 → 「TIPS波动 < 2bp，视为平盘震荡」。"""
    page = make_env(macro=macro_doc(tips_bp=0.5, dxy_pct=0.03)).page
    page.wait_for_timeout(1100)
    r = page.evaluate("""() => {
      const e = document.querySelector('#mcRipple');
      return e ? {t: e.textContent.trim(), fs: getComputedStyle(e).fontSize,
                  c: getComputedStyle(e).color} : {miss:true};
    }""")
    assert not r.get("miss"), "缺少 #mcRipple"
    assert "平盘震荡" in r["t"], r
    assert r["fs"] == "12px", r


def test_t214_macro_ripple_conflict(make_env):
    """方向相反 → 「TIPS与DXY方向相反，拉锯」。"""
    page = make_env(macro=macro_doc(tips_bp=-4.0, dxy_pct=0.74)).page
    page.wait_for_timeout(1100)
    assert "拉锯" in txt(page, "#mcRipple")


def test_t215_macro_ripple_bear(make_env):
    """强利空 → 「实际利率与美元共振，压制金银」。"""
    page = make_env(macro=macro_doc(tips_bp=4.0, dxy_pct=0.30)).page
    page.wait_for_timeout(1100)
    r = page.evaluate("""() => {
      const e = document.querySelector('#mcRipple');
      return {t: e.textContent.trim(), c: getComputedStyle(e).color};
    }""")
    assert "压制金银" in r["t"], r
    assert r["c"] == "rgb(255, 77, 79)", r


def test_t216_macro_ripple_bull(make_env):
    """强利多 → 「支撑金银」，绿色。"""
    page = make_env(macro=macro_doc(tips_bp=-4.0, dxy_pct=-0.30)).page
    page.wait_for_timeout(1100)
    r = page.evaluate("""() => {
      const e = document.querySelector('#mcRipple');
      return {t: e.textContent.trim(), c: getComputedStyle(e).color};
    }""")
    assert "支撑金银" in r["t"], r
    assert r["c"] == "rgb(82, 196, 26)", r


def test_t217_macro_time_own_line(make_env):
    """任务 3：时间戳单独一行、左对齐、11px 灰。"""
    page = make_env(macro=macro_doc()).page
    page.wait_for_timeout(1100)
    r = page.evaluate("""() => {
      const t = document.querySelector('#mcAs'), s = document.querySelector('#mcStatus');
      const ct = getComputedStyle(t), cs = getComputedStyle(s);
      const rt = t.getBoundingClientRect(), rs = s.getBoundingClientRect();
      return {fs: ct.fontSize, disp: ct.display, col: ct.color,
              below: rt.top >= rs.bottom - 1, left: Math.abs(rt.left - rs.left),
              flex: cs.display, txt: t.textContent.trim()};
    }""")
    assert r["fs"] == "11px", r
    assert r["below"], r                       # 在 badge 下方，不同行
    assert r["txt"].startswith("数据截至 "), r


# ==================================================== 任务 4：排版分行
def test_t218_chline_struct_block(make_env):
    """通道图下方追加文字独立成行：block / 12px / 灰色 / 上间距 4px。"""
    page = make_env(macro=macro_doc()).page
    page.wait_for_timeout(1000)
    r = page.evaluate("""() => {
      const sp = document.querySelector('#chLine .chstruct');
      if(!sp) return {miss:true};
      const cs = getComputedStyle(sp);
      const line = document.querySelector('#chLine');
      return {disp: cs.display, fs: cs.fontSize, mt: cs.marginTop,
              text: line.textContent, raw: line.dataset.raw || ''};
    }""")
    assert not r.get("miss"), r
    assert r["disp"] == "block", r
    assert r["fs"] == "12px", r
    assert r["mt"] == "4px", r
    # 兼容红线：原通道句仍是前缀，startswith 断言不能破
    assert r["raw"] and r["text"].startswith(r["raw"]), r


def test_t219_layout_budget_and_no_overflow(make_env):
    """加了事件卡与时间戳行，一屏预算与横向溢出仍要守住。"""
    env = make_env(calendar=cal(filler(now_ms())), macro=macro_doc())
    page = env.page
    page.wait_for_timeout(1200)
    h = page.eval_on_selector("#panel-ov", "e => e.getBoundingClientRect().height")
    assert h <= 860, "总览高度 %s，超出一屏" % h
    struct(page)
    for w, hh in ((390, 844), (320, 568)):
        p2 = make_env(calendar=cal(filler(now_ms())), width=w, height=hh).page
        p2.wait_for_timeout(1100)
        struct(p2)
        ov = p2.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert ov <= 0, (w, ov)
        assert p2.evaluate("!!document.querySelector('#evCard').offsetHeight"), w
    assert env.errors == [], env.errors
