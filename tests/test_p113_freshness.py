"""v7.9.1 P113：宏观新鲜度改按数据日判 + 结构摘要不省略（T188–T201）。

## 为什么要改

v7.6 定的口径是「`fetched_at` 超过 90 分钟 → 数据延迟」。这条口径把日频指标
当成分钟频在用：

- TIPS（FRED DFII10）是**美东交易日 T-1** 收盘后发布，一天一个值；
- DXY 的 `change_pct` 是**日涨跌**，也只有收盘后才变；
- 抓取脚本「值没变就不落盘」（`.github/workflows/macro.yml` 注释写明「无变化不产生空提交」）。

结果 `fetched_at` 在值变化前会一直停在旧时间，前端每 90 分钟后就判一次延迟——
绝大多数时间窗都在降级，卡片全是 `--`。这不是数据坏了，是用抓取时刻去衡量
日频数据的新鲜度。

新口径：**看 `as_of`（数据日）**，自然日差 ≤ 8 天算新鲜（覆盖周末、长周末、
联邦假日与 T-1 滞后）；`as_of` 缺失才退回 90 分钟口径（没有数据日可依，
只能保守看抓取时刻）。8 天不是随手取的：正常最长空窗是「感恩节周四+周五
休市 + 周末 + 周一」，从周三数据算到周二打开是 6 天。

## 第二条：结构摘要首行不再省略

v7.9 按 SRS 2.1 做了「单行 + ellipsis」，390px 上真实数据是
`结构：通道为主 通道 (ADX 32.4) · 箱体未成立（…）· 4H 震荡`——
右侧被切掉，「箱体未成立」的原因和 4H 状态读者永远看不到。省略号在这里
不是排版手段，是丢信息。改为自然换行，并顺手去掉「通道为主 通道」的重复。
"""
import json
import time

NOW = 1794222000000          # 2026-11-09 11:00 UTC，周一
DAY = 86400000
DEAD = {"deadBp": 2.0, "deadPct": 0.10, "deadStrict": True}


def doc(tips_as_of="2026-11-06", dxy_as_of="2026-11-06", ago_h=8,
        tips=2.15, tips_bp=-4.0, dxy=102.30, dxy_pct=0.74,
        now_ms=NOW, drop_tips=False, drop_dxy=False, fetched_at=None):
    """一份宏观文档。`ago_h` 是抓取时刻距 now 的小时数（默认落后 8 小时）。"""
    d = {"fetched_at": now_ms - int(ago_h * 3600000) if fetched_at is None else fetched_at,
         "src": "fred+cnbc",
         "tips_10y": {"value": tips, "change_bp": tips_bp, "trend_5d": tips_bp,
                      "as_of": tips_as_of},
         "dxy": {"value": dxy, "change_pct": dxy_pct, "trend_5d": dxy_pct,
                 "as_of": dxy_as_of, "src": "cnbc"}}
    if drop_tips:
        d["tips_10y"] = {"value": None, "change_bp": None, "as_of": ""}
    if drop_dxy:
        d["dxy"] = {"value": None, "change_pct": None, "as_of": "", "src": "cnbc"}
    return d


def judge(page, d, now_ms=NOW, opt=None):
    o = dict(DEAD)
    if opt:
        o.update(opt)
    o["now"] = now_ms
    return page.evaluate("a => window.__xt.v76.macroJudge(a.d, a.o)", {"d": d, "o": o})


# ============================================ 一、新鲜度按数据日判
def test_t188_daily_data_not_stale_by_fetch_time(make_env):
    """核心回归：抓取 8 小时前、数据日是上周五 → 不许判延迟。

    v7.9 在这里判「数据延迟」（90 分钟口径），卡片整块 `--`。
    """
    page = make_env(macro=doc(dxy_pct=-0.20)).page
    page.wait_for_timeout(900)
    r = judge(page, doc(dxy_pct=-0.20))
    assert r["stale"] is False, r
    assert r["status"] == "强利多", r


def test_t189_pipeline_dead_is_stale(make_env):
    """as_of 停在 10 天前 → 管线真停摆，必须判延迟。"""
    page = make_env(macro=doc(tips_as_of="2026-10-30", dxy_as_of="2026-10-30")).page
    page.wait_for_timeout(900)
    r = judge(page, doc(tips_as_of="2026-10-30", dxy_as_of="2026-10-30"))
    assert r["stale"] is True, r
    assert r["status"] == "数据延迟", r


def test_t190_weekend_not_stale(make_env):
    """周五的数据隔两天（周日）打开照常有效，不因周末误判延迟。"""
    page = make_env(macro=doc()).page
    page.wait_for_timeout(900)
    sun = NOW + 2 * DAY                     # 11-11，距数据日 11-06 是 5 天
    r = judge(page, doc(now_ms=sun), now_ms=sun)
    assert r["stale"] is False, r


def test_t191_long_holiday_within_window(make_env):
    """感恩节式空窗：周三数据、下周二打开，6 个自然日仍算新鲜。"""
    page = make_env(macro=doc(tips_as_of="2026-11-04", dxy_as_of="2026-11-04")).page
    page.wait_for_timeout(900)
    t6 = NOW + 1 * DAY           # 11-10 周二，距 11-04 周三 6 天 → 绿
    ok = judge(page, doc(tips_as_of="2026-11-04", dxy_as_of="2026-11-04", now_ms=t6), now_ms=t6)
    assert ok["stale"] is False, ok
    t9 = NOW + 4 * DAY           # 11-13 周五，距 11-04 是 9 天 → 红
    bad = judge(page, doc(tips_as_of="2026-11-04", dxy_as_of="2026-11-04", now_ms=t9), now_ms=t9)
    assert bad["stale"] is True, bad


def test_t192_missing_as_of_falls_back_to_fetch_age(make_env):
    """as_of 缺失时没有数据日可依，退回 90 分钟口径（保守）。"""
    page = make_env(macro=doc()).page
    page.wait_for_timeout(900)
    d = doc(tips_as_of="", dxy_as_of="", ago_h=8)
    assert judge(page, d)["stale"] is True
    fresh = doc(tips_as_of="", dxy_as_of="", ago_h=0.5)
    assert judge(page, fresh)["stale"] is False


def test_t193_one_side_expired_degrades_only_that_side(make_env):
    """TIPS 过期、DXY 新鲜 → 只把 TIPS 写成 `--`，DXY 照常出值。"""
    page = make_env(macro=doc()).page
    page.wait_for_timeout(900)
    d = doc(tips_as_of="2026-10-01", dxy_as_of="2026-11-06")
    r = judge(page, d)
    assert r["stale"] is False, r
    assert r["status"] == "数据不完整", r
    assert r["tips"] is None and r["dxy"]["change_pct"] == 0.74, r


def test_t194_future_as_of_is_stale(make_env):
    """数据日在未来 3 天 → 时钟错乱，宁可判延迟也不假装新鲜。"""
    page = make_env(macro=doc()).page
    page.wait_for_timeout(900)
    r = judge(page, doc(tips_as_of="2026-11-12", dxy_as_of="2026-11-12"))
    assert r["stale"] is True, r


def test_t195_fresh_of_pure_function(make_env):
    """`freshOf` 是纯函数，边界写死：8 天绿、9 天红、无 as_of 返回 null。"""
    page = make_env(macro=doc()).page
    page.wait_for_timeout(900)
    r = page.evaluate("""n => {
      const f = window.__xt.v79.freshOf;
      return {d8: f('2026-11-01', n), d9: f('2026-10-31', n),
              none: f('', n), bad: f('x', n), future: f('2026-11-30', n)};
    }""", NOW)
    assert r["d8"]["ok"] is True and r["d8"]["days"] == 8, r
    assert r["d9"]["ok"] is False and r["d9"]["days"] == 9, r
    assert r["none"]["ok"] is None and r["bad"]["ok"] is None, r
    assert r["future"]["ok"] is False, r


def test_t196_footer_shows_data_date(make_env):
    """页脚写「数据截至」就该是数据日，不是抓取时刻。"""
    page = make_env(macro=doc()).page
    page.wait_for_timeout(1000)
    r = page.evaluate("""() => {
      const s = document.querySelector('#mcAs');
      return {txt: (s.textContent||'').trim(), title: s.getAttribute('title') || ''};
    }""")
    assert "2026-11-06" in r["title"] or "11-06" in r["txt"], r
    assert "--" not in r["txt"], r


# ============================================ 二、结构摘要不省略
def _struct(page, w=390):
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(500)
    return page.evaluate("""() => {
      const a = document.querySelector('.pnote .pn-s');
      const cs = getComputedStyle(a);
      const r = a.getBoundingClientRect();
      return {ws: cs.whiteSpace, ov: cs.textOverflow, of: cs.overflow,
              h: Math.round(r.height), lh: parseFloat(cs.lineHeight) || 20,
              cw: a.clientWidth, sw: a.scrollWidth,
              ch: a.clientHeight, sh: a.scrollHeight,
              txt: a.textContent, page_ov: document.documentElement.scrollWidth -
                                          document.documentElement.clientWidth};
    }""")


def test_t197_summary_not_ellipsised(make_env):
    """390px：首行不许 nowrap + ellipsis，文本不许被裁。"""
    page = make_env(macro=doc()).page
    page.wait_for_timeout(900)
    r = _struct(page)
    assert r["ws"] != "nowrap", r
    assert r["ov"] != "ellipsis", r
    assert r["sh"] <= r["ch"] + 1, r          # 纵向没有被裁
    assert r["sw"] <= r["cw"] + 1, r          # 横向没有溢出
    assert "…" not in r["txt"] and "..." not in r["txt"], r


def test_t198_summary_full_text_visible(make_env):
    """首行整体可见：折行后高度 > 单行，且尾部字段（4H / 箱体）确实在。"""
    page = make_env(macro=doc()).page
    page.wait_for_timeout(900)
    r = _struct(page)
    assert r["h"] > r["lh"] * 1.4, r          # 真的换行了，不是挤在一行
    assert r["txt"].startswith("结构："), r
    assert "ADX" in r["txt"], r


def test_t199_summary_no_duplicate_word(make_env):
    """`通道为主 通道` 这种同一个词写两遍要去掉。"""
    page = make_env(macro=doc()).page
    page.wait_for_timeout(900)
    s = page.evaluate("""() => window.__xt.v79.summaryLine(
      {dominant:'通道为主', state:'通道', adx_1d:32.4, box:{valid:false, reason:'宽度不足'},
       trend_state:'震荡'}, {status:'中性'})""")
    assert "通道为主 通道" not in s, s
    assert s.count("通道为主") == 1, s
    assert "ADX 32.4" in s and "4H 震荡" in s, s


def test_t200_summary_320_fits(make_env):
    """320（iPhone SE 一代）折行后仍不许横向滚动。"""
    page = make_env(macro=doc(), width=320, height=568).page
    page.wait_for_timeout(900)
    r = _struct(page, 320)
    assert r["page_ov"] <= 0, r
    assert r["sw"] <= r["cw"] + 1, r


def test_t201_struct_page_no_error(make_env):
    """结构页整页无报错、无横向溢出。"""
    env = make_env(macro=doc())
    page = env.page
    page.wait_for_timeout(900)
    _struct(page)
    assert env.errors == [], env.errors
