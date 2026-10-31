"""P12 下沉标签的单位：数字是克，不是百分比。

用户看到「+2.2 下沉入」，不知道 2.2 是克还是百分比——旁边都是 -17.4%、-2.0% 这类百分数，
很容易读成占比。这一期只补「可读性」，不动数据：

  · 数字后面的 g 用 CSS ::after 画（textContent 不变，P8 的文案契约保持不变）
  · title 属性给一句「从已跌破档收到的待补克重」
  · 建仓页 note 解释「下沉入 / 下沉出」的来源

口径同 P8 第 2 期：H=4700、总仓 200g、档距 48、现价 4030 → 第 1 档失效，
16g 挪给第 2 档。
"""
import math

import pytest

from conftest import CFG_EMPTY

H = 4700.0
W = 200.0
PX = 4030.0


def bars_calm(n=240, px=PX, amp=24.0):
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
             swingGears=[1, 2], gearMarks={},
             realloc={"on": True, "mode": "near", "ratio": 1, "maxDrop": 3})
    c.update(kw)
    return c


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
        mk.setdefault("bars", bars_calm())
        mk.setdefault("price", PX)
        return make_env(cfg=base, **mk)
    return _f


def to_plan(p):
    p.evaluate("""() => {
        if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
        window.scrollTo(0, 0);
    }""")
    p.wait_for_timeout(340)
    p.click('.tabbar button[data-tab="plan"]')
    p.wait_for_timeout(460)


def shift_info(p, gear):
    return p.evaluate("""(g) => {
        const row = [...document.querySelectorAll('#ladder .body')].find(x => x.dataset.gear === g);
        if(!row) return null;
        const s = row.querySelector('.shift');
        if(!s) return null;
        const b = s.querySelector('b');
        const cs = getComputedStyle(b, '::after');
        return {
            txt: s.textContent.trim(),
            bTxt: b ? b.textContent.trim() : null,
            after: cs.content,
            afterFs: cs.fontSize,
            title: s.getAttribute('title'),
            inCls: s.classList.contains('in'),
        };
    }""", gear)


def test_u1_unit_comes_from_css(env):
    """数字后必须有 g；它由 ::after 提供，所以 textContent 还是「+16 下沉入」。"""
    p = env().page
    to_plan(p)
    s = shift_info(p, "第2档")
    assert s is not None, "第 2 档应收到下沉"
    assert s["txt"] == "+16 下沉入", s        # P8 的文案契约保持不变
    assert s["bTxt"] == "+16", s
    assert s["after"] in ('"g"', "'g'"), s
    assert s["inCls"] is True


def test_u2_donor_also_has_unit(env):
    p = env().page
    to_plan(p)
    s = shift_info(p, "第1档")
    assert s is not None, "第 1 档应让出额度"
    assert s["txt"] == "−16 下沉出", s
    assert s["after"] in ('"g"', "'g'"), s
    assert s["inCls"] is False


def test_u3_title_explains_direction(env):
    """hover 提示要说明这个数从哪来 / 去哪了，避免「下沉」两字两种理解。"""
    p = env().page
    to_plan(p)
    inn = shift_info(p, "第2档")
    out = shift_info(p, "第1档")
    assert inn["title"] and "收到" in inn["title"], inn
    assert out["title"] and "让给" in out["title"], out


def test_u4_note_explains_unit(env):
    """建仓页说明里要点明单位是克，并给出 +2.2g 的读法。"""
    p = env().page
    to_plan(p)
    note = p.evaluate("""() => {
        const n = [...document.querySelectorAll('#panel-plan .note')].pop();
        return n ? n.textContent.replace(/\\s+/g, ' ') : '';
    }""")
    assert "克重" in note, note
    assert "下沉入" in note and "下沉出" in note, note
    assert "2.2" in note, note


def test_u5_still_tappable(env):
    """加了 b/title 之后，点胶囊仍弹明细（事件委托不能被打断）。"""
    p = env().page
    to_plan(p)
    p.click('#ladder [data-shift="第2档"]')
    p.wait_for_timeout(320)
    t = p.evaluate("() => document.getElementById('toast').textContent")
    assert "下沉" in t and "16" in t, t
    assert p.evaluate("() => document.getElementById('sheet').classList.contains('on')") is False


def test_u6_no_overflow_320(env):
    p = env(width=320).page
    to_plan(p)
    info = p.evaluate("""() => ({
        sw: document.documentElement.scrollWidth, iw: innerWidth,
        rowW: (() => { const r = [...document.querySelectorAll('#ladder .body')]
            .find(x => x.dataset.gear === '第2档');
            return r ? r.scrollWidth - r.clientWidth : 0; })(),
    })""")
    assert info["sw"] <= info["iw"] + 1, info
    assert info["rowW"] <= 1, info
