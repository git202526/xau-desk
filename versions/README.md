# 版本归档

每个发布过的版本都存一份完整的单文件 HTML，出问题可以直接打开旧版。

| 版本 | 文件 | 在线地址 | 存储方式 |
|---|---|---|---|
| v6.3 | `v6.3.html` | https://git202526.github.io/xau-desk/versions/v6.3.html | GitHub 私有仓库为唯一数据源 |
| v6.2 | `v6.2.html` | https://git202526.github.io/xau-desk/versions/v6.2.html | GitHub 私有仓库为唯一数据源 |
| v6.1 | `v6.1.html` | https://git202526.github.io/xau-desk/versions/v6.1.html | GitHub 私有仓库为唯一数据源 |
| v6.0 | `v6.0.html` | https://git202526.github.io/xau-desk/versions/v6.0.html | GitHub 私有仓库为唯一数据源 |
| v5.2 | `v5.2.html` | https://git202526.github.io/xau-desk/versions/v5.2.html | 浏览器 localStorage + 可选云同步 |

> v5.3 没有单独存档（当时是在同一文件上直接改的，被 v6.0 覆盖）。它和 v6.0 的功能差异只有存储层：v5.3 是「本地为主、云端备份」，v6.0 反过来。功能算法两边一致。

## 怎么回退

### 方式一：临时用旧版看数据（推荐）

直接打开上表里的旧版地址。旧版会读同一个 `data.json`，看到的是同一份持仓。

缺点是那个地址不固定，不适合天天用。

### 方式二：正式回退（Pages 主地址换成旧版）

1. 下载对应版本的 HTML
2. 在仓库根目录把 `index.html` 换成它（GitHub 网页上直接编辑 / 上传覆盖）
3. 等 1 分钟，主地址 https://git202526.github.io/xau-desk/ 就是旧版了

数据不受影响，`data.json` 在两个仓库里独立存放。

> 注意：v5.2 是「本地存储为主」，回退后在新设备上打开会是空的，需要手动「从云端拉取」一次。

### 方式三：按提交记录回退

仓库每次改动都有独立提交，`Commits` 里找到对应时间点，点文件名 → `History` → 选旧版本 → 复制内容。

## 以后怎么办

- 每次发版前，先把当前的 `index.html` 复制成 `versions/vX.Y.html`，再改代码
- 每次发版在 `CHANGELOG.md` 记一条：改了什么、为什么、有没有数据格式变化
- 打完版本打一个 git tag，界面上能直接跳到那个时间点

这套流程从 v6.0 起执行，之前的版本只能补到 v5.2。
