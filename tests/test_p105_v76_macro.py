"""v7.6 P3：宏观双变量引擎（T97 / T99 / T110 / T113）。

口径（计划书 §5.5）：
  - TIPS |Δbp| ≥ 1.5 才算有方向；DXY |Δ%| ≥ 0.10（广义美元 0.08）
  - 两变量同向 → 强利多/强利空 High；单边 → Medium；都没到阈值 → 中性 Low
  - v7.9.1 起新鲜度按**数据日**（as_of）判：自然日差 ≤ 8 天算新鲜。
    抓取时刻 > 90 分钟只在 as_of 缺失时兜底——日频值不变脚本就不落盘，
    拿抓取时刻判日频数据会把「正常」判成「延迟」。
    无论走哪条路径，过期数据一律「数据延迟」+ 数值置空，**不得输出「中性」**
  - 美元涨而金价日涨 → 记背离例外，置信降到 Medium

本文件先红后绿：macroJudge 尚未实现，红在接口缺失。
"""
import time

NOW = lambda: int(time.time() * 1000)          # noqa: E731


def _as_of(days_ago=1):
    """数据日相对今天算，不写死日期——写死的话用例会在几个月后集体变红。"""
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days_ago * 86400))


def doc(tips_bp=0.0, dxy_pct=0.0, age_min=0, tips=2.15, dxy=105.2,
        src="cnbc", fetched=None, as_of=None):
    d = _as_of(1) if as_of is None else as_of
    return {
        "fetched_at": NOW() - int(age_min * 60000) if fetched is None else fetched,
        "src": "fred+cnbc",
        "tips_10y": {"value": tips, "change_bp": tips_bp, "trend_5d": tips_bp,
                     "as_of": d, "n": 5966},
        "dxy": {"value": dxy, "change_pct": dxy_pct, "trend_5d": dxy_pct,
                "as_of": d, "src": src, "n": 259},
        "hist": {"tips": [], "dxy": []},
        "errors": [],
    }


def judge(page, d, gold_pct=0.0):
    return page.evaluate("a => window.__xt.v76.macroJudge(a.d, a.o)",
                         {"d": d, "o": {"gold_pct": gold_pct}})


# ---------------------------------------------------------------- T99 方向
def test_t99_strong_bearish(make_env):
    """TIPS +3.0bp、DXY +0.20% → 双杀，强利空 / High。"""
    r = judge(make_env().page, doc(tips_bp=3.0, dxy_pct=0.2))
    assert r["status"] == "强利空", r
    assert r["confidence"] == "High", r
    assert r["tips_10y"]["change_bp"] == 3.0 and r["dxy"]["change_pct"] == 0.2, r


def test_t99b_strong_bullish(make_env):
    r = judge(make_env().page, doc(tips_bp=-3.0, dxy_pct=-0.2))
    assert r["status"] == "强利多" and r["confidence"] == "High", r


def test_t99c_threshold_boundary(make_env):
    """正好踩在 1.5bp / 0.10% 上必须算有方向——阈值是「达到」不是「超过」。"""
    r = judge(make_env().page, doc(tips_bp=1.5, dxy_pct=0.10))
    assert r["status"] == "强利空", r


def test_t99d_single_factor(make_env):
    """只有 TIPS 到阈值 → 利空 Medium，不能升格成强利空。"""
    r = judge(make_env().page, doc(tips_bp=3.0, dxy_pct=0.02))
    assert r["status"] == "利空" and r["confidence"] == "Medium", r


def test_t99e_below_threshold(make_env):
    """两个都没到阈值 → 中性 Low，note 必须说明是阈值问题，不能写「无数据」。"""
    r = judge(make_env().page, doc(tips_bp=0.4, dxy_pct=0.03))
    assert r["status"] == "中性" and r["confidence"] == "Low", r
    assert "阈值" in r["note"], r


def test_t99f_broad_dollar_threshold(make_env):
    """广义美元（DTWEXBGS）波动天然更小，阈值放宽到 0.08%。"""
    r = judge(make_env().page, doc(tips_bp=2.0, dxy_pct=0.09, src="fred-dtwex"))
    assert r["status"] == "强利空", r


# ---------------------------------------------------------------- T110 失效
def test_t110_stale_three_hours(make_env):
    """数据日缺失 + 抓取已过 3 小时：只能按抓取时刻保守判延迟。

    v7.9.1 把主判据换成数据日（as_of），这条守的是「没有数据日可依」时的兜底：
    此时无法判断数据是新是旧，宁可降级也不能拿它给结论。
    """
    r = judge(make_env().page,
              doc(tips_bp=3.0, dxy_pct=0.2, age_min=180, as_of=""))
    assert r["status"] == "数据延迟", r
    assert r["status"] != "中性", r
    assert r["stale"] is True, r
    assert r["tips_10y"]["value"] is None and r["dxy"]["value"] is None, r


def test_t110b_missing_fetched_at(make_env):
    r = judge(make_env().page, doc(fetched=None) | {"fetched_at": None})
    assert r["status"] == "数据延迟", r


def test_t110c_fresh_89min(make_env):
    """89 分钟还在 90 分钟窗口内，不能误判延迟。"""
    r = judge(make_env().page, doc(tips_bp=3.0, dxy_pct=0.2, age_min=89))
    assert r["status"] == "强利空", r


def test_t110d_iso_string_timestamp(make_env):
    """产物若写成 ISO 字符串也要能认，不能因为格式就整张卡变灰。"""
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    r = judge(make_env().page, doc(tips_bp=3.0, dxy_pct=0.2, fetched=iso))
    assert r["status"] == "强利空", r


# ---------------------------------------------------------------- T113 背离
def test_t113_divergence_dollar_up_gold_up(make_env):
    """TIPS 降（利多）+ 美元涨（利空）+ 金价日涨 → 记背离，置信 Medium。"""
    r = judge(make_env().page, doc(tips_bp=-4.0, dxy_pct=0.2), gold_pct=0.8)
    assert r["exceptions"], r
    assert any("背离" in x for x in r["exceptions"]), r
    assert r["confidence"] == "Medium", r


def test_t113b_no_divergence_when_gold_down(make_env):
    """美元涨、金价跌是正常负相关，不许乱记例外。"""
    r = judge(make_env().page, doc(tips_bp=-1.0, dxy_pct=0.2), gold_pct=-0.8)
    assert r["exceptions"] == [], r


# ---------------------------------------------------------------- T97 404
def test_t97_macro_404(make_env):
    """产物 404：卡片走「数据延迟」，现价照常，0 pageerror。"""
    env = make_env(macro=None)
    page = env.page
    page.wait_for_timeout(700)
    r = page.evaluate("""() => {
      const st = window.__xt.v76.macroState();
      return {status: st.status, has: !!st.doc, err: st.err,
              price: (document.querySelector('#pNow')||{}).textContent||''};
    }""")
    assert r["status"] == "数据延迟", r
    assert r["price"].strip() not in ("", "--"), r
    assert env.errors == [], env.errors


def test_t97b_macro_ok(make_env):
    env = make_env(macro=doc(tips_bp=3.0, dxy_pct=0.2))
    page = env.page
    page.wait_for_timeout(700)
    r = page.evaluate("() => window.__xt.v76.macroState()")
    assert r["doc"] is True and r["status"] == "强利空", r


def test_t113c_conflict_is_neutral(make_env):
    """TIPS 降（利多）+ 美元涨（利空）：谁都不比谁权威，只能给中性。"""
    r = judge(make_env().page, doc(tips_bp=-3.0, dxy_pct=0.2), gold_pct=-0.5)
    assert r["status"] == "中性" and r["confidence"] == "Low", r
    assert "方向相反" in r["note"], r
