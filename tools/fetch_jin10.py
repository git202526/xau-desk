#!/usr/bin/env python3
"""金十 MCP 中转抓取（J3 中转方案）。

为什么是"中转"：mcp.jin10.com 的 CORS 预检不返回任何 Access-Control-* 头，
且带 Origin 的 POST 被服务端以 403 显式拒绝（Origin 必须等于 Host）。
浏览器永远满足不了这个条件，所以只能由服务端拉取、落成同源静态 JSON。
详见 docs/J2-金十MCP探测记录.md。

职责边界：
  * 只做「拉取 → 归一化 → 落盘」，不做任何业务判断
  * 每工具独立软上限，到顶只停该工具，不影响其它工具
  * 凭证只从环境变量读，绝不打印、绝不落盘

用法：
    JIN10_TOKEN=*** python3 tools/fetch_jin10.py            # 按 TTL 增量抓
    JIN10_TOKEN=*** python3 tools/fetch_jin10.py --force    # 忽略 TTL 全抓
    python3 tools/fetch_jin10.py --selftest                 # 本地 mock 自测，不联网
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

OUT_DIR = os.environ.get("JIN10_OUT", "jin10")
STATE_PATH = os.path.join(OUT_DIR, "state.json")

MCP_URL = os.environ.get("JIN10_MCP_URL", "https://mcp.jin10.com/mcp")
TIMEOUT = int(os.environ.get("JIN10_TIMEOUT", "20"))

# 服务端来源校验要求「Origin 与 Host 一致」。中转是服务端调用，没有浏览器 Origin，
# 所以显式伪造一对自洽的头来满足它；若服务端改用签名校验，这层要重做。
MCP_ORIGIN = os.environ.get("JIN10_ORIGIN", "https://mcp.jin10.com")

# ============================================================ 工具表
# ttl   : 缓存有效期（秒），命中则完全不发请求
# cap   : 每工具每自然日软上限（按 GMT+8 切日）
# args  : 调用参数
# TODO(待核实) 各工具的入参 schema 尚未核实（J2 配额只够 3 次请求，未调 tools/list）。
#              下面按命名推断，联调时以 tools/list 返回的 inputSchema 为准。
TOOLS = {
    # 快讯 / 资讯（list_flash / search_flash / list_news / search_news / get_news）
    # J4 起停抓：前端已移除这两个卡片，每天省下 900 次额度，留给日历与 K 线。
    "list_calendar": {"ttl": 1200, "cap": 110, "limit": 100},
    "get_quote":     {"ttl": 60, "cap": 200, "limit": 1},
    # cap 是本脚本自设的软上限，不是金十给的配额 —— 服务端真实额度至今未探明（J2 只够 3 次请求）。
    # 800 是按「周期级 TTL 下的理论日耗 696」+ 重试余量定的；ttl 只在没传 override 时兜底。
    # 若哪天的 state.json 出现 get_kline 的 quota / stopped 标记，说明真实配额低于 800，
    # 应该回调 cap，而不是让它每天撞一次墙停到次日。
    "get_kline":     {"ttl": 240, "cap": 800, "limit": 200},
}

# K 线按周期分别设 TTL：四个周期共用一个额度池，不能让周线陪着小时线一起每 5 分钟抓一遍。
# 日耗 = 24（周）+ 96（日）+ 288（4H）+ 288（1H）= 696 次，全天覆盖。
KLINE_TTL = {"1w": 3600, "1d": 900, "4h": 300, "1h": 300}

# 金十报价代码（J1 备源也用这个 id）
QUOTE_CODES = ["hf_XAU"]
KLINE_CODES = ["hf_XAU"]
KLINE_PERIODS = ["1w", "1d", "4h", "1h"]

# 每日软上限的"到顶"判定：剩余 <= 该值时不再重试，直接降级
RETRY_FLOOR = 2
MAX_RETRY = 2


class QuotaExhausted(Exception):
    """工具当日额度用完 —— 立即停到次日，不重试。"""


class SourceDown(Exception):
    """其余不可恢复错误（网络、协议、鉴权）。"""


# ============================================================ 时间工具
def now_utc():
    return datetime.now(timezone.utc)


def day_key_gmt8(dt=None):
    """按 GMT+8 切自然日 —— 用户在东八区，额度按用户的"今天"重置。"""
    dt = dt or now_utc()
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d")


# ============================================================ 状态
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict):
            raise ValueError("not dict")
    except Exception:
        st = {}
    st.setdefault("day", "")
    st.setdefault("counts", {})
    st.setdefault("stopped", {})
    st.setdefault("cache", {})
    return st


def save_state(st):
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def roll_day(st):
    """跨自然日：计数与"停用"标记一起清零。"""
    today = day_key_gmt8()
    if st.get("day") == today:
        return False
    st["day"] = today
    st["counts"] = {}
    st["stopped"] = {}
    return True


# ============================================================ MCP 传输
class Mcp:
    """最小 MCP Streamable-HTTP 客户端：initialize → tools/call。

    只实现这次需要的两条路径，不引第三方 SDK —— 依赖越少，Actions 里越不容易坏。
    """

    def __init__(self, url, token, timeout=TIMEOUT, opener=None):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.session_id = None
        self._opener = opener          # 自测时注入假 transport
        self._id = 0

    def _headers(self, with_auth=True):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Origin": MCP_ORIGIN,
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "xau-desk-relay/1.0",
        }
        if with_auth and self.token:
            h["Authorization"] = "Bearer " + self.token
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _post(self, payload, with_auth=True):
        raw = json.dumps(payload).encode("utf-8")
        if self._opener:
            status, headers, body = self._opener(self.url, self._headers(with_auth), raw)
        else:
            req = urllib.request.Request(self.url, data=raw, method="POST",
                                         headers=self._headers(with_auth))
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    status, headers, body = r.status, dict(r.headers), r.read()
            except urllib.error.HTTPError as e:
                status, headers, body = e.code, dict(e.headers or {}), e.read()
        # 会话 id 可能出现在响应头（大小写不敏感）
        for k, v in (headers or {}).items():
            if k.lower() == "mcp-session-id":
                self.session_id = v
        return status, headers, body

    @staticmethod
    def _parse(body, content_type=""):
        """同时支持 application/json 与 text/event-stream 两种响应。"""
        text = body.decode("utf-8", "replace").strip()
        if not text:
            return None
        if "event-stream" in (content_type or "") or text.startswith("data:"):
            for line in text.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    return json.loads(chunk)
                except Exception:
                    continue
            return None
        try:
            return json.loads(text)
        except Exception:
            return None

    def initialize(self):
        self._id += 1
        payload = {
            "jsonrpc": "2.0", "id": self._id, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "xau-desk-relay", "version": "1.0"},
            },
        }
        status, headers, body = self._post(payload)
        if status == 403:
            raise SourceDown("origin rejected (403): " + body.decode("utf-8", "replace")[:120])
        if status == 401:
            raise SourceDown("unauthorized (401) —— Token 无效或已过期")
        if status >= 400:
            raise SourceDown("initialize http %d" % status)
        d = self._parse(body, (headers or {}).get("Content-Type", "")) or {}
        if "error" in d:
            raise SourceDown("initialize error: " + json.dumps(d["error"], ensure_ascii=False)[:160])
        # 通知服务端"已初始化"，协议要求，无 id、不关心响应
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        except Exception:
            pass
        return d.get("result", {})

    def call(self, name, arguments):
        self._id += 1
        payload = {
            "jsonrpc": "2.0", "id": self._id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        status, headers, body = self._post(payload)
        text = body.decode("utf-8", "replace")
        if status == 429:
            raise QuotaExhausted("http 429")
        if status == 403 and "cross-origin" in text.lower():
            raise SourceDown("origin rejected (403)")
        if status >= 400:
            raise SourceDown("tools/call http %d" % status)
        d = self._parse(body, (headers or {}).get("Content-Type", "")) or {}
        if "error" in d:
            err = d["error"]
            msg = json.dumps(err, ensure_ascii=False)
            if is_quota_text(msg):
                raise QuotaExhausted(msg[:160])
            raise SourceDown("tools/call error: " + msg[:160])
        return d.get("result", {})


QUOTA_PAT = re.compile(
    r"(额度|配额|超出.{0,4}限|次数.{0,4}用|quota|rate.?limit|too\s*many|exceed)",
    re.I,
)


def is_quota_text(s):
    return bool(QUOTA_PAT.search(s or ""))


# ============================================================ 结果归一化
def unwrap(result):
    """优先 structuredContent，其次把 content[0].text 当 JSON 解。

    返回 (payload, source)。payload 一定是 dict 或 list。
    """
    if not isinstance(result, dict):
        return {}, "empty"
    sc = result.get("structuredContent")
    if isinstance(sc, (dict, list)) and sc:
        return sc, "structuredContent"
    for item in (result.get("content") or []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            txt = item.get("text") or ""
            txt = txt.strip()
            if not txt:
                continue
            try:
                return json.loads(txt), "content.text"
            except Exception:
                return {"text": txt}, "content.text.raw"
    return {}, "empty"


def list_items(payload):
    """列表类工具统一从 data.items 取；兼容顶层 items / 裸数组。"""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for holder in (payload.get("data"), payload):
        if isinstance(holder, dict):
            items = holder.get("items")
            if isinstance(items, list):
                return items
    return []


def page_meta(payload):
    """分页只认 cursor：读 next_cursor / has_more，不用 offset。"""
    holder = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else (payload or {})
    if not isinstance(holder, dict):
        return None, False
    cur = holder.get("next_cursor") or holder.get("nextCursor")
    more = bool(holder.get("has_more") or holder.get("hasMore"))
    return cur, more


def call_paged(mcp, name, args, max_pages=3, cap_left=None, count=None):
    """按 cursor 翻页，最多 max_pages 页。

    返回 (payloads, pages)。任一页报额度用完，立刻停手并把已有结果交回。
    计数不在这里做：只要请求真的发出去，call_one 就会记一次（含失败与超时）。
    """
    out = []
    cursor = None
    pages = 0
    while pages < max_pages:
        a = dict(args)
        if cursor:
            a["cursor"] = cursor
        payload = call_one(mcp, name, a, cap_left, count)
        out.append(payload)
        pages += 1
        nxt, more = page_meta(payload)
        if not more or not nxt:
            break
        cursor = nxt
    return out, pages


def call_one(mcp, name, args, cap_left=None, count=None):
    """发一次 tools/call，失败按 MAX_RETRY 重试。

    count 在每次真正发出请求前调用 —— 超时、5xx、额度用完同样消耗服务端额度，
    所以失败也要计。剩余额度 <= RETRY_FLOOR 时只试一次，把最后的额度留给
    下一轮，而不是在这一轮烧光。
    """
    # 剩余额度 <= RETRY_FLOOR 时不重试：一次不成就降级，省着用
    tries = 1 if (cap_left is not None and cap_left <= RETRY_FLOOR) else (1 + MAX_RETRY)
    last = None
    for i in range(tries):
        if count:
            count(name, 1)
        try:
            return unwrap(mcp.call(name, args))[0]
        except QuotaExhausted:
            raise
        except SourceDown as e:
            last = e
            if i + 1 < tries:
                time.sleep(min(2 ** i, 4))
    raise last


# ============================================================ 归一化 → 落盘格式
def norm_flash(items):
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append({
            "id": str(it.get("id") or it.get("flash_id") or ""),
            "t": int(it.get("time") or it.get("ts") or it.get("timestamp") or 0),
            "text": (it.get("content") or it.get("text") or it.get("title") or "").strip(),
            "important": bool(it.get("important") or it.get("is_important")),
            "url": it.get("url") or "",
        })
    out = [x for x in out if x["text"]]
    out.sort(key=lambda x: x["t"], reverse=True)
    return out


def norm_news(items):
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append({
            "id": str(it.get("id") or it.get("news_id") or ""),
            "t": int(it.get("time") or it.get("ts") or it.get("timestamp") or 0),
            "title": (it.get("title") or "").strip(),
            "summary": (it.get("summary") or it.get("digest") or "").strip(),
            "url": it.get("url") or "",
        })
    return [x for x in out if x["title"]]


def norm_calendar(items):
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        t = it.get("time") or it.get("ts") or it.get("timestamp") or 0
        try:
            t = int(t)
        except Exception:
            t = 0
        # 金十给的是秒；超过 1e12 视作毫秒
        if t > 10 ** 12:
            t //= 1000
        out.append({
            "t": t * 1000,
            "ccy": (it.get("country") or it.get("currency") or it.get("ccy") or "").upper(),
            "name": (it.get("name") or it.get("title") or "").strip(),
            "impact": max(1, min(5, int(it.get("star") or it.get("importance") or it.get("impact") or 3))),
            "fc": str(it.get("forecast") or it.get("fc") or ""),
            "pv": str(it.get("previous") or it.get("pv") or ""),
        })
    return [x for x in out if x["name"] and x["t"] > 0]


def norm_quote(payload):
    """报价：兼容单条与列表。字段对齐 J1 的备源映射规则。"""
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("items") or payload.get("data") or []
        if isinstance(rows, dict):
            rows = [rows]
    else:
        rows = []
    out = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        price = it.get("lastPrice") or it.get("price") or it.get("last")
        try:
            price = float(str(price).replace(",", ""))
        except Exception:
            continue
        chg = it.get("changeMargin") or it.get("change") or ""
        out.append({
            "code": it.get("goldId") or it.get("code") or "",
            "name": it.get("varietyName") or it.get("name") or "",
            "price": price,
            "change": pct_num(chg),
            "time": it.get("updateTime") or it.get("time") or "",
        })
    return out


def norm_kline(payload):
    """K 线：输出 [{t,o,h,l,c,v}]，t 为毫秒开盘时间。"""
    if isinstance(payload, dict):
        rows = payload.get("items") or payload.get("data") or payload.get("bars") or []
    else:
        rows = payload or []
    out = []
    for it in rows:
        if isinstance(it, dict):
            try:
                t = int(it.get("time") or it.get("t") or 0)
                if t < 10 ** 11:
                    t *= 1000
                out.append({"t": t,
                            "o": float(it.get("open") or it.get("o") or 0),
                            "h": float(it.get("high") or it.get("h") or 0),
                            "l": float(it.get("low") or it.get("l") or 0),
                            "c": float(it.get("close") or it.get("c") or 0),
                            "v": float(it.get("volume") or it.get("v") or 0)})
            except Exception:
                continue
        elif isinstance(it, (list, tuple)) and len(it) >= 5:
            try:
                t = int(it[0])
                if t < 10 ** 11:
                    t *= 1000
                out.append({"t": t, "o": float(it[1]), "h": float(it[2]),
                            "l": float(it[3]), "c": float(it[4]), "v": float(it[5] if len(it) > 5 else 0)})
            except Exception:
                continue
    out.sort(key=lambda x: x["t"])
    return out


def pct_num(s):
    """'1.23%' / '+1.23%' / 1.23 → 1.23（数字，不带百分号）"""
    if isinstance(s, (int, float)):
        return float(s)
    txt = str(s or "").replace("%", "").replace("＋", "+").replace("－", "-").strip()
    try:
        return float(txt)
    except Exception:
        return None


# ============================================================ 主流程
def write_json(name, obj):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return path


def ttl_of(name, dt=None):
    return TOOLS[name]["ttl"]


def run(force=False, mcp=None, log=print, now=None):
    """主入口。返回每个工具的结果摘要，便于自测断言。"""
    now = now or now_utc()
    st = load_state()
    rolled = roll_day(st)
    # v6.55：缓存键从工具名改为输出文件名。清掉旧键，否则 state.json 里会永久留着
    # get_kline / list_calendar 这些已经不再读取的记录。
    for k in [x for x in st.get("cache", {}) if "." not in x]:
        st["cache"].pop(k, None)
    mcp = mcp or Mcp(MCP_URL, os.environ.get("JIN10_TOKEN", "").strip())
    summary = {}

    if not mcp.session_id and not getattr(mcp, "_inited", False):
        try:
            mcp.initialize()
            mcp._inited = True
        except Exception as e:
            log("[jin10] initialize 失败：%s" % e)
            save_state(st)
            return {"_fatal": str(e)}

    def budget(name):
        """返回 (已用, 上限, 剩余)。"""
        cap = TOOLS[name]["cap"]
        used = int(st["counts"].get(name, 0))
        return used, cap, cap - used

    def note(name, n=1):
        # 一次翻页 = 一次 tools/call，按页计数才和真实额度对得上
        st["counts"][name] = int(st["counts"].get(name, 0)) + max(1, int(n or 1))

    def fresh(name, target, ttl=None):
        if force:
            return False
        # 缓存键必须是 target（文件名），不能是工具名 —— 四个 K 线周期共用 get_kline，
        # 用工具名做键会让第一个周期刚写的时间戳把后三个判成"命中缓存"，从此再不更新。
        meta = st["cache"].get(target) or {}
        at = float(meta.get("at") or 0)
        life = ttl if ttl is not None else ttl_of(name, now)
        if not at or (time.time() - at) > life:
            return False
        # 缓存新鲜，但输出文件可能被清理过 —— 文件不在就当未命中
        return os.path.exists(os.path.join(OUT_DIR, target))

    def guard(name, target, fn, ttl=None):
        used, cap, left = budget(name)
        if name in st["stopped"]:
            summary[name] = "stopped"
            return
        if left <= 0:
            st["stopped"][name] = day_key_gmt8(now)
            log("[jin10] %s 已达软上限 %d，今日停用" % (name, cap))
            summary[name] = "capped"
            return
        if fresh(name, target, ttl):
            summary[name] = "cached"
            return
        try:
            obj, pages = fn(left)
            # 计数已在 call_one 内按"实际发出的请求"记过，这里不再补记
            write_json(target, obj)
            st["cache"][target] = {"at": time.time(), "pages": pages}
            summary[name] = "ok x%d" % pages
        except QuotaExhausted as e:
            # 额度用完：立即停到次日，不重试
            st["stopped"][name] = day_key_gmt8(now)
            log("[jin10] %s 额度用完（%s），今日停用" % (name, e))
            summary[name] = "quota"
        except SourceDown as e:
            log("[jin10] %s 失败：%s" % (name, e))
            summary[name] = "fail"

    # ---- 快讯 / 资讯：J4 起下线 ----
    # 页面上原本只有一张「市场快讯」卡（快讯 / 资讯两个 Tab），实用价值低于直接开 App，
    # 且 list_flash + list_news 每天要吃掉 900 次额度。前端已移除，这里一并停抓，
    # 把额度留给日历和 K 线。norm_flash / norm_news 的解析函数保留（纯函数，无副作用），
    # 若日后要恢复，把 guard 加回来即可。

    # ---- 日历 ----
    def do_cal(left):
        start = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        end = (now + timedelta(days=7)).strftime("%Y-%m-%d")
        payloads, pages = call_paged(mcp, "list_calendar",
                                     {"start": start, "end": end,
                                      "limit": TOOLS["list_calendar"]["limit"]}, 2, left, note)
        items = []
        for p in payloads:
            items.extend(norm_calendar(list_items(p)))
        return {"updatedAt": now.isoformat(), "events": items}, pages

    guard("list_calendar", "calendar.json", do_cal)

    # ---- 报价（第三层，仅中转落盘；浏览器侧默认不用） ----
    def do_quote(left):
        p = call_one(mcp, "get_quote", {"codes": QUOTE_CODES}, left, note)
        return {"updatedAt": now.isoformat(), "items": norm_quote(p)}, 1

    guard("get_quote", "quote.json", do_quote)

    # ---- K 线（Binance 的备源） ----
    # 四个周期共用 get_kline 这一个额度池，但各自落一个文件。
    # guard 只按工具名记 summary，所以这里先把每周期的结果收进局部表再合并，
    # 否则后一个周期会把前一个覆盖掉。
    kl_sum = {}
    for period in KLINE_PERIODS:
        target = "kline_%s.json" % period

        def do_kl(left, period=period):
            p = call_one(mcp, "get_kline",
                         {"code": KLINE_CODES[0], "period": period,
                          "limit": TOOLS["get_kline"]["limit"]}, left, note)
            bars = norm_kline(p)
            if len(bars) < 10:
                raise SourceDown("kline too short: %s" % len(bars))
            return {"updatedAt": now.isoformat(), "period": period, "bars": bars}, 1

        guard("get_kline", target, do_kl, KLINE_TTL.get(period))
        kl_sum[period] = summary.pop("get_kline", "")
    summary["get_kline"] = kl_sum

    save_state(st)
    return summary


# ============================================================ 自测（本地 mock，不联网）
def _selftest():
    """用假 transport 跑一遍全链路：验证字段解析、翻页、软上限、TTL。

    这是 J3 验收第 1、2 条的可执行版本 —— 不需要金十凭据。
    """
    calls = []

    def fake_transport(url, headers, raw):
        req = json.loads(raw.decode())
        method = req.get("method")
        name = (req.get("params") or {}).get("name")
        args = (req.get("params") or {}).get("arguments") or {}
        calls.append((method, name))
        if method == "initialize":
            return 200, {"Content-Type": "application/json", "Mcp-Session-Id": "sess-1"}, \
                json.dumps({"jsonrpc": "2.0", "id": req["id"],
                            "result": {"protocolVersion": "2025-06-18",
                                       "serverInfo": {"name": "mock", "version": "0"}}}).encode()
        if method == "notifications/initialized":
            return 202, {}, b""
        if method != "tools/call":
            return 200, {}, b"{}"
        body = _mock_result(name, args, calls)
        return 200, {"Content-Type": "application/json"}, json.dumps(
            {"jsonrpc": "2.0", "id": req["id"], "result": body}).encode()

    m = Mcp(MCP_URL, "test-token", opener=fake_transport)
    global OUT_DIR, STATE_PATH
    OUT_DIR = "/tmp/jin10_selftest"
    STATE_PATH = os.path.join(OUT_DIR, "state.json")
    if os.path.isdir(OUT_DIR):
        for f in os.listdir(OUT_DIR):
            os.remove(os.path.join(OUT_DIR, f))
    os.makedirs(OUT_DIR, exist_ok=True)

    out = {}
    r1 = run(force=True, mcp=m, log=lambda *a: None)
    out["pass1"] = r1
    r2 = run(force=False, mcp=m, log=lambda *a: None)     # TTL 命中
    out["pass2"] = r2

    def read(name):
        with open(os.path.join(OUT_DIR, name), encoding="utf-8") as f:
            return json.load(f)

    ok = True

    def check(label, cond, extra=""):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else "  " + str(extra)))
        if not cond:
            ok = False

    print("[自测] 第一次强制抓取")
    check("list_calendar 落盘且有内容", len(read("calendar.json")["events"]) >= 3)
    check("list_calendar 翻页 2 页（cursor）", r1.get("list_calendar", "").endswith("x2"),
          r1.get("list_calendar"))
    check("list_calendar 秒转毫秒", read("calendar.json")["events"][0]["t"] > 10 ** 12)
    check("get_quote 读了 structuredContent", read("quote.json")["items"][0]["price"] == 4072.4)
    check("get_quote changeMargin 去 %", read("quote.json")["items"][0]["change"] == 1.23)
    check("kline_1h 有 bars", len(read("kline_1h.json")["bars"]) >= 10)
    check("快讯/资讯已停抓", "list_flash" not in r1 and "list_news" not in r1, r1)
    # 解析函数保留（前端已不用），单独验一下字段映射，防日后恢复时踩空
    check("norm_news 字段映射", norm_news([{"news_id": "n1", "ts": 1762290000,
                                            "title": "标题一", "summary": "摘要一"}])[0]["title"] == "标题一")

    print("[自测] 第二次（TTL 未过期）")
    check("list_calendar 命中缓存", r2.get("list_calendar") == "cached", r2.get("list_calendar"))
    check("get_quote 命中缓存", r2.get("get_quote") == "cached", r2.get("get_quote"))

    print("[自测] K 线周期级 TTL 与独立缓存")
    st = load_state()
    st["cache"]["kline_1w.json"] = {"at": time.time(), "pages": 1}          # 周线刚抓过
    st["cache"]["kline_1h.json"] = {"at": time.time() - 400, "pages": 1}    # 1H 超过 300s
    save_state(st)
    r2b = run(force=False, mcp=m, log=lambda *a: None)
    kl = r2b.get("get_kline", {})
    check("周线在 1h TTL 内 → cached", kl.get("1w") == "cached", kl)
    check("1H 超 300s → 重抓", str(kl.get("1h", "")).startswith("ok"), kl)
    check("四个周期各有独立缓存键（不会互相顶掉）",
          all("kline_%s.json" % k in load_state()["cache"] for k in KLINE_PERIODS),
          list(load_state()["cache"].keys()))

    print("[自测] 软上限：把 list_calendar 预置为 110")
    st = load_state()
    st["day"] = day_key_gmt8()
    st["counts"]["list_calendar"] = TOOLS["list_calendar"]["cap"]
    save_state(st)
    r3 = run(force=True, mcp=m, log=lambda *a: None)
    check("list_calendar 到顶即停", r3.get("list_calendar") == "capped", r3.get("list_calendar"))
    check("其它工具不受影响（get_quote 仍可抓）",
          r3.get("get_quote") in ("ok x1", "cached"), r3.get("get_quote"))

    print("[自测] 跨日重置")
    st = load_state()
    st["day"] = "2000-01-01"
    save_state(st)
    st2 = load_state()
    roll_day(st2)
    check("跨日清零计数", st2["counts"] == {} and st2["stopped"] == {})

    print("[自测] 额度用完 → 停到次日")
    def quota_transport(url, headers, raw):
        req = json.loads(raw.decode())
        if req.get("method") == "initialize":
            return 200, {"Content-Type": "application/json"}, json.dumps(
                {"jsonrpc": "2.0", "id": req["id"], "result": {}}).encode()
        return 200, {"Content-Type": "application/json"}, json.dumps(
            {"jsonrpc": "2.0", "id": req["id"],
             "error": {"code": -32000, "message": "今日额度已用完"}}).encode()

    st = load_state()
    st["day"] = day_key_gmt8()
    st["counts"] = {}
    st["stopped"] = {}
    st["cache"] = {}
    save_state(st)
    m2 = Mcp(MCP_URL, "t", opener=quota_transport)
    r4 = run(force=True, mcp=m2, log=lambda *a: None)
    check("额度用完标 quota", r4.get("list_calendar") == "quota", r4.get("list_calendar"))
    check("已停用写入 stopped", "list_calendar" in load_state()["stopped"])

    print("[自测] 结构化优先级")
    sc, src = unwrap({"structuredContent": {"data": {"items": [1]}},
                      "content": [{"type": "text", "text": '[{"x":2}]'}]})
    check("structuredContent 优先", src == "structuredContent" and sc == {"data": {"items": [1]}}, src)
    sc2, src2 = unwrap({"content": [{"type": "text", "text": '{"data":{"items":[2]}}'}]})
    check("无 structuredContent 时读 content.text", src2 == "content.text" and sc2["data"]["items"] == [2])

    print("[自测] 分页元数据")
    check("读 next_cursor/has_more", page_meta({"data": {"items": [], "next_cursor": "c2", "has_more": True}}) == ("c2", True))
    check("无 cursor 视为末页", page_meta({"data": {"items": []}}) == (None, False))

    print("[自测] 总请求数（不含 initialize）")
    n = sum(1 for m_, _n in calls if m_ == "tools/call")
    print("  tools/call 次数：%d" % n)
    print("结果：%s" % ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


def _mock_result(name, args, calls):
    """构造 3 个在抓工具的假响应，用来验证解析分支。"""
    if name == "list_calendar":
        # 两页：第一页带 next_cursor，用来验证翻页与合并
        page2 = bool(args.get("cursor"))
        rows = [
            {"time": 1762350000 + (i + (2 if page2 else 0)) * 60, "country": "us" if i % 2 == 0 else "cn",
             "name": ("非农就业" if i == 0 and not page2 else "事件%d" % (i + (2 if page2 else 0))),
             "star": 5 if i == 0 else 3,
             "forecast": "18万", "previous": "15万"}
            for i in range(2)
        ]
        return {"structuredContent": {"data": {
            "items": rows,
            "has_more": not page2,
            "next_cursor": None if page2 else "cur-2"}}}
    if name == "get_quote":
        return {"structuredContent": {"items": [
            {"goldId": "hf_XAU", "varietyName": "伦敦金", "lastPrice": "4,072.40",
             "changeMargin": "+1.23%", "updateTime": "2026-11-04 09:40:00"}]}}
    if name == "get_kline":
        bars = []
        for i in range(20):
            bars.append({"time": 1762200000 + i * 3600, "open": 4050 + i, "high": 4060 + i,
                         "low": 4045 + i, "close": 4055 + i, "volume": 100 + i})
        return {"structuredContent": {"items": bars}}
    return {"structuredContent": {}}


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    if not os.environ.get("JIN10_TOKEN", "").strip():
        print("[jin10] 缺少 JIN10_TOKEN，跳过（不发起请求）", file=sys.stderr)
        sys.exit(2)
    res = run(force="--force" in sys.argv)
    print(json.dumps(res, ensure_ascii=False, indent=1))
