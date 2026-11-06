"""P34-2b 终止跌幅 = 上限（§3.3.1）：统一夹取、只压不拉、越界必须说明。

口径：H=4700、6 档、起始 13%、默认权重 [8,12,24,36,12,8]。
  T=3900 → 最深 −22.6%，未越界：d 与价格一字不改，余量 >0
  T=3200 → 夹到 −25.00%，档表 == 区间 13~25%，给「目标改为 X」「放开终止」两条路
  α=3.0 → 夹到 −25.00%，报「α 3.00 会让最深档到 −X%，收到 α Z」
  end 25→40（未越界场景）→ 价格不动，余量从 1.x 跳到 17.x pp
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

H = 4700.0
W = 200.0


def bars(n=240, px=PRICE, amp=24.0):
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


def cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=W, planOnly=True,
             gearMarks={}, alpha=1, startDrop=13, endDrop=25)
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    def _f(c=None, amp=24.0, **kw):
        base = dict(c) if c is not None else cfg()
        base.update(kw)
        return make_env(cfg=base, bars=bars(amp=amp), price=PRICE,
                        width=390, height=844).page
    return _f


def meta(p):
    return p.evaluate("() => window.__xt.gearMeta()")


def base(p):
    return p.evaluate("() => window.__xt.gearBase()")


def prices(p):
    return p.evaluate("() => window.__xt.gearPrices(window.__xt.gearBase().he, window.__xt.gearBase().d)")


def card(p):
    return p.evaluate("() => { const el=document.getElementById('avgCard'); return el?el.innerText:''; }")


def has_sel(p, sel):
    return p.evaluate("(s) => !!document.querySelector('#avgCard ' + s)", sel)


def note(p):
    return p.evaluate("() => { const el=document.getElementById('dropDepthNote'); return el?el.innerText:''; }")


# ---------- 1. 未越界：一个字节都不改 ----------

def test_t1_within_limit_no_change(env):
    """T=3900 最深 −22.6% < 25%：d 等于反解 d₀，价格不变，卡片不出现夹取说明。"""
    p = env(avgTarget=3900, avgAnchor=0, gearCount=6, spanMode="target")
    b = base(p)
    d0 = p.evaluate("() => window.__xt.spanForTarget(window.__xt.gearBase().he, 3900, window.__xt.curW()).d")
    assert b["clamped"] is False
    assert b["d"] == pytest.approx(d0, abs=0.01)
    assert "已按终止" not in card(p)
    assert has_sel(p, "[data-release]") is False
    assert has_sel(p, "[data-retarget]") is False


# ---------- 2. target 越界：α 先封顶、终止再夹一次（两个上限叠加） ----------

def test_t2_target_clamped_equals_range(env):
    """T=3200：α 真需 8.82 先被 3.5 截，再被终止收到 −25.00%；档表等于「区间 13~25%」。

    两个上限叠加时因果链必须写完：需 α 多少 → α 先封顶到多深 → 终止再收到多深 →
    放开终止能到多少（仍够不到目标）。按钮两条都给：只留「改目标」等于藏掉了
    「是谁把档表压到 25%」，用户看不出因果。
    """
    p = env(avgTarget=3200, avgAnchor=0, gearCount=6, spanMode="target")
    m = meta(p)
    assert m["clamped"] is True
    assert m["alpha0"] == pytest.approx(3.5, abs=1e-9)
    assert m["deepestPct"] == pytest.approx(-25.0, abs=0.05)
    # 与区间模式同高点同档数：价格逐档相等
    pr_target = prices(p)
    p.evaluate("() => window.__xt.setSpan('range', undefined, undefined, undefined)")
    pr_range = prices(p)
    for a, b in zip(pr_target, pr_range):
        assert a == pytest.approx(b, abs=0.02)
    # 回到 target 看文案
    p.evaluate("() => window.__xt.setSpan('target', undefined, undefined, undefined)")
    t = card(p)
    assert "需 α≈8.8" in t and "超过上限 3.5" in t
    assert "最深档只能到 −30.87%" in t
    assert "终止 25% 再把它收到 −25.00%" in t
    assert "现档表等同「区间 13~25%」" in t and "当前可达" in t
    # 第三行：说清放开终止的边界（解除第二道、仍够不到）
    assert "放开终止只解除第二道限制" in t
    assert "仍够不到 3200" in t
    assert "已到终止上限" in note(p), note(p)
    assert has_sel(p, "[data-retarget]") is True
    assert has_sel(p, "[data-release]") is True


def test_t2c_release_moves_prices_but_not_target(env):
    """α 封顶时点「放开终止」：档表真的变深（d 回到 d₀），目标仍未命中 —— 按钮不是安慰剂。

    它解的是「第二道限制」，让用户看见 α 才是瓶颈；不是让 3200 变可达。
    """
    p = env(avgTarget=3200, avgAnchor=0, gearCount=6, spanMode="target")
    d_before = base(p)["d"]
    p.evaluate("() => document.querySelector('#avgCard [data-release]').click()")
    p.wait_for_timeout(200)
    m2 = meta(p)
    assert m2["clamped"] is False
    assert m2["d"] == pytest.approx(168.0, abs=0.5)      # 回到 α 3.5 的 d₀
    assert m2["d"] > d_before
    assert m2["diffAnchor"] > 100                        # 离 3200 还很远
    assert "该均价在档距上限内做不到" in card(p)


# ---------- 2b. 终止才是瓶颈：两条路都给 ----------

def test_t2b_termination_only_clamp_gives_both_paths(env):
    """T=3800（α 2.87 未封顶）：终止是唯一瓶颈，给「目标改为」+「放开终止」两条路。"""
    p = env(avgTarget=3800, avgAnchor=0, gearCount=6, spanMode="target")
    m = meta(p)
    assert m["clamped"] is True
    assert m["alpha0"] < 3.5
    t = card(p)
    assert "需最深档 −27.64%" in t and "已按终止 25% 收到 −25.00%" in t
    # 只有 α 真被封顶时才有第三行「两道限制」说明
    assert "放开终止只解除第二道限制" not in t
    assert has_sel(p, "[data-retarget]") is True
    assert has_sel(p, "[data-release]") is True


# ---------- 3. α 越界：报需 α 与实际 α ----------

def test_t3_alpha_clamped_reports_alpha(env):
    """α=3.0、无目标：最深档被夹到 −25%，提示「α 3.00 … 收到 α Z」。"""
    p = env(avgTarget=None, gearCount=6, spanMode="alpha", alpha=3.0, amp=50.0)
    m = meta(p)
    assert m["clamped"] is True
    assert m["deepestPct"] == pytest.approx(-25.0, abs=0.05)
    t = card(p)
    assert "α 3.00 会让最深档到" in t and "已按终止 25% 收到 α" in t
    assert m["alphaEff"] == pytest.approx(m["capD"] / (m["d0"] / 3.0), rel=1e-6)


# ---------- 4. 双按钮各写一个字段，互不串改 ----------

def test_t4_buttons_write_one_field_each(env):
    """「目标改为 X」只改 avgTarget；「放开终止」只改 endDrop。"""
    p = env(avgTarget=3800, avgAnchor=0, gearCount=6, spanMode="target")
    reach = meta(p)["reach"]
    st0 = p.evaluate("() => ({t: window.__xt.state().cfg.avgTarget, e: window.__xt.state().cfg.endDrop})")
    p.evaluate("() => document.querySelector('#avgCard [data-retarget]').click()")
    st1 = p.evaluate("() => ({t: window.__xt.state().cfg.avgTarget, e: window.__xt.state().cfg.endDrop})")
    assert abs(st1["t"] - reach) <= 1     # 向上取整到可达值
    assert st1["e"] == st0["e"]          # 终止没被动

    q = env(avgTarget=3800, avgAnchor=0, gearCount=6, spanMode="target")
    st0 = q.evaluate("() => ({t: window.__xt.state().cfg.avgTarget, e: window.__xt.state().cfg.endDrop})")
    q.evaluate("() => document.querySelector('#avgCard [data-release]').click()")
    st2 = q.evaluate("() => ({t: window.__xt.state().cfg.avgTarget, e: window.__xt.state().cfg.endDrop})")
    assert st2["t"] == st0["t"]          # 目标没被动
    assert st2["e"] > st0["e"]


# ---------- 5. 余量摘要：改终止不动价，但余量立刻变 ----------

def test_t5_margin_summary_reacts_without_price_change(env):
    """未越界场景把终止 25→40：价格不动，余量从 2.x 跳到 17.x pp（验收项）。"""
    p = env(avgTarget=3900, avgAnchor=0, gearCount=6, spanMode="target")
    pr0 = prices(p)
    n0 = note(p)
    assert "余量" in n0 and "终止 25%" in n0
    p.evaluate("() => window.__xt.setSpan(undefined, undefined, 40)")
    pr1 = prices(p)
    n1 = note(p)
    assert pr0 == pytest.approx(pr1, abs=1e-6)
    assert "终止 40%" in n1 and "余量" in n1
    assert n0 != n1
    # 余量 = 40 − 22.57 ≈ 17.43pp
    assert "17.4" in n1


# ---------- 6. 超过终止上限 60%：不给按钮 ----------

def test_t6_over_termination_max_no_button(env):
    """10 档 + 高 ATR（α 模式），需 −69% 深于 END_DROP_MAX：只说明、不给「放开终止」。"""
    p = env(avgTarget=None, gearCount=10, spanMode="alpha", alpha=3.5,
            startDrop=5, endDrop=60, amp=100.0)
    m = meta(p)
    assert m["clamped"] is True
    assert -m["deepest0Pct"] > 60
    t = card(p)
    assert "超过终止上限 60%" in t
    assert has_sel(p, "[data-release]") is False


# ---------- 7. α 低于下限：不夹档距，但必须说明（旧漏洞 ⑤） ----------

def test_t7_alpha_below_min_warns_without_moving_prices(env):
    """目标只比第 1 档价低 9 美元 → 反解 α≈0.19 < 0.6：档距一字不改（目标仍准），
    但卡片必须说明「档位挤成一团」，不能静默。"""
    p = env(avgTarget=4070, avgAnchor=0, gearCount=6, spanMode="target")
    m = meta(p)
    b = base(p)
    assert m["hint"] == "narrow"
    assert m["clamped"] is False
    assert m["alpha0"] < 0.6
    # 只压不拉：档距等于反解值，没有被 α 下限「拉深」
    assert b["d"] == pytest.approx(b["d0"], abs=1e-9)
    t = card(p)
    assert "低于下限" in t
    assert "一档全成交" in t
    # 跨度读数与档距一致（6 档 5 段）
    span = 5 * b["d"]
    assert num_round(span) in t


def num_round(x):
    return "{:,}".format(int(round(x)))


# ---------- 8. 设置页说明与实现一致（P0） ----------

def test_t8_settings_note_says_global_cap(env):
    """终止跌幅已是全局上限：说明不能再写「切到区间后生效」，也不该挂 warn。"""
    p = env(avgTarget=3200, avgAnchor=0, gearCount=6, spanMode="target")
    p.evaluate("() => window.__xt.gotoTab('set')")
    p.wait_for_timeout(250)
    txt = p.evaluate("() => document.getElementById('setDropNote').innerText")
    cls = p.evaluate("() => document.getElementById('setDropNote').className")
    assert "最大深度" in txt
    assert "切到「区间」后生效" not in txt
    assert "warn" not in cls


# ---------- 9. 区间退化：起止差 1pp 必须说明（P3） ----------

def test_t9_narrow_range_warns(env):
    """起始 40% + 终止 8%：终止被抬到 41%，区间只剩 1pp、10 档间距 5.2 美元。"""
    p = env(avgTarget=None, gearCount=10, spanMode="range", startDrop=40, endDrop=8)
    m = meta(p)
    assert m["d"] == pytest.approx(4700 * 0.01 / 9, abs=0.05)
    t = card(p)
    assert "区间只有 1.0pp" in t
    assert "一档全成交" in t
    # 区间方式不该再谎称「按波动率 α=1 排列」
    assert "按波动率排列" not in t
