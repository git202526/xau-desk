# 金十中转产物

本目录由 `.github/workflows/jin10.yml` 每 5 分钟写入，勿手工编辑。
浏览器只读这里的同源静态文件，不直连 mcp.jin10.com（CORS 不放行，见 docs/J2-金十MCP探测记录.md）。

| 文件 | 来源工具 | 缓存 TTL |
|---|---|---|
| `flash.json` | `list_flash` | 240s 交易 / 300s 非交易 |
| `news.json` | `list_news` | 600s |
| `calendar.json` | `list_calendar` | 1200s |
| `quote.json` | `get_quote` | 60s |
| `kline_1w/1d/4h/1h.json` | `get_kline` | 240s |
| `state.json` | — | 当日计数与停用标记 |
