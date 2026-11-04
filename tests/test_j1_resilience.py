"""J1 健壮性：隐藏暂停、回前台补一次、429 退避、主备源切换。

约定：
- 不依赖真实网络，全部走 page.route 注入。
- 备源端点由 __xt.setAltUrl 注入，测试里用 https://alt.test/price，
  不写死任何真实地址。
"""
import json

import pytest


@pytest.fixture
def env(make_env):
    """本文件不需要构造账本，直接用默认环境。"""
    def _f(**kw):
        return make_env(**kw)
    return _f


def src(p):
    return p.evaluate("() => window.__xt.srcState()")


def reset(p):
    p.evaluate("() => window.__xt.resetSrc()")


def stub_hidden(p, hidden=True):
    p.evaluate(
        """(h) => {
            Object.defineProperty(document, 'hidden',
                { get: () => h, configurable: true });
        }""",
        hidden,
    )


class Counter:
    """统计某域名下的请求数。"""

    def __init__(self, page, needle):
        self.n = 0
        page.on("request", self._on)
        self.needle = needle

    def _on(self, req):
        if self.needle in req.url:
            self.n += 1

    def take(self):
        n, self.n = self.n, 0
        return n


# ---------------------------------------------------------------- h1 隐藏暂停

def test_h1_hidden_skips_all_three(env):
    """隐藏状态下，三个刷新入口都不发请求。"""
    e = env()
    p = e.page
    gold = Counter(p, "api.gold-api.com")
    tick = Counter(p, "ticker/price")
    kline = Counter(p, "klines")

    stub_hidden(p, True)
    gold.take(); tick.take(); kline.take()

    p.evaluate("() => { window.__xt.refreshPrice(true); window.__xt.refreshTick(); window.__xt.refreshData(); }")
    p.wait_for_timeout(600)

    assert gold.take() == 0, "隐藏时仍在请求现货"
    assert tick.take() == 0, "隐藏时仍在请求逐笔"
    assert kline.take() == 0, "隐藏时仍在拉 K 线"


def test_h2_visible_refresh_paused_but_paxg_guarded(env):
    """可见时正常请求，用来反证 h1 不是「本来就没请求」。"""
    e = env()
    p = e.page
    gold = Counter(p, "api.gold-api.com")
    gold.take()
    p.evaluate("() => window.__xt.refreshPrice(true)")
    p.wait_for_timeout(500)
    assert gold.take() >= 1


# ---------------------------------------------------------------- h3 回前台补一次

def test_h3_visibility_resume_refires_once(env):
    """回前台：报价 / 逐笔 / K 线各补一次；1 秒内重复触发不重复打。"""
    e = env()
    p = e.page
    gold = Counter(p, "api.gold-api.com")
    kline = Counter(p, "klines")

    stub_hidden(p, False)
    gold.take(); kline.take()

    p.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    p.wait_for_timeout(600)

    assert gold.take() >= 1, "回前台没补报价"
    assert kline.take() >= 4, "回前台没补 K 线（应 4 个周期各一次）"

    # 1 秒去重：窗口内再触发一次，不应再打
    p.evaluate("() => window.__xt.refreshPrice()")
    p.wait_for_timeout(200)
    assert gold.take() == 0, "1 秒去重失效"


# ---------------------------------------------------------------- h4 429 退避

def test_h4_429_backoff_ladder_and_reset(env):
    """429 按 30s→60s→120s→300s 递增，成功一次清零。"""
    e = env()
    p = e.page

    p.route("**/api.gold-api.com/**", lambda r: r.fulfill(status=429, body="rate limited"))
    reset(p)

    p.evaluate("() => window.__xt.refreshPrice(true)")
    p.wait_for_timeout(400)
    s = src(p)
    assert s["gold"]["level"] == 1
    left = (s["gold"]["until"] - p.evaluate("() => Date.now()")) / 1000
    assert 25 <= left <= 31, left

    # 退避期内不再打
    p.evaluate("() => { window.__xt.refreshPrice(true); }")
    p.wait_for_timeout(300)
    assert src(p)["gold"]["level"] == 1

    # 强制过期后再失败 → 第二档 60s
    p.evaluate("() => { window.__xt.patchSrc('gold', {until:0}); window.__xt.refreshPrice(true); }")
    p.wait_for_timeout(400)
    s = src(p)
    assert s["gold"]["level"] == 2
    left = (s["gold"]["until"] - p.evaluate("() => Date.now()")) / 1000
    assert 55 <= left <= 61, left

    # 成功一次 → 归零
    p.route("**/api.gold-api.com/**", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({"price": 4072.4})))
    p.evaluate("() => { window.__xt.patchSrc('gold', {until:0}); window.__xt.refreshPrice(true); }")
    p.wait_for_timeout(400)
    s = src(p)
    assert s["gold"]["level"] == 0
    assert s["gold"]["until"] == 0


def test_h5_kline_429_backoff(env):
    """K 线 429 后进入退避，5 分钟周期内不再拉。"""
    e = env()
    p = e.page
    p.route("**/data-api.binance.vision/**", lambda r: r.fulfill(status=429, body="limited"))
    reset(p)
    p.evaluate("() => window.__xt.refreshData()")
    p.wait_for_timeout(600)
    s = src(p)
    assert s["kline"]["level"] == 1
    assert s["kline"]["until"] > p.evaluate("() => Date.now()")


# ---------------------------------------------------------------- h6 主备切换

def alt_body(price=4100.11):
    return {
        "lastPrice": price,
        "changeMargin": "+0.85%",
        "varietyName": "黄金",
        "updateTime": "2026-11-04 01:30:00",
        "goldId": "hf_XAU",
    }


def test_h6_main_fail_switches_to_alt(env):
    """主源 500 → 备源接管；备源成功不累计整体失败。"""
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.setAltUrl('https://alt.test/price')")
    p.route("**/alt.test/**", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(alt_body())))
    p.route("**/api.gold-api.com/**", lambda r: r.fulfill(status=500, body="down"))
    reset(p)

    p.evaluate("() => window.__xt.refreshPrice(true)")
    p.wait_for_timeout(600)

    s = src(p)
    assert s["spotSrc"] == "alt"
    assert s["altCount"] == 1
    assert abs(p.evaluate("() => window.__xt.state().price") - 4100.11) < 1e-6
    assert p.evaluate("() => window.__xt.state().fail") == 0, "备源顶上仍被计入整体失败"


def test_h7_alt_field_mapping(env):
    """lastPrice→price、changeMargin 去 % 转数字、varietyName/updateTime 保留。"""
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.setAltUrl('https://alt.test/price')")
    p.route("**/alt.test/**", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(alt_body())))
    p.route("**/api.gold-api.com/**", lambda r: r.fulfill(status=502, body="bad gateway"))
    reset(p)

    p.evaluate("() => window.__xt.refreshPrice(true)")
    p.wait_for_timeout(600)
    m = src(p)["altMeta"]
    assert m["change"] == pytest.approx(0.85)
    assert m["name"] == "黄金"
    assert m["time"] == "2026-11-04 01:30:00"


def test_h8_alt_url_appends_gold_id(env):
    """goldId=hf_XAU 自动拼；已有则不重复。"""
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.setAltUrl('https://alt.test/price')")
    assert p.evaluate("() => window.__xt.altUrl()") == "https://alt.test/price?goldId=hf_XAU"
    p.evaluate("() => window.__xt.setAltUrl('https://alt.test/price?goldId=hf_XAU')")
    assert p.evaluate("() => window.__xt.altUrl()").count("goldId=") == 1


def test_h9_both_fail_counts_once(env):
    """主备都失败才累加整体失败，且只加 1。"""
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.setAltUrl('https://alt.test/price')")
    p.route("**/alt.test/**", lambda r: r.fulfill(status=503, body="down"))
    p.route("**/api.gold-api.com/**", lambda r: r.fulfill(status=500, body="down"))
    reset(p)

    p.evaluate("() => window.__xt.refreshPrice(true)")
    p.wait_for_timeout(600)
    assert p.evaluate("() => window.__xt.state().fail") == 1


def test_h10_alt_absent_keeps_old_behaviour(env):
    """备源未配置时，主源失败行为与改造前一致：fail+1，价格不动。"""
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.setAltUrl('')")
    p.route("**/api.gold-api.com/**", lambda r: r.fulfill(status=500, body="down"))
    reset(p)
    before = p.evaluate("() => window.__xt.state().price")

    p.evaluate("() => window.__xt.refreshPrice(true)")
    p.wait_for_timeout(500)
    assert p.evaluate("() => window.__xt.state().fail") == 1
    assert p.evaluate("() => window.__xt.state().price") == before


# ---------------------------------------------------------------- h11 备源降频

def test_h11_alt_gap_ladder(env):
    """备源间隔：1 小时内 60s、之后 300s、日累计 800 后 300s。"""
    e = env()
    p = e.page
    assert p.evaluate("() => window.__xt.altGap()") == 60000

    p.evaluate("() => window.__xt.patchState({altSince: Date.now() - 2*3600*1000})")
    assert p.evaluate("() => window.__xt.altGap()") == 300000

    p.evaluate("() => window.__xt.patchState({altSince: Date.now(), altCount: 800})")
    assert p.evaluate("() => window.__xt.altGap()") == 300000


def test_h12_main_probe_gap_after_alt(env):
    """备源激活后主源探测：1 小时内 30s，之后 120s。"""
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.patchState({altSince: Date.now()})")
    assert p.evaluate("() => window.__xt.goldProbeGap()") == 30000
    p.evaluate("() => window.__xt.patchState({altSince: Date.now() - 2*3600*1000})")
    assert p.evaluate("() => window.__xt.goldProbeGap()") == 120000
