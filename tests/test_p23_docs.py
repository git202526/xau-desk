"""P23 文档一致性：把「文档说对了」变成可执行断言。

这一组不启浏览器、不跑 Playwright，纯文本与解析，整套在 1 秒内跑完。
它守的不是页面行为，而是**文档与代码是否还说同一件事**：

    d1  旧术语没有回流（「计划模式 / 账本模式」已随 P20 删除）
    d2  P20/P21/P18/P19-1/P19-2 五个功能都在功能段里有交代
    d3  期次表里的文档路径都真实存在（防死链）
    d4  没有孤儿计划书（每个 docs/开发计划书-*.md 都进表）
    d5  三处版本号一致（index.html 首行注释 / #verTag / CHANGELOG 首条）
    d6  期次表里写的「落地版本」在 CHANGELOG 里真实存在
    d7  约束文档存在且列全 8 个允许标签
    d8  约束文档的标签集合 == index.html 里 _ALLOW 的键集合（文档与代码同源）
    d9  期次表不再有「待开发」
    d10 README 有测试章节且写了命令
    d11 文档里的相对链接都能解析
    d12 术语表五个词齐全

d8 是最有价值的一条：它让「白名单是什么」从一句注释变成双向绑定。
以后动 _ALLOW 却没改文档，这里会红。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
CONSTRAINT = ROOT / "docs" / "约束-代码与DOM.md"
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

# 允许不进期次表的两类：被取代的旧方案、期次体系之前的改版记录。
# 共同点是「已落地或已作废」，留在仓库只为对照，不在排期里。
EXEMPT_PLANS = {"开发计划书-P5-双引擎重写.md", "开发计划书-v6.2-移动版.md"}


def _section(title):
    """取 README 里某个二级章节的正文（到下一个 ## 为止）。"""
    m = re.search(r"^## " + re.escape(title) + r"\s*$(.*?)(?=^## |\Z)",
                  README, re.S | re.M)
    assert m, "README 缺少章节：" + title
    return m.group(1)


def _plan_table():
    """期次表正文：从表头到第一个空行之后。"""
    m = re.search(r"^\| 期 \| 文档 \| 落地版本 \| 内容 \|\s*$(.*?)(?=^\*\*|^\Z)",
                  README, re.S | re.M)
    assert m, "README 缺少期次表"
    return m.group(1)


def _versions_in(text):
    return re.findall(r"v(\d+)\.(\d+)", text)


# ---------- d1：旧术语不回流 ----------

def test_d1_no_legacy_mode_terms():
    """「计划模式 / 账本模式 / planOnly」在功能段里必须彻底消失。

    期次表里 P15 那行写的是历史事实（v6.28 确实叫这个名字），
    属于「变更日志」，允许保留；所以本断言只看「它能做什么」这一段。
    """
    body = _section("它能做什么")
    for bad in ("计划模式", "账本模式", "planOnly", "setPlanOnly"):
        assert bad not in body, "功能段仍出现过时术语：" + bad

    # 全文范围只禁代码标识符，历史名词不提
    assert "planOnly" not in README
    assert "setPlanOnly" not in README


# ---------- d2：五个功能都有交代 ----------

@pytest.mark.parametrize("keyword,term", [
    ("标记即执行", "P20 单模式合一"),
    ("摊派回滚", "P21 摊派回滚"),
    ("执行复盘", "P18 执行复盘"),
    ("判定回溯", "P19-1 判定回溯"),
    # P31：这个功能已从「跑一组参数给你看」改成「五组档距对比」，
    # README 的功能名跟着改；期次表里仍留着 P19-2 那一行（历史事实）
    ("档距扫描", "P19-2 策略回测 → P31 档距扫描"),
])
def test_d2_features_documented(keyword, term):
    """功能段要提到每个功能，且在期次表里能找到对应期次文档。"""
    body = _section("它能做什么")
    assert keyword in body, "功能段缺少：" + keyword + "（" + term + "）"


def test_d2b_feature_versions_in_table():
    """四个功能期的计划书都要在期次表里。"""
    table = _plan_table()
    for p in ("P18", "P19-2", "P20", "P21"):
        assert "| " + p + " |" in table, "期次表缺少 " + p


# ---------- d3：期次表里的路径都真实存在 ----------

def test_d3_all_doc_paths_exist():
    """期次表与文件结构段里反引号包住的 .md 路径都要能解析到真实文件。"""
    missing = []
    for raw in re.findall(r"`([^`]+\.md)`", README):
        if raw.startswith("http"):
            continue
        if "/" in raw:
            cands = [ROOT / raw]
        else:
            cands = [ROOT / raw, ROOT / "docs" / raw]
        if not any(c.exists() for c in cands):
            missing.append(raw)
    assert missing == [], "README 里有不存在的文档路径：" + str(missing)


# ---------- d4：没有孤儿计划书 ----------

def test_d4_no_orphan_plan():
    """docs 下每份计划书都要出现在期次表里（豁免名单除外）。"""
    plans = sorted(p.name for p in (ROOT / "docs").glob("开发计划书-*.md"))
    assert plans, "docs 下没有任何计划书？"
    table = _plan_table()
    orphans = [p for p in plans if p not in table and p not in EXEMPT_PLANS]
    assert orphans == [], "有计划书没进期次表：" + str(orphans)


def test_d4b_exempt_plan_declared():
    """豁免的旧方案必须在正文里被点名「不要按它实现」，不能悄悄留着。"""
    for p in EXEMPT_PLANS:
        assert p in README, p + " 没被点名"
    assert "不要按它实现" in README


# ---------- d5：三处版本一致 ----------

def _ver_html():
    m = re.search(r"<!-- XAU Desk v(\d+\.\d+) ·", INDEX)
    assert m, "index.html 首行注释缺少版本号"
    return m.group(1)


def _ver_tag():
    m = re.search(r'id="verTag">v(\d+\.\d+)', INDEX)
    assert m, "index.html 缺少 #verTag"
    return m.group(1)


def _ver_log():
    m = re.search(r"^## v(\d+\.\d+) · ", CHANGELOG, re.M)
    assert m, "CHANGELOG 缺少版本条目标题"
    return m.group(1)


def test_d5_version_consistent():
    """首行注释、页面角标、CHANGELOG 首条必须是同一个版本。"""
    a, b, c = _ver_html(), _ver_tag(), _ver_log()
    assert a == b == c, "版本不一致：注释 %s / 角标 %s / CHANGELOG %s" % (a, b, c)


# ---------- d6：表格版本在 CHANGELOG 里存在 ----------

def test_d6_table_versions_exist_in_changelog():
    """期次表写的每个 **vX.Y** 都要在 CHANGELOG 里有对应条目。"""
    table = _plan_table()
    # 只认 **vX.Y**：未落地的期次写成「计划 vX.Y」，不参与本断言
    listed = ["v" + x for x in re.findall(r"\*\*v(\d+\.\d+)\*\*", table)]
    logged = {"v" + ".".join(x) for x in _versions_in(CHANGELOG)}
    missing = [v for v in listed if v not in logged]
    assert missing == [], "期次表写了 CHANGELOG 里没有的版本：" + str(sorted(set(missing)))


# ---------- d7 / d8：白名单文档与代码同源 ----------

def _doc_tags():
    assert CONSTRAINT.exists(), "缺少 docs/约束-代码与DOM.md"
    txt = CONSTRAINT.read_text(encoding="utf-8")
    m = re.search(r"<!--\s*allow-tags:\s*([A-Z ]+?)\s*-->", txt)
    assert m, "约束文档缺少机器可读的 allow-tags 注释"
    return set(m.group(1).split())


def _code_tags():
    m = re.search(r"var _ALLOW=\{([^}]*)\}", INDEX)
    assert m, "index.html 里找不到 _ALLOW"
    return {k.split(":")[0].strip() for k in m.group(1).split(",") if k.strip()}


def test_d7_constraint_doc_lists_eight_tags():
    """约束文档必须列全 8 个允许标签。"""
    tags = _doc_tags()
    assert len(tags) == 8, tags
    assert {"DIV", "SPAN", "BUTTON"} <= tags, tags


def test_d8_doc_tags_match_code():
    """文档标签集合 == 代码 _ALLOW 键集合。动一处必须动两处。"""
    assert _doc_tags() == _code_tags(), (
        "文档与代码不一致：doc=%s code=%s" % (sorted(_doc_tags()), sorted(_code_tags())))


def test_d8b_forbidden_items_documented():
    """四条硬禁令（br / table / id / style）都要写清。"""
    txt = CONSTRAINT.read_text(encoding="utf-8")
    for bad in ("<br>", "<table>", "id=", "style="):
        assert bad in txt, "约束文档没提：" + bad


# ---------- d9：没有残留的「待开发」 ----------

def test_d9_no_pending_status():
    """八期全部落地后，期次表里不该再有「待开发」。"""
    table = _plan_table()
    assert "待开发" not in table, "期次表仍有未落地的期次"


# ---------- d10：测试章节 ----------

def test_d10_test_section_exists():
    """README 要有测试章节，且给出可复制的命令。"""
    body = _section("测试")
    assert "pytest tests/test_smoke.py" in body, body[:200]
    assert "pytest tests/" in body
    assert "冒烟" in body and "全量" in body


# ---------- d11：相对链接可解析 ----------

def test_d11_relative_links_resolve():
    """README 与约束文档里所有 docs/...md 链接都要能打开。"""
    for path, txt in ((README, README), (CONSTRAINT, CONSTRAINT.read_text(encoding="utf-8"))):
        for link in re.findall(r"`(docs/[\w\-.一-龥]+\.md)`", txt):
            assert (ROOT / link).exists(), "死链：%s（来自 %s）" % (link, path)


# ---------- d12：术语表 ----------

def test_d12_glossary_complete():
    """五个口径词必须写进术语表，否则读者会各自理解。"""
    body = _section("它能做什么")
    for term in ("计划", "净持", "待补", "超买", "账本持仓"):
        assert term in body, "术语表缺少：" + term
