#!/usr/bin/env python3
"""金十 MCP 中转抓取（J3 中转方案）。

为什么是"中转"：mcp.jin10.com 的 CORS 预检不返回任何 Access-Control-* 头，
且带 Origin 的 POST 被服务端以 403 显式拒绝（Origin 必须等于 Host）。
浏览器永远满足不了这个条件，所以只能由服务端拉取、落成同源静态 JSON。
详见 docs/J2-金十MCP探测记录.md。

只服务一件事：**财经日历**。

金十另外两个工具（get_quote / get_kline）经 J5 实测都不适合本页：
  * get_quote 的 time 精确到秒，报价本身是实时的，但中转最快 5 分钟一份快照，
    救不了页面的 15 秒报价，且报价已有 Gold API + gold-api.cn 两层。
  * get_kline 只有分钟级接口（count 上限 100、窗口 24 小时），凑不出 4H / 日线
    任何一根 bar，当备源和当校验源都不成立。
详见 docs/J5-金十MCP实测记录.md。两个解析函数保留（纯函数），要恢复把 guard 加回来即可。

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
# ttl : 缓存有效期（秒），命中则完全不发请求
# cap : 每自然日软上限（按 GMT+8 切日）
#
# 只剩 list_calendar 一项。get_quote / get_kline 的抓取已在 v6.58 下线，原因见文件头。
# 入参已按实测 schema 对齐：list_calendar 不收任何参数，传 {start,end,limit} 会被拒。
TOOLS = {
    # 服务端硬额度（官方口径）：每用户每工具每天 1500 次，北京时间自然日重置。
    # 「每工具」= 各自独立计数，所以只跑日历时整条 1500 都是它的。
    #
    # cap 自设 110，远低于 1500：日历本身每 20 分钟才抓一次（72 次/天），
    # 110 只是防"某个 bug 导致每轮都判定过期"把额度打光的护栏。
    # 撞硬额度 = 当天彻底停摆；撞软上限 = 提前收手，第二天照常。
    "list_calendar": {"ttl": 1200, "cap": 110, "limit": 100},
}

# 协议版本：接入文档推荐 2025-11-25。J2 探测时用的是 2025-06-18，服务端也接受，
# 但新版本对齐官方文档，避免将来被拒。
MCP_PROTOCOL = "2025-11-25"

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
            # Sec-Fetch-Site 必须与 Origin 配套：服务端拿它判断"是否跨源"，
            # 缺了就按跨源拒（J2 实测 403）。所以 Origin 为空时这个头也一并去掉。
            "User-Agent": "xau-desk-relay/1.0",
        }
        if MCP_ORIGIN:
            h["Origin"] = MCP_ORIGIN
            h["Sec-Fetch-Site"] = "same-origin"
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
                "protocolVersion": MCP_PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "xau-desk-relay", "version": "1.0"},
            },
        }
        status, headers, body = self._post(payload)
        if status == 403:
            raise SourceDown("origin rejected (403): " + body.decode("utf-8", "replace")[:120])
        if status == 401:
            # 带上响应体：服务端可能说明是"Token 格式不对"还是"已过期"，两者处理方式不同
            raise SourceDown("unauthorized (401): " +
                             body.decode("utf-8", "replace")[:200])
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
        res = d.get("result", {})
        # 文档：isError=true 是工具业务错误，不是协议错误 —— 但对我们都是"这次没拿到数据"。
        # 额度用完也会走这里，所以同样先按额度识别一次。
        if isinstance(res, dict) and res.get("isError"):
            msg = ""
            for it in (res.get("content") or []):
                if isinstance(it, dict) and it.get("type") == "text":
                    msg = it.get("text") or ""
                    break
            msg = msg or json.dumps(res, ensure_ascii=False)
            if is_quota_text(msg):
                raise QuotaExhausted(msg[:160])
            raise SourceDown("tools/call isError: " + msg[:200])
        return res

    def list_tools(self):
        """拉工具清单（元数据请求，不是 tools/call）。

        只给探测脚本用：确认 inputSchema 里 time / count 的类型与含义。
        日常抓取不会调它 —— 参数是写死的，每轮多一次请求没有意义。
        """
        self._id += 1
        status, headers, body = self._post(
            {"jsonrpc": "2.0", "id": self._id, "method": "tools/list", "params": {}})
        if status >= 400:
            raise SourceDown("tools/list http %d" % status)
        d = self._parse(body, (headers or {}).get("Content-Type", "")) or {}
        if "error" in d:
            raise SourceDown("tools/list error: " +
                             json.dumps(d["error"], ensure_ascii=False)[:160])
        return (d.get("result") or {}).get("tools") or []


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


def parse_time(v):
    """把 秒 / 毫秒 / 日期字符串 统一成毫秒时间戳。失败返回 0。

    金十的字段口径文档没写全：报价与 K 线给 time，日历给 pub_time，
    可能是数字（秒或毫秒）也可能是 "2025-11-05 20:30:00" 这类字符串。
    无时区的字符串按北京时间解释（金十是国内站，日历时间就是北京时间）。
    """
    if isinstance(v, (int, float)):
        t = int(v)
        return t * 1000 if t < 10 ** 11 else t
    if isinstance(v, str):
        txt = v.strip()
    else:
        return 0
    if not txt:
        return 0
    if re.fullmatch(r"\d{10,13}", txt):
        t = int(txt)
        return t * 1000 if t < 10 ** 11 else t
    dt = None
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
            try:
                dt = datetime.strptime(txt, fmt)
                break
            except Exception:
                dt = None
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return int(dt.timestamp() * 1000)


def pick_list(payload, keys):
    """按 keys 顺序在 payload 里找数组，允许下钻一层 payload.data。

    金十的口径不统一：K 线是 data.klines，快讯/资讯是 data.items，
    日历干脆 data 本身就是数组。与其在每个 norm_* 里各写一遍，不如收敛到这里。
    """
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    d = payload.get("data")
    if isinstance(d, list):
        return d
    holders = [payload]
    if isinstance(d, dict):
        holders.append(d)
    for holder in holders:
        for k in keys:
            v = holder.get(k)
            if isinstance(v, list):
                return v
    return []


def list_items(payload):
    """列表类工具取数组：优先 data.items，其余交给 pick_list。"""
    return pick_list(payload, ("items", "klines"))


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
    """日历：文档口径为 data 直接是数组，每项 pub_time / star / title /
    previous / consensus / actual / revised / affect_txt。

    ccy 金十不给（国内站不按国家分），留空；前端渲染时缺 ccy 只显示事件名。
    consensus 是预期值（对应前端的 fc），actual 是公布值，还没出就为空。
    """
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        t = parse_time(it.get("pub_time") or it.get("time") or it.get("ts") or it.get("timestamp"))
        try:
            impact = int(it.get("star") or it.get("importance") or it.get("impact") or 3)
        except Exception:
            impact = 3
        out.append({
            "t": t,
            "ccy": (it.get("country") or it.get("currency") or it.get("ccy") or "").upper(),
            "name": str(it.get("title") or it.get("name") or "").strip(),
            "impact": max(1, min(5, impact)),
            "fc": str(it.get("consensus") or it.get("forecast") or it.get("fc") or ""),
            "pv": str(it.get("previous") or it.get("pv") or ""),
            "av": str(it.get("actual") or it.get("av") or ""),
            "affect": str(it.get("affect_txt") or "").strip(),
        })
    return [x for x in out if x["name"] and x["t"] > 0]


def norm_quote(payload):
    """报价：兼容单条与列表。字段对齐 J1 的备源映射规则。"""
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        # 文档口径：get_quote 返回单条，structuredContent 直接就是 {code, close, ...}
        if payload.get("code") or payload.get("close") or payload.get("lastPrice"):
            rows = [payload]
        else:
            rows = payload.get("items") or payload.get("data") or []
            if isinstance(rows, dict):
                rows = [rows]
    else:
        rows = []
    out = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        # 文档口径：close 是现价，ups_price 是涨跌额，ups_percent 是涨跌幅
        price = _first_num(it.get("close"), it.get("lastPrice"), it.get("price"), it.get("last"))
        if price is None:
            continue
        out.append({
            "code": str(it.get("code") or it.get("goldId") or ""),
            "name": str(it.get("name") or it.get("varietyName") or ""),
            "price": price,
            "change": pct_num(it.get("ups_percent") or it.get("changeMargin") or it.get("change")),
            "chg": _first_num(it.get("ups_price"), it.get("changeAmount")),
            "time": it.get("time") or it.get("updateTime") or "",
            "open": _first_num(it.get("open")),
            "high": _first_num(it.get("high")),
            "low": _first_num(it.get("low")),
        })
    return out


def norm_kline(payload):
    """K 线：输出 [{t,o,h,l,c,v}]，t 为毫秒开盘时间。

    文档口径：data.klines 为数组，每项 close / high / low / open / time / volume。
    时间可能是秒级时间戳或字符串，统一走 parse_time。
    每项是一个「周期」还是一根「bar」文档没写清，两种都试：取不到 o/h/l/c 的项直接丢。
    """
    rows = pick_list(payload, ("klines", "items", "bars"))
    out = []
    for it in rows:
        if isinstance(it, dict):
            try:
                t = parse_time(it.get("time") or it.get("t"))
                if not t:
                    continue
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
                t = parse_time(it[0])
                if not t:
                    continue
                out.append({"t": t, "o": float(it[1]), "h": float(it[2]),
                            "l": float(it[3]), "c": float(it[4]), "v": float(it[5] if len(it) > 5 else 0)})
            except Exception:
                continue
    out.sort(key=lambda x: x["t"])
    return out


def _first_num(*vals):
    """按顺序取第一个能转成正数的值。价格不会是 0，所以 0 视为无效。"""
    for v in vals:
        if v is None or v == "":
            continue
        try:
            f = float(str(v).replace(",", ""))
        except Exception:
            continue
        if f > 0:
            return f
    return None


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
        # 接入文档：list_calendar({}) —— 不收任何参数。
        # 原来的 {start, end, limit} 是照 ForexFactory 的习惯猜的，传了会被拒。
        payloads, pages = call_paged(mcp, "list_calendar", {}, 2, left, note)
        items = []
        for p in payloads:
            items.extend(norm_calendar(list_items(p)))
        return {"updatedAt": now.isoformat(), "events": items}, pages

    guard("list_calendar", "calendar.json", do_cal)

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
        calls.append((method, name, args))
        if method == "initialize":
            return 200, {"Content-Type": "application/json", "Mcp-Session-Id": "sess-1"}, \
                json.dumps({"jsonrpc": "2.0", "id": req["id"],
                            "result": {"protocolVersion": MCP_PROTOCOL,
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
    cal = read("calendar.json")["events"]

    # ---- 入参：守住"别把参数改回猜的那套" ----
    sent = {}
    for m_, n_, a_ in calls:
        if m_ == "tools/call":
            sent.setdefault(n_, a_)
    check("list_calendar 传空对象（实测：不收参数）", sent.get("list_calendar") == {},
          sent.get("list_calendar"))

    # ---- 解析：按实测字段 ----
    check("list_calendar 落盘且有内容", len(cal) >= 3, len(cal))
    check("list_calendar 翻页 2 页（cursor）", r1.get("list_calendar", "").endswith("x2"),
          r1.get("list_calendar"))
    check("日历 pub_time 字符串 → 毫秒", cal[0]["t"] > 10 ** 12, cal[0]["t"])
    check("日历 consensus → fc", cal[0]["fc"] == "18万", cal[0].get("fc"))
    check("日历 title → name", cal[0]["name"] in ("非农就业", "事件0"), cal[0]["name"])
    check("快讯/资讯已停抓", "list_flash" not in r1 and "list_news" not in r1, r1)
    check("get_quote / get_kline 已停抓（v6.58 起只跑日历）",
          "get_quote" not in r1 and "get_kline" not in r1, r1)

    # ---- 已下线的解析函数：直接喂假数据，防日后恢复时踩空 ----
    q = norm_quote({"code": "XAUUSD", "name": "现货黄金", "close": "4,072.40",
                    "open": "4,050.10", "high": 4080.0, "low": 4045.2,
                    "ups_price": 23.1, "ups_percent": "+1.23%"})
    check("norm_quote 单条对象 / close 千分位 / % 去符号",
          len(q) == 1 and q[0]["price"] == 4072.4 and q[0]["change"] == 1.23, q)
    kb2 = norm_kline({"data": {"klines": [
        {"time": 1762200000 + i * 60, "open": 4050, "high": 4060,
         "low": 4045, "close": 4055, "volume": 10} for i in range(3)]}})
    check("norm_kline data.klines / 秒级 time → 毫秒", len(kb2) == 3 and kb2[0]["t"] > 10 ** 12, kb2)
    check("norm_news 字段映射", norm_news([{"news_id": "n1", "ts": 1762290000,
                                            "title": "标题一", "summary": "摘要一"}])[0]["title"] == "标题一")

    print("[自测] 第二次（TTL 未过期）")
    check("list_calendar 命中缓存", r2.get("list_calendar") == "cached", r2.get("list_calendar"))

    print("[自测] 软上限：把 list_calendar 预置为 110")
    st = load_state()
    st["day"] = day_key_gmt8()
    st["counts"]["list_calendar"] = TOOLS["list_calendar"]["cap"]
    save_state(st)
    n_call_before = sum(1 for m_, _n, _a in calls if m_ == "tools/call")
    r3 = run(force=True, mcp=m, log=lambda *a: None)
    n_call_after = sum(1 for m_, _n, _a in calls if m_ == "tools/call")
    check("list_calendar 到顶即停", r3.get("list_calendar") == "capped", r3.get("list_calendar"))
    check("到顶后一个 tools/call 都没发", n_call_after == n_call_before,
          n_call_after - n_call_before)

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
    n = sum(1 for m_, _n, _a in calls if m_ == "tools/call")
    print("  tools/call 次数：%d" % n)
    print("结果：%s" % ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


def _mock_result(name, args, calls):
    """构造 3 个在抓工具的假响应，形状严格按金十接入文档，用来验证解析分支。

    故意用最"难"的形态：报价是单条对象、K 线时间戳是秒级、日历 data 是裸数组
    且 pub_time 是字符串。这三种都能过，真实响应就不会踩空。
    """
    if name == "list_calendar":
        # 文档：data 为数组；分页字段在顶层（data 是数组时放不进 next_cursor）
        page2 = bool(args.get("cursor"))
        base = 2 if page2 else 0
        rows = [
            {"pub_time": "2025-11-06 20:30:00" if i == 0 else "2025-11-07 21:00:00",
             "star": 5 if i == 0 else 3,
             "title": ("非农就业" if i == 0 and not page2 else "事件%d" % (i + base)),
             "previous": "15万", "consensus": "18万",
             "actual": "", "revised": "", "affect_txt": "利好美元"}
            for i in range(2)
        ]
        return {"structuredContent": {"data": rows,
                                      "has_more": not page2,
                                      "next_cursor": None if page2 else "cur-2"}}

    return {"structuredContent": {}}


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    if not os.environ.get("JIN10_TOKEN", "").strip():
        print("[jin10] 缺少 JIN10_TOKEN，跳过（不发起请求）", file=sys.stderr)
        sys.exit(2)
    res = run(force="--force" in sys.argv)
    print(json.dumps(res, ensure_ascii=False, indent=1))
