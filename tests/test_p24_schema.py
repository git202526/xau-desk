"""P24 · 数据版本与迁移入口。

P20 的迁移只有一个布尔 `migratedV7`，调用点被复制到 4 个「数据到达」时机上
（applyRemote / doPull 合并 / doPull 断网快照 / fetchFx 回调）。加一次迁移就要再复制 4 遍。

本期加 `schemaVersion` 并把入口收敛成 `migrateAll()`：
    版本推进的前提是**迁移真的跑完**（汇率门没过多半途而废，版本不能动）

    v1 新配置 → 版本 2（P27 起多了 v2：给标记补人民币成本）
    v2 老数据（带档记录、无标记）→ 折标记 + 版本 2
    v3 汇率没到 → 版本不推进；汇率到位补跑 → 2
    v4 已迁移过（migratedV7=true，无 schemaVersion）→ 标记一个不改，只升版本
    v5 幂等：连跑三次，标记与版本完全不变
    v6 mergeRemote 版本取 max（设备来回切换不会把版本带回去）
    v7 未来版本（99）→ 不降级、不清字段
    v8 源码断言：migrateMarksV7() 的独立调用点只剩 1 处
"""
import json
import re
from pathlib import Path

import pytest

from conftest import CFG_EMPTY

ROOT = Path(__file__).resolve().parents[1]
CX = 900.0
# 人民币 900 元/克 ≈ 2799 美元/盎司，与 mock 汇率 7.10 一致
USD_PER_G = 900.0 / 7.10


def _cfg(**kw):
    c = dict(CFG_EMPTY, H_BASE=4700, TOTAL=200)
    c.update(kw)
    return c


def _ver(p):
    return p.evaluate("() => window.__xt.state().cfg.schemaVersion")


def _marks(p):
    return p.evaluate("() => JSON.parse(JSON.stringify(window.__xt.state().cfg.gearMarks || {}))")


def test_v1_new_cfg_gets_version(make_env):
    """新配置加载完就是当前版本（v2 = P27 加的「标记补人民币成本」）。"""
    p = make_env(cfg=_cfg()).page
    assert _ver(p) == 3
    assert p.evaluate("() => window.__xt.SCHEMA") == 3
    assert p.evaluate("() => window.__xt.runMigrations()") is False


def test_v2_legacy_buys_fold_into_marks(make_env):
    """老数据：只有带档买入记录，没有标记 → 折成标记，版本升到 2。"""
    cfg = _cfg(buys=[{"id": "b1", "name": "老记录", "gram": 20, "priceCny": CX,
                      "time": "2026-08-01T02:00:00.000Z", "note": "", "gear": "第2档"}])
    p = make_env(cfg=cfg, price=4030.0).page
    assert _ver(p) == 3
    assert p.evaluate("() => window.__xt.boughtOf(2)") == pytest.approx(20, abs=0.05)
    mk = _marks(p)
    assert mk["2"]["g"] == pytest.approx(20, abs=0.05)
    # cost 按人民币成交价折美元，不是 0
    assert mk["2"]["cost"] == pytest.approx(20 * USD_PER_G, abs=1.0)


def test_v3_fx_gate_holds_version(make_env):
    """汇率没到：记录那部分不能做，版本必须停在 0；汇率到位后再补跑。"""
    p = make_env(cfg=_cfg()).page
    p.evaluate("""() => {
      var S = window.__xt.state();
      S.cfg.buys = [{id:'b1', name:'老记录', gram:20, priceCny:900,
                     time:'2026-08-01T02:00:00.000Z', note:'', gear:'第2档'}];
      S.cfg.gearMarks = {};
      S.cfg.gearsDone = [];
      S.cfg.schemaVersion = 0;
      delete S.cfg.migratedV7;
      S.fx = 0;
    }""")
    r = p.evaluate("() => window.__xt.migrateAll()")
    assert r is False, "汇率没到时不该宣称迁移完成"
    assert _ver(p) == 0
    assert p.evaluate("() => !!window.__xt.state().cfg.migratedV7") is False
    assert p.evaluate("() => window.__xt.boughtOf(2)") == pytest.approx(0, abs=1e-9)

    # 汇率到位 → 补跑
    p.evaluate("""() => {
      window.__xt.state().fx = 7.10;
      window.__xt.migrateAll();
    }""")
    p.wait_for_timeout(150)
    assert _ver(p) == 3
    assert p.evaluate("() => window.__xt.boughtOf(2)") == pytest.approx(20, abs=0.05)


def test_v4_already_migrated_keeps_marks(make_env):
    """已经迁移过（migratedV7=true）但没有版本号：只补版本，标记一个都不动。"""
    cfg = _cfg(migratedV7=True,
               gearMarks={"1": {"g": 16, "cost": 100, "sold": 0, "proceeds": 0, "at": 1}},
               buys=[{"id": "b1", "name": "老记录", "gram": 20, "priceCny": CX,
                      "time": "2026-08-01T02:00:00.000Z", "note": "", "gear": "第2档"}])
    p = make_env(cfg=cfg, price=4030.0).page
    assert _ver(p) == 3
    mk = _marks(p)
    assert list(mk.keys()) == ["1"], "迁移本已完成，不该再补第 2 档"
    assert mk["1"]["g"] == 16


def test_v5_idempotent(make_env):
    """连跑三次：版本与标记完全不变。"""
    p = make_env(cfg=_cfg()).page
    p.evaluate("() => window.__xt.migrateAll()")
    before = json.dumps(_marks(p), sort_keys=True, ensure_ascii=False)
    for _ in range(3):
        assert p.evaluate("() => window.__xt.migrateAll()") is False
    assert _ver(p) == 3
    assert json.dumps(_marks(p), sort_keys=True, ensure_ascii=False) == before


def test_v6_merge_takes_max_version(make_env):
    """并集合并时版本取 max：本机 0 + 远端 2 → 2，不会被带回去重跑。"""
    p = make_env(cfg=_cfg()).page
    out = p.evaluate("""() => {
      var S = window.__xt.state();
      S.cfg.schemaVersion = 0;
      var merged = window.__xt.mergeRemote({schemaVersion: 2, buys: [], sells: []}, false);
      return merged.schemaVersion;
    }""")
    assert out == 2, out


def test_v7_future_version_untouched(make_env):
    """读到未来版本（99）：不降级、不清字段。"""
    cfg = _cfg(schemaVersion=99, gearMarks={"1": {"g": 16, "cost": 1, "sold": 0,
                                                  "proceeds": 0, "at": 1}})
    p = make_env(cfg=cfg).page
    assert _ver(p) == 99
    assert p.evaluate("() => window.__xt.runMigrations()") is False
    assert "1" in _marks(p), "未来版本不该清掉已有标记"


def test_v8_single_call_site():
    """源码断言：migrateMarksV7() 的独立调用点只剩 runMigrations 里那一处。

    这条锁的不是运行时，而是结构：没有它，下一期很容易又在某个分支里补一个直调。
    """
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    calls = re.findall(r"(?<!function )migrateMarksV7\(\);", src)
    assert len(calls) == 1, "迁移调用点应为 1 处，实际 %d 处" % len(calls)
    assert "function runMigrations()" in src
    assert "function migrateAll()" in src
