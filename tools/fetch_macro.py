"""抓宏观双变量：10Y TIPS 实际收益率 + ICE 美元指数，落 macro.json。

为什么是这两个源：
  - **TIPS**：FRED `DFII10`（10 年期通胀保值国债实际收益率），CSV 直出、无需 key。
    伦敦金与 10Y 实际利率的负相关是几十年最稳的一条，比名义利率干净。
  - **DXY**：CNBC 的 ts-api 直接给 ICE 美元指数日线（`.DXY`），1 年 259 根、无需 key。
    Yahoo 对共享出口 429；Stooq 加了 JS 工作量证明，Actions 里跑不了；
    FRED 的 `DTWEXBGS`（广义美元）是周更、滞后 5–7 天，做不了「今天美元怎么走」。

口径：
  - `change_bp` / `change_pct` = 最近两个**观测值**之差（不是自然日），周末自动跳过。
  - `trend_5d` = 最近值与 5 个观测值之前比，用来滤掉单日噪声。
  - `as_of` 用源自己的日期，前端展示「数据截至」，不拿抓取时间冒充行情时间。

失败策略：**两个源必须都成功才落盘**。任一失败就退出 1、保留旧文件——
半张卡（只有 TIPS 没有美元）比一张明确标注「数据延迟」的旧卡更容易误导。
"""
import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.request

FRED_TIPS = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
CNBC_DXY = "https://ts-api.cnbc.com/harmony/app/charts/1M.json?symbol=.DXY"
UA = "Mozilla/5.0 (compatible; xau-desk/7.6; +https://github.com/git202526/xau-desk)"
HIST_N = 12


def get(url, tries=3, timeout=25):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("GET %s 失败：%s" % (url, last))


def parse_fred(text):
    """DATE,DFII10 → [(date, value)]，跳过 '.' 与坏行。"""
    rows = []
    rd = csv.reader(io.StringIO(text))
    head = next(rd, None)
    if not head or "DFII10" not in head[-1]:
        raise RuntimeError("FRED 返回格式变了：%s" % (head,))
    for r in rd:
        if len(r) < 2 or r[1] in (".", "", "NA"):
            continue
        try:
            rows.append((r[0], float(r[1])))
        except ValueError:
            continue
    if len(rows) < 8:
        raise RuntimeError("FRED 有效观测不足：%d" % len(rows))
    return rows


def parse_cnbc(text):
    d = json.loads(text)
    bars = (d.get("barData") or {}).get("priceBars") or []
    rows = []
    for b in bars:
        try:
            rows.append((str(b["tradeTime"])[:8], float(b["close"])))
        except (KeyError, TypeError, ValueError):
            continue
    if len(rows) < 8:
        raise RuntimeError("CNBC 有效观测不足：%d" % len(rows))
    rows.sort(key=lambda x: x[0])
    return rows


def iso(d):
    """20261106 → 2026-11-06"""
    d = str(d)
    return "%s-%s-%s" % (d[0:4], d[4:6], d[6:8]) if len(d) >= 8 and "-" not in d else d


def tail(rows):
    """(最新值, 前值, 5 个观测前, as_of, 样本数)"""
    v, prev = rows[-1][1], rows[-2][1]
    base5 = rows[-6][1] if len(rows) >= 6 else rows[0][1]
    return v, prev, base5, iso(rows[-1][0]), len(rows)


def build(tips_rows, dxy_rows, now_ms, errors=None):
    v, prev, b5, asof, n = tail(tips_rows)
    tips = {
        "value": round(v, 2),
        "change_bp": round((v - prev) * 100, 1),        # 收益率是百分点，×100 换 bp
        "trend_5d": round((v - b5) * 100, 1),
        "as_of": asof, "n": n,
    }
    v, prev, b5, asof, n = tail(dxy_rows)
    dxy = {
        "value": round(v, 3),
        "change_pct": round((v / prev - 1) * 100, 2),   # 指数是比值，必须用涨幅
        "trend_5d": round((v / b5 - 1) * 100, 2),
        "as_of": asof, "src": "cnbc", "n": n,
    }
    return {
        "fetched_at": int(now_ms),
        "src": "fred+cnbc",
        "tips_10y": tips,
        "dxy": dxy,
        "hist": {
            "tips": [{"d": iso(d), "v": round(x, 2)} for d, x in tips_rows[-HIST_N:]],
            "dxy": [{"d": iso(d), "v": round(x, 3)} for d, x in dxy_rows[-HIST_N:]],
        },
        "errors": errors or [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="macro.json")
    ap.add_argument("--print", action="store_true")
    args = ap.parse_args()

    try:
        tips = parse_fred(get(FRED_TIPS))
        dxy = parse_cnbc(get(CNBC_DXY))
    except Exception as e:                        # noqa: BLE001
        print("抓取失败，保留旧文件：%s" % e, file=sys.stderr)
        return 1

    doc = build(tips, dxy, time.time() * 1000)
    raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"
    if args.print:
        print(raw)
    old = ""
    if os.path.exists(args.out):
        with open(args.out, "r", encoding="utf-8") as f:
            old = f.read()
    if raw != old:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(raw)
        print("写入 %s" % args.out)
    else:
        print("无变化")
    print("TIPS %s%% (%+.1fbp, as_of %s) · DXY %s (%+.2f%%, as_of %s)" % (
        doc["tips_10y"]["value"], doc["tips_10y"]["change_bp"], doc["tips_10y"]["as_of"],
        doc["dxy"]["value"], doc["dxy"]["change_pct"], doc["dxy"]["as_of"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
