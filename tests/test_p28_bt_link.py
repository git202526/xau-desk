"""P28 · 回测与设置参数关联。

四个域：
    a1–a8  计算层：量纲（克 ÷ 31.1035 × $/oz）、均价、同本金对照
    h1–h5  高点来源：set / then / 非法值回落 / 切回复原
    i1–i4  隔离性：回测不读写实盘的下沉状态、价格、标记
    u1–u5  UI：参数快照条、档位对照表、窄屏、前视偏差文案

前置事实（P28 之前）：
    budget = totalGrams() * $/oz        —— 克 × 美元/盎司，量纲不成立，金额放大 31.1 倍
    plan   = planOf()                   —— 读 S.price / S.reallocDead，把今天的状态塞进起点
    height = btbHigh(bars, i0)          —— 与设置页的档位锚点无关，且无任何说明
"""
import pytest

from test_p19_2_back import H, TOTAL, cfg, goto_struct, make_bars

OZ = 31.1035


def back(p, opts=None):
    if opts is None:
        return p.evaluate("(k) => window.__xt.btBack(k)", "1d")
    return p.evaluate("(a) => window.__xt.btBack(a[0], a[1])", ["1d", opts])


def state(p, expr):
    return p.evaluate("(e) => { var S = window.__xt.state(); return " + expr + "; }", expr)


@pytest.fixture
def env(make_env):
    def _f(bars=None, width=390, height=844, **kw):
        # width/height 必须给 make_env，不能塞进 cfg —— 否则 320 宽的用例实际跑在 390 上
        return make_env(cfg=cfg(**kw), bars=bars if bars is not None else make_bars(),
                        width=width, height=height)
    return _f


# ---------- 批一：计算层 ----------

def test_a1_budget_is_grams_over_oz(env):
    """本金 = 总克重 ÷ 31.1035 × 起点价。

    旧代码 `TOTAL * 起点价` 把 200g 当成 200 盎司，本金放大 31.1 倍。
    """
    p = env().page
    r = back(p)
    assert r["meta"]["budget"] == pytest.approx(TOTAL / OZ * H, rel=0.005)


def test_a2_invest_is_grams_over_oz(env):
    """投入 = Σ(克 ÷ 31.1035 × 档位价)，只算已触发的档。"""
    p = env().page
    r = back(p)
    want = sum(g["plan"] / OZ * g["price"] for g in r["gear"] if g["done"])
    assert want > 0, "场景应至少触发一档"
    assert r["stats"]["invest"] == pytest.approx(want, rel=0.005)


def test_a3_avg_cost_unit(env):
    """均价 = 投入 ÷ 买入克重，量级应落在 $/g（而不是 $/oz）。"""
    p = env().page
    s = back(p)["stats"]
    assert s["holdGram"] > 0
    assert s["avgCost"] == pytest.approx(s["invest"] / s["holdGram"], rel=0.005)
    # $5000/oz ≈ $160/g；若量纲错，均价会是 $5000 量级
    assert 100 < s["avgCost"] < 250, s["avgCost"]


def test_a4_no_trigger_keeps_cash(env):
    """一档没触发：净现金流与持仓都是 0，期末净值 0（P31 起不再有「躺着不动的本金」）。"""
    p = env(bars=make_bars(trough=4500.0)).page
    r = back(p)
    assert r["stats"]["trigGears"] == 0
    assert r["stats"]["endV"] == pytest.approx(0, abs=0.01)


def test_a5_lump_baseline_dimension(env):
    """一次性基准 budget = 总克重 ÷ 31.1035 × 起点价。

    P31：budget 不再是「本金上限」，而是「同样这些克、在区间首日一次性买」的成本，
    也就是扫描表里溢价率的分母。量纲必须和 fullCost 一致。
    """
    p = env().page
    m = back(p)["meta"]
    want = sum(m["planGrams"]) / OZ * m["firstClose"]
    assert m["budget"] == pytest.approx(want, rel=0.005), (m["budget"], want)


def test_a6_scan_trigger_monotonic(env):
    """档距越大档位越疏，触发档数单调不增，且最疏那组确实少买到东西。

    投入金额会差好几倍，那不是 bug —— 档距 ×1.4 的深档挂得更低，行情没跌到就不成交。
    真正要锁的是方向：档距越大，买到的档越少。
    """
    p = env().page
    scan = p.evaluate("() => window.__xt.btScan('1d')")
    tr = [x["r"]["stats"]["trigGears"] for x in scan["rows"]]
    assert all(tr[i] >= tr[i + 1] for i in range(4)), tr
    assert tr[0] > tr[-1], "本场景应体现档距的区分度：%s" % (tr,)


def test_a7_end_v_identity(env):
    """P31：没有预算之后 pnl 就是 endV（净现金流 + 持仓市值），二者必须相等。"""
    p = env().page
    s = back(p)["stats"]
    assert s["endV"] == pytest.approx(s["pnl"], rel=1e-9)
    if s["holdGram"] <= 0:
        assert s["endMV"] == 0


def test_a8_realized_zero_without_sell(env):
    """默认不开循环档：已实现收益为 0。"""
    p = env().page
    r = back(p, {"loop": False})
    assert r["stats"]["soldGram"] == 0
    assert r["stats"]["realized"] == pytest.approx(0, abs=1e-6)


# ---------- 批二：高点来源 ----------

def test_h1_default_is_set(env):
    """默认用设置页的高点 —— 用户要对得上阶梯页。"""
    p = env().page
    r = back(p)
    assert r["meta"]["heightSrc"] == "set"
    assert r["meta"]["he"] == pytest.approx(H, rel=0.001)


def test_h2_set_matches_gear_base(env):
    """set 时 meta.he 必须等于页面 gearBase().he，不能各算各的。"""
    p = env(H_BASE=6000.0).page
    r = back(p)
    base = p.evaluate("() => window.__xt.gearBase().he")
    assert r["meta"]["heightSrc"] == "set"
    assert r["meta"]["he"] == pytest.approx(base, rel=1e-6)


def test_h3_then_uses_history(env):
    """then 用回测起点的历史高点，与 set 明显不同。"""
    p = env(H_BASE=6000.0).page
    set_r = back(p)
    then_r = back(p, {"height": "then"})
    assert then_r["meta"]["heightSrc"] == "then"
    assert then_r["meta"]["he"] == pytest.approx(then_r["meta"]["heThen"], rel=1e-6)
    assert abs(then_r["meta"]["he"] - set_r["meta"]["he"]) > 1


def test_h4_bad_height_falls_back_to_set(env):
    """未知 height 值按 set 处理，不能崩也不能当成 then。"""
    p = env().page
    r = back(p, {"height": "whatever"})
    assert r["meta"]["heightSrc"] == "set"


def test_h5_switch_back(env):
    """切到 then 再切回 set，高点复原（无残留状态）。"""
    p = env(H_BASE=6000.0).page
    a = back(p)["meta"]["he"]
    back(p, {"height": "then"})
    c = back(p)["meta"]["he"]
    assert c == pytest.approx(a, rel=1e-9)


# ---------- 批三：隔离性 ----------

def test_i1_no_realloc_state_leak(env):
    """回测不写实盘的下沉状态容器。"""
    p = env().page
    before = state(p, "JSON.stringify(S.reallocDead || {})")
    back(p)
    after = state(p, "JSON.stringify(S.reallocDead || {})")
    assert before == after


def test_i2_realloc_follows_off(env):
    """设置关下沉时，回测的 realloc 缺省 = false。"""
    p = env(realloc={"on": False}).page
    assert back(p)["meta"]["realloc"] is False


def test_i3_realloc_override(env):
    """显式 reallocOverride=true 才下沉，且不改设置。"""
    p = env(realloc={"on": False}).page
    r = back(p, {"reallocOverride": True})
    assert r["meta"]["realloc"] is True
    assert state(p, "!!S.cfg.realloc.on") is False


def test_i4_no_marks_mutation(env):
    """回测不动实盘标记与现价。"""
    p = env().page
    before = state(p, "JSON.stringify({m:S.cfg.gearMarks||{},p:S.price||0})")
    back(p)
    after = state(p, "JSON.stringify({m:S.cfg.gearMarks||{},p:S.price||0})")
    assert before == after


# ---------- 批四：UI ----------

def test_u1_param_bar(env):
    """参数条同时给出：档位来源、档数、档距、配额、下沉。"""
    p = env().page
    goto_struct(p)
    txt = p.eval_on_selector("#bbParam", "e => e.innerText")
    for k in ("档位来源", "档距", "配额", "下沉"):
        assert k in txt, (k, txt)
    assert "$" in txt


def test_u2_switch_to_then(env):
    """点「当时」chip：高点换成历史值，选中态跟着走。"""
    p = env(H_BASE=6000.0).page
    goto_struct(p)
    p.click('#bbParam button[data-h="then"]')
    p.wait_for_timeout(400)
    assert back(p)["meta"]["heightSrc"] == "then"
    on = p.eval_on_selector('#bbParam button[data-h="then"]', "e => e.classList.contains('on')")
    assert on


def test_u3_gear_rows_match(env):
    """档位表行数 == 档数，首行价格与第一档一致。"""
    p = env().page
    goto_struct(p)
    r = back(p)
    rows = p.eval_on_selector_all("#bbGears .bbg .gp", "els => els.map(e => e.innerText.trim())")
    assert len(rows) == len(r["gear"])
    first = float(rows[0].replace("$", "").replace(",", ""))
    assert first == pytest.approx(r["gear"][0]["price"], abs=1)


def test_u4_narrow(env):
    """320 / 390 宽：参数条与档位表都不横向溢出。"""
    for w in (390, 320):
        p = env(width=w).page
        goto_struct(p)
        over = p.evaluate("""() => ({
          de: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          bd: document.body.scrollWidth - document.body.clientWidth
        })""")
        assert over["de"] <= 1, (w, over)
        assert over["bd"] <= 1, (w, over)


def test_u5_caveat_text(env):
    """set 时必须出现「前视偏差」，then 时不出现。"""
    p = env(H_BASE=6000.0).page
    goto_struct(p)
    assert "前视偏差" in p.eval_on_selector("#bbNote", "e => e.innerText")
    p.click('#bbParam button[data-h="then"]')
    p.wait_for_timeout(400)
    txt = p.eval_on_selector("#bbNote", "e => e.innerText")
    assert "前视偏差" not in txt
    assert "当时高点" in txt
