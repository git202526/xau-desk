"""上线一致性门禁：本地改完到用户能打开，中间不能断。

为什么需要这一组
----------------
v6.35～v6.43 九个版本开发完都只提交在本地，24 小时没推送，线上一直停在 v6.34。
原因是所有验收条件（用例全绿 / 全量通过 / 工作区干净）都在本地闭环，
**没有一条会因为「没上线」而变红**，848 条测试全都 `page.goto(localhost)`。

这组测试补的就是这条跨边界的断言，默认不跑（见 pytest.ini 的 `-m "not deploy"`），
收尾时显式执行：

    python3 -m pytest -m deploy -v

五条按链路顺序排列，失败在哪一条就说明断在哪一段：

    d1  工作区干净          —— 改完没提交
    d2  本地与 origin 同步   —— 提交了没推
    d3  线上可访问          —— 推了但 Pages 挂了 / 没构建
    d4  线上字节 == 本地     —— 构建成功但不是这份文件（缓存、部署目录错）
    d5  版本号一致          —— 给 d4 一个能读懂的失败说明

d3/d4/d5 需联网，d1/d2 需要 git 仓库。无 remote 时 d2 跳过而不是失败。
"""
import hashlib
import re
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.deploy

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
LIVE = "https://git202526.github.io/xau-desk/"

# 线上至少该有这么大，低于此基本是 404 页面或空壳
MIN_BYTES = 100_000


def _git(*args, timeout=60):
    return subprocess.run(["git", *args], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=timeout)


def _local_version():
    head = INDEX.read_text(encoding="utf-8", errors="replace")[:4000]
    m = re.search(r"XAU Desk (v[\d.]+)", head)
    assert m, "本地 index.html 头部取不到版本号（注释格式被改过？）"
    return m.group(1)


def _fetch_live(attempts=3, wait=5):
    """拉线上首页，带唯一查询串破 CDN 缓存，失败重试。"""
    last = ""
    for i in range(attempts):
        try:
            r = requests.get(
                LIVE,
                params={"v": uuid.uuid4().hex},
                headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
                timeout=60,
            )
            if r.status_code == 200 and len(r.content) >= MIN_BYTES:
                return r
            last = "HTTP %s，%d 字节（低于下限 %d）" % (r.status_code, len(r.content), MIN_BYTES)
        except Exception as e:  # 网络异常也重试
            last = "%s: %s" % (type(e).__name__, e)
        if i < attempts - 1:
            time.sleep(wait)
    pytest.fail("线上拉取失败（重试 %d 次）：%s\n地址：%s\n"
                "→ 先确认本机网络能打开该地址，再判断是不是 Pages 没构建。" % (attempts, last, LIVE))


# ==== d1 本地：改动都进仓库了 ====
def test_d1_worktree_clean():
    """有未提交改动就说明还没到「可以发布」的状态。"""
    out = _git("status", "--porcelain").stdout.strip()
    assert out == "", "工作区有未提交改动，先提交再上线：\n" + out


# ==== d2 本地：提交都推上去了 ====
def test_d2_pushed_to_origin():
    """两级判断：本地引用判「没推」，远端真实值判「远端被改过」。

    为什么不用 git fetch：github.com 的 fetch 会卡在对象协商上（实测 45~60 秒无响应），
    而 ls-remote 只取 ref 列表，几百毫秒。用 fetch 会让门禁自己变成不稳定源。
    """
    if not _git("remote").stdout.strip():
        pytest.skip("本地仓库没有 remote，跳过推送检查")

    # 第一级：HEAD 领先本地 origin/main 引用 —— 离线也能判，抓的就是「忘推」
    ahead, _behind = _git("rev-list", "--left-right", "--count",
                            "HEAD...origin/main").stdout.split()
    assert int(ahead) == 0, (
        "本地领先 origin/main %s 条，肯定有提交没推。\n"
        "→ git push origin main，再等 Pages 构建（约 1~2 分钟）。" % ahead
    )

    # 第二级：远端 main 的真实 SHA 与 HEAD 一致（抓远端被别人改过）
    try:
        out = _git("ls-remote", "origin", "refs/heads/main", timeout=60)
    except subprocess.TimeoutExpired:
        pytest.skip("ls-remote 超时，未核对远端真实状态（本地引用已确认不领先，但网络不稳）")
    if out.returncode != 0 or not out.stdout.strip():
        pytest.skip("ls-remote 不通，未核对远端真实状态：%s" % out.stderr.strip()[:200])

    remote_sha = out.stdout.split()[0]
    local_sha = _git("rev-parse", "HEAD").stdout.strip()
    assert remote_sha == local_sha, (
        "远端 main 指向 %s，本地 HEAD 是 %s。\n"
        "→ 远端比本地旧：没推。\n"
        "→ 远端比本地新：别处（含 calendar.yml）推过，先 git pull --rebase 再重跑。"
        % (remote_sha[:8], local_sha[:8])
    )


# ==== d3 线上：页面还在 ====
def test_d3_live_reachable():
    """HTTP 200 且体积正常。Pages 构建期间会短暂 404/旧版。"""
    r = _fetch_live()
    assert r.status_code == 200, "线上返回 %s" % r.status_code
    assert len(r.content) >= MIN_BYTES, "线上只有 %d 字节，像是空壳页" % len(r.content)


# ==== d4 线上：就是这份文件 ====
def test_d4_live_bytes_equal():
    """逐字节比对。这一条才是「真的上线了」。"""
    r = _fetch_live()
    live_sha = hashlib.sha256(r.content).hexdigest()
    local_sha = hashlib.sha256(INDEX.read_bytes()).hexdigest()
    assert live_sha == local_sha, (
        "线上文件与本地 index.html 不是同一份：\n"
        "  线上 %d 字节 sha256=%s\n  本地 %d 字节 sha256=%s\n"
        "→ 已推送：多半是 Pages 还在构建（约 1~2 分钟），稍后重跑。\n"
        "→ 未推送：先 git push，再等构建。"
        % (len(r.content), live_sha[:16], INDEX.stat().st_size, local_sha[:16])
    )


# ==== d5 线上：版本号对得上 ====
def test_d5_version_matches():
    """给 d4 一个不用算哈希也能读懂的失败信息。"""
    r = _fetch_live()
    text = r.text
    m = re.search(r"XAU Desk (v[\d.]+)", text[:4000])
    assert m, "线上页面头部取不到版本号，可能拉到了错误页面"
    live_ver, local_ver = m.group(1), _local_version()
    assert live_ver == local_ver, (
        "线上还在 %s，本地已经是 %s —— 有版本没上线。\n"
        "→ 若已 push：等 Pages 构建完（约 1~2 分钟）再重跑。\n"
        "→ 若没 push：d2 应该先红，回看那一条。" % (live_ver, local_ver)
    )
