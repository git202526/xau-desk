"""P26 · 冒烟集：22 条，覆盖 10 个核心域，目标 2 分钟内跑完。

定位：**不是**全量的随机抽样，而是按链路挑——每条回答「这个功能还活着吗」，
不回答「这个功能的第 7 个边界还对吗」。边界留给全量。

用法：
    python3 -m pytest tests/test_smoke.py     # 改完先跑这个
    python3 -m pytest                          # 全量，发布前跑

写法约束（`test_p26_smoke.py` 会检查）：只用 conftest 的公共 fixture，
不 import 其它 test_* 模块的私有 helper，否则改一处会同时弄坏两层。
"""
import math

import pytest

from conftest import CFG_EMPTY

# 整份文件即冒烟层：`pytest -m smoke` 只跑这里
pytestmark = pytest.mark.smoke

H = 4700.0
TOTAL = 200.0
PX = 4030.0
PLAN6 = [16.0, 24.0, 48.0, 72.0, 24.0, 16.0]


def _bars(n=240, px=PX, amp=24.0, start=None):
    """收尾在 px 的确定性日线。

    start 给定时从 start 一路跌到 px（用来造「跌破档位」的行情）；
    不给则从 px 下方 7% 缓慢爬到 px（大部分用例想要的是「不触发」的平静行情）。
    """
    now = 1761696000000
    step = 24 * 3600 * 1000
    if start is None:
        raw = [px * 0.93 + i * (px * 0.07 / (n - 1)) + math.sin(i / 9.0) * 11 for i in range(n)]
    else:
        raw = [start + (px - start) * i / (n - 1) + math.sin(i / 9.0) * 11 for i in range(n)]
    adj = raw[-1] - px
    out = []
    for i in range(n):
        c = raw[i] - adj
        t = int(now - (n - 1 - i) * step)
        out.append([t, "%.2f" % (c - 4), "%.2f" % (c + amp), "%.2f" % (c - amp),
                    "%.2f" % c, "12.5", t + step - 1, "0", 0, "0", "0", "0"])
    return out


def _marks(grams=None):
    gs = list(grams) if grams else list(PLAN6)
    out = {}
    for i, g in enumerate(gs, 1):
        if g <= 0:
            continue
        out[str(i)] = {"g": g, "cost": round(g * 4000 / 31.1035, 2), "sold": 0,
                       "proceeds": 0, "at": 1}
    return out


def _cfg(**kw):
    """200g 六档 + 一笔 200g 底仓：卖出校验看账本持仓，没有记录会被「超过剩余持仓」挡掉。"""
    c = dict(CFG_EMPTY, H_BASE=H, TOTAL=TOTAL, swingGears=[1, 2], gearMarks=_marks(),
             buys=[{"id": "b0", "name": "底仓", "gram": 200, "priceCny": 900,
                    "time": "2026-08-01T02:00:00.000Z", "note": "", "gear": None}])
    c.update(kw)
    return c


@pytest.fixture
def page_of(make_env):
    def _f(cfg=None, width=390, bars=None, tab=None, price=PX):
        env = make_env(cfg=cfg if cfg is not None else _cfg(),
                       width=width, price=price,
                       bars=bars if bars is not None else _bars())
        p = env.page
        if tab:
            p.click('.tabbar button[data-tab="%s"]' % tab)
            p.wait_for_timeout(240)
        return p
    return _f


def _sold(p):
    return p.evaluate("() => Array.from({length:6},(_,k)=>window.__xt.gearState(k+1).soldG)")


# ==== 加载与首屏 ====

def test_s01_first_paint(page_of):
    """首屏：跑起来了、价格有值、六个 tab 都在。"""
    p = page_of()
    r = p.evaluate("""() => ({
      ready: !!window.__xt.ready,
      price: window.__xt.state().price,
      tabs: document.querySelectorAll('.tabbar button').length,
      cards: document.querySelectorAll('.card').length
    })""")
    assert r["ready"] is True
    assert r["price"] > 0, r
    assert r["tabs"] == 6, r
    assert r["cards"] >= 3, r


def test_s02_switch_all_tabs(page_of):
    """四个主 tab 轮一遍，回来仍然正常。"""
    p = page_of()
    for t in ("struct", "plan", "pos", "ov"):
        p.click('.tabbar button[data-tab="%s"]' % t)
        p.wait_for_timeout(160)
        assert p.eval_on_selector('.tabbar button[data-tab="%s"]' % t,
                                  "e => e.className") == "on"
    assert p.evaluate("() => window.__xt.state().price") > 0


def test_s03_core_cards_present(page_of):
    """持仓页三张核心卡都在：阶梯、复盘、流水。"""
    p = page_of(tab="pos")
    for sel in ("#ladder", "#posReview", "#buyList"):
        assert p.query_selector(sel) is not None, sel


# ==== 档位表 ====

def test_s04_ladder_rows(page_of):
    """档位行数 = 档数（预警行不算）。"""
    p = page_of(tab="pos")
    n = p.evaluate("() => window.__xt.state().gears.filter(g => !g.warn).length")
    assert n == 6, n
    rows = p.eval_on_selector_all("#ladder .lv", "els => els.length")
    assert rows >= 6, rows


def test_s05_plan_sum(page_of):
    """六档计划克重合计 = 目标总仓。"""
    p = page_of(tab="pos")
    total = p.evaluate("() => window.__xt.planOf().reduce((a,b)=>a+b,0)")
    assert total == pytest.approx(TOTAL, abs=0.11), total


def test_s06_net_equals_mark_minus_sold(page_of):
    """净持 = 标记克重 − 该档已卖。"""
    p = page_of(tab="pos")
    rows = p.evaluate("""() => Array.from({length:6},(_,k)=>{
      var g = window.__xt.gearState(k+1);
      return {g:k+1, bought:g.bought, sold:g.soldG};
    })""")
    mk = p.evaluate("() => window.__xt.state().cfg.gearMarks")
    for r in rows:
        m = mk.get(str(r["g"])) if isinstance(mk, dict) else mk[str(r["g"])]
        assert r["bought"] == pytest.approx(m["g"] - r["sold"], abs=1e-6), r


# ==== 标记与账本同源 ====

def test_s07_mark_sets_bought(page_of):
    """标 16g → 该档已买 16g。"""
    p = page_of(tab="pos")
    p.evaluate("() => window.__xt.markGear(1, 16)")
    p.wait_for_timeout(200)
    assert p.evaluate("() => window.__xt.boughtOf(1)") == pytest.approx(16, abs=0.01)


def test_s08_ledger_folds_into_marks(page_of):
    """只有带档买入记录、没有标记 → 加载后自动折成标记（单一口径）。"""
    cfg = _cfg(gearMarks={},
               buys=[{"id": "b0", "name": "带档", "gram": 20, "priceCny": 900,
                      "time": "2026-08-01T02:00:00.000Z", "note": "", "gear": "第2档"}])
    p = page_of(cfg=cfg, tab="pos")
    assert p.evaluate("() => window.__xt.boughtOf(2)") == pytest.approx(20, abs=0.05)


# ==== 减仓摊派 ====

def test_s09_sell_allots_all_gears(page_of):
    """卖 100g：六档卖出合计正好 100（摊派不是估算）。"""
    p = page_of(tab="pos")
    p.evaluate("() => window.__xt.openSell(null)")
    p.wait_for_timeout(140)
    p.fill('[name="g_gram"]', "100")
    p.fill('[name="g_price"]', "900")
    p.click("#btnSave")
    p.wait_for_timeout(300)
    assert sum(_sold(p)) == pytest.approx(100, abs=0.1), _sold(p)


def test_s10_delete_sell_rolls_back(page_of):
    """删掉那笔卖出：六档卖出量归零（按快照精确退回）。"""
    p = page_of(tab="pos")
    p.evaluate("() => window.__xt.openSell(null)")
    p.wait_for_timeout(140)
    p.fill('[name="g_gram"]', "100")
    p.fill('[name="g_price"]', "900")
    p.click("#btnSave")
    p.wait_for_timeout(300)

    rid = p.evaluate("() => (window.__xt.state().cfg.sells || [])[0].id")
    p.evaluate("(id) => window.__xt.openSell(null, id)", rid)
    p.wait_for_timeout(150)
    p.evaluate("() => { window.confirm = () => true; }")
    p.click("#btnDelBuy")
    p.wait_for_timeout(320)
    assert sum(_sold(p)) == pytest.approx(0, abs=0.05), _sold(p)


# ==== 执行复盘 ====

def test_s11_review_collapsed_no_scan(page_of):
    """收起时不扫 K 线（这是它敢放在首屏下面的原因）。"""
    p = page_of(tab="pos")
    assert p.evaluate("() => window.__xt.rvScans()") == 0
    assert p.eval_on_selector_all("#posReview .rv-g", "e => e.length") == 0


def test_s12_review_expands_six_rows(page_of):
    """展开：六行明细、无 NaN。"""
    p = page_of(tab="pos")
    p.eval_on_selector('#posReview [data-rv="h"]', "e => e.click()")
    p.wait_for_timeout(240)
    assert p.eval_on_selector_all("#posReview .rv-g", "e => e.length") == 6
    txt = p.eval_on_selector("#posReview", "e => e.innerText")
    for bad in ("NaN", "undefined", "Infinity"):
        assert bad not in txt, bad


# ==== 策略回测 ====

def test_s13_backtest_scan_five_rows(page_of):
    """P31：5 组档距扫描，首档价五组相同（档距不影响第 1 档）。

    用「从 5000 跌到 4030」的行情，档位会被跌破，各组才会真的成交。
    """
    p = page_of(tab="struct", bars=_bars(start=5000.0))
    rows = p.eval_on_selector_all("#bbPeers .sr", """els => els.map(e =>
      Array.from(e.children).map(c => c.innerText.trim()))""")
    assert len(rows) == 5, rows
    assert len(set(r[1] for r in rows)) == 1, rows


def test_s14_backtest_no_trade_is_zero(page_of):
    """一档没触发：净现金流与持仓都是 0，收益率是 null（不是 0）。"""
    p = page_of(tab="struct", bars=_bars(px=4600.0, amp=8.0))
    s = p.evaluate("() => window.__xt.btBack('1d',{loop:false,realloc:false,fee:0}).stats")
    assert s["trigGears"] == 0, s
    assert s["pnl"] == pytest.approx(0, abs=0.05), s
    assert s["pnlPct"] is None, s


def test_s15_backtest_no_nan(page_of):
    """回测卡文本里不出现 NaN / undefined / Infinity。"""
    p = page_of(tab="struct")
    txt = p.eval_on_selector("#btBackCard", "e => e.innerText")
    for bad in ("NaN", "undefined", "Infinity"):
        assert bad not in txt, bad
    assert "触发档位" in txt and "无预算上限" in txt, txt[:160]


# ==== 判定回溯 ====

def test_s16_backtrace_strip(page_of):
    """回溯色带有内容。

    色带按「连续同态合并」画段，所以段数不等于天数：
    单边上涨的合成行情可能就是 1 段。天数看 btRun，段数只看 ≥1。
    """
    # 默认 240 根减去 210 根预热只剩 31 天，这里给 300 根留出可回溯区间
    p = page_of(tab="struct", bars=_bars(n=300))
    days = p.evaluate("() => (window.__xt.btRun('1d') || []).length")
    assert days > 50, days
    assert p.eval_on_selector_all("#btStrip svg .btseg", "e => e.length") >= 1


def test_s17_backtrace_pick(page_of):
    """点某一天，出三态与命中条数。"""
    p = page_of(tab="struct")
    p.evaluate("() => window.__xt.btShowPick(20)")
    p.wait_for_timeout(160)
    txt = p.eval_on_selector("#btPick", "e => e.innerText")
    assert "命中" in txt, txt
    assert p.eval_on_selector("#btPick", "e => e.hidden") is False


# ==== 设置与保存 ====

def test_s18_gear_count(page_of):
    """档数 6 → 8 生效，计划仍合计 200。"""
    p = page_of(tab="pos")
    p.evaluate("() => window.__xt.setGearCount(8)")
    p.wait_for_timeout(280)
    assert p.evaluate("() => window.__xt.gearCount()") == 8
    total = p.evaluate("() => window.__xt.planOf().reduce((a,b)=>a+b,0)")
    assert total == pytest.approx(TOTAL, abs=0.2), total


def test_s19_save_survives_reload(page_of):
    """改总仓 → 保存 → 刷新，值还在（走云端，不是内存）。"""
    p = page_of(tab="pos")
    p.evaluate("""() => {
      var S = window.__xt.state();
      S.cfg.TOTAL = 240; S.TOTAL = 240;
      window.__xt.saveCfg();
      window.__xt.compute(); window.__xt.refreshAll();
    }""")
    p.wait_for_timeout(1300)          # saveCfg 的云推送有 800ms 防抖
    p.reload(wait_until="domcontentloaded")
    p.wait_for_function("() => window.__xt && window.__xt.ready", timeout=8000)
    p.wait_for_timeout(400)
    total = p.evaluate("() => window.__xt.planOf().reduce((a,b)=>a+b,0)")
    assert total == pytest.approx(240, abs=0.2), total


# ==== 响应式 ====

def test_s20_narrow_struct(page_of):
    """320 宽结构页不横向溢出。"""
    p = page_of(width=320, tab="struct")
    over = p.evaluate("""() => ({
      de: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      bd: document.body.scrollWidth - document.body.clientWidth
    })""")
    assert over["de"] <= 1 and over["bd"] <= 1, over


def test_s21_narrow_pos(page_of):
    """320 宽持仓页不横向溢出（阶梯是最挤的卡片）。"""
    p = page_of(width=320, tab="pos")
    over = p.evaluate("""() => ({
      de: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      bd: document.body.scrollWidth - document.body.clientWidth
    })""")
    assert over["de"] <= 1 and over["bd"] <= 1, over


# ==== 云端 ====

def test_s22_cloud_cfg_rendered(page_of):
    """云端配置真的进了页面：总仓与档数都来自 cfg。"""
    p = page_of(cfg=_cfg(TOTAL=180.0), tab="pos")
    assert p.evaluate("() => window.__xt.gearCount()") == 6
    total = p.evaluate("() => window.__xt.planOf().reduce((a,b)=>a+b,0)")
    assert total == pytest.approx(180, abs=0.2), total
