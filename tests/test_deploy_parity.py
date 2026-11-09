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
    d2  提交的内容已在远端   —— 提交了但没部署
    d3  线上可访问          —— 部署了但 Pages 挂了 / 没构建
    d4  线上字节 == 本地     —— 构建成功但不是这份文件（缓存、部署目录错）
    d5  版本号一致          —— 给 d4 一个能读懂的失败说明

d3/d4/d5 需联网，d1/d2 需要 git 仓库。无 remote 时 d2 跳过而不是失败。

d2 为什么判「内容」而不是「commit sha」
------------------------------------
v7.6 P6 起部署改走 Git Data API（`tools/deploy.py`），因为这台机器到
github.com:443 的 `git push` 会卡在对象协商上。API 生成的 commit 以远端 head
为 parent、tree 与本地一致，但 **sha 与本地 HEAD 一定不同**（commit 对象内容不同）。
所以「远端 sha == 本地 HEAD」这条判据在 API 部署下恒红，是个假警报。
真正要守的是「用户打开线上看到的就是我提交的这份 index.html」，于是改成比
`HEAD:index.html` 与远端 tree 里 index.html 的 blob sha —— 同一份内容 sha 必然相同，
不管它是 push 上去的还是 API 拼出来的。
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


# ==== d2 本地：提交的内容已到远端 ====
def _deploy_tool():
    """复用部署工具的 token 解析与 API 封装，门禁不再自带一套凭据逻辑。"""
    import importlib.util
    path = ROOT / "tools" / "deploy.py"
    assert path.exists(), "tools/deploy.py 不见了，部署链路断了"
    spec = importlib.util.spec_from_file_location("xau_deploy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_d2_content_deployed():
    """两级判断：sha 相同即过；sha 不同则比 index.html 的 blob 内容。

    为什么不用 git fetch：github.com 的 fetch 会卡在对象协商上（实测 45~60 秒无响应），
    而 ls-remote 只取 ref 列表，几百毫秒。用 fetch 会让门禁自己变成不稳定源。
    """
    if not _git("remote").stdout.strip():
        pytest.skip("本地仓库没有 remote，跳过部署检查")

    local_head = _git("rev-parse", "HEAD").stdout.strip()
    local_blob = _git("rev-parse", "HEAD:index.html").stdout.strip()
    assert local_blob, "取不到 HEAD:index.html 的 blob sha（index.html 还没提交？）"

    # 第一级：远端 main 的 sha 与本地 HEAD 相同 → 普通 git push 路径，直接过。
    # 这台机器到 github.com:443 时通时断，单次 60 秒常超时，改为 25 秒 × 2 次。
    remote_head = None
    for i in range(2):
        try:
            out = _git("ls-remote", "origin", "refs/heads/main", timeout=25)
            if out.returncode == 0 and out.stdout.strip():
                remote_head = out.stdout.split()[0]
                break
        except subprocess.TimeoutExpired:
            pass
        if i == 0:
            time.sleep(2)
    if remote_head and remote_head == local_head:
        return

    # 第二级：sha 不同（多半是 API 部署）→ 比内容。取远端 tree 里 index.html 的 blob。
    try:
        dep = _deploy_tool()
        api = dep.Api(dep.get_token())
        rhead = api.get("/git/ref/heads/main")["object"]["sha"]
        if rhead == local_head:
            return
        tree = api.get("/git/commits/" + rhead)["tree"]["sha"]
        data = api.get("/git/trees/" + tree, params={"recursive": "1"})
        blobs = {e["path"]: e["sha"] for e in data["tree"] if e["type"] == "blob"}
        remote_blob = blobs.get("index.html")
    except (SystemExit, Exception) as e:  # noqa: BLE001
        pytest.skip("拿不到远端 tree（token 或网络问题），d4 另证线上字节一致：%s"
                    % str(e)[:160])

    assert remote_blob == local_blob, (
        "远端 main（%s）里的 index.html 与本地 HEAD（%s）不是同一份：\n"
        "  远端 %s\n  本地 %s\n"
        "→ 远端比本地旧：提交了没部署，跑 python3 tools/deploy.py\n"
        "→ 远端比本地新：别处推过（Actions 只写数据文件，不会动 index.html），"
        "先 git pull --rebase 再重跑。\n"
        "→ 若确认已部署：Pages 构建有延迟，等 1~2 分钟或加 --wait 180。"
        % (rhead[:8], local_head[:8], (remote_blob or "无")[:16], local_blob[:16])
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
