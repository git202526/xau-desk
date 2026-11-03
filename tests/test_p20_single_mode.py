"""P20 · 单模式合一：档位只认 gearMarks，账本数据一次性折成标记。

合并前是两套取数：计划模式读标记，账本模式按 buys[] 聚合 + gearsDone 兜底。
合并后只剩一套，老数据靠 migrateMarksV7() 折过来（只写标记，不改记录）。

批一 m1–m8：取数统一与迁移
批二 n1–n9：入口开放
批三 o1–o5：均价口径按持仓判定
批四 p1–p4：兜底开放与死代码清理
"""
import math

import pytest

from conftest import CFG_EMPTY

H = 4700.0
TOTAL = 200.0
PX = 4030.0
PLAN = [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]      # 权重 8:12:24:36:12:8


def bars_calm(n=240, px=PX, amp=24.0):
    now = 1761696000000
    step = 24 * 3600 * 1000
    raw = [px * 0.93 + i * (px * 0.07 / (n - 1)) + math.sin(i / 9.0) * 11 for i in range(n)]
    adj = raw[-1] - px
    out = []
    for i in range(n):
        c = raw[i] - adj
        t = int(now - (n - 1 - i) * step)
        out.append([t, "%.2f" % (c - 4), "%.2f" % (c + amp), "%.2f" % (c - amp),
                    "%.2f" % c, "12.5", t + step - 1, "0", 0, "0", "0", "0"])
    return out


def buy(id_, gram, gear=None, price=900.0):
    return {"id": id_, "name": gear or "买入", "gram": gram, "priceCny": price,
            "time": "2026-09-01T02:00:00.000Z", "note": "", "gear": gear}


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, swingGears=[1, 2])
    c.update(kw)
    c["bars"] = bars_calm()
    return c


@pytest.fixture
def env(make_env):
    def _f(price=PX, **kw):
        return make_env(cfg=cfg(**kw), price=price)
    return _f


def st(p, i):
    return p.evaluate("(i) => window.__xt.gearState(i)", i)


def nets(p, n=6):
    return p.evaluate("(n) => Array.from({length:n},(_,k)=>window.__xt.gearState(k+1).bought)", n)


def pends(p, n=6):
    return p.evaluate("(n) => Array.from({length:n},(_,k)=>window.__xt.gearState(k+1).pending)", n)


def marks(p):
    return p.evaluate("() => JSON.parse(JSON.stringify(window.__xt.state().cfg.gearMarks || {}))")


# ---------- 批一：迁移 ----------

def test_m1_buys_with_gear_become_marks(env):
    """账本里 48g 记在第 2 档 → 迁移后第 2 档净持按记录克重（不是计划克重）。"""
    e = env(buys=[buy("b1", 48, "第2档")])
    p = e.page
    assert st(p, 2)["bought"] == pytest.approx(48, abs=0.05)
    assert st(p, 2)["pending"] == pytest.approx(0, abs=0.05)
    assert st(p, 2)["over"] == pytest.approx(24, abs=0.05)
    assert st(p, 1)["bought"] == pytest.approx(0, abs=0.05)
    assert st(p, 1)["pending"] == pytest.approx(16, abs=0.05)


def test_m2_gears_done_only_fills_plan_gram(env):
    """只有 gearsDone 名单 → 迁移后该档净持 = 计划克重。"""
    e = env(gearsDone=["第3档"])
    p = e.page
    assert st(p, 3)["bought"] == pytest.approx(48, abs=0.05)
    assert st(p, 3)["pending"] == pytest.approx(0, abs=0.05)


def test_m3_existing_mark_not_overwritten(env):
    """既有标记又有 gearsDone → 标记不被覆盖，也不带 migrated 字段。"""
    e = env(gearsDone=["第3档"],
            gearMarks={"3": {"g": 30, "cost": 120000, "sold": 0, "proceeds": 0, "at": 1}})
    p = e.page
    mk = marks(p)
    assert mk["3"]["g"] == pytest.approx(30)
    assert "migrated" not in mk["3"]
    assert st(p, 3)["bought"] == pytest.approx(30)


def test_m4_migration_is_idempotent(env):
    """迁移跑两次，gearMarks 逐字节不变（第二次不写任何东西）。"""
    e = env(gearsDone=["第3档", "第4档"], buys=[buy("b1", 10, "第1档")])
    p = e.page
    # 带档记录的成交价要折美元，迁移会等汇率；等它真正跑完再取基线
    p.wait_for_function("() => window.__xt.state().fx > 0", timeout=8000)
    p.wait_for_function("() => window.__xt.state().cfg.migratedV7 === true", timeout=8000)
    before = marks(p)
    again = p.evaluate("""() => {
      var c = window.__xt.state().cfg;
      c.migratedV7 = false;
      var changed = window.__xt.migrateMarksV7();
      return { changed: changed, marks: JSON.parse(JSON.stringify(c.gearMarks || {})) };
    }""")
    assert again["changed"] is False
    assert again["marks"] == before


def test_m5_fresh_user_no_error(env):
    """全新用户：迁移不报错，档位全待补，标记仍为空。"""
    e = env()
    p = e.page
    assert p.evaluate("() => window.__xt.state().cfg.migratedV7") is True
    assert marks(p) == {}
    assert [round(x, 1) for x in pends(p)] == PLAN


def test_m6_net_formula(env):
    """净持 = g − sold：标 16g、卖 8g → 净持 8g、待补 8g。"""
    e = env(gearMarks={"1": {"g": 16, "cost": 0, "sold": 8, "proceeds": 30000, "at": 1}})
    p = e.page
    s = st(p, 1)
    assert s["bought"] == pytest.approx(8, abs=0.05)
    assert s["pending"] == pytest.approx(8, abs=0.05)
    assert s["over"] == pytest.approx(0, abs=0.05)


def test_m7_bad_gear_name_skipped(env):
    """gearsDone 里的非法档名跳过，不产生空标记。"""
    e = env(gearsDone=["第9档", "第0档", "第2档"])
    p = e.page
    mk = marks(p)
    assert list(mk.keys()) == ["2"]
    assert mk["2"]["g"] == pytest.approx(24)


def test_m8_buy_record_writes_back_to_gear(env):
    """记账带档 → 档位净持 = 该档累计买入（两笔累加）。"""
    e = env(buys=[buy("b1", 10, "第4档")])
    p = e.page
    p.evaluate("""() => {
      var c = window.__xt.state().cfg;
      c.buys.push({id:'b2', name:'第4档', gram:26, priceCny:905,
                   time:'2026-09-20T02:00:00.000Z', note:'', gear:'第4档'});
      window.__xt.syncMarksFromBuys();
      window.__xt.compute(); window.__xt.refreshAll();
    }""")
    assert st(p, 4)["bought"] == pytest.approx(36, abs=0.05)
    # 计划 72g，待补 36g
    assert st(p, 4)["pending"] == pytest.approx(36, abs=0.05)


# ---------- 批二：入口开放 ----------

def row_info(p):
    return p.evaluate("""() => [...document.querySelectorAll('#ladder .lv')].map(l => ({
        name: (l.querySelector('.nm') || {}).textContent || '',
        g: ((l.querySelector('.r2 .pill.g') || {}).textContent || ''),
        btns: [...l.querySelectorAll('.r2 .mkbtn')].map(b => b.textContent.trim()),
        markDone: l.classList.contains('markDone'),
    }))""")


def test_n1_every_gear_has_action_button(env):
    """每一档都有操作按钮（不再受模式限制）；循环档有净持时多一枚「卖出」。"""
    e = env(gearMarks={"1": {"g": 16, "cost": 0, "sold": 0, "proceeds": 0, "at": 1}})
    p = e.page
    rows = [r for r in row_info(p) if r["g"] != "只预警"]
    assert len(rows) == 6
    for r in rows:
        assert r["btns"], r
    assert "买入" in rows[0]["btns"] and "卖出" in rows[0]["btns"], rows[0]
    assert "标记已买" in rows[2]["btns"], rows[2]
    # 已标记的建仓档给「撤销」，且整行带 markDone
    assert rows[0]["markDone"] is True


def test_n2_buy_sheet_has_gear_link(env):
    """买入情境：关联档位下拉可见，含 6 档 + 「不关联」。"""
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.openSheet('')")
    p.wait_for_timeout(200)
    assert p.evaluate("() => getComputedStyle(document.getElementById('gearLinkFld')).display") != "none"
    opts = p.evaluate("() => [...document.querySelectorAll('#gGear option')].map(o => o.value)")
    assert opts[0] == ""
    assert len(opts) == 7
    assert opts[1] == "第1档" and opts[6] == "第6档"


def test_n3_sell_sheet_has_gear_link(env):
    """卖出情境：关联档位同样可见（本期改动点），默认落在「不关联」。"""
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.openSell(null)")
    p.wait_for_timeout(200)
    assert p.evaluate("() => getComputedStyle(document.getElementById('gearLinkFld')).display") != "none"
    assert p.evaluate("() => document.getElementById('gGear').value") == ""
    assert p.evaluate("() => document.querySelectorAll('#gGear option').length") == 7


def test_n4_swing_gear_can_sell(env):
    """循环档可以卖出，扣减后净持减少。"""
    e = env(gearMarks={"1": {"g": 16, "cost": 0, "sold": 0, "proceeds": 0, "at": 1}})
    p = e.page
    assert p.evaluate("() => window.__xt.markSell(1, 8, 4100)") is True
    assert st(p, 1)["bought"] == pytest.approx(8, abs=0.05)
    assert st(p, 1)["soldG"] == pytest.approx(8, abs=0.05)


def test_n5_build_gear_sell_rejected(env):
    """建仓档仍被拒绝 —— 这是业务规则，不是模式限制。"""
    e = env(gearMarks={"3": {"g": 48, "cost": 0, "sold": 0, "proceeds": 0, "at": 1}})
    p = e.page
    assert p.evaluate("() => window.__xt.markSell(3, 8, 4100)") is False
    assert st(p, 3)["bought"] == pytest.approx(48, abs=0.05)


def test_n6_plan_switch_removed(env):
    """设置页不再有「计划模式」开关。"""
    e = env()
    p = e.page
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(220)
    assert p.evaluate("() => document.getElementById('setPlanOnly')") is None
    assert "计划模式" not in p.evaluate("() => document.getElementById('panel-set').innerText")


def test_n7_old_cfg_with_planonly(env):
    """老配置含 planOnly:true → 启动不报错，行为与新用户一致（认标记）。"""
    e = env(planOnly=True, gearMarks={"2": {"g": 24, "cost": 0, "sold": 0, "proceeds": 0, "at": 1}})
    p = e.page
    assert p.evaluate("() => 'planOnly' in window.__xt.state().cfg") is False
    assert st(p, 2)["bought"] == pytest.approx(24, abs=0.05)
    assert st(p, 1)["pending"] == pytest.approx(16, abs=0.05)


def test_n8_title_row_empty(env):
    """正常态标题行为空，不含高点 / 手动 / 待补（P22 契约延续）。"""
    e = env()
    p = e.page
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(260)
    meta = p.evaluate("() => document.getElementById('ladderMeta').textContent.trim()")
    assert meta == ""


def test_n9_over_budget_tag(env):
    """超买 → 标题出现「已超预算 10g」（不需要任何模式开关）。"""
    e = env(gearMarks={"3": {"g": 58, "cost": 0, "sold": 0, "proceeds": 0, "at": 1}})
    p = e.page
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(260)
    meta = p.evaluate("() => document.getElementById('ladderMeta').textContent")
    assert "已超预算 10g" in meta, meta
    assert p.evaluate("() => !!document.querySelector('#ladderMeta .mtag.warn')") is True


# ---------- 批三：均价口径按持仓判定 ----------

def test_o1_held_fold_hits_target(env):
    """有 100g 持仓 + 目标均价 → 折算后的剩余均价让「持仓 + 补满」整体命中 T。"""
    e = env(buys=[buy("b1", 100, None, 900.0)])
    p = e.page
    p.evaluate("() => window.__xt.setTargetT(3900)")
    got = p.evaluate("() => window.__xt.gearBase().targetEff")
    want = p.evaluate("""() => {
      var hs = window.__xt.holdStats();
      var usd = window.__xt.cnyToUsd(hs.avg);
      return (3900 * 200 - usd * hs.grams) / (200 - hs.grams);
    }""")
    assert got == pytest.approx(want, abs=0.5)
    # 剩余均价明显低于目标价：手上有便宜的货，剩下的钱要少花
    assert got < 3900


def test_o2_no_hold_no_fold(env):
    """无持仓 → 不做折算，targetEff 就是 T。"""
    e = env()
    p = e.page
    p.evaluate("() => window.__xt.setTargetT(3900)")
    assert p.evaluate("() => window.__xt.gearBase().targetEff") == pytest.approx(3900, abs=0.01)


def test_o3_no_target_no_error(env):
    """有持仓但没有目标均价 → 不折算、不报错。"""
    e = env(buys=[buy("b1", 60, None, 900.0)])
    p = e.page
    b = p.evaluate("() => window.__xt.gearBase()")
    assert b["targetEff"] is None
    assert b["he"] > 0


def test_o4_dual_avg_relation_kept(env):
    """P10 双口径：锚定均价（只算前 k 档）≥ 全档试算均价。"""
    e = env(buys=[buy("b1", 60, None, 900.0)])
    p = e.page
    p.evaluate("() => window.__xt.setTargetT(3900)")
    m = p.evaluate("() => window.__xt.gearMeta()")
    assert m["avgAnchor"] >= m["avgAll"] - 1e-6, m
    assert m["anchorK"] >= 1


def test_o5_tiny_hold_threshold(env):
    """折算阈值是 0.01g：低于它视为空仓（一分不折），略高于它照折但影响极小。

    计划书里写的是「0.5g 视为无持仓」，实测阈值是 `heldG > 0.01`，
    0.5g 会参与折算。这里按代码的真实阈值锁两条，不为了迁就文档改实现。
    """
    e0 = env(buys=[buy("b1", 0.005, None, 900.0)])
    p0 = e0.page
    p0.evaluate("() => window.__xt.setTargetT(3900)")
    assert p0.evaluate("() => window.__xt.gearBase().targetEff") == pytest.approx(3900, abs=0.01)

    e1 = env(buys=[buy("b1", 0.5, None, 900.0)])
    p1 = e1.page
    p1.evaluate("() => window.__xt.setTargetT(3900)")
    eff = p1.evaluate("() => window.__xt.gearBase().targetEff")
    assert abs(eff - 3900) < 1.0, eff       # 折了，但半个克影响不到 1 美元


# ---------- 批四：兜底开放与死代码清理 ----------

DRIP = {"days": 30, "pct": 0.5, "prem": 3.0}


def drip_cfg(**kw):
    old = int(__import__("time").time() * 1000) - 40 * 86400 * 1000
    c = cfg(dripDays=DRIP["days"], dripPct=DRIP["pct"], dripMaxPremium=DRIP["prem"],
            dripAt={"3": old})
    c.update(kw)
    return c


def test_p1_drip_prompts_with_records(make_env):
    """记录带档的数据也参与兜底：第 3 档成队首且到期 → 出「按现价买」按钮。"""
    p = make_env(cfg=drip_cfg(buys=[buy("b1", 16, "第1档")]), price=PX).page
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(300)
    info = p.evaluate("() => window.__xt.dripInfo(3)")
    assert info["canPrompt"] is True, info
    assert p.evaluate("() => !!document.querySelector('#ladder [data-dripgo]')") is True


def test_p2_drip_prompts_with_marks(make_env):
    """纯标记数据同样能触发兜底。"""
    p = make_env(cfg=drip_cfg(gearMarks={"1": {"g": 16, "cost": 0, "sold": 0, "proceeds": 0, "at": 1}}),
                 price=PX).page
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(300)
    assert p.evaluate("() => window.__xt.dripInfo(3).canPrompt") is True
    assert p.evaluate("() => !!document.querySelector('#ladder [data-dripgo]')") is True


def test_p3_plan_summary_fields(env):
    """planSummary 恰好 7 个字段，且都不为 undefined。"""
    e = env()
    s = e.page.evaluate("() => window.__xt.planSummary()")
    assert sorted(s.keys()) == ["avgBought", "bought", "budget", "done", "overBudget",
                                "pending", "planned"]
    # 七个字段都必须存在；avgBought 在没买过时按设计是 null（不是 undefined）
    undef = e.page.evaluate("() => Object.keys(window.__xt.planSummary())"
                            ".filter(k => window.__xt.planSummary()[k] === undefined)")
    assert undef == []
    assert s["avgBought"] is None and s["bought"] == 0


def test_p4_dead_code_gone():
    """三个无引用函数已删除：主文件里检索为 0 处。"""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "index.html"), encoding="utf-8").read()
    for name in ("function dirtyItems", "function openMtFold", "function usdToCny"):
        assert name not in src, name
