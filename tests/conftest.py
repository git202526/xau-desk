"""XAU Desk v6.2 测试公共设施：静态服务、外部接口 mock、云端数据种子。"""
import base64
import functools
import http.server
import json
import math
import os
import socket
import threading
import time

import pytest
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 并行跑时每个 worker 独占一个端口：xdist 的 gw0/gw1/... → 8765/8766/...
# 不共享单端口，避免 bind 竞态（Errno 98）与 owner worker 先退出导致的服务消失。
_WK = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
PORT = 8765 + (int(_WK[2:]) if _WK[2:].isdigit() else 0)
BASE = "http://127.0.0.1:%d" % PORT

PRICE = 4072.40
FX_CNY = 7.10

CFG_EMPTY = {
    "H_BASE": None, "TOTAL": 100, "GRAMS": 0, "COST": 0,
    "gearsDone": [], "takesDone": [], "buys": [], "sells": [], "del": [],
    "cutRatio": 70, "keepGram": None, "stopMode": True, "takePlan": {},
}

# alpha 把档距标定回「≈1% × 高点」：合成 K 线的 ATR14 只有 0.49%，
# 1.0 会让六档挤在 20 美元内，与真实行情（ATR 1.2%）的档位形状差太远。
CFG_NORMAL = dict(CFG_EMPTY, **{
    "H_BASE": 4800, "alpha": 2.44,
    "keepGram": 20,
    "buys": [
        {"id": "b1", "name": "首笔建仓", "gram": 20, "priceCny": 905.0,
         "time": "2026-08-12T02:00:00.000Z", "note": "", "gear": None},
        {"id": "b2", "name": "回撤加仓", "gram": 25, "priceCny": 921.0,
         "time": "2026-09-03T02:00:00.000Z", "note": "", "gear": None},
        {"id": "b3", "name": "突破追加", "gram": 15, "priceCny": 938.0,
         "time": "2026-10-14T02:00:00.000Z", "note": "", "gear": None},
    ],
})


def _port_open(port):
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="session", autouse=True)
def server():
    if not _port_open(PORT):
        handler = functools.partial(_QuietHandler, directory=ROOT)
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield BASE


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--force-prefers-reduced-motion=no-preference"])
        yield b
        b.close()


def _bars(interval, n=240):
    """确定性 K 线：缓慢上行 + 正弦波动，保证结构/关键位有内容。

    振幅刻意做窄（单根 h-l ≈ 21 美元，ATR14 ≈ 0.49%）：关键位与通道计算依赖
    这样一个稳定、可复现的窄幅序列。v6.18 起档距 = α×ATR，所以凡是要按
    「1% × 高点」排档位的用例，都通过 CFG_NORMAL 里的 alpha 把档距标定回来
    （0.49% × 2.44 ≈ 1.2%），避免六档全挤在现价附近。
    """
    step = {"1h": 3600e3, "4h": 4 * 3600e3, "1d": 24 * 3600e3}.get(interval, 3600e3)
    now = 1761696000000  # 2026-10-29 00:00 UTC 附近，固定值保证可复现
    out = []
    for i in range(n):
        base = 3600 + i * 1.9 + math.sin(i / 9.0) * 26 + math.sin(i / 31.0) * 44
        o = base
        c = base + math.sin(i / 5.0) * 7
        h = max(o, c) + 6 + (i % 5)
        l = min(o, c) - 6 - (i % 4)
        t = int(now - (n - 1 - i) * step)
        out.append([t, "%.2f" % o, "%.2f" % h, "%.2f" % l, "%.2f" % c,
                    "12.5", t + step - 1, "0", 0, "0", "0", "0"])
    return out


def _xau_doc_from(bars, price):
    """把用例的 K 线参数包成 xau/klines.json 产物。

    v7.10.1 起判定链只剩 Dukascopy 一份，夹具不能再靠 Binance PAXG 供数。
    这里按老链路的口径把整条序列归一到 ``price``（老链路 scale = price / 1h 末根收盘，
    四个周期共用同一个 scale），这样 700+ 条既有数值断言不用改。
    """
    if isinstance(bars, dict):
        data = dict(bars)
    elif bars is not None:
        data = {k: bars for k in ("1w", "1d", "4h", "1h")}
    else:
        data = {k: _bars(k) for k in ("1w", "1d", "4h", "1h")}

    scale = 1.0
    one = data.get("1h")
    if one:
        last = float(one[-1][4])
        if last:
            scale = float(price) / last

    # 时间戳保持用例原值：周线自然周分组、回溯窗口起止、序列索引 60 等
    # 数百条断言都按 _bars() 的固定时间戳算，任何平移都会让期望值错位。
    # 代价是夹具末根「看起来」很旧，页面会显示滞后角标——那只是文案，
    # 不进任何计算；真正要验新鲜度的用例自己造相对现在的时间戳。
    def norm(arr):
        out = []
        for b in arr:
            out.append([int(b[0]),
                        "%.3f" % (float(b[1]) * scale),
                        "%.3f" % (float(b[2]) * scale),
                        "%.3f" % (float(b[3]) * scale),
                        "%.3f" % (float(b[4]) * scale),
                        str(b[5])])
        return out

    kl = {k: norm(v) for k, v in data.items() if v}
    return {
        "updated": 1794150000000, "src": "dukascopy", "symbol": "XAUUSD",
        "price_type": "bid",
        "window": {"from": "2026-06-12", "to": "2026-11-08", "days_ok": 1,
                   "days_empty": 0, "failed_n": 0, "failed": []},
        "sanitized": {"synthetic": 0, "dup": 0},
        "stats": {}, "gaps": {k: [] for k in kl}, "kl": kl,
    }


class GhState:
    def __init__(self, cfg=None):
        self.cfg = dict(cfg) if cfg is not None else None
        self.sha = "sha0" if cfg is not None else None
        self.puts = 0
        self.gets = 0
        self.put_bodies = []
        # P118：页面端催 Actions（workflow_dispatch）的请求体，用来断言「只发一次」
        self.dispatches = []

    def as_content(self):
        raw = json.dumps(self.cfg, ensure_ascii=False).encode()
        return {
            "name": "data.json", "path": "data.json", "sha": self.sha,
            "size": len(raw), "encoding": "base64",
            "content": base64.b64encode(raw).decode(),
        }


class Env:
    """一个带 mock 的页面环境。"""

    def __init__(self, page, gh, ctx):
        self.page = page
        self.gh = gh
        self.ctx = ctx
        self.errors = []          # pageerror 收集：白屏类问题必须能被断言

    def goto(self, hash_=""):
        self.page.goto(BASE + "/index.html" + hash_, wait_until="domcontentloaded")
        self.page.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
        return self

    def close(self):
        self.ctx.close()


@pytest.fixture
def make_env(browser):
    created = []

    def _make(cfg=CFG_NORMAL, width=390, height=844, token="t0ken",
              gh_on=True, price=PRICE, reduced_motion=False, path="/index.html?xt=1",
              hash_="", bars=None, calendar=None, tz=None, jn=None, xau="auto",
              macro=None, tick=None, td=None, td_cfg=None):
        opts = dict(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
            reduced_motion="reduce" if reduced_motion else "no-preference",
        )
        if tz:
            opts["timezone_id"] = tz
        ctx = browser.new_context(**opts)
        gh = GhState(cfg if gh_on else None)
        page = ctx.new_page()

        page.route("**/api.gold-api.com/**", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"price": price})))

        def kline(route):
            url = route.request.url
            itv = "1h"
            for k in ("1w", "1d", "4h", "1h"):
                if "interval=" + k in url:
                    itv = k
                    break
            # bars 支持两种形态：单一数组（所有周期共用）或 {周期: 数组}。
            # 后者给「各周期根数不同」的用例用（如判定回溯的窗口长度）。
            if isinstance(bars, dict):
                data = bars.get(itv, bars.get("1h", []))
            elif bars is not None:
                data = bars
            else:
                data = _bars(itv)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(data))

        page.route("**/data-api.binance.vision/api/v3/klines**", kline)

        # PAXG 逐笔（报价桥接，只让价格数字跳得细，不进判定链）。
        # 默认回 price=0：页面判「结构不符」静默跳过（不 srcFail、不写 S.paxg），
        # 于是 srcReady("paxg") 恒为真、锚点 paxg 保持 null，桥接类用例第一次
        # 逐笔仍能走「只补锚点不跳价」的分支。若默认回 500，首屏那次请求就
        # 触发 5 秒退避，所有显式 refreshTick() 的用例会在退避期里静默失效；
        # 若默认给个真实价，又会把 S.anchor.paxg 顶成现货价，drift 全算错。
        # 真正要验「逐笔断开」的场景传 tick="down"。
        def tick_route(route):
            if tick == "down":
                route.fulfill(status=500, content_type="application/json", body="{}")
            else:
                px = 0 if tick in (None, True, False) else tick
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"symbol": "PAXGUSDT",
                                               "price": "%.2f" % float(px)}))
        page.route("**/ticker/price**", tick_route)

        # v7.10.1：默认按 bars 现造一份 xau/klines.json——判定链只剩这一个源。
        # xau=None 表示「产物不可用」，用于验「停算 + 数据延迟」。
        if xau == "auto":
            xau_doc = _xau_doc_from(bars, price)
        elif isinstance(xau, dict):
            xau_doc = xau
        else:
            xau_doc = None

        def xau_route(route):
            if xau_doc is None:
                route.fulfill(status=404, content_type="application/json",
                              body=json.dumps({"message": "Not Found"}))
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(xau_doc))
        page.route("**/xau/klines.json*", xau_route)

        # v7.6 P3：宏观双变量产物。默认 404 = 中转没跑，页面必须显示「数据延迟」而不是崩。
        def macro_route(route):
            if macro is None:
                route.fulfill(status=404, content_type="application/json",
                              body=json.dumps({"message": "Not Found"}))
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(macro, ensure_ascii=False))
        page.route("**/macro.json*", macro_route)
        page.route("**/open.er-api.com/**", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"result": "success", "rates": {"CNY": FX_CNY}})))

        def github(route):
            req = route.request
            url = req.url
            if req.method == "POST" and url.endswith("/dispatches"):
                gh.dispatches.append(req.post_data_json or {})
                route.fulfill(status=204, content_type="application/json", body="")
                return
            if req.method == "PUT":
                gh.puts += 1
                body = req.post_data_json or {}
                if body.get("content"):
                    gh.cfg = json.loads(base64.b64decode(body["content"]).decode())
                    gh.put_bodies.append(gh.cfg)
                gh.sha = "sha%d" % gh.puts
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"content": {"sha": gh.sha}}))
            elif "/contents/" in url:
                gh.gets += 1
                if gh.cfg is None:
                    route.fulfill(status=404, content_type="application/json",
                                  body=json.dumps({"message": "Not Found"}))
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps(gh.as_content()))
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"full_name": "git202526/xau-desk-data",
                                               "private": True}))

        page.route("**/api.github.com/**", github)

        # P118：Twelve Data 直连补数据。默认回 401（等价于「没配 key 也是这个结局」），
        # 页面必须静默吞掉；用例传 td={"1h": {...}, "4h": {...}} 才真正供数。
        td_calls = []

        def td_route(route):
            url = route.request.url
            itv = "1h"
            for k in ("1d", "4h", "1h"):
                if "interval=" + k in url:
                    itv = k
                    break
            td_calls.append(itv)
            if not isinstance(td, dict) or "error" in td:
                route.fulfill(status=401, content_type="application/json",
                              body=json.dumps((td or {}).get("error") or
                                              {"code": 401, "status": "error",
                                               "message": "no key"}))
                return
            body = td.get(itv)
            if body is None:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"code": 400, "status": "error",
                                               "message": "bad interval"}))
                return
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(body))
        page.route("**api.twelvedata.com/**", td_route)

        # P118：可选配置文件的加载路径。默认 404 = 「没配」，页面必须静默跳过。
        def cfg_route(route):
            if isinstance(td_cfg, dict):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(td_cfg))
            else:
                route.fulfill(status=404, content_type="application/json", body="{}")
        page.route("**/xau/config.json*", cfg_route)
        # 事件日历：默认按「取不到」处理，保持 v6.13 之前的行为（只有内置 FOMC + 非农）；
        # 需要测实时日历的用例显式传 calendar=dict
        def _cal(route):
            if isinstance(calendar, dict):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(calendar, ensure_ascii=False))
            else:
                route.fulfill(status=404, content_type="application/json", body="{}")

        page.route("**/calendar.json*", _cal)

        # 金十中转产物：默认全部 404，等价于"中转还没跑起来"。
        # 用例传 jn={"calendar.json": {...}} 即注入。注意注册顺序在 calendar.json 之后，
        # Playwright 后注册的优先，否则 jin10/calendar.json 会被上面的 _cal 抢走。
        # v6.58 起金十只产 calendar.json，flash / news / quote / kline_*.json 都已停抓。
        def _jn(route):
            url = route.request.url
            name = url.split("/jin10/")[-1].split("?")[0]
            body = (jn or {}).get(name)
            if body is None:
                route.fulfill(status=404, content_type="application/json", body="{}")
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(body, ensure_ascii=False))

        page.route("**/jin10/**", _jn)
        page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
        page.route("**/fonts.gstatic.com/**", lambda r: r.abort())

        cred = {"token": token if gh_on else "", "repo": "git202526/xau-desk-data",
                "branch": "main", "path": "data.json", "sha": ""}
        page.add_init_script(
            "localStorage.setItem('wb_xau_desk_gh', %s);"
            "window.__osc = 0;"
            "(function(){var AC = window.AudioContext || window.webkitAudioContext;"
            "if(!AC) return; var old = AC.prototype.createOscillator;"
            "AC.prototype.createOscillator = function(){ window.__osc++; return old.apply(this, arguments); };"
            "})();" % json.dumps(json.dumps(cred)))

        env = Env(page, gh, ctx)
        page.on("pageerror", lambda e: env.errors.append(str(e)))
        created.append(env)
        page.goto(BASE + path + hash_, wait_until="domcontentloaded")
        page.wait_for_function("() => !!document.querySelector('.tabbar') || !!document.querySelector('.tabs')",
                               timeout=8000)
        page.wait_for_timeout(450)
        return env

    yield _make
    for e in created:
        try:
            e.ctx.close()
        except Exception:
            pass


def js(page, expr, arg=None):
    return page.evaluate(expr, arg) if arg is not None else page.evaluate(expr)
