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
| 2 评论采集 | Collector + cursor + upsert + 日志 | ✅ 完成：采集器 + 两处降级 + 无状态发布 + 19 项离线测试 | — |
| 3 情感与热点 | 模型分类 + 主题 + 可追溯 | ✅ 情感（CV F1 0.839）+ 主题候选（覆盖率随结果发布）+ 可追溯 id | 语义归类待样本量提升 |
| 4 风险与策略 | 分量公式 + 每日快照 + 建议状态 | ◐ 风险指数已上线（重归一化 + 90 天历史）；LLM 建议待定 | 需 API Key 决策 |
| 5 前端接 API | 全部走 API，无静默 mock | ◐ 8 个板块已接真实数据并去静默回退；演示板块已加徽标 | 剩余面板待改造 |
| 6 联调测试 | 测试 + 文档 + Demo | 冒烟 86 项 + 后端 65 项（已接入 PR 门禁） | 采集器真网络隔离、API contract 扩展 |

结论：**Phase 1–4 完成（LLM 建议待定），Phase 5 完成第一批（核心 8 个板块），Phase 6 未做**。原估算 9–15 个工作日对本仓库偏乐观，主要成本在 Phase 2/3 的模型选型与持久化方案。

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

### Phase 2：评论采集（2–3 天）✅ 已完成（2026-11-16）

| 动作 | 文件 | 结果 |
|---|---|---|
| 新增 Collector | `backend/collectors/steam_reviews_collector.py` | 封装 `IUserReviewsService/GetAppReviews/v1`，失败降级到旧 `/appreviews`；页内按 id 去重；游标不前进即停 |
| 建表 | `backend/data/schema.sql` | `steam_reviews`（主键 `appid+review_id`，重复采集幂等）+ `collection_cursors` |
| 聚合与发布 | `backend/collectors/reviews_publish.py` | 无状态：读 data 分支 → 合并 → 写回 `reviews-daily.json` / `reviews-samples.jsonl` / `collection-status.json` / `reviews-state.json` |
| 新工作流 | `.github/workflows/collect-reviews.yml` | `cron 20 */2 * * *`，`concurrency: collect-reviews`（与 CCU 任务分开，避免互相排队） |
| 测试 | `tests/test_phase2.py` | 19 项全离线：解析容错、游标终止、降级、聚合、去重上限、发布链路端到端 |

#### 实测推翻了三条文档假设

| 文档/直觉 | 实测 | 后果 |
|---|---|---|
| `num_per_page` 可控制每页条数 | 只在 `filter=all` 时生效；**`filter=recent` 固定 5 条**（带 `day_range` 时更少） | 增量靠「多翻几页」，不能指望一次拿 100 |
| 游标会一直向前 | 没有新数据时服务端**原样返回你发过去的游标**且 reviews 为空 | 不判「游标没前进」就是死循环；已加双重终止条件 + 页内去重 |
| `language=` 可服务端过滤 | 传 `schinese` 仍返回 english 评论 | 语言不做服务端过滤，落库后按 `review.language` 聚合 |

另外两条工程结论：

1. **`filter=recent` 的排序不严格按创建时间**（Path of Exile 一次翻页的跨度从 2017 到 2026）。
   因此聚合一律以 `timestamp_created` 分日，不依赖返回顺序。
2. **网络抖动是逐连接的**：同一时刻 CCU 端点超时、评论端点 200。
   所以不要「换个端点绕」，而是指数退避重试（1.5s/3s/6s），4 次仍失败才降级旧接口。

#### 与计划的两处偏离

1. **样本文件用 `reviews-samples.jsonl`，不是 `.jsonl.gz`。**
   gz 是二进制，每次重写都会在 Git 里产生一个全新 blob（无法 delta），一天 12 次 = 12 份完整副本；
   文本 JSONL 能被正常 delta 压缩，配合 3000 行上限，仓库增长反而更可控。
2. **没有落 `filter=updated` 这条流。**
   追评识别已用 `updated_ts` 字段（存了就不用再采一条流）；等 Phase 3 做「情感重算」时再按需开启，
   现在开只会每天多写一遍相同的 id。

#### 去重的取舍

`reviews-state.json` 每款游戏记 3000 个已计数 `review_id`（FIFO 淘汰）。
超出容量后被淘汰的旧 id 若再次出现会计重一次 —— 这是容量与精确度的折中：
按实测的评论量级（每款每两小时几十条），3000 条覆盖约 10 天，重采同一批的概率极低。

验收对照：

- **重复采集不重复入库** → `merge_seen` + `reviews-state.json`，`test_seen_set_dedupes` / `test_seen_set_is_fifo_capped` 证明；
- **中断后能续采** → 状态在 data 分支，runner 无状态也能接着来；`test_main_writes_four_files_with_expected_shape` 验证四个文件齐全；
- **追评覆盖** → `steam_reviews` 用 `ON CONFLICT DO UPDATE` 覆盖 `updated_ts`（本地库路径，Phase 3 用）。

### Phase 3：情感与热点（2–3 天）✅ 已完成（2026-11-16）

#### 已落地：句向量 + 分类头

| 部件 | 文件 | 说明 |
|---|---|---|
| 句向量 | `backend/ml/embedder.py` | `paraphrase-multilingual-MiniLM-L12-v2`，384 维，L2 归一化；延迟导入，没装 torch 时给可执行提示而不是 ImportError 栈 |
| 分类头 | `backend/ml/head.py` | 逻辑回归（385 个参数），输入 384 维向量，输出 P(推荐)；含中性带与置信度 |
| 训练 | `backend/ml/train_head.py` | 读本地 `steam_reviews` → 向量（带 .npy 缓存）→ 5 折 CV → 保存 joblib + 指标 |
| 推理与聚合 | `backend/collectors/sentiment_publish.py` | 读 data 分支样本 → 打分 → 按游戏×日期聚合 → 写 `sentiment-daily.json` |
| 调度 | `.github/workflows/analyze-sentiment.yml` | 每天 02:40 UTC，缓存 HF 模型，CPU 推理 |

#### 真实训练结果（不是估计值）

```
样本：1847 条（去重后）→ 下采样 1232 条（推荐 739 / 不推荐 493）
5 折交叉验证 macro-F1 = 0.839    准确率 0.843
混淆矩阵（行=真实，列=预测）：[[428, 65], [129, 610]]
推荐类 precision 0.904 / recall 0.825；不推荐类 precision 0.768 / recall 0.868
```

模型文件 `sentiment-head-v1.joblib` 只有 **3.9 KB**，进仓库；
470MB 的句向量模型不进仓库，由 sentence-transformers 按名字下载（Actions 侧有缓存）。

#### 五条必须写进页面的口径

1. **标签是弱标签**：`voted_up` 混入玩法、价格、优化、外挂等非情感因素，还受差评轰炸影响。
   模型输出是「推荐倾向」，不是「情绪极性」。F1 0.839 衡量的是「能否复现推荐/不推荐划分」。
2. **中性带不做硬切**：P ∈ [0.4, 0.6] 记为 `neu`。弱标签模型在中间区域本就不可信，硬分只会造出假精度。
3. **模型判定与真实投票率并列输出**：`pos_rate`（模型）与 `voted_up_rate`（样本实际投票）
   同时给出，偏差一眼可见 —— 这是最便宜的自检。
4. **每个比例都带 `n`**：`sentiment-daily.json` 的每层聚合都有 `n`，页面不得只显示百分比。
5. **样本是英文**：实测 2032 条全部 `language=english`（接口忽略 `language=` 参数）。
   多语言能力**尚未被验证**，中文玩家场景需要先补中文样本再谈。

#### 主题与热点（同日完成）

| 部件 | 文件 | 说明 |
|---|---|---|
| 词典与匹配 | `backend/analysis/topics.py` | 10 个主题、中英双语关键词；英文用 `\b词\w*\b`，中文用子串 |
| 聚合发布 | `backend/collectors/topics_publish.py` | 按 游戏 × 日期 × 主题；写 `topics-daily.json` |
| 调度 | 同上工作流，主题步骤排在模型安装**之前** | 纯规则、不依赖 torch，模型挂了它照跑 |

**为什么是规则而不是聚类或 LLM**：聚类产出的簇无法命名、无法解释；逐条调 LLM 成本高且不可复现；
关键词命中虽粗，但每条计数都能回指到 `review_id` 与命中的词，可审计。

真实样本上的表现（902 条）：

| 游戏 | 有文本评论 | 命中主题 | 覆盖率 | Top 主题 |
|---|---:|---:|---:|---|
| Cyberpunk 2077 | 300 | 194 | 0.647 | 剧情与角色 31.7% / 内容与更新 24.7% / 性能 19.7% |
| Path of Exile | 300 | 159 | 0.530 | 内容与更新 20.3% / 剧情 17.0% / 重复与肝度 8.7% |
| PUBG | 289 | 75 | 0.260 | 外挂与作弊 13.8% / 性能 5.5% / 内容 4.8% |
| 其余三款 | 13 | 3 | — | 样本过少，`low_sample` 全为 true |

PUBG 覆盖率只有 26%，而 Cyberpunk 是 65% —— 这不是 bug，是词典覆盖差异：
PUBG 评论大量是短句与缩写（"gg"、"p2w lol"），词典命中率天然低。**覆盖率必须随结果一起发布**，
否则「某主题占 13.8%」会被误读成「13.8% 的玩家在骂外挂」。

**趋势口径**：近 7 个**自然日** vs 前 7 个自然日，分母固定为 7。
不按「最近 7 个有数据的日期」取窗口——PUBG/PoE 的数据不连续，那样会把几个月前的评论算成「最近」。
基期为 0 返回 `base_zero` 而不是 `inf`；两个窗口都没有评论返回 `insufficient`。

**样本偏差**（写进 `method.limitations`）：样本来自接口的 `recent` 流，偏向近期评论，
不是全体评论的随机样本；趋势反映的是「我们采到的样本」，不是全体玩家。

#### 与计划的三处偏离

1. **没有用「公开 Steam 评论数据集」**。Kaggle 上那份需要账号授权、版本说不清，
   CI 无法复现。改用自己采集的真实评论 + 明确的弱标签定义 —— 可复现、可追溯、可反驳。
   代价是样本量小（1232 条），F1 上限受限，这一点写在指标里而不是藏起来。
2. **训练放本地、推理放 CI**。CI 里没有原始评论库（评论只落地本机 SQLite），
   所以训练必须有个能读库的环境；训练产物只有 3.9KB，提交进仓库让 CI 与本地用同一份权重。
3. **主题只做到「规则候选」，没做语义归类**。计划里写的是「规则候选 + 语义归类」，
   实际只实现前半段：语义归类需要一个可解释的映射方案，而当前样本量（902 条）
   不足以验证归类质量。输出字段名因此统一为 candidate，并要求页面同样标注。

验收对照（情感部分）：

- **结果带模型版本 / 置信度 / 语言 / 样本量** → `sentiment-daily.json` 的 `model` 与每层 `n`、`low_conf_share`；
- **未知语言不强行归类** → 语言字段原样记录，不做映射猜测；
- **热点可追溯** → 每日聚合保留 pos/neg 各 2 条 `review_id`（最小实现，主题阶段扩展）。

### Phase 4：风险与策略（1–2 天）◐ 风险指数已完成（2026-11-17），LLM 策略建议未做

#### 已落地：游戏级公开信号风险指数

| 部件 | 文件 | 说明 |
|---|---|---|
| 公式与分量 | `backend/analysis/risk.py` | `risk = 0.35N + 0.30G + 0.20C + 0.15A`，缺失按剩余权重重归一化 |
| 发布 | `backend/collectors/risk_publish.py` | 读 data 分支四个文件 → 写 `risk-daily.json`（含 90 天历史） |
| 调度 | 情感工作流第三步 | 纯 JSON 计算零依赖；排在情感之后（它消费情感与主题的产物） |

分量与来源（不引入任何新数据源）：

| 分量 | 权重 | 定义 | 来源 | 当前状态 |
|---|---|---|---|---|
| N 负面评论比例 | 0.35 | 模型 neg 带占比 × 100，分母 = 有文本评论数 | sentiment-daily | 可用（<30 条标 low_sample）|
| G 主题集中度 | 0.30 | 最大主题占有文本评论的比例 | topics-daily | 可用 |
| C 在线人数变化 | 0.20 | 近 24h 均值 vs 前 24h 均值，`clamp(-降幅%,0,50)×2` | steam-ccu | 仅永劫可用（其余采样不足 3 点/侧）|
| A 成就推进衰减 | 0.15 | 需要完成率时间序列 | steam-funnels | **不可用**（只有单点快照）|

三条防误导设计：

1. **缺失不当 0**。A 对所有游戏不可用、C 只对永劫可用，全部按剩余权重重归一化；
   若当 0，没数据的游戏会显得「最健康」。
2. **排序先看数据充分性**。`games` 数组按 ok → low_sample → insufficient、
   组内按风险值排。否则 2 条样本的 Warframe（50.0）会压过 300 条样本的
   Cyberpunk（26.6）排到第一——小样本噪声被排成「最高风险」。
3. **历史同日覆盖**。手动 dispatch 不会在同一天追加重复点。

首日真实结果（dry-run 于本地，输入为 data 分支当日文件）：

```
Cyberpunk 2077  26.6 [ok]        N:21.1 G:32.9
PUBG            24.1 [ok]        N:32.9 G:13.8
Path of Exile   16.9 [ok]        N:14.0 G:20.3
永劫无间         15.3 [low_sample] N:20.0 G:20.0 C:0
Apex Legends    34.6 [low_sample] N:50.0 G:16.7     ← 6 条样本，不可引用
Warframe        50.0 [low_sample] N:50.0            ← 2 条样本，不可引用
```

#### 未做：LLM 策略建议

计划里的「策略建议走 LLM，输入必须是结构化证据」**没有实现**，原因：

1. 需要一个 LLM API Key 进 Secrets，以及选择模型/控制成本——这是使用方决策，不该由 Agent 代定；
2. 现有前端召回页展示的是「预计召回率」这类不可验证预测（审计 P7），在 Phase 5 改造成
   「AI 策略建议」时才需要这个接口。

数据侧已为它准备好输入：`risk-daily.json` 每个分量都带 status / 来源 / 样本量，
LLM 的输入将直接引用这些字段（结构化证据），而不是让它读原始评论自由发挥。

### Phase 5：前端接真实数据（2–3 天）◐ 第二批完成（2026-11-17）

| 动作 | 状态 | 说明 |
|---|---|---|
| 统一数据层 `js/realdata.js` | ✅ | 远程 → 快照 → `unavailable`，**没有第三级演示回退**；带四态与来源条 |
| 移除 `loadRealCcu` 静默回退（审计 P1） | ✅ | 取不到就画布中央写「未取到真实在线数据」，不再画演示曲线 |
| 总览 KPI 四项换真实 | ✅ | 合计在线（CCU）/ 负面占比（情感）/ 正面倾向（情感）/ 风险指数（risk-daily）|
| 情感趋势图（分析页） | ✅ | sentiment-daily，近 90 天、每日 ≥5 条才画，三线（正面/中性/负面）|
| 主题热度（分析页） | ✅ | topics-daily，4 行主题占比 + 覆盖率 + `low_sample` 标注；替换原「热点与流失关联」的编造相关系数 |
| 成就漏斗 | ✅（早前已完成）| steam-funnels 真实数据 |
| 召回页「预计召回率 / 回流玩家」 | ✅ 改为「未接入」 | 需触达日志，本项目无权限；页面顶部加数据边界说明 |
| 留存页 Cohort 指标 | ✅ 已换成可得的公开信号 | 评论总量 / 推荐率 / 覆盖天数 / 覆盖游戏 + 每日评论量趋势 + **评论者游戏时长分布**（`playtime_at_review` 分桶）；D1/D7/D30 明确标注不可得 |
| 分群画像 / 地球 / 区域排行 / 预警 | ◐ 标 `演示` 徽标 | 个体级数据拿不到；地球的「实时」字样已删（改为「演示」），区域排行与 AI 预警标题加 `演示` 徽标 |

#### 三个实现约束（踩过才知道）

1. **跨 `<script>` 作用域**：`index.html` 有三段脚本，`safeRender` 在第一段、`renderRealKpis` 在第三段。
   直接把函数写在第三段，第一段的异步回调调用时会 `ReferenceError`。
   修法是显式挂 `window.__renderRealKpis` / `window.__updateKpi`，而不是靠函数提升。
   （仓库历史上 `stage.setRiskPillars` 崩掉，是同一类问题。）
2. **占比聚合按评论数加权**：`Σ分子/Σ分母`，不是各游戏占比求平均。后者会让 2 条样本的游戏
   和 300 条样本的游戏等权。
3. **情感趋势只画近 90 天**：样本里混着 2017 年的老评论（`filter=recent` 排序不严格按时间），
   全画会把九年压成一条横线。

#### 这一批补上的（第二批）

- **留存页真实化**：D1/D7/D30 不可得，改为三项公开信号——评论总量 953、推荐率 77.0%、
  覆盖 109 天 / 6 款；趋势图是「每日评论量（柱）+ 当日推荐率（线，n≥5 才画）」；
  Cohort 表换成**评论者游戏时长分布**（按游戏，4 档）。
  意外发现：全部评论里 75% 来自「50h+」玩家——这正是「评论者样本 ≠ 全体玩家随机样本」的证据。
- **首页地球去伪装**：标题「全球玩家脉搏 · 实时」里的「实时」二字删掉（地理分布与迁移弧线
  本项目没有数据源），区域排行与 AI 预警加 `演示` 徽标。

#### 仍未解决

- 首页地球、区域排行、预警列表仍占大面积视觉，但内容无数据来源。彻底方案只有两个：
  删掉，或换成「按游戏的评论量/在线排名」——后者更诚实，但要重排整个舞台布局，风险不小。
- 分群留存、流失因素归因、AI 策略建议三块仍是演示（已标徽标）。

### Phase 6：联调（1–2 天）

- 采集器单测（fixture 假响应，不打真实网络）。
- API contract test。
- 多 AppID 隔离测试、cursor 恢复测试、429/5xx 测试。
- 冒烟测试扩展到真实数据路径（当前 84 项只覆盖静态渲染）。

---

### Phase 6.1：AI 模型接入 ◐ 代码完成（2026-11-17），真实模型待安装

| 交付 | 文件 | 状态 |
|---|---|---|
| 配置 | `backend/ai/config.py` | ✅ 环境变量 + 校验，密钥零落库 |
| Provider 抽象 | `backend/ai/llm/base.py` | ✅ 错误码与方案 §11.7 对齐 |
| Ollama | `backend/ai/llm/ollama_provider.py` | ✅ `/api/chat`、`format=json`、超时/404/空文本分流 |
| SiliconFlow | `backend/ai/llm/siliconflow_provider.py` | ✅ OpenAI 兼容，密钥仅环境变量 |
| Mock | `backend/ai/llm/mock_provider.py` | ✅ 仅测试用，附完整策略 JSON |
| 工厂 | `backend/ai/llm/provider_factory.py` | ✅ 未知 provider 直接报错 |
| 路由 | `backend/routes/ai_routes.py` | ✅ `/api/ai/health`、`/api/ai/llm/ping` |
| 测试 | `tests/test_phase6.py` | ✅ 21 项，**用本地桩 HTTP 服务**验证失败路径 |

#### 一个刻意的设计：Provider 不校验 JSON

`generate()` 只返回文本，剥掉 json 代码围栏就结束；schema 校验留给策略校验器。
否则「模型返回了非 JSON」会被误报成「模型不可用」，两类错误混在一起就没法定位。
`/api/ai/llm/ping` 会返回 `json_valid` 字段，把这件事显式暴露出来。

#### 未完成的验收项（如实记录）

方案 §17 的验收要求「能通过 Flask 调用本地 Qwen3」。当前机器**未安装 Ollama**，
`127.0.0.1:11434` 不可达，因此这一条**未验证**。已完成的是：

- Flask → Provider → HTTP 链路：用本地桩服务跑通（含正常、404、500、超时、空文本、围栏 JSON）
- 健康检查在 `AI_ENABLED=false` 与「服务不可达」两种情况下都如实报告
- 一旦 `ollama pull qwen3:4b` 完成，只需设 `AI_ENABLED=true`，无需改代码

#### 安装步骤（待执行）

```text
winget install Ollama.Ollama      # 或官网安装包
ollama pull qwen3:4b              # 约 2.5GB
ollama pull bge-m3                # Phase 6.2 需要
```

装完后在 `.env` 设 `AI_ENABLED=true`，`POST /api/ai/llm/ping` 应返回 `json_valid: true`。

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
