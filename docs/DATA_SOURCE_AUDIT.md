# 数据来源审计（Phase 0）

- 审计时间：2026-11-16 19:20（UTC+8）
- 审计对象：本仓库 `main` 分支工作区
- 审计人：Agent（依 `AGENTS.md` 第 2 节「先审计再修改」要求执行）
- 结论摘要：**真实数据覆盖率约 11%（3 个板块 / 共 28 个板块）**；若按数据点计则更低，因为真实的 3 个板块内部也只覆盖单一指标。后端分析链路无输入文件，等同空壳；前端存在静默回退 mock 的违规行为，需在 Phase 5 前修正。

---

## 1. 代码资产清单

### 1.1 后端（Flask）

| 文件 | 职责 | 状态 |
|---|---|---|
| `backend/app.py` | 7 个路由 | 3 个依赖缺失数据文件，恒返回 404 |
| `backend/data_cleaning.py` | 原始 CSV → cleaned | **无输入**：`backend/data/before/players.csv` 不存在 |
| `backend/data_analysis.py` | 情感与热点 | **无输入**：`cleaned_players.csv` 不存在 |
| `backend/churn_risk_analysis.py` | 流失风险测算 | **无输入**：`cleaned_players.csv` / `cleaned_history.csv` 不存在 |
| `backend/churn_retention_analysis.py` | 召回测算 | 同上 |
| `backend/player_comparison_analysis.py` | 玩家对比 | 同上 |
| `backend/analyze_sentiment.py` | 关键词情感打分 | **无输入**：`cleaned_reviews.csv` 不存在 |

路由与数据依赖：

| 路由 | 依赖 | 当前表现 |
|---|---|---|
| `GET /api/health` | 无 | 可用 |
| `GET/POST /api/game-data` | `instance/game_churn.db` | 可用（表 `user`, `game_data`） |
| `GET /api/player-comparison` | `data/player_comparison.json` | 404 |
| `GET /api/churn-retention` | `data/churn_retention.json` | 404 |
| `GET /api/churn-risk` | `data/churn_risk.json` | 404 |
| `GET /api/sentiment-trend` | 无数据时返回内置占位趋势 | 返回占位（**属静默回退，违反 `AGENTS.md` §4.3**） |

### 1.2 采集器（`backend/collectors/`）

| 文件 | 职责 | 状态 |
|---|---|---|
| `steam_collector.py` | CCU + 成就 → SQLite | 可用；`--proxy auto` 自适应网络 |
| `ci_publish.py` | CI 版：多游戏 CCU + 成就 → `data` 分支 | 可用，4 款游戏 |
| `export_snapshot.py` | 生成站点内离线兜底 JSON | 可用 |
| `funnel.py` | 成就漏斗命令行分析 | 可用 |

### 1.3 工作流（`.github/workflows/`）

| 文件 | 触发 | 作用 |
|---|---|---|
| `collect-ccu.yml` | `cron 5 * * * *` + 手动 | 每小时采集 4 款游戏 CCU + 成就，写 `data` 分支 |
| `deploy.yml` | push main | 发布 `front/static_html` 到 Pages |
| `smoke.yml` | PR / push main,dev | Playwright 四页冒烟，84 项断言 |

### 1.4 前端（`front/static_html/`）

4 个页面：`index.html`（总览）、`analysis.html`（流失分析）、`solution.html`（召回策略）、`validation.html`（留存追踪）。
取数唯一出口：`js/dashboard-data.js`（`window.DashboardData`）。

---

## 2. 数据库现状

| 库 | 表 | 行数 | 说明 |
|---|---|---:|---|
| `backend/data/steam.db` | `ccu` | 74 | 3 个 appid；2026-11-16 08:22–11:17 UTC |
| | `achievements` | 470 | 7 次快照 |
| `backend/instance/game_churn.db` | `user` / `game_data` | 未使用 | 旧版实验遗留，当前页面不读取 |

CCU 覆盖明细（本地库）：

| appid | 点数 | 时间范围（UTC） | 备注 |
|---|---:|---|---|
| 1203220 永劫无间 | 46 | 08:22 – 11:17 | 本地计划任务每 5 分钟持续写入 |
| 578080 PUBG | 14 | 08:22 – 08:30 | 仅早期测试，已停止 |
| 230410 Warframe | 14 | 08:22 – 08:30 | 同上 |
| 238960 Path of Exile | 0 | — | **从未写入本地库** |

> 已执行备份：`backend/data/backup/steam-20261116T111954Z.db`（77,824 B），行数与时间范围与源库逐项一致。
> 注：`backend/data/*.db` 已被 `.gitignore` 排除，备份同样不入库。

线上 JSON（`data` 分支）覆盖 4 款游戏，是当前唯一对页面生效的真实数据源。

---

## 3. 外部接口实测结果

实测时间 2026-11-16 17:00–19:20（UTC+8），环境为本机（TUN 代理）。

| 接口 | 是否需要 Key | 实测 | 备注 |
|---|---|---|---|
| `ISteamUserStats/GetNumberOfCurrentPlayers/v1` | 否 | ✅ 200 | 有 `Cache-Control: max-age≈647`，**必须加穿透参数** |
| `ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2` | 否 | ✅ 200 | Dota 2 返回空数组；CS2 仅 1 个成就 |
| **`IUserReviewsService/GetAppReviews/v1`** | **否** | ✅ **200** | **新版官方服务，字段完整，带 `cursor` 分页** |
| `store.steampowered.com/appreviews/{appid}?json=1` | 否 | ⚠️ 302 | 本机出口被重定向到首页，不可用 |
| `store.steampowered.com/api/appdetails` | 否 | ✅ 200 | 可取中文名、封面、免费状态 |
| `steamspy.com/api.php` | 否 | ❌ 403 | Cloudflare 挑战 |

### 3.1 关键发现：`IUserReviewsService` 确实存在

`AGENTS.md` 与方案文档主张优先使用新接口。实测确认成立，且**无需 API Key**：

```
GET https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/?appid=1203220&filter=recent&num_per_page=2&language=all
→ HTTP 200
```

返回结构（实测）：

- 顶层：`query_summary`、`reviews[]`、`cursor`、`day_range_used`、`total_matching`
- `query_summary`：`num_reviews`、`review_score`、`review_score_desc`、`total_positive`、`total_negative`、`total_reviews`
- `review` 字段：`recommendationid`、`review`、`voted_up`、`votes_up`、`votes_funny`、`weighted_vote_score`、`language`、`timestamp_created`、`timestamp_updated`、`comment_count`、`steam_purchase`、`received_for_free`、`refunded`、`written_during_early_access`、`primarily_steam_deck`
- `author` 字段：`steamid`、`playtime_forever`、`playtime_at_review`、`playtime_last_two_weeks`、`last_played`、`num_reviews`

对方案的三点影响：

1. **评论采集在技术上完全可行**，Phase 2 不必降级为「用公开数据集离线分析」。
2. `cursor` 可用（实测返回 `AoIFQFaAAAAAAAB2kYGNBw==`），支持断点续采。
3. 旧 `/appreviews` 接口在本机已不可用（302），**兼容实现只能作为降级路径，不能作为主路径**。

---

## 4. 前端数据来源矩阵（逐板块）

标记：`real` 真实 · `derived` 真实数据衍生 · `mock` 模拟 · `unavailable` 不可得

### 4.1 总览 `index.html`

| 板块 | 数据点 | 类型 | 来源 |
|---|---|---|---|
| KPI「活跃玩家」 | 数值 + 环比 | **real** | `data` 分支 `steam-ccu.json`（4 款合计或单游戏） |
| 「玩家活跃趋势」图 | 4 条指数曲线 | **real** | 同上，归一化 = 值 ÷ 窗口均值 × 100 |
| KPI「高危玩家」8,780 | — | mock | `KPI_BASE` |
| KPI「情感指数」+0.64 | — | mock | `KPI_BASE` |
| KPI「召回率」22.6% | — | mock | `KPI_BASE` |
| 情感趋势图 | 正面/中性/负面 | mock | `series().sentiment` |
| 区域流失排行 | 5 个区域 | mock | `REGIONS` + `REGION_FACTOR` |
| 三维地球（点位/热点/弧线） | 16 点 / 3 热点 / 4 弧 | mock | `PLAYER_POINTS`、`HOTSPOTS`、`CHURN_ARCS` |
| 信息卡 / 底部决策链 | — | mock | 同上派生 |
| AI 预警（3 条） | — | mock | `ALERTS` |
| 下一步最优动作 | — | mock | `ACTIONS` |
| 底部实时动态条 | — | mock | `LIVE_ACTIVITIES` |

### 4.2 流失分析 `analysis.html`

| 板块 | 类型 | 来源 |
|---|---|---|
| 4 个 KPI（流失率/风险区域/热点强度/覆盖量） | mock | 硬编码于 HTML |
| 「流失率变化趋势」 | mock | 内联 ECharts 固定数组 |
| **「成就完成漏斗」** | **real** | `data` 分支 `steam-funnels.json`（4 款） |
| 玩家分群画像 | mock | 硬编码 |
| 热点与流失关联 | mock | 硬编码 |

### 4.3 召回策略 `solution.html`

全部 mock / 推演：风险概览、人群细分、AI 引擎状态、方案卡（27.3% 召回率等）、四张策略卡。
唯一「真实」的是模型调用本身（SiliconFlow），但**模型回答不是数据**。

### 4.4 留存追踪 `validation.html`

全部 mock：留存 KPI、三曲线、cohort 热力表、分群、影响因素。

### 4.5 汇总

| 页面 | real 板块 | mock 板块 | 真实占比 |
|---|---:|---:|---|
| 总览 | 2 | 10 | 17% |
| 流失分析 | 1 | 4 | 20% |
| 召回策略 | 0 | 5 | 0% |
| 留存追踪 | 0 | 6 | 0% |
| **合计** | **3** | **25** | **约 11%**（按板块计） |

---

## 5. 已识别问题（按严重度）

| # | 问题 | 严重度 | 位置 | 处理 |
|---|---|---|---|---|
| P1 | **前端静默回退 mock**：真实数据缺失时不报 `unavailable`，直接画演示序列 | 高 | `loadRealCcu` / 首页 | 违反 §4.3，Phase 5 必须改为显式空态 |
| P2 | 后端分析链路无输入：6 个脚本依赖的 CSV 全部不存在 | 高 | `backend/*.py` | Phase 2/3 重建数据源 |
| P3 | 3 个 API 恒 404，但前端未调用，问题被掩盖 | 中 | `app.py` | Phase 1 起按需重写 |
| P4 | 本地 SQLite 只覆盖 1 款游戏（活跃） | 中 | 计划任务 `NEXUS-SteamCCU` | 扩到配置列表 |
| P5 | `KPI_BASE` 中的「召回率 22.6%」「高危玩家 8,780」等指标口径不存在 | 高 | `dashboard-data.js` | Phase 5 移除或改名 |
| P6 | 留存页展示 D1/D7/D30 Cohort，当前权限**不可计算** | 高 | `validation.html` | Phase 5 改造 |
| P7 | 召回页展示「预计召回率 / 回流人数」，属不可验证预测 | 高 | `solution.html` | Phase 5 改为策略建议 |
| P8 | 旧 `/appreviews` 在本机不可用，但无降级记录 | 低 | — | Phase 2 加接口层封装 |
| P9 | `instance/game_churn.db` 为死表，无人读写 | 低 | — | Phase 1 决定保留或废弃 |

---

## 6. 与 `AGENTS.md` 规范的差距

| 规范 | 现状 | 差距 |
|---|---|---|
| §3.3 禁止硬编码单一 AppID | `steam_collector.py` 默认 `1203220`；计划任务写死 | 需迁移到配置 |
| §4.3 禁止静默回退 mock | 首页、分析页均静默回退 | **违规** |
| §8.1 前端不得直接请求 Steam | 前端直连 `raw.githubusercontent.com`（非 Steam，可接受） | 待评估是否统一走后端 |
| §8.2 组件处理 loading/empty/error/stale | 仅有「加载中」与「暂不可用」两态 | 缺 stale / error |
| §6 数据库分层 | 无 `games` 表，无 `collection_logs` / `cursors` | Phase 1/2 补 |

---

## 7. 审计结论

1. **真实数据只覆盖 3 个板块**（首页 2 + 分析页 1），其余 25 个板块为模拟，且大多数**没有任何来源标注**。
2. **评论与情感这条主线目前是断的**：技术上可行（新接口实测通过），但采集器、存储表、分析脚本、前端展示全都没有。
3. **后端分析脚本是空壳**，其输出产物从未被生成过——这批代码对当前 Demo 无贡献。
4. 数据库历史数据完整，已备份，未发现丢失风险。

下一步见 `DATA_MIGRATION_PLAN.md`。
