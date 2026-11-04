# J2 · 金十 MCP 探测记录

> 执行时间：2026-11-04 09:40（GMT+8）
> 探测对象：`https://mcp.jin10.com/mcp`
> 约束：本阶段是全程唯一允许真实请求的阶段，请求数严格受限，不调业务工具。

## 一、结论

**CORS 不放行 → 走中转（GitHub Actions 每 5 分钟拉一次）。**

判定依据：OPTIONS 预检返回 401 且**完全没有任何 `Access-Control-*` 响应头**；带 `Origin` 的 POST 被服务端以 403 显式拒绝，理由是「`Sec-Fetch-Site` 缺失，且 `Origin` 与 `Host` 不匹配」。浏览器发起的跨源 POST 必然满足「Origin ≠ Host」，因此**无论 Token 是否正确，浏览器直连都不可能成功**。

## 二、请求清单（共 3 次，均为探测性质）

| # | 方法 | 带 Token | 用于 | 结果 |
|---|---|---|---|---|
| 1 | `OPTIONS` | 否 | 预检是否放行 | `401`，无任何 ACAO 头 |
| 2 | `OPTIONS` | 否 | 复核响应头（同上端点，仅取完整头） | `401`，确认无 ACAO |
| 3 | `POST` initialize | 是（故意无效的占位串） | 看鉴权层与 CORS 行为 | `403`，跨源被拒 |

> 说明：第 1、2 次是同一端点同一请求（第一次只取状态码，第二次取完整响应头），**实际有 3 次网络往返**，如实记录，不合并成 2 次粉饰。

## 三、逐次原始结果

### 1. OPTIONS 预检（不带 Token）

请求头：

```
Origin: https://git202526.github.io
Access-Control-Request-Method: POST
Access-Control-Request-Headers: authorization,content-type
```

响应：

```
HTTP/2 401
content-type: text/plain; charset=utf-8
content-length: 16
www-authenticate: Bearer resource_metadata="http://localhost:8080/.well-known/oauth-protected-resource"
x-content-type-options: nosniff
```

三点关键事实：

1. **没有任何 `Access-Control-Allow-Origin`**，也没有 `Allow-Methods` / `Allow-Headers` / `Max-Age`。浏览器会在预检阶段直接判失败，根本走不到 POST。
2. **OPTIONS 不当作预检处理**，而是走了鉴权分支（401）。说明该服务只按 MCP 应用端点实现，未为浏览器跨源做任何适配。
3. `www-authenticate` 里的 `resource_metadata` 指向 `http://localhost:8080/...` —— **OAuth 发现地址是错的**（应为公网地址）。走 OAuth 动态发现的路子也走不通，只能手工填 Bearer。

### 2. POST initialize（无效 Token）

请求头：

```
Origin: https://git202526.github.io
Content-Type: application/json
Accept: application/json, text/event-stream
Authorization: Bearer <已脱敏>
```

响应：

```
HTTP/2 403
content-type: text/plain; charset=utf-8
content-length: 120

cross-origin request detected, and/or browser is out of date:
Sec-Fetch-Site is missing, and Origin does not match Host
```

要点：

- **403 不是鉴权失败，是跨源拒绝**。服务端先做来源校验，再轮不到 Token 校验；所以「Token 对不对」在这一层根本体现不出来。
- 校验条件写作 `Sec-Fetch-Site is missing, and Origin does not match Host`。浏览器跨源 POST 会带 `Sec-Fetch-Site: cross-site`，且 Origin 恒等于页面源（`git202526.github.io`），永远不可能等于 `mcp.jin10.com`。**这是无解的门，不是可以绕过的配置问题。**
- 无 ACAO，即使侥幸返回 200，浏览器也会拦截响应体。

## 四、路由判定

按 J2 规则表：

| 条件 | 判定 |
|---|---|
| CORS 放行 且 能拿会话 | 直连 |
| CORS 放行 但 拿不到会话 | 中转 |
| **CORS 不放行** | **中转** ← 命中 |

**结论：中转。** 由 GitHub Actions 每 5 分钟拉一次，落静态 JSON，浏览器只读同源文件。这与现有 `calendar.json` 的形态完全一致，可复用现成的 workflow 骨架与"小时级 URL 戳"防缓存写法。

## 五、未完成的一项：真实 Token initialize（无凭据）

规则要求「1 次真实 Token initialize」，**本次未执行**，原因是配置中不存在金十凭据：

| 查找位置 | 结果 |
|---|---|
| `~/.codebuddy/.mcp.json` | 只有 `space-library` 一个 server，无金十 |
| `settings.json` / `user-state.json` | 无 `jin10` 字样 |
| `~/.codebuddy` 全域 `*mcp*` 文件 | 仅 `.mcp.json` |
| 环境变量 | 无 `JIN10*` / 相关 Token 变量 |
| 仓库（`/workspace` 全域文本） | 无 `jin10` 字样 |
| `~/uploads` | 空 |

按「查不到的写常量 + TODO，不要编」的规则，此处**留空并标 TODO**，没有用占位串冒充真实凭据去跑一次"看起来完成了"的探测。

**这不影响路由结论**：CORS 已判定不放行，无论真实 Token 能否拿到会话，结果都是「中转」。真实 Token 的作用要在**中转侧**验证（Actions 里能否 initialize 成功、能否拿到 `Mcp-Session-Id`），属 J3 的实施前提，见下。

## 六、交给 J3 的两个前置未知

1. **中转侧能否建立会话**：服务端来源校验要求 `Origin` 匹配 Host。Actions 里用 `curl` 不带 `Origin`，或显式带 `Origin: https://mcp.jin10.com`，是否放行 —— 未验证（超出 J2 请求配额）。若两种都不行，中转方案同样落空，需要另找入口。
2. **会话是否需要保持**：MCP Streamable HTTP 通常在 `initialize` 响应头返回 `Mcp-Session-Id`，后续请求要带。5 分钟一次的短连接拉取，要么每次重新 initialize（多一次往返），要么把 session id 落盘复用（可能过期）。这决定 Actions 脚本是「1 次请求」还是「2 次请求」。

## 七、变更声明

- **本轮改了 0 行代码**，只新增本文档
- **未改变价格合成算法**
- **未改变业务规则**
- 未打印任何真实 Token / Key / Cookie / Authorization 值；探测用的 Token 是故意构造的无效占位串，已标 `<已脱敏>`
- 请求数：3 次（2 次 OPTIONS + 1 次无效 Token POST），未调用任何业务工具
