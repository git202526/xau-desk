"""v6.11 档位已买入：持久标记、单档撤销、按持仓重算。

覆盖用户提的三件事：
1. 在某一档记过买入，即使后来减仓，这一档仍应显示「已买入」；
2. 「已买入 / 已买」与推荐克重支持撤销标记，撤销后按计划重算；
3. 撤销后价格再落回该档，能重新给出待买克重（反弹后再接）。
"""
import json

import pytest

from conftest import BASE, CFG_EMPTY, PRICE

H_BASE = 4800.0
# 4800 的档位价：第1档 4176 / 第2档 4128 / 第3档 4080 / 第4档 4032
CFG = dict(CFG_EMPTY, **{
    "H_BASE": H_BASE, "TOTAL": 200, "keepGram": 20,
    "gearsDone": ["第1档", "第2档"],
    "buys": [
        {"id": "b1700000000001", "name": "第1档", "gram": 16, "priceCny": 830.0,
         "time": "2026-09-10T02:00:00.000Z", "note": "", "gear": "第1档"},
        {"id": "b1700000000002", "name": "第2档", "gram": 24, "priceCny": 818.0,
         "time": "2026-09-22T02:00:00.000Z", "note": "", "gear": "第2档"},
    ],
})


def open_plan(page):
    page.click('.tabbar button[data-tab="plan"]')
    page.wait_for_timeout(250)


def gears(page):
    return page.evaluate("""() => [...document.querySelectorAll('#ladder .lv')].map(l => ({
        name: l.querySelector('.nm').textContent.trim(),
        price: parseFloat(l.querySelector('.price').textContent.replace(/,/g, '')),
        pills: [...l.querySelectorAll('.r2 .pill')].map(p => p.textContent.trim()),
        gpill: (l.querySelectorAll('.r2 .pill.g')[0] || {}).textContent || '',
        undo: !!l.querySelector('[data-ungear]'),
        warn: l.querySelector('.nm').textContent.indexOf('预警') >= 0,
    }))""")


def g(rows, name):
    for r in rows:
        if r["name"] == name:
            return r
    raise AssertionError("找不到档位 " + name + "：" + json.dumps(rows, ensure_ascii=False))


def accept_dialogs(page, log=None):
    def on_d(d):
        if log is not None:
            log.append(d.message)
        d.accept()
    page.on("dialog", on_d)


def bought_grams(page, name):
    return page.evaluate("(n) => window.__xt.gearBought(n)", name)


def test_g1_done_gear_shows_bought_gram(make_env):
    env = make_env(CFG)
    page = env.page
    open_plan(page)
    rows = gears(page)
    first = g(rows, "第1档")
    assert "已买入" in first["pills"], first
    assert "已买 16g" in first["gpill"], first
    second = g(rows, "第2档")
    assert "已买入" in second["pills"]
    assert "已买 24g" in second["gpill"]
    # 两档都带撤销入口
    assert first["undo"] and second["undo"]


def test_g2_sell_keeps_done_mark(make_env):
    """减仓 24 克后，第2档仍应是已买入——这是用户报的核心问题。"""
    env = make_env(CFG)
    page = env.page
    open_plan(page)
    assert "已买入" in g(gears(page), "第2档")["pills"]

    page.evaluate("""() => {
      const S = window.__xt.state();
      S.cfg.sells = (S.cfg.sells || []).concat([{
        id: 's1700000009000', name: '高位减仓', gram: 24, priceCny: 960,
        time: '2026-10-20T02:00:00.000Z', note: '' }]);
      window.__xt.compute(); window.__xt.refreshAll();
    }""")
    page.wait_for_timeout(200)

    rows = gears(page)
    second = g(rows, "第2档")
    assert "已买入" in second["pills"], second
    assert "已买 24g" in second["gpill"]
    assert page.evaluate("() => window.__xt.holdStats().grams") == 16


def test_g3_undo_clears_mark_and_reallocs(make_env):
    env = make_env(CFG)
    page = env.page
    open_plan(page)
    accept_dialogs(page)

    before = g(gears(page), "第2档")
    assert "已买入" in before["pills"]

    page.click('#ladder [data-ungear="第2档"]')
    page.wait_for_timeout(400)

    after = g(gears(page), "第2档")
    assert "已买入" not in after["pills"], after
    assert after["undo"] is False
    # 回到待买，且克重按额度重新分配（> 0）
    gp = after["gpill"]
    assert gp.startswith("待买"), gp
    num = float(gp.replace("待买", "").replace("g", "").strip())
    assert num > 0
    # 第1档不受影响
    assert "已买入" in g(gears(page), "第1档")["pills"]


def test_g4_undo_allows_rebuy_when_price_returns(make_env):
    """撤销后价格落回该档，应回到「刚到价」并给出待买克重。"""
    env = make_env(CFG)
    page = env.page
    open_plan(page)
    accept_dialogs(page)

    # 第2档 4128，现价 4072 在它下方 —— 先把价格抬到 4135 制造「已跌破/已过」
    page.evaluate("(p) => window.__xt.tick(p)", 4135)
    page.wait_for_timeout(200)
    assert "已买入" in g(gears(page), "第2档")["pills"]

    page.click('#ladder [data-ungear="第2档"]')
    page.wait_for_timeout(300)

    # 价格回落到第2档下方（4120 < 4128）→ 刚到价
    page.evaluate("(p) => window.__xt.tick(p)", 4120)
    page.wait_for_timeout(200)
    row = g(gears(page), "第2档")
    assert "刚到价" in row["pills"], row
    assert row["gpill"].startswith("待买"), row

    # 待办里也重新出现这一档的买入提示（总览页才渲染待办）
    page.click('.tabbar button[data-tab="ov"]')
    page.wait_for_timeout(300)
    todo = page.evaluate("""() => [...document.querySelectorAll('#todoList .tic')]
        .map(e => e.textContent.replace(/\\s+/g, ' ').trim())""")
    assert any("第2档" in t for t in todo), todo


def test_g5_undo_survives_reload(make_env):
    env = make_env(CFG)
    page = env.page
    open_plan(page)
    accept_dialogs(page)
    page.click('#ladder [data-ungear="第2档"]')
    page.wait_for_timeout(1500)

    # 落库后再打开
    page.goto(BASE + "/index.html?xt=1", wait_until="domcontentloaded")
    page.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    page.wait_for_timeout(600)
    open_plan(page)
    rows = gears(page)
    assert "已买入" not in g(rows, "第2档")["pills"], g(rows, "第2档")
    assert "已买入" in g(rows, "第1档")["pills"]
    cleared = page.evaluate("() => window.__xt.state().cfg.gearCleared")
    assert "第2档" in cleared and cleared["第2档"] > 0
    # 墓碑挡住了旧买入记录的反推
    assert bought_grams(page, "第2档") == 0
    assert bought_grams(page, "第1档") == 16


def test_g6_rebuy_after_undo_marks_again(make_env):
    env = make_env(CFG)
    page = env.page
    open_plan(page)
    accept_dialogs(page)
    page.click('#ladder [data-ungear="第2档"]')
    page.wait_for_timeout(300)

    # 点该档打开弹层，直接用预填的待买克重记一笔
    page.click('#ladder .body[data-gear="第2档"]')
    page.wait_for_timeout(250)
    assert page.eval_on_selector("#sheet", "e => e.classList.contains('on')") is True
    gram = page.eval_on_selector('input[name="g_gram"]', "e => e.value")
    assert float(gram) > 0
    page.click("#btnSave")
    page.wait_for_timeout(400)

    row = g(gears(page), "第2档")
    assert "已买入" in row["pills"], row
    assert "已买" in row["gpill"]
    assert bought_grams(page, "第2档") > 0


def test_g7_recalc_follows_holding(make_env):
    """重算档位：按持仓从第1档往上填。"""
    env = make_env(CFG)
    page = env.page
    open_plan(page)
    log = []
    accept_dialogs(page, log)

    page.click("#btnRecalc")
    page.wait_for_timeout(400)
    assert log and "重排" in log[0]

    rows = gears(page)
    # 持仓 40 克：第1档 16 + 第2档 24，刚好两档
    assert "已买入" in g(rows, "第1档")["pills"]
    assert "已买入" in g(rows, "第2档")["pills"]
    assert "已买入" not in g(rows, "第3档")["pills"]

    # 减到 10 克再重算 → 只剩第1档
    page.evaluate("""() => {
      const S = window.__xt.state();
      S.cfg.sells = (S.cfg.sells || []).concat([{
        id: 's1700000009100', name: '大减', gram: 30, priceCny: 980,
        time: '2026-10-21T02:00:00.000Z', note: '' }]);
      window.__xt.compute(); window.__xt.refreshAll();
    }""")
    page.wait_for_timeout(200)
    page.click("#btnRecalc")
    page.wait_for_timeout(400)

    rows = gears(page)
    assert "已买入" in g(rows, "第1档")["pills"]
    assert "已买入" not in g(rows, "第2档")["pills"], g(rows, "第2档")


def test_g8_recalc_writes_tombstones(make_env):
    env = make_env(CFG)
    page = env.page
    open_plan(page)
    accept_dialogs(page)
    page.click("#btnRecalc")
    page.wait_for_timeout(1500)

    cleared = page.evaluate("() => window.__xt.state().cfg.gearCleared")
    assert len(cleared) >= 6, cleared
    allv = list(cleared.values())
    assert all(v > 0 for v in allv)
    # 重排只认标记表，旧买入记录不再反推
    assert bought_grams(page, "第1档") == 0
    assert page.evaluate("() => (window.__xt.state().cfg.gearsDone || []).length") == 2


def test_g9_nonempty_when_only_tombstones(make_env):
    env = make_env(CFG)
    page = env.page
    assert page.evaluate("() => window.__xt.isEmptyCfg({gearCleared: {'第2档': 1761000000000}})") is False
    assert page.evaluate("() => window.__xt.isEmptyCfg({gearCleared: {}})") is True


def test_g10_cleared_saved_to_cloud(make_env):
    env = make_env(CFG)
    page = env.page
    open_plan(page)
    accept_dialogs(page)
    page.click('#ladder [data-ungear="第2档"]')
    page.wait_for_timeout(1600)
    body = env.gh.cfg or {}
    assert "第2档" in (body.get("gearCleared") or {}), body.get("gearCleared")
    assert "第2档" not in (body.get("gearsDone") or []), body.get("gearsDone")


def test_g11_undo_keeps_records(make_env):
    env = make_env(CFG)
    page = env.page
    open_plan(page)
    accept_dialogs(page)
    page.click('#ladder [data-ungear="第2档"]')
    page.wait_for_timeout(400)
    st = page.evaluate("() => window.__xt.state()")
    assert len(st["cfg"]["buys"]) == 2
    assert len(st["cfg"]["sells"]) == 0
    assert page.evaluate("() => window.__xt.holdStats().grams") == 40


def test_g12_undo_scope_is_single_gear(make_env):
    """撤销只改变这一档的标记；其余五档的克重一分不动（v6.18 A 方案）。

    v6.18 起计划值恒定：撤销后第 1 档回到「待补 16g」，差额留在本档，
    不再像旧版那样收回池子重分给其余档 —— 所以「待补合计」不等于「剩余额度」，
    它等于「总仓 − 仍标记的克重」。
    """
    env = make_env(CFG)
    page = env.page
    open_plan(page)
    accept_dialogs(page)
    before = {r["name"]: r for r in gears(page)}
    b_rows = {r["name"]: r for r in page.evaluate("() => window.__xt.gearRows()")}
    page.click('#ladder [data-ungear="第1档"]')
    page.wait_for_timeout(400)
    after = {r["name"]: r for r in gears(page)}
    a_rows = {r["name"]: r for r in page.evaluate("() => window.__xt.gearRows()")}

    assert "已买入" not in after["第1档"]["pills"]
    assert a_rows["第1档"]["boughtG"] == 0
    assert a_rows["第1档"]["pending"] == pytest.approx(16, abs=0.01)

    for name in ("第2档", "第3档", "第4档", "第5档", "第6档"):
        assert after[name]["pills"][:2] == before[name]["pills"][:2], (name, before[name], after[name])
        assert after[name]["undo"] == before[name]["undo"], name
        # 计划值、待补值逐档不变 —— 撤销不外溢
        assert a_rows[name]["plan"] == pytest.approx(b_rows[name]["plan"])
        assert a_rows[name]["pending"] == pytest.approx(b_rows[name]["pending"]), name

    assert a_rows["第2档"]["boughtG"] == pytest.approx(24, abs=0.01)
    total = sum(r["pending"] for r in a_rows.values() if not r["warn"])
    assert total == pytest.approx(200 - 24, abs=0.05)


def test_g13_undo_target_is_tappable(make_env):
    env = make_env(CFG)
    page = env.page
    open_plan(page)
    box = page.eval_on_selector('#ladder [data-ungear="第2档"]', """e => {
        const r = e.getBoundingClientRect();
        const cs = getComputedStyle(e);
        return {w: Math.round(r.width), h: Math.round(r.height), cursor: cs.cursor};
    }""")
    assert box["cursor"] == "pointer", box
    # 热区靠 ::after 撑开，视觉标签本身不小于 16px 高
    assert box["h"] >= 16, box


@pytest.mark.parametrize("width", [320, 390])
def test_g14_plan_page_fits(make_env, width):
    env = make_env(CFG, width=width, height=780)
    page = env.page
    open_plan(page)
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    iw = page.evaluate("() => window.innerWidth")
    assert sw <= iw + 1, (sw, iw)
    tops = page.evaluate("""() => [...document.querySelectorAll('.sact .act')]
        .map(e => Math.round(e.getBoundingClientRect().top))""")
    assert len(tops) == 2 and len(set(tops)) == 1, tops
