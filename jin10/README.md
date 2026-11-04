# 金十中转产物

本目录由 `.github/workflows/jin10.yml` 每 5 分钟写入，勿手工编辑。
浏览器只读这里的同源静态文件，不直连 mcp.jin10.com（CORS 不放行，见 docs/J2-金十MCP探测记录.md）。

| 文件 | 来源工具 | 缓存 TTL | 页面用途 |
|---|---|---|---|
| `calendar.json` | `list_calendar` | 1200s | 事件雷达主源（失败回落 ForexFactory） |
| `kline_1w/1d/4h/1h.json` | `get_kline`（`hf_XAU`） | 240s | K 线源校验；Binance 全挂时兼作备源 |
| `quote.json` | `get_quote` | 60s | 仅落盘，浏览器侧不用（5 分钟滞后救不了 15 秒实时价） |
| `state.json` | — | — | 当日计数与停用标记 |

## 已下线

`flash.json` / `news.json`（对应 `list_flash` / `list_news`）自 v6.54 起停抓：
页面上的快讯 / 资讯卡片已移除，继续抓只是每天白烧 900 次额度。
`tools/fetch_jin10.py` 里的 `norm_flash` / `norm_news` 解析函数保留，恢复时把 `guard` 加回来即可。

## 每日额度（GMT+8 切日）

| 工具 | 软上限 |
|---|---|
| `list_calendar` | 110 |
| `get_quote` | 200 |
| `get_kline` | 200（四个周期共用一个池） |

计数按**实际发出的请求**记：超时、5xx、额度用完同样 +1。
剩余 ≤ 2 时不再重试，避免最后一轮把额度烧光。
