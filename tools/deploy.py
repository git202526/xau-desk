#!/usr/bin/env python3
"""一键部署：把工作区推到 GitHub Pages，绕开 git push 的 443 超时。

为什么不用 git push
------------------
这台机器到 github.com:443 的 `git push` 会卡在对象协商上（实测 45~90 秒无响应，
偶尔直接 reset），而 Git Data API 走的是同一个域名的 443 + REST，却稳定几百毫秒。
v7.6 P6 上线时临时写了一次性脚本走 API，sha 硬编码，复用还得改代码 —— 所以收成本工具。

两道闸（都是踩坑踩出来的）
--------------------------
1. **按 blob 内容算差异**：本地内容算 git blob sha（与 GitHub tree 里同一算法），
   和远端 tree 逐条比，只推真正变了的文件。旧脚本按「本地整份快照覆盖远端」，
   把 Actions 刚更新的 xau/klines.json 覆盖回 7.7 小时前的旧快照。
2. **数据文件按时间戳拦截回退**：xau/klines.json、calendar.json、macro.json 等
   由 Actions 在云端直写，本地副本可能更旧。本地时间戳小于远端 → 跳过该文件并告警，
   其它文件照推。宁可少推一份数据，也不让线上数据倒退。

用法
----
    python3 tools/deploy.py --dry-run              # 只列计划，不写远端
    python3 tools/deploy.py                        # 推工作区全部变更
    python3 tools/deploy.py --only index.html      # 只推指定文件
    python3 tools/deploy.py --message "fix: xxx"   # 覆盖提交信息（默认取 HEAD subject）
    python3 tools/deploy.py --verify               # 只做线上校验
    python3 tools/deploy.py --verify --wait 180    # 部署后等 Pages 构建

token 来源（按序）：$GH_TOKEN → $GITHUB_TOKEN → ~/.git-credentials → gh auth token
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid

REPO = "git202526/xau-desk"
BRANCH = "main"
API = "https://api.github.com/repos/" + REPO
LIVE = "https://git202526.github.io/xau-desk/"

# 永远不推的路径。screenshots/ 是交付截图（体积大、每次重跑字节都不同），
# versions/ 是历史归档，缓存目录更不该进仓库。
IGNORE_PREFIX = ("screenshots/", "versions/", "mockups/_latest/", ".pytest_cache/", ".git/")
IGNORE_SEGMENT = ("__pycache__",)
IGNORE_SUFFIX = (".pyc", ".pyo")

# 由 Actions 在云端写的文件 → 各自的时间戳字段。本地副本可能比远端旧，
# 按「本地 < 远端」拦截，避免把线上数据覆盖回退。
# 值是候选字段（按序取第一个能解析的）；元组表示嵌套路径。
DATA_STAMPS = {
    "xau/klines.json": ("updated",),                       # 毫秒 epoch
    "calendar.json": ("updatedAt",),                       # ISO 字符串
    "macro.json": ("fetched_at",),                         # 毫秒 epoch
    "jin10/calendar.json": ("updatedAt",),                 # ISO 字符串
    "jin10/state.json": (("cache", "calendar.json", "at"), "day"),  # 抓取配额状态，推旧会重复抓
}


# ============================ 纯函数（可单测） ============================
def git_blob_sha(raw):
    """git 的 blob sha：sha1("blob <len>\\0" + 内容)。与 GitHub tree 里的 sha 同算法。"""
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(raw))
    h.update(raw)
    return h.hexdigest()


def should_ignore(path):
    p = path.replace("\\", "/").lstrip("./")
    if any(p.startswith(x) for x in IGNORE_PREFIX):
        return True
    if any(seg in p.split("/") for seg in IGNORE_SEGMENT):
        return True
    return p.endswith(IGNORE_SUFFIX)


def parse_stamp(value):
    """把时间戳读成秒级 float。毫秒 / 秒 / ISO 三种写法都认，读不出返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v   # >1e11 只能是毫秒
    if isinstance(value, str):
        s = value.strip()
        if re.fullmatch(r"-?\d+(\.\d+)?", s):
            return parse_stamp(float(s))
        try:
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def doc_stamp(path, doc):
    """按 DATA_STAMPS 取该文件的时间戳；非数据文件返回 None。

    候选字段按序尝试：jin10/state.json 的 `cache.calendar.json.at` 最准，
    取不到再退回 `day`（只到天，但方向仍对）。
    """
    fields = DATA_STAMPS.get(path)
    if not fields or not isinstance(doc, dict):
        return None
    for field in fields:
        cur = doc
        for key in (field if isinstance(field, tuple) else (field,)):
            cur = cur.get(key) if isinstance(cur, dict) else None
        ts = parse_stamp(cur)
        if ts is not None:
            return ts
    return None


def _fmt(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def freshness(path, local_raw, remote_raw):
    """数据文件回退闸。返回 (verdict, detail)，verdict ∈ {ok, stale, unknown}。

    unknown 不拦：读不出时间戳就交给人判断，比瞎拦更安全（代码文件也会落到这里）。
    """
    if path not in DATA_STAMPS:
        return "unknown", "非数据文件"
    if remote_raw is None:
        return "ok", "远端无此文件（新增）"
    try:
        lt = doc_stamp(path, json.loads(local_raw))
        rt = doc_stamp(path, json.loads(remote_raw))
    except Exception as e:  # noqa: BLE001
        return "unknown", "解析失败：%s" % e
    if lt is None or rt is None:
        return "unknown", "无时间戳字段 %s" % (DATA_STAMPS[path],)
    if lt + 1 < rt:   # 1 秒容差：同一秒内的推送不算回退
        return "stale", "本地 %s 旧于远端 %s" % (_fmt(lt), _fmt(rt))
    return "ok", "本地 %s / 远端 %s" % (_fmt(lt), _fmt(rt))


def plan_push(local, remote, prune=False):
    """比对两棵 {path: blob_sha}，返回 (changes, 相同数)。

    changes 顺序固定：先 add、再 modify、最后 delete，方便日志阅读。
    prune=False 时远端独有文件保留 —— Pages 产物、Actions 新提交的内容都在远端。
    """
    adds = [{"path": p, "action": "add", "sha": local[p]}
            for p in sorted(local) if p not in remote]
    mods = [{"path": p, "action": "modify", "sha": local[p]}
            for p in sorted(local) if p in remote and remote[p] != local[p]]
    dels = [{"path": p, "action": "delete", "sha": remote[p]}
            for p in sorted(remote) if p not in local] if prune else []
    same = sum(1 for p in local if remote.get(p) == local[p])
    return adds + mods + dels, same


def local_entries(workdir):
    """扫工作区，返回 {path: blob_sha}。优先问 git（拿到已跟踪 + 未跟踪未忽略），
    非仓库目录时退化为遍历磁盘。"""
    paths = []
    try:
        out = subprocess.run(
            ["git", "-C", workdir, "ls-files", "-z", "-c", "-o", "--exclude-standard"],
            capture_output=True, timeout=120)
        if out.returncode == 0:
            paths = [p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        paths = []

    if not paths:   # 非 git 目录：遍历（测试用）
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if d not in (".git",)]
            for fn in files:
                paths.append(os.path.relpath(os.path.join(root, fn), workdir))

    entries = {}
    for p in paths:
        p = p.replace("\\", "/")
        if should_ignore(p):
            continue
        full = os.path.join(workdir, p)
        if not os.path.isfile(full):
            continue   # ls-files 里有、磁盘上已删 → 由 prune 决定是否清理
        try:
            with open(full, "rb") as fh:
                entries[p] = git_blob_sha(fh.read())
        except OSError:
            continue
    return entries


def read_bytes(workdir, path):
    with open(os.path.join(workdir, path), "rb") as fh:
        return fh.read()


def get_token():
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    cred = os.path.expanduser("~/.git-credentials")
    if os.path.exists(cred):
        m = re.search(r"https://[^:]*:([^@]*)@github\.com", open(cred, encoding="utf-8").read())
        if m and m.group(1).strip():
            return m.group(1).strip()
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, timeout=20)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.decode().strip()
    except (OSError, subprocess.SubprocessError):
        pass
    raise SystemExit("拿不到 GitHub token：设置 GH_TOKEN，或确认 ~/.git-credentials 里有 github.com 账号")


# ============================ HTTP 层 ============================
class Api:
    """GitHub REST 薄封装：4 次退避重试，5xx/网络抖动都不直接放弃。"""

    def __init__(self, token, base=API, timeout=90):
        self.h = {"Authorization": "Bearer " + token,
                  "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": "2022-11-28"}
        self.base = base
        self.timeout = timeout

    def _req(self, method, path, **kw):
        import requests
        last = ""
        for i in range(4):
            try:
                r = requests.request(method, self.base + path, headers=self.h,
                                     timeout=self.timeout, **kw)
                if r.status_code < 500:
                    return r
                last = "HTTP %s %s" % (r.status_code, r.text[:200])
            except Exception as e:  # noqa: BLE001
                last = "%s: %s" % (type(e).__name__, e)
            if i < 3:
                time.sleep(3 * (i + 1))
        raise SystemExit("%s %s 四次均失败：%s" % (method, path, last))

    def get(self, path, params=None):
        return self._req("GET", path, params=params).json()

    def post(self, path, payload):
        r = self._req("POST", path, json=payload)
        if r.status_code not in (200, 201):
            raise SystemExit("POST %s 失败 HTTP %s：%s" % (path, r.status_code, r.text[:300]))
        return r.json()

    def patch(self, path, payload):
        r = self._req("PATCH", path, json=payload)
        if r.status_code not in (200, 201):
            raise SystemExit("PATCH %s 失败 HTTP %s：%s" % (path, r.status_code, r.text[:300]))
        return r.json()


# ============================ 主流程 ============================
class Deployer:
    def __init__(self, api, workdir, message=None, dry_run=False, only=None,
                 prune=False, out=print):
        self.api = api
        self.workdir = workdir
        self.message = message
        self.dry_run = dry_run
        self.only = set(only or [])
        self.prune = prune
        self.out = out

    # ---- 远端快照 ----
    def remote_snapshot(self):
        head = self.api.get("/git/ref/heads/" + BRANCH)["object"]["sha"]
        base_tree = self.api.get("/git/commits/" + head)["tree"]["sha"]
        data = self.api.get("/git/trees/" + base_tree, params={"recursive": "1"})
        if data.get("truncated"):
            raise SystemExit("远端 tree 被截断，本工具不支持超大仓库的递归比对")
        entries = {e["path"]: e["sha"] for e in data["tree"] if e["type"] == "blob"}
        return head, base_tree, entries

    def _subject(self):
        if self.message:
            return self.message
        try:
            out = subprocess.run(["git", "-C", self.workdir, "log", "-1", "--format=%s"],
                                 capture_output=True, timeout=30)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.decode("utf-8", "replace").strip()
        except (OSError, subprocess.SubprocessError):
            pass
        return "chore: deploy %s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    def run(self):
        head, base_tree, remote = self.remote_snapshot()
        local = local_entries(self.workdir)
        if self.only:
            local = {p: s for p, s in local.items() if p in self.only}

        changes, same = plan_push(local, remote, prune=self.prune)
        self.out("远端 HEAD %s ｜ 工作区 %d 个文件 ｜ 相同 %d ｜ 待处理 %d"
                 % (head[:8], len(local), same, len(changes)))

        if not changes:
            self.out("已是最新，不产生空提交（Actions 每小时跑，空提交会刷满历史）")
            return {"pushed": [], "skipped": [], "commit": None, "head": head}

        # ---- 数据回退闸 ----
        ready, skipped = [], []
        for ch in changes:
            if ch["action"] == "delete":
                ready.append(ch)
                continue
            raw = read_bytes(self.workdir, ch["path"])
            remote_raw = None
            if ch["path"] in DATA_STAMPS and ch["path"] in remote:
                blob = self.api.get("/git/blobs/" + remote[ch["path"]])
                remote_raw = base64.b64decode(blob["content"])
            verdict, detail = freshness(ch["path"], raw, remote_raw)
            if verdict == "stale":
                skipped.append(ch["path"])
                self.out("  拦下 %s：%s（线上数据不倒退）" % (ch["path"], detail))
                continue
            ch["_raw"] = raw
            ready.append(ch)
            self.out("  %-6s %s  (%s)" % (ch["action"], ch["path"], detail))

        if not ready:
            self.out("待推送的文件全部被数据闸拦下，本次不提交")
            return {"pushed": [], "skipped": skipped, "commit": None, "head": head}
        if self.dry_run:
            self.out("dry-run：以上 %d 个文件会被推送，未写远端" % len(ready))
            return {"pushed": ready, "skipped": skipped, "commit": None, "head": head}

        # ---- blobs → tree → commit → ref ----
        entries = []
        for ch in ready:
            if ch["action"] == "delete":
                continue
            rb = self.api.post("/git/blobs", {
                "content": base64.b64encode(ch["_raw"]).decode(), "encoding": "base64"})
            entries.append({"path": ch["path"], "mode": "100644", "type": "blob",
                            "sha": rb["sha"]})

        tree_payload = {"base_tree": base_tree, "tree": entries}
        if any(ch["action"] == "delete" for ch in ready):
            # 删除必须显式给 sha=null 的条目，base_tree 才会摘掉它
            for ch in ready:
                if ch["action"] == "delete":
                    tree_payload["tree"].append(
                        {"path": ch["path"], "mode": "100644", "type": "blob", "sha": None})
        new_tree = self.api.post("/git/trees", tree_payload)["sha"]

        msg = self._subject()
        commit = self.api.post("/git/commits",
                               {"message": msg, "tree": new_tree, "parents": [head]})["sha"]
        self.api.patch("/git/refs/heads/" + BRANCH, {"sha": commit, "force": False})
        self.out("已推送 %s ｜ commit %s ｜ “%s”" % (commit[:8], commit[:8], msg.split("\n")[0]))
        return {"pushed": ready, "skipped": skipped, "commit": commit, "head": head}


def create_tag(api, name, commit, message=None, out=print):
    """在指定 commit 上打注解标签（refs/tags/<name>）。

    走 API 而不是 `git push --tags`：本机的 git 协议链路在这个环境里会卡住，
    而部署本身就是 API 直写，标签跟着 API 走才是同一条链路。
    """
    r = api._req("GET", "/git/ref/tags/" + name)
    if r.status_code == 200:
        out("标签 %s 已存在，跳过" % name)
        return False
    msg = message or ("XAU Desk " + name)
    obj = api.post("/git/tags", {"tag": name, "message": msg,
                                "object": commit, "type": "commit"})["sha"]
    api.post("/git/refs", {"ref": "refs/tags/" + name, "sha": obj})
    out("已打标签 %s → %s" % (name, commit[:8]))
    return True


def verify(wait=0, interval=15, out=print):
    """线上校验：字节 sha256 与本地 index.html 一致才算真上线。

    Pages 构建有 1~2 分钟延迟，所以按 wait 秒轮询；轮询期间看到的旧版不算失败。
    """
    index = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html")
    local = open(index, "rb").read()
    want = hashlib.sha256(local).hexdigest()
    ver = re.search(r"XAU Desk (v[\d.]+)", local[:4000].decode("utf-8", "replace"))
    ver = ver.group(1) if ver else "?"
    import requests
    deadline = time.time() + max(wait, 0)
    last = ""
    while True:
        try:
            r = requests.get(LIVE, params={"v": uuid.uuid4().hex},
                             headers={"Cache-Control": "no-cache"}, timeout=60)
            if r.status_code == 200:
                got = hashlib.sha256(r.content).hexdigest()
                if got == want:
                    out("线上已是本地这份：%s ｜ %d 字节 ｜ sha256 %s" % (ver, len(local), want[:16]))
                    return True
                last = "线上 %d 字节 sha256 %s ≠ 本地 %s" % (len(r.content), got[:16], want[:16])
            else:
                last = "HTTP %s" % r.status_code
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
        if time.time() >= deadline:
            out("校验未通过：%s" % last)
            out("→ 刚推的话等 Pages 构建（约 1~2 分钟），或加 --wait 180")
            return False
        out("  等待 Pages 构建…（%s）" % last)
        time.sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description="XAU Desk 一键部署（GitHub Data API）")
    ap.add_argument("--dry-run", action="store_true", help="只列计划，不写远端")
    ap.add_argument("--only", nargs="*", default=None, help="只处理指定路径")
    ap.add_argument("--prune", action="store_true", help="删除远端有、本地无的文件")
    ap.add_argument("--message", "-m", default=None, help="提交信息（默认取本地 HEAD subject）")
    ap.add_argument("--verify", action="store_true", help="只做线上校验")
    ap.add_argument("--wait", type=int, default=0, help="部署后等 Pages 构建的秒数")
    ap.add_argument("--tag", default=None, help="推送成功后打注解标签，如 v7.9")
    ap.add_argument("--workdir", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args(argv)

    if a.verify:
        return 0 if verify(a.wait) else 1

    dep = Deployer(Api(get_token()), a.workdir, message=a.message,
                   dry_run=a.dry_run, only=a.only, prune=a.prune)
    res = dep.run()
    if a.dry_run or not res["commit"]:
        return 0
    if a.tag:
        create_tag(dep.api, a.tag, res["commit"])
    if a.wait:
        return 0 if verify(a.wait) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
