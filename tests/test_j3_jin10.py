"""J3–J6：金十中转（只剩日历）+ K 线降频。

三块分别验证：
  j* 日历三级回落（金十 → ForexFactory → 内置）；v7/v8 验降级提示文案
  k* K 线降频、增量、缺口回退、未收盘本地推进（数据源是 Binance，与金十无关）
  r* 中转脚本自身的自测（真跑 tools/fetch_jin10.py --selftest，不联网）

已下线并删掉用例的两块：快讯 / 资讯（J4）、金十 K 线备源与源校验（J6）。
金十 get_kline 实测只有分钟级 100 根，凑不出 4H / 日线，见 docs/J5-金十MCP实测记录.md。
"""
import json
import os
import subprocess
import sys

import pytest

from conftest import CFG_NORMAL

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def JN_CAL(n=4):
    base = 1762400000000
    return {"updatedAt": "2026-11-04T01:30:00Z", "events": [
        {"t": base + i * 3600000, "ccy": "usd", "name": "事件%d" % i,
         "impact": 4 if i == 0 else 2, "fc": "1.0%", "pv": "0.8%"} for i in range(n)
    ]}


def _jl(p):
    return p.evaluate("() => window.__xt.jnState()")


# ================================================================ 中转缺失

def test_j3_missing_relay_falls_back_to_builtin(make_env):
    """中转没跑起来（全 404）：日历回落内置事件，报价与通道图照常，不报错、不白屏。"""
    e = make_env(cfg=CFG_NORMAL)          # jn=None → 全部 404
    p = e.page
    p.wait_for_timeout(800)
    assert p.inner_text("#pNow").replace(",", "") == "4072.40"
    assert p.evaluate("() => window.__xt.jnState().calSrc") == "none"
    # 校验行已随金十 K 线一起下线，通道卡里不该再有 chVerify
    assert p.query_selector("#chVerify") is None


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


def test_k2_single_package_covers_all_periods(make_env):
    """v7.10.1：一次 refreshData 只发一个 xau 请求，四个周期同源。

    旧版按周期各拉一次（4 个 Binance 请求 + 增量/全量两套判据）；判定链只留
    Dukascopy 之后，整包一次到位，「增量补洞」这套逻辑连同代币源一起下线。
    """
    e = make_env(cfg=CFG_NORMAL)
    p = e.page
    p.wait_for_timeout(600)
    urls = []
    p.on("request", lambda r: urls.append(r.url) if "klines" in r.url else None)
    p.evaluate("() => window.__xt.refreshData(true)")
    p.wait_for_timeout(700)

    assert len(urls) == 1, urls
    assert "xau/klines.json" in urls[0], urls
    st = p.evaluate("() => window.__xt.klState()")
    assert all(st[k]["n"] > 0 for k in ("1w", "1d", "4h", "1h")), st


def _align_bars_now(page, age_ms=5 * 60 * 1000):
    """把四个周期的末根时间对齐到「刚刚」，专供 klStale() 的用例用。

    夹具 _bars() 用 2026-10-29 的固定时间戳（数百条数值断言靠它做 UTC 周分组、
    回溯窗口起止），按真实新鲜度判当然全落后。要验新鲜度判据的用例自己对齐，
    不去动夹具 —— 平移夹具时间戳会连带打翻周枢轴和窗口索引。
    """
    page.evaluate("""(age) => {
        var s = window.__xt.state(), now = Date.now();
        ['1w','1d','4h','1h'].forEach(function(k){
            var b = s.kl && s.kl[k];
            if(b && b.length) b[b.length-1].t = now - age;
        });
    }""", age_ms)


def test_k3_stale_product_keeps_retrying(make_env):
    """产物落后一个周期就要重试，不能只看「上次什么时候拉的」。

    上游当天文件为空时，19:06 拉回来的产物末根还停在 08:00：拉取时间很新、
    数据却停了三小时。旧判据（按拉取时间）会认为"刚拉过不用再拉"，于是一整天
    都不再重试；这里改成按末根时间判。
    """
    e = make_env(cfg=CFG_NORMAL)
    p = e.page
    p.wait_for_timeout(600)
    _align_bars_now(p)

    fresh = p.evaluate("() => window.__xt.klStale()")
    assert fresh is False, "四周期末根都在本周期内，不应判落后"

    p.evaluate("""() => {
        var s = window.__xt.state();
        var last = s.kl['1h'][s.kl['1h'].length-1];
        last.t = last.t - 3 * 3600 * 1000;          // 末根退回 3 小时前
    }""")
    assert p.evaluate("() => window.__xt.klStale()") is True


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


def test_k6_forming_bar_follows_tick(make_env):
    """未收盘的 K 线用现货价本地推进：c 跟着走，h/l 随之扩张。"""
    e = make_env(cfg=CFG_NORMAL)
    p = e.page
    p.evaluate("""() => {
        var s = window.__xt.state();
        var now = Date.now();
        s.kl = { '1h': [ {t: now - 3600000, o: 4000, h: 4010, l: 3995, c: 4002, v: 1},
                         {t: now - 60000,  o: 4002, h: 4004, l: 4001, c: 4003, v: 1} ] };
        s.price = 4030;
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
        s.price = 4030;
    }""")
    p.evaluate("() => window.__xt.patchFormingBar()")
    last = p.evaluate("() => { var b = window.__xt.state().kl['1h']; return b[b.length-1]; }")
    assert last["c"] == 4002, last
    assert last["h"] == 4010, last


def test_v7_cal_fallback_note_ff(make_env):
    """金十日历拿不到、落 FF → 事件雷达底部要写明"已切换到备源"。"""
    ff = {"updatedAt": "2026-11-04T00:00:00Z", "week": "2026-11-02",
          "events": [{"t": 1762400000000 + i * 3600000, "ccy": "USD",
                      "name": "FF事件%d" % i, "impact": 3} for i in range(4)]}
    e = make_env(cfg=CFG_NORMAL, calendar=ff)
    p = e.page
    p.wait_for_function("() => window.__xt.jnState().calSrc === 'ff'", timeout=6000)
    p.evaluate("() => window.__xt.refreshAll()")
    assert "已切换到备源" in p.inner_text("#evNote")


def test_v8_cal_builtin_note(make_env):
    """两级都拿不到 → 写明"已降级为内置事件"，而不是含糊的"不可用"。"""
    e = make_env(cfg=CFG_NORMAL)
    p = e.page
    p.wait_for_function("() => window.__xt.jnState().calSrc === 'none'", timeout=6000)
    p.evaluate("() => window.__xt.refreshAll()")
    assert "已降级为内置事件" in p.inner_text("#evNote")


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


def test_k8_within_period_is_not_stale(make_env):
    """末根还在本周期内就不算落后：1 小时图的末根只要不到 1 小时就不重拉。"""
    e = make_env(cfg=CFG_NORMAL)
    p = e.page
    p.wait_for_timeout(600)
    _align_bars_now(p)

    p.evaluate("""() => {
        var s = window.__xt.state();
        var now = Date.now();
        s.kl['1h'] = [{t: now - 5*60000, o: 4000, h: 4006, l: 3998, c: 4004, v: 1}];
    }""")
    assert p.evaluate("() => window.__xt.klStale()") is False

    p.evaluate("""() => {
        var s = window.__xt.state();
        s.kl['1h'][0].t = Date.now() - 95*60000;   // 跨过一个整点
    }""")
    assert p.evaluate("() => window.__xt.klStale()") is True


def test_j9_calendar_refreshes_periodically(make_env):
    """日历必须按中转节奏重拉，不能只在启动时拉一次。

    中继每 20 分钟出一份新的 calendar.json；页面若只在 boot 时读一次，
    开着过夜就永远停在前一天，而底部文案却写着「每 20 分钟更新」。
    """
    e = make_env(cfg=CFG_NORMAL, jn={"calendar.json": JN_CAL()})
    p = e.page
    p.wait_for_function("() => window.__xt.jnState().calSrc === 'jin10'", timeout=6000)

    assert p.evaluate("() => window.__xt.calRefreshMs()") <= 20 * 60000

    hits = []
    p.on("request", lambda r: hits.append(r.url) if "/jin10/calendar.json" in r.url else None)
    hits.clear()
    p.evaluate("() => window.__xt.refreshCalendar()")
    p.wait_for_timeout(600)
    assert hits, "再次刷新时没有重新请求日历"

    # 内容没变就不该重绘（签名相同）
    sig = p.evaluate("() => window.__xt.calSig()")
    p.evaluate("() => window.__xt.refreshCalendar()")
    p.wait_for_timeout(600)
    assert p.evaluate("() => window.__xt.calSig()") == sig
