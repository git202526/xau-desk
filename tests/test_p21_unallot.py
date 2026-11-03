"""P21 · 摊派回滚：卖出记录带分摊快照，改克重 / 删记录自动退回档位。

P16 的摊派只写一次：`gearMarks.sold` 加上去就没人撤了。删掉那笔减仓记录，
档位的卖出量还虚挂在那里 —— 净持偏小、待补偏大，整张表都是错的。

本期把「这次摊到哪几档、各多少克」记进记录本身（`allot`），改动时按快照精确退回；
老记录没有快照，就按当前各档 sold 占比近似退，总量仍然精确。
"""
import math

import pytest

from conftest import CFG_EMPTY

H = 4700.0
TOTAL = 200.0
PX = 4030.0
BASE = [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]


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


def marks(grams=None):
    gs = list(grams) if grams else list(BASE)
    out = {}
    for i, g in enumerate(gs, 1):
        if g <= 0:
            continue
        out[str(i)] = {"g": g, "cost": round(g * 4000 / 31.1035, 2), "sold": 0,
                       "proceeds": 0, "at": 1}
    return out


def cfg(**kw):
    # 账面上要真有 200g：卖出校验用的是账本持仓（标记层只是执行进度），
    # 只有标记没有记录时「减仓」会被「超过剩余持仓 0 克」挡掉。
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, swingGears=[1, 2], gearMarks=marks(),
             buys=[{"id": "b0", "name": "底仓", "gram": 200, "priceCny": 900,
                    "time": "2026-08-01T02:00:00.000Z", "note": "", "gear": None}])
    c.update(kw)
    c["bars"] = bars_calm()
    return c


@pytest.fixture
def env(make_env):
    def _f(gm=None, **kw):
        c = cfg(**kw)
        if gm is not None:
            c["gearMarks"] = gm
        return make_env(cfg=c, price=PX)
    return _f


def sold(p):
    return p.evaluate("() => Array.from({length:6},(_,k)=>window.__xt.gearState(k+1).soldG)")


def nets(p):
    return p.evaluate("() => Array.from({length:6},(_,k)=>window.__xt.gearState(k+1).bought)")


def recs(p):
    return p.evaluate("() => JSON.parse(JSON.stringify(window.__xt.state().cfg.sells || []))")


def marks_raw(p):
    return p.evaluate("() => JSON.parse(JSON.stringify(window.__xt.state().cfg.gearMarks || {}))")


def _wait_sold(p, gram, timeout=4000):
    """等逐档分摊落到目标克重，替代固定 280ms，返回是否达标。

    固定时长在并行（xdist 多进程抢 CPU）下会不够，断言在渲染完成前就跑了，
    表现为「assert 100 == 80」这类假失败。改成条件等待后与负载无关。
    """
    try:
        p.wait_for_function(
            "g => Math.abs(Array.from({length:6},(_,k)=>window.__xt.gearState(k+1).soldG)"
            ".reduce((a,b)=>a+b,0) - g) < 0.2",
            arg=float(gram), timeout=timeout)
        return True
    except Exception:
        return False  # 真不达标时留给断言层报出具体数值


def _fill_gram(p, gram):
    """填克重并回读确认。行情刷新重渲染弹层会把刚填的值写回原值，
    那时保存的是原值（100 而不是 80），所以填完必须核对一遍。"""
    for _ in range(3):
        p.fill('[name="g_gram"]', str(gram))
        p.wait_for_timeout(60)
        if p.input_value('[name="g_gram"]') == str(gram):
            return
    raise AssertionError("克重填不进去：%s" % p.input_value('[name="g_gram"]'))


def sell(p, gram, price=900):
    p.evaluate("() => window.__xt.openSell(null)")
    p.wait_for_timeout(150)
    _fill_gram(p, gram)
    p.fill('[name="g_price"]', str(price))
    p.click("#btnSave")
    _wait_sold(p, gram)


def edit_sell(p, gram):
    """改已存在的卖出记录。并行高负载下第一遍可能保存成原值，重试一次。"""
    for attempt in (0, 1):
        rid = p.evaluate("() => (window.__xt.state().cfg.sells || [])[0].id")
        p.evaluate("(id) => window.__xt.openSell(null, id)", rid)
        p.wait_for_timeout(150)
        _fill_gram(p, gram)
        p.click("#btnSave")
        if _wait_sold(p, gram, timeout=3000 if attempt == 0 else 4000):
            return
        p.wait_for_timeout(200)


def del_sell(p):
    rid = p.evaluate("() => (window.__xt.state().cfg.sells || [])[0].id")
    p.evaluate("(id) => window.__xt.openSell(null, id)", rid)
    p.wait_for_timeout(150)
    p.evaluate("() => { window.confirm = () => true; }")
    p.click("#btnDelBuy")
    p.wait_for_timeout(300)


# ---------- 批一：快照与反摊 ----------

def test_r1_record_has_allot(env):
    """卖 100g（六档已标）→ 记录带 allot，各档份额合计 = 100。"""
    p = env().page
    sell(p, 100)
    r = recs(p)[0]
    assert "allot" in r, r
    assert sum(r["allot"].values()) == pytest.approx(100, abs=0.1)
    assert len(r["allot"]) == 6
    # 卖 100 = 一半持仓，各档按原净持比例摊 → 正好是计划克重的一半
    assert [round(x, 1) for x in sold(p)] == [round(x / 2, 1) for x in BASE]
    # 净持 = g − sold，同样减半
    assert [round(x, 1) for x in nets(p)] == [round(x / 2, 1) for x in BASE]


def test_r2_delete_restores_sold(env):
    """删记录 → 六档 sold 全部回 0，净持回到卖出前。"""
    p = env().page
    before = nets(p)
    sell(p, 100)
    assert [round(x, 1) for x in nets(p)] != before
    del_sell(p)
    assert [round(x, 2) for x in sold(p)] == [0] * 6
    assert [round(x, 2) for x in nets(p)] == [round(x, 2) for x in before]
    assert recs(p) == []


def test_r3_edit_reallots(env):
    """卖 100 → 改 80：各档 sold 之和 = 80，且与直接卖 80 的分布一致。"""
    p1 = env().page
    sell(p1, 100)
    edit_sell(p1, 80)
    got = [round(x, 2) for x in sold(p1)]
    assert sum(got) == pytest.approx(80, abs=0.1), ("sum_got", got)
    # allot 也要跟着重算 —— 必须趁 p2 起来之前读：
    # p2 的建页会让 p1 多活几百毫秒，慢一步的云端 PUT 落库后 p1 会拉回上一版
    # （这是 mock 的写入乱序，不是产品行为），读晚了会看到卖 100 的旧 allot。
    allot = dict(recs(p1)[0].get("allot") or {})

    p2 = env().page
    sell(p2, 80)
    assert got == [round(x, 2) for x in sold(p2)], ("dist", got, sold(p2))
    assert sum(allot.values()) == pytest.approx(80, abs=0.1), ("allot", allot)


def test_r4_delete_then_recreate(env):
    """卖 100 → 删 → 重记 100：逐档与一次记 100 完全相同。"""
    p1 = env().page
    sell(p1, 100)
    del_sell(p1)
    sell(p1, 100)

    p2 = env().page
    sell(p2, 100)
    assert [round(x, 2) for x in sold(p1)] == [round(x, 2) for x in sold(p2)]
    assert len(recs(p1)) == 1


def test_r5_legacy_record_without_allot(env):
    """老记录（手工去掉 allot）删除：sold 总量减少 = 记录克重（±0.1）。"""
    p = env().page
    sell(p, 100)
    p.evaluate("""() => {
      var c = window.__xt.state().cfg;
      delete c.sells[0].allot;
      window.__xt.compute(); window.__xt.refreshAll();
    }""")
    assert sum(sold(p)) == pytest.approx(100, abs=0.1)
    del_sell(p)
    assert sum(sold(p)) == pytest.approx(0, abs=0.1)


def test_r6_sold_stays_in_range(env):
    """反摊后任何档 0 ≤ sold ≤ g，不会出现负数或超过买入量。"""
    p = env().page
    sell(p, 100)
    edit_sell(p, 40)
    del_sell(p)
    mk = marks_raw(p)
    for k, m in mk.items():
        assert 0 <= m["sold"] <= m["g"] + 1e-6, (k, m)


def test_r7_unallot_keeps_g_and_cost(env):
    """反摊只动 sold / proceeds，g 与 cost 一个都不变。"""
    p = env().page
    before = marks_raw(p)
    sell(p, 100)
    edit_sell(p, 60)
    after = marks_raw(p)
    for k in before:
        assert after[k]["g"] == pytest.approx(before[k]["g"])
        assert after[k]["cost"] == pytest.approx(before[k]["cost"])


def test_r8_price_change_keeps_split(env):
    """只改价格、克重不变：各档 sold 与 allot 都不动。"""
    p = env().page
    sell(p, 100)
    s0 = [round(x, 2) for x in sold(p)]
    a0 = recs(p)[0]["allot"]

    rid = p.evaluate("() => window.__xt.state().cfg.sells[0].id")
    p.evaluate("(id) => window.__xt.openSell(null, id)", rid)
    p.wait_for_timeout(150)
    p.fill('[name="g_price"]', "950")
    p.click("#btnSave")
    p.wait_for_timeout(280)

    assert [round(x, 2) for x in sold(p)] == s0
    assert recs(p)[0]["allot"] == a0


def test_r9_gear_count_shrunk(env):
    """8 档卖出后缩到 6 档再删记录：界内档精确退回，界外档号跳过，不报错。

    档号下限是 6（GCFG_MIN），所以只能「先多后少」制造越界键：8 档时记的 allot
    带着 7/8 号档，缩档后这两个档号已不在界内。setGearCount 的约定是「切回来还在」——
    界外标记不清洗，只是界面不显示 —— 所以 unallotSell 只退界内、跳过界外，
    关键是别抛异常、别把界内那几档退错。
    """
    gm = {}
    for i, g in enumerate([10, 16, 24, 32, 40, 32, 26, 20], 1):
        gm[str(i)] = {"g": g, "cost": round(g * 4000 / 31.1035, 2), "sold": 0,
                      "proceeds": 0, "at": 1}
    p = env(gm=gm, gearCount=8).page
    sell(p, 100)
    assert len(recs(p)[0]["allot"]) == 8

    p.evaluate("() => window.__xt.setGearCount(6)")
    p.wait_for_timeout(320)
    assert p.evaluate("() => window.__xt.gearCount()") == 6

    del_sell(p)
    assert recs(p) == []
    # 界内 1–6 档全部退回 0（卖掉的那一半被精确撤销）
    assert sum(sold(p)) == pytest.approx(0, abs=0.1)
    for k, m in marks_raw(p).items():
        assert 0 <= m["sold"] <= m["g"] + 1e-6, (k, m)
    # 页面还活着，状态可读 —— 说明没有抛异常
    assert p.evaluate("() => !!window.__xt.state().cfg.gearMarks") is True


def test_r10_one_record_per_sell(env):
    """账本列表仍是一笔卖出 —— 分摊不改记录的条数。"""
    p = env().page
    sell(p, 100)
    edit_sell(p, 70)
    assert len(recs(p)) == 1
    assert p.evaluate("() => window.__xt.holdStats().sells.length") == 1


# ---------- 批二：明细与文案 ----------

def rec_line(p):
    return p.evaluate("""() => {
      var t = document.querySelector('#buyList [data-sellid]');
      return t ? t.innerText.replace(/\\s+/g, ' ').trim() : null;
    }""")


def test_s1_allot_line_shown(env):
    """卖出记录出现「已摊入」，档数与 allot 键数一致。"""
    p = env().page
    sell(p, 100)
    p.click('.tabbar button[data-tab="pos"]')
    p.wait_for_timeout(280)
    txt = rec_line(p)
    assert txt and "已摊入" in txt, txt
    assert txt.count("第") == len(recs(p)[0]["allot"]), txt
    for nm in p.evaluate("() => window.__xt.gearSeq()"):
        assert nm in txt, (nm, txt)


def test_s2_delete_confirm_text(env):
    """删除确认文案提到「档位卖出量」。"""
    p = env().page
    sell(p, 100)
    msgs = []
    p.on("dialog", lambda d: (msgs.append(d.message), d.accept()))
    rid = p.evaluate("() => window.__xt.state().cfg.sells[0].id")
    p.evaluate("(id) => window.__xt.openSell(null, id)", rid)
    p.wait_for_timeout(150)
    p.click("#btnDelBuy")
    p.wait_for_timeout(300)
    assert msgs and "档位卖出量" in msgs[0], msgs


def test_s3_legacy_delete_hint(env):
    """老记录删除时提示「近似退回」。"""
    p = env().page
    sell(p, 100)
    p.evaluate("""() => {
      delete window.__xt.state().cfg.sells[0].allot;
      window.__xt.refreshAll();
    }""")
    msgs = []
    p.on("dialog", lambda d: (msgs.append(d.message), d.accept()))
    rid = p.evaluate("() => window.__xt.state().cfg.sells[0].id")
    p.evaluate("(id) => window.__xt.openSell(null, id)", rid)
    p.wait_for_timeout(150)
    p.click("#btnDelBuy")
    p.wait_for_timeout(300)
    assert msgs and "近似退回" in msgs[0], msgs


def test_s4_buy_record_has_no_allot_line(env):
    """买入记录不出现「已摊入」。"""
    p = env().page
    p.evaluate("""() => {
      var c = window.__xt.state().cfg;
      c.buys.push({id:'b1', name:'买入', gram:10, priceCny:900,
                   time:new Date().toISOString(), note:'', gear:null});
      window.__xt.compute(); window.__xt.refreshAll();
    }""")
    p.click('.tabbar button[data-tab="pos"]')
    p.wait_for_timeout(280)
    txt = p.evaluate("""() => {
      var t = document.querySelector('#buyList [data-buy]');
      return t ? t.innerText : '';
    }""")
    assert "已摊入" not in txt, txt
