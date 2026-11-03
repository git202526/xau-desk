"""P30 · 档位体检：满仓成本与逐档累计均价。

两个自我校验点是这套算法的锚：
    cumAvg[0]  == 第一个「有克重」的档的价 —— 不是 gears[0]，头部可能有纯预警档
    cumAvg[末] == avgAll                     —— 全买完，应与均价卡的「全部买满」同数

第二条若不等，说明 avgAll 与这里的克重来源不一致（一个用 planBase、一个用 plan），
要查来源而不是放宽断言 —— 这正是 P28 那次「逐档表与阶梯页对不上」的同一类问题。
"""
import pytest

from test_p19_2_back import cfg, make_bars

OZ = 31.1035


@pytest.fixture
def env(make_env):
    def _f(bars=None, width=390, height=844, **kw):
        # width/height 必须给 make_env，不能塞进 cfg —— 否则 320 宽的用例实际跑在 390 上
        return make_env(cfg=cfg(**kw), bars=bars if bars is not None else make_bars(),
                        width=width, height=height)
    return _f


def goto_plan(p):
    p.evaluate("() => window.__xt.gotoTab('plan')")
    p.wait_for_timeout(320)


def meta(p):
    return p.evaluate("() => window.__xt.state().gearMeta")


def gear_rows(p):
    return p.evaluate("() => window.__xt.state().gears")


def live_gears(p):
    """真正参与建仓的档：非 warn、有克重。"""
    return [g for g in gear_rows(p) if not g["warn"] and g["plan"] > 0.05]


# ---------- 计算层 ----------

def test_m1_full_cost_matches_hand_calc(env):
    """满仓成本 = Σ(克重 × 档位价) ÷ 31.1035，只算有克重的档。"""
    p = env().page
    goto_plan(p)
    m = meta(p)
    want = sum(g["plan"] / OZ * g["price"] for g in live_gears(p))
    assert want > 0
    assert m["fullCost"] == pytest.approx(want, rel=0.005), (m["fullCost"], want)


def test_m2_first_cum_equals_first_gear_price(env):
    """第 1 个有克重的档，累计均价就是它自己的价。

    注意不能写 gears[0] —— 头部有「预警① ②」这类纯预警档，它们没有克重、被跳过。
    本场景必须含预警档，否则这条测不到「跳过」这件事。
    """
    p = env().page
    goto_plan(p)
    m, gs = meta(p), gear_rows(p)
    assert gs[0]["warn"] is True, "场景应含预警档，否则测不到跳过逻辑"
    live = live_gears(p)
    assert m["cumAvg"][0] == pytest.approx(live[0]["price"], rel=0.001)


def test_m3_last_cum_equals_avg_all(env):
    """末项累计均价 == 均价卡的「选定档位试算均价」。两个不同的算法必须同数。"""
    p = env().page
    goto_plan(p)
    m = meta(p)
    assert m["cumAvg"][-1] == pytest.approx(m["avgAll"], rel=0.001), (
        m["cumAvg"][-1], m["avgAll"])


def test_m4_cum_avg_strictly_decreasing(env):
    """档位价越往下越低，累计均价随之下移 —— 严格递减，不能有一档回升。"""
    p = env().page
    goto_plan(p)
    cum = meta(p)["cumAvg"]
    assert len(cum) >= 3, cum
    assert all(cum[i] > cum[i + 1] for i in range(len(cum) - 1)), cum


def test_m5_cum_gram_increasing(env):
    """累计克重严格递增，末项 = 总计划克重。"""
    p = env().page
    goto_plan(p)
    m = meta(p)
    cg = m["cumGram"]
    assert all(cg[i] < cg[i + 1] for i in range(len(cg) - 1)), cg
    assert cg[-1] == pytest.approx(sum(g["plan"] for g in live_gears(p)), abs=0.05)


def test_m6_realloc_moves_cost(env):
    """配额下沉开启后，额度从高价档挪到低价档 → 满仓成本下降（克重总量不变）。

    这条同时验证 gearCostOf 用的是 plan（含下沉）而不是 planBase ——
    用 planBase 的话，开关怎么切 fullCost 都不会变。
    """
    p = env().page
    goto_plan(p)
    before = meta(p)["fullCost"]
    p.evaluate("""() => {
      var S = window.__xt.state();
      S.cfg.realloc = {on:true, mode:"near", ratio:1, maxDrop:20};
      S.reallocDead = undefined;
      window.__xt.compute();
    }""")
    after = meta(p)["fullCost"]
    assert after < before, "下沉后克重挪向低价档，成本应下降：%s → %s" % (before, after)


def test_m7_warn_excluded_from_cum(env):
    """纯预警档不进累计：cumAvg 长度 = 有克重的档数。"""
    p = env().page
    goto_plan(p)
    gs, m = gear_rows(p), meta(p)
    assert any(g["warn"] for g in gs), "场景应含预警档"
    assert len(m["cumAvg"]) == len(live_gears(p))
    assert len(m["cumGram"]) == len(m["cumAvg"])


def test_m8_zero_weight_gear_skipped(env):
    """权重为 0 的档没有克重，跳过，不占累计位。"""
    p = env(wPct=[8, 12, 0, 36, 24, 20]).page
    goto_plan(p)
    gs = gear_rows(p)
    zero = [g for g in gs if not g["warn"] and g["plan"] < 0.05]
    assert zero, "应构造出一档权重为 0"
    assert len(meta(p)["cumAvg"]) == len(live_gears(p))


def test_m9_no_fx_shows_dash_not_zero(env):
    """汇率缺失时人民币是 unknown，不是 0 —— 显示 0 会被读成「满仓不要钱」。"""
    p = env().page
    goto_plan(p)
    # 不走 refreshAll：它会重拉汇率，把构造好的 S.fx=0 覆盖掉。只算 + 只重绘本卡
    p.evaluate("""() => {
      var S = window.__xt.state();
      S.fx = 0;
      window.__xt.compute();
      window.__xt.renderAvgCard();
    }""")
    m = meta(p)
    assert m["fullCostCny"] is None
    txt = p.eval_on_selector("#avgCard", "e => e.innerText")
    assert "满仓成本" in txt
    assert "¥ —" in txt or "¥—" in txt, txt
    assert "¥0" not in txt.replace(" ", ""), txt


# ---------- 界面 ----------

def test_m10_avg_card_shows_cost(env):
    """均价卡出现「满仓成本」与 $ 金额，且带克重。"""
    p = env().page
    goto_plan(p)
    txt = p.eval_on_selector("#avgCard", "e => e.innerText")
    assert "满仓成本" in txt
    assert "$" in txt
    assert "200 g" in txt or "200g" in txt, txt


def test_m11_ladder_row_shows_cum(env):
    """每档一行「买到此档均价」，条数 = 有克重的档数，首条数值 = 该档价。"""
    p = env().page
    goto_plan(p)
    rows = p.eval_on_selector_all("#ladder .lv", "els => els.map(e => e.innerText)")
    hit = [r for r in rows if "买到此档均价" in r]
    live = live_gears(p)
    assert len(hit) == len(live), (len(hit), len(live))
    first = int(round(live[0]["price"]))
    assert ("买到此档均价 " + str(first)) in hit[0].replace("\n", " "), hit[0]


def test_m12_narrow_320(env):
    """320 宽：新增的成本行与均价小字都完整显示，整页不横向溢出。"""
    p = env(width=320).page
    goto_plan(p)
    ov = p.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert ov <= 0, "横向溢出 %spx" % ov
    txt = p.eval_on_selector("#avgCard", "e => e.innerText")
    assert "满仓成本" in txt and "¥" in txt, txt


def test_m13_no_high_no_nan(env):
    """没有高点（算不出档位）时不报错、不出现 NaN。"""
    p = env(H_BASE=None, bars=[]).page
    goto_plan(p)
    body = p.evaluate("() => document.body.innerText")
    assert "NaN" not in body
    assert "undefined" not in body


def test_m14_idempotent(env):
    """连刷两次结果完全相同 —— 累计是纯计算，不能有状态累积。"""
    p = env().page
    goto_plan(p)
    a = meta(p)
    p.evaluate("() => window.__xt.refreshAll()")
    b = meta(p)
    assert a["fullCost"] == b["fullCost"]
    assert a["cumAvg"] == b["cumAvg"]
    assert a["cumGram"] == b["cumGram"]
