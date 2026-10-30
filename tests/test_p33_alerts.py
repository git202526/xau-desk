"""P3-3（v6.8）到价提醒：穿越判定、去抖、重新武装、联动、写入频率、并集。"""
import pytest


def goto_struct(page):
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(220)


def tick(page, p):
    page.evaluate("(p) => window.__xt.tick(p)", p)
    page.wait_for_timeout(90)


def alerts(page):
    return page.evaluate("() => window.__xt.state().cfg.alerts || []")


def alert0(page):
    a = alerts(page)
    return a[0] if a else None


def set_alert(page, price="4050", name="提醒位", dir_="up", repeat=False):
    page.click("#mkAdd")
    page.wait_for_timeout(120)
    page.fill("#mkPrice", price)
    page.fill("#mkName", name)
    page.check("#mkAlertOn")
    page.wait_for_timeout(80)
    page.click('#mkAlertDir button[data-d="%s"]' % dir_)
    if repeat:
        page.check("#mkAlertRepeat")
    page.click("#mkSave")
    page.wait_for_timeout(250)


@pytest.fixture
def page(make_env):
    env = make_env()
    goto_struct(env.page)
    return env.page


def test_al1_up_cross_fires(page):
    set_alert(page, "4050", "前高压力", "up")
    tick(page, 4040)
    assert alert0(page)["firedAt"] == 0
    tick(page, 4055)
    a = alert0(page)
    assert a["firedAt"] > 0, a
    assert a["ackAt"] == 0
    feed = page.evaluate("() => window.__xt.state().cfg.feed")
    assert any("提醒命中" in f["text"] for f in feed), feed[:3]


def test_al2_no_cross_no_fire(page):
    set_alert(page, "4100", "远处", "up")
    tick(page, 4040)
    tick(page, 4050)
    assert alert0(page)["firedAt"] == 0


def test_al3_no_fire_on_first_load(make_env):
    from conftest import BASE
    env = make_env()
    page = env.page
    goto_struct(page)
    set_alert(page, "4000", "已在下方", "up")
    page.wait_for_timeout(1200)
    page.goto(BASE + "/index.html?xt=1")
    page.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    page.wait_for_timeout(1200)
    tick(page, 4070)          # 第二次观测，仍在上方，不算穿越
    tick(page, 4075)
    assert alert0(page)["firedAt"] == 0, alert0(page)


def test_al4_once_mode(page):
    set_alert(page, "4050", "只响一次", "up")
    tick(page, 4040); tick(page, 4055)
    a = alert0(page)
    assert a["firedAt"] > 0 and a["on"] is False
    first = a["firedAt"]
    tick(page, 4040); tick(page, 4055)
    a = alert0(page)
    assert a["firedAt"] == first
    assert a["on"] is False


def test_al5_repeat_mode_rearms(page):
    set_alert(page, "4050", "每次都响", "up", repeat=True)
    tick(page, 4040); tick(page, 4055)
    first = alert0(page)["firedAt"]
    assert first > 0
    # 模拟一分钟已过，避免撞上去抖窗口
    page.evaluate("() => { const a = window.__xt.state().cfg.alerts[0]; a.firedAt = Date.now() - 61000; }")
    tick(page, 4040); tick(page, 4055)
    a = alert0(page)
    assert a["firedAt"] > first, (first, a["firedAt"])
    assert a["on"] is True


def test_al6_debounce_60s(page):
    set_alert(page, "4050", "防抖", "both", repeat=True)
    tick(page, 4040); tick(page, 4055)
    first = alert0(page)["firedAt"]
    assert first > 0
    # 立刻反向再穿回来：仍在 60 秒窗口内，不应再次命中
    tick(page, 4040); tick(page, 4055)
    assert alert0(page)["firedAt"] == first


def test_al7_todo_and_ack(page):
    set_alert(page, "4050", "待确认", "up")
    tick(page, 4040); tick(page, 4055)
    page.click('.tabbar button[data-tab="ov"]')
    page.wait_for_timeout(200)
    item = page.query_selector(".tic.k-alert")
    assert item is not None
    assert "待确认" in item.inner_text()
    page.click(".tic.k-alert button")
    page.wait_for_timeout(220)
    assert page.query_selector(".tic.k-alert") is None
    assert alert0(page)["ackAt"] > 0


def test_al8_badge_counts_unacked(page):
    set_alert(page, "4050", "角标", "up")
    tick(page, 4040); tick(page, 4055)
    assert page.eval_on_selector("#bdStruct", "e => e.hidden") is False
    assert page.eval_on_selector("#bdStruct", "e => e.textContent") == "1"
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(120)
    assert page.eval_on_selector("#bdStruct", "e => getComputedStyle(e).display") == "none"
    page.click('.tabbar button[data-tab="ov"]')
    page.wait_for_timeout(120)
    assert page.eval_on_selector("#bdStruct", "e => getComputedStyle(e).display") != "none"


def test_al9_sound_once(page):
    assert page.evaluate("() => window.__osc") == 0
    set_alert(page, "4050", "响声", "up")
    tick(page, 4040)
    page.evaluate("() => { window.__osc = 0; }")
    tick(page, 4055)
    assert page.evaluate("() => window.__osc") == 2


def test_al10_write_frequency(make_env):
    env = make_env()
    page = env.page
    goto_struct(page)
    set_alert(page, "4050", "写入次数", "up")
    page.wait_for_timeout(1400)
    base = env.gh.puts
    tick(page, 4040)
    tick(page, 4055)
    page.wait_for_timeout(1300)
    assert env.gh.puts == base + 1, (base, env.gh.puts)
    # 没有穿越的连续刷新不应写云端
    tick(page, 4056); tick(page, 4057); tick(page, 4058)
    page.wait_for_timeout(1300)
    assert env.gh.puts == base + 1, (base, env.gh.puts)


def test_al11_remove_alert_keeps_mark(make_env):
    env = make_env()
    page = env.page
    goto_struct(page)
    set_alert(page, "4050", "要撤销", "up")
    aid = alert0(page)["id"]
    page.wait_for_timeout(1300)
    page.click("#mkList .mkrow")
    page.wait_for_timeout(150)
    page.uncheck("#mkAlertOn")
    page.click("#mkSave")
    page.wait_for_timeout(1400)
    assert alerts(page) == []
    assert len(page.query_selector_all("#mkList .mkrow")) == 1
    body = env.gh.put_bodies[-1]
    assert aid in body.get("del", []), body.get("del")
    assert body["alerts"] == []


def test_al12_union_across_devices(make_env):
    env = make_env()
    page = env.page
    goto_struct(page)
    set_alert(page, "4050", "本机提醒", "up")
    page.wait_for_timeout(1300)
    mid = alert0(page)["id"]
    env.gh.cfg["alerts"] = env.gh.cfg.get("alerts", []) + [
        {"id": "al_remote", "markId": None, "price": 4200, "name": "远端提醒",
         "dir": "up", "mode": "once", "on": True, "firedAt": 0, "ackAt": 0, "at": 5, "up": 5}
    ]
    page.evaluate("() => window.__xt.saveCfg()")
    page.wait_for_timeout(1500)
    body = env.gh.put_bodies[-1]
    ids = sorted(a["id"] for a in body["alerts"])
    assert mid in ids and "al_remote" in ids, ids
