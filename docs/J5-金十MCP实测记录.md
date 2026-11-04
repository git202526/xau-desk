# J5 金十 MCP 实测记录

日期：2026-11-04 12:1x（GMT+8）
目的：把 J2 遗留的「入参 schema 未核实」一次性问清楚，顺带验证 K 线能否当校验源。
凭据：用户提供，只经环境变量传入进程，未落盘、未写入任何文件；本文所有输出已脱敏。

---

## 1. 请求清单与额度消耗

| # | 请求 | 结果 | 计入 `tools/call` |
|---|---|---|---|
| 1 | `initialize`（错误 token） | 401 `invalid token: invalid api key` | 否 |
| 2 | `initialize`（正确 token） | 200，`serverInfo` = `jin10-mcp` v1.0.0 | 否 |
| 3 | `tools/list` | 200，8 个工具 | 否（元数据请求） |
| 4 | `get_kline({code, time:"1d", count:1000})` | `isError`：`time` 类型应为 integer | 是（1） |
| 5 | `get_kline({code, count:100})` | 200，100 根 1 分钟线 | 是（1） |
| 6 | `get_quote({code:"XAUUSD"})` | 200，秒级实时报价 | 是（1） |

实际消耗 **3 次** `tools/call`，占日额度 1500 的 0.2%。

探测过程中还发现：token 里的 `l`（小写 L）在截图上与 `I` / `1` 几乎无法区分，
前两次 OCR 都读错，报 401。**结论：凭据不要从截图转抄，直接让用户填进 Secret。**

---

## 2. `tools/list` 拿到的真实 schema

### get_kline

```json
{
  "type": "object",
  "properties": {
    "code":  {"type": "string", "description": "行情品种代码，如 XAUUSD（必填）"},
    "time":  {"type": "integer", "description": "起始 Unix 时间戳（秒），从此往后取数据，范围24小时内（可选，默认当前时间）"},
    "count": {"type": "integer", "description": "从 time 开始往后取 count 个分钟K线，范围1-100（可选，默认100）"}
  },
  "required": ["code"],
  "additionalProperties": false
}
```

**三个要害**：

1. `time` 是**起始 Unix 秒**，不是周期名。传 `"1d"` 直接 `isError`。
2. `count` 上限 **100**，不是 200 也不是 800。
3. 返回的是**分钟级** K 线，且只在**最近 24 小时窗口**内。

### get_quote

```json
{
  "type": "object",
  "properties": {"code": {"type": "string", "description": "如 XAUUSD（必填）"}},
  "required": ["code"],
  "additionalProperties": false
}
```

### list_calendar

```json
{"type": "object", "properties": {}, "additionalProperties": false}
```

不收任何参数；描述为「当前自然周（周一到周日）的财经日历」。

---

## 3. `get_kline` 实测（`{code:"XAUUSD", count:100}`）

外层结构：

```json
{"status": 200, "message": "", "data": {"code": "XAUUSD", "name": "现货黄金", "klines": [...]}}
```

`klines` 实测：

| 项 | 实测值 |
|---|---|
| 根数 | 100（等于请求的 `count`） |
| 相邻间隔 | **60 秒** |
| 首末跨度 | **1.65 小时** |
| 排列顺序 | **倒序**（新的在前） |
| 字段类型 | `close` / `high` / `low` / `open` 都是**字符串**，`volume` 是数字 |
| `time` | 秒级整数 |

单根样例：

```json
{"time": 1793765820, "open": "3946.41", "high": "3946.41", "low": "3945.32", "close": "3945.34", "volume": 9}
```

---

## 4. `get_quote` 实测

```json
{"status": 200, "message": "",
 "data": {"code": "XAUUSD", "name": "现货黄金", "time": "2026-11-04T12:17:05+08:00",
          "open": "3931.19", "close": "3946.13", "high": "3964.76", "low": "3919.80",
          "volume": 103571, "ups_price": "15.66", "ups_percent": "0.40"}}
```

`time` 精确到秒且就是请求时刻 —— 金十报价本身是**实时**的。
注意 `ups_percent` 是 `"0.40"`（不带 `%`），`ups_price` 是**涨跌额**。

---

## 5. 结论：K 线校验这条路走不通

J3 设计的校验逻辑是「`1d` / `4h` / `1h` 各取 20 根已收盘 bar，与 Binance 的 PAXG 比收益率」。
实测口径下这**物理上不可能**：

| 需要的 | 金十能给 | 差 |
|---|---|---|
| 日线 20 根 = 20 天跨度 | 100 根 1 分钟 = 1.65 小时 | 缺口约 290 倍 |
| 4H 20 根 = 80 小时跨度 | 同上 | 缺口约 48 倍 |
| 1H 20 根 = 20 小时跨度 | 同上 | 缺口约 12 倍 |

**金十没有多周期 K 线接口，只有分钟线，且窗口 24 小时、单次 100 根。**
所以：

- `kline_*.json` 这一整套没有意义，`klVerify` 会永远是「未就绪」。
- 前端与中转两侧的 K 线抓取已在 v6.58 整体删除（含 `ENABLE_KLINE` 在内的四个常量），
  不是靠开关关掉 —— 开关留在代码里，下一个人还是会把它打开。

金十的价值收敛到**日历一项**（`list_calendar` 恰是自然周财经日历，正是页面需要的）。
`get_quote` 虽是秒级实时，但中转最快 5 分钟一份快照，救不了页面的 15 秒报价；
报价已有 Gold API 一层，备源（gold-api.cn）的端点路径未核实、当前未启用，
第三层的边际价值不足以抵掉复杂度。

---

## 6. 本轮纠正的错误（全部是我此前的推断，没有一条对）

| 我之前的说法 | 实测 |
|---|---|
| 品种代码 `hf_XAU` | `XAUUSD` |
| `get_kline({code, period, limit})` | `{code, time:整数秒, count:1-100}` |
| `get_quote({codes:["hf_XAU"]})` | `{code: "XAUUSD"}`（单数） |
| `list_calendar({start, end, limit})` | `{}`（不收参数） |
| 单次可返回 200 根 | 100 根，且是 1 分钟线 |
| K 线有 `1w/1d/4h/1h` 四个周期 | 只有一个分钟级接口 |
| 返回 `data.items` / `bars` | `data.klines` |
| 报价字段 `lastPrice` / `changeMargin` | `close` / `ups_price` / `ups_percent` |
| 协议版本 `2025-06-18` | 服务端回 `2025-11-25`（也接受旧版） |

教训：**「按命名推断」在协议对接上等于瞎猜。** 3 次调用就能问清的事，拖了三个版本。
