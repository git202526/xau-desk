#!/usr/bin/env python3
"""v6.18 上线前的真机走查：真浏览器、真行情接口、不打 ?xt=1。

做四件事：
  1. 起本地静态服务（模拟 GitHub Pages 的同源环境）拉 index.html，含 calendar.json
  2. 390×844（iPhone）与 320×568（最窄）两档各截 计划 / 设置 两页
  3. 收集 console error / page error / 请求失败
  4. 断言页面无横向溢出、六格分布与 α 输入框都在、档位行 6 条

用法：python3 tools/verify_v618.py [--out /tmp/shots]
"""
import argparse
import functools
import http.server
import json
import os
import socket
import sys
import threading

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8899


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve():
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect(("127.0.0.1", PORT))
        return
    except OSError:
        pass
    finally:
        s.close()
    h = functools.partial(Quiet, directory=ROOT)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), h)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/v618")
    ap.add_argument("--url", default="")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    url = args.url
    if not url:
        serve()
        url = "http://127.0.0.1:%d/index.html" % PORT

    bad = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for w, h, tag in ((390, 844, "iphone"), (320, 568, "narrow")):
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2)
            page = ctx.new_page()
            page.on("console", lambda m: bad.append(("console", m.type, m.text))
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: bad.append(("pageerror", str(e))))
            page.on("requestfailed",
                    lambda r: bad.append(("reqfail", r.url[:90], str(r.failure))))
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector(".tabbar", timeout=15000)
            page.wait_for_timeout(3500)          # 等行情/汇率/K 线回来

            for tab, name in (("plan", "plan"), ("set", "set")):
                page.click('.tabbar button[data-tab="%s"]' % tab)
                page.wait_for_timeout(700)
                # 视口截图（full_page 会把 position:fixed 的抽屉拉到页中，看着像弹层没关）
                page.screenshot(path=os.path.join(args.out, "%s-%s.png" % (tag, name)))
                if tab == "plan" and page.locator("#avgCard").count():
                    page.locator("#avgCard").scroll_into_view_if_needed()
                    page.wait_for_timeout(250)
                    page.screenshot(path=os.path.join(args.out, "%s-card.png" % tag))
                if tab == "set" and page.locator("#setWGrid").count():
                    page.locator("#setWGrid").scroll_into_view_if_needed()
                    page.wait_for_timeout(250)
                    page.screenshot(path=os.path.join(args.out, "%s-wgrid.png" % tag))

            info = page.evaluate("""() => {
                const doc = document.documentElement;
                const over = [...document.querySelectorAll('body *')]
                    .filter(e => e.getBoundingClientRect().right > innerWidth + 1)
                    .slice(0, 5).map(e => e.tagName + '.' + (e.className || ''));
                return {
                    sw: doc.scrollWidth, iw: innerWidth,
                    gears: document.querySelectorAll('#ladder .lv').length,
                    wgrid: document.querySelectorAll('#setWGrid input').length,
                    alpha: !!document.getElementById('setAlpha'),
                    planOnly: !!document.getElementById('setPlanOnly'),
                    card: (document.getElementById('avgCard') || {}).textContent || '',
                    over: over,
                };
            }""")
            print("[%s %dx%d] scrollW=%d innerW=%d 档位行=%d 分布格=%d α=%s 计划模式=%s 越界=%s"
                  % (tag, w, h, info["sw"], info["iw"], info["gears"],
                     info["wgrid"], info["alpha"], info["planOnly"], info["over"] or "无"))
            print("           卡片: %s" % " ".join(info["card"].split())[:110])
            assert info["sw"] <= info["iw"] + 1, "横向溢出 %s" % info
            assert info["gears"] >= 6, "档位行不足 %s" % info
            assert info["wgrid"] == 6, "档位分布应为 6 格 %s" % info
            assert info["alpha"] and info["planOnly"], "设置页缺新控件 %s" % info
            ctx.close()
        b.close()

    noisy = [x for x in bad if x[0] != "reqfail"]
    fails = [x for x in bad if x[0] == "reqfail"]
    if fails:
        print("\n请求失败（离线环境下正常，联网部署后应消失）：")
        for x in fails[:6]:
            print("  -", x[1], x[2][:60])
    if noisy:
        print("\n控制台/页面错误：")
        for x in noisy:
            print("  -", x)
        sys.exit(1)
    print("\n控制台无 error；截图在 %s" % args.out)


if __name__ == "__main__":
    main()
