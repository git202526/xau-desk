"""M0 基线固化：为 v6.1 生成五视口截图，供改版后对比与回退验证。"""
import functools
import http.server
import os
import threading

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "mockups", "baseline")
VIEWPORTS = [(360, 780, "360x780"), (390, 844, "390x844"), (430, 932, "430x932"),
             (768, 1024, "768x1024"), (1280, 900, "1280x900")]


class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def main():
    os.makedirs(OUT, exist_ok=True)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 8766), functools.partial(Q, directory=ROOT))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        b = p.chromium.launch()
        for w, h, name in VIEWPORTS:
            ctx = b.new_context(viewport={"width": w, "height": h})
            pg = ctx.new_page()
            pg.goto("http://127.0.0.1:8766/index.html", wait_until="domcontentloaded")
            pg.wait_for_timeout(900)
            pg.screenshot(path=os.path.join(OUT, "v61-%s.png" % name))
            print("saved", name, "scrollHeight=", pg.evaluate("document.documentElement.scrollHeight"))
            ctx.close()
        b.close()
    httpd.shutdown()


if __name__ == "__main__":
    main()
