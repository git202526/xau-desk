"""P111 · 多周期共振：四周期点击切换依据 + 判定链缺陷修复（T143–T156）。

需求（本轮）：
  1. 四个周期卡可点击，切换下方「判定依据」区显示该周期的 4 维 5 条
  2. 勾选态跟随，数据刷新后保持选择；该周期缺数据时如实说「数据不足」
  3. 顺带修：判定链里三处真实缺陷（veto 后 1h 仍写触发 / 同向分母写死 3 / 数据不足时 meta 不清）
  4. 顺带修：未保存浮条在用户没改过任何参数时也会弹出

先红后绿。
"""


def J(state="up", up=4, dn=1, adx=32, bars=120, prefix=""):
    """构造一份 judgeOne 形状的判定结果，供 __xt.setStruct 注入。

    依据区只读 upC/dnC 的 {k,d,v}；卡片读 state/upN/dnN/adx.adx/bars。
    verdictText 还会读 rsi / ma20 / c，一并给全，避免注入后渲染偶发 TypeError。
    """
    def side(n, kind):
        return [{"dim": "trend", "k": "%s%s条件%d" % (prefix, kind, i + 1),
                 "d": "%s数值%d" % (prefix, i + 1), "v": i < n} for i in range(5)]
    return {
        "state": state, "upN": up, "dnN": dn,
        "upC": side(up, "多"), "dnC": side(dn, "空"),
        "c": 4072.4, "ma20": 4060.0, "ma60": 4020.0,
        "rsi": 58.2, "rsiState": "偏强",
        "macd": {"hist": 1.2, "dif": 2.0, "dea": 0.8},
        "adx": {"adx": adx, "pdi": 28.0, "mdi": 16.0},
        "atr": 22.0, "bars": bars,
    }


def P(**kw):
    """四周期默认同向做多；用 kw 覆盖单个周期，传 None 表示该周期无数据。"""
    base = {
        "1w": J(prefix="[周] ", adx=28, bars=220),
        "1d": J(prefix="[日] ", adx=32, bars=800),
        "4h": J(prefix="[4h] ", adx=27, bars=750),
        "1h": J(prefix="[1h] ", adx=22, bars=1000, up=3),
    }
    base.update(kw)
    return base


def boot(make_env, p=None, **cfgkw):
    from conftest import CFG_EMPTY
    cfg = dict(CFG_EMPTY, H_BASE=4800, alpha=2.44, keepGram=None,
               buys=[{"id": "b1", "name": "建仓", "gram": 60, "priceCny": 800,
                      "time": "2026-08-12T02:00:00.000Z", "note": "", "gear": None},
                     {"id": "b2", "name": "加仓", "gram": 40, "priceCny": 820,
                      "time": "2026-09-12T02:00:00.000Z", "note": "", "gear": None}])
    cfg.update(cfgkw)
    env = make_env(cfg=cfg, price=4072.4)
    page = env.page
    page.wait_for_timeout(500)
    page.evaluate("() => window.__xt.gotoTab('struct')")
    page.wait_for_timeout(200)
    if p is not None:
        page.evaluate("(o) => window.__xt.setStruct(o)", p)
        page.wait_for_timeout(150)
    return env, page


def hd(page):
    return (page.text_content("#reasonsHd") or "").strip()


def reasons(page):
    return page.text_content("#reasons") or ""


def tab(page, k):
    page.click('#pgrid .pcard[data-k="%s"]' % k)
    page.wait_for_timeout(150)


# ---------------------------------------------------------------- T143 可交互
def test_t143_cards_are_buttons(make_env):
    """四张卡必须真的是可交互元素：data-k + role=button + tabindex + aria-pressed。

    只加 onclick 而留 div，键盘用户与读屏器都够不到 —— 卡片本来就画成可点的样子，
    这里补齐语义。分组按 role=group / aria-label 标出，避免四张孤立按钮没有上下文。
    """
    env, page = boot(make_env, P())
    cards = page.evaluate("""() => [...document.querySelectorAll('#pgrid .pcard')].map(c => ({
        k: c.getAttribute('data-k'),
        role: c.getAttribute('role'),
        tab: c.getAttribute('tabindex'),
        pressed: c.getAttribute('aria-pressed')
    }))""")
    assert [c["k"] for c in cards] == ["1w", "1d", "4h", "1h"], cards
    for c in cards:
        assert c["role"] == "button", c
        assert c["tab"] == "0", c
        assert c["pressed"] in ("true", "false"), c
    assert len(page.query_selector_all("#pgrid .pcard")) == 4


# ---------------------------------------------------------------- T144 默认
def test_t144_default_is_daily(make_env):
    env, page = boot(make_env, P())
    assert "日线" in hd(page), hd(page)
    assert "权重 50%" in hd(page), hd(page)
    assert page.evaluate("() => document.querySelector('#pgrid .pcard[data-k=\"1d\"]').classList.contains('on')")
    assert page.evaluate("() => document.querySelector('#pgrid .pcard[data-k=\"1d\"]').getAttribute('aria-pressed')") == "true"
    assert page.evaluate("() => document.querySelector('#pgrid .pcard[data-k=\"1w\"]').getAttribute('aria-pressed')") == "false"


# ---------------------------------------------------------------- T145 点周线
def test_t145_click_weekly_switches_reasons(make_env):
    env, page = boot(make_env, P())
    tab(page, "1w")
    h = hd(page)
    assert "周线" in h and "权重 10%" in h, h
    body = reasons(page)
    assert "[周]" in body, body
    assert "[日]" not in body, "依据区仍混着日线内容：%s" % body
    assert page.evaluate("() => document.querySelector('#pgrid .pcard[data-k=\"1w\"]').classList.contains('on')")
    assert page.evaluate("() => document.querySelector('#pgrid .pcard[data-k=\"1d\"]').classList.contains('on')") is False


# ---------------------------------------------------------------- T146 1 小时
def test_t146_hour_says_not_voting(make_env):
    """1 小时权重 0，依据区标题必须写清楚「不参与方向」，否则读者以为它也在定多空。"""
    env, page = boot(make_env, P())
    tab(page, "1h")
    h = hd(page)
    assert "1小时" in h, h
    assert "不参与方向" in h, h
    assert "触发" in h, h
    assert "[1h]" in reasons(page), reasons(page)


# ---------------------------------------------------------------- T147 键盘
def test_t147_keyboard_enter_switches(make_env):
    env, page = boot(make_env, P())
    page.focus('#pgrid .pcard[data-k="4h"]')
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    h = hd(page)
    assert "4小时" in h and "权重 40%" in h, h
    assert "[4h]" in reasons(page), reasons(page)


# ---------------------------------------------------------------- T148 刷新保持
def test_t148_selection_survives_refresh(make_env):
    """refreshAll 会重算 struct 并重绘 pgrid，选择必须留在用户点的那一格。

    默认回日线也行，但那样用户每 15 秒被踢回去 —— 页面在自动刷新，等于点一次看三秒。
    """
    env, page = boot(make_env, P())
    tab(page, "1w")
    page.evaluate("() => window.__xt.refreshAll()")
    page.wait_for_timeout(600)
    assert page.evaluate("() => document.querySelector('#pgrid .pcard[data-k=\"1w\"]').classList.contains('on')"), \
        "refreshAll 后选择被重置"
    assert "周线" in hd(page), hd(page)


# ---------------------------------------------------------------- T149 缺数据
def test_t149_missing_period_says_so(make_env):
    """点了没有数据的周期，如实说数据不足，且不崩、不清空别的卡。"""
    env, page = boot(make_env, P(**{"4h": None}))
    tab(page, "4h")
    body = reasons(page)
    assert "数据不足" in body, body
    assert "4小时" in hd(page), hd(page)
    assert len(page.query_selector_all("#pgrid .pcard")) == 4
    assert env.errors == [], env.errors


# ---------------------------------------------------------------- T150 veto
def test_t150_veto_hides_1h_trigger(make_env):
    """二对一否决后方向作废，1 小时就不能再写「触发」——那是在给一个已作废的方向做注脚。

    现状：structCore 的 trig 只看日线，不看 dir 是否被 veto 成 rg。
    """
    env, page = boot(make_env, P(**{
        "1w": J("dn", up=1, dn=4, prefix="[周] "),
        "1d": J("up", up=4, dn=1, prefix="[日] "),
        "4h": J("dn", up=1, dn=4, prefix="[4h] "),
        "1h": J("up", up=3, dn=2, prefix="[1h] "),
    }))
    # setStruct 内部已重算 structCore：先确认裁决确实是 veto
    shape = page.evaluate("() => window.__xt.structOf().shape")
    assert shape == "veto", shape
    smalls = page.evaluate("""() => [...document.querySelectorAll('#pgrid .pcard')].map(c => ({
        k: c.getAttribute('data-k'), m: c.querySelector('.m').textContent.trim()}))""")
    h1 = [x for x in smalls if x["k"] == "1h"][0]
    assert h1["m"] == "—", "方向已作废，1 小时仍写：%s" % h1["m"]


# ---------------------------------------------------------------- T151 分母
def test_t151_align_denominator_is_valid_count(make_env):
    """同向分母用实际投票数（valid），不能写死 3。

    周线缺数据时只有日线 + 4h 两票，写「2/3 同向」等于凭空编一个没投票的周期。
    """
    env, page = boot(make_env, P(**{"1w": None}))
    page.evaluate("() => window.__xt.renderVerdict()")
    page.wait_for_timeout(200)
    txt = page.text_content("#vSub") or ""
    assert "2/2 同向" in txt, txt
    assert "2/3" not in txt, txt


# ---------------------------------------------------------------- T152 未保存不误报
def test_t152_dirtybar_hidden_without_edit(make_env):
    """没动过设置页任何输入，浮条就不能出现。

    持仓 100g、keepGram 为空时，底仓显示值是 computedKeep 派生的（30）。
    记一笔卖出后持仓变 80g，派生值变 24，输入框还留着 30 —— 旧逻辑判「用户改了没保存」。
    """
    env, page = boot(make_env, P())
    assert page.is_hidden("#dirtyBar"), "刚进页面就弹未保存"
    page.evaluate("() => window.__xt.gotoTab('pos')")
    page.wait_for_timeout(300)
    page.click("#btnSell")
    page.wait_for_timeout(250)
    page.fill('input[name="g_gram"]', "20")
    page.fill('input[name="g_price"]', "900")
    page.click("#btnSave")
    page.wait_for_timeout(700)
    assert page.is_hidden("#dirtyBar"), "卖出一笔后浮条自己冒出来了"


# ---------------------------------------------------------------- T153 真改才报
def test_t153_dirtybar_only_after_real_edit(make_env):
    """改了没保存、又切走，浮条必须出来兜住。

    留在设置页且保存按钮在视野内时浮条本来就该隐藏（按钮就是入口，不压两条）——
    所以这里先切到总览，模拟「改完忘了保存就走了」。
    """
    env, page = boot(make_env, P())
    page.evaluate("() => window.__xt.gotoTab('set')")
    page.wait_for_timeout(300)
    page.fill("#setTotal", "150")
    page.wait_for_timeout(200)
    page.evaluate("() => window.__xt.gotoTab('ov')")
    page.wait_for_timeout(250)
    assert page.is_visible("#dirtyBar"), "用户改了总仓又切走，浮条没出来"
    assert (page.text_content("#dirtyNum") or "").strip() == "1"
    page.click("#dirtySave")
    page.wait_for_timeout(700)
    assert page.is_hidden("#dirtyBar"), "保存后浮条还在"


# ---------------------------------------------------------------- T154 派生显示
def test_t154_keep_display_follows_position(make_env):
    """底仓显示值必须跟着持仓走：100g 显示 30，卖到 80g 要变 24。

    否则用户看到「底仓保留 30g」，引擎实际按 24g 算 —— 两个数打架。
    """
    env, page = boot(make_env, P())
    before = page.evaluate("() => document.getElementById('setKeep').value")
    assert before == "30", before
    page.evaluate("() => window.__xt.gotoTab('pos')")
    page.wait_for_timeout(300)
    page.click("#btnSell")
    page.wait_for_timeout(250)
    page.fill('input[name="g_gram"]', "20")
    page.fill('input[name="g_price"]', "900")
    page.click("#btnSave")
    page.wait_for_timeout(800)
    after = page.evaluate("() => document.getElementById('setKeep').value")
    assert after == "24", after


# ---------------------------------------------------------------- T155 无脏项计数
def test_t155_dirty_bar_clears_counter(make_env):
    """浮条隐藏后计数归位，下次真改时从 1 开始，不是接着上次的数。"""
    env, page = boot(make_env, P())
    page.evaluate("() => window.__xt.gotoTab('set')")
    page.wait_for_timeout(250)
    page.fill("#setTotal", "150")
    page.wait_for_timeout(150)
    assert (page.text_content("#dirtyNum") or "").strip() == "1"
    page.click("#btnSaveSet")
    page.wait_for_timeout(600)
    page.fill("#setTotal", "180")
    page.wait_for_timeout(150)
    assert (page.text_content("#dirtyNum") or "").strip() == "1"


# ---------------------------------------------------------------- T156 无报错
def test_t156_no_pageerror_after_tabs(make_env):
    env, page = boot(make_env, P())
    for k in ("1w", "4h", "1h", "1d"):
        tab(page, k)
    assert env.errors == [], env.errors
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")


def test_t157_hour_ranging_shows_count(make_env):
    """1 小时自己震荡时，卡片小字给条件数，不写「—」。

    「—」只表示「触发无从谈起」（日线震荡或方向被否决）。它自己震荡时，
    1/2 或 2/2 才是读者要的信息；写「—」等于把这一格的信息量清零。
    """
    env, page = boot(make_env, P(**{"1h": J("rg", up=2, dn=2, prefix="[1h] ")}))
    small = page.evaluate("() => document.querySelector('#pgrid .pcard[data-k=\"1h\"] .m').textContent.trim()")
    assert small == "2/5 · 不投票", small
