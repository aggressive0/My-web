# PHASE6_3_RAG_ABSTENTION_AND_RETRIEVAL_OPTIMIZATION.md

> **项目阶段：** Phase 6.3 — RAG 拒答机制与检索质量优化  
> **项目：** 基于热点情感的玩家流失阻断与精准召回方案  
> **核心目标：** 解决语义检索返回主题相关但无法回答问题的文档，导致系统错误作答的问题。  
> **主要任务：** 检索分数核验、问题路由、候选召回优化、Reranker、Evidence Sufficiency Gate、分级拒答、评测体系建设。  
> **执行原则：** 保留召回能力，强化证据判断；先复现问题，再实施改动；不能用未经验证的固定阈值替代完整的拒答机制。

---

# 1. 背景与当前问题

## 1.1 当前测试结果

目前 RAG 检索使用：

```env
RAG_SCORE_THRESHOLD=0.35
```

但是该配置在当前测试中无法有效阻止无答案问题进入最终回答流程。

四个本应拒答的问题：

1. 游戏退款政策是什么？
2. 游戏开发团队有多少人？
3. 游戏机房具体位于哪里？
4. 如何设计召回活动？

测试结果：

| 指标 | 当前结果 |
|---|---:|
| 当前阈值 | 0.35 |
| 应拒答问题数量 | 4 |
| 每题返回内容数量 | 5 |
| 最高检索分数范围 | 0.43–0.53 |
| 拒答准确率 | 0% |
| 初步发现的阈值拐点 | 0.55 |
| 阈值提高后的 Recall 损失 | 0.115 |

当前问题不是简单的检索阈值过低，而是：

**向量检索系统把主题相关性误当成了事实可回答性。**

例如：

- 游戏退款问题检索到了玩家购买体验，但没有官方退款政策。
- 团队人数问题检索到了开发团队相关公告，但没有明确人数。
- 机房位置问题检索到了网络和服务器讨论，但没有机房物理位置。
- 召回活动问题检索到了公告和评论，但没有足够的运营依据支持具体活动方案。

这些文档可能与问题具有较高的语义相似度，但并不能直接回答用户的问题。

## 1.2 当前方案存在的结构性问题

现有流程可能是：

```text
用户问题
   ↓
Embedding
   ↓
ChromaDB 向量检索 Top-K
   ↓
RAG_SCORE_THRESHOLD
   ↓
将检索结果交给 LLM
   ↓
生成回答
```

主要问题：

1. 向量相似度只衡量语义接近程度，不能直接证明文档包含答案。
2. 单一阈值同时承担召回过滤和拒答判断，容易造成 Recall 与拒答准确率之间的冲突。
3. 系统没有充分区分“主题相关”“直接回答问题”和“证据足以支持答案”。
4. 即使没有有效证据，LLM 仍然可能根据常识或已有知识生成看似合理的回答。
5. 当前缺少覆盖可回答、不可回答、需要 SQL 和多文档证据问题的固定评测集。

## 1.3 本阶段核心原则

不要再让单一的 `RAG_SCORE_THRESHOLD` 决定一个问题是否可回答。

本阶段将职责拆分：

| 模块 | 职责 |
|---|---|
| 问题路由 | 判断用户问题属于知识问答、动态分析、策略生成还是系统范围之外 |
| 向量检索 | 尽可能召回潜在相关的候选文档 |
| Reranker | 对候选文档进行更精细的相关性排序 |
| Evidence Sufficiency Gate | 判断现有证据是否足以回答问题 |
| LLM | 在有效证据范围内生成答案 |
| 结果校验 | 检查来源、事实和 AppID 是否正确 |
| 拒答机制 | 在证据不足时拒绝生成没有依据的答案 |

最终目标：

**减少无证据作答，同时尽可能保留真正有用的检索结果。**

---

# 2. 本阶段目标与非目标

## 2.1 主要目标

| 编号 | 目标 | 预期结果 |
|---|---|---|
| T1 | 核查检索分数语义 | 确认分数方向、距离函数和转换逻辑 |
| T2 | 建立问题路由 | 区分知识问答、SQL 查询和策略生成 |
| T3 | 优化候选召回 | 减少过早阈值过滤造成的漏召回 |
| T4 | 增加 Reranker | 提高候选文档排序质量 |
| T5 | 增加证据充分性判断 | 区分主题相关和事实可回答 |
| T6 | 增加分级拒答 | 证据不足、证据冲突等情况不再强行回答 |
| T7 | 保证来源可追溯 | 每条事实性回答都能关联有效证据 |
| T8 | 建立固定评测集 | 可量化比较 Recall、Precision 和拒答表现 |
| T9 | 保持资源可控 | 不让额外模型破坏本地推理环境 |

## 2.2 非目标

本阶段不实施：

- 不迁移 Flask。
- 不迁移 SQLite。
- 不替换 ChromaDB。
- 不重新设计整个 RAG 框架。
- 不引入微服务架构。
- 不强制引入 Celery、Redis。
- 不强制引入多个 Agent。
- 不要求立即部署大型 Reranker 服务。
- 不将向量相似度分数包装成事实置信度。
- 不将模型生成的运营建议描述成真实发生的运营案例。
- 不将公开 Steam 数据包装成具体玩家身份数据。

优先保留现有项目架构，通过模块化方式逐步完善 RAG 流程。

---

# 3. Task 0：执行前检查与问题复现

**Agent 必须先检查现有代码，禁止直接覆盖原有 RAG 实现。**

## 3.1 阅读项目文件

至少检查：

- `AGENTS.md`
- `README.md`
- Flask 应用入口。
- 现有 AI / RAG 路由。
- ChromaDB 初始化和连接代码。
- Embedding 模型调用。
- 文档导入和切分模块。
- RAG 检索模块。
- LLM Provider。
- 当前拒答 Prompt。
- 召回策略生成模块。
- SQLite 中的知识库元数据和历史记录。
- Docker Compose 配置。
- `.env.example`。

Agent 应通过代码搜索找到：

```text
RAG_SCORE_THRESHOLD
similarity_search
query_collection
query_embeddings
distances
distances_to_scores
embedding
retrieval
rerank
abstention
```

以上关键词仅供定位，实际名称以仓库代码为准。

## 3.2 核查 ChromaDB 分数语义

这是本阶段最高优先级的检查。

不能假定 ChromaDB 返回的字段就是 0–1 相似度。

必须检查：

1. ChromaDB Collection 使用的距离函数。
2. Collection 创建时的 `hnsw:space` 配置。
3. 查询接口返回的原始 `distances`。
4. 项目是否自行进行了距离到相似度的转换。
5. `RAG_SCORE_THRESHOLD` 比较的到底是距离还是相似度。
6. 阈值比较方向是否正确。
7. 是否存在归一化、截断或其他分数转换逻辑。

ChromaDB 常见距离配置包括：

```text
cosine
l2
ip
```

例如，使用 cosine distance 时，距离越小通常表示越相似；如果业务层将它转换为 cosine similarity，分数方向则相反。

不能将不同距离函数、不同模型或不同转换方法产生的分数直接比较。

建议日志同时记录：

```python
{
    "document_id": document_id,
    "raw_distance": raw_distance,
    "normalized_score": normalized_score,
    "distance_metric": distance_metric
}
```

上述字段是日志结构示意，实际实现应兼容现有代码。

如果转换公式无法确认，应先停止使用现有阈值作最终判断。

## 3.3 复现四个失败问题

必须使用当前知识库和当前模型，重新运行以下问题：

```text
问题 1：游戏退款政策是什么？

问题 2：游戏开发团队有多少人？

问题 3：游戏机房具体位于哪里？

问题 4：如何设计召回活动？
```

每个问题至少记录：

| 字段 | 内容 |
|---|---|
| query | 原始问题 |
| app_id | 当前目标游戏 |
| expected_behavior | 预期行为 |
| retrieved_document_ids | 返回的文档 ID |
| raw_scores | 原始检索分数 |
| normalized_scores | 业务层转换后的分数 |
| document_app_ids | 文档所属 AppID |
| source_type | 公告、评论、FAQ 或其他 |
| document_excerpt | 文档内容摘要 |
| final_answer | 当前系统最终回答 |
| actual_result | 实际表现 |

必须检查每条候选文档是否包含问题要求的关键事实。

不能只看分数。

## 3.4 建立基线报告

生成：

```text
PHASE6_3_PRECHECK_REPORT.md
```

报告包含：

- 当前 RAG 实际执行流程。
- 当前向量模型和向量维度。
- ChromaDB 距离函数。
- 分数转换公式。
- `RAG_SCORE_THRESHOLD` 的实际生效位置。
- 四个失败案例的真实检索结果。
- 当前拒答机制。
- 当前是否有 Reranker。
- 当前文档元数据字段。
- 当前 AppID 过滤机制。
- 目前已知的性能瓶颈。
- 建议修改的文件。
- 尚未确认的问题。

完成基线后，继续实施后续任务，不要为了等待用户确认而停止整个执行流程；遇到无法确定的业务规则时，应在报告中标记并采用保守处理。

---

# 4. 建议的新 RAG 架构

## 4.1 目标流程

```text
用户问题
   │
   ▼
问题规范化
   │
   ▼
问题类型识别 / Intent Router
   │
   ├── 知识库事实问答
   │       │
   │       ▼
   │   向量检索候选召回
   │       │
   │       ▼
   │   AppID / 元数据过滤
   │       │
   │       ▼
   │   Reranker（可选）
   │       │
   │       ▼
   │   Evidence Sufficiency Gate
   │       │
   │       ├── 证据充分
   │       │       │
   │       │       ▼
   │       │   LLM 生成回答
   │       │       │
   │       │       ▼
   │       │   来源与事实校验
   │       │       │
   │       │       ▼
   │       │   返回回答
   │       │
   │       ├── 证据不足
   │       │       │
   │       │       ▼
   │       │   拒答 / 请求补充信息
   │       │
   │       └── 证据冲突
   │               │
   │               ▼
   │           返回冲突提示
   │
   ├── 动态数据查询
   │       │
   │       ▼
   │   SQLite / 业务分析模块
   │       │
   │       ▼
   │   返回真实指标
   │
   ├── 召回策略生成
   │       │
   │       ▼
   │   SQL 动态指标 + RAG 运营证据
   │       │
   │       ▼
   │   策略证据充分性校验
   │       │
   │       ▼
   │   生成带依据的建议方案
   │
   └── 系统范围之外
           │
           ▼
       明确拒答
```

## 4.2 关键设计原则

不同类型的问题不能统一通过知识库检索解决。

例如：

| 问题 | 应使用的能力 |
|---|---|
| 游戏某版本什么时候更新？ | RAG |
| 官方公告是否提到退款规则？ | RAG + 权威来源检查 |
| 当前 CCU 是多少？ | SQL / Steam 数据 |
| 最近评论数量变化如何？ | SQL / 分析模块 |
| 哪些群体可能存在流失风险？ | 现有业务分析模块 |
| 应该如何设计召回活动？ | SQL 指标 + 运营资料 + 策略生成 |
| 团队有多少人？ | 有明确可靠来源才允许 RAG 回答 |

特别注意：

**“如何设计召回活动”不应被简单定义为 OUT_OF_SCOPE。**

这是本项目可能需要支持的业务功能，但它应进入策略生成模式，而不是将普通评论和更新公告包装成已验证的运营依据。

如果缺少运营数据或案例：

- 可以明确说明项目资料不足。
- 可以请求补充目标群体、指标或运营约束。
- 只有在产品明确允许通用建议模式时，才允许提供明确标记为“通用建议”的方案。
- 不得伪造真实案例、玩家数据或历史运营效果。

---

# 5. Task 1：实现问题路由 Intent Router

## 5.1 问题类型

建议建立以下问题类型：

```python
class QueryIntent:
    KB_FACTUAL_QA = "kb_factual_qa"
    ANALYTICS = "analytics"
    STRATEGY_GENERATION = "strategy_generation"
    OUT_OF_SCOPE = "out_of_scope"
    AMBIGUOUS = "ambiguous"
```

如果现有项目已经有意图分类模块，优先扩展原模块。

## 5.2 路由规则

### KB_FACTUAL_QA

适用于：

- 游戏公告。
- 版本更新内容。
- 已知游戏机制。
- 官方发布的说明。
- 知识库已有的 FAQ。

进入 RAG 检索。

### ANALYTICS

适用于：

- 当前 CCU。
- 评论数量变化。
- 历史活跃趋势。
- 用户评论情感分布。
- 留存和流失指标。

优先使用 SQLite 和现有业务分析模块。

不要让 LLM 根据过期知识库文档猜测实时指标。

### STRATEGY_GENERATION

适用于：

- 召回活动设计。
- 用户群体运营建议。
- 流失风险应对方案。
- 活跃度提升建议。

输入应包含：

```text
SQL 动态分析结果
+
RAG 运营资料
+
用户提供的业务约束
```

输出必须区分：

- 真实观测数据。
- 知识库证据。
- 模型建议。
- 尚未验证的假设。

### OUT_OF_SCOPE

适用于明确超出当前系统能力的问题，例如与游戏运营、游戏数据和知识库无关的普通生活问题。

注意：不要简单根据关键词判定。

“退款政策”“团队人数”和“机房位置”不应永久被硬编码为拒答问题。

如果知识库以后导入了可信的官方退款政策或开发者公开访谈，相应问题应能够正常回答。

### AMBIGUOUS

适用于：

- 游戏对象不明确。
- 时间范围不明确。
- 问题缺少关键条件。
- 无法确定用户希望查询事实还是生成策略。

可以请求用户补充信息，不应盲目检索或直接拒答。

## 5.3 分类结果格式

推荐：

```json
{
  "intent": "kb_factual_qa",
  "app_id": 730,
  "requires_live_data": false,
  "answer_mode": "evidence_based",
  "missing_parameters": [],
  "reason": "用户询问游戏知识"
}
```

以上字段是结构示意，不代表实际 AppID 或已有项目数据。

必须对分类结果进行结构校验。

## 5.4 路由原则

- 问题分类不能代替证据检查。
- 分类器不能因为判断为 KB_FACTUAL_QA 就自动允许回答。
- 分类置信度不足时，进入保守检索或请求澄清。
- 对于策略问题，不要在路由阶段直接判定拒答。
- 不允许将普通语言模型常识当作项目专属数据。

---

# 6. Task 2：优化候选召回，保护 Recall

## 6.1 核心调整

当前不能继续把 `RAG_SCORE_THRESHOLD=0.35` 当作最终回答许可条件。

建议将检索拆分为：

```text
候选召回
    ↓
候选排序
    ↓
证据筛选
```

向量检索负责尽量找到可能有用的文档，不负责最终决定能否回答。

## 6.2 初始候选数量

建议先测试：

| 参数 | 初始测试值 |
|---|---:|
| Retrieval Top-K | 10 |
| 扩展候选测试 | 15 / 20 |
| Reranker Top-K | 5 |
| 最终证据数量 | 3–5 |

以上只是初始测试配置，必须根据数据集和性能测试确定，不可直接认定为最优值。

建议流程：

```python
candidates = vector_search(
    query=query,
    top_k=config.RAG_RETRIEVAL_TOP_K,
    app_id=app_id
)
```

接着：

1. 检查 AppID。
2. 去掉重复文档。
3. 检查文档状态。
4. 检查来源和时间信息。
5. 对候选文档执行 Reranker。
6. 进入证据充分性判断。

## 6.3 AppID 过滤

必须保留现有的 AppID 隔离机制。

默认规则：

- 只检索当前目标 AppID 的文档。
- 如果允许全局文档，必须显式标记 `scope=global`。
- 不允许因为语义相似而混入其他游戏的数据。
- 检查召回、重排、证据判断和最终回答的 AppID 是否一致。

必要时增加以下逻辑：

```python
assert document.app_id == requested_app_id \
    or document.scope == "global"
```

实际实现需要与已有数据结构兼容。

## 6.4 分数处理原则

不能直接把不同模型输出的分数放在一起比较。

必须区分：

```text
raw_distance
normalized_similarity
reranker_score
evidence_confidence
```

四者含义不同。

- `raw_distance`：底层距离度量。
- `normalized_similarity`：经过明确转换的语义相似度。
- `reranker_score`：重排模型输出。
- `evidence_confidence`：证据判断结果，不应简单由前面某个分数替代。

禁止：

```python
if embedding_score > 0.55:
    answer()
```

也禁止未经校准就将 Reranker 分数使用相同的 0.55 阈值。

---

# 7. Task 3：引入 Reranker

## 7.1 为什么需要 Reranker

Embedding 更适合快速候选召回，但它不能充分判断：

- 文档是否直接回答问题。
- 文档是否只在主题上相关。
- 文档是否包含问题需要的关键事实。
- 文档是否只是提到了相同关键词。

Reranker 可以对问题和候选文档进行更细粒度的相关性排序。

推荐候选：

```text
BAAI/bge-reranker-v2-m3
```

但是否采用需要通过当前机器上的性能测试决定。

## 7.2 建议流程

```text
Query
  │
  ▼
Embedding Retrieval
  │
  ▼
Top 10–20 Candidates
  │
  ▼
Reranker
  │
  ▼
Top 3–5 Evidence Candidates
  │
  ▼
Evidence Sufficiency Gate
```

## 7.3 Reranker 的职责边界

Reranker 只负责排序和相关性判断。

它不能独立完成：

- 事实真实性判断。
- 官方来源认证。
- 文档有效时间判断。
- 多条证据之间的矛盾消解。
- 最终是否允许回答的全部决策。

例如：

用户问题：

```text
游戏开发团队有多少人？
```

候选文档：

```text
文档 A：游戏开发团队发布了最新版本公告。
文档 B：开发者分享了游戏开发过程。
文档 C：官方发布了新的开发路线图。
```

这些内容可能被判断为高度主题相关，但如果都没有明确团队人数，就仍然不能回答人数问题。

## 7.4 Reranker 阈值

不应直接复用：

```env
RAG_SCORE_THRESHOLD=0.35
```

或者直接设置：

```env
RAG_RERANKER_THRESHOLD=0.55
```

Reranker 分数的范围和含义取决于模型及推理实现。

执行要求：

1. 记录原始 Reranker 输出。
2. 根据真实可回答和不可回答样本进行校准。
3. 比较不同阈值下的 Recall 和 Precision。
4. 检查排序结果是否能改善正确证据排名。
5. 不将相关性分数直接当作答案充分性。

如果暂时无法稳定运行 Reranker，可以先以候选召回 + Evidence Gate 完成第一阶段，不应为了引入新模型而阻断整体修复。

## 7.5 本地资源约束

本项目本地硬件：

```text
Windows 11
RAM: 16GB
GPU: NVIDIA RTX 5070 Laptop GPU
VRAM: 8GB
```

建议：

- 优先评估 CPU Reranker。
- 控制并发数量。
- 避免 Qwen3、Embedding、Reranker 多个模型同时争抢显存。
- 记录首次加载和热启动时间。
- 使用独立配置开关控制是否启用 Reranker。
- 如果性能开销明显超过收益，保留可关闭的 Provider 接口。

不允许为了提高 Recall 或 Precision 破坏已有 LLM 的可用性。

---

# 8. Task 4：实现 Evidence Sufficiency Gate

这是本阶段的核心模块。

## 8.1 目标

Evidence Sufficiency Gate 用来回答：

**现有候选证据是否足以支持用户想要的答案？**

它不只是判断文档是否与问题相关，还需要判断：

- 是否存在直接证据。
- 回答所需的关键事实是否齐全。
- 来源是否可靠。
- 资料是否过期。
- 多条证据是否相互矛盾。
- 证据是否属于当前游戏。
- 最终回答是否超出了证据范围。

## 8.2 输入结构

建议：

```python
class EvidenceCandidate:
    document_id: str
    app_id: int | None
    scope: str
    content: str
    source_type: str
    source_url: str | None
    published_at: str | None
    valid_from: str | None
    valid_to: str | None
    authority_level: str
    rerank_score: float | None
```

以上是结构示意，优先复用现有文档数据模型。

## 8.3 输出结构

建议：

```json
{
  "decision": "insufficient",
  "confidence": "high",
  "supported_claims": [],
  "missing_facts": [
    "团队具体人数"
  ],
  "evidence_ids": [],
  "conflicts": [],
  "reason": "检索结果没有直接提供团队人数"
}
```

允许的决策：

```text
answerable
insufficient
conflicting
```

建议置信等级：

```text
low
medium
high
```

字段的意义：

| 字段 | 用途 |
|---|---|
| decision | 是否允许生成事实性回答 |
| confidence | 充分性判断的置信等级 |
| supported_claims | 已被证据支持的事实 |
| missing_facts | 尚缺少的关键信息 |
| evidence_ids | 支持判断的文档 ID |
| conflicts | 存在矛盾的事实 |
| reason | 判断原因 |

## 8.4 证据充分性的判断标准

### A. 直接证据

问题要求的是明确事实时，证据必须直接包含对应信息。

例：

```text
问题：游戏开发团队有多少人？
```

需要的事实：

```text
团队人数
对应的团队主体
可靠来源
```

仅仅出现“开发团队”四个字不算证据。

### B. 关键事实覆盖

问题需要多个事实才能回答时，证据需要覆盖这些必要事实。

例：

```text
问题：如何根据当前流失情况设计召回活动？
```

可考虑的必要信息：

```text
1. 目标玩家群体
2. 当前流失或活跃变化依据
3. 可使用的运营措施或经过验证的运营知识
```

其中不同问题可能有不同的必要信息，不要对所有问题使用同一份固定字段清单。

### C. 来源可靠性

应检查：

- 是否来自官方公告。
- 是否来自开发者公开资料。
- 是否来自用户评论。
- 是否来自第三方报道。
- 是否有明确发布日期。
- 是否能访问原始来源。
- 是否可能过期。

示例优先级：

| 来源类型 | 适用范围 |
|---|---|
| 官方公告 | 游戏更新、政策、官方说明 |
| 开发者公开资料 | 团队信息、开发计划 |
| 官方 FAQ | 常见规则和机制 |
| 第三方报道 | 可作为辅助，需要核对 |
| 玩家评论 | 玩家体验与情绪分析，不作为官方政策证据 |
| 模型生成内容 | 不能作为外部事实证据 |

注意：来源类型不能代替实际内容检查。官方公告也可能没有回答当前问题。

### D. 时间有效性

对可能变化的事实，检查：

- 发布日期。
- 生效日期。
- 失效日期。
- 是否有更新版本。
- 是否存在更新的官方资料。

例如，退款政策需要优先检查当前有效政策，而不是使用过期的 FAQ。

### E. 证据冲突

例如：

- 两篇公告对功能发布时间存在差异。
- 旧政策与新政策不同。
- 不同游戏的文档被混合检索。
- 第三方报道和官方声明存在冲突。

处理规则：

- 不能随意挑选一篇文档作为唯一事实。
- 优先检查发布日期和来源权威性。
- 无法消解冲突时，返回 `conflicting`。
- 明确告诉用户存在资料差异。

## 8.5 证据判断的实施方式

建议先实现确定性检查，再按需使用 LLM 判断。

**第一层：程序规则**

- 是否有候选文档。
- 文档是否属于当前 AppID。
- 来源是否有效。
- 文档是否仍处于有效期。
- 是否存在明显重复。
- 是否满足问题类型要求的元数据。
- 是否有可验证的证据文本。

**第二层：可选 LLM 证据评估**

当问题需要语义理解或跨文档汇总时，可以调用模型进行证据充分性评估。

Prompt 必须明确：

```text
你是证据充分性评估器，不负责生成最终回答。

只能根据提供的证据判断用户问题是否可以回答。

不得使用模型自身知识补足文档中不存在的事实。

不得根据主题相关性推断事实存在。

必须指出：
1. 哪些事实获得了直接支持；
2. 还缺少哪些关键事实；
3. 是否存在证据冲突；
4. 是否允许进入最终回答阶段。

证据不足时必须返回 insufficient。
存在无法消解的事实冲突时必须返回 conflicting。
```

要求：

- 使用 JSON Schema 约束输出。
- 评估结果必须通过程序二次校验。
- `evidence_ids` 必须来自本次实际候选文档。
- 禁止引用不存在的文档。
- 不允许 LLM 自己编造来源 ID。
- LLM 的判断不等于事实真实性证明。

## 8.6 不能只依赖 LLM 自我判断

必须避免：

```text
LLM 检索文档
    ↓
LLM 自己判断有没有证据
    ↓
LLM 自己生成答案
```

这种流程容易出现模型先入为主、判断过于宽松的问题。

应至少保留程序侧验证：

```text
Evidence Gate
    ↓
校验 evidence_ids
    ↓
校验 AppID
    ↓
校验来源元数据
    ↓
校验必要事实
    ↓
决定是否允许生成
```

---

# 9. Task 5：实现分级拒答机制

## 9.1 拒答类型

建议在应用层明确区分以下结果：

```text
out_of_scope
insufficient_evidence
conflicting_evidence
ambiguous_query
missing_live_data
retrieval_failed
```

这些状态不能全部归为普通的 `failed`。

## 9.2 决策逻辑

建议：

```python
def determine_response(query_type, retrieval_result, evidence_result):

    if query_type == "out_of_scope":
        return "out_of_scope"

    if query_type == "analytics":
        return "route_to_sql"

    if query_type == "ambiguous":
        return "ask_for_clarification"

    if retrieval_result.failed:
        return "retrieval_failed"

    if evidence_result.decision == "insufficient":
        return "refuse_insufficient_evidence"

    if evidence_result.decision == "conflicting":
        return "refuse_conflicting_evidence"

    if evidence_result.decision == "answerable":
        return "generate_answer"

    return "refuse_insufficient_evidence"
```

这是逻辑示意，Agent 应按照已有服务层和错误处理机制实现。

## 9.3 四个问题的目标行为

### 问题一：退款政策

用户：

```text
这个游戏的退款政策是什么？
```

当前资料：

- 只有玩家购买评论。
- 没有官方退款规则。

预期：

```json
{
  "status": "insufficient_evidence",
  "answerable": false,
  "missing_facts": [
    "适用的官方退款政策"
  ]
}
```

回复示例：

> 当前知识库没有检索到能够确认该游戏退款政策的官方资料，因此暂时无法可靠回答。建议以适用平台或发行方公布的现行政策为准。

如果未来导入有效的官方政策文档，应允许系统正常回答。

### 问题二：团队人数

用户：

```text
这个游戏的开发团队有多少人？
```

当前资料：

- 有团队相关公告。
- 没有明确成员数量。

预期：

```json
{
  "status": "insufficient_evidence",
  "answerable": false,
  "missing_facts": [
    "开发团队人数"
  ]
}
```

回复示例：

> 目前检索到的资料提到了开发团队，但没有明确说明团队人数，因此无法确认具体人数。

### 问题三：机房位置

用户：

```text
这个游戏的机房具体位于哪里？
```

当前资料：

- 有服务器或网络体验讨论。
- 没有机房物理位置的可靠信息。

预期：

```json
{
  "status": "insufficient_evidence",
  "answerable": false,
  "missing_facts": [
    "服务器机房物理位置"
  ]
}
```

回复示例：

> 当前资料没有提供可以验证的机房物理位置。服务器连接情况或玩家延迟讨论不能直接证明机房所在地点。

### 问题四：如何设计召回活动

用户：

```text
应该如何设计召回活动？
```

这个问题与本项目业务范围相关，不应简单判定为系统范围之外。

预期：

1. 进入 `strategy_generation`。
2. 查询可用的真实流失、活跃和评论分析指标。
3. 检索运营案例、运营规则和相关游戏资料。
4. 判断证据是否足够。
5. 证据充分时生成建议策略。
6. 证据不足时说明缺少的指标或资料。

如果业务允许通用建议模式：

- 明确标注为“通用策略建议”。
- 不声称有真实历史案例支持。
- 不伪造目标玩家规模或活动效果。
- 不声称已经执行召回活动。
- 不把模拟数据写入真实统计结果。

---

# 10. Task 6：最终回答必须具备证据追溯能力

## 10.1 生成要求

只有 Evidence Gate 通过后才允许生成事实性回答。

Prompt 应包含：

```text
你只能使用下列证据回答用户问题。

禁止：
- 使用证据之外的游戏事实；
- 推测没有明确记录的数字；
- 将玩家评论描述成官方声明；
- 将过期内容描述成当前政策；
- 生成不存在的证据来源。

如果证据不足，请明确说明无法确认。
```

## 10.2 回答结构

建议：

```json
{
  "status": "answered",
  "answer": "根据官方公告，游戏在指定日期发布了该版本更新。",
  "citations": [
    {
      "document_id": "doc_001",
      "claim": "版本发布时间",
      "source_type": "official_announcement"
    }
  ],
  "confidence": "supported"
}
```

以上 ID 和内容仅为结构示意。

对于不能回答的问题：

```json
{
  "status": "insufficient_evidence",
  "answer": "当前知识库没有足够资料确认该信息。",
  "missing_facts": [
    "当前有效的官方退款政策"
  ],
  "citations": []
}
```

## 10.3 结果校验

LLM 返回后必须检查：

1. 返回的 document ID 是否存在。
2. document ID 是否属于本次检索结果。
3. AppID 是否一致。
4. 来源类型是否正确。
5. 引用内容是否确实支持对应事实。
6. 是否存在超出证据范围的数字或结论。
7. 是否使用了过期资料。
8. 是否将模型建议误写成已验证事实。

如果验证失败：

- 不允许正常返回为可信回答。
- 应进行有限次数的修复或直接拒答。
- 不能反复请求模型直到获得一个看似符合预期的结果。
- 必须留下失败原因和日志。

---

# 11. 配置调整

## 11.1 配置目标

不再依赖单一的：

```env
RAG_SCORE_THRESHOLD=0.35
```

建议根据现有配置模块增加：

```env
# 候选召回
RAG_RETRIEVAL_TOP_K=10

# Reranker 最终候选数量
RAG_RERANK_TOP_K=5

# Reranker 开关
RAG_ENABLE_RERANKER=false

# 证据充分性判断
RAG_ENABLE_EVIDENCE_GATE=true

# 最终回答必须有可验证引用
RAG_REQUIRE_CITATIONS=true

# 是否允许无证据生成
RAG_ALLOW_UNGROUNDED_ANSWER=false

# 候选检索分数是否进行阈值过滤
RAG_USE_RETRIEVAL_MIN_SCORE=false
```

上述配置是建议的初始配置。

Agent 应优先兼容现有环境配置，不要重复创建含义相近的配置。

## 11.2 关于 RAG_SCORE_THRESHOLD 的迁移

不要简单删除后导致原有系统发生配置错误。

执行步骤：

1. 搜索所有 `RAG_SCORE_THRESHOLD` 使用位置。
2. 确认它是否只用于候选过滤，还是也控制最终回答。
3. 保留必要的兼容逻辑。
4. 将候选检索分数与证据判断解耦。
5. 增加弃用日志或迁移说明。
6. 在确认没有旧逻辑依赖后，再清理配置。

建议最终将配置职责分离：

```text
RAG_RETRIEVAL_TOP_K
RAG_RERANK_TOP_K
RAG_ENABLE_EVIDENCE_GATE
RAG_ENABLE_RERANKER
```

后续如果确实需要设置候选检索分数阈值，应使用：

```env
RAG_RETRIEVAL_MIN_SCORE=
```

但只有在距离语义和分数范围已经确认、阈值已经经过评测校准后才启用。

Reranker 阈值应单独校准，不能复用 Embedding 阈值。

**不要设置一个未经实验验证的全局新阈值来替代旧阈值。**

---

# 12. Task 7：建立 RAG 评测集

只有四个拒答问题，无法可靠地完成阈值校准和拒答策略评估。

本阶段必须建立固定的小型评测集。

## 12.1 测试类别

建议至少包含以下五类：

| 类别 | 典型问题 | 预期结果 |
|---|---|---|
| A：可直接回答 | 游戏某版本什么时候发布？ | 回答 |
| B：主题相关但无答案 | 开发团队有多少人？ | 拒答 |
| C：完全无关 | 当前新加坡天气怎么样？ | 拒答或转其他能力 |
| D：动态数据查询 | 当前 CCU 是多少？ | SQL 查询 |
| E：策略生成 | 应该如何设计召回活动？ | 根据数据和证据生成建议 |

每类建议先准备 20–30 个问题，总量约 100–150 个。

如果当前知识库规模较小，可以先建立较小版本，但必须保证五种类型都有样本。

## 12.2 重点困难样本

必须覆盖：

1. 文档包含关键词，但没有回答问题。
2. 文档和问题属于同一主题，但没有关键事实。
3. 多篇文档分别包含部分事实。
4. 两个 AppID 的文档内容非常相似。
5. 旧文档和新文档信息不同。
6. 官方公告和玩家评论对同一问题提供不同信息。
7. 问题涉及一个具体数字，但文档没有数字。
8. 用户询问实时数据，但知识库只有历史资料。
9. 用户问题存在歧义。
10. 需要通用建议，但没有足够的项目专属运营资料。

## 12.3 Golden Dataset 格式

建议以 JSONL 或 JSON 保存：

```json
{
  "query_id": "qa_001",
  "query": "这个游戏的开发团队有多少人？",
  "app_id": 730,
  "expected_intent": "kb_factual_qa",
  "expected_answerable": false,
  "expected_behavior": "refuse_insufficient_evidence",
  "required_facts": [
    "团队人数"
  ],
  "relevant_document_ids": [],
  "notes": "存在团队相关公告，但没有成员人数"
}
```

以上内容仅作格式示意。

每条样本应尽可能提供：

- 标准问题。
- AppID。
- 预期意图。
- 是否可回答。
- 预期处理结果。
- 必要事实。
- 正确证据 ID。
- 备注。

人工检查 Golden Dataset，避免将本身有证据的问题错误标成拒答。

## 12.4 评测集版本化

建议路径：

```text
tests/
    rag/
        datasets/
            rag_eval_v1.jsonl
        test_retrieval.py
        test_reranker.py
        test_evidence_gate.py
        test_abstention.py
        test_appid_isolation.py
        test_answer_grounding.py
        evaluation_runner.py
```

路径仅为建议，应以现有测试目录为准。

评测集和评测脚本必须进入版本管理。

---

# 13. Task 8：建立评估指标

不能只看 Recall，也不能只看拒答准确率。

必须同时评价检索质量、回答质量和拒答质量。

## 13.1 Retrieval Recall@K

衡量正确证据是否进入候选列表。

建议区分：

- Evidence Recall@K：标准证据集合中有多少比例被召回。
- Hit Rate@K：有多少问题至少召回一条正确证据。

这样可以避免仅凭“召回到一条”就认为完整证据已经找到。

## 13.2 Precision@K

衡量前 K 条候选文档中真正相关的比例。

必须根据人工标注判断，而不是让检索分数自行定义相关性。

## 13.3 MRR

衡量正确证据是否排在靠前的位置。

用于判断 Reranker 是否真正改善排序。

## 13.4 Answerable Precision

衡量系统选择回答的问题中，真正有足够证据支持的比例。

```text
Answerable Precision
=
正确回答的可回答问题数
/
系统选择回答的问题总数
```

该指标越高，说明系统错误放行的问题越少。

## 13.5 Abstention Accuracy

衡量系统对应该拒答的问题是否正确拒答。

```text
Abstention Accuracy
=
正确拒答的问题数
/
所有应拒答的问题数
```

不能将所有问题全部拒答来获得较高的拒答准确率，因此必须与 Coverage 一起看。

## 13.6 False Answer Rate

衡量没有足够证据时，系统仍然生成事实性答案的比例。

```text
False Answer Rate
=
应拒答但错误回答的问题数
/
所有应拒答的问题数
```

这是本阶段必须重点压低的指标。

## 13.7 Coverage

衡量系统选择回答的请求比例。

```text
Coverage
=
系统选择回答的问题数
/
总问题数
```

不能为了降低错误回答率，直接拒绝大量原本可以回答的问题。

## 13.8 AppID Leakage

统计是否出现跨 AppID 证据泄漏。

这是硬性安全要求：

```text
AppID Leakage = 0
```

## 13.9 推荐评测报告

生成：

```text
PHASE6_3_RAG_EVALUATION_REPORT.md
```

至少包含：

| 方案 | Recall@K | Precision@K | MRR | Answerable Precision | Abstention Accuracy | False Answer Rate | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| 当前基线 0.35 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| 阈值 0.55 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| Top-K + Evidence Gate | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| Top-K + Reranker + Gate | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |

所有数据必须来自真实执行，不能填入预测数据。

---

# 14. A/B 测试方案

## 14.1 测试组

建议至少比较：

| 组别 | 检索方式 | Reranker | Evidence Gate | 用途 |
|---|---|---|---|---|
| A | 原有 0.35 | 无 | 无 | 当前基线 |
| B | 原有 0.55 | 无 | 无 | 验证阈值提高的影响 |
| C | Top-K 候选召回 | 无 | 有 | 验证拒答模块收益 |
| D | Top-K 候选召回 | 有 | 有 | 验证完整方案 |

如果 Reranker 尚未完成，可先执行 A/B/C。

## 14.2 测试条件

保证：

- 相同数据集。
- 相同 AppID。
- 相同知识库版本。
- 相同 Embedding 模型。
- 相同业务数据快照。
- 相同 Prompt 版本。
- 相同 LLM 模型及配置。
- 相同测试环境。

同时记录每次配置变更。

## 14.3 测试结果判断

需要重点回答：

1. 0.55 是否真的改善拒答？
2. 它损失的 0.115 Recall 具体来自哪些问题？
3. Evidence Gate 能否不提高向量阈值，就拦截无证据问题？
4. Reranker 是否改善正确证据排名？
5. 哪些问题是检索失败，哪些问题是证据不足？
6. 是否有可回答问题被错误拒绝？
7. 是否存在 AppID 交叉污染？
8. 性能开销是否能够接受？

不能只根据四个无答案问题判断系统整体效果。

---

# 15. 自动化测试要求

## 15.1 检索分数测试

覆盖：

- cosine distance。
- L2 distance。
- IP distance（如果实际项目使用）。
- 自定义相似度转换。
- 阈值比较方向。
- 分数为空或异常。
- 不同分数类型不能混用。

最终实现以项目实际 Collection 配置为准。

## 15.2 问题路由测试

覆盖：

- 知识库事实问答。
- 动态数据查询。
- 召回策略生成。
- 系统范围之外的问题。
- 歧义问题。
- 需要跨模块组合的请求。

重点防止：

- 将召回活动设计直接判为 OUT_OF_SCOPE。
- 将实时 CCU 问题交给 RAG 猜测。
- 将正常知识问答误判为无答案问题。

## 15.3 AppID 隔离测试

模拟：

```text
用户询问游戏 A 的更新内容。
游戏 B 的公告中存在高度相似的关键词。
```

验证：

- 不允许游戏 B 的私有文档进入游戏 A 的最终证据。
- 全局文档必须经过显式授权。
- 最终引用仍然保持正确 AppID。

## 15.4 Evidence Gate 测试

至少覆盖：

| 场景 | 预期结果 |
|---|---|
| 有直接明确证据 | answerable |
| 只有主题相关内容 | insufficient |
| 缺少核心数字 | insufficient |
| 多篇文档互补且覆盖完整 | answerable |
| 多篇文档存在矛盾 | conflicting |
| 引用了过期资料 | 根据业务规则拒答或降级 |
| 引用了其他 AppID 文档 | 拒绝 |
| 证据 ID 不存在 | 拒绝 |

## 15.5 最终生成校验

验证：

- 无证据不能生成事实性回答。
- 引用 ID 必须来自本次候选。
- 引用必须与事实对应。
- 不能将玩家评论写成官方公告。
- 不能将建议写成已执行行为。
- 不能生成不存在的统计数字。
- 不能跨 AppID 引用。
- 证据冲突不能被静默隐藏。

## 15.6 四个回归问题

原始四个问题必须成为自动化回归样本：

```text
1. 游戏退款政策是什么？
2. 游戏开发团队有多少人？
3. 游戏机房具体位于哪里？
4. 如何设计召回活动？
```

前 3 个问题在当前知识库没有新增可靠证据的情况下，应拒答。

第 4 个问题应进入策略生成流程；如果数据和证据不足，应说明具体缺失项，不能简单套用普通知识问答逻辑。

---

# 16. 前端交互与用户反馈

前端不应把所有拒答都显示为“AI 生成失败”。

建议区分：

| 状态 | 建议提示 |
|---|---|
| answered | 已根据现有资料生成回答 |
| insufficient_evidence | 当前知识库缺少足够证据 |
| conflicting_evidence | 检索到的资料存在冲突，暂时无法确认 |
| out_of_scope | 当前系统暂不支持该类问题 |
| ambiguous_query | 请补充游戏或查询条件 |
| route_to_sql | 正在查询实时业务数据 |
| strategy_generation | 正在结合运营数据生成建议 |
| retrieval_failed | 知识检索服务暂时不可用 |

## 16.1 证据不足提示

推荐：

> 当前知识库没有找到足以支持该结论的可靠资料，因此暂时无法确认。你可以补充相关资料，或调整问题范围后再试。

## 16.2 证据冲突提示

推荐：

> 当前检索到的资料存在差异，暂时无法确定哪个信息是最新或有效版本。请以官方最新公告为准。

## 16.3 策略生成提示

推荐：

> 正在结合当前游戏指标和相关运营资料生成召回建议。结果属于待评估的策略建议，并不代表已经执行召回活动。

## 16.4 来源展示

对于正常回答，建议展示：

- 来源名称。
- 来源类型。
- 文档发布日期。
- 关联游戏。
- 引用内容摘要。

对于证据不足的问题，不能展示五条看起来像答案依据的文档作为“推荐答案来源”。

可以展示“检索到的相关资料”，但必须明确标注这些资料不足以回答用户问题。

---

# 17. 建议新增或调整的代码模块

以下为逻辑模块建议，并非要求 Agent 按照名称机械创建文件。

```text
backend/
  ai/
    rag/
      retrieval/
        retriever.py
        score_normalizer.py
        reranker.py

      routing/
        intent_router.py

      evidence/
        sufficiency_gate.py
        evidence_validator.py
        source_validator.py

      generation/
        grounded_generator.py

      evaluation/
        metrics.py
        evaluator.py
        dataset_loader.py
```

建议职责：

| 模块 | 职责 |
|---|---|
| `retriever.py` | 现有 ChromaDB 检索封装 |
| `score_normalizer.py` | 处理原始距离和分数转换 |
| `reranker.py` | 候选文档重排序 |
| `intent_router.py` | 问题分类与能力路由 |
| `sufficiency_gate.py` | 证据充分性判断 |
| `evidence_validator.py` | 证据 ID、AppID 和内容校验 |
| `source_validator.py` | 来源可信度、有效期检查 |
| `grounded_generator.py` | 使用已验证证据生成回答 |
| `metrics.py` | 检索、回答和拒答指标 |
| `evaluator.py` | 执行固定评测集 |

执行要求：

- 先检查当前项目模块。
- 已有相同功能就扩展原模块。
- 不要重复创建两个 ChromaDB Provider。
- 不要将全部业务逻辑堆积在 Flask 路由。
- 不要因为目录调整而破坏现有 API。
- 不要为了符合文档目录而重构无关模块。

---

# 18. Agent 分阶段执行清单

## Task 0：现状检查与复现

- [ ] 阅读项目规范和关键代码。
- [ ] 定位 `RAG_SCORE_THRESHOLD`。
- [ ] 检查 ChromaDB 距离函数。
- [ ] 确认原始分数和转换公式。
- [ ] 复现四个拒答失败案例。
- [ ] 保存每题 Top-K 文档和原始分数。
- [ ] 记录当前 Recall 和拒答指标。
- [ ] 生成 `PHASE6_3_PRECHECK_REPORT.md`。

验收：能够解释当前阈值为何失效，并明确哪些问题来自检索、哪些来自证据判断。

## Task 1：问题路由

- [ ] 定义问题类型。
- [ ] 实现或扩展 Intent Router。
- [ ] 区分 RAG、SQL、策略生成和范围外问题。
- [ ] 增加歧义问题处理。
- [ ] 为四个原始问题编写路由测试。

验收：召回活动设计不会被简单判为范围外，实时指标问题不会依赖 RAG 猜测。

## Task 2：检索优化

- [ ] 拆分候选召回和最终证据筛选。
- [ ] 将 Top-K 改为可配置参数。
- [ ] 检查 AppID 过滤。
- [ ] 记录原始分数和归一化分数。
- [ ] 移除旧阈值直接决定最终回答的逻辑。
- [ ] 保留必要的旧配置兼容性。

验收：候选召回阶段不再承担最终拒答职责。

## Task 3：Reranker

- [ ] 检查是否已有 Reranker。
- [ ] 评估 BGE Reranker v2-m3。
- [ ] 实现可替换 Provider。
- [ ] 记录重排前后结果。
- [ ] 测试 CPU 推理性能。
- [ ] 测试对 Recall、Precision 和 MRR 的影响。
- [ ] 确保可以关闭 Reranker。

验收：能够通过数据说明 Reranker 是否提升了候选排序质量。

## Task 4：Evidence Sufficiency Gate

- [ ] 实现证据充分性判断。
- [ ] 支持 answerable、insufficient、conflicting。
- [ ] 增加 AppID 校验。
- [ ] 增加来源和有效期检查。
- [ ] 增加必要事实覆盖检查。
- [ ] 按需接入 LLM 证据评估。
- [ ] 对 Gate 输出进行程序校验。

验收：主题相关但没有答案的文档不能直接让系统进入最终回答阶段。

## Task 5：分级拒答与最终生成

- [ ] 增加分级拒答状态。
- [ ] 证据通过后才允许事实性回答。
- [ ] 增加来源 ID 校验。
- [ ] 增加回答引用校验。
- [ ] 增加证据不足提示。
- [ ] 增加证据冲突提示。
- [ ] 区分策略建议和事实回答。

验收：无证据时不会生成看似确定的事实；正常问题不会因严格阈值而被大规模拒绝。

## Task 6：评测体系

- [ ] 建立 Golden Dataset。
- [ ] 加入四个原始失败案例。
- [ ] 加入可回答问题。
- [ ] 加入主题相关但无答案的问题。
- [ ] 加入 SQL 动态数据问题。
- [ ] 加入策略生成问题。
- [ ] 实现 Recall、Precision、MRR 等指标。
- [ ] 实现拒答和错误回答评测。
- [ ] 执行 A/B 测试。
- [ ] 生成 `PHASE6_3_RAG_EVALUATION_REPORT.md`。

验收：能够通过固定数据集证明修改带来的改善，而不是只依靠人工试问。

## Task 7：回归测试与交付

- [ ] 执行自动化测试。
- [ ] 执行原有 Steam 分析模块回归测试。
- [ ] 执行 Docker 部署验证。
- [ ] 检查前端拒答状态。
- [ ] 检查历史记录兼容性。
- [ ] 检查模型性能和资源消耗。
- [ ] 生成最终报告。

最终文件：

```text
PHASE6_3_PRECHECK_REPORT.md

PHASE6_3_RAG_EVALUATION_REPORT.md

PHASE6_3_RAG_ABSTENTION_AND_RETRIEVAL_REPORT.md
```

---

# 19. 最终验收标准

## 19.1 功能验收

- [ ] 已确认 ChromaDB 分数的真实含义。
- [ ] 不再直接使用单一 Embedding 阈值决定是否允许回答。
- [ ] 问题路由能够区分知识问答、动态查询和策略生成。
- [ ] 召回候选与最终证据筛选相互独立。
- [ ] AppID 过滤在所有相关阶段生效。
- [ ] Evidence Sufficiency Gate 能判断证据不足。
- [ ] Evidence Sufficiency Gate 能发现明显冲突。
- [ ] 无效证据不能进入最终答案。
- [ ] 最终答案能够关联真实文档来源。
- [ ] 召回策略能够区分真实数据和模型建议。
- [ ] 前端可以展示不同拒答原因。

## 19.2 拒答验收

对当前知识库：

| 测试问题 | 目标行为 |
|---|---|
| 游戏退款政策是什么？ | 无官方政策资料时拒答 |
| 游戏开发团队有多少人？ | 没有明确人数时拒答 |
| 游戏机房具体位于哪里？ | 没有可靠位置资料时拒答 |
| 如何设计召回活动？ | 进入策略生成流程，证据不足时说明缺失项 |

前三个问题不能通过固定关键词永久拒答；未来有可信新资料时必须允许正常回答。

## 19.3 指标验收

建议将以下作为初步工程目标，而不是未经测试的既成事实：

| 指标 | 目标 |
|---|---|
| AppID Leakage | 0 |
| 无证据错误作答 | 固定评测集内尽可能为 0 |
| 应拒答样本拒答准确率 | 目标 ≥95% |
| Answerable Precision | 相比当前基线明显提高 |
| Evidence Recall@K | 不因拒答机制而出现不可接受的下降 |
| 可回答问题 Coverage | 不出现大规模下降 |
| 证据来源校验 | 所有成功回答均通过 |
| JSON / Schema 校验 | 所有结构化输出均通过 |

对于较小的评测集，应同时报告样本数和具体错误案例，不能仅报告百分比。

### 19.4 Recall 损失控制

当前将阈值提高到 0.55 时，Recall 损失为 0.115。

本阶段希望通过更好的候选召回和证据充分性判断，避免为了拒答而直接大幅过滤候选文档。

建议目标：

- 保留正确证据进入候选池的能力。
- 通过重排提升有效证据排名。
- 通过 Evidence Gate 拦截无答案问题。
- 在与当前基线相同的评测集上，优先避免 Recall 出现明显下降。

如果完整方案仍然损失较多 Recall，Agent 必须列出具体漏召回问题，分析是 Embedding、Top-K、AppID 过滤、Reranker 还是数据本身的问题。

不能只写“模型效果不佳”。

---

# 20. 资源与性能约束

本项目本地环境：

```text
Windows 11
CPU: AMD Ryzen 9 8940HX
RAM: 16GB
GPU: NVIDIA RTX 5070 Laptop GPU
VRAM: 8GB
Storage: 1TB NVMe
```

资源使用原则：

1. 不默认要求 Qwen3、Embedding 和 Reranker 同时常驻 GPU。
2. 优先测试 CPU Reranker。
3. 控制模型并发。
4. 避免重复加载模型。
5. 记录 Reranker 的冷启动与热启动耗时。
6. 记录增加 Gate 后的整体端到端延迟。
7. 如果 LLM Gate 导致额外延迟明显，可以先尝试确定性程序规则覆盖容易判断的情况。
8. 仅在复杂问题需要语义判断时调用 LLM Gate。

不要为了拒答质量增加大量无法在本地稳定运行的模型。

---

# 21. Git 与代码修改约束

Agent 必须遵守项目现有开发规范：

- 修改前检查当前分支。
- 检查工作区是否存在未提交变更。
- 不覆盖用户已有本地修改。
- 不擅自修改 `test` 分支。
- 未经明确授权，不合并到 `main`。
- 未经明确授权，不执行发布操作。
- 不擅自推送远端分支。
- 不删除已有历史策略记录。
- 不破坏现有 SQLite 数据。
- 不修改与本阶段无关的 Steam 采集逻辑。

如果需要数据库结构调整：

- 优先使用兼容性迁移。
- 保留已有历史记录。
- 对迁移前后数据执行校验。
- 不直接删除或重建生产数据表。

---

# 22. 最终交付要求

Agent 必须提供：

## 22.1 代码交付

- 检索分数语义核验。
- 问题路由模块。
- 候选召回优化。
- 可选 Reranker。
- Evidence Sufficiency Gate。
- 证据与来源校验。
- 分级拒答。
- 最终回答引用校验。
- 自动化测试。
- 必要的前端状态展示。

## 22.2 文档交付

```text
PHASE6_3_PRECHECK_REPORT.md

PHASE6_3_RAG_EVALUATION_REPORT.md

PHASE6_3_RAG_ABSTENTION_AND_RETRIEVAL_REPORT.md
```

## 22.3 最终报告必须回答

1. 为什么原来的 `RAG_SCORE_THRESHOLD=0.35` 无效？
2. ChromaDB 当前返回的分数究竟代表什么？
3. 当前四个拒答问题分别为什么会失败？
4. 是否使用了 Reranker？它带来了什么改善？
5. Evidence Gate 如何判断证据充分性？
6. 如何保证 AppID 不会发生交叉污染？
7. 如何保证模型不会在没有证据时强行回答？
8. 四个原始问题目前的测试结果是什么？
9. Recall 和拒答准确率分别发生了什么变化？
10. 额外的延迟和内存开销是多少？
11. 哪些问题仍然存在？
12. 下一阶段最值得优化的地方是什么？

如果某项功能没有完成，或者因为环境条件无法验证，必须明确标记为：

```text
未实现
```

或：

```text
未验证
```

不能将计划实现写成已经实现。

---

# 23. 最终实施原则

本阶段最重要的不是找到一个更好的固定阈值，而是重新明确 RAG 系统中不同模块的职责。

```text
向量检索：
尽可能找到潜在相关内容。

Reranker：
尽可能把真正相关的内容排在前面。

Evidence Sufficiency Gate：
判断现有证据是否足以支持答案。

LLM：
只根据通过验证的证据生成回答。

结果校验：
确保回答引用真实、正确、可追溯。

Abstention：
在没有充分证据时明确拒绝作答。
```

**最终要求：不要为了提高拒答准确率而牺牲整个检索系统的召回能力，也不要为了提高 Recall 而允许模型使用无关文档编造答案。**

本阶段的交付标准是：系统既能够找到正确证据，也能够识别自己没有足够证据的情况。