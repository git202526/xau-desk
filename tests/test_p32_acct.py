"""P32 · 多账户仓位（仓库）。

一个账户 = 一个银行积存金账户。账本里的每笔买卖都带 `acct`，
持仓页可以按账户筛选，汇总卡多一个「回本价」。

三条硬口径（写在这里，改代码前先读）：
1. 档位价、权重、目标克重都是**全局**的 —— 账户只给记录分归属，不嵌套档位。
2. `holdStats(acct)` 不传参 = 全局。18 个调用点里只有 renderPos / renderBuys /
   saveSell 传参，其余（档位、止盈、仓位进度）一律全局。
3. 卖出不按账户重新分摊成本，所以 **Σ 账户 costLeft ≠ 全局 costLeft**。
   只有克重与买卖总额可以相加 —— s3 是反向断言，防止后来的人"顺手加一下"。

迁移 m1–m6 / 账户 CRUD a1–a8 / 记账 r1–r7 / 汇总 s1–s6 /
回本价 c1–c4 / 联动 x1–x4 / 窄屏 w1–w2
"""
import os

import pytest

from conftest import CFG_EMPTY

H = 4700.0
TOTAL = 200.0
PX = 4030.0

A0 = {"id": "a0", "name": "招行", "note": "", "color": "#D4A24C", "at": 1}
A1 = {"id": "a1", "name": "工行", "note": "", "color": "#5B8FF9", "at": 2}


def buy(bid, gram, px, acct="a0", gear=None, name="买入", t="2026-08-01T02:00:00.000Z"):
    return {"id": bid, "name": name, "gram": gram, "priceCny": px,
            "time": t, "note": "", "gear": gear, "acct": acct}


def sell(sid, gram, px, acct="a0", t="2026-09-20T02:00:00.000Z"):
    return {"id": sid, "name": "减仓", "gram": gram, "priceCny": px,
            "time": t, "note": "", "acct": acct}


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, alpha=2.44,
             schemaVersion=3, migratedV7=True,
             accounts=[dict(A0), dict(A1)], acctLast="a0", comeback=10,
             buys=[], sells=[])
    c.update(kw)
    return c


def two_acct(**kw):
    """招行 10g@900 + 工行 10g@1000 —— 均价、成本都能一眼手算。"""
    return cfg(buys=[buy("b1", 10, 900.0, "a0"), buy("b2", 10, 1000.0, "a1")], **kw)


@pytest.fixture
def env(make_env):
    def _f(c=None, **kw):
        base = dict(c) if c is not None else two_acct()
        mk = {}
        for k in list(kw.keys()):
            if k in ("width", "height", "price", "gh_on", "reduced_motion", "tz", "calendar"):
                mk[k] = kw.pop(k)
        base.update(kw)
        return make_env(cfg=base, **mk)
    return _f


def to_pos(p):
    p.evaluate("""() => {
        if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
        window.scrollTo(0, 0);
    }""")
    p.wait_for_timeout(280)
    p.click('.tabbar button[data-tab="pos"]')
    p.wait_for_timeout(400)


def stats(p):
    return p.evaluate("() => JSON.parse(JSON.stringify(window.__xt.acctStats()))")


def hold(p, acct=None):
    if acct is None:
        return p.evaluate("() => { var h = window.__xt.holdStats(); "
                          "return {g:h.grams, avg:h.avg, amt:h.amount, sold:h.sold}; }")
    return p.evaluate("(a) => { var h = window.__xt.holdStats(a); "
                      "return {g:h.grams, avg:h.avg, amt:h.amount, sold:h.sold}; }", acct)


def accts(p):
    return p.evaluate("() => JSON.parse(JSON.stringify(window.__xt.state().cfg.accounts || []))")


def recs(p, kind="buys"):
    return p.evaluate("(k) => JSON.parse(JSON.stringify(window.__xt.state().cfg[k] || []))", kind)


def set_state(p, js):
    """直接改内存态（构造边界数据用），不走 UI。"""
    p.evaluate("() => { var S = window.__xt.state(); %s }" % js)


# ================================================================ 迁移 m1–m6

def test_m1_legacy_gets_default_account(env):
    """老数据没有 accounts：迁移后出现「默认账户」，每条记录都带上 a0。"""
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, schemaVersion=2, migratedV7=True,
             buys=[buy("b1", 20, 900.0, acct=None)], sells=[])
    for r in c["buys"]:
        r.pop("acct")
    p = env(c).page
    a = accts(p)
    assert len(a) == 1 and a[0]["id"] == "a0"
    assert recs(p)[0]["acct"] == "a0"
    assert p.evaluate("() => window.__xt.state().cfg.schemaVersion") == 3


def test_m2_migration_keeps_numbers(env):
    """迁移前后 grams / avg / amount 完全一致 —— 这一批的硬门。"""
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, schemaVersion=2, migratedV7=True,
             buys=[buy("b1", 20, 905.0, acct=None), buy("b2", 25, 921.0, acct=None)],
             sells=[sell("s1", 5, 1150.0, acct=None)])
    for r in c["buys"] + c["sells"]:
        r.pop("acct")
    p = env(c).page
    h = hold(p)
    assert h["g"] == pytest.approx(40, abs=0.001)
    assert h["amt"] == pytest.approx(20 * 905.0 + 25 * 921.0, abs=0.5)
    assert h["avg"] == pytest.approx((20 * 905.0 + 25 * 921.0) / 45, abs=0.001)
    # 全部落在默认账户名下，且账户合计等于全局
    st = stats(p)
    assert len(st) == 1
    assert st[0]["gram"] == pytest.approx(h["g"], abs=0.001)


def test_m3_migration_idempotent(env):
    """连跑两次：账户数仍为 1，记录逐字节不变。"""
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, schemaVersion=2, migratedV7=True,
             buys=[buy("b1", 20, 900.0, acct=None)], sells=[])
    c["buys"][0].pop("acct")
    p = env(c).page
    before = p.evaluate("() => JSON.stringify(window.__xt.state().cfg.buys)")
    assert p.evaluate("() => window.__xt.runMigrations()") is False
    assert p.evaluate("() => JSON.stringify(window.__xt.state().cfg.buys)") == before
    assert len(accts(p)) == 1


def test_m4_dangling_acct_falls_back(env):
    """记录指向一个不存在的账户：acctOf 回落第一个，总数不变。"""
    p = env(two_acct()).page
    set_state(p, "S.cfg.buys[1].acct = 'a9';")
    assert p.evaluate("() => window.__xt.acctOf('a9')") == "a0"
    assert hold(p)["g"] == pytest.approx(20, abs=0.001)
    assert p.evaluate("() => window.__xt.acctName('a9')") == "招行"


def test_m5_future_version_untouched(env):
    """schemaVersion=99：不降级，也不凭空造出 accounts。"""
    p = env(dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, schemaVersion=99)).page
    assert p.evaluate("() => window.__xt.state().cfg.schemaVersion") == 99
    assert p.evaluate("() => window.__xt.runMigrations()") is False
    assert p.evaluate("() => window.__xt.state().cfg.accounts") is None


@pytest.mark.parametrize("raw,want", [("abc", 10), (-5, 0), (99, 50)])
def test_m6_comeback_clamped(env, raw, want):
    """回本加点非法值：'abc'→10，-5→0，99→50。"""
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, schemaVersion=2, migratedV7=True,
             comeback=raw, buys=[], sells=[])
    p = env(c).page
    assert p.evaluate("() => window.__xt.state().cfg.comeback") == want


# ================================================================ 账户 CRUD a1–a8

def open_mgr(p):
    to_pos(p)
    p.click("#btnAcctMgr")
    p.wait_for_timeout(350)


def mrows(p):
    return p.evaluate("""() => [...document.querySelectorAll('#acctListBox .acmgr')]
        .map(n => n.innerText.replace(/\\s+/g, ' ').trim())""")


def new_acct(p, name, note=""):
    p.click("#acNew")
    p.wait_for_timeout(180)
    p.fill("#acName", name)
    p.fill("#acNote", note)
    p.click("#acSave")
    p.wait_for_timeout(420)


def test_a1_create_shows_up(env):
    """新建后立刻出现在账户卡片与管理列表。"""
    p = env(two_acct()).page
    open_mgr(p)
    new_acct(p, "建行")
    assert [a["name"] for a in accts(p)] == ["招行", "工行", "建行"]
    assert len(mrows(p)) == 3
    p.click("#acctClose")
    p.wait_for_timeout(300)
    assert "建行" in p.inner_text("#acctBox")


def test_a2_duplicate_name_rejected(env):
    """重名被拦，文案含「已经有叫」。"""
    p = env(two_acct()).page
    open_mgr(p)
    new_acct(p, "招行")
    assert len(accts(p)) == 2
    assert "已经有叫" in p.inner_text("#toast")


def test_a3_name_rules(env):
    """空名被拦；超 20 字被拦。"""
    p = env(two_acct()).page
    open_mgr(p)
    p.click("#acNew")
    p.wait_for_timeout(180)
    p.fill("#acName", "")
    p.click("#acSave")
    p.wait_for_timeout(200)
    assert "不能为空" in p.inner_text("#toast")
    assert len(accts(p)) == 2
    # maxlength 会挡住手输，用 JS 直接赋值才能测到 20 字上限
    p.evaluate("() => { document.getElementById('acName').value = '银'.repeat(21); }")
    p.click("#acSave")
    p.wait_for_timeout(200)
    assert "最多 20" in p.inner_text("#toast")
    assert len(accts(p)) == 2


def test_a4_delete_empty_acct(env):
    """删除无记录账户：二次确认后消失，delAccts 留下墓碑。"""
    p = env(two_acct(accounts=[dict(A0), dict(A1),
                              {"id": "a2", "name": "建行", "note": "", "color": "#5AD8A6", "at": 3}])).page
    open_mgr(p)
    p.on("dialog", lambda d: d.accept())
    p.click('[data-del="a2"]')
    p.wait_for_timeout(450)
    assert [a["id"] for a in accts(p)] == ["a0", "a1"]
    assert "a2" in p.evaluate("() => window.__xt.state().cfg.delAccts")


def test_a5_delete_with_records_blocked(env):
    """有记录的账户不能直接删，先提示笔数并要求转移。"""
    p = env(two_acct()).page
    open_mgr(p)
    p.click('[data-del="a0"]')
    p.wait_for_timeout(300)
    txt = p.inner_text("#acctListBox")
    assert "还有" in txt and "笔记录" in txt
    assert len(accts(p)) == 2


def test_a6_move_then_delete(env):
    """转移：原账户 0 克、目标账户变多、全局克重不变。"""
    p = env(two_acct()).page
    before = hold(p)["g"]
    open_mgr(p)
    p.click('[data-del="a1"]')
    p.wait_for_timeout(250)
    p.click('[data-move="a0"]')
    p.wait_for_timeout(500)
    st = {r["id"]: r for r in stats(p)}
    assert [a["id"] for a in accts(p)] == ["a0"]
    assert st["a0"]["gram"] == pytest.approx(before, abs=0.001)
    assert hold(p)["g"] == pytest.approx(before, abs=0.001)


def test_a7_last_account_kept(env):
    """只剩 1 个账户时删除被拦。"""
    p = env(cfg(accounts=[dict(A0)], buys=[buy("b1", 10, 900.0, "a0")])).page
    open_mgr(p)
    p.click('[data-del="a0"]')
    p.wait_for_timeout(300)
    assert "至少保留一个" in p.inner_text("#toast")
    assert len(accts(p)) == 1


def test_a8_rename_updates_badge(env):
    """记录只存 id，改名后记录行的徽标跟着变。"""
    p = env(two_acct()).page
    open_mgr(p)
    p.click('[data-edit="a1"]')
    p.wait_for_timeout(200)
    p.fill("#acName", "工商银行")
    p.click("#acSave")
    p.wait_for_timeout(450)
    p.click("#acctClose")
    p.wait_for_timeout(350)
    txt = p.inner_text("#buyList")
    assert "工商银行" in txt and "工行 ·" not in txt


# ================================================================ 记账 r1–r7

def test_r1_default_is_acct_last(env):
    """新建记账默认选中 acctLast。"""
    p = env(two_acct(acctLast="a1")).page
    to_pos(p)
    p.click("#btnAddBuy")
    p.wait_for_timeout(300)
    assert p.input_value("#gAcct") == "a1"


def test_r2_single_acct_disabled(env):
    """只有一个账户时下拉禁用，并给出提示行。"""
    p = env(cfg(accounts=[dict(A0)], buys=[])).page
    to_pos(p)
    p.click("#btnAddBuy")
    p.wait_for_timeout(300)
    assert p.evaluate("() => document.getElementById('gAcct').disabled") is True
    assert p.evaluate("() => document.getElementById('acctNote').style.display") != "none"


def test_r3_edit_record_moves_grams(env):
    """改记录的账户：两账户克重互变，全局合计不变。"""
    p = env(two_acct()).page
    before = hold(p)["g"]
    to_pos(p)
    p.click('[data-buy="b1"]')
    p.wait_for_timeout(320)
    p.select_option("#gAcct", "a1")
    p.click("#btnSave")
    p.wait_for_timeout(450)
    st = {r["id"]: r for r in stats(p)}
    assert st["a0"]["gram"] == pytest.approx(0, abs=1e-9)
    assert st["a1"]["gram"] == pytest.approx(20, abs=0.001)
    assert hold(p)["g"] == pytest.approx(before, abs=0.001)


def test_r4_sell_over_balance_blocked(env):
    """卖出超过该账户余额：被拦，提示含账户名与余额。"""
    p = env(two_acct()).page
    to_pos(p)
    p.click("#btnAddBuy")
    p.wait_for_timeout(280)
    p.evaluate("() => { document.getElementById('sheetTitle').textContent = ''; }")
    # 走减仓入口：直接打开卖出表单
    p.evaluate("() => window.__xt.openSell(null, null)")
    p.wait_for_timeout(300)
    p.select_option("#gAcct", "a1")
    p.fill('[name="g_gram"]', "15")     # 工行只有 10g
    p.fill('[name="g_price"]', "1100")
    p.click("#btnSave")
    p.wait_for_timeout(320)
    assert "工行" in p.inner_text("#toast")
    assert "10" in p.inner_text("#toast")
    assert len(recs(p, "sells")) == 0


def test_r5_switch_acct_drops_back(env):
    """编辑卖出时同时改账户 + 改大克重：不能借用旧账户的额度。"""
    c = cfg(buys=[buy("b1", 20, 900.0, "a0"), buy("b2", 2, 1000.0, "a1")],
            sells=[sell("s1", 5, 1100.0, "a0")])
    p = env(c).page
    to_pos(p)
    p.click('[data-sellid="s1"]')
    p.wait_for_timeout(320)
    p.select_option("#gAcct", "a1")
    p.fill('[name="g_gram"]', "6")      # 工行只有 2g；若 back 生效 2+5=7 会放行
    p.click("#btnSave")
    p.wait_for_timeout(350)
    assert "工行" in p.inner_text("#toast")
    assert recs(p, "sells")[0]["gram"] == 5, "没拦住就会被改成 6"


def test_r6_same_acct_keeps_back(env):
    """同账户编辑卖出改克重：校验用「余额 + 原克重」，所以能改大。"""
    c = cfg(buys=[buy("b1", 5, 900.0, "a0")], sells=[sell("s1", 5, 1100.0, "a0")])
    p = env(c).page
    to_pos(p)
    assert hold(p)["g"] == pytest.approx(0, abs=1e-9)
    p.click('[data-sellid="s1"]')
    p.wait_for_timeout(320)
    p.fill('[name="g_gram"]', "4")
    p.click("#btnSave")
    p.wait_for_timeout(450)
    assert recs(p, "sells")[0]["gram"] == 4
    assert hold(p)["g"] == pytest.approx(1, abs=0.001)


def test_r7_badge_short_name_and_title(env):
    """记录行徽标：超 6 字截断，title 里是全名。"""
    c = two_acct(accounts=[{"id": "a0", "name": "招商银行积存金", "note": "", "color": "#D4A24C", "at": 1},
                           dict(A1)])
    p = env(c).page
    to_pos(p)
    p.wait_for_timeout(300)
    d = p.evaluate("""() => {
        const n = document.querySelector('#buyList .abdot');
        return n ? {cls: n.className, title: n.getAttribute('title'),
                    color: n.style.background, txt: n.parentElement.innerText} : null;
    }""")
    assert d and d["title"] == "招商银行积存金"
    assert "招商银行积存…" in d["txt"]
    assert d["color"], "色点必须有颜色（sanitize 会剥 style，靠 data-c 补回来）"


# ================================================================ 汇总口径 s1–s6

def mixed():
    """招行 10g@900 + 工行 10g@1000，招行卖 5g@1100。
    全局 avg=950 grams=15 costLeft=14250；账户 costLeft 合计 14500（差 250）。"""
    return cfg(buys=[buy("b1", 10, 900.0, "a0"), buy("b2", 10, 1000.0, "a1")],
               sells=[sell("s1", 5, 1100.0, "a0")])


def test_s1_grams_add_up(env):
    """Σ 账户克重 == 全局克重（唯一的硬对账）。"""
    p = env(mixed()).page
    h = hold(p)
    assert h["g"] == pytest.approx(15, abs=0.001)
    assert sum(r["gram"] for r in stats(p)) == pytest.approx(h["g"], abs=0.001)


def test_s2_buy_amount_adds_up(env):
    """Σ 账户买入额 == 全局 amount。"""
    p = env(mixed()).page
    h = hold(p)
    assert sum(r["amt"] for r in stats(p)) == pytest.approx(h["amt"], abs=0.5)


def test_s3_cost_left_does_not_add_up(env):
    """反向断言：有卖出时 Σ 账户 costLeft **不等于** 全局 costLeft。

    这是「卖出不重新分摊成本」的必然结果，不是 bug。留这条是为了防止
    后来的人把卡片头改成累加 —— 那样数字会对不上，且找不到原因。
    """
    p = env(mixed()).page
    h = hold(p)
    global_cost = h["g"] * h["avg"]
    sum_cost = sum(r["costLeft"] for r in stats(p))
    assert global_cost == pytest.approx(14250, abs=1)
    assert sum_cost == pytest.approx(14500, abs=1)
    assert abs(sum_cost - global_cost) > 1, "两个口径相等了，说明成本核算模型变了"


def test_s4_card_header_uses_global(env):
    """账户区不再重复全账户的三个数；全账户口径只在持仓汇总卡，且取全局、不累加账户行。"""
    p = env(mixed()).page
    to_pos(p)
    p.wait_for_timeout(320)
    d = p.evaluate("""() => {
        const dup = document.querySelectorAll('#acctBox .acsum').length;
        const it = [...document.querySelectorAll('#posBox .posmini .it')]
          .map(n => n.innerText.replace(/\\s+/g, ' ').trim());
        return {dup: dup, it: it};
    }""")
    assert d["dup"] == 0, d
    cost = [x for x in d["it"] if "持仓成本" in x][0]
    # 全局成本 14250；账户行相加是 14500 —— 显示必须是前者
    assert "14,250" in cost, cost


def test_s5_filter_rescopes_summary_only(env):
    """筛选某账户 → 汇总卡按该账户算，仓位进度仍是全局。"""
    p = env(two_acct()).page
    to_pos(p)
    p.wait_for_timeout(300)
    gauge_before = p.inner_text("#posGauge")
    p.click('.acctbar button[data-acct="a1"]')
    p.wait_for_timeout(400)
    box = p.inner_text("#posBox")
    assert "10 g" in box.replace("10g", "10 g") or "10g" in box
    assert "当前：工行" in box
    assert p.inner_text("#posGauge") == gauge_before


def test_s6_filter_empty_acct_no_legacy_fallback(env):
    """筛到空账户：显示「还没有买入记录」，不能拿到老数据兼容汇总。"""
    c = cfg(buys=[], sells=[], GRAMS=100, COST=900)
    p = env(c).page
    to_pos(p)
    p.wait_for_timeout(300)
    assert "100" in p.inner_text("#posBox").replace(",", "")
    p.click('.acctbar button[data-acct="a1"]')
    p.wait_for_timeout(400)
    box = p.inner_text("#posBox")
    assert "还没有买入记录" in box
    assert "100 g" not in box and "100g" not in box


# ================================================================ 回本价 c1–c4

def test_c1_comeback_plus_10(env):
    """回本价 = 均价 + 10；把设置改成 5 之后跟着变。"""
    p = env(two_acct()).page
    to_pos(p)
    p.wait_for_timeout(300)
    assert "960.00" in p.inner_text("#posBox")      # 均价 950 + 10
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(400)
    p.fill("#setComeback", "5")
    p.click("#btnSaveSet")
    p.wait_for_timeout(500)
    p.click('.tabbar button[data-tab="pos"]')
    p.wait_for_timeout(400)
    assert "955.00" in p.inner_text("#posBox")
    assert "960.00" not in p.inner_text("#posBox")


def test_c2_gap_direction_colors(env):
    """低于回本价 → 绿「还差」；高于 → 红「已过」。"""
    p = env(two_acct(), price=4030).page     # 现价 ¥920 < 回本 960
    to_pos(p)
    p.wait_for_timeout(300)
    box = p.inner_text("#posBox")
    assert "还差" in box
    p2 = env(two_acct(), price=4400).page    # 现价 ¥1004 > 960
    to_pos(p2)
    p2.wait_for_timeout(300)
    box2 = p2.inner_text("#posBox")
    assert "已过" in box2
    cls = p2.evaluate("""() => {
        const it = [...document.querySelectorAll('#posBox .posmini .it')]
          .find(n => n.innerText.indexOf('距回本') >= 0);
        return it ? it.querySelector('b').className : '';
    }""")
    assert "red" in cls


def test_c3_missing_fx_shows_dash(env):
    """现价或汇率为空 → 距回本显示 --，不是 0.00%。"""
    p = env(two_acct()).page
    p.evaluate("() => { window.__xt.state().fx = 0; window.__xt.renderPos(); }")
    p.wait_for_timeout(250)
    box = p.inner_text("#posBox")
    assert "--" in box
    assert "0.00%" not in box


def test_c4_each_row_own_comeback(env):
    """账户行各自回本价 = 各自均价 + 同一点数（900+10 / 1000+10）。"""
    p = env(two_acct()).page
    to_pos(p)
    p.wait_for_timeout(350)
    text = p.inner_text("#acctBox")
    assert "910.00" in text and "1010.00" in text


# ================================================================ 联动 x1–x4

def test_x1_gear_page_untouched(env):
    """切账户筛选后，建仓页的档位进度 / 待补 / 满仓成本一字不变。"""
    p = env(two_acct()).page
    before = p.evaluate("() => JSON.stringify(window.__xt.planSummary())")
    gears_before = p.evaluate("() => JSON.stringify([1,2,3,4,5,6].map(i => window.__xt.gearState(i)))")
    to_pos(p)
    p.click('.acctbar button[data-acct="a1"]')
    p.wait_for_timeout(450)
    assert p.evaluate("() => JSON.stringify(window.__xt.planSummary())") == before
    assert p.evaluate("() => JSON.stringify([1,2,3,4,5,6].map(i => window.__xt.gearState(i)))") == gears_before


def test_x2_marks_do_not_create_holdings(env):
    """纯「标记已买」不产生账户持仓，账户合计仍等于账本。"""
    c = cfg(buys=[], sells=[], gearMarks={"1": {"g": 16, "cost": 0, "sold": 0, "proceeds": 0, "at": 1}})
    p = env(c).page
    assert hold(p)["g"] == pytest.approx(0, abs=1e-9)
    assert all(r["gram"] == 0 for r in stats(p))
    assert sum(r["gram"] for r in stats(p)) == pytest.approx(hold(p)["g"], abs=0.001)


def test_x3_take_ladder_not_filtered(env):
    """止盈阶梯不随筛选变，且写明「按全账户均价」。"""
    p = env(two_acct()).page
    to_pos(p)
    p.wait_for_timeout(400)
    before = p.inner_text("#takeBox")
    p.click('.acctbar button[data-acct="a1"]')
    p.wait_for_timeout(450)
    assert p.inner_text("#takeBox") == before
    assert "全账户均价" in p.inner_text("#panel-pos")


def test_x4_filter_makes_no_cloud_write(env):
    """切筛选只在内存里，不产生云端 PUT（守住「切 Tab 不写云端」）。"""
    e = env(two_acct())
    p = e.page
    to_pos(p)
    p.wait_for_timeout(1400)          # 等首屏那几次写入落地
    before = e.gh.puts
    p.click('.acctbar button[data-acct="a1"]')
    p.wait_for_timeout(700)
    p.click('.acctbar button[data-acct=""]')
    p.wait_for_timeout(700)
    assert e.gh.puts == before, "切筛选触发了云端写入（S.acctFilter 不该进 cfg）"


# ================================================================ 窄屏 w1–w2

def test_w1_320_no_overflow(env):
    """320 宽：页面不横向溢出，账户行不挤压。"""
    p = env(mixed(), width=320, height=844).page
    to_pos(p)
    p.wait_for_timeout(400)
    d = p.evaluate("""() => ({
        docW: document.documentElement.scrollWidth,
        winW: window.innerWidth,
        rows: [...document.querySelectorAll('#acctBox .acrow')].map(n => n.getBoundingClientRect().width),
        boxW: document.getElementById('acctBox').getBoundingClientRect().width,
    })""")
    assert d["docW"] <= d["winW"] + 1, d
    assert all(w <= d["boxW"] + 1 for w in d["rows"]), d


def test_w2_390_filter_bar_scrolls(env):
    """390 宽：筛选条横向滚动、按钮高 34、不换行。"""
    c = cfg(accounts=[
        {"id": "a0", "name": "招商银行积存金", "note": "", "color": "#D4A24C", "at": 1},
        {"id": "a1", "name": "工商银行如意金", "note": "", "color": "#5B8FF9", "at": 2},
        {"id": "a2", "name": "建设银行易存金", "note": "", "color": "#5AD8A6", "at": 3},
        {"id": "a3", "name": "中国银行积存金", "note": "", "color": "#F6BD16", "at": 4},
    ], buys=[buy("b1", 10, 900.0, "a0"), buy("b2", 10, 1000.0, "a1")])
    p = env(c, width=390, height=844).page
    to_pos(p)
    p.wait_for_timeout(400)
    d = p.evaluate("""() => {
        const bar = document.querySelector('.acctbar');
        const bs = [...bar.querySelectorAll('button')];
        return {
          scrollW: bar.scrollWidth, clientW: bar.clientWidth,
          h: bs[0].getBoundingClientRect().height,
          tops: [...new Set(bs.map(b => Math.round(b.getBoundingClientRect().top)))],
        };
    }""")
    assert d["h"] >= 34
    assert len(d["tops"]) == 1, "按钮不该换行"
    assert d["scrollW"] > d["clientW"], "4 个长名账户在 390 宽应该需要横向滚动"
