"""P14-2 设置页速览条：把「滑动找控件」换成「点一下」。

手机一屏 844px，原来从页首滑到「档位分布」要走 1.45 屏、到「配额下沉」2.3 屏。
顶部五枚只读 chip 各自绑定一个目标控件，点完滚到视口中央并闪一下：

  [8 档] [α 1.20] [锚定 第4档] [合计 100%] [下沉 开]

脱锚（bdSet 亮起）时点底栏「设置」，也直接落到「建仓高点」而不是停在页首。
"""
import pytest

from test_p11_weight_sum import cfg, BARS


def cfg_rich(**kw):
    c = cfg(gearCount=8, wPct=[5, 7, 11, 16, 20, 18, 14, 9], TOTAL=310, planOnly=True,
            swingGears=[1, 2], realloc={"on": True, "mode": "near", "ratio": 1, "maxDrop": 3},
            spanMode="alpha", alpha=1.2, avgAnchor=4)
    c.update(kw)
    return c


@pytest.fixture
def env(make_env):
    def _f(c=None, **kw):
        base = dict(c) if c is not None else cfg_rich()
        mk = {}
        for k in list(kw.keys()):
            if k in ("width", "height", "price", "gh_on", "reduced_motion", "tz", "calendar"):
                mk[k] = kw.pop(k)
        base.update(kw)
        mk.setdefault("bars", BARS)
        return make_env(cfg=base, **mk)
    return _f


def open_set(page):
    page.click('.tabbar button[data-tab="set"]')
    page.wait_for_timeout(450)


def nav(page):
    return page.evaluate("""() => [...document.querySelectorAll('#setNav button')].map(b => ({
        t: b.textContent.trim(), jump: b.getAttribute('data-jump')
    }))""")


def in_view(page, sel):
    return page.evaluate("""(s) => {
        const r = document.querySelector(s).getBoundingClientRect();
        return {top: Math.round(r.top), bottom: Math.round(r.bottom), vh: window.innerHeight};
    }""", sel)


# ---------------------------------------------------------------- 渲染

def test_n1_five_chips(env):
    """五枚 chip，顺序：档数 → 档距 → 锚定 → 合计 → 下沉。"""
    p = env().page
    open_set(p)
    n = nav(p)
    assert len(n) == 5, n
    assert n[0]["jump"] == "setGearCount"
    assert n[3]["jump"] == "setWGrid"
    assert n[4]["jump"] == "setReOn"


def test_n2_chip_text(env):
    """文案是「标签 + 当前值」，值取自实际配置而不是模板。"""
    p = env().page
    open_set(p)
    t = [x["t"] for x in nav(p)]
    assert t[0] == "8 档", t
    assert t[1] == "α 1.20", t
    assert t[2] == "锚定第4档", t
    assert t[3] == "合计100%", t
    assert t[4] == "下沉开", t


def test_n3_follows_gear_count(env):
    """切 10 档：chip 立刻变「10 档」。"""
    p = env().page
    open_set(p)
    p.evaluate("() => window.__xt.setGearCount(10)")
    p.wait_for_timeout(400)
    assert nav(p)[0]["t"] == "10 档"


def test_n4_follows_weight_sum(env):
    """权重超 100%：合计 chip 显示真实合计。"""
    p = env(gearCount=8, wPct=[4, 5, 8, 11, 14, 16, 50, 50]).page
    open_set(p)
    assert nav(p)[3]["t"] == "合计158%", nav(p)


def test_n5_anchor_auto(env):
    """没设锚定档位时显示「自动」。"""
    p = env(avgAnchor=0).page
    open_set(p)
    assert nav(p)[2]["t"] == "锚定自动", nav(p)


def test_n6_ledger_realloc_on(env):
    """P15 需求反转：账本模式下沉同样生效 → chip 显示「开」而不是「—」。"""
    p = env(planOnly=False).page
    open_set(p)
    assert nav(p)[4]["t"] == "下沉开", nav(p)


# ---------------------------------------------------------------- 跳转

def test_n7_jump_weight(env):
    """点「合计」：档位分布滚到视口内。"""
    p = env().page
    open_set(p)
    assert in_view(p, "#setWGrid")["top"] > 844, "初始应在首屏之外"
    p.click('#setNav button[data-jump="setWGrid"]')
    p.wait_for_timeout(900)
    v = in_view(p, "#setWGrid")
    assert 0 <= v["top"] < v["vh"], v


def test_n8_jump_gear_count(env):
    """点「8 档」：档位数量控件滚到视口内。"""
    p = env().page
    open_set(p)
    p.click('#setNav button[data-jump="setGearCount"]')
    p.wait_for_timeout(900)
    v = in_view(p, "#setGearCount")
    assert 0 <= v["top"] < v["vh"], v


def test_n9_jump_flash(env):
    """跳转后目标闪一下（flash-hold），1.3 秒后自动摘掉，不留残余类。"""
    p = env().page
    open_set(p)
    p.click('#setNav button[data-jump="setWGrid"]')
    p.wait_for_timeout(120)
    assert p.evaluate("() => document.getElementById('setWGrid').classList.contains('flash-hold')") is True
    p.wait_for_timeout(1400)
    assert p.evaluate("() => document.getElementById('setWGrid').classList.contains('flash-hold')") is False


def test_n10_jump_realloc(env):
    """点「下沉」：落到配额下沉开关（卡 C 深处）。"""
    p = env().page
    open_set(p)
    p.click('#setNav button[data-jump="setReOn"]')
    p.wait_for_timeout(900)
    v = in_view(p, "#setReOn")
    assert 0 <= v["top"] < v["vh"], v


def test_n11_off_anchor_jumps_high(env):
    """基准脱锚（bdSet 亮）时点底栏「设置」：直接落在建仓高点。"""
    p = env().page
    open_set(p)
    p.evaluate("() => { const b = document.getElementById('bdSet'); b.hidden = false; b.textContent = '1'; }")
    p.click('.tabbar button[data-tab="ov"]')
    p.wait_for_timeout(300)
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(1000)
    v = in_view(p, "#setHigh")
    assert 0 <= v["top"] < v["vh"], v


def test_n12_no_anchor_when_badge_off(env):
    """badge 没亮时点设置：停在页首，不乱滚。"""
    p = env().page
    open_set(p)
    p.evaluate("() => { const b = document.getElementById('bdSet'); b.hidden = true; }")
    p.click('.tabbar button[data-tab="ov"]')
    p.wait_for_timeout(300)
    p.click('.tabbar button[data-tab="set"]')
    p.wait_for_timeout(600)
    y = p.evaluate("() => Math.round(window.scrollY)")
    assert y < 20, y


# ---------------------------------------------------------------- 窄屏

def test_n13_narrow_scrollable(env):
    """320 宽：chips 不换行，靠横滑（scrollWidth > clientWidth 是可滑动的证据）。"""
    p = env(width=320).page
    open_set(p)
    d = p.evaluate("""() => {
        const box = document.getElementById('setNav');
        const tops = [...box.querySelectorAll('button')].map(b => Math.round(b.getBoundingClientRect().top));
        return {sw: box.scrollWidth, cw: box.clientWidth, tops,
                bodyOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth};
    }""")
    assert len(set(d["tops"])) == 1, d["tops"]          # 同一行
    assert d["sw"] >= d["cw"], d                        # 允许横滑
    assert d["bodyOverflow"] <= 0, d                    # 但不许把整页撑宽


def test_n14_chip_tap_height(env):
    """chip 点击高度 ≥ 40px（项目内 .act 的同一档），手指能点准。"""
    p = env(width=320).page
    open_set(p)
    h = p.evaluate("() => Math.round(document.querySelector('#setNav button').getBoundingClientRect().height)")
    assert h >= 40, h
