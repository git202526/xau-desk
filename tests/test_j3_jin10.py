"""J3：金十中转接入 + 路由 + K 线降频。

三块分别验证：
  j* 金十快讯/资讯/日历（经同源静态文件，浏览器只读）
  k* K 线降频、增量、缺口回退、金十备源、未收盘本地推进
  r* 中转脚本自身的自测（真跑 tools/fetch_jin10.py --selftest，不联网）
"""
import json
import os
import subprocess
import sys

import pytest

from conftest import CFG_NORMAL, PRICE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FLASH = {"updatedAt": "2026-11-04T01:30:00Z", "items": [
    {"id": "f1", "t": 1762300000000, "text": "美联储官员称通胀仍在回落通道", "important": True},
    {"id": "f2", "t": 1762299940000, "text": "COMEX 黄金期货短线上扬 5 美元", "important": False},
    {"id": "f3", "t": 1762299880000, "text": "美元指数跌破 104 关口", "important": False},
]}

NEWS = {"updatedAt": "2026-11-04T01:30:00Z", "items": [
    {"id": "n1", "t": 1762290000000, "title": "央行连续第 12 个月增持黄金",
     "summary": "10 月末黄金储备较上月增加 5 万盎司"},
    {"id": "n2", "t": 1762280000000, "title": "机构：明年金价中枢上移", "summary": ""},
]}


def JN_CAL(n=4):
    base = 1762400000000
    return {"updatedAt": "2026-11-04T01:30:00Z", "events": [
        {"t": base + i * 3600000, "ccy": "usd", "name": "事件%d" % i,
         "impact": 4 if i == 0 else 2, "fc": "1.0%", "pv": "0.8%"} for i in range(n)
    ]}


def _bar(i, t0=1762200000000, step=3600000):
    p = 4000 + i
    return [t0 + i * step, p, p + 6, p - 5, p + 3, 100 + i]


def _bars_desc(n=60, t0=1762200000000, step=3600000):
    return [_bar(i, t0, step) for i in range(n)]


def _jl(p):
    return p.evaluate("() => window.__xt.jnState()")


# ================================================================ 快讯 / 资讯

def test_j1_flash_renders(make_env):
    """中转快讯落盘后，总览的快讯卡按时间倒序显示，重要条目高亮。"""
    e = make_env(cfg=CFG_NORMAL, jn={"flash.json": FLASH})
    p = e.page
    p.wait_for_function("() => window.__xt.jnState().flash > 0", timeout=6000)
    p.evaluate("() => window.__xt.gotoTab('sig')")       # 快讯卡在「事件雷达」页
    p.wait_for_timeout(250)
    txt = p.inner_text("#jnFeed")
    assert "通胀仍在回落通道" in txt
    assert "美元指数跌破 104" in txt
    hot = p.evaluate("""() => [...document.querySelectorAll('#jnFeed .jnrow')]
        .filter(n => n.classList.contains('hot')).length""")
    assert hot == 1
    assert _jl(p)["flash"] == 3


def test_j2_news_tab_switch(make_env):
    """点「资讯」切到资讯列表，标题与摘要都在；切回快讯内容不变。"""
    e = make_env(cfg=CFG_NORMAL, jn={"flash.json": FLASH, "news.json": NEWS})
    p = e.page
    p.wait_for_function("() => window.__xt.jnState().news > 0", timeout=6000)
    p.evaluate("() => window.__xt.gotoTab('sig')")
    p.wait_for_timeout(250)
    p.click('#jnChips button[data-k="news"]')
    p.wait_for_timeout(200)
    txt = p.inner_text("#jnFeed")
    assert "央行连续第 12 个月增持黄金" in txt
    assert "5 万盎司" in txt
    p.click('#jnChips button[data-k="flash"]')
    p.wait_for_timeout(200)
    assert "COMEX" in p.inner_text("#jnFeed")


def test_j3_missing_relay_is_empty_not_broken(make_env):
    """中转没跑起来（全 404）：显示空态文案，不报错、不阻塞其它模块。"""
    e = make_env(cfg=CFG_NORMAL)          # jn=None → 全部 404
    p = e.page
    p.wait_for_timeout(700)
    p.evaluate("() => window.__xt.gotoTab('sig')")
    p.wait_for_timeout(250)
    txt = p.inner_text("#jnFeed")
    assert "暂无快讯" in txt
    # 报价照常
    assert p.inner_text("#pNow").replace(",", "") == "4072.40"


# ================================================================ 日历三级

def test_j4_calendar_from_jin10(make_env):
    """金十日历可用 → 直接用它，src=jin10。"""
    e = make_env(cfg=CFG_NORMAL, jn={"calendar.json": JN_CAL()})
    p = e.page
    p.wait_for_function("() => window.__xt.jnState().calSrc === 'jin10'", timeout=6000)
    assert _jl(p)["calSrc"] == "jin10"


def test_j5_calendar_falls_back_to_ff(make_env):
    """金十没有 → 回落 calendar.json（ForexFactory），src=ff。"""
    ff = {"updatedAt": "2026-11-04T00:00:00Z", "week": "2026-11-02",
          "events": [{"t": 1762400000000 + i * 3600000, "ccy": "USD",
                      "name": "FF事件%d" % i, "impact": 3} for i in range(4)]}
    e = make_env(cfg=CFG_NORMAL, calendar=ff)
    p = e.page
    p.wait_for_function("() => window.__xt.jnState().calSrc === 'ff'", timeout=6000)


def test_j6_calendar_both_fail_uses_builtin(make_env):
    """两级都失败 → 回落内置（FOMC / 非农），src=none，页面不空。"""
    e = make_env(cfg=CFG_NORMAL)
    p = e.page
    p.wait_for_function("() => window.__xt.jnState().calSrc === 'none'", timeout=6000)
    body = p.inner_text("#panel-sig")
    assert ("非农" in body) or ("FOMC" in body) or ("美联储" in body)


# ================================================================ K 线降频

def test_k1_due_rules(make_env):
    """到期判据：周看 UTC 周、日看 UTC 日、4H/1H 看边界与兜底间隔。"""
    e = make_env(cfg=CFG_NORMAL)
    p = e.page
    # 用一个固定时刻做基准：2026-11-04（周三）12:00 UTC
    t0 = 1762344000000
    p.evaluate("""(t) => {
        Object.keys(window.__xt.state().klMeta).forEach(k => {
            window.__xt.state().klMeta[k] = { at: t, lastT: 0, mode: "full" };
        });
    }""", t0)
    # 同一周同一日：都不该刷
    assert p.evaluate("(t) => window.__xt.klDue('1w', t)", t0 + 3600000) is False
    assert p.evaluate("(t) => window.__xt.klDue('1d', t)", t0 + 3600000) is False
    # 跨日 → 日线要刷
    assert p.evaluate("(t) => window.__xt.klDue('1d', t)", t0 + 20 * 3600000) is True
    # 同一周内跨日，周线不刷
    assert p.evaluate("(t) => window.__xt.klDue('1w', t)", t0 + 20 * 3600000) is False
    # 下周一 → 周线要刷
    assert p.evaluate("(t) => window.__xt.klDue('1w', t)", t0 + 5 * 86400000) is True
    # 1H 兜底 30 分钟
    assert p.evaluate("(t) => window.__xt.klDue('1h', t)", t0 + 20 * 60000) is False
    assert p.evaluate("(t) => window.__xt.klDue('1h', t)", t0 + 31 * 60000) is True
    # 4H 兜底 60 分钟
    assert p.evaluate("(t) => window.__xt.klDue('4h', t)", t0 + 50 * 60000) is False
    assert p.evaluate("(t) => window.__xt.klDue('4h', t)", t0 + 61 * 60000) is True


def test_k2_increment_pulls_only_tail(make_env):
    """首次全量（limit=1000），到期后只拉 5 根增量，不重拉 1000 根。"""
    e = make_env(cfg=CFG_NORMAL)
    p = e.page
    urls = []
    p.on("request", lambda r: urls.append(r.url) if "klines" in r.url else None)
    # 人为把 1h 标成 2 小时前抓的，触发到期
    p.evaluate("""() => {
        var m = window.__xt.state().klMeta['1h'];
        m.at = Date.now() - 2*3600*1000;
    }""")
    urls.clear()
    p.evaluate("() => window.__xt.refreshData()")
    p.wait_for_timeout(600)
    one_h = [u for u in urls if "interval=1h" in u]
    assert one_h, "没有发起 1h 请求"
    assert "limit=5" in one_h[0], one_h[0]
    # 4 个周期里只有到期的才发，不该 4 个全发
    assert len(urls) <= 2, urls


def test_k3_gap_falls_back_to_full(make_env):
    """缺口超过 5 个周期（几天没开页面）→ 退回全量，增量接不上。"""
    e = make_env(cfg=CFG_NORMAL)
    p = e.page

    # 增量请求（limit<=5）返回"最近 5 根"，与本地最后一根（10 天前）之间留着大缺口；
    # 全量请求（limit 大）返回 60 根。这样才能真正走到缺口判定分支。
    def kline(route):
        url = route.request.url
        lim = 5
        if "limit=" in url:
            try:
                lim = int(url.split("limit=")[1].split("&")[0])
            except Exception:
                lim = 5
        now = 1762400000000
        n = 5 if lim <= 5 else 60
        step = 3600000
        t0 = now - n * step
        body = [[t0 + i * step, 4000 + i, 4010 + i, 3990 + i, 4005 + i, 10] for i in range(n)]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    p.route("**/data-api.binance.vision/**", kline)
    urls = []
    p.on("request", lambda r: urls.append(r.url) if "interval=1h" in r.url else None)
    # 把 1h 的最后一根挪到 10 天前，制造大缺口
    # 本地最后一根固定在 1761741000000（远早于 mock 的 1762382000000），确保缺口成立
    p.evaluate("""() => {
        var s = window.__xt.state();
        var bars = [];
        for (var i = 0; i < 40; i++) {
            var t = 1761600000000 + i*3600000;
            bars.push({t: t, o: 4000+i, h: 4010+i, l: 3990+i, c: 4005+i, v: 10});
        }
        s.kl['1h'] = bars;
        s.klMeta['1h'] = { at: Date.now() - 2*3600*1000,
                           lastT: bars[bars.length-1].t, mode: 'full' };
    }""")
    urls.clear()
    p.evaluate("() => window.__xt.refreshData()")
    p.wait_for_timeout(900)
    one_h = [u for u in urls if "interval=1h" in u]
    assert one_h, "没有发起 1h 请求"
    assert any("limit=1000" in u for u in one_h), one_h


def test_k4_skip_makes_no_request(make_env):
    """5 分钟内重复 refreshData：全部未到期，一个 K 线请求都不发。"""
    e = make_env(cfg=CFG_NORMAL)
    p = e.page
    p.wait_for_timeout(600)
    urls = []
    p.on("request", lambda r: urls.append(r.url) if "klines" in r.url else None)
    p.evaluate("() => window.__xt.refreshData()")
    p.wait_for_timeout(400)
    p.evaluate("() => window.__xt.refreshData()")
    p.wait_for_timeout(400)
    assert urls == [], urls


def test_k5_jin10_kline_fallback(make_env):
    """Binance 全挂且本地没旧数据 → 用中转的 kline_1h.json 顶上。"""
    bars = [{"t": 1762200000000 + i * 3600000, "o": 4000 + i, "h": 4010 + i,
             "l": 3990 + i, "c": 4005 + i, "v": 10} for i in range(40)]
    e = make_env(cfg=CFG_NORMAL, jn={"kline_1h.json": {"bars": bars}})
    p = e.page
    p.route("**/data-api.binance.vision/**", lambda r: r.fulfill(status=500, body="{}"))
    p.evaluate("""() => {
        var s = window.__xt.state();
        s.kl = {}; s.klMeta = {}; s.scale = 1;
    }""")
    p.evaluate("() => window.__xt.refreshData(true)")
    p.wait_for_timeout(900)
    st = p.evaluate("() => window.__xt.klState()")
    assert st["1h"]["n"] == 40, st
    assert st["1h"]["mode"] == "jn", st


def test_k6_forming_bar_follows_tick(make_env):
    """未收盘的 K 线用 PAXG 逐笔本地推进：c 跟着走，h/l 随之扩张。"""
    e = make_env(cfg=CFG_NORMAL)
    p = e.page
    p.evaluate("""() => {
        var s = window.__xt.state();
        var now = Date.now();
        s.kl = { '1h': [ {t: now - 3600000, o: 4000, h: 4010, l: 3995, c: 4002, v: 1},
                         {t: now - 60000,  o: 4002, h: 4004, l: 4001, c: 4003, v: 1} ] };
        s.paxg = 4030;
    }""")
    p.evaluate("() => window.__xt.patchFormingBar()")
    last = p.evaluate("() => { var b = window.__xt.state().kl['1h']; return b[b.length-1]; }")
    assert last["c"] == 4030
    assert last["h"] == 4030          # 突破原高点
    assert last["l"] == 4001          # 低点不动


def test_k7_closed_bar_not_patched(make_env):
    """已收盘的 K 线不被本地改 —— 否则会污染指标，且下次增量也覆盖不回来。"""
    e = make_env(cfg=CFG_NORMAL)
    p = e.page
    p.evaluate("""() => {
        var s = window.__xt.state();
        var now = Date.now();
        s.kl = { '1h': [ {t: now - 3*3600000, o: 4000, h: 4010, l: 3995, c: 4002, v: 1} ] };
        s.paxg = 4030;
    }""")
    p.evaluate("() => window.__xt.patchFormingBar()")
    last = p.evaluate("() => { var b = window.__xt.state().kl['1h']; return b[b.length-1]; }")
    assert last["c"] == 4002, last
    assert last["h"] == 4010, last


# ================================================================ 中转脚本自测

def test_r1_relay_selftest():
    """真跑中转脚本的本地自测：字段解析 / cursor 翻页 / 软上限 / TTL / 额度用完。

    这条不联网，用假 transport 顶上，所以没有金十凭据也能跑。
    """
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "fetch_jin10.py"), "--selftest"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "FAIL" not in r.stdout, r.stdout
    assert "全部通过" in r.stdout


def test_r2_relay_no_token_skips():
    """没有 JIN10_TOKEN 时直接跳过并以 2 退出，绝不发起请求。"""
    env = {k: v for k, v in os.environ.items() if k != "JIN10_TOKEN"}
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "fetch_jin10.py")],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "跳过" in r.stderr
