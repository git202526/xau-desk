"""v7.10.1 P115：现价进判定 + 触发解耦 + 去 PAXG 兜底（T220–T229）。

三条要守住的口径：
  1. 判定的「价格」一律用现货价，末根停在哪都不能拿旧收盘冒充现价；
  2. 末根推进不再依赖 PAXG 逐笔（PAXG 断开时现货刷新必须自己推）；
  3. 数据源只剩 Dukascopy 一份：产物不可用就停算写「数据延迟」，不许静默退回代币。

其余四条判据（斜率/摆点/MACD/ADX）仍按 K 线末根算，现价只替换「价格 vs MA20/60」。
"""
import time

HOUR = 3600_000
DAY = 24 * HOUR
PRICE = 4128.40


def bars(period_ms, n, end, base=3900.0, step=0.5, dip=0):
    """连续序列：稳步上行；dip>0 时最后 dip 根平滑回落（每根 −1.5）。

    回落要平滑，不能把几根的 O/C 压成同一个值——那会造出一段零波动的平台，
    ADX 直接飙到 70、摆点全部消失，测的就不是「价格条件」而是畸形样本。
    """
    end = end - end % period_ms              # 对齐到周期边界
    closes = [base + i * step + 0.6 for i in range(n)]
    if dip:
        top = closes[n - dip - 1]
        for j in range(dip):
            closes[n - dip + j] = top - 1.5 * (j + 1)
    out = []
    for i in range(n):
        t = end - (n - 1 - i) * period_ms
        o = closes[i - 1] if i else closes[0] - 0.5
        c = closes[i]
        out.append([t, "%.3f" % o, "%.3f" % (max(o, c) + 1.2),
                    "%.3f" % (min(o, c) - 1.2), "%.3f" % c, "10"])
    return out


def xau_doc(**periods):
    kl = {k: v for k, v in periods.items() if v}
    return {
        "updated": int(time.time() * 1000),
        "src": "dukascopy", "symbol": "XAUUSD", "price_type": "bid",
        "window": {"from": "2026-06-12", "to": "2026-11-09", "days_ok": 1,
                   "days_empty": 0, "failed_n": 0, "failed": []},
        "sanitized": {"synthetic": 0, "dup": 0},
        "stats": {}, "gaps": {k: [] for k in kl}, "kl": kl,
    }


def now_ms():
    return int(time.time() * 1000)


def judge_row(page, keyword):
    """取依据区里某一条判据的 ✓/✗ 与整行文字。"""
    return page.evaluate("""(kw) => {
        const rows = [...document.querySelectorAll('#reasons .rz')];
        const r = rows.find(x => x.querySelector('.c') && x.querySelector('.c').textContent.includes(kw));
        if(!r) return null;
        const ok = r.querySelector('.ok');
        return { mark:(ok ? ok.textContent : '').trim(), txt:r.textContent };
    }""", keyword)


def open_period(page, k):
    page.evaluate("() => window.__xt.gotoTab('struct')")
    page.wait_for_timeout(200)
    page.click('#pgrid .pcard[data-k="%s"]' % k, timeout=4000)
    page.wait_for_timeout(250)


# ------------------------------------------------------------------ T220
def test_t220_price_condition_uses_live_price(make_env):
    """末根停在 14 小时前、收盘在 MA20 下方：价格条件必须按现价判 ✓。"""
    end = now_ms() - 14 * HOUR
    arr = bars(4 * HOUR, 200, end, base=3900, dip=6)
    env = make_env(price=PRICE, xau=xau_doc(**{"4h": arr}))
    page = env.page
    page.wait_for_timeout(900)

    r = page.evaluate("""(px) => {
        const n = window.__xt.klState()['4h'].n;
        const off = window.__xt.judgeAt('4h', n - 1);
        const on  = window.__xt.judgeAt('4h', n - 1, null, px);
        return {off:off.up[0], on:on.up[0], onTxt:on.upD[0], offTxt:off.upD[0]};
    }""", PRICE)
    assert r["off"] is False, r          # 旧收盘在 MA20 下方
    assert r["on"] is True, r            # 现价 4128 在上方
    assert "4128" in r["onTxt"], r

    open_period(page, "4h")
    # 依据区只列一侧：价格在 MA20 上方时，若列的是「跌破」侧就必须打 ✗，
    # 列的是「站上」侧就必须打 ✓——两种都算过，关键是这一行必须用现价。
    row = judge_row(page, "MA20 且 MA20 在 MA60")
    assert row, page.inner_text("#reasons")
    assert "4128" in row["txt"], row
    assert row["mark"] == ("✗" if "跌破" in row["txt"] else "✓"), row


def test_t222_live_price_only_touches_the_price_rule(make_env):
    """现价只替换价格条件：MA20 / ATR / ADX / 摆点 / MACD 一个都不许变。"""
    end = now_ms() - 14 * HOUR
    arr = bars(4 * HOUR, 200, end, base=3900, dip=6)
    env = make_env(price=PRICE, xau=xau_doc(**{"4h": arr}))
    page = env.page
    page.wait_for_timeout(800)

    r = page.evaluate("""(px) => {
        const n = window.__xt.klState()['4h'].n;
        const off = window.__xt.judgeAt('4h', n - 1);
        const on  = window.__xt.judgeAt('4h', n - 1, null, px);
        return {ma20:[on.ma20, off.ma20], atr:[on.atr, off.atr],
                adx:[on.adx, off.adx], up:[on.up, off.up], dn:[on.dn, off.dn]};
    }""", PRICE)
    assert r["ma20"][0] == r["ma20"][1], r
    assert r["atr"][0] == r["atr"][1], r
    assert r["adx"][0] == r["adx"][1], r
    # 第 1 条（价格位置）允许不同，第 2–5 条必须一致；反向侧同理
    assert r["up"][0][1:] == r["up"][1][1:], r
    assert r["dn"][0][1:] == r["dn"][1][1:], r
    assert r["up"][0][0] != r["up"][1][0], r


# ------------------------------------------------------------------ T221
def test_t221_forming_bar_advances_without_paxg(make_env):
    """PAXG 逐笔彻底断开（500 + 退避）：现货刷新仍要把未收盘根推到现价。"""
    # 取本小时起点，不用 now−5 分钟：整点后 5 分钟内 now−5min 会落回上一小时，
    # bars() 对齐后 since 超 60 分钟，被判「已收盘」而不推进（实测 05:00 跑必红）。
    end = now_ms() - now_ms() % HOUR
    arr = bars(HOUR, 120, end, base=4000)
    env = make_env(price=PRICE, xau=xau_doc(**{"1h": arr}), tick="down")
    page = env.page
    page.wait_for_timeout(800)
    page.evaluate("() => window.__xt.refreshPrice(true)")
    page.wait_for_timeout(1000)

    last = page.evaluate("() => { const b = window.__xt.scaled('1h'); return b[b.length-1]; }")
    assert abs(last["c"] - PRICE) < 0.02, last
    assert last["h"] >= PRICE - 0.01, last


# ------------------------------------------------------------------ T223
def test_t223_weekly_forming_bar_advances(make_env):
    """周线也在推进名单里：本周那根必须跟现价，不能永远停在产物末根。"""
    # 取「本周期起点」而不是 now−2 天再对齐：bars() 的对齐基准是 1970-01-01（周四），
    # 在周四/周五跑时 now−2 天会落到上一周期，since 超 7 天被判已收盘，
    # 这条用例会在某些星期几恒红（与 t224b 同一类时段依赖）。
    end = now_ms() - now_ms() % (7 * DAY)
    arr = bars(7 * DAY, 90, end, base=3900)
    env = make_env(price=PRICE, xau=xau_doc(**{"1w": arr}))
    page = env.page
    page.wait_for_timeout(800)
    page.evaluate("() => window.__xt.refreshPrice(true)")
    page.wait_for_timeout(250)

    # 读之前再 patch 一次：xdist 并行时 refreshData 可能刚把 S.kl['1w'] 换成新数组，
    # 原地 patch 的修改会随之丢失（实测只在 -n 8 下偶发）。这条用例要验的是
    # 「周线在推进名单里」，不是异步时序——时序由 T221 覆盖。
    last = page.evaluate("""() => {
        window.__xt.patchFormingBar();
        const b = window.__xt.scaled('1w');
        return b[b.length - 1];
    }""")
    assert abs(last["c"] - PRICE) < 0.02, last


# ------------------------------------------------------------------ T224
def test_t224_stale_marker_is_honest(make_env):
    """末根已收盘且滞后：卡片标「滞后」，依据行写「末根 … 收 …」，不许再叫现价。"""
    end = now_ms() - 14 * HOUR
    arr = bars(4 * HOUR, 200, end, base=3900, dip=6)
    env = make_env(price=PRICE, xau=xau_doc(**{"4h": arr}))
    page = env.page
    page.wait_for_timeout(800)
    open_period(page, "4h")

    k = page.evaluate("() => document.querySelector('#pgrid .pcard[data-k=\"4h\"] .k').textContent")
    assert "滞后" in k, k
    row = judge_row(page, "MA20 且 MA20 在 MA60")
    assert row, page.inner_text("#reasons")
    assert "末根" in row["txt"] and "收" in row["txt"], row
    assert "4128" in row["txt"], row


def test_t224b_forming_marker_says_intraday(make_env):
    """末根还在本周期内：标「盘中」。

    末根取「本周期起点」，不用 now−6h 再对齐：bars() 会把时间戳对齐到周期边界，
    now−6h 在 UTC 00:00–06:00 这段会对齐到**昨天** 00:00，`since` 超过 24h，
    角标就从「盘中」变「滞后」——时段依赖的假红（实测北京 08:40 必红），
    不是口径问题。取本周期起点则恒在窗内。
    """
    end = now_ms() - now_ms() % DAY
    arr = bars(DAY, 120, end, base=4000)
    env = make_env(price=PRICE, xau=xau_doc(**{"1d": arr}))
    page = env.page
    page.wait_for_timeout(800)
    open_period(page, "1d")

    k = page.evaluate("() => document.querySelector('#pgrid .pcard[data-k=\"1d\"] .k').textContent")
    assert "盘中" in k, k


# ------------------------------------------------------------------ T225
def test_t225_closed_basis_line_exists(make_env):
    """依据区必须同时给出「收盘口径」的条数，读者才知道盘中翻没翻。"""
    end = now_ms() - 14 * HOUR
    arr = bars(4 * HOUR, 200, end, base=3900, dip=6)
    env = make_env(price=PRICE, xau=xau_doc(**{"4h": arr}))
    page = env.page
    page.wait_for_timeout(800)
    open_period(page, "4h")

    txt = page.inner_text("#reasons")
    assert "收盘口径" in txt, txt


# ------------------------------------------------------------------ T226
def test_t226_backtest_ignores_live_price(make_env):
    """回溯是历史口径：现价变了，btSeq 一个字节都不许动。"""
    end = now_ms() - 14 * HOUR
    arr = bars(DAY, 200, end, base=3900)
    env = make_env(price=PRICE, xau=xau_doc(**{"1d": arr}))
    page = env.page
    page.wait_for_timeout(800)

    a = page.evaluate("() => JSON.stringify(window.__xt.btSeq('1d'))")
    page.evaluate("() => window.__xt.patchState({price: 3500})")
    page.evaluate("() => window.__xt.compute()")
    page.wait_for_timeout(200)
    b = page.evaluate("() => JSON.stringify(window.__xt.btSeq('1d'))")
    assert a == b


# ------------------------------------------------------------------ T227
def test_t227_no_paxg_fallback_when_xau_missing(make_env):
    """产物 404：四周期停算 + 写「数据延迟」，不许静默退回 PAXG 冒充行情。"""
    env = make_env(xau=None)
    page = env.page
    page.wait_for_timeout(1000)

    note = page.inner_text("#klSrcNote")
    assert "数据" in note and "延迟" in note, note
    p = page.evaluate("() => window.__xt.structOf()")
    assert p is None or all(p["p"][k] is None for k in ("1w", "1d", "4h", "1h")), p
    body = page.inner_text("#pgrid")
    assert "数据不足" in body, body


# ------------------------------------------------------------------ T228
def test_t228_no_binance_kline_requests(make_env):
    """判定链的数据源只剩 xau/klines.json：一个 Binance K 线请求都不许发。"""
    env = make_env(price=PRICE, xau=xau_doc(**{"1h": bars(HOUR, 120, now_ms() - 5 * 60 * 1000)}))
    page = env.page
    urls = []
    page.on("request", lambda r: urls.append(r.url) if "/api/v3/klines" in r.url else None)
    page.wait_for_timeout(600)
    page.evaluate("() => window.__xt.refreshData()")
    page.wait_for_timeout(900)
    assert urls == [], urls


# ------------------------------------------------------------------ T229
def test_t229_layout_still_fits(make_env):
    """加了「盘中/滞后」小标后，390px 不许横向溢出，一屏预算仍 ≤ 860。"""
    end = now_ms() - 14 * HOUR
    arr = bars(4 * HOUR, 200, end, base=3900, dip=6)
    env = make_env(price=PRICE, xau=xau_doc(**{"4h": arr, "1d": bars(DAY, 200, end, base=3900)}))
    page = env.page
    page.wait_for_timeout(800)
    page.evaluate("() => window.__xt.gotoTab('struct')")
    page.wait_for_timeout(300)

    sw, cw = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert sw <= cw + 1, (sw, cw)
    h = page.eval_on_selector("#panel-ov", "e => e.getBoundingClientRect().height")
    assert h <= 860, "总览高度 %s，超出一屏" % h
