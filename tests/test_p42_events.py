"""v6.13 事件日历：实时日历（calendar.json）接入、降级与过期判定。

calendar.json 由 GitHub Actions 每 6 小时抓一次。测试直接 mock 这个文件，
时区固定 Asia/Shanghai，这样「20:30」这类本地时间断言才稳定。
"""
import datetime as dt
from zoneinfo import ZoneInfo

SH = ZoneInfo("Asia/Shanghai")
UTC = dt.timezone.utc

TODAY_2030_NAME = "美国成屋签约销售月率"


def ms(d):
    return int(d.timestamp() * 1000)


def now_sh():
    return dt.datetime.now(SH)


def plus_min(m):
    return ms(now_sh() + dt.timedelta(minutes=m))


def iso(d):
    return d.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_2030():
    n = now_sh()
    return ms(n.replace(hour=20, minute=30, second=0, microsecond=0))


def ev(t, name, impact=4, ccy="USD", fc="", pv="", en=""):
    return {"t": t, "ccy": ccy, "name": name, "en": en or name,
            "impact": impact, "fc": fc, "pv": pv, "src": "ff"}


def doc(events, age_days=0.0):
    upd = dt.datetime.now(UTC) - dt.timedelta(days=age_days)
    return {"updatedAt": iso(upd), "week": "2026-10-26..2026-10-31",
            "count": len(events), "events": events}


def doc_live():
    return doc([
        ev(plus_min(45), "美国GDP季率", 4, fc="3.2%", pv="1.5%", en="Advance GDP q/q"),
        ev(plus_min(90), "美国核心PCE物价指数月率", 4, fc="0.3%", pv="0.2%",
           en="Core PCE Price Index m/m"),
        ev(plus_min(135), "美国初请失业金", 4, fc="198K", pv="194K", en="Unemployment Claims"),
        ev(plus_min(180), "美国零售销售月率", 3, fc="0.4%", pv="0.6%", en="Retail Sales m/m"),
    ])


def doc_mix():
    return doc([
        ev(plus_min(-90), "美国工业产出月率", 3, fc="0.1%", pv="0.3%", en="Industrial Production m/m"),
        ev(today_2030(), TODAY_2030_NAME, 4, fc="1.2%", pv="0.4%", en="Pending Home Sales m/m"),
        ev(plus_min(45), "美国GDP季率", 4, fc="3.2%", pv="1.5%", en="Advance GDP q/q"),
        ev(plus_min(90), "美国核心PCE物价指数月率", 4, fc="0.3%", pv="0.2%",
           en="Core PCE Price Index m/m"),
    ])


def open_sig(page):
    page.click('.tabbar button[data-tab="sig"]')
    page.wait_for_timeout(280)


def wait_cal(page, want=True):
    if want:
        page.wait_for_function("() => window.__xt.state().cal.ok === true", timeout=6000)
    page.wait_for_timeout(250)


def items(page):
    return page.evaluate("""() => [...document.querySelectorAll('#evList .ev')].map(e => ({
        name: (e.querySelector('.n') || {}).textContent || '',
        m: (e.querySelector('.m') || {}).textContent || '',
        cd: (e.querySelector('.cd b') || {}).textContent || '',
        time: (e.querySelector('.cd span') || {}).textContent || '',
        src: (e.querySelector('.src') || {}).textContent || '',
        stars: ((e.querySelector('.stars') || {}).textContent || '').length,
        done: e.className.indexOf('done') >= 0,
    }))""")


def txt(page, sel):
    return page.evaluate("(s) => (document.querySelector(s) || {}).textContent || ''", sel)


def find(rows, kw):
    for r in rows:
        if kw in r["name"]:
            return r
    raise AssertionError("没找到含「%s」的事件：%s" % (kw, [r["name"] for r in rows]))


# ---------- 实时日历接入 ----------

def test_ev1_all_events_rendered(make_env):
    env = make_env(calendar=doc_live(), tz="Asia/Shanghai")
    page = env.page
    wait_cal(page)
    open_sig(page)
    rows = items(page)
    for kw in ("GDP", "核心PCE", "初请失业金", "零售销售"):
        assert find(rows, kw), kw
    assert "实时" in txt(page, "#evMeta"), txt(page, "#evMeta")
    assert find(rows, "GDP")["src"] == "实时日历"


def test_ev2_four_star_first(make_env):
    env = make_env(calendar=doc_live(), tz="Asia/Shanghai")
    page = env.page
    wait_cal(page)
    open_sig(page)
    rows = items(page)
    top = rows[:2]
    assert all(r["stars"] == 4 for r in top), [(r["name"], r["stars"]) for r in rows]
    assert "GDP" in top[0]["name"] and "核心PCE" in top[1]["name"], top
    # 后面的 3 星排在 4 星之后（同为未来时按时间）
    assert rows[2]["stars"] == 4 and rows[3]["stars"] == 3, rows


def test_ev3_local_timezone(make_env):
    env = make_env(calendar=doc_mix(), tz="Asia/Shanghai")
    page = env.page
    wait_cal(page)
    open_sig(page)
    r = find(items(page), TODAY_2030_NAME)
    assert r["time"] == "20:30", r


def test_ev4_forecast_previous(make_env):
    env = make_env(calendar=doc_live(), tz="Asia/Shanghai")
    page = env.page
    wait_cal(page)
    open_sig(page)
    r = find(items(page), "核心PCE")
    assert "预期 0.3%" in r["m"], r["m"]
    assert "前值 0.2%" in r["m"], r["m"]
    assert "USD" in r["m"], r["m"]


# ---------- 降级 ----------

def test_ev5_fallback_when_missing(make_env):
    env = make_env(calendar=None, tz="Asia/Shanghai")
    page = env.page
    page.wait_for_timeout(600)
    open_sig(page)
    assert page.evaluate("() => window.__xt.state().cal.ok") is False
    body = txt(page, "#evList")
    assert body.strip(), "取不到日历时不能白屏"
    assert ("非农" in body) or ("利率决议" in body), body
    assert "不可用" in txt(page, "#evNote"), txt(page, "#evNote")
    assert "实时" not in txt(page, "#evMeta")


def test_ev6_stale_warning(make_env):
    env = make_env(calendar=doc_live(), tz="Asia/Shanghai")
    page = env.page
    # 换成 4 天前的数据
    page.evaluate("""(d) => { window.__xt.state().cal.updatedAt = d; window.__xt.refreshAll(); }""",
                  int((dt.datetime.now(UTC) - dt.timedelta(days=4)).timestamp() * 1000))
    open_sig(page)
    st = page.evaluate("() => window.__xt.calState()")
    assert st["ok"] is True and st["stale"] is True, st
    assert "未更新" in txt(page, "#evNote"), txt(page, "#evNote")


def test_ev7_too_old_falls_back(make_env):
    env = make_env(calendar=doc_live(), tz="Asia/Shanghai")
    page = env.page
    wait_cal(page)
    page.evaluate("""(d) => { window.__xt.state().cal.updatedAt = d; window.__xt.refreshAll(); }""",
                  int((dt.datetime.now(UTC) - dt.timedelta(days=9)).timestamp() * 1000))
    open_sig(page)
    st = page.evaluate("() => window.__xt.calState()")
    assert st["ok"] is False, st
    assert "7 天" in txt(page, "#evNote"), txt(page, "#evNote")
    body = txt(page, "#evList")
    assert "核心PCE" not in body, "超过 7 天应回落到固定事件"
    assert "实时" not in txt(page, "#evMeta")


# ---------- 兼容与布局 ----------

def test_ev8_unmapped_keeps_english(make_env):
    # schema 校验要求至少 3 条，所以配 2 条已知的 + 1 条没进映射表的
    env = make_env(calendar=doc([
        ev(plus_min(30), "美国GDP季率", 4, fc="3.2%", pv="1.5%", en="Advance GDP q/q"),
        ev(plus_min(60), "Foo Bar Sentiment Index", 4, fc="12.5", pv="11.0"),
        ev(plus_min(90), "美国零售销售月率", 3, fc="0.4%", pv="0.6%", en="Retail Sales m/m"),
    ]), tz="Asia/Shanghai")
    page = env.page
    wait_cal(page)
    open_sig(page)
    r = find(items(page), "Foo Bar")
    assert r["name"].strip() == "Foo Bar Sentiment Index", r
    assert "undefined" not in txt(page, "#evList")


def test_ev9_narrow_no_overflow(make_env):
    for w in (320, 360):
        env = make_env(calendar=doc_mix(), width=w, tz="Asia/Shanghai")
        page = env.page
        wait_cal(page)
        open_sig(page)
        info = page.evaluate("""() => ({
            sw: document.documentElement.scrollWidth, iw: innerWidth,
            over: [...document.querySelectorAll('#evList .ev')].map(e => e.scrollWidth - e.clientWidth),
            nOver: [...document.querySelectorAll('#evList .n')].map(e => e.scrollWidth - e.clientWidth),
        })""")
        assert info["sw"] <= info["iw"] + 1, (w, info)
        assert max(info["over"]) <= 1, (w, info["over"])
        env.close()


def test_ev10_past_event_marked(make_env):
    """已公布判定要精确到「这一条」，不能取 done 列表第一项 ——
    fixture 里「今天 20:30」那条在 20:30 之后也会变 done，会随运行时刻改变顺序。"""
    env = make_env(calendar=doc_mix(), tz="Asia/Shanghai")
    page = env.page
    wait_cal(page)
    open_sig(page)
    rows = items(page)
    done = [r for r in rows if r["done"]]
    assert done, [r["name"] for r in rows]
    # -90 分钟那条任何时候跑都必然已公布
    past = find(rows, "工业产出")
    assert past["done"] is True, past
    assert "已公布" in past["cd"], past
    # 已公布的那条仍有名称与星级，不是空壳
    assert past["stars"] >= 2 and past["name"].strip()
    # 边界：「今天 20:30」那条的已公布状态必须与当前时刻一致
    n = now_sh()
    is_past = n >= n.replace(hour=20, minute=30, second=0, microsecond=0)
    today = find(rows, TODAY_2030_NAME)
    assert today["done"] is is_past, (today, n.isoformat())
