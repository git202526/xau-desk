"""P108 部署工具（tools/deploy.py）的单元测试 —— 不联网、不碰真仓库。

为什么要有这一组
----------------
v7.6 P6 上线时踩了两件事，都不是业务 bug：

1. `git push` 到 github.com:443 超时，只能临时写一段一次性脚本走
   Git Data API（blobs → trees → commits → PATCH ref）。脚本里 commit sha
   是硬编码的，下次复用还得重新改，等于每次上线现场写代码。
2. 同一次推送把 `xau/klines.json` 从 11-09 02:20 的版本覆盖回 11-08 的旧快照，
   回退 1 根日线 7.7 小时。原因是本地仓库的快照比 Actions 刚提交的旧，
   而脚本按「本地整份快照覆盖远端」推送，不区分代码与数据产物。

所以部署工具必须自带两道闸：**按 blob 内容算差异**（只推真正变了的文件，
不再整份覆盖）、**数据文件按时间戳拦截回退**。这组测试锁的就是这两道闸
和「dry-run 真的不写远端」。

跑法：python3 -m pytest tests/test_p108_deploy_tool.py -v
"""
import base64
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "deploy.py"


@pytest.fixture(scope="module")
def dep():
    assert TOOL.exists(), "tools/deploy.py 不存在"
    spec = importlib.util.spec_from_file_location("xau_deploy", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ==== T108a 本地算的 blob sha 必须和 git 自己算的一致 ====
def test_t108a_blob_sha_matches_git(dep, tmp_path):
    """GitHub tree 里的 blob sha 就是 git 的 blob sha。

    只有两者算法一致，才能拿本地 `git ls-tree` 的结果去和远端 tree 比，
    从而在不下载任何文件的前提下算出「哪些文件真的变了」。
    """
    f = tmp_path / "sample.txt"
    body = "v7.6 · 11/09\n中文也要一致\n".encode()
    f.write_bytes(body)
    want = subprocess.check_output(["git", "hash-object", str(f)]).decode().strip()
    assert dep.git_blob_sha(body) == want


# ==== T108b 三种时间戳写法都要能读 ====
@pytest.mark.parametrize("value,expect", [
    (1794190807163, 1794190807.163),          # klines.json：毫秒
    (1794190807, 1794190807.0),               # 秒
    ("2026-11-08T15:32:29.518010+00:00", 1794151949.518010),  # calendar.json：ISO
    (None, None),
    ("", None),
    ("not-a-time", None),
])
def test_t108b_parse_stamp(dep, value, expect):
    got = dep.parse_stamp(value)
    if expect is None:
        assert got is None
    else:
        assert abs(got - expect) < 0.01


# ==== T108c 截图和缓存永远不进部署包 ====
@pytest.mark.parametrize("path,ignored", [
    ("screenshots/ship-v76-m.png", True),
    ("mockups/_latest/a.png", True),
    ("tools/__pycache__/deploy.cpython-311.pyc", True),
    ("versions/v6.0.html", True),
    ("index.html", False),
    ("xau/klines.json", False),
    ("tools/deploy.py", False),
])
def test_t108c_ignore_rules(dep, path, ignored):
    assert dep.should_ignore(path) is ignored


# ==== T108d 差异分类：新增 / 修改 / 删除 ====
def test_t108d_plan_classifies(dep):
    local = {"index.html": "a" * 40, "new.md": "b" * 40, "same.js": "c" * 40}
    remote = {"index.html": "z" * 40, "same.js": "c" * 40, "gone.md": "d" * 40}
    changes, same = dep.plan_push(local, remote, prune=True)
    by = {c["path"]: c["action"] for c in changes}
    assert by == {"index.html": "modify", "new.md": "add", "gone.md": "delete"}
    assert same == 1


def test_t108d_prune_off_keeps_remote_only(dep):
    """默认不删远端独有文件：Pages 构建产物、Actions 刚写的产物都不该被本地缺失误删。"""
    changes, _ = dep.plan_push({"a": "1"}, {"a": "1", "b": "2"}, prune=False)
    assert changes == []


# ==== T108e/f/g 数据新鲜度闸门 ====
def _doc(ts):
    return json.dumps({"updated": ts, "payload": "x" * 50}).encode()


def test_t108e_stale_data_blocked(dep):
    """本地旧于远端 → 拦下。这是 klines 回退事故的直接防线。"""
    local, remote = _doc(1794190000000), _doc(1794190807163)
    verdict, detail = dep.freshness("xau/klines.json", local, remote)
    assert verdict == "stale"
    assert "11-08" in detail or "旧" in detail


def test_t108f_newer_data_passes(dep):
    verdict, _ = dep.freshness("xau/klines.json", _doc(1794190900000), _doc(1794190807163))
    assert verdict == "ok"


def test_t108g_remote_missing_is_ok(dep):
    """远端没有这个文件 = 新增，谈不上回退。"""
    verdict, _ = dep.freshness("macro.json", _doc(1794190900000), None)
    assert verdict == "ok"


def test_t108h_unparsable_stamp_not_blocked(dep):
    """读不出时间戳时不能瞎拦 —— 代码能看懂，数据文件格式未来可能变。"""
    verdict, _ = dep.freshness("xau/klines.json", b'{"payload":1}', b'{"payload":2}')
    assert verdict == "unknown"

    verdict, _ = dep.freshness("index.html", b"<html>", b"<html>")
    assert verdict == "unknown", "非数据文件不该走新鲜度判定"


# ==== T108i 主流程：dry-run 不产生任何写请求 ====
class FakeApi:
    """记录所有调用，返回足够让主流程跑完的假响应。"""

    def __init__(self, head="f" * 40, tree="e" * 40, entries=None, blobs=None):
        self.head = head
        self.tree = tree
        self.entries = entries or []
        self.blobs = blobs or {}
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        if path.startswith("/git/ref/heads/"):
            return {"object": {"sha": self.head}}
        if path.startswith("/git/commits/"):
            return {"tree": {"sha": self.tree}}
        if path.startswith("/git/trees/"):
            return {"tree": self.entries, "truncated": False}
        if path.startswith("/git/blobs/"):
            sha = path.rsplit("/", 1)[-1]
            return {"content": base64.b64encode(self.blobs[sha]).decode(),
                    "encoding": "base64"}
        raise AssertionError("unexpected GET " + path)

    def post(self, path, payload):
        self.calls.append(("POST", path))
        if path == "/git/blobs":
            raw = base64.b64decode(payload["content"])
            return {"sha": dep_sha(raw)}
        if path == "/git/trees":
            return {"sha": "1" * 40}
        if path == "/git/commits":
            return {"sha": "2" * 40}
        raise AssertionError("unexpected POST " + path)

    def patch(self, path, payload):
        self.calls.append(("PATCH", path))
        return {"object": {"sha": payload["sha"]}}

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PATCH")]


def dep_sha(raw):
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(raw))
    h.update(raw)
    return h.hexdigest()


def test_t108i_dry_run_never_writes(dep, tmp_path):
    (tmp_path / "index.html").write_text("<html>new</html>", encoding="utf-8")
    api = FakeApi()
    res = dep.Deployer(api, str(tmp_path), message="test", dry_run=True,
                       out=lambda *_: None).run()
    assert api.writes() == [], "dry-run 竟然写了远端：%s" % api.writes()
    assert res["pushed"] == [] or all(c["action"] != "delete" for c in res["pushed"])


# ==== T108j 主流程：只推变了的，且提交以远端 head 为 parent ====
def test_t108j_pushes_only_changed_with_remote_parent(dep, tmp_path):
    changed = b"<html>v2</html>"
    same = b"<html>v1</html>"
    (tmp_path / "index.html").write_bytes(changed)
    (tmp_path / "README.md").write_bytes(same)
    (tmp_path / "screenshots").mkdir()
    (tmp_path / "screenshots" / "a.png").write_bytes(b"png")

    api = FakeApi(entries=[
        {"path": "index.html", "type": "blob", "sha": "0" * 40},
        {"path": "README.md", "type": "blob", "sha": dep_sha(same)},
    ], blobs={})
    res = dep.Deployer(api, str(tmp_path), message="chore: deploy", dry_run=False,
                       out=lambda *_: None).run()

    pushed = {c["path"] for c in res["pushed"]}
    assert pushed == {"index.html"}, "只该推 index.html，实际 %s" % pushed
    assert res["commit"], "应产生新 commit"

    posts = [c for c in api.calls if c[0] == "POST"]
    assert ("POST", "/git/blobs") in posts
    assert len([c for c in posts if c == ("POST", "/git/blobs")]) == 1, "未变的文件不该重传 blob"


def test_t108k_no_diff_means_no_commit(dep, tmp_path):
    """远端已是最新时不做空提交（Actions 每小时跑 macro.yml，空提交会把历史刷满）。"""
    body = b"<html>same</html>"
    (tmp_path / "index.html").write_bytes(body)
    api = FakeApi(entries=[{"path": "index.html", "type": "blob", "sha": dep_sha(body)}])
    res = dep.Deployer(api, str(tmp_path), message="noop", dry_run=False,
                       out=lambda *_: None).run()
    assert api.writes() == [], "无差异却写了远端"
    assert res["commit"] is None


# ==== T108l 数据回退时跳过该文件，其它文件照推 ====
def test_t108l_stale_data_skipped_others_pushed(dep, tmp_path):
    (tmp_path / "xau").mkdir()
    stale = _doc(1794000000000)
    fresh_remote = _doc(1794190807163)
    (tmp_path / "xau" / "klines.json").write_bytes(stale)
    new_index = b"<html>v3</html>"
    (tmp_path / "index.html").write_bytes(new_index)

    api = FakeApi(entries=[
        {"path": "index.html", "type": "blob", "sha": "0" * 40},
        {"path": "xau/klines.json", "type": "blob", "sha": dep_sha(fresh_remote)},
    ], blobs={dep_sha(fresh_remote): fresh_remote})
    res = dep.Deployer(api, str(tmp_path), message="deploy", dry_run=False,
                       out=lambda *_: None).run()

    pushed = {c["path"] for c in res["pushed"]}
    assert pushed == {"index.html"}
    assert "xau/klines.json" in res["skipped"]


# ==== T108m 嵌套时间戳：jin10/state.json 的抓取配额状态 ====
def test_t108m_nested_stamp_blocks_state_regress(dep):
    """state.json 记的是「今天抓过几页」，推到旧的会让 Actions 重复抓当天日历
    —— 金十配额是本项目最贵的资源，所以它也得进闸。时间戳藏在
    cache['calendar.json'].at 里，取不到时退回 day。"""
    def state(day, at):
        return json.dumps({"cache": {"calendar.json": {"at": at, "pages": 1}},
                           "counts": {"list_calendar": 3}, "day": day}).encode()

    old = state("2026-11-08", 1794151953.0)
    new = state("2026-11-09", 1794191991.0)
    verdict, detail = dep.freshness("jin10/state.json", old, new)
    assert verdict == "stale", detail

    # at 缺失时用 day 兜底，方向仍然正确
    bare = json.dumps({"cache": {}, "day": "2026-11-08"}).encode()
    verdict, _ = dep.freshness("jin10/state.json", bare, new)
    assert verdict == "stale"
