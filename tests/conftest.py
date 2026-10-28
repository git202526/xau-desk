"""XAU Desk v6.2 测试公共设施：静态服务、外部接口 mock、云端数据种子。"""
import base64
import functools
import http.server
import json
import math
import os
import socket
import threading

import pytest
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8765
BASE = "http://127.0.0.1:%d" % PORT

PRICE = 4072.40
FX_CNY = 7.10

CFG_EMPTY = {
    "H_BASE": None, "TOTAL": 100, "GRAMS": 0, "COST": 0,
    "gearsDone": [], "takesDone": [], "buys": [], "sells": [], "del": [],
    "cutRatio": 70, "keepGram": None, "stopMode": True, "takePlan": {},
}

CFG_NORMAL = dict(CFG_EMPTY, **{
    "H_BASE": 4800,
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
    """确定性 K 线：缓慢上行 + 正弦波动，保证结构/关键位有内容。"""
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


class GhState:
    def __init__(self, cfg=None):
        self.cfg = dict(cfg) if cfg is not None else None
        self.sha = "sha0" if cfg is not None else None
        self.puts = 0
        self.gets = 0
        self.put_bodies = []

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
              gh_on=True, price=PRICE, reduced_motion=False, path="/index.html?xt=1", hash_=""):
        ctx = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
            reduced_motion="reduce" if reduced_motion else "no-preference",
        )
        gh = GhState(cfg if gh_on else None)
        page = ctx.new_page()

        page.route("**/api.gold-api.com/**", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"price": price})))

        def kline(route):
            url = route.request.url
            itv = "1h"
            for k in ("1d", "4h", "1h"):
                if "interval=" + k in url:
                    itv = k
                    break
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(_bars(itv)))

        page.route("**/data-api.binance.vision/**", kline)
        page.route("**/open.er-api.com/**", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"result": "success", "rates": {"CNY": FX_CNY}})))

        def github(route):
            req = route.request
            url = req.url
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
