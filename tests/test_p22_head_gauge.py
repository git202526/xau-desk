"""P22：建仓阶梯头部去重 + 重操作按钮实体化 + 仓位色阶去绿。

契约：
  · 标题行不再重复「高点 / 手动 / 待补」—— 高点只在「档位来源」行出现一次，
    待补由「档位执行 x / y 克」报
  · 正常态标题行只剩「计划模式」标签；只有超预算 / 超目标才加橙色异常标签
  · 「重算档位 / 按高点」是实体按钮（底色 + 边框 + 圆角），不再是纯文字，320 下不折行
  · 仓位条五阶「钢蓝 → 蓝 → 金 → 橙 → 红」，20 格里没有绿色
  · 百分比文字色跟着「点亮到第几阶」走，与色块同源，不是另一套阈值
"""
import math

import pytest

from conftest import CFG_EMPTY, PRICE

TOTAL = 200.0
H = 4700.0


def bars_calm(n=240, px=PRICE, amp=24.0):
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
    c = dict(CFG_EMPTY, TOTAL=TOTAL, H_BASE=H, buys=[], sells=[])
    c.update(kw)
    return c


def buys(*grams, price=900.0):
    return [{"id": "b%d" % (i + 1), "name": "买入", "gram": g, "priceCny": price,
             "time": "2026-10-%02dT02:00:00.000Z" % (i + 1), "note": ""}
            for i, g in enumerate(grams)]


@pytest.fixture
def env(make_env):
    mk_keys = ("price", "width", "height", "gh_on", "reduced_motion", "tz", "calendar")

    def _f(c=None, **kw):
        base = dict(c) if c is not None else cfg()
        mk = {}
        for k in list(kw.keys()):
            if k in mk_keys:
                mk[k] = kw.pop(k)
        base.update(kw)
        mk.setdefault("price", 4030.0)
        return make_env(cfg=base, bars=bars_calm(), **mk)
    return _f


def open_tab(page, tab):
    page.click('.tabbar button[data-tab="%s"]' % tab)
    page.wait_for_timeout(260)


def enable_plan(page):
    open_tab(page, "set")
    page.check("#setPlanOnly")
    page.click("#btnSaveSet")
    page.wait_for_timeout(320)
    open_tab(page, "plan")


def head(page):
    return page.evaluate("""() => {
        const meta = document.getElementById('ladderMeta');
        const src  = document.querySelector('#gearSrcBox .gshint');
        const tag  = meta ? meta.querySelector('.mtag') : null;
        return {
            meta: meta ? meta.textContent.trim() : '',
            src: src ? src.textContent.trim() : '',
            tagTxt: tag ? tag.textContent.trim() : '',
            tagWarn: !!(tag && tag.classList.contains('warn')),
        };
    }""")


def btns(page):
    return page.evaluate("""() => {
        const out = {};
        ['btnRecalc', 'btnRebuild'].forEach(id => {
            const b = document.getElementById(id);
            const cs = getComputedStyle(b);
            const r = b.getBoundingClientRect();
            out[id] = {
                txt: b.textContent.trim(),
                bg: cs.backgroundColor,
                bd: cs.borderTopWidth + ' ' + cs.borderTopStyle,
                radius: parseFloat(cs.borderRadius) || 0,
                h: Math.round(r.height),
                w: Math.round(r.width),
                top: Math.round(r.top),
            };
        });
        const bar = document.querySelector('#panel-plan .sact');
        const h2 = document.querySelector('#panel-plan .sech h2');
        return {
            b: out,
            sactW: bar.scrollWidth, sactC: bar.clientWidth,
            titleClipped: h2 ? h2.scrollWidth > h2.clientWidth + 1 : false,
        };
    }""")


def gauge(page):
    return page.evaluate("""() => {
        const box = document.querySelector('#posGauge');
        if (!box) return null;
        const segs = [...box.querySelectorAll('.gbar i')];
        const t = box.querySelector('.gg-h b');
        return {
            text: box.innerText.replace(/\\s+/g, ' ').trim(),
            segs: segs.length,
            on: segs.filter(x => x.classList.contains('on')).length,
            cls: t ? t.className : '',
            txtColor: t ? getComputedStyle(t).color : '',
            segBg: segs.map(x => getComputedStyle(x).backgroundColor),
            lastOnBg: (() => {
                const lit = segs.filter(x => x.classList.contains('on'));
                return lit.length ? getComputedStyle(lit[lit.length - 1]).backgroundColor : '';
            })(),
            sw: box.scrollWidth, cw: box.clientWidth,
        };
    }""")


def rgb(s):
    """'rgb(74, 115, 150)' / 'rgba(0, 0, 0, 0)' → (r, g, b, a)"""
    body = s[s.index("(") + 1:s.index(")")]
    v = [float(x.strip()) for x in body.split(",")]
    while len(v) < 4:
        v.append(1.0)
    return tuple(v)


def is_green(c):
    """G 同时显著高于 R 和 B 才算绿 —— 蓝、金、橙、红都不该命中。"""
    r, g, b, a = c
    if a < 0.2:
        return False
    return g > r + 15 and g > b + 15


# ---------------------------------------------------------------- 标题行去重

def test_h1_high_shown_once(env):
    """高点与「手动 / 自动」只在档位来源行出现，标题行不再复述。"""
    p = env().page
    open_tab(p, "plan")
    h = head(p)
    assert "高点" not in h["meta"], h
    assert "手动" not in h["meta"], h
    assert "高点" in h["src"] and "手动" in h["src"], h


def test_h2_no_pending_in_title(env):
    """待补与「档位执行 x / y 克」是同一个数，标题行不再报。"""
    p = env()
    enable_plan(p.page)
    h = head(p.page)
    assert "待补" not in h["meta"], h
    assert h["meta"] == "计划模式", h


def test_h3_over_budget_is_the_only_title_alert(env):
    """超预算才在标题行加橙色标签 —— 这是唯一需要抬头看的状态。"""
    p = env().page
    enable_plan(p)
    p.click('#ladder [data-mark="第3档"]')
    p.wait_for_timeout(200)
    p.fill("#markGram", "58")
    p.click("#markSave")
    p.wait_for_timeout(320)
    h = head(p)
    assert "已超预算 10g" in h["tagTxt"], h
    assert h["tagWarn"] is True, h


# ---------------------------------------------------------------- 按钮实体化

def test_h4_actions_are_real_buttons(env):
    """两枚重操作有底色、有边框、有圆角，不再是纯文字。"""
    p = env().page
    open_tab(p, "plan")
    d = btns(p)
    for key in ("btnRecalc", "btnRebuild"):
        b = d["b"][key]
        assert b["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), (key, b)
        assert "none" not in b["bd"], (key, b)
        assert b["radius"] >= 6, (key, b)
        assert b["h"] >= 36, (key, b)


def test_h5_primary_is_solid_secondary_is_outline(env):
    """主按钮实心、次按钮描边：实心那枚的底不透明，描边那枚的底是半透明。"""
    p = env().page
    open_tab(p, "plan")
    d = btns(p)
    a = rgb(d["b"]["btnRecalc"]["bg"])
    b = rgb(d["b"]["btnRebuild"]["bg"])
    assert a[3] > 0.8, a
    assert b[3] < 0.5, b


def test_h6_narrow_keeps_both_buttons(env):
    """320 宽下两枚按钮都完整、同一行、标题不被压掉。"""
    p = env(width=320).page
    open_tab(p, "plan")
    d = btns(p)
    assert d["sactW"] <= d["sactC"] + 1, d
    assert d["titleClipped"] is False, d
    assert abs(d["b"]["btnRecalc"]["top"] - d["b"]["btnRebuild"]["top"]) <= 1, d
    for key in ("btnRecalc", "btnRebuild"):
        assert d["b"][key]["w"] >= 54, (key, d["b"][key])


# ---------------------------------------------------------------- 色阶

def test_g1_no_green_in_any_segment(env):
    """20 段里一个绿都不许有 —— 绿在本页是「跌」，表示安全会读反。"""
    p = env(buys=buys(100)).page
    open_tab(p, "pos")
    g = gauge(p)
    assert g["segs"] == 20, g
    bad = [c for c in g["segBg"] if is_green(rgb(c))]
    assert bad == [], bad


def test_g2_pct_text_not_green(env):
    """百分比文字也不用绿（原先是 var(--dn)）。"""
    p = env(buys=buys(100)).page
    open_tab(p, "pos")
    g = gauge(p)
    assert not is_green(rgb(g["txtColor"])), g["txtColor"]
    assert "p2" in g["cls"].split(), g  # 50% 落在第 3 阶（金）


def test_g3_cls_follows_lit_segments(env):
    """文字所处分阶 = 最后一格亮段所处分阶，两套色不能各算各的。"""
    cases = [(40.0, "p0", "blue"), (100.0, "p2", "gold"),
             (150.0, "p3", "orange"), (180.0, "p4", "red")]
    for grams, want, tone in cases:
        p = env(buys=buys(grams)).page
        open_tab(p, "pos")
        g = gauge(p)
        assert want in g["cls"].split(), (grams, g)
        c = rgb(g["txtColor"])
        last = rgb(g["lastOnBg"])
        if tone == "blue":
            assert c[2] > c[0] and last[2] > last[0], (grams, c, last)
        elif tone == "gold":
            assert c[0] > c[2] and c[1] > c[2], (grams, c)
            assert last[0] > last[2], (grams, last)
        else:
            assert c[0] > c[1] and c[0] > c[2], (grams, c)
            assert last[0] > last[2], (grams, last)


def test_g4_over_limit_keeps_danger_class(env):
    """超过上限仍是独立一档，class 必须含 over。"""
    p = env(buys=buys(250)).page
    open_tab(p, "pos")
    g = gauge(p)
    assert "over" in g["cls"], g
    assert g["on"] == 20, g
    assert "超上限 50.0 g" in g["text"], g


def test_g5_zero_is_plain(env):
    """0% 不上色（中性白），避免「空仓」被读成某种状态。"""
    p = env().page
    open_tab(p, "pos")
    g = gauge(p)
    assert g["on"] == 0, g
    assert g["cls"].split() == ["num"], g
    assert not is_green(rgb(g["txtColor"])), g["txtColor"]


def test_g6_narrow_no_overflow(env):
    """390 宽下进度卡不横向溢出。"""
    p = env(buys=buys(125), width=390).page
    open_tab(p, "pos")
    g = gauge(p)
    assert g["sw"] <= g["cw"] + 1, g
