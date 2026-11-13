"""P118 · 页面直连补数据（Twelve Data）+ 产物落后时主动触发 Actions

问题现场（v7.11.0 上线十小时后，2026-11-13 13:00 北京）：
  多周期卡再次出现「滞后 11 小时」。这一次不是管线算错，而是**没人跑**——
  GitHub 对公共仓库的 schedule 触发有大量丢弃，实测间隔 5.8/8.8/7.2/6.5/7.8 小时。
  长任务循环（10 轮 × 30 分钟）只能覆盖 5 小时，空窗期产物就停在那里。

管线侧已经到极限（只能靠外部定时器）。页面这一层能做两件事，都不改判定链：

  1. **直连补数据**：产物落后时，直接从 Twelve Data 拿 1h / 4h / 1d，
     只填不覆盖地并进 S.kl。页面开着，数据就是新的。
  2. **主动催一次**：产物落后超过 90 分钟，用窄权限 token 触发一次
     workflow_dispatch，让后台把历史补回来（tick 回看 24 小时，能把空窗整段填上）。

两条都是可选开关：`TD.key` / `HOOK.token` 为空时整块不执行，行为与 v7.11 一致。
key 走 URL 参数注入（`?tdk=` / `?hkt=`）——测试与线上临时验证都靠它，不改文件。
"""
import time

import pytest

HOUR_MS = 3600 * 1000


def _bars(step, n, end):
    """从 end 往前 n 根，收盘价 3900 起。时间戳为毫秒。"""
    out = []
    for i in range(n):
        t = end - (n - 1 - i) * step
        out.append([t, 3890.0 + i * 0.5, 3892.0 + i * 0.5,
                    3888.0 + i * 0.5, 3891.0 + i * 0.5, 100])
    return out


def _doc(end):
    return {
        "updated": int(time.time() * 1000),
        "src": "dukascopy", "symbol": "XAUUSD", "price_type": "bid",
        "window": {"from": "2026-10-01", "to": "2026-11-13", "days_ok": 1,
                   "days_empty": 0, "failed_n": 0, "failed": []},
        "sanitized": {"synthetic": 0, "dup": 0},
        "stats": {}, "gaps": {k: [] for k in ("1w", "1d", "4h", "1h")},
        "kl": {"1w": _bars(7 * 24 * HOUR_MS, 70, end),
               "1d": _bars(24 * HOUR_MS, 90, end),
               "4h": _bars(4 * HOUR_MS, 140, end),
               "1h": _bars(HOUR_MS, 200, end)},
    }


def _iso(ms):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ms / 1000))


def _td(step, n, end, base=3900.0):
    """Twelve Data time_series 的响应形态：values 倒序（最新在前），全是字符串。"""
    vals = []
    for i in range(n):
        t = end - i * step
        px = base + (n - 1 - i) * 0.5
        vals.append({"datetime": _iso(t), "open": "%.3f" % px,
                     "high": "%.3f" % (px + 2), "low": "%.3f" % (px - 2),
                     "close": "%.3f" % (px + 1), "volume": "10"})
    return {"meta": {"symbol": "XAU/USD", "interval": "1h"}, "values": vals,
            "status": "ok"}


# ---------------- 纯函数：解析 ----------------

def test_t266_td_parse_maps_and_sorts(make_env):
    """TD 的 values 是最新在前、全字符串。解析后必须升序、字段进对象形态。

    顺序错了的后果不是「图有点怪」，而是 MA20 取错一端、缺口判定全反。
    """
    now = int(time.time() * 1000) - int(time.time() * 1000) % HOUR_MS
    env = make_env(path="/index.html?xt=1&tdk=K")
    bars = env.page.evaluate("""(j) => window.__xt.v712.tdParse(j, '1h')""",
                             _td(HOUR_MS, 5, now))
    assert len(bars) == 5, bars
    ts = [b["t"] for b in bars]
    assert ts == sorted(ts), ts
    assert ts[-1] == now, (ts[-1], now)
    b = bars[-1]
    assert abs(b["c"] - (3900.0 + 4 * 0.5 + 1)) < 0.01, b
    assert b["h"] > b["c"] > b["l"], b


def test_t267_td_parse_skips_blank_rows(make_env):
    """每日停盘那一小时 TD 会回空字符串。当日志用 0 填，会把均线拽下去。"""
    env = make_env(path="/index.html?xt=1&tdk=K")
    now = int(time.time() * 1000) - int(time.time() * 1000) % HOUR_MS
    js = _td(HOUR_MS, 3, now)
    js["values"].insert(1, {"datetime": _iso(now - HOUR_MS),
                            "open": "", "high": "", "low": "", "close": ""})
    bars = env.page.evaluate("(j) => window.__xt.v712.tdParse(j, '1h')", js)
    assert len(bars) == 3, bars


def test_t268_td_parse_rejects_error_body(make_env):
    """401/429 的响应体没有 values。返回空数组，绝不能当成「0 根正常数据」。"""
    env = make_env(path="/index.html?xt=1&tdk=K")
    assert env.page.evaluate(
        "() => window.__xt.v712.tdParse({code:401,status:'error',message:'x'}, '1h')") == []


# ---------------- 纯函数：合并 ----------------

def test_t269_td_merge_never_overwrites(make_env):
    """重叠的时间戳一律保留产物原值。

    TD 是另一路报价（现货 vs 期货、mid vs bid），同一根根价差能到 0.3%。
    覆盖会让接缝处出现台阶，MA/ATR 跟着跳——「只填不覆盖」是这条链的底线。
    """
    env = make_env(path="/index.html?xt=1&tdk=K")
    page = env.page
    page.evaluate("""() => {
        window.__xt.v712.tdSeed('1h', [
          {t: 1000, o: 1, h: 2, l: 0.5, c: 1.5, v: 1},
          {t: 2000, o: 1, h: 2, l: 0.5, c: 1.6, v: 1}
        ]);
    }""")
    added = page.evaluate("""() => window.__xt.v712.tdMerge('1h', [
        {t: 2000, o: 9, h: 9, l: 9, c: 9, v: 9},
        {t: 3000, o: 3, h: 4, l: 2, c: 3.5, v: 1}
    ])""")
    assert added == 1, added
    got = page.evaluate("() => window.__xt.scaled('1h')")
    assert [b["t"] for b in got] == [1000, 2000, 3000], got
    assert got[1]["c"] == 1.6, "重叠根被 TD 覆盖了"
    assert got[2]["c"] == 3.5


def test_t270_td_merge_fills_holes(make_env):
    """中间的洞也要填，不只是追加尾巴 —— 上一版 tick 就是只补尾巴才漏掉 5 小时洞。"""
    env = make_env(path="/index.html?xt=1&tdk=K")
    page = env.page
    page.evaluate("""() => {
        window.__xt.v712.tdSeed('1h', [
          {t: 1000, o: 1, h: 2, l: 0.5, c: 1.5, v: 1},
          {t: 5000, o: 1, h: 2, l: 0.5, c: 1.5, v: 1}
        ]);
    }""")
    added = page.evaluate("""() => window.__xt.v712.tdMerge('1h', [
        {t: 2000, o: 2, h: 2, l: 2, c: 2, v: 1},
        {t: 3000, o: 3, h: 3, l: 3, c: 3, v: 1}
    ])""")
    assert added == 2
    assert [b["t"] for b in page.evaluate("() => window.__xt.scaled('1h')")] == \
           [1000, 2000, 3000, 5000]


# ---------------- 纯函数：节流判据 ----------------

def test_t271_td_due_false_when_fresh(make_env):
    """产物新鲜时一个请求都不发。免费额度 800/天，不能拿它当轮询用。"""
    env = make_env(path="/index.html?xt=1&tdk=K")
    now = int(time.time() * 1000)
    assert env.page.evaluate(
        "(n) => window.__xt.v712.tdDue(n, 0, n - 1200000)", (now,)) is False
    # 冷却期：刚从 TD 拉过（5 分钟前），就算产物还是旧的也不再打
    assert env.page.evaluate(
        "(n) => window.__xt.v712.tdDue(n, n - 5 * 60000, n - 11 * 3600000)", (now,)) is False


def test_t272_td_due_true_when_stale(make_env):
    env = make_env(path="/index.html?xt=1&tdk=K")
    now = int(time.time() * 1000)
    assert env.page.evaluate(
        "(n) => window.__xt.v712.tdDue(n, 0, n - 11 * 3600000)", (now,)) is True


def test_t273_td_due_false_without_key(make_env):
    """没填 key 就别发请求（401 也是请求，会占额度也会留 4xx 垃圾日志）。"""
    env = make_env(path="/index.html?xt=1")
    now = int(time.time() * 1000)
    assert env.page.evaluate(
        "(n) => window.__xt.v712.tdDue(n, 0, n - 11 * 3600000)", (now,)) is False


def test_t274_hook_should_needs_90min_and_cooldown(make_env):
    """两个条件：产物落后 ≥ 90 分钟，且距上次触发 ≥ 30 分钟。

    少了冷却，每个标签页、每次刷新都会戳一次 Actions —— 排队十几轮，
    产物反而更晚写出来。
    """
    env = make_env(path="/index.html?xt=1&hkt=T")
    now = int(time.time() * 1000)
    q = "(a) => window.__xt.v712.hookOk(a[0], a[1], a[2], a[3])"
    assert env.page.evaluate(q, (now, 30 * 60000, 0, True)) is False, "落后 30 分钟不催"
    assert env.page.evaluate(q, (now, 200 * 60000, 0, True)) is True
    assert env.page.evaluate(q, (now, 200 * 60000, now - 5 * 60000, True)) is False, "冷却内"
    assert env.page.evaluate(q, (now, 200 * 60000, now - 40 * 60000, True)) is True
    assert env.page.evaluate(q, (now, 200 * 60000, 0, False)) is False, "无 token 不催"


# ---------------- 页面级：真的把滞后压下去 ----------------

def test_t275_page_td_fills_stale_product(make_env):
    """产物末根 11 小时前 + TD 给到当前小时 → 1H 不再「滞后」。

    这条用例是整件事的目的：用户看到的是「滞后 11 小时」，修完要看到 0 小时。
    """
    now = int(time.time() * 1000)
    end = now - now % HOUR_MS - 11 * HOUR_MS
    env = make_env(xau=_doc(end), path="/index.html?xt=1&tdk=K",
                   td={"1h": _td(HOUR_MS, 30, now - now % HOUR_MS),
                       "4h": _td(4 * HOUR_MS, 20, now - now % (4 * HOUR_MS)),
                       "1d": _td(24 * HOUR_MS, 20, now - now % (24 * HOUR_MS))})
    page = env.page
    page.wait_for_timeout(1500)

    info = page.evaluate("() => window.__xt.klLagInfo()")
    assert info["lagH"] < 1.5, info
    assert info["missingN"] == 0, info
    st = page.evaluate("() => window.__xt.klState()")
    assert abs(st["1h"]["lastT"] - (now - now % HOUR_MS)) <= HOUR_MS, st
    assert page.evaluate("() => window.__xt.v712.tdState()")["ok"] >= 1
    assert not env.errors, env.errors


def test_t276_page_td_failure_keeps_product(make_env):
    """TD 挂了（401/429）不能把页面搞坏：保留产物、照旧写滞后、零报错。"""
    now = int(time.time() * 1000)
    end = now - now % HOUR_MS - 11 * HOUR_MS
    env = make_env(xau=_doc(end), path="/index.html?xt=1&tdk=BAD",
                   td={"error": {"code": 401, "status": "error", "message": "bad key"}})
    page = env.page
    page.wait_for_timeout(1500)

    assert page.evaluate("() => window.__xt.klLagInfo()")["lagH"] > 10
    assert page.evaluate("() => window.__xt.v712.tdState()")["ok"] == 0
    assert not env.errors, env.errors


def test_t277_page_td_no_key_no_request(make_env):
    """没填 key：连一次请求都不许发（默认部署就是这个状态）。"""
    now = int(time.time() * 1000)
    end = now - now % HOUR_MS - 11 * HOUR_MS
    env = make_env(xau=_doc(end), path="/index.html?xt=1", td={"1h": _td(HOUR_MS, 5, now)})
    env.page.wait_for_timeout(1200)
    assert env.page.evaluate("() => window.__xt.v712.tdState()")["req"] == 0


def test_t278_page_hook_fires_once(make_env):
    """产物落后 11 小时 + 有 token → 触发一次 dispatch；同一浏览器不再重发。"""
    now = int(time.time() * 1000)
    end = now - now % HOUR_MS - 11 * HOUR_MS
    env = make_env(xau=_doc(end), path="/index.html?xt=1&hkt=T")
    env.page.wait_for_timeout(1500)
    assert len(env.gh.dispatches) == 1, env.gh.dispatches
    body = env.gh.dispatches[0]
    assert body["ref"] == "main"
    assert body["inputs"]["loop"] == "10"
    # 再等一轮报价定时器的机会：不能重复发
    env.page.evaluate("() => window.__xt.v712.hookRun()")
    env.page.wait_for_timeout(400)
    assert len(env.gh.dispatches) == 1, env.gh.dispatches


def test_t279_page_hook_silent_without_token(make_env):
    now = int(time.time() * 1000)
    end = now - now % HOUR_MS - 11 * HOUR_MS
    env = make_env(xau=_doc(end), path="/index.html?xt=1")
    env.page.wait_for_timeout(1200)
    assert env.gh.dispatches == []


def test_t280_page_td_survives_product_reload(make_env):
    """产物重拉之后，TD 补的根必须还在。

    `applyXauDoc` 是整段替换 S.kl 的。产物本身还停在 11 小时前，所以每 60 秒一次的
    重拉会把 TD 补好的根全冲掉 —— 页面就会「新鲜 → 滞后 → 新鲜」来回跳。
    这条用「重拉一次产物」把那个瞬间固定下来。
    """
    now = int(time.time() * 1000)
    end = now - now % HOUR_MS - 11 * HOUR_MS
    env = make_env(xau=_doc(end), path="/index.html?xt=1&tdk=K",
                   td={"1h": _td(HOUR_MS, 30, now - now % HOUR_MS),
                       "4h": _td(4 * HOUR_MS, 20, now - now % (4 * HOUR_MS)),
                       "1d": _td(24 * HOUR_MS, 20, now - now % (24 * HOUR_MS))})
    page = env.page
    page.wait_for_timeout(1500)
    fresh = page.evaluate("() => window.__xt.klState()['1h'].lastT")

    page.evaluate("() => window.__xt.v76.loadXau()")   # 产物重拉：S.kl 被整段替换
    page.evaluate("() => window.__xt.patchFormingBar()")
    page.wait_for_timeout(300)

    after = page.evaluate("() => window.__xt.klState()['1h'].lastT")
    assert after >= fresh, "产物重拉把 TD 补的根冲掉了：%s -> %s" % (fresh, after)
    assert page.evaluate("() => window.__xt.klLagInfo()")["lagH"] < 1.5


def test_t281_config_file_enables_td(make_env):
    """key 写在 xau/config.json 里就该生效 —— 换手机换浏览器都不用重填。

    这条同时盯住「配置是启动后才到的」这个时序：加载完必须自己补一次，
    否则要等 5 分钟后的下一个 tick，用户看到的就是「配置了还是滞后」。
    """
    now = int(time.time() * 1000)
    end = now - now % HOUR_MS - 11 * HOUR_MS
    env = make_env(xau=_doc(end), path="/index.html?xt=1",
                   td_cfg={"td_key": "CFGK", "hook_token": ""},
                   td={"1h": _td(HOUR_MS, 30, now - now % HOUR_MS),
                       "4h": _td(4 * HOUR_MS, 20, now - now % (4 * HOUR_MS)),
                       "1d": _td(24 * HOUR_MS, 20, now - now % (24 * HOUR_MS))})
    page = env.page
    page.wait_for_timeout(1800)

    st = page.evaluate("() => window.__xt.v712.tdState()")
    assert st["key"] is True, st
    assert st["ok"] >= 1, st
    assert page.evaluate("() => window.__xt.klLagInfo()")["lagH"] < 1.5
    assert not env.errors, env.errors
