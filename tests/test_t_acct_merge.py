# -*- coding: utf-8 -*-
"""账户改名 / 改归属在「云端合并」之后必须还在。

这两条都是用户报的：
1. 持仓页把「默认账户」改名，保存后过一会儿又变回旧名字；
2. 买入记录点进去改归属账户，保存后还是原账户。

根因不在表单，在 mergeRemote：同名 id 的记录是「远端先到先得」，
本地改过的对象被直接丢掉。所以断言必须等 merge 跑完（saveCfg → 800ms → PUT），
只看保存那一瞬间的 state 会假通过。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conftest import CFG_EMPTY

A0 = {"id": "a0", "name": "默认账户", "note": "", "color": "#D4A24C", "at": 1}
A1 = {"id": "a1", "name": "招行积存金", "note": "", "color": "#5B8FF9", "at": 2}


def cfg():
    c = dict(CFG_EMPTY, H_BASE=4700.0, TOTAL=200.0, alpha=2.44,
             schemaVersion=3, migratedV7=True,
             accounts=[dict(A0), dict(A1)], acctLast="a0", comeback=10,
             buys=[{"id": "b1", "name": "首笔建仓", "gram": 20, "priceCny": 905.0,
                    "time": "2026-08-12T02:00:00.000Z", "note": "", "gear": None, "acct": "a0"}],
             sells=[])
    return c


def cfg_two_pos():
    """两个账户各有 20 克：减仓才有资格从 a0 改挂到 a1（余额校验按目标账户算）。"""
    c = cfg()
    c["buys"] = [
        {"id": "b1", "name": "首笔建仓", "gram": 20, "priceCny": 905.0,
         "time": "2026-08-12T02:00:00.000Z", "note": "", "gear": None, "acct": "a0"},
        {"id": "b2", "name": "工行建仓", "gram": 20, "priceCny": 905.0,
         "time": "2026-08-13T02:00:00.000Z", "note": "", "gear": None, "acct": "a1"},
    ]
    c["sells"] = [{"id": "s1", "name": "减仓", "gram": 5, "priceCny": 950.0,
                   "time": "2026-09-20T02:00:00.000Z", "note": "", "acct": "a0"}]
    return c


def to_pos(p):
    p.evaluate("""() => {
        if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
        window.scrollTo(0, 0);
    }""")
    p.wait_for_timeout(250)
    p.click('.tabbar button[data-tab="pos"]')
    p.wait_for_timeout(400)


def accts(p):
    return p.evaluate("() => JSON.parse(JSON.stringify(window.__xt.state().cfg.accounts || []))")


def buys(p):
    return p.evaluate("() => JSON.parse(JSON.stringify(window.__xt.state().cfg.buys || []))")


def test_a1_rename_default_account_survives_merge(make_env):
    e = make_env(cfg=cfg())
    p = e.page
    to_pos(p)

    p.evaluate("() => window.__xt.openAcctMgr()")
    p.wait_for_timeout(200)
    p.click('[data-edit="a0"]')
    p.fill("#acName", "招行积存金改名")
    p.click("#acSave")
    p.wait_for_timeout(2200)          # 800ms 防抖 + 一次 GET/PUT 合并

    names = [a["name"] for a in accts(p)]
    assert "招行积存金改名" in names, names
    # 云端也必须落这个新名字，否则另一台设备拉下去还是旧的
    assert any(a["name"] == "招行积存金改名" for a in e.gh.cfg["accounts"]), e.gh.cfg["accounts"]
    # 列表里能看见（不是只改了内存）
    assert "招行积存金改名" in p.inner_text("#acctListBox")


def test_a2_buy_acct_change_survives_merge(make_env):
    e = make_env(cfg=cfg())
    p = e.page
    to_pos(p)

    p.click('.tabbar button[data-tab="pos"]')
    p.wait_for_timeout(300)
    p.click("#buyList [data-buy='b1']")
    p.wait_for_timeout(300)
    p.select_option("#gAcct", "a1")
    p.click("#btnSave")
    p.wait_for_timeout(2200)

    rec = [b for b in buys(p) if b["id"] == "b1"][0]
    assert rec["acct"] == "a1", rec
    remote = [b for b in e.gh.cfg["buys"] if b["id"] == "b1"][0]
    assert remote["acct"] == "a1", remote
    # 持仓归属真的换了：招行积存金 20g，默认账户 0g
    g = p.evaluate("(a) => window.__xt.holdStats(a).grams", "a1")
    assert abs(g - 20) < 1e-6, g


def test_a3_sell_acct_change_survives_merge(make_env):
    """减仓记录改归属账户走的是另一条分支（saveSell），也要打 up。"""
    e = make_env(cfg=cfg_two_pos())
    p = e.page
    to_pos(p)

    p.click("#buyList [data-sellid='s1']")
    p.wait_for_timeout(300)
    p.select_option("#gAcct", "a1")
    p.click("#btnSave")
    p.wait_for_timeout(2200)

    rec = [s for s in p.evaluate(
        "() => JSON.parse(JSON.stringify(window.__xt.state().cfg.sells || []))") if s["id"] == "s1"][0]
    assert rec["acct"] == "a1", rec
    remote = [s for s in e.gh.cfg["sells"] if s["id"] == "s1"][0]
    assert remote["acct"] == "a1", remote
