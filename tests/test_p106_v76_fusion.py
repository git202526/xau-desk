"""v7.6 P4：结构 / 宏观的文字融合（T95 / T96 / T109 + 卡片渲染 + 390px 不溢出）。

约束（计划书 §6）：
  - `#macroCard` 是唯一新增卡片，放在 hero 之后、posbar 之前
  - `#chLine` 保留原通道那句（周期 + % 位 + 宽），只在末尾追加结构结论
  - `#vMain` 追加「（状态：过渡 · 4H ADX 22.1）」，周期必须写出来
  - `.pnote` 改成结构 + 宏观的一行验证结论
  - 390px 宽度不得出现横向滚动
"""
import math
import time

DAY = 86400000
START = 1793000000000
WED_OPEN = 1794362400000


def wrap(mids, half=2.0, step=DAY, start=START):
    """开盘价接上一根收盘：日线连续，不制造「开盘缺口」误判。"""
    out, prev = [], None
    for i, m in enumerate(mids):
        o = m - half * 0.4 if prev is None else prev
        out.append([start + i * step, "%.2f" % o, "%.2f" % (m + half),
                    "%.2f" % (m - half), "%.2f" % m, "10"])
        prev = m
    return out


BOX_P = [4000, 4015, 4030, 4045, 4058, 4058, 4045, 4030, 4015, 4000]


def box_wave(n=70, half=2.0):
    """周期 10 的对称箱：每 10 根走一个完整的上下往返。

    选对称波形而不是正弦，是因为正弦在「最近 30 根」上回归斜率并不为 0
    （索引权重会破掉周期对称），boxDetect 会判「斜率不为 0」。
    70 根同时满足 channelOf 的 40 根下限，否则通道图不画，#chLine 没得拼。
    """
    return wrap([BOX_P[i % len(BOX_P)] for i in range(n)], half)


def xau_doc(bars):
    return {"updated": 1794150000000, "src": "dukascopy", "symbol": "XAUUSD",
            "price_type": "bid", "sanitized": {"synthetic": 0, "dup": 0},
            "stats": {}, "gaps": {}, "kl": {"1d": bars, "4h": bars}}


def macro_doc(age_min=0):
    return {"fetched_at": int(time.time() * 1000) - int(age_min * 60000),
            "src": "fred+cnbc",
            "tips_10y": {"value": 2.15, "change_bp": 3.0, "trend_5d": 2.0, "as_of": "2026-11-05"},
            "dxy": {"value": 105.2, "change_pct": 0.2, "trend_5d": 0.3,
                    "as_of": "2026-11-06", "src": "cnbc"}}


def boot(make_env, adx=None, **kw):
    env = make_env(xau=xau_doc(box_wave()), **kw)
    page = env.page
    page.wait_for_timeout(700)
    if adx:
        page.evaluate("a => window.__xt.v76.setAdx(a[0], a[1])", adx)
        page.evaluate("() => window.__xt.refreshAll()")
        page.wait_for_timeout(200)
    return env, page


def txt(page, sel):
    return page.evaluate("s => (document.querySelector(s)||{}).textContent||''", sel)


# ---------------------------------------------------------------- T95 双结构
def test_t95_dual_structure_copy(make_env):
    """日线 24.6 + 4H 22.1：状态过渡、主导双结构，文案带上下沿。"""
    env, page = boot(make_env, adx=(24.6, 22.1))
    s = page.evaluate("() => window.__xt.v76.snapshot().structure")
    assert s["state"] == "双结构", s
    assert s["dominant"] == "双结构并存", s
    assert s["box"]["valid"] is True, s

    ch = txt(page, "#chLine")
    assert "上沿" in ch and "下沿" in ch, ch
    assert "震荡整理中" in ch, ch
    assert "% 位" in ch, ch

    vm = txt(page, "#vMain")
    assert "状态：过渡" in vm, vm
    assert "4H" in vm, vm


# ---------------------------------------------------------------- T96 通道
def test_t96_channel_dominant_copy(make_env):
    """日线 ADX 30：通道为主、箱体作废，文案写「主导：通道（有效）」。"""
    env, page = boot(make_env, adx=(30, 18))
    s = page.evaluate("() => window.__xt.v76.snapshot().structure")
    assert s["dominant"] == "通道为主", s
    assert s["box"]["valid"] is False, s
    assert "趋势" in (s["box"]["reason"] or ""), s

    ch = txt(page, "#chLine")
    assert "主导：通道（有效）" in ch, ch
    assert "% 位" in ch, ch


# ---------------------------------------------------------------- T109 周期
def test_t109_periods_never_mixed(make_env):
    """4H 28 / 日线 15：同屏既有 4H 也有日线，且各自带周期标签。"""
    env, page = boot(make_env, adx=(15, 28))
    s = page.evaluate("() => window.__xt.v76.snapshot().structure")
    assert s["trend_state"] == "趋势", s
    assert s["adx_1d"] == 15 and s["adx_4h"] == 28, s

    all_txt = txt(page, "#vMain") + " " + txt(page, "#vSub")
    assert "4H" in all_txt, all_txt
    assert "日线" in all_txt, all_txt


# ---------------------------------------------------------------- 形态标签
def test_t104_tag_in_chline(make_env):
    """命中形态时只在末尾追加辅助标签，不覆盖原通道描述。"""
    bars = []
    for i in range(45):
        u, l = 4088 - 0.8 * i, 4010 + 0.8 * i
        m, h = (u + l) / 2, (u - l) / 2
        bars.append([START + i * DAY, m - h * 0.35, m + h, m - h, m + h * 0.35, "10"])
    env = make_env(xau=xau_doc(bars))
    page = env.page
    page.wait_for_timeout(700)
    s = page.evaluate("() => window.__xt.v76.snapshot().structure")
    assert any("三角" in x for x in s["extended_patterns"]), s
    ch = txt(page, "#chLine")
    assert "辅助标签" in ch and "三角" in ch, ch
    assert "% 位" in ch, ch


# ---------------------------------------------------------------- 宏观卡
def test_t97_card_renders(make_env):
    env = make_env(macro=macro_doc())
    page = env.page
    page.wait_for_timeout(800)
    c = txt(page, "#macroCard")
    assert "TIPS" in c, c
    assert "美元" in c, c
    assert "数据截至" in c, c
    assert "强利空" in c, c
    # 「TIPS」是术语，普通用户不知道它指 10 年期通胀保值国债的实际收益率，
    # 容易误当成平时说的「美债收益率」（名义）。第一行标签直接用中文，
    # 全称与数据源放 title 里，不占窄屏宽度。
    assert "实际利率" in c, c


def test_t97b_card_404(make_env):
    env = make_env(macro=None)
    page = env.page
    page.wait_for_timeout(800)
    c = txt(page, "#macroCard")
    assert "数据延迟" in c, c
    assert "--" in c, c
    assert env.errors == [], env.errors


def test_t97c_card_placement(make_env):
    """卡片必须在 hero 之后、持仓条之前——放错位置等于把宏观塞进持仓逻辑里。"""
    env, page = boot(make_env, adx=(24.6, 22.1))
    r = page.evaluate("""() => {
      const c = document.querySelector('#macroCard');
      const hero = document.querySelector('.hero');
      const bar = document.querySelector('#posBar');
      return {exists: !!c,
              afterHero: !!(c && hero && (hero.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING)),
              beforeBar: !!(c && bar && (c.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_FOLLOWING))};
    }""")
    assert r["exists"], r
    assert r["afterHero"], r
    assert r["beforeBar"], r


# ---------------------------------------------------------------- 溢出
def test_t115_no_horizontal_overflow_390(make_env):
    env, page = boot(make_env, adx=(24.6, 22.1), width=390, height=844)
    r = page.evaluate("""() => ({
      sw: document.documentElement.scrollWidth,
      iw: window.innerWidth,
      mc: (document.querySelector('#macroCard')||{}).scrollWidth || 0,
      mcw: (document.querySelector('#macroCard')||{}).clientWidth || 0
    })""")
    assert r["sw"] <= r["iw"] + 1, r
    assert r["mc"] <= r["mcw"] + 1, r


def test_t116_pnote_has_structure_and_macro(make_env):
    env = make_env(macro=macro_doc())
    page = env.page
    page.wait_for_timeout(800)
    p = txt(page, ".pnote")
    assert "结构" in p and "宏观" in p, p


# ---------------------------------------------------------------- UI 微调（P109）
def test_t121_macro_labels_plain_chinese(make_env):
    """宏观卡改用中文标签，缩写退到 title。

    「TIPS」是行业黑话，普通用户会当成平时说的「美债收益率」（名义）。
    但完整写法放不进第一行：390px 实测 `.mcrow` 的 scrollWidth 已经等于
    clientWidth（336px，零余量），`美元指数(DXY)` 会溢出到 346px，
    `10Y ` 前缀再宽 15px。所以标签写「实际利率 / 美元指数」，
    全称与数据源放 title —— 桌面可读，不占窄屏宽度。
    """
    env = make_env(macro=macro_doc())
    page = env.page
    page.wait_for_timeout(800)
    c = txt(page, "#macroCard")
    assert "实际利率" in c, c
    assert "美元指数" in c, c
    assert "TIPS" not in c.split("数据截至")[0], "标签位还在用缩写：%s" % c

    titles = page.evaluate(
        "() => [...document.querySelectorAll('#macroCard .mk')].map(e => e.title)")
    assert any("TIPS" in t for t in titles), titles
    assert any("DXY" in t for t in titles), titles


def test_t122_pnote_two_lines(make_env):
    """结构结论与宏观验证分行：挤一行时手机端的折行位置不可控。

    两段必须是各自独立的块（`.pn-s` / `.pn-m`），不是靠 `<br>`——
    靠换行符的话 320px 下每段再折一次，仍然读串。
    """
    env = make_env(macro=macro_doc())
    page = env.page
    page.wait_for_timeout(800)
    page.click('.tabbar button[data-tab="struct"]')   # .pnote 在结构页，不切页量不到位置
    page.wait_for_timeout(400)
    r = page.evaluate("""() => {
      const p = document.querySelector('.pnote');
      const a = p.querySelector('.pn-s'), b = p.querySelector('.pn-m');
      return {a: a && a.textContent.trim(), b: b && b.textContent.trim(),
              aTop: a ? a.getBoundingClientRect().top : null,
              bTop: b ? b.getBoundingClientRect().top : null,
              blocks: a ? getComputedStyle(a).display : null};
    }""")
    assert r["a"] and r["b"], r
    assert "结构" in r["a"] and "宏观" not in r["a"], r["a"]
    assert "宏观" in r["b"], r["b"]
    assert r["blocks"] == "block", r
    assert r["bTop"] > r["aTop"], "两段没分行：%s" % r


def test_t123_chstruct_visual_rank(make_env):
    """通道原句（当前周期描述）与日线仲裁结论要有主次。

    两行同字号同色时，读者分不清哪句是「1 小时看到的」，哪句是「日线仲裁出来的」。
    仲裁结论降半档（10.5px）并加一条淡分割线，视觉上归入「背景结论」。
    """
    env, page = boot(make_env, adx=(24.6, 22.1))
    r = page.evaluate("""() => {
      const line = document.querySelector('#chLine');
      const s = line.querySelector('.chstruct');
      if(!s) return {missing: true};
      const cs = getComputedStyle(s), cl = getComputedStyle(line);
      return {fsSmall: parseFloat(cs.fontSize), fsLine: parseFloat(cl.fontSize),
              border: parseFloat(cs.borderTopWidth) || 0,
              txt: s.textContent.trim()};
    }""")
    assert not r.get("missing"), "找不到 .chstruct"
    assert r["fsSmall"] < r["fsLine"], r
    assert r["border"] >= 1, "仲裁结论没有分隔线：%s" % r
    assert r["txt"].startswith("日线"), r


def test_t124_macro_row_fits_320(make_env):
    """320（iPhone SE 一代）：宏观定价卡不得横向溢出。

    v7.9 把原来的单行 `.mcrow` 改成两列 `.mgrid`（左实际利率 / 右美元指数），
    所以这里改成量新容器；断言意图不变——容器内不溢出、整页不横滚、两个标签都在。
    尺寸账由本用例守：320 下每列约 130px，18px 数值 + 11.5px 变化仍放得下。
    """
    env = make_env(width=320, height=568, macro=macro_doc())
    page = env.page
    page.wait_for_timeout(800)
    r = page.evaluate("""() => {
      const card = document.querySelector('#macroCard');
      const row = card.querySelector('.mgrid');
      return {rowW: row.clientWidth, rowSW: row.scrollWidth,
              cardW: card.clientWidth, cardSW: card.scrollWidth,
              docSW: document.documentElement.scrollWidth, iw: window.innerWidth,
              txt: row.textContent};
    }""")
    assert r["rowSW"] <= r["rowW"] + 1, "第一行溢出：%s" % r
    assert r["cardSW"] <= r["cardW"] + 1, r
    assert r["docSW"] <= r["iw"] + 1, "页面横向滚动：%s" % r
    assert "实际利率" in r["txt"] and "美元指数" in r["txt"], r["txt"]
