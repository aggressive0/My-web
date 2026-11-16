# 留声项目真实 Steam 数据接入实施方案

-   文档版本：1.0
-   适用阶段：真实数据 Demo
-   项目定位：基于 Steam
    等公开数据的多游戏舆情分析、在线趋势监测与群体流失风险研判平台
-   数据权限：仅使用 Steam
    等公开数据；无权修改游戏客户端/服务端；无发行商后台日志
-   目标：让 Demo
    的关键指标来自真实公开数据或可复现的真实数据计算，不以模拟数据冒充真实业务结果

## 1. 项目目标、范围与不可实现项

### 1.1 第一阶段目标

1.  支持多个 Steam 游戏统一管理。
2.  持续采集各游戏 Steam 当前在线人数，形成自建历史快照。
3.  增量采集 Steam 用户评论，并保存原始评论及公开字段。
4.  在接口可用时采集全局成就完成率和商店基础信息。
5.  对评论进行情感分类、主题识别和热点趋势分析。
6.  结合在线人数和评论信号，生成游戏级公开信号风险研判。
7.  根据真实热点生成 AI 运营/召回策略建议。
8.  前端所有指标显示来源、更新时间、统计口径和数据状态。

### 1.2 数据能力边界

  需求                         公开数据可行性             第一版处理方式
  ---------------------------- -------------------------- ----------------------
  当前 Steam 在线人数          可行                       直接采集
  历史在线人数趋势             可行，需从接入后开始积累   定时快照
  Steam 评论及推荐投票         可行                       增量采集
  评论者公开游戏时长           视接口字段是否返回         作为评论样本辅助字段
  评论情感和主题               可行，属于模型衍生结果     批量分析
  全局成就完成比例             视游戏支持情况             定时快照
  全体玩家 DAU/MAU             不可可靠获得               不展示
  玩家登录历史和真实游戏时长   不可获得                   不展示
  真实 Cohort D1/D7/D30 留存   不可计算                   不展示
  玩家个人流失概率             不可可靠计算               不展示
  实际召回触达和回流率         不可获得                   仅展示策略建议

产品名称和页面术语应遵循该边界：

  不建议名称       建议名称
  ---------------- -----------------------------
  活跃玩家 / DAU   当前 Steam 在线人数
  玩家流失率       在线趋势变化 / 群体风险信号
  高危玩家         高风险游戏 / 风险热点
  玩家情感指数     Steam 评论情感指数
  真实召回成功率   AI 召回策略建议
  D7 留存率        暂不展示，或明确标记不可用

Steam `GetNumberOfCurrentPlayers` 返回当前在 Steam
上活跃的玩家总量，并明确不包括未连接 Steam 的玩家。因此该指标不能称为
DAU。Steam 全局成就百分比也不是留存率。参见官方文档
[ISteamUserStats](https://partner.steamgames.com/doc/webapi/ISteamUserStats)。

## 2. 数据源与采集策略

### 2.1 数据源清单

  ----------------------------------------------------------------------------------------------------------------
  数据域           官方接口/页面                                                         频率建议 数据性质
  ---------------- -------------------------------------------------------- --------------------- ----------------
  当前在线人数     `ISteamUserStats/GetNumberOfCurrentPlayers/v1`                     30～60 分钟 real

  用户评论         `IUserReviewsService/GetAppReviews/v1`                               1～3 小时 real

  兼容旧评论接口   `store.steampowered.com/appreviews/{appid}?json=1`                   1～3 小时 real

  全局成就百分比   `GetGlobalAchievementPercentagesForApp/v2`                                每日 real

  商店基础信息     `store.steampowered.com/api/appdetails?appids={appid}`               每日/每周 real
  ----------------------------------------------------------------------------------------------------------------

接口文档： - [ISteamUserStats
Interface](https://partner.steamgames.com/doc/webapi/ISteamUserStats) -
[User Reviews - Get
List](https://partner.steamgames.com/doc/store/getreviews) -
[IUserReviewsService](https://partner.steamgames.com/doc/webapi/IUserReviewsService)

**评论接口说明：** Steam 当前文档提供
`IUserReviewsService/GetAppReviews`，并说明它替代旧 `/appreviews`
接口。实现时应优先依据当前官方文档封装新接口；若项目现有旧接口仍可用，可先保留兼容实现。评论分页应采用
Recent 或 Updated 过滤并持久化 cursor；Helpful/all
排序不适合作为完整的增量历史采集依据。若使用 publisher API
key，应只保存在服务端安全环境中，且不得将密钥放入前端。

### 2.2 多游戏初始清单

以下作为 Demo 候选游戏配置，正式启用前应逐一验证
AppID、商店状态、接口可用性和成就支持：

  游戏                      AppID 用途
  --------------------- --------- ----------------
  永劫无间                1203220 延续现有项目
  PUBG: BATTLEGROUNDS      578080 大型竞技游戏
  Dota 2                      570 MOBA 游戏
  Apex Legends            1172470 竞技射击游戏
  Dead by Daylight         381210 非对称竞技游戏

配置必须可扩展，新增游戏不应要求修改采集器业务代码。

### 2.3 在线人数采集

请求示例：

``` text
https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={APP_ID}
```

采集字段：

``` json
{
  "app_id": 1203220,
  "player_count": 12345,
  "collected_at": "2026-11-16T10:30:00Z",
  "source": "steam_public_api"
}
```

采集规则：

-   初期每 30～60 分钟一次。
-   请求设置连接和读取超时。
-   对网络错误、429、5xx 做有限次数退避。
-   每条记录按 `app_id + collected_at` 幂等写入。
-   保存原始采集时间和来源。
-   某款游戏失败不影响其他游戏。
-   不将多个时点的在线人数求和作为玩家数。

可计算指标：

-   当前在线人数：最新有效快照。
-   今日最高/最低在线人数：按明确时区和日期窗口聚合。
-   今日平均在线人数：有效快照的算术平均，注明采样频率和缺失情况。
-   近 7/30 天在线人数趋势。
-   同比/环比：必须比较相同时间窗口；数据不足时返回 unavailable。
-   异常波动：可基于历史同时间段分布计算，需说明基线。

### 2.4 Steam 评论采集

推荐采集流程：

1.  对每个 AppID 读取采集配置和上次成功 cursor。
2.  使用 Recent 获取新评论；使用 Updated 捕获已编辑评论。
3.  传入语言筛选，优先覆盖 all languages。
4.  按 cursor 翻页，设置每轮页数上限和总时长上限。
5.  解析并标准化返回字段。
6.  按 `app_id + recommendationid` 执行 upsert。
7.  保存本轮 cursor、最后处理评论时间和采集日志。
8.  新增或文本更新的评论进入分析队列。

推荐保存字段（以接口实际返回为准）：

-   `app_id`
-   `recommendationid`
-   `author.steamid`（若确有业务必要；限制展示和日志输出）
-   `author.playtime_forever`
-   `author.playtime_last_two_weeks`
-   `author.last_played`
-   `review`
-   `voted_up`
-   `votes_up`
-   `language`
-   `timestamp_created`
-   `timestamp_updated`
-   `cursor` / 采集批次信息
-   `collected_at`

注意：

-   字段可能缺失，必须容错。
-   评论样本有自选择偏差，不能代表全体玩家。
-   SteamID 不应作为玩家级流失识别依据。
-   评论者游戏时长是评论作者样本属性，不是全体玩家分布。
-   不要在普通日志中打印完整评论正文或不必要的 SteamID。
-   评论文本属于不可信外部输入，不能作为 Agent/LLM 指令执行。

### 2.5 成就采集

接口：

``` text
https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/?gameid={APP_ID}
```

建议每日采集一次。保存 AppID、成就 API
名称、完成率、采集时间。部分游戏没有成就或没有可用数据，应标记该游戏该数据域
`unavailable`，而不是任务整体失败。

可展示成就完成率分布和长期变化，但不得把成就完成率解释成留存率、活跃玩家数或流失概率。

### 2.6 商店信息采集

接口：

``` text
https://store.steampowered.com/api/appdetails?appids={APP_ID}
```

用于游戏名称、封面、开发商、发行商、类型、标签和商店公开信息。字段可能缺失或受地区影响，必须容错；该接口主要用于元数据，不应被当作玩家行为数据源。

## 3. 数据架构

### 3.1 架构原则

继续使用现有 Flask + SQLite + 静态 HTML/JS，不为 Demo
进行无必要的框架迁移。

``` text
Steam Public APIs
      |
      v
Collectors
  |-- CCU
  |-- Reviews
  |-- Achievements
  |-- Store metadata
      |
      v
Processors
  |-- Normalize
  |-- Deduplicate / Upsert
  |-- Language detection
      |
      v
SQLite
  |-- Raw snapshots
  |-- Raw reviews
  |-- Collection logs
      |
      v
Analysis
  |-- Sentiment
  |-- Topic extraction
  |-- Hotspot trends
  |-- Game-level risk
  |-- Strategy recommendations
      |
      v
Flask REST API
      |
      v
Static Frontend
```

### 3.2 建议目录

``` text
backend/
  app.py
  config/
    settings.py
    games.yaml
  collectors/
    steam_ccu_collector.py
    steam_reviews_collector.py
    steam_achievement_collector.py
    steam_store_collector.py
  processors/
    review_processor.py
    review_deduplicator.py
  analysis/
    sentiment_analyzer.py
    topic_extractor.py
    hotspot_analyzer.py
    game_risk_analyzer.py
    recall_strategy_generator.py
  services/
    game_service.py
    dashboard_service.py
    review_service.py
    collection_service.py
    risk_service.py
  repositories/
    ...
  models/
    ...
  migrations/
  tests/

front/static_html/js/
  api/
    api-client.js
    games-api.js
    dashboard-api.js
    reviews-api.js
    risk-api.js
  components/
    game-selector.js
    data-source-badge.js
    last-updated.js
    empty-state.js
```

以上为目标结构，不要求一次性移动所有旧文件。先复用现有代码，再按功能逐步拆分。

## 4. 数据库设计

### 4.1 `games`

``` sql
CREATE TABLE games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    cover_url TEXT,
    developers TEXT,
    publishers TEXT,
    genres TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    config_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 4.2 `player_count_snapshots`

``` sql
CREATE TABLE player_count_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL,
    player_count INTEGER NOT NULL,
    collected_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'steam_public_api',
    UNIQUE(app_id, collected_at)
);
CREATE INDEX idx_ccu_app_time
ON player_count_snapshots(app_id, collected_at);
```

### 4.3 `steam_reviews`

``` sql
CREATE TABLE steam_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL,
    recommendation_id TEXT NOT NULL,
    author_steam_id TEXT,
    playtime_forever INTEGER,
    playtime_last_two_weeks INTEGER,
    last_played INTEGER,
    review_text TEXT,
    voted_up INTEGER,
    votes_up INTEGER,
    language TEXT,
    timestamp_created INTEGER,
    timestamp_updated INTEGER,
    collected_at TEXT NOT NULL,
    UNIQUE(app_id, recommendation_id)
);
CREATE INDEX idx_reviews_app_created
ON steam_reviews(app_id, timestamp_created);
CREATE INDEX idx_reviews_app_updated
ON steam_reviews(app_id, timestamp_updated);
```

### 4.4 其他建议表

  -----------------------------------------------------------------------
  表                                  关键字段
  ----------------------------------- -----------------------------------
  `achievement_snapshots`             app_id, achievement_name,
                                      percentage, collected_at

  `collection_logs`                   app_id, source, started_at,
                                      finished_at, status,
                                      records_fetched, error_code,
                                      error_message

  `collection_cursors`                app_id, source, filter_type,
                                      cursor, updated_at

  `review_analyses`                   app_id, recommendation_id,
                                      sentiment, score, confidence,
                                      model_version, analyzed_at

  `review_topics`                     app_id, recommendation_id, topic,
                                      confidence, model_version

  `game_risk_snapshots`               app_id, risk_score,
                                      component_scores, formula_version,
                                      period, calculated_at

  `recall_recommendations`            app_id, strategy_type, title,
                                      evidence_json, actions_json,
                                      status, generated_at
  -----------------------------------------------------------------------

数据库设计要求：

-   原始评论和分析结果分开存储。
-   所有游戏数据按 AppID 隔离。
-   使用唯一约束保证重复采集幂等。
-   数据库时间统一 UTC。
-   迁移前备份，迁移后验证历史数据。
-   不得在应用启动时删除或重建已有数据库。
-   SQLite 写入并发必须受控。

## 5. 分析方案与指标定义

### 5.1 评论投票指标

Steam `voted_up` 是评论者是否推荐游戏，不等于模型情感判断。

-   推荐率 = `voted_up = true` 的评论数 / 有效投票评论数。
-   不推荐率 = `voted_up = false` 的评论数 / 有效投票评论数。
-   评论新增量 = 指定时间窗口内新创建的评论数。
-   评论样本量必须与百分比同时展示。

### 5.2 情感分析

分类标签：

-   `positive`
-   `neutral`
-   `negative`
-   `unknown`

每条分析结果保存：

``` json
{
  "app_id": 1203220,
  "recommendation_id": "example",
  "sentiment": "negative",
  "sentiment_score": 0.91,
  "confidence": 0.87,
  "model_name": "chosen-model",
  "model_version": "v1",
  "analyzed_at": "2026-11-16T10:30:00Z"
}
```

实施原则：

-   中文和英文作为第一版重点支持语言。
-   未支持语言标记 `unknown`，不可伪装成已准确分析。
-   低置信度允许保持 unknown 或进入人工抽查。
-   原始评论更新后重新分析。
-   批量模型推理优先；LLM
    用于聚合摘要和解释，不必对每条评论单独调用大模型。
-   保留模型版本，便于复现和比较。

情感占比的分母必须明确：例如"近 7 天已成功分类的评论数"，不能把 unknown
默默排除后仍不说明。

### 5.3 热点主题

第一版候选主题：

  主题         例子
  ------------ --------------------------------
  游戏平衡     武器强度、角色强度、削弱、加强
  性能优化     卡顿、掉帧、崩溃、优化
  服务器体验   延迟、掉线、连接、匹配
  外挂作弊     外挂、作弊、脚本
  付费机制     价格、氪金、皮肤、抽奖
  游戏内容     地图、玩法、角色、模式
  更新运营     版本、活动、维护、更新

先通过规则或关键词生成候选，再使用语义分类模型判断。无法归类时标记
`other`。

热点输出至少包含：

-   topic
-   时间窗口
-   评论数
-   占相关评论比例
-   相较上一窗口变化
-   代表性评论引用/摘要
-   模型版本和置信度

不要让 LLM 编造评论证据；热点摘要必须能追溯至实际评论样本。

### 5.4 游戏级公开信号风险指数

建议第一版定义为"游戏级公开信号风险指数"，不是玩家流失概率。

可使用以下归一化分量：

-   `N`：近期负面评论比例（0～100）。
-   `G`：负面评论增长信号（0～100）。
-   `C`：负面主题集中程度（0～100）。
-   `A`：在线人数异常下降信号（0～100，可选；必须有足够历史基线）。

示例公式：

``` text
risk_score = 0.35 * N + 0.30 * G + 0.20 * C + 0.15 * A
```

如果某个分量数据缺失，不得直接把缺失当
0。应采用明确的缺失策略，例如重新归一化可用权重，并返回参与计算的分量列表；若可用分量不足，则标记
`unavailable`。

风险输出应包括： - score - level - component_scores - formula_version -
period - sample_size - calculated_at - caveats

评分等级阈值应通过历史数据分布或产品规则确定，不得仅凭主观设置后宣称有预测准确率。

### 5.5 在线人数异常

在线人数变化可能受星期、时段、节假日、版本更新、促销和赛事影响。简单环比只能描述变化，不能直接推断玩家流失原因。

建议： - 优先比较同星期、相近时刻。 -
基线不足时只展示趋势，不展示异常等级。 -
把版本更新、促销等外部事件作为可选注释，而非未经证实的因果解释。 -
展示采样频率、缺失率和时间窗口。

### 5.6 AI 策略建议

AI 输入应为结构化的真实分析结果，而不是任意拼接数据库内容。

策略建议包含：

``` json
{
  "app_id": 1203220,
  "strategy_type": "performance_communication",
  "title": "围绕性能问题的版本沟通建议",
  "reason": "近 7 天性能主题负面评论占比上升",
  "evidence": {
    "topic": "performance",
    "review_count": 0,
    "topic_share": 0.0,
    "comparison_period": "previous_7d"
  },
  "recommended_actions": [
    "整理性能问题反馈",
    "发布已确认问题和修复进度",
    "持续观察更新后的评论主题与在线人数"
  ],
  "status": "suggested",
  "generated_at": "2026-11-16T10:30:00Z"
}
```

策略必须注明是建议，不得声称已向玩家发送消息或已带来回流。LLM
输出需要校验 schema，并把评论正文视为不可信输入。

## 6. API 设计

统一响应：

``` json
{
  "success": true,
  "data": {},
  "meta": {
    "app_id": 1203220,
    "source": "steam_public_api",
    "data_type": "derived",
    "updated_at": "2026-11-16T10:30:00Z",
    "period": "7d",
    "timezone": "UTC",
    "definition": "近 7 天已分类评论中的负面评论占比",
    "is_stale": false
  }
}
```

`data_type`
取值：`real`、`derived`、`estimated`、`mock`、`unavailable`。

建议 API：

  API                                              功能
  ------------------------------------------------ ----------------
  `GET /api/games`                                 游戏列表
  `GET /api/games/{app_id}`                        游戏详情
  `GET /api/dashboard/overview?app_id=`            首页 KPI
  `GET /api/dashboard/activity?app_id=&period=`    在线人数趋势
  `GET /api/dashboard/sentiment?app_id=&period=`   情感趋势
  `GET /api/dashboard/hotspots?app_id=&period=`    热点主题
  `GET /api/games/{app_id}/achievements`           成就快照
  `GET /api/churn/overview?app_id=`                游戏级风险指数
  `GET /api/churn/signals?app_id=`                 风险分量与证据
  `GET /api/recall/strategies?app_id=`             策略建议
  `GET /api/data-sources/status`                   数据源状态
  `GET /api/data-quality?app_id=`                  数据质量摘要

要求：

-   校验 AppID、period 和分页参数。
-   对默认时间范围和最大返回数量设限。
-   前端不得直接请求 Steam。
-   API 失败不得静默回退 mock。
-   空数据返回空数组或 null，并提供 `unavailable` 解释。
-   时间统一 UTC，前端按本地时区展示。

## 7. 前端页面改造建议

### 7.1 首页

建议展示： - 当前 Steam 在线人数。 - 今日/近 7 天在线人数趋势。 - 近 7
天新增评论量。 - Steam 推荐率。 - 模型负面评论比例。 - 近期负面热点。 -
游戏级公开信号风险指数。 - AI 策略建议摘要。

所有 KPI
显示来源标签和更新时间。不要再展示伪造的玩家总量、高危玩家数、真实召回率。

### 7.2 舆情分析页

-   评论情感趋势。
-   好评/差评投票比例。
-   语言分布（基于实际采集评论）。
-   主题热度排行。
-   负面主题趋势。
-   可追溯评论样本。
-   模型置信度和样本量。

### 7.3 流失风险研判页

页面名称建议改为"群体风险研判"或明确说明是公开信号分析。

-   游戏级风险指数。
-   在线人数异常变化。
-   负面评论比例和增长。
-   热点集中程度。
-   风险分量解释。
-   证据评论。
-   数据样本量和局限性。

不得展示个人高危玩家列表。

### 7.4 召回策略页

-   AI 生成的策略建议。
-   对应热点与证据。
-   适用游戏和目标问题。
-   推荐动作与优先级。
-   生成时间和模型版本。
-   "建议，未执行"状态。

不得展示实际触达率、召回率和召回后留存，除非未来取得真实运营事件数据。

### 7.5 留存页面

在当前权限下不应展示真实 D1/D7/D30 Cohort 留存。可以改成： -
在线人数趋势。 - 评论者公开时长分布（仅评论作者样本）。 -
成就完成率变化（游戏支持时）。 - 公开群体活跃信号。

页面上必须解释这些不是全体玩家留存。

## 8. 自动化采集与数据持久化

建议调度：

  工作流                            频率 任务
  ------------------------ ------------- ---------------------------
  collect-ccu                30～60 分钟 多游戏在线人数
  collect-reviews              1～3 小时 Recent / Updated 评论增量
  collect-achievements              每日 全局成就
  collect-store-metadata       每日/每周 商店信息
  analyze-reviews             评论采集后 新增/更新评论分析
  daily-risk-analysis               每日 热点和风险快照

GitHub Actions runner 是临时运行环境。SQLite
文件不会因为工作流结束而自动持久化。必须先审计当前仓库已有的数据分支和
JSON 持久化方式。

如果沿用 Git 数据分支： - 每个写任务设置 concurrency group。 -
避免多个任务并发覆盖同一数据库文件。 - 使用原子写入和备份。 -
任务失败不得覆盖上一次有效数据。 - 记录提交/持久化结果。 -
检查数据库文件增长、Git 历史膨胀和合并冲突。

若现有机制无法保证安全持久化，应先使用 Actions artifacts
作为短期备份验证方案，并评估长期使用托管数据库；不能把 artifact
当作在线业务数据库。数据规模增长后再迁移 PostgreSQL。

## 9. 分阶段 Agent 实施计划

### Phase 0：代码审计与数据盘点（0.5～1 天）

任务： 1. 检查当前 Flask 路由和数据库。 2. 检查现有 CCU collector 和
workflow。 3. 检查 `dashboard-data.js` 所有模拟数组、常量和公式。 4.
检查风险、留存、情感分析脚本的真实数据输入。 5. 建立指标数据来源矩阵。
6. 备份数据库并验证历史数据。

交付： - `docs/DATA_SOURCE_AUDIT.md` -
`docs/DATA_METRIC_DEFINITIONS.md` - `docs/DATA_MIGRATION_PLAN.md`

验收： - 每个 KPI 有来源和口径。 - 模拟数据已标注。 - 现有 CCU
历史数据不丢失。

### Phase 1：多游戏配置与 CCU（1～2 天）

任务： 1. 建立 games 配置和数据库表。 2. 改造 CCU collector 支持 AppID
列表。 3. 保存多游戏快照。 4. 建立游戏列表和详情 API。 5.
前端增加游戏选择器。 6. 保证数据按 AppID 隔离。

验收： - 至少 5 款配置游戏可以独立采集。 - 一款失败不影响其他游戏。 -
新增游戏无需修改 collector 代码。 - 原有历史数据可兼容。

### Phase 2：Steam Reviews 采集（1～2 天）

任务： 1. 实现 Reviews collector。 2. 支持 Recent 和 Updated。 3. cursor
持久化和断点续采。 4. 按 AppID + recommendationid upsert。 5.
保存原始评论和公开字段。 6. 建立 collection logs 和 status API。

验收： - 多款游戏可采集。 - 重复采集不重复入库。 - 分页中断后可继续。 -
评论更新可以覆盖旧版本并触发重分析。 - 错误和空结果可观测。

### Phase 3：情感分析与热点（2～3 天）

任务： 1. 建立批量分析队列或可重跑处理任务。 2. 支持中文和英文。 3. 保存
sentiment、score、confidence、model_version。 4.
实现主题分类和热点聚合。 5. 提供情感和热点 API。 6.
为结果保留原始评论引用关系。

验收： - 新评论可自动分析。 - 模型失败不影响评论原始数据。 -
低置信度和未知语言正确标记。 - 图表统计口径与 API 一致。 -
热点可追溯至实际评论。

### Phase 4：游戏级风险和 AI 策略（1～2 天）

任务： 1. 实现可配置的风险分量和公式版本。 2. 保存每日风险快照。 3.
生成证据充分的策略建议。 4. 输出建议状态和生成时间。 5. UI
清楚标注"群体级公开信号"和"建议，未执行"。

验收： - 每个评分都能解释分量和公式。 - 缺失数据不会被当成 0。 -
不输出个人流失概率。 - 不伪造召回效果。

### Phase 5：前端真实数据接入（2～3 天）

任务： 1. 建立统一 API client。 2. 移除生产路径中的模拟数组和固定 KPI。
3. 首页接入 CCU、评论、情感、热点和风险 API。 4. 舆情页接入评论和主题
API。 5. 风险页改为群体风险研判。 6. 召回页改为 AI 策略建议。 7.
留存页隐藏无法验证的 D1/D7 指标或替换为公开信号分析。 8.
增加数据来源、更新时间、空态、错误态和过期态。

验收： - 关键展示数据均由 API 返回。 - 切换游戏后全页面数据正确刷新。 -
API 失败不会显示 mock 数据。 - 每个图表可追溯数据源和时间范围。

### Phase 6：联调、测试和 Demo 发布（1～2 天）

测试： - 采集器单元测试。 - API contract tests。 - 数据库迁移测试。 - 多
AppID 隔离测试。 - cursor 断点恢复测试。 - 重复采集和更新评论测试。 -
429、5xx、timeout 测试。 - Actions 并发和持久化测试。 - 前端 loading /
empty / error / stale 测试。

交付： - 可运行 Demo。 - 数据源状态页。 - 数据指标定义文档。 -
操作和故障排查说明。 - 测试报告。

整体估算约 9～15
个工作日，取决于现有代码质量、数据持久化机制和模型选型；不含外部权限审批或大规模模型调优。

## 10. 测试与验收总表

  类别     验收条件
  -------- ------------------------------------------
  多游戏   至少 5 款配置游戏；数据严格按 AppID 隔离
  CCU      有真实快照、时间戳、历史趋势和来源
  评论     支持增量、分页、断点恢复、去重和更新
  成就     可用时保存快照，不支持时显示 unavailable
  情感     有模型版本、置信度、语言和样本量
  热点     可追溯评论，明确时间窗口和分母
  风险     游戏级评分，公式透明，缺失值处理合理
  召回     仅策略建议，未执行状态明确
  API      统一 meta、参数校验、错误响应
  前端     无静默 mock fallback，支持空态和错误态
  持久化   Actions 结束后历史数据仍可恢复
  安全     无密钥泄漏，不滥用个人标识符
  文档     数据来源、指标口径、部署和故障处理完整

## 11. 数据状态与前端展示规范

所有指标使用：

-   `real`：外部来源原始数据。
-   `derived`：基于真实数据的计算/模型结果。
-   `estimated`：明确估算。
-   `mock`：模拟数据。
-   `unavailable`：无数据或权限不足。

推荐 UI 文案： - "Steam 实时接口采集" - "基于 Steam 评论分析" -
"模型推断结果" - "历史快照统计" - "数据暂不可用" -
"样本量不足，暂不计算"

避免使用： - "全平台真实玩家数"（除非数据确实覆盖全平台） -
"真实流失概率" - "精准召回成功率" - "D7 留存率"（没有 Cohort 事件时）

## 12. 风险与应对

  --------------------------------------------------------------------------
  风险                                应对
  ----------------------------------- --------------------------------------
  Steam 接口字段或限制变化            统一 Collector 封装、schema
                                      容错、记录接口版本

  某些游戏不支持成就                  单游戏单数据源 unavailable

  评论样本偏差                        明确样本性质，不推断全体玩家

  多语言分类误差                      先支持中英，其他语言 unknown

  评论更新导致旧分析失效              upsert 后重新分析

  Actions 临时环境导致数据丢失        明确持久化、并发和备份机制

  SQLite 并发写冲突                   串行化写任务或迁移托管数据库

  LLM 幻觉                            结构化证据输入、输出校验、评论可追溯

  风险评分被误解为预测概率            命名和 UI 明确标注为公开信号指数
  --------------------------------------------------------------------------

## 13. 推荐实施顺序

先完成 Phase 0～2，确保多游戏 CCU
和评论数据真实、可持续、可追溯。然后完成情感和热点分析，再做游戏级风险与
AI 策略建议。最后将前端所有核心页面切换到 API。

第一版 Demo 的成功标准不是"每个页面都有很多数字"，而是：

1.  数据确实来自 Steam。
2.  数据能持续采集和保存。
3.  统计口径可以解释。
4.  分析结果可以追溯。
5.  数据不可用时诚实呈现。
6.  多游戏切换稳定。
7.  页面能够展示从原始数据到 AI 建议的完整链路。
