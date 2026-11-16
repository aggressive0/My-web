# 数据迁移实施计划（Phase 0 交付）

- 版本：1.0（2026-11-16）
- 依据：`docs/REAL_DATA_INTEGRATION_PLAN.md` 第 9 节 + `docs/DATA_SOURCE_AUDIT.md` 审计结论
- 前置约束：`AGENTS.md` §13——未经授权不切换分支、不推送、不合并、不发布

---

## 1. 现状与目标的差距（按 Phase）

| Phase | 目标 | 现状 | 差距 |
|---|---|---|---|
| 0 审计 | 三份文档 + 备份 | ✅ 完成 | — |
| 1 多游戏 CCU | `games` 配置 + 表 + 列表 API + 前端选择器 | ✅ 完成：6 款配置化、schema 落库、API 上线、切换器由配置生成、23 项离线测试 | — |
| 2 评论采集 | Collector + cursor + upsert + 日志 | **完全空白** | 全新开发（接口已验证可用） |
| 3 情感与热点 | 模型分类 + 主题 + 可追溯 | 仅关键词表打分，且无输入数据 | 重写分析层 |
| 4 风险与策略 | 分量公式 + 每日快照 + 建议状态 | 无 | 全新开发 |
| 5 前端接 API | 全部走 API，无静默 mock | 3 个板块真实，25 个 mock | 逐页改造 |
| 6 联调测试 | 测试 + 文档 + Demo | 冒烟 86 项（仅前端静态）+ Phase 1 后端 23 项 | 采集器单测、API contract 扩展 |

结论：**Phase 1 已完成，Phase 2 之后仍为零**。原估算 9–15 个工作日对本仓库偏乐观，主要成本在 Phase 2/3 的模型选型与持久化方案。

Phase 1 额外收益：`collection_logs` 这张表让「哪款游戏何时挂了、挂多久」第一次变成可查询的事实，
而不是靠翻控制台输出猜测——Phase 2 的采集健康度看板直接复用它。

---

## 2. 关键决策：数据持久化

这是整个方案的地基，必须先定。

### 2.1 三个候选

| 方案 | 容量 | 优点 | 致命问题 |
|---|---|---|---|
| A. Git `data` 分支存 SQLite | 单文件，>100MB 后 clone 变慢 | 零成本、可追溯、已有基建 | **二进制不可 diff，每次提交整份复制，历史迅速膨胀** |
| B. Actions cache / artifact | 单文件 ≤10GB | 无需仓库膨胀 | 缓存 7 天过期、跨运行不保证、**不能当数据库** |
| C. 托管数据库（Supabase / Neon 免费层） | GB 级 | 真正的并发与持久化、可 SQL 查询 | 引入外部依赖、需联网、Demo 演示时多一个故障点 |

### 2.2 建议方案（分阶段）

**Phase 2–3（评论原始数据）**：不把原始评论写入 Git。

```
每次 Actions 运行：
  SQLite（runner 内临时） ← cursor 增量拉取
        ↓ 计算聚合
  提交到 data 分支：reviews-daily.json（按日聚合：总量、推荐率、语言分布、主题分布）
        ↓ 抽样
  提交到 data 分支：reviews-sample.jsonl.gz（每游戏每日上限 500 条，保留可追溯样本）
```

- 原始全量评论只活在当次 runner 内；需要重分析时，用 `cursor` 从接口重拉（Steam 接口可回溯历史评论）。
- 聚合结果体积可控：4 款 × 30 天 ≈ 几百 KB。
- 样品用于「热点可追溯到评论」的验收要求，且有明确上限。

**Phase 4（风险快照）**：`risk-daily.json`，每游戏每日一条，体积可忽略。

**当评论量级超过约 10 万条需要长期保留时**：迁移到 C 方案（托管 Postgres），保留 `data` 分支作为降级只读快照。

### 2.3 为什么不选 A

一份含 5 万条评论的 SQLite 约 30–60MB。Git 每次提交保存完整新副本，一周 168 次运行 → 仓库体积轻松破 GB。这在 Demo 阶段是不可接受的操作成本。

---

## 3. 分阶段改造清单（文件级）

### Phase 1：多游戏配置与 CCU（0.5–1 天）✅ 已完成（2026-11-16）

| 动作 | 文件 | 结果 |
|---|---|---|
| 新增配置 | `backend/config/games.yaml` | 6 款游戏 + `defaults`（间隔/窗口/成就快照周期） |
| 新增加载器 | `backend/collectors/game_config.py` | 零依赖读取 + 字段校验（appid/slug 唯一、颜色格式、未知字段拒绝） |
| 改造 | `backend/collectors/ci_publish.py` | 删除 `GAMES` 常量，全部读配置；注册表内嵌进 `steam-ccu.json.games[]` |
| 改造 | `backend/collectors/steam_collector.py` | `--appids` 留空即读配置；间隔与成就周期取 `defaults`；每次请求写 `collection_logs` |
| 建表 | `backend/data/schema.sql` + `backend/collectors/db.py` | `games`、`ccu`、`achievements`、`collection_logs`、`collection_cursors`；`init_db()` 幂等 |
| 新增 API | `backend/app.py` | `GET /api/games`、`GET /api/games/{appid}`，统一 `meta`，`GAME_NOT_FOUND` / `INVALID_APPID` 可预期 |
| 前端 | `index.html` + `dashboard-data.js` | 切换器由 `payload.games[]` 生成（顺序/名称/颜色来自配置），无数据游戏标「暂无数据」并禁用 |
| 测试 | `tests/test_phase1.py` | 23 项，全离线：配置校验、schema 幂等、失败隔离、API 契约 |

验收对照：

- **新增一款游戏只需改 yaml** → `test_new_game_needs_only_yaml` 证明；
- **一款失败不影响其余** → `test_one_failure_does_not_break_others` / `test_failed_fetch_keeps_existing_points` 证明；
- **历史 CCU 不丢** → `test_init_is_idempotent_and_keeps_history` / `test_sync_games_upserts_without_touching_ccu` /
  `test_disabled_game_keeps_history_and_is_not_fetched` 证明，且停用游戏不再打接口但保留序列。

#### 与计划的四处偏离（都有明确理由）

1. **`games.yaml` 用自研子集解析器，未引入 PyYAML。**
   采集链路刻意保持零第三方依赖：本机计划任务与 GitHub Actions 都不装包，
   为一个扁平列表引入依赖，会同时增加本地装包与 CI 失败两种风险。
   解析器只支持本项目用到的结构，遇到嵌套会直接报「请改用 PyYAML」而不是静默误读。

2. **没有单独产出 `games.json`。**
   原本设想前端读一个独立注册表文件，但它会多一次请求，且文件尚未推送到 `data` 分支时
   会产生 404 —— 冒烟测试的「无 console.error」断言会因此变红（实测已发生）。
   改为把注册表内嵌进 `steam-ccu.json.games[]`：一次请求拿到数据 + 元信息，无新增失败面。

3. **前端图表提前改为时间轴并关闭平滑（原属 Phase 5）。**
   接入 6 款游戏后，类目轴把「17:37 → 19:33」画成与「16:22 → 16:26」等宽，
   等于谎报事件间隔；平滑曲线又会跨越停机缺口凭空造出趋势。
   这两处属于「数据形状变了才暴露」的问题，晚改只会让更多人看到错误图形。

4. **`instance/game_churn.db` 保留不动（审计 P9）。**
   库里三张表无人读写，但删除要动 SQLAlchemy 模型与 `create_all()`，
   与本阶段目标无关；随 Phase 5 前端改造一并废弃更安全。

### Phase 2：评论采集（2–3 天）

| 动作 | 文件 | 说明 |
|---|---|---|
| 新增 Collector | `backend/collectors/steam_reviews_collector.py` | 封装 `IUserReviewsService/GetAppReviews/v1`；保留旧 `/appreviews` 作为降级 |
| 运行时临时库 | SQLite 表 `steam_reviews` + `collection_cursors` | 按方案 §4.3 建表 |
| 聚合导出 | `backend/collectors/reviews_publish.py` | 写 `reviews-daily.json` + `reviews-sample.jsonl.gz` 到 `data` 分支 |
| 新工作流 | `.github/workflows/collect-reviews.yml` | `cron 20 */2 * * *`，独立 concurrency group |
| 采集日志 | `data` 分支 `collection-status.json` | 每游戏最后一次成功时间、条数、错误码 |

关键实现点（实测得出）：

- 接口**无需 API Key**。
- `cursor` 必须持久化；`filter=recent`（增量）与 `filter=updated`（追评）两条流。
- 网络不稳定：本机实测同一接口出现过 HTTP 000 / 302，必须重试 + 降级。
- 每轮设上限（建议 ≤ 200 条/游戏），避免 Actions 超时。

验收：重复采集不重复入库；中断后能续采；追评能覆盖并触发重分析。

### Phase 3：情感与热点（2–3 天）

| 动作 | 说明 |
|---|---|
| 模型选型 | 中英双语优先。候选：本地 `distilbert-base-multilingual-cased` 微调 / 现成中文情感模型 / LLM 批量分类（成本高，不推荐逐条调用） |
| 输出 | `sentiment` / `score` / `confidence` / `model_name` / `model_version` |
| 未知语言 | 标记 `unknown`，不得强行归类 |
| 主题 | 规则候选 + 语义归类，输出必须带评论 ID 关联 |
| 分母 | 所有百分比必须带 `sample_size` |

替换点：首页「情感指数」、分析页「热点强度」、`analyze_sentiment.py`（关键词表版本废弃）。

### Phase 4：风险与策略（1–2 天）

- 实现 `risk = 0.35N + 0.30G + 0.20C + 0.15A`，缺失分量重归一化。
- 每日快照写入 `risk-daily.json`。
- 策略建议走 LLM，但**输入必须是结构化证据**，输出校验 schema，状态固定为 `suggested`。

### Phase 5：前端接真实数据（2–3 天）

按页面逐个推进，优先级 = 真实数据可得性：

1. **留存追踪**（风险最高，指标全部不可得）→ 改为「公开群体信号」：在线趋势、评论者时长分布、成就完成率变化。
2. **召回策略** → 改为「AI 策略建议」，删除所有召回率/回流人数。
3. **流失分析** → KPI 与趋势改为评论情感 + 在线趋势；保留已上线的成就漏斗。
4. **总览** → KPI 全部换为 CCU / 评论量 / 推荐率 / 风险指数；删除高危玩家、召回率。

同时必须完成：

- 移除 `loadRealCcu` 的静默回退（§4.3 违规项 P1）。
- 每个图表加来源徽标与「更新于」时间戳。
- 补齐 loading / empty / error / stale 四态。

### Phase 6：联调（1–2 天）

- 采集器单测（fixture 假响应，不打真实网络）。
- API contract test。
- 多 AppID 隔离测试、cursor 恢复测试、429/5xx 测试。
- 冒烟测试扩展到真实数据路径（当前 84 项只覆盖静态渲染）。

---

## 4. 风险与回滚

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| Steam 接口变更或限流 | 中 | 采集中断 | Collector 层封装 + `collection-status.json` 暴露；失败不覆盖上次有效数据 |
| `data` 分支体积失控 | 中 | clone 变慢 | 只提交聚合与抽样，原始不落 Git |
| Actions 并发覆盖 | 低 | 数据错乱 | 每任务独立 concurrency group；写入前比 sha |
| 模型分类质量不足 | 高 | 情感数据不可信 | 先小样本人工抽检，置信度低归 unknown |
| 前端改造引入回退 | 中 | 页面空 | 冒烟 + 四态覆盖 |

回滚点：每个 Phase 独立提交；`data` 分支可回退到任一历史提交（只影响数据，不影响代码）。

---

## 5. 验收清单（对齐方案第 10 节）

- [ ] 至少 5 款游戏可独立采集，数据按 AppID 隔离
- [ ] CCU 有真实快照、时间戳、来源
- [ ] 评论支持增量、分页、断点、去重、追评
- [ ] 成就不可用时单游戏标 `unavailable`，不阻断整体
- [ ] 情感结果带模型版本、置信度、语言、样本量
- [ ] 热点可追溯至评论 ID
- [ ] 风险指数公式透明，缺失值不当 0
- [ ] 召回仅「建议」，状态明确未执行
- [ ] API 统一 meta，参数校验，错误可预期
- [ ] 前端无静默 mock，四态齐全
- [ ] Actions 结束后历史数据可恢复
- [ ] 无密钥泄漏，不滥用 SteamID

---

## 6. 建议执行顺序

```
Phase 1（半天）→ Phase 2（2–3 天）→ Phase 3（2–3 天）→ Phase 4（1–2 天）→ Phase 5（2–3 天）→ Phase 6（1–2 天）
```

**不要跳步**。特别是不要先做 Phase 5——把页面改成调用还不存在的 API，只会得到十个空面板。

短期可先做一件低成本高收益的事：**给首页与分析页的 mock 指标打上 `mock` 徽标**（约 1 小时），让当前状态诚实可交付。这不需要等后端 —— 仍未做，建议与 Phase 2 并行。
