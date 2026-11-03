"""P26 元测试：守「冒烟层还在，而且没退化成别的东西」。

这一组测的不是页面，是测试本身：
    t1  冒烟文件存在且规模合理（18–30 条）
    t2  pytest.ini 注册了 smoke / slow
    t3  冒烟不 import 其它 test_* 模块（否则两层会一起坏）
    t4  覆盖域 ≥ 8（按 `# ==== 域名 ====` 分区计数）
    t5  `pytest -m smoke --collect-only` 真的能收到 19 条（marker 名没写错）
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tests" / "test_smoke.py"
INI = ROOT / "pytest.ini"

SRC = SMOKE.read_text(encoding="utf-8")


def test_t1_smoke_size():
    """18–30 条：少于 18 覆盖不住十个域，多于 30 就跑不进两分钟。"""
    assert SMOKE.exists(), "缺少 tests/test_smoke.py"
    n = len(re.findall(r"^def test_", SRC, re.M))
    assert 18 <= n <= 30, "冒烟用例数 %d 超出 18–30" % n


def test_t2_ini_markers():
    """markers 段必须有 smoke 与 slow，且 strict-markers 打开。"""
    assert INI.exists(), "缺少 pytest.ini"
    txt = INI.read_text(encoding="utf-8")
    assert "smoke:" in txt and "slow:" in txt, txt
    assert "--strict-markers" in txt, "未开 strict-markers，写错 marker 会静默少跑"


def test_t3_no_cross_import():
    """冒烟文件不得 import 其它测试模块的私有 helper。"""
    bad = re.findall(r"^\s*(?:from|import)\s+(test_\w+)", SRC, re.M)
    assert bad == [], "冒烟文件 import 了其它测试模块：" + str(bad)


def test_t4_domain_coverage():
    """按分区注释数域，至少 8 个。"""
    domains = set(re.findall(r"^# ==== (.+?) ====$", SRC, re.M))
    assert len(domains) >= 8, "冒烟只覆盖 %d 个域：%s" % (len(domains), sorted(domains))


def test_t5_marker_collects():
    """子进程跑 `-m smoke --collect-only`，确认 19 条都能被 marker 选中。

    v6.49 策略回测下线带走 3 条（s13/s14/s15），22 → 19。

    不真正执行用例（--collect-only），约 2 秒。
    addopts 被清空，避免 -q 叠加成 -qq 后连 nodeid 都不打印。
    """
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "smoke", "--collect-only", "-q",
         "-o", "addopts="],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    m = re.search(r"(\d+)/(\d+) tests collected", out.stdout)
    assert m, out.stdout[-400:]
    picked, total = int(m.group(1)), int(m.group(2))
    assert picked == 19, "marker 只选中 %d 条（应为 19）" % picked
    assert total > picked, "全量没被排除，marker 可能没生效"
