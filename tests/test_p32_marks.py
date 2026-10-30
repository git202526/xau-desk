"""P3-2（v6.7）自定义位：增删改、上限、空态、双设备并集、墓碑、不污染关键位。"""
import pytest

PRICE = 4072.40
ROUND = int(round(PRICE / 50)) * 50  # 4050


@pytest.fixture
def page(make_env):
    env = make_env()
    p = env.page
    p.click('.tabbar button[data-tab="struct"]')
    p.wait_for_timeout(250)
    return p


def rows(page):
    return page.evaluate("""() => Array.from(document.querySelectorAll('#mkList .mkrow')).map(r => ({
      id: r.getAttribute('data-mid'),
      price: parseFloat(r.querySelector('.p').textContent.replace(/,/g, '')),
      name: r.querySelector('.n').textContent,
      cls: r.className,
    }))""")


def add_mark(page, price="4100", name="前高压力"):
    page.click("#mkAdd")
    page.wait_for_timeout(120)
    page.fill("#mkPrice", price)
    page.fill("#mkName", name)
    page.click("#mkSave")
    page.wait_for_timeout(150)


def test_mk1_create_and_push(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(200)
    add_mark(page, "4100", "前高压力")
    r = rows(page)
    assert len(r) == 1 and r[0]["price"] == 4100, r
    assert "前高压力" in r[0]["name"]
    page.wait_for_timeout(1200)
    body = env.gh.put_bodies[-1]
    assert len(body["marks"]) == 1, body.get("marks")
    m = body["marks"][0]
    assert m["price"] == 4100 and m["name"] == "前高压力"
    assert m["id"].startswith("mk_") and m["at"] and m["up"]


def test_mk2_survives_reload(make_env):
    from conftest import BASE
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(200)
    add_mark(page, "3988.5", "缺口上沿")
    page.wait_for_timeout(1200)
    page.goto(BASE + "/index.html?xt=1")
    page.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    page.wait_for_timeout(400)
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(300)
    r = rows(page)
    assert len(r) == 1, r
    assert abs(r[0]["price"] - 3988.5) < 0.01
    assert "缺口上沿" in r[0]["name"]


def test_mk3_edit(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(200)
    add_mark(page, "4100", "旧名字")
    page.wait_for_timeout(1200)
    old = rows(page)[0]
    page.click("#mkList .mkrow")
    page.wait_for_timeout(150)
    page.fill("#mkName", "新名字")
    page.fill("#mkPrice", "4120.25")
    page.click("#mkSave")
    page.wait_for_timeout(1300)
    body = env.gh.put_bodies[-1]
    m = [x for x in body["marks"] if x["id"] == old["id"]][0]
    assert m["name"] == "新名字"
    assert abs(m["price"] - 4120.25) < 0.001
    assert m["up"] > 0 and m["up"] >= m["at"]
    # 列表同步（列表沿用关键位的显示精度：1 位小数）
    r = rows(page)
    assert len(r) == 1 and r[0]["price"] == 4120.3, r


def test_mk4_delete_with_tombstone(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(200)
    add_mark(page, "4100", "要删的")
    page.wait_for_timeout(1200)
    mid = rows(page)[0]["id"]
    page.on("dialog", lambda d: d.accept())
    page.click("#mkList .mkrow")
    page.wait_for_timeout(150)
    page.click("#mkDel")
    page.wait_for_timeout(1300)
    assert rows(page) == []
    body = env.gh.put_bodies[-1]
    assert body["marks"] == []
    assert mid in body["del"], body.get("del")


def test_mk5_tombstone_blocks_resurrect(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(200)
    add_mark(page, "4100", "要删的")
    page.wait_for_timeout(1200)
    mid = rows(page)[0]["id"]
    page.on("dialog", lambda d: d.accept())
    page.click("#mkList .mkrow")
    page.wait_for_timeout(150)
    page.click("#mkDel")
    page.wait_for_timeout(1300)

    # 另一台设备仍持有这条记录，并把它推回云端
    stale = {"id": mid, "price": 4100, "name": "要删的", "kind": "auto",
             "note": "", "at": 1, "up": 1}
    env.gh.cfg["marks"] = [stale]
    env.gh.cfg["del"] = []
    # 本机再存一次，触发与远端合并
    page.evaluate("() => { window.__xt.saveCfg(); }")
    page.wait_for_timeout(1400)
    body = env.gh.put_bodies[-1]
    assert all(x["id"] != mid for x in body["marks"]), body["marks"]
    assert mid in body["del"]


def test_mk6_two_devices_union(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(200)
    add_mark(page, "4100", "本机加的")
    page.wait_for_timeout(1200)

    # 另一台设备往云端加了 2 条
    env.gh.cfg["marks"] = env.gh.cfg.get("marks", []) + [
        {"id": "mk_remote1", "price": 3900, "name": "远端一", "kind": "auto", "note": "", "at": 2, "up": 2},
        {"id": "mk_remote2", "price": 4200, "name": "远端二", "kind": "auto", "note": "", "at": 3, "up": 3},
    ]
    page.evaluate("() => { window.__xt.saveCfg(); }")
    page.wait_for_timeout(1500)
    body = env.gh.put_bodies[-1]
    ids = sorted(x["id"] for x in body["marks"])
    assert len(ids) == 3, body["marks"]
    assert "mk_remote1" in ids and "mk_remote2" in ids


def test_mk7_same_id_newest_wins(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(200)
    add_mark(page, "4100", "本机较新")
    page.wait_for_timeout(1200)
    mid = rows(page)[0]["id"]

    env.gh.cfg["marks"] = [{"id": mid, "price": 4100, "name": "远端较旧",
                            "kind": "auto", "note": "", "at": 1, "up": 1}]
    page.evaluate("() => { window.__xt.saveCfg(); }")
    page.wait_for_timeout(1500)
    m = [x for x in env.gh.put_bodies[-1]["marks"] if x["id"] == mid][0]
    assert m["name"] == "本机较新", m


def test_mk8_limit_50(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(200)
    page.evaluate("""() => {
      const S = window.__xt.state();
      S.cfg.marks = [];
      for (let i = 0; i < 49; i++) {
        S.cfg.marks.push({id:'mk_f'+i, price:4000+i, name:'填充'+i, kind:'auto', note:'', at:i, up:i});
      }
      window.__xt.compute(); window.__xt.refreshAll();
    }""")
    page.wait_for_timeout(150)
    add_mark(page, "4101", "第50条")
    assert len(rows(page)) == 50

    page.click("#mkAdd")
    page.wait_for_timeout(120)
    page.fill("#mkPrice", "4102")
    page.click("#mkSave")
    page.wait_for_timeout(200)
    assert len(rows(page)) == 50
    assert page.eval_on_selector("#toast", "e => e.textContent").find("50") >= 0
    assert page.eval_on_selector("#sheetMk", "e => e.classList.contains('on')") is True


def test_mk9_empty_state_and_sample(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(250)
    empty = page.eval_on_selector("#mkList", "e => e.textContent")
    assert "还没有自定义位置" in empty
    btn = page.query_selector("#mkList [data-mk-sample]")
    assert btn is not None
    btn.click()
    page.wait_for_timeout(200)
    r = rows(page)
    assert len(r) == 1 and r[0]["price"] == ROUND, r
    assert "整数" in r[0]["name"]


def test_mk10_invalid_input(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(200)
    page.click("#mkAdd")
    page.wait_for_timeout(120)
    page.fill("#mkPrice", "")
    page.click("#mkSave")
    page.wait_for_timeout(200)
    assert rows(page) == []
    assert page.eval_on_selector("#sheetMk", "e => e.classList.contains('on')") is True
    assert "价格" in page.eval_on_selector("#toast", "e => e.textContent")

    page.fill("#mkPrice", "400000")
    page.click("#mkSave")
    page.wait_for_timeout(200)
    assert rows(page) == []


def test_mk11_levels_untouched(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(250)
    before = page.evaluate("""() => ({
      rows: document.querySelectorAll('#klist .kl:not(.now)').length,
      lv: JSON.stringify(window.__xt.buildLevels(12)),
      takes: JSON.stringify(window.__xt.state().takes),
    })""")
    add_mark(page, "4100", "不该影响系统位")
    page.wait_for_timeout(300)
    after = page.evaluate("""() => ({
      rows: document.querySelectorAll('#klist .kl:not(.now)').length,
      lv: JSON.stringify(window.__xt.buildLevels(12)),
      takes: JSON.stringify(window.__xt.state().takes),
    })""")
    assert before["rows"] == after["rows"], (before["rows"], after["rows"])
    assert before["lv"] == after["lv"]
    assert before["takes"] == after["takes"]


def test_mk12_structure_layout_320(make_env):
    env = make_env(width=320, height=780)
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(250)
    add_mark(page, "4100", "超长名字测试一下会不会顶破布局")
    page.wait_for_timeout(250)
    over = page.evaluate("""() => ({
      sw: document.documentElement.scrollWidth,
      iw: window.innerWidth,
      rowW: (document.querySelector('#mkList .mkrow') || {}).getBoundingClientRect
            ? document.querySelector('#mkList .mkrow').getBoundingClientRect().width : 0,
      boxW: document.getElementById('mkList').getBoundingClientRect().width,
    })""")
    assert over["sw"] <= over["iw"] + 1, over
    assert over["rowW"] <= over["boxW"] + 1, over
