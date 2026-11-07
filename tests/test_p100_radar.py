"""事件雷达 v7.5：4 星以上 + 预期差判定。

P3 覆盖判定规则本身（纯函数，不依赖 DOM）；
P4 覆盖集成输出（表头、字段、双情景、自检、不编造、响应式）。

判定口径见 index.html 的 EV_RULES 注释：
  dir = 数据「超预期」时对黄金的方向（+1 利多 / -1 利空）；
  失业率、初请是反向指标；地缘/流动性/购金/大选走 sp 分支，不套 dir。
"""
import datetime as dt

import pytest

UTC = dt.timezone.utc
MIN = 60000


def ms(offset_min):
    return int((dt.datetime.now(UTC) + dt.timedelta(minutes=offset_min)).timestamp() * 1000)


def jn(events):
    return {"updatedAt": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "events": events}


def e(name, offset=60, impact=4, fc="", pv="", av="", ccy="", en=""):
    return {"t": ms(offset), "ccy": ccy, "name": name, "en": en, "impact": impact,
            "fc": fc, "pv": pv, "av": av, "affect": ""}


def open_sig(page):
    page.click('.tabbar button[data-tab="sig"]')
    page.wait_for_timeout(300)


def rows(page):
    return page.evaluate("""() => [...document.querySelectorAll('#evList .evrow')].map(r => ({
        name: (r.querySelector('.c3')||{}).textContent || '',
        region: (r.querySelector('.c2')||{}).textContent || '',
        dt: (r.querySelector('.c1')||{}).textContent || '',
        stars: +r.getAttribute('data-st'),
        pv: (r.querySelector('.c5')||{}).textContent || '',
        fc: (r.querySelector('.c6')||{}).textContent || '',
        av: (r.querySelector('.c7')||{}).textContent || '',
        diff: (r.querySelector('.c8')||{}).textContent || '',
        verdict: (r.querySelector('.c9')||{}).textContent || '',
        horizon: (r.querySelector('.c11')||{}).textContent || '',
        conf: (r.querySelector('.c12')||{}).textContent || '',
        logic: (r.querySelector('.c10')||{}).textContent || '',
    }))""")


def jd(page, obj):
    return page.evaluate("(o) => window.__xt.evJudge(o)", obj)


# ================= P3 单元：判定规则 =================

def test_r1_cpi_beat_is_bearish(make_env):
    """CPI 超预期 → 紧缩预期 → 实际利率上行 → 利空。"""
    p = make_env().page
    r = jd(p, e("美国10月CPI年率", fc="3.2%", pv="3.0%", av="3.5%", ccy="USD"))
    assert r["diff"] == "超预期"
    assert r["verdict"] == "利空"
    assert "实际利率上行" in r["logic"]
    assert r["conf"] == "高" and r["horizon"] == "短线"


def test_r2_unemployment_is_inverse_indicator(make_env):
    """失业率是反向指标：升=就业弱=利多，不能套「强数据=利空」。"""
    p = make_env().page
    r = jd(p, e("美国10月失业率", fc="4.2%", pv="4.2%", av="4.5%", ccy="USD"))
    assert r["diff"] == "超预期"
    assert r["verdict"] == "利多", r
    assert "降息预期前移" in r["logic"]


def test_r3_inline_is_neutral(make_env):
    """符合预期 → 中性，且必须提分项/边际，不能只说中性。"""
    p = make_env().page
    r = jd(p, e("美国10月核心PCE物价指数月率", fc="0.3%", pv="0.2%", av="0.3%", ccy="USD"))
    assert r["diff"] == "符合预期"
    assert r["verdict"] == "中性"
    assert "分项" in r["logic"] or "边际" in r["logic"]


def test_r4_pending_has_two_scenarios(make_env):
    """未公布 → 待定 + 双情景，且不得出现具体公布值。"""
    p = make_env().page
    r = jd(p, e("美国10月非农就业人口", fc="6.5万", pv="2.9万"))
    assert r["diff"] == "待公布"
    assert r["verdict"] == "待定"
    assert "若超预期" in r["logic"] and "若不及预期" in r["logic"]
    assert r["av"] == ""


def test_r5_unknown_metric_not_faked(make_env):
    """未收录指标：只写「需人工核对」，不许瞎判。"""
    p = make_env().page
    r = jd(p, e("某国奇怪指数", fc="12.5", pv="11.0", av="15.0"))
    assert r["verdict"] == "待定"
    assert "需人工核对" in r["logic"]
    assert r["cat"] == "其他"


def test_r6_geo_two_chains(make_env):
    """地缘必须拆两条链，并说明当前阶段 B 链权重更高。"""
    p = make_env().page
    r = jd(p, e("美伊局势升级 霍尔木兹海峡"))
    assert r["verdict"] == "拉锯" and r["cat"] == "地缘"
    assert "避险脉冲" in r["logic"]
    assert "实际利率上行" in r["logic"]
    assert "B 链权重更高" in r["logic"]


def test_r7_liquidity_exception(make_env):
    """流动性挤兑：黄金会被卖，短线利空，不能默认避险=利多。"""
    p = make_env().page
    r = jd(p, e("跨资产流动性冲击 回购市场"))
    assert r["verdict"] == "利空" and r["cat"] == "流动性"
    assert "换取现金" in r["logic"]


def test_r8_cb_gold_structural(make_env):
    """央行购金是中期结构性支撑，不是短线做多理由。"""
    p = make_env().page
    r = jd(p, e("中国央行黄金储备 购金"))
    assert r["horizon"] == "中期"
    assert "不是短线做多" in r["logic"]


def test_r9_num_parse(make_env):
    """数值解析：K/万/亿 要换算，占位符返回 null。"""
    p = make_env().page
    assert p.evaluate("() => window.__xt.evNum('198K')") == 198000
    assert p.evaluate("() => window.__xt.evNum('6.5万')") == 65000
    assert p.evaluate("() => window.__xt.evNum('3.2%')") == pytest.approx(3.2)
    assert p.evaluate("() => window.__xt.evNum('—')") is None
    assert p.evaluate("() => window.__xt.evNum('待公布')") is None


def test_r10_diff_tolerance(make_env):
    """预期差用相对 1% 容差：0.3 vs 0.3 是符合，不是超预期。"""
    p = make_env().page
    f = lambda fc, av: p.evaluate("(a) => window.__xt.evDiff(a[0], a[1])", [fc, av])
    assert f("0.3%", "0.3%") == "符合预期"
    assert f("0.3%", "0.4%") == "超预期"
    assert f("54.6", "55.5") == "超预期"
    assert f("54.6", "54.7") == "符合预期"
    assert f("6.5", "") == "待公布"
    assert f("", "3.2") == "无法判断"


def test_r11_region_without_ccy(make_env):
    """金十源没有 ccy，要能按中文名前缀判国别。"""
    p = make_env().page
    r = p.evaluate("() => window.__xt.evRegion({name:'美国10月ISM制造业PMI',ccy:''})")
    assert r == "美国"
    r2 = p.evaluate("() => window.__xt.evRegion({name:'欧元区9月工业产出月率',ccy:''})")
    assert r2 == "欧元区"


def test_r12_fomc_hold_is_neutral(make_env):
    """FOMC 按结果判：维持不变 → 中性，且提示看声明与点阵图。"""
    p = make_env().page
    r = jd(p, e("美联储FOMC利率决议", av="维持利率不变 3.75%", impact=5))
    assert r["verdict"] == "中性" and r["horizon"] == "中期"
    assert "点阵图" in r["logic"]


# ================= P4 集成：页面输出 =================

def test_i1_only_four_star(make_env):
    """3 星事件不得进表（旧版是「4 星优先」，v7.5 改为只留 ≥4）。"""
    env = make_env(jn={"calendar.json": jn([
        e("美国10月ISM制造业PMI", 60, 4, "54.6", "54.5", ""),
        e("美国10月零售销售月率", 120, 3, "0.4%", "0.6%", ""),
        e("美国10月工业产出月率", 180, 2, "0.1%", "0.3%", ""),
    ])})
    open_sig(env.page)
    rs = rows(env.page)
    assert len(rs) == 1, [r["name"] for r in rs]
    assert rs[0]["stars"] == 4
    assert "零售销售" not in rs[0]["name"]


def test_i2_headers_and_fields(make_env):
    """表头 11 列 + 行内 12 字段（传导逻辑独占一行）都要有。"""
    env = make_env(jn={"calendar.json": jn([
        e("美国10月ISM制造业PMI", 60, 4, "54.6", "54.5", "55.5"),
        e("美国10月零售销售月率", 120, 3, "0.4%", "0.6%", ""),
        e("美国10月工业产出月率", 180, 2, "0.1%", "0.3%", ""),
    ])})
    open_sig(env.page)
    head = env.page.evaluate("() => document.querySelector('.evt-head').textContent")
    for k in ("日期时间", "国家/地区", "数据/事件", "星级", "前值", "预期",
              "公布值", "预期差", "金银判定", "影响时段", "置信度"):
        assert k in head, (k, head)
    r = rows(env.page)[0]
    for k in ("name", "region", "dt", "stars", "pv", "fc", "av",
              "diff", "verdict", "horizon", "conf", "logic"):
        assert r[k] not in ("", 0), (k, r)
    assert r["diff"] == "超预期" and r["verdict"] == "利空"
    assert "实际利率上行" in r["logic"]


def test_i3_pending_shows_two_scenarios(make_env):
    """未公布行：公布值写「待公布」，传导逻辑含双向情景。"""
    env = make_env(jn={"calendar.json": jn([
        e("美国10月非农就业人口", 240, 5, "6.5万", "2.9万", ""),
        e("美国10月零售销售月率", 120, 3, "0.4%", "0.6%", ""),
        e("美国10月工业产出月率", 180, 2, "0.1%", "0.3%", ""),
    ])})
    open_sig(env.page)
    r = rows(env.page)[0]
    assert r["av"] == "待公布"
    assert r["diff"] == "待公布" and r["verdict"] == "待定"
    assert "若超预期" in r["logic"] and "若不及预期" in r["logic"]


def test_i4_no_fabricated_actual(make_env):
    """已过点但没拿到公布值 → 「待核实」，不许把前值填进去冒充。"""
    env = make_env(jn={"calendar.json": jn([
        e("美国10月ISM制造业PMI", -20, 4, "54.6", "54.5", ""),
        e("美国10月零售销售月率", 120, 3, "0.4%", "0.6%", ""),
        e("美国10月工业产出月率", 180, 2, "0.1%", "0.3%", ""),
    ])})
    open_sig(env.page)
    r = rows(env.page)[0]
    assert r["av"] == "待核实", r
    assert r["av"] != r["pv"] and r["av"] != r["fc"]
    assert r["diff"] == "待公布"


def test_i5_selfcheck_block(make_env):
    """自检清单必须渲染，且 4 星表在正常数据下应全绿。"""
    env = make_env(jn={"calendar.json": jn([
        e("美国10月CPI年率", 60, 4, "3.2%", "3.0%", "3.5%"),
        e("美国10月非农就业人口", 240, 5, "6.5万", "2.9万", ""),
        e("美国10月零售销售月率", 120, 3, "0.4%", "0.6%", ""),
    ])})
    open_sig(env.page)
    txt = env.page.evaluate("() => document.getElementById('evChk').textContent")
    assert "自检清单" in txt
    assert "10/10" in txt, txt
    bad = env.page.evaluate("() => document.querySelectorAll('#evChk .ck.no').length")
    assert bad == 0, txt


def test_i6_top3_and_scenarios(make_env):
    """表后必须有 Top3 与双情景两块。"""
    env = make_env(jn={"calendar.json": jn([
        e("美国10月CPI年率", 60, 4, "3.2%", "3.0%", "3.5%"),
        e("美国10月非农就业人口", 240, 5, "6.5万", "2.9万", ""),
        e("美伊局势升级 霍尔木兹海峡", 360, 5),
        e("美国10月零售销售月率", 120, 3, "0.4%", "0.6%", ""),
    ])})
    open_sig(env.page)
    top = env.page.evaluate("() => document.getElementById('evTop3').textContent")
    sc = env.page.evaluate("() => document.getElementById('evScen').textContent")
    assert "最重要的 3 个事件" in top
    assert "观察：" in top and "预案：" in top
    assert "情景 A" in sc and "情景 B" in sc
    assert "交叉情景" in sc, "有地缘事件时必须给交叉情景"
    assert "非投资建议" in top + sc


def test_i7_desktop_table_mobile_card(make_env):
    """≥768px 是 11 列表格；<768px 是 4 列紧凑卡片（12 字段全在，靠 data-l 标签自解释）。"""
    env = make_env(jn={"calendar.json": jn([
        e("美国10月CPI年率", 60, 4, "3.2%", "3.0%", "3.5%"),
        e("美国10月零售销售月率", 120, 3, "0.4%", "0.6%", ""),
        e("美国10月工业产出月率", 180, 2, "0.1%", "0.3%", ""),
    ])}, width=900)
    open_sig(env.page)
    info = env.page.evaluate("""() => {
        const s = getComputedStyle(document.querySelector('.evrow'));
        return {d: s.display, cols: s.gridTemplateColumns.split(' ').length};
    }""")
    assert info["d"] == "grid" and info["cols"] == 11, info
    env.close()

    env = make_env(jn={"calendar.json": jn([
        e("美国10月CPI年率", 60, 4, "3.2%", "3.0%", "3.5%"),
        e("美国10月零售销售月率", 120, 3, "0.4%", "0.6%", ""),
        e("美国10月工业产出月率", 180, 2, "0.1%", "0.3%", ""),
    ])}, width=390)
    open_sig(env.page)
    info = env.page.evaluate("""() => {
        const s = getComputedStyle(document.querySelector('.evrow'));
        return {d: s.display, cols: s.gridTemplateColumns.split(' ').length};
    }""")
    assert info["d"] == "grid" and info["cols"] == 4, info
    # 手机上字段标签由 data-l 生成，保证 12 项都能对上号
    labels = env.page.evaluate(
        "() => [...document.querySelectorAll('.evrow .c3,.evrow .c10')]"
        ".map(e => e.getAttribute('data-l'))")
    assert labels == ["数据/事件", "传导逻辑"], labels


def test_i8_mobile_no_overflow(make_env):
    """320/360px 不允许出现横向溢出。"""
    for w in (320, 360):
        env = make_env(jn={"calendar.json": jn([
            e("美国10月核心PCE物价指数月率", 60, 4, "0.3%", "0.2%", "0.4%"),
            e("美国10月零售销售月率", 120, 3, "0.4%", "0.6%", ""),
            e("美国10月工业产出月率", 180, 2, "0.1%", "0.3%", ""),
        ])}, width=w)
        open_sig(env.page)
        info = env.page.evaluate("""() => ({
            sw: document.documentElement.scrollWidth, iw: innerWidth,
            over: [...document.querySelectorAll('#evList .evrow')].map(e => e.scrollWidth - e.clientWidth),
        })""")
        assert info["sw"] <= info["iw"] + 1, (w, info)
        assert max(info["over"]) <= 1, (w, info["over"])
        env.close()


def test_i9_empty_state_explained(make_env):
    """没有 4 星事件时不能白屏，要说明为什么空、什么时候会有。"""
    env = make_env(jn={"calendar.json": jn([
        e("美国10月零售销售月率", 60, 3, "0.4%", "0.6%", ""),
        e("美国10月工业产出月率", 120, 2, "0.1%", "0.3%", ""),
        e("美国10月耐用品订单月率", 180, 1, "0.2%", "0.5%", ""),
    ])})
    env.page.wait_for_timeout(400)
    open_sig(env.page)
    body = env.page.evaluate("() => document.getElementById('evList').textContent")
    assert "暂无 4 星以上事件" in body, body
    assert "周一" in body, body
