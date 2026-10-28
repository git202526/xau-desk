"""M5 回归：运行时错误、声音、价格闪动、记一笔/减仓、云端合并与墓碑。"""
import json


def page_errors(page):
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("console", lambda m: errs.append("console:" + m.text) if m.type == "error" else None)
    return errs


def test_no_runtime_errors_on_load_and_switch(make_env):
    env = make_env()
    page = env.page
    errs = page_errors(page)
    for key in ("struct", "plan", "pos", "sig", "set", "ov"):
        page.click('.tabbar button[data-tab="%s"]' % key)
        page.wait_for_timeout(180)
    assert errs == [], errs


def test_take_ladder_renders(make_env):
    """获利状态下成本位缺 dist 曾导致整块空白，这里守住。"""
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="pos"]')
    page.wait_for_timeout(200)
    rows = page.eval_on_selector_all("#takeBox .tw", "els => els.length")
    assert rows >= 3, "止盈阶梯行数 %s" % rows
    text = page.eval_on_selector("#takeBox", "e => e.innerText")
    assert "记一笔买入后" not in text


def test_sound_synth_three_kinds(make_env):
    env = make_env()
    page = env.page
    counts = page.evaluate("""() => {
      const c = {};
      window.__osc = 0; window.__xt.playSnd('gear'); c.gear = window.__osc;
      window.__osc = 0; window.__xt.playSnd('take'); c.take = window.__osc;
      window.__osc = 0; window.__xt.playSnd('warn'); c.warn = window.__osc;
      return c;
    }""")
    assert counts["gear"] == 2, counts
    assert counts["take"] == 3, counts
    assert counts["warn"] == 2, counts


def test_sound_toggle_blocks_playback(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="set"]')
    page.uncheck("#setSnd")
    n = page.evaluate("() => { window.__osc = 0; window.__xt.playSnd('gear'); return window.__osc; }")
    assert n == 0
    page.check("#setSnd")
    n2 = page.evaluate("() => { window.__osc = 0; window.__xt.playSnd('gear'); return window.__osc; }")
    assert n2 == 2


def test_sound_test_buttons(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="set"]')
    n = page.evaluate("""() => { window.__osc = 0; document.getElementById('btnSndBuy').click(); return window.__osc; }""")
    assert n == 2


def test_price_flash_and_topbar_sync(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="pos"]')
    page.wait_for_timeout(100)
    page.evaluate("window.__xt.setPrice(4088.20)")
    page.wait_for_timeout(60)
    assert page.eval_on_selector("#pNow", "e => e.className").find("flash-up") >= 0
    assert page.eval_on_selector("#tpx", "e => e.textContent.trim()") in ("4,088.20", "4088.20")
    page.wait_for_timeout(700)
    page.evaluate("window.__xt.setPrice(4060.00)")
    page.wait_for_timeout(60)
    assert page.eval_on_selector("#pNow", "e => e.className").find("flash-dn") >= 0


def test_add_buy_via_sheet_writes_cloud(make_env):
    env = make_env()
    page = env.page
    page.wait_for_timeout(600)
    before = env.gh.puts
    page.click('.tabbar button[data-tab="pos"]')
    page.click("#btnAddBuy")
    page.wait_for_timeout(200)
    assert page.eval_on_selector("#mask", "e => e.classList.contains('on')") is True
    page.fill('[name="g_name"]', "回归加仓")
    page.fill('[name="g_gram"]', "10")
    page.fill('[name="g_price"]', "930")
    page.click("#btnSave")
    page.wait_for_timeout(1800)
    assert env.gh.puts > before, "记一笔后没有写入云端"
    ids = [b["name"] for b in env.gh.cfg["buys"]]
    assert "回归加仓" in ids, ids
    assert len(ids) == 4, ids
    assert page.eval_on_selector("#mask", "e => e.classList.contains('on')") is False
    page.click('.tabbar button[data-tab="ov"]')
    page.wait_for_timeout(150)
    assert page.eval_on_selector("#pbGram", "e => e.textContent.trim()") == "70.0g"


def test_sell_reduces_holdings(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="pos"]')
    page.wait_for_timeout(200)
    page.click("#takeBox .tw.cut")
    page.wait_for_timeout(200)
    assert page.eval_on_selector("#sheet", "e => e.classList.contains('on')") is True
    gram = page.eval_on_selector('[name="g_gram"]', "e => e.value")
    assert float(gram) > 0
    page.click("#btnSave")
    page.wait_for_timeout(1800)
    assert len(env.gh.cfg["sells"]) == 1
    page.click('.tabbar button[data-tab="ov"]')
    page.wait_for_timeout(150)
    grams = page.eval_on_selector("#pbGram", "e => e.textContent.trim()")
    assert grams != "60.0g", grams


def test_delete_buy_writes_tombstone_and_persists(make_env):
    """删除要写墓碑并同步，刷新后不得复活。"""
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="pos"]')
    page.wait_for_timeout(200)
    before = page.eval_on_selector_all("#buyList .tk[data-buy]", "els => els.length")
    assert before == 3

    page.on("dialog", lambda d: d.accept())
    page.click("#buyList .tk[data-buy]")
    page.wait_for_timeout(200)
    assert page.eval_on_selector("#btnDelBuy", "e => getComputedStyle(e).display") != "none"
    page.click("#btnDelBuy")
    page.wait_for_timeout(1800)

    buys = env.gh.cfg["buys"]
    dels = env.gh.cfg.get("del") or []
    assert len(buys) == 2, buys
    assert len(dels) == 1, dels
    assert dels[0] not in [b["id"] for b in buys]

    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    page.click('.tabbar button[data-tab="pos"]')
    page.wait_for_timeout(200)
    assert page.eval_on_selector_all("#buyList .tk[data-buy]", "els => els.length") == 2
    assert len(env.gh.cfg["buys"]) == 2, "刷新后被删除的记录复活了"


def test_empty_cloud_mounts_without_error(make_env):
    from conftest import CFG_EMPTY
    env = make_env(cfg=CFG_EMPTY, gh_on=True)
    page = env.page
    errs = page_errors(page)
    page.wait_for_timeout(800)
    assert errs == [], errs
    assert page.eval_on_selector("#pbGram", "e => e.textContent.trim()") == "—"
    assert page.eval_on_selector("#todoBox", "e => !!e") is True


def test_export_button_triggers_download(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="set"]')
    page.wait_for_timeout(150)
    with page.expect_download(timeout=5000) as d:
        page.click("#btnExport")
    name = d.value.suggested_filename
    assert name.endswith(".json"), name
    path = d.value.path()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data.get("buys", [])) == 3
