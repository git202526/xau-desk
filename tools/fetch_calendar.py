#!/usr/bin/env python3
"""抓取 ForexFactory 周历（经 faireconomy 镜像），精简成 calendar.json。

为什么不直接在页面里 fetch：
  这个源不返回 Access-Control-Allow-Origin，浏览器端取不到。
  所以由 GitHub Actions 每 6 小时跑一次，落成与 index.html 同源的静态文件。

用法：
  python3 tools/fetch_calendar.py            # 写 calendar.json
  python3 tools/fetch_calendar.py --dry-run   # 只打印，不写盘
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request

SOURCES = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]

# 只保留这几个币种：黄金主要受美元、欧元区、人民币、日元影响
KEEP_CCY = {"USD", "EUR", "CNY", "JPY"}
COUNTRY_CCY = {
    "US": "USD", "EU": "EUR", "CN": "CNY", "JP": "JPY",
    "DE": "EUR", "FR": "EUR", "IT": "EUR", "ES": "EUR", "NL": "EUR",
}
IMPACT_STARS = {"High": 4, "Medium": 3, "Low": 2}

# 英文标题 → 中文名。规则只给「指标本身」，国别前缀由币种决定，避免把
# 欧元区 GDP 认成美国 GDP。没命中的保留英文原名，不猜、不机翻。
NAME_RULES = [
    (r"core pce price index", "核心PCE物价指数"),
    (r"\bpce price index", "PCE物价指数"),
    (r"core cpi", "核心CPI"),
    (r"\bcpi\b", "CPI"),
    (r"gdp price index", "GDP价格指数"),
    (r"advance gdp|prelim gdp|final gdp|\bgdp q/q\b|\bgdp\b", "GDP"),
    (r"non-farm employment|nonfarm payroll", "非农就业"),
    (r"unemployment claims|jobless claims", "初请失业金"),
    (r"unemployment rate", "失业率"),
    (r"average hourly earnings", "平均时薪"),
    (r"adp employment", "ADP就业"),
    (r"jolts job openings", "JOLTS职位空缺"),
    (r"retail sales", "零售销售"),
    (r"ism manufacturing", "ISM制造业PMI"),
    (r"ism services|ism non-manufacturing", "ISM服务业PMI"),
    (r"cb consumer confidence", "谘商会消费者信心"),
    (r"michigan consumer sentiment|consumer sentiment", "密歇根消费者信心"),
    (r"durable goods", "耐用品订单"),
    (r"factory orders", "工厂订单"),
    (r"building permits", "营建许可"),
    (r"housing starts", "房屋开工"),
    (r"existing home sales", "成屋销售"),
    (r"pending home sales", "成屋签约销售"),
    (r"new home sales", "新屋销售"),
    (r"case-shiller", "房价指数"),
    (r"trade balance", "贸易帐"),
    (r"current account", "经常帐"),
    (r"\bppi\b|producer price", "PPI"),
    (r"import prices", "进口物价"),
    (r"export prices", "出口物价"),
    (r"empire state", "纽约联储制造业指数"),
    (r"philly fed", "费城联储制造业指数"),
    (r"industrial production", "工业产出"),
    (r"capacity utilization", "产能利用率"),
    (r"business inventories", "商业库存"),
    (r"personal income", "个人收入"),
    (r"personal spending", "个人支出"),
    (r"m3 money supply", "M3货币供应"),
    (r"private loans", "私人贷款"),
    (r"wholesale inventories", "批发库存"),
    (r"fed monetary policy|fomc statement|federal funds rate|rate decision", "美联储利率决议"),
    (r"fomc members? speak|fed chair|fed officials|fed speaks", "美联储官员讲话"),
    (r"fomc meeting minutes|fomc minutes", "美联储会议纪要"),
    (r"beige book", "美联储褐皮书"),
    (r"fomc press conference", "美联储发布会"),
    (r"crude oil inventories", "原油库存"),
    (r"natural gas storage", "天然气库存"),
    (r"main refinancing rate|deposit facility rate|monetary policy statement", "欧洲央行利率决议"),
    (r"ecb interest|ecb rate|ecb monetary policy", "欧洲央行利率决议"),
    (r"ecb press conference", "欧洲央行发布会"),
    (r"ecb president|lagarde speaks", "欧洲央行行长讲话"),
    (r"german ifo", "德国IFO商业景气"),
    (r"german zew", "德国ZEW经济景气"),
    (r"\bhicp\b|german flash cpi", "调和CPI"),
    (r"\bpmi\b", "制造业PMI"),
    (r"\bpboc\b|people's bank of china", "中国央行"),
    (r"\bboj\b|bank of japan", "日本央行"),
    (r"tankan", "短观调查"),
    (r"industrial production", "工业产出"),
]

_ZH = [(re.compile(p, re.I), zh) for p, zh in NAME_RULES]
PREFIX = {"USD": "美国", "EUR": "欧元区", "CNY": "中国", "JPY": "日本"}
NO_PREFIX = ("美联储", "欧洲央行", "中国央行", "日本央行", "德国")


def zh_name(en, ccy="USD"):
    for rx, core in _ZH:
        if not rx.search(en):
            continue
        low = en.lower()
        tail = ""
        if re.search(r"\bm/m\b|\bmonthly\b", low):
            tail = "月率"
        elif re.search(r"\by/y\b|\bannual\b|\byearly\b", low):
            tail = "年率"
        elif re.search(r"\bq/q\b|\bquarterly\b", low):
            tail = "季率"
        pre = "" if core.startswith(NO_PREFIX) else PREFIX.get(ccy, "")
        return pre + core + tail
    return en


def parse_ts(s):
    """FF 的 date 带偏移，例如 2026-10-29T08:30:00-04:00"""
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; xau-desk-calendar/1.0)",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def build(rows):
    out, seen = [], set()
    for x in rows:
        if not isinstance(x, dict):
            continue
        # FF 的 country 有时直接就是币种代码（USD），有时是国家码（US）
        raw = (x.get("country") or "").upper()
        ccy = raw if raw in KEEP_CCY else COUNTRY_CCY.get(raw, "")
        if ccy not in KEEP_CCY:
            continue
        ts = parse_ts(x.get("date") or "")
        en = (x.get("title") or "").strip()
        if not ts or not en:
            continue
        key = (ts // 60000, en.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "t": ts,
            "ccy": ccy,
            "name": zh_name(en, ccy),
            "en": en,
            "impact": IMPACT_STARS.get(x.get("impact") or "", 2),
            "fc": (x.get("forecast") or "").strip(),
            "pv": (x.get("previous") or "").strip(),
            "src": "ff",
        })
    out.sort(key=lambda e: (e["t"], -e["impact"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="calendar.json")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows, used = [], []
    for u in SOURCES:
        try:
            rows += fetch(u)
            used.append(u.rsplit("/", 1)[-1])
        except Exception as e:  # 单个源失败不影响另一个
            print("warn: %s -> %s" % (u, e), file=sys.stderr)

    events = build(rows)

    # schema 校验：拿不到足够数据就不覆盖旧文件，宁可让页面走回落
    if len(events) < 5:
        print("FAIL: 只解析出 %d 条，保留旧文件" % len(events), file=sys.stderr)
        return 1

    now = dt.datetime.now(dt.timezone.utc)
    doc = {
        "updatedAt": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "week": "%s..%s" % (
            dt.datetime.fromtimestamp(events[0]["t"] / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
            dt.datetime.fromtimestamp(events[-1]["t"] / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
        ),
        "src": ",".join(used),
        "count": len(events),
        "events": events,
    }

    print("events: %d（USD %d / 4星 %d）" % (
        len(events),
        sum(1 for e in events if e["ccy"] == "USD"),
        sum(1 for e in events if e["impact"] == 4),
    ))
    if a.dry_run:
        print(json.dumps(doc["events"][:6], ensure_ascii=False, indent=2))
        return 0

    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, a.out)
    print("wrote %s (%d bytes)" % (a.out, os.path.getsize(a.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
