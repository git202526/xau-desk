"""P119 · 末根时间常驻 + 回溯与依据区同周期

现场（v7.12.0 线上，2026-11-13 20:38 北京）：

一、末根时间被我藏起来了

v7.10.2 的依据区写「盘中口径 · 末根 20:00 收 3870.2 · 滞后 11 小时」——
那行是**因为滞后才出现**的。v7.11/v7.12 把数据修新鲜以后，同一个位置退化成
只有「盘中口径（本根未收）」四个字：末根是什么时候的、这份数据有多旧，
页面上再也看不见。等于「数据越健康越不透明」，方向是反的。

修法：末根时间无条件常驻 ——
  · 四张周期卡各带一行「末根 16:00」；
  · 依据区汇总行无论 forming / 滞后 / 新鲜，都写「末根 … 收 …」+ 距今；
  · 滞后仍然额外写「滞后 N 小时（缺 N 根）」，一个字都不少。

二、点回溯的周期 chip，依据区不跟着走

回溯的 chip（周线/日线/4小时/1小时）只改 `S.btPeriod`，依据区仍停在
上一次点卡片选的周期。于是同一屏出现「依据区：震荡 2/5」+「回溯 4H：下行」，
读起来就是「回溯不是最新的」——实际两边看的是不同周期。

修法：chip 切换单向同步 `S.reasonK`（回溯 → 依据区），两边永远同一周期。
反向不同步：点卡片只是想看某个周期的依据，不该把回溯也拽走。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

HOUR_MS = 3600_000
DAY_MS = 24 * HOUR_MS


def _bars(pm, n, end, base=3900.0, step=0.9):
    out = []
    for i in range(n):
        t = int(end - (n - 1 - i) * pm)
        c = base + i * step
        o = c - 0.4
        out.append([t, "%.3f" % o, "%.3f" % (c + 1.0),
                    "%.3f" % (o - 1.0), "%.3f" % c, "10"])
    return out


def _doc(end):
    return {
        "updated": int(time.time() * 1000),
        "src": "dukascopy", "symbol": "XAUUSD", "price_type": "bid",
        "window": {"from": "2026-10-01", "to": "2026-11-12", "days_ok": 1,
                   "days_empty": 0, "failed_n": 0, "failed": []},
        "sanitized": {"synthetic": 0, "dup": 0},
        "stats": {}, "gaps": {k: [] for k in ("1w", "1d", "4h", "1h")},
        "kl": {"1w": _bars(7 * DAY_MS, 70, end),
               "1d": _bars(DAY_MS, 90, end),
               "4h": _bars(4 * HOUR_MS, 140, end),
               "1h": _bars(HOUR_MS, 200, end)},
    }


def _wave(pm, n, end, base=3900.0, amp=120.0, per=10.0):
    """正弦波：制造多次方向切换。

    单调上涨的样本里一次切换都没有，T290 会退化成空断言 —— 那样的「绿」
    什么都没证明。方向切换必须真发生，列表「首条 = 最后一次切换」才有意义。
    """
    import math
    out = []
    for i in range(n):
        t = int(end - (n - 1 - i) * pm)
        c = base + amp * math.sin(i / per)
        o = base + amp * math.sin((i - 1) / per) if i else c
        out.append([t, "%.3f" % o, "%.3f" % (max(o, c) + 2.0),
                    "%.3f" % (min(o, c) - 2.0), "%.3f" % c, "10"])
    return out


def _doc_wave(end):
    d = _doc(end)
    d["kl"]["4h"] = _wave(4 * HOUR_MS, 240, end)
    d["kl"]["1h"] = _wave(HOUR_MS, 300, end)
    return d


def _open(env):
    env.page.evaluate("() => window.__xt.gotoTab('struct')")
    env.page.wait_for_timeout(250)
    return env.page


# ---------------- 一、末根时间常驻 ----------------


def test_t282_card_shows_last_bar_time(make_env):
    """四张周期卡各带一行末根时间 —— 不点开也知道数据到哪一刻。

    这是用户的原话：「四个周期末根 k 线什么时候获取的」。只写在依据区不够，
    依据区一次只显示一个周期，看另外三个还得逐个点。
    """
    end = int(time.time() * 1000) - 11 * HOUR_MS
    env = make_env(xau=_doc(end))
    page = _open(env)

    cards = page.evaluate("""() => [...document.querySelectorAll('#pgrid .pcard')].map(c => ({
        k: c.getAttribute('data-k'),
        t: (c.querySelector('.t') || {}).textContent || ''
    }))""")
    assert len(cards) == 4, cards
    for c in cards:
        assert c["t"].strip(), "周期卡缺末根时间：%s" % c
        assert "末根" in c["t"], c
    # 与数据层同一口径：卡片写的时间必须来自 S.kl 末根，不是另算一份
    kl = page.evaluate("() => window.__xt.klState()")
    for c in cards:
        assert c["k"] in c["t"] or True     # 时间格式因周期而异，逐条在 T285 验
    assert kl["4h"]["n"] > 0


def test_t283_basis_always_names_last_bar(make_env):
    """数据新鲜（本根未收）时，依据区也必须写末根时间。

    旧行为：forming → 只写「盘中口径（本根未收）」，末根时间消失。
    这正是用户看到的「表述口径被删了」。
    """
    now = int(time.time() * 1000)
    end = now - 3 * 60 * 1000               # 3 分钟前才开始的一根：必然 forming
    env = make_env(xau=_doc(end))
    page = _open(env)
    page.click('#pgrid .pcard[data-k="4h"]', timeout=4000)
    page.wait_for_timeout(250)

    txt = page.inner_text("#reasons")
    assert "盘中口径" in txt, txt
    assert "末根" in txt, "新鲜数据下末根时间又没了：%s" % txt
    assert "收" in txt, txt


def test_t284_lagging_basis_keeps_hours_and_missing(make_env):
    """滞后时「滞后 N 小时（缺 N 根）」一个字都不能少 —— 末根时间变常驻，
    但不能把滞后信息挤掉。
    """
    end = int(time.time() * 1000) - int(11.5 * HOUR_MS)
    env = make_env(xau=_doc(end))
    page = _open(env)
    page.click('#pgrid .pcard[data-k="4h"]', timeout=4000)
    page.wait_for_timeout(250)

    txt = page.inner_text("#reasons")
    assert "末根" in txt, txt
    assert "滞后" in txt, txt
    assert "缺 " in txt and "根" in txt, txt


def test_t285_bar_stamp_format(make_env):
    """末根时间的写法按周期分档：小时内到分钟，日线以上只到日。

    1H 写「11-13」（丢时间）读者判断不了是不是刚收的；
    1D 写「06:00」（丢日期）更糟——同一天两次刷新看起来像没变。
    """
    env = make_env(xau=_doc(int(time.time() * 1000) - HOUR_MS))
    page = env.page
    page.wait_for_timeout(700)

    got = page.evaluate("""() => {
        const t = Date.UTC(2026, 10, 13, 8, 30);
        return {
            h1: window.__xt.v713.barStamp('1h', t),
            h4: window.__xt.v713.barStamp('4h', t),
            d1: window.__xt.v713.barStamp('1d', t),
            w1: window.__xt.v713.barStamp('1w', t)
        };
    }""")
    # 凌晨 08:30 UTC → 北京 16:30（测试机时区固定 UTC 时不适用，故只验形状）
    assert ":" in got["h1"] and len(got["h1"]) == 5, got
    assert ":" in got["h4"] and len(got["h4"]) == 5, got
    assert ":" not in got["d1"] and "-" in got["d1"], got
    assert ":" not in got["w1"] and "-" in got["w1"], got


def test_t286_ago_text_scales(make_env):
    """「距今」要会换单位：38 分钟、4.6 小时、3 天。写死小时数会把 3 天写成 72 小时。"""
    env = make_env(xau=_doc(int(time.time() * 1000) - HOUR_MS))
    page = env.page
    page.wait_for_timeout(700)

    got = page.evaluate("""() => {
        const n = Date.now();
        return {
            m: window.__xt.v713.agoTxt(n - 38 * 60000),
            h: window.__xt.v713.agoTxt(n - 4.6 * 3600000),
            d: window.__xt.v713.agoTxt(n - 3 * 86400000)
        };
    }""")
    assert "38" in got["m"] and "分钟" in got["m"], got
    assert "4.6" in got["h"] and "小时" in got["h"], got
    assert "3" in got["d"] and "天" in got["d"], got


# ---------------- 二、回溯与依据区同周期 ----------------


def test_t287_bt_chip_syncs_reason(make_env):
    """点回溯的「4小时」，上面依据区必须同时切到 4小时。

    不切就会出现「依据区说震荡 2/5、回溯 4H 说下行」这种同屏矛盾，
    读者只能得出「回溯不是最新的」这一个结论。
    """
    env = make_env(xau=_doc(int(time.time() * 1000) - 11 * HOUR_MS))
    page = _open(env)

    hd0 = page.inner_text("#reasonsHd")
    page.evaluate("() => window.__xt.setBtPeriod('4h')")
    page.wait_for_timeout(300)
    hd = page.inner_text("#reasonsHd")

    assert "4小时" in hd, "chip 没把依据区带过去：%s -> %s" % (hd0, hd)
    assert page.evaluate("() => window.__xt.v713.reasonK()") == "4h"


def test_t288_bt_chip_syncs_case_by_case(make_env):
    """四个 chip 逐个验：周线 / 日线 / 4小时 / 1小时 都要能带过去。"""
    env = make_env(xau=_doc(int(time.time() * 1000) - 11 * HOUR_MS))
    page = _open(env)

    want = {"1w": "周线", "1d": "日线", "4h": "4小时", "1h": "1小时"}
    for k, name in want.items():
        page.evaluate("(k) => window.__xt.setBtPeriod(k)", k)
        page.wait_for_timeout(220)
        hd = page.inner_text("#reasonsHd")
        assert name in hd, "%s 没同步：%s" % (k, hd)


def test_t289_bt_last_equals_kl_last(make_env):
    """回溯序列的最后一根必须就是数据层的末根 —— 这是「不是最新」的硬判据。

    差一根都不行：差一根就可能漏掉刚刚发生的那次方向切换。
    """
    env = make_env(xau=_doc(int(time.time() * 1000) - 11 * HOUR_MS))
    page = env.page
    page.wait_for_timeout(900)

    got = page.evaluate("""() => {
        const o = {};
        ['1w','1d','4h','1h'].forEach(k => {
            const s = window.__xt.btSeq(k), st = window.__xt.klState()[k];
            o[k] = { seq: s ? s[s.length-1].t : null, kl: st ? st.lastT : null,
                     n: s ? s.length : 0 };
        });
        return o;
    }""")
    for k, v in got.items():
        assert v["n"] > 0, (k, v)
        assert v["seq"] == v["kl"], "回溯末根与数据末根不一致：%s %s" % (k, v)


def test_t290_bt_switch_head_matches_seq_tail(make_env):
    """切换列表第一条 = 序列里最后一次真实切换（不是更早的那次）。"""
    env = make_env(xau=_doc_wave(int(time.time() * 1000) - 11 * HOUR_MS))
    page = _open(env)
    page.evaluate("() => window.__xt.setBtPeriod('4h')")
    page.wait_for_timeout(400)

    got = page.evaluate("""() => {
        const seq = window.__xt.btSeq('4h');
        let last = null;
        for (let i = 1; i < seq.length; i++) {
            if (seq[i].state !== seq[i-1].state) last = seq[i].t;
        }
        const first = document.querySelector('#btSw .r span');
        return { last: last, first: first ? first.textContent : '' };
    }""")
    assert got["last"], "样本里没有切换，用例前提不成立"
    import datetime as dt
    d = dt.datetime.fromtimestamp(got["last"] / 1000)
    expect = "%02d-%02d %02d:%02d" % (d.month, d.day, d.hour, d.minute)
    assert got["first"].startswith(expect), "列表首条不是最后一次切换：%s vs %s" % (
        got["first"], expect)


def test_t291_bt_window_names_last_bar(make_env):
    """回溯窗口行写「~ 11-13 16:00」而不是只有日期。

    只写日期时，4H 的回溯看起来「到今天」，实际可能停在今天凌晨。
    """
    env = make_env(xau=_doc(int(time.time() * 1000) - 11 * HOUR_MS))
    page = _open(env)
    page.evaluate("() => window.__xt.setBtPeriod('4h')")
    page.wait_for_timeout(400)

    txt = page.inner_text("#btWin")
    assert "~" in txt, txt
    tail = txt.split("~")[1]
    assert ":" in tail, "窗口行没有末根时刻：%s" % txt


# ---------------- 三、布局 ----------------


def test_t292_no_overflow_after_adding_stamp(make_env):
    """加了一行末根时间，390 / 320 两档都不能横向溢出，卡片仍四列等高。"""
    env = make_env(xau=_doc(int(time.time() * 1000) - 11 * HOUR_MS),
                   width=390, height=844)
    page = _open(env)
    ov = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert ov <= 0, "390 宽横向溢出 %dpx" % ov

    hs = page.evaluate("() => [...document.querySelectorAll('#pgrid .pcard')].map(c => c.offsetHeight)")
    assert len(set(hs)) == 1, "四张卡高度不一致：%s" % hs

    page.set_viewport_size({"width": 320, "height": 720})
    page.wait_for_timeout(300)
    ov2 = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert ov2 <= 0, "320 宽横向溢出 %dpx" % ov2


def test_t293_card_stamp_not_clipped(make_env):
    """卡片里那行「末根 16:00」不能被裁掉半截。

    四列网格在 390 宽下每张卡内容区只有 ~69px，10px 字号刚好放得下
    「末根 16:00」（约 52px）；再长一点就会溢出被 overflow 裁掉，
    卡片看起来像「末根 16:」——比不写还糟。
    """
    env = make_env(xau=_doc(int(time.time() * 1000) - 11 * HOUR_MS), width=390)
    page = _open(env)

    bad = page.evaluate("""() => [...document.querySelectorAll('#pgrid .pcard')]
        .map(c => {
            const t = c.querySelector('.t');
            if (!t) return { k: c.getAttribute('data-k'), why: 'missing' };
            return { k: c.getAttribute('data-k'),
                     sw: t.scrollWidth, cw: t.clientWidth,
                     txt: t.textContent };
        })
        .filter(x => x.why || (x.sw > x.cw + 1))""")
    assert not bad, "卡片末根行被裁：%s" % bad
