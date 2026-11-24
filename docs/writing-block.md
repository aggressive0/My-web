# PHASE6_AI_RAG_IMPLEMENTATION.md

# Phase 6：AI 召回策略生成与本地 RAG 知识库实施方案

> **项目：** My-web — 基于热点情感的玩家流失阻断与精准召回方案  
> **阶段：** Phase 6 — AI Recall Strategy & Local RAG  
> **执行对象：** AI Coding Agent  
> **文档性质：** 工程实施规范  
> **核心目标：** 在现有项目基础上，建立可本地运行、可验证、可扩展的 RAG 知识库与 AI 召回策略生成系统。

---

# 0. Agent 执行总则

## 0.1 开始开发前必须完成的工作

在修改任何代码之前，必须先检查：

1. 项目根目录中的 `AGENTS.md`。
2. 项目 `README.md`。
3. 当前 Flask 后端目录结构。
4. SQLite 数据库结构及现有数据访问方式。
5. Steam 数据采集、评论分析、CCU 分析及玩家流失分析逻辑。
6. 现有 AI 分析面板及模型 API 调用方式。
7. Docker Compose、Dockerfile 和本地运行方式。
8. 当前前端页面、API 请求封装和 ECharts 配置。

先形成项目现状报告，再确定具体修改位置。

**禁止未经检查就重建后端、替换数据库、重写前端或修改现有业务逻辑。**

## 0.2 必须遵守的项目约束

- 保留 Flask 后端。
- 保留 SQLite 数据库。
- 保留现有 HTML、CSS、JavaScript 和 ECharts 前端架构。
- 不得为了引入 RAG 而整体迁移到 FastAPI、React 或其他技术栈。
- 不得破坏现有 Steam 数据采集、分析和可视化功能。
- 不得在浏览器端保存模型 API Key。
- 不得将模型生成的建议伪装成已经执行的召回操作。
- 不得虚构玩家行为、玩家身份、游戏数据、公告内容或知识库来源。
- 所有新功能必须可以在 Docker 环境中运行。
- 保持现有项目代码风格、命名方式和 API 设计习惯。

## 0.3 Git 操作约束

- 开发工作按照项目现有工作分支规范进行。
- 不得修改 `test` 分支。
- 不得擅自合并到 `main`。
- 不得擅自发布或部署。
- 未经明确授权，不执行远端分支合并、发布等操作。

---

# 1. 项目目标与功能范围

## 1.1 本阶段目标

本阶段需要完成四个核心能力：

1. 本地知识库管理。
2. RAG 检索与证据提取。
3. AI 召回策略生成。
4. AI 生成结果的历史记录与可视化。

整体流程：

```text
Steam 公开数据
      |
      v
现有数据分析系统
      |
      v
群体级玩家流失风险分析
      |
      +----------------------+
      |                      |
      v                      v
动态业务数据              RAG 知识库
SQLite                    ChromaDB
      |                      |
      |                 BGE-M3 Embedding
      |                      |
      +----------+-----------+
                 |
                 v
          AI 召回策略生成
                 |
                 v
           Ollama + Qwen3
                 |
                 v
           证据校验模块
                 |
                 v
          结构化召回策略
                 |
                 v
           前端展示与保存
```

## 1.2 本阶段不做的事情

本项目是基于 Steam 公开数据的分析与策略生成 Demo。

因此：

- 不建立真实玩家身份识别系统。
- 不获取玩家私人联系方式。
- 不追踪无法从公开数据中识别的具体玩家。
- 不声称可以精准识别某个具体流失玩家。
- 不声称已经向玩家发送召回消息。
- 不实现真实营销自动化。
- 不实现真实游戏内消息推送。

所有召回策略均为**群体级、建议型、可解释的运营方案**。

---

# 2. 技术选型

## 2.1 最终技术方案

采用：

**自建轻量 RAG 业务层 + ChromaDB + BGE-M3 + Ollama。**

不直接复制完整的第三方 RAG 项目，也不从零开发向量数据库、Embedding 模型或大语言模型。

| 模块 | 技术选型 | 用途 |
|---|---|---|
| 后端 | Flask | API 和业务逻辑 |
| 关系数据库 | SQLite | 业务数据、策略记录、知识库元数据 |
| 向量数据库 | ChromaDB | 存储知识库向量 |
| Embedding | BGE-M3 | 文本向量化 |
| LLM | Ollama + Qwen3 4B | 生成召回策略 |
| 关键词检索 | SQLite FTS5（第二阶段） | 补充关键词召回 |
| Reranker | BGE Reranker v2-m3（第二阶段） | 检索结果重排序 |
| 容器 | Docker Desktop + WSL2 | 后端、数据库及相关服务 |
| 模型运行 | Windows 原生 Ollama | 本地模型推理 |

## 2.2 为什么采用自建轻量 RAG 业务层

不要将整个 RAG 流程完全交给某个第三方框架。

本项目的业务逻辑相对明确：

- 按 AppID 区分游戏。
- 检索对应游戏的公告、补丁、FAQ 和运营案例。
- 将实时业务指标与知识库证据组合。
- 生成群体级召回策略。
- 对策略中的证据进行校验。
- 将结果保存到现有业务数据库。

这些逻辑需要与当前 Flask 项目深度集成。

因此，自建业务层能够更好地控制：

1. 数据来源。
2. 游戏隔离。
3. 检索条件。
4. 证据引用。
5. 生成格式。
6. 错误处理。
7. 后续维护。

但不意味着所有底层能力都自己开发。

向量存储使用 ChromaDB，文本向量化使用 BGE-M3，模型推理使用 Ollama。

## 2.3 为什么第一阶段选择 ChromaDB

第一阶段不需要复杂的分布式向量检索系统。

ChromaDB 的优势：

- Python 集成简单。
- 支持本地持久化。
- 支持 Metadata Filter。
- 可以按照 AppID 过滤知识库。
- 适合单机 Demo。
- 能够快速完成文档导入、向量检索和 RAG 流程。

第一阶段使用 ChromaDB，不代表项目永久绑定 ChromaDB。

需要封装 VectorStore 接口，使后续可以替换为 Qdrant。

建议接口：

```python
class VectorStore:
    def add_documents(self, documents):
        raise NotImplementedError

    def search(self, query, filters=None, top_k=5):
        raise NotImplementedError

    def delete_documents(self, document_ids):
        raise NotImplementedError

    def health_check(self):
        raise NotImplementedError
```

实际实现：

```text
VectorStore
    |
    +-- ChromaVectorStore
    |
    +-- QdrantVectorStore（未来可选）
```

不要让业务层直接依赖 ChromaDB 的内部对象。

## 2.4 为什么不直接使用 myLocalKb

参考项目：

https://github.com/apg6390/myLocalKb

可以借鉴：

- 文档上传与索引流程。
- ChromaDB 本地持久化。
- Ollama RAG 对话。
- 文档来源返回。
- 证据不足时拒绝回答。

但不建议直接将其作为项目基础。

原因：

- 项目定位偏向个人本地知识库。
- 当前属于早期原型。
- 技术架构与本项目现有 Flask 项目不完全一致。
- 默认 Embedding 方案并非本项目计划采用的 BGE-M3。
- 文档解析、扫描 PDF 和大文档性能仍需要自行验证。

**结论：只参考具体实现，不直接复制整个项目。**

## 2.5 为什么不直接使用 KnowIsland

参考项目：

https://github.com/toyhank/private-knowledge-base

可以借鉴：

- 检索结果重排序。
- 证据来源引用。
- 低置信度拒答。
- 检索上下文预算控制。
- CPU 与 GPU 资源分配。
- 检索结果质量评估。

但不建议整体移植。

原因：

- 它采用 FastAPI、React、Qdrant 等独立应用架构。
- 本项目没有必要为了 RAG 改造现有技术栈。
- 它当前的定位是单机、单用户应用。
- README 中列出的部分高级检索能力仍需要进一步验证。

**结论：借鉴检索链路和工程设计，不复制其完整应用。**

## 2.6 是否使用 LlamaIndex

参考：

https://github.com/run-llama/llama_index

第一阶段不强制引入。

原因：

- 当前业务流程清晰。
- 需要控制依赖数量。
- 自建轻量 Pipeline 更容易与现有 Flask 项目结合。
- 直接使用完整框架可能增加调试和维护成本。

未来当文档解析、复杂检索器、混合检索和多数据源管理明显增加时，再考虑局部引入 LlamaIndex。

---

# 3. 本地硬件与模型部署

## 3.1 本地机器配置

| 项目 | 配置 |
|---|---|
| 操作系统 | Windows 11 |
| CPU | AMD Ryzen 9 8940HX |
| 内存 | 16GB |
| GPU | NVIDIA RTX 5070 Laptop GPU |
| 显存 | 8GB |
| 硬盘 | 1TB NVMe |

部署必须考虑内存和显存竞争。

不要让多个大模型同时常驻显存。

## 3.2 Ollama 部署方式

推荐：

**Windows 原生运行 Ollama，Docker 容器访问宿主机 Ollama。**

原因：

- Windows 原生 Ollama 可以直接使用 NVIDIA GPU。
- Docker Desktop + WSL2 负责运行 Flask、SQLite 和 ChromaDB。
- 避免在容器内部重复配置模型运行环境。
- 简化 GPU 容器配置。

访问地址：

```text
http://host.docker.internal:11434
```

Docker 容器中不能使用：

```text
http://localhost:11434
```

因为容器内的 localhost 指向容器本身。

## 3.3 LLM 模型

第一阶段使用：

```text
Qwen3 4B
```

建议配置：

```text
OLLAMA_MODEL=qwen3:4b
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_NUM_CTX=4096
OLLAMA_NUM_PARALLEL=1
```

具体模型标签应以 Ollama 当前可用模型为准。

要求：

- 单并发。
- 控制上下文长度。
- 避免同时加载多个大模型。
- 设置合理超时。
- 对模型不可用情况提供明确错误提示。

模型参数集中放入环境变量，不允许散落在业务代码中。

## 3.4 Embedding 模型

第一阶段使用：

```text
BAAI/bge-m3
```

建议优先采用 CPU 运行或分时运行。

原因：

- Embedding 与 LLM 同时占用 GPU，可能造成资源竞争。
- 本地内存只有 16GB。
- 项目第一阶段数据量不会特别大。

要求：

- Embedding 模型只初始化一次。
- 支持批量向量化。
- 支持离线缓存。
- 不要每次请求重新加载模型。
- 记录 Embedding 模型名称和版本。

注意：BGE-M3 的向量维度必须由实际模型输出确认，不能在代码中凭经验写死。

## 3.5 模型资源管理

建议初始配置：

```env
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:4b

EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=BAAI/bge-m3

RAG_TOP_K=5
RAG_SCORE_THRESHOLD=0.35
RAG_MAX_CONTEXT_CHARS=10000

OLLAMA_NUM_CTX=4096
OLLAMA_NUM_PARALLEL=1
```

上述阈值只是初始值，必须通过实际检索评估调整。

不得将相似度阈值直接当成通用的准确率指标。

---

# 4. 系统架构与代码目录

Agent 必须先检查当前项目目录，再按照现有结构增量添加文件。

以下为建议结构，不要求机械地重建现有项目：

```text
project/
│
├── app/
│   ├── routes/
│   │   ├── ai_routes.py
│   │   ├── knowledge_routes.py
│   │   └── recall_routes.py
│   │
│   ├── services/
│   │   ├── ai/
│   │   │   ├── llm_provider.py
│   │   │   ├── ollama_provider.py
│   │   │   ├── siliconflow_provider.py
│   │   │   └── prompt_builder.py
│   │   │
│   │   ├── rag/
│   │   │   ├── document_loader.py
│   │   │   ├── document_cleaner.py
│   │   │   ├── document_splitter.py
│   │   │   ├── embedding_service.py
│   │   │   ├── vector_store.py
│   │   │   ├── chroma_store.py
│   │   │   ├── retriever.py
│   │   │   ├── evidence_validator.py
│   │   │   └── rag_pipeline.py
│   │   │
│   │   └── recall/
│   │       ├── recall_generator.py
│   │       ├── recall_schema.py
│   │       └── recall_repository.py
│   │
│   ├── models/
│   │   ├── knowledge_document.py
│   │   └── recall_strategy.py
│   │
│   └── config/
│       └── ai_config.py
│
├── data/
│   ├── chroma/
│   ├── knowledge/
│   └── models/
│
├── scripts/
│   ├── import_knowledge.py
│   ├── evaluate_retrieval.py
│   └── rebuild_index.py
│
├── tests/
│   ├── test_rag_pipeline.py
│   ├── test_retriever.py
│   ├── test_evidence_validator.py
│   └── test_recall_generator.py
│
├── docs/
│   └── PHASE6_AI_RAG_REPORT.md
│
└── PHASE6_AI_RAG_IMPLEMENTATION.md
```

---

# 5. 知识库设计

## 5.1 知识库数据范围

第一阶段只接入与游戏运营和玩家流失分析相关的公开资料。

包括：

| 类型 | 内容示例 | 用途 |
|---|---|---|
| 游戏公告 | Steam 官方公告 | 了解官方运营动态 |
| 更新补丁 | Patch Notes | 识别版本更新影响 |
| FAQ | 常见问题 | 解决玩家反馈问题 |
| 游戏评价 | 公开评论中的典型问题 | 提取群体情绪和痛点 |
| 运营案例 | 游戏活动、福利和召回案例 | 提供策略参考 |
| 活动资料 | 官方公开活动信息 | 制定活动建议 |

第一阶段不要求自动爬取所有来源。

优先支持本地文件导入和手动维护。

## 5.2 支持的文件格式

优先支持：

- TXT
- Markdown
- PDF
- DOCX
- HTML

其他格式按实际项目需要增加。

文档解析失败时必须返回错误信息，不允许静默跳过。

对于扫描版 PDF，应明确提示可能需要 OCR，不得假设普通 PDF 文本解析可以识别图片文字。

## 5.3 文档数据结构

每份知识文档必须包含：

```json
{
  "document_id": "doc_001",
  "title": "Game Update 1.2.0",
  "app_id": 123456,
  "source_type": "patch_note",
  "source_name": "Steam Official Announcement",
  "source_url": null,
  "content": "Document content...",
  "publish_date": "2026-01-01",
  "created_at": "2026-01-02T10:00:00",
  "updated_at": "2026-01-02T10:00:00",
  "version": 1,
  "tags": [
    "update",
    "gameplay"
  ],
  "status": "active"
}
```

说明：

- `app_id` 必须用于游戏数据隔离。
- `source_url` 没有真实链接时使用 null。
- `publish_date` 未知时使用 null。
- 不允许编造发布日期。
- `version` 用于文档更新管理。
- `status` 用于控制文档是否参与检索。

## 5.4 Chunk 切分策略

不要直接将整份文档作为一个向量。

建议：

- 中文文本优先按标题、段落和语义边界切分。
- 英文文本按段落和句子切分。
- 保留原始标题和来源。
- 避免将一个完整公告切得过于零碎。

初始参数：

```env
CHUNK_SIZE=500
CHUNK_OVERLAP=80
```

这里的单位需要在实现中明确，建议统一使用字符数。

不同格式可以有不同的切分策略，但必须保持统一的输出结构。

Chunk 示例：

```json
{
  "chunk_id": "chunk_001",
  "document_id": "doc_001",
  "app_id": 123456,
  "chunk_index": 0,
  "title": "Game Update 1.2.0",
  "content": "Update content...",
  "source_type": "patch_note",
  "source_name": "Steam Official Announcement",
  "source_url": null,
  "publish_date": "2026-01-01",
  "tags": ["update", "gameplay"]
}
```

## 5.5 数据去重和更新

必须考虑：

1. 重复文件导入。
2. 同一公告重复抓取。
3. 原文内容更新。
4. 文档删除。
5. 文档重新索引。

建议使用：

- document_id。
- 文件内容 SHA256。
- 文档版本号。
- Chunk ID。
- AppID。

同一文档重复导入时，应识别内容哈希。

内容未变化时，不重复生成 Embedding。

内容变化时，应删除旧 Chunk 并重新索引。

---

# 6. RAG 检索系统

## 6.1 第一阶段检索架构

第一阶段只实现：

```text
用户查询
   |
   v
查询预处理
   |
   v
BGE-M3 Embedding
   |
   v
ChromaDB 向量检索
   |
   v
AppID Metadata Filter
   |
   v
Top-K 检索结果
   |
   v
证据质量过滤
   |
   v
构建 RAG Context
```

初始：

```env
RAG_TOP_K=5
RAG_MAX_CONTEXT_CHARS=10000
```

需要记录：

- 查询文本。
- 查询 AppID。
- 检索到的 Chunk。
- 相似度或距离。
- 检索耗时。
- 过滤原因。
- 最终使用的证据。

## 6.2 AppID 隔离

这是必须实现的核心逻辑。

例如：

```python
results = vector_store.search(
    query=query,
    filters={
        "app_id": 123456,
        "status": "active"
    },
    top_k=5
)
```

禁止在检索后才简单地过滤其他游戏的结果。

必须尽可能在向量检索阶段使用 Metadata Filter。

如果某个知识文档没有 AppID，需要通过明确的全局文档策略处理，不允许默认将它当作所有游戏的资料。

建议区分：

```text
game_specific
global
```

只有显式标记为 `global` 的通用知识才可以跨游戏检索。

## 6.3 检索结果结构

每个检索结果应包含：

```json
{
  "chunk_id": "chunk_001",
  "document_id": "doc_001",
  "app_id": 123456,
  "title": "Game Update 1.2.0",
  "content": "Relevant content...",
  "source_type": "patch_note",
  "source_name": "Steam Official Announcement",
  "source_url": null,
  "publish_date": "2026-01-01",
  "score": 0.82
}
```

注意：

- `score` 的方向和含义必须与底层 ChromaDB 距离度量保持一致。
- 不得把距离值直接当成相似度展示。
- 如果需要相似度转换，必须明确转换方法。
- 不同检索器的分数不能未经校准直接比较。

## 6.4 第一阶段不强制引入 Reranker

先使用 BGE-M3 + ChromaDB 完成基本检索。

不要在第一天就引入：

- BGE Reranker。
- BM25。
- 多路检索。
- 复杂 RRF 融合。
- 多级 Agent 检索。

原因：

- 先验证知识库质量。
- 先验证 Chunk 切分是否合理。
- 先验证 AppID 过滤是否正确。
- 减少本地资源压力。
- 避免增加无必要的调试成本。

## 6.5 第二阶段：Hybrid Search

只有在基础检索评估发现明显问题时，才实施混合检索。

推荐：

```text
                  用户查询
                     |
          +----------+----------+
          |                     |
          v                     v
      Dense Search          BM25 Search
       ChromaDB              SQLite FTS5
          |                     |
          +----------+----------+
                     |
                     v
                 RRF 融合
                     |
                     v
              可选 Reranker
                     |
                     v
               最终 Top-K
```

可采用 Reciprocal Rank Fusion：

\[
RRF(d)=\sum_{r \in R}\frac{1}{k+rank_r(d)}
\]

其中：

- \(d\) 为候选文档。
- \(R\) 为检索结果集合。
- \(rank_r(d)\) 为文档在某路检索中的排名。
- \(k\) 为平滑参数。

第一阶段不需要实现此模块。

## 6.6 第二阶段：Reranker

可选：

```text
BAAI/bge-reranker-v2-m3
```

建议：

- 初始候选 Top 20–30。
- 重排序后取 Top 5。
- 通过评估决定是否使用。
- 优先 CPU 运行或与 LLM 分时运行。
- 不允许无条件常驻 GPU。

只有当 Reranker 明显改善 Recall@K、相关性或证据质量时才保留。

---

# 7. 检索质量评估

这一部分是本次实施中必须新增的工作。

不能因为向量数据库能够返回结果，就认为 RAG 已经完成。

## 7.1 建立测试问题集

至少准备 20–30 个测试问题。

建议覆盖：

| 类型 | 示例 |
|---|---|
| 补丁查询 | 最近一次更新修复了什么问题？ |
| 活动查询 | 游戏最近有哪些公开活动？ |
| 玩家反馈 | 玩家最集中抱怨的问题是什么？ |
| 情绪分析 | 玩家对最近版本的主要负面情绪是什么？ |
| 运营策略 | 如何针对近期活跃度下降设计活动？ |
| 游戏隔离 | 不同 AppID 的知识是否会互相污染？ |
| 无答案问题 | 知识库没有相关内容时是否正确拒答？ |
| 时间问题 | 旧公告是否会错误覆盖新公告？ |

## 7.2 人工标注相关文档

为每个测试问题准备：

- 问题。
- 对应 AppID。
- 预期相关文档。
- 预期相关 Chunk。
- 是否允许回答。
- 必须引用的关键证据。

不要让模型自己生成标准答案后再用自己的答案评价自己。

## 7.3 评估指标

第一阶段至少检查：

| 指标 | 说明 |
|---|---|
| Recall@5 | 前 5 个结果中是否找到相关证据 |
| Precision@5 | 前 5 个结果中相关结果的比例 |
| MRR | 第一个相关结果的排名表现 |
| AppID Isolation | 是否检索到其他游戏的内容 |
| Evidence Coverage | 生成答案是否有证据支持 |
| Refusal Accuracy | 无证据时是否正确拒答 |

初始验收建议：

- AppID 隔离测试必须全部通过。
- 明确无答案的问题必须能够拒答。
- 关键测试问题的 Recall@5 应达到可接受水平，目标先设为 0.80。
- 引用来源必须能够回溯到真实文档。
- 对错误检索案例进行分类分析。

Recall@5 的目标是工程验收目标，不代表实际检索质量已经达到该数值。

如果没有足够测试资料，不允许通过虚构测试结果宣称达标。

---

# 8. LLM Provider 抽象

## 8.1 设计目标

保留现有 SiliconFlow 接入能力，同时新增 Ollama。

业务逻辑不能直接依赖某个供应商的 SDK。

建议接口：

```python
class LLMProvider:

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.2,
        max_tokens: int = 2048
    ):
        raise NotImplementedError

    def health_check(self):
        raise NotImplementedError
```

实现：

```text
LLMProvider
    |
    +-- OllamaProvider
    |
    +-- SiliconFlowProvider
```

通过环境变量选择：

```env
LLM_PROVIDER=ollama
```

## 8.2 API Key 管理

SiliconFlow API Key 只能保存在服务端环境变量中。

示例：

```env
SILICONFLOW_API_KEY=your_api_key
```

严禁：

- 在前端 JS 中写入 API Key。
- 将 API Key 返回给浏览器。
- 将 API Key 写入 Git。
- 在日志中打印完整 API Key。

如果原有前端使用 localStorage 保存 API Key，需要迁移至后端配置，并检查历史代码中是否还有暴露风险。

---

# 9. AI 召回策略生成

## 9.1 生成流程

```text
用户选择游戏
      |
      v
获取 AppID
      |
      v
读取 SQLite 动态分析指标
      |
      v
构造分析上下文
      |
      v
RAG 检索相关知识
      |
      v
Evidence Validator
      |
      v
构造 Prompt
      |
      v
Ollama / SiliconFlow
      |
      v
JSON 结构校验
      |
      v
证据引用校验
      |
      v
保存召回策略
      |
      v
前端展示
```

## 9.2 动态数据和知识证据必须分开

这是核心要求。

### 动态业务数据

来自当前项目 SQLite 和已有分析逻辑：

- CCU 变化。
- 玩家评论数量。
- 好评率变化。
- 差评比例。
- 评论情绪。
- 玩家反馈主题。
- 公开成就数据。
- 现有流失风险指标。

这些数据不能通过 RAG 虚构。

### 知识库证据

来自：

- 官方公告。
- 补丁说明。
- FAQ。
- 游戏运营案例。
- 公开活动资料。

这些数据用于解释问题、提供背景和支持策略建议。

### AI 推理

模型基于上述两类输入提出：

- 可能的问题原因。
- 运营干预建议。
- 活动方案。
- 内容优化建议。
- 后续监测指标。

三者必须在结果中明确区分。

## 9.3 召回策略输出结构

建议统一 JSON Schema：

```json
{
  "app_id": 123456,
  "game_name": "Example Game",
  "generated_at": "2026-01-01T12:00:00",
  "execution_status": "suggested",
  "summary": "群体级玩家流失风险分析",
  "risk_analysis": {
    "risk_level": "high",
    "observed_signals": [
      {
        "metric": "negative_review_ratio",
        "value": 0.42,
        "source": "sqlite"
      }
    ],
    "possible_causes": [
      "近期更新可能影响玩家体验"
    ]
  },
  "data_evidence": [
    {
      "name": "negative_review_ratio",
      "value": 0.42,
      "description": "近期差评比例",
      "source": "sqlite"
    }
  ],
  "knowledge_evidence": [
    {
      "document_id": "doc_001",
      "chunk_id": "chunk_001",
      "title": "Game Update 1.2.0",
      "source_name": "Steam Official Announcement",
      "source_url": null,
      "claim": "公告中说明了相关更新内容"
    }
  ],
  "ai_inference": [
    {
      "reasoning": "近期负面评价与更新内容存在时间上的关联",
      "confidence": "medium",
      "limitations": "仅为相关性观察，不能证明因果关系"
    }
  ],
  "recall_actions": [
    {
      "name": "优化新版本问题反馈引导",
      "target_group": "近期公开评论中表达相关问题的玩家群体",
      "objective": "改善玩家对更新问题的认知与反馈体验",
      "action_type": "community_operation",
      "priority": "high",
      "description": "针对集中出现的问题制定说明和沟通方案",
      "expected_effect": "降低相关负面反馈的持续增长",
      "measurement": [
        "相关负面评论比例",
        "评论情绪变化",
        "CCU 变化"
      ]
    }
  ],
  "limitations": [
    "本方案基于公开数据，不具备个人玩家身份识别能力"
  ]
}
```

注意：

- 示例中的数值只用于说明数据结构。
- 实际生成时必须使用真实查询结果。
- 无法获得的数据必须为 null 或不输出。
- 不允许模型自行编造业务指标。
- 不允许将 AI 推理包装成确定事实。
- `execution_status` 第一阶段固定为 `suggested`。

## 9.4 Prompt 设计

System Prompt 必须明确：

1. 你是游戏运营分析助手。
2. 只能根据提供的数据和证据进行分析。
3. 不得编造数据、公告、玩家反馈或来源。
4. 必须区分事实、推测和建议。
5. 必须优先引用与当前 AppID 对应的知识。
6. 如果证据不足，必须说明不足。
7. 不得声称已经执行召回。
8. 不得输出具体玩家身份或私人信息。
9. 输出必须符合指定 JSON Schema。

建议将 Prompt 模板独立保存，不要在路由函数中拼接大量字符串。

## 9.5 输出校验

模型输出不能直接返回前端。

必须执行：

1. JSON 解析。
2. Schema 校验。
3. AppID 校验。
4. 数据来源校验。
5. Chunk ID 校验。
6. 证据文档校验。
7. 执行状态校验。

如果模型输出无效：

- 最多进行一次结构修复尝试。
- 修复失败返回明确错误。
- 不允许将不合法 JSON 直接作为成功结果保存。

---

# 10. SQLite 数据表

Agent 必须先检查当前数据库结构。

以下是建议新增的数据表，不得覆盖现有业务表。

## 10.1 knowledge_documents

```sql
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT UNIQUE NOT NULL,
    app_id INTEGER,
    scope TEXT NOT NULL DEFAULT 'game_specific',
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_name TEXT,
    source_url TEXT,
    file_name TEXT,
    content_hash TEXT NOT NULL,
    publish_date TEXT,
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

## 10.2 knowledge_chunks

```sql
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id TEXT UNIQUE NOT NULL,
    document_id TEXT NOT NULL,
    app_id INTEGER,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id)
        REFERENCES knowledge_documents(document_id)
);
```

注意：SQLite 元数据表与 ChromaDB 中的向量数据需要保持一致。

## 10.3 recall_strategies

```sql
CREATE TABLE IF NOT EXISTS recall_strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT UNIQUE NOT NULL,
    app_id INTEGER NOT NULL,
    game_name TEXT,
    status TEXT NOT NULL DEFAULT 'suggested',
    summary TEXT,
    result_json TEXT NOT NULL,
    llm_provider TEXT,
    llm_model TEXT,
    embedding_model TEXT,
    created_at TEXT NOT NULL
);
```

## 10.4 rag_evaluation_runs

建议保存检索评估记录：

```sql
CREATE TABLE IF NOT EXISTS rag_evaluation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id TEXT UNIQUE NOT NULL,
    test_set_version TEXT,
    total_questions INTEGER NOT NULL,
    recall_at_5 REAL,
    precision_at_5 REAL,
    mrr REAL,
    appid_isolation_pass INTEGER,
    refusal_accuracy REAL,
    created_at TEXT NOT NULL,
    details_json TEXT
);
```

---

# 11. API 设计

API 路径需要先检查现有命名习惯，再决定是否调整前缀。

以下为建议接口。

## 11.1 AI 服务健康检查

```http
GET /api/ai/health
```

返回：

```json
{
  "status": "ok",
  "llm_provider": "ollama",
  "llm_available": true,
  "embedding_available": true,
  "vector_store_available": true
}
```

如果某个组件不可用，必须明确返回对应状态。

## 11.2 知识库文档导入

```http
POST /api/knowledge/import
```

支持文件上传和必要的元数据。

## 11.3 知识库列表

```http
GET /api/knowledge/documents
```

支持：

- AppID。
- 文档类型。
- 状态。
- 分页。

## 11.4 知识库文档删除

```http
DELETE /api/knowledge/documents/{document_id}
```

必须同时处理：

- SQLite 元数据。
- Chunk 元数据。
- ChromaDB 向量记录。

删除失败时必须记录状态，避免产生数据不一致。

## 11.5 知识库检索测试

```http
POST /api/knowledge/search
```

请求：

```json
{
  "app_id": 123456,
  "query": "最近更新修复了哪些问题？",
  "top_k": 5
}
```

返回真实检索结果和来源。

不得使用模拟数据。

## 11.6 生成召回策略

```http
POST /api/recall/generate
```

请求：

```json
{
  "app_id": 123456,
  "analysis_period": "7d"
}
```

返回：

- 真实业务指标。
- 知识库证据。
- AI 推理。
- 召回建议。
- 执行状态。

## 11.7 策略历史

```http
GET /api/recall/history?app_id=123456
```

## 11.8 策略详情

```http
GET /api/recall/{strategy_id}
```

---

# 12. 前端改造

## 12.1 改造原则

保留当前项目页面风格和组件体系。

不要为了 AI 功能重新制作整个网站。

前端必须通过 Flask API 获取真实数据。

不得在 JS 中硬编码模型生成结果。

## 12.2 AI 召回策略页面

建议展示：

### A. 风险概览

- 当前风险等级。
- 主要风险信号。
- 近期变化。
- 分析时间范围。

### B. 数据证据

- CCU 趋势。
- 评论情绪。
- 差评比例。
- 主要负面反馈主题。

### C. 知识库证据

- 公告标题。
- 补丁说明。
- 来源。
- 发布时间。
- 被引用的具体内容。

### D. AI 召回建议

- 策略名称。
- 目标群体。
- 策略目标。
- 具体措施。
- 优先级。
- 预期效果。
- 评估指标。

### E. 风险提示

清晰显示：

- 这是 AI 生成的建议。
- 不是实际执行记录。
- 数据来自公开信息。
- 不具备个人玩家精准识别能力。

## 12.3 交互要求

- 点击生成后展示加载状态。
- 请求失败显示错误原因。
- 不允许失败后展示旧的模拟成功结果。
- 策略生成成功后支持查看详情。
- 历史策略支持查看。
- 知识库检索结果支持查看来源。
- 没有证据时明确显示证据不足。
- 不允许把空数据展示成 0 或成功状态。

---

# 13. Docker 部署

## 13.1 服务划分

建议 Docker Compose 继续运行：

```text
Flask
SQLite
ChromaDB 持久化目录
```

Ollama 在 Windows 宿主机运行。

如果当前项目已经将 ChromaDB 嵌入 Flask 进程运行，不必为了本阶段额外启动独立 ChromaDB 服务。

优先采用 ChromaDB PersistentClient。

## 13.2 持久化目录

确保：

```text
data/
    chroma/
    knowledge/
    models/
```

不会因为 Docker 容器重建而丢失。

## 13.3 环境变量

新增：

```env
# LLM
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3:4b

# Embedding
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=cpu

# Vector Store
VECTOR_STORE=chroma
CHROMA_PERSIST_DIRECTORY=/app/data/chroma

# RAG
RAG_TOP_K=5
RAG_SCORE_THRESHOLD=0.35
RAG_MAX_CONTEXT_CHARS=10000

# Chunk
CHUNK_SIZE=500
CHUNK_OVERLAP=80
```

以上路径需要根据现有 Docker 工作目录适配。

不要覆盖项目现有环境变量。

## 13.4 Docker 验收

必须验证：

1. 容器可以访问宿主机 Ollama。
2. Ollama 不可用时 API 返回合理错误。
3. Flask 可以正常连接 SQLite。
4. ChromaDB 数据能够持久化。
5. 容器重启后知识库仍然存在。
6. 文档导入后可以被检索。
7. 召回策略生成接口可正常工作。

---

# 14. 分阶段实施任务

Agent 不允许一次性大规模修改所有模块。

必须按以下顺序执行。

## Phase 6.1：项目审查与架构确认

任务：

- 阅读现有项目结构。
- 确认数据库。
- 确认 AI 模块。
- 确认 Docker。
- 确认前端接口。
- 输出实施计划。

交付：

```text
PHASE6_ARCHITECTURE_AUDIT.md
```

验收：

- 不改变现有项目技术栈。
- 所有修改点有明确说明。
- 找出可能影响现有功能的地方。

## Phase 6.2：本地模型与 Provider

任务：

- 新增 Ollama Provider。
- 保留 SiliconFlow Provider。
- 实现 Provider 抽象。
- 增加模型健康检查。
- 配置环境变量。
- 处理超时和异常。

验收：

- Ollama 可以正常生成文本。
- SiliconFlow 原有能力不受影响。
- API Key 不暴露在前端。
- 模型不可用时返回明确错误。

## Phase 6.3：知识库与基础 RAG

任务：

- 实现文档解析。
- 实现文档清洗。
- 实现 Chunk 切分。
- 实现 BGE-M3 Embedding。
- 实现 ChromaDB VectorStore。
- 实现 AppID Metadata Filter。
- 实现文档来源追踪。
- 实现文档更新和删除。

验收：

- 能够导入真实文件。
- 能够完成向量化。
- 能够检索真实知识。
- 能够正确过滤 AppID。
- 能够返回真实来源。
- Docker 重启后数据仍然存在。

## Phase 6.4：检索评估

任务：

- 创建 20–30 个检索问题。
- 人工标注相关证据。
- 编写检索评估脚本。
- 统计 Recall@5、Precision@5、MRR。
- 检查 AppID 隔离。
- 检查无答案拒答。
- 分析失败案例。

验收：

- 有可重复运行的评估脚本。
- 有真实评估结果。
- 有错误案例分析。
- 不得用虚构数据填充结果。

如果检索质量不足，优先调整文档清洗、Chunk 切分、Metadata 和查询预处理。

不要直接通过增加模型参数掩盖检索质量问题。

## Phase 6.5：AI 召回策略生成

任务：

- 接入现有动态业务数据。
- 接入 RAG 检索。
- 构造 Prompt。
- 实现 JSON Schema。
- 实现证据校验。
- 实现召回策略保存。
- 实现历史查询。

验收：

- 业务数据来自真实 SQLite 查询。
- 知识证据来自真实检索结果。
- AI 推理与事实分离。
- 无证据时明确提示。
- 输出符合 JSON Schema。
- 不声称策略已经执行。

## Phase 6.6：前端与整体联调

任务：

- 改造 AI 召回页面。
- 接入真实 API。
- 展示数据证据。
- 展示知识库证据。
- 展示 AI 推理。
- 展示召回建议。
- 展示历史策略。
- 完成错误状态和加载状态。

验收：

- 页面正常展示真实数据。
- 不存在虚构的成功结果。
- 能够查看策略历史。
- 能够追溯证据来源。
- 原有页面功能不受影响。

---

# 15. 第二阶段优化（本次不强制实现）

基础 RAG 通过验收后，再考虑以下优化。

## 15.1 Hybrid Search

引入 SQLite FTS5 或 BM25。

适合：

- 精确查询游戏名称。
- 补丁版本号。
- 特定活动名称。
- 专有名词。
- 数字和日期。

## 15.2 Reranker

引入 BGE Reranker v2-m3。

适合：

- 候选结果较多。
- 相似度检索结果排序不理想。
- 语义相似但实际不相关的文档较多。

## 15.3 更换向量数据库

当数据量、并发量和检索复杂度明显增加时，可以评估 Qdrant。

但必须先确认：

- 是否存在实际性能瓶颈。
- 是否需要独立向量服务。
- 是否需要更复杂的过滤能力。
- 是否值得增加部署复杂度。

不要为了使用新技术而替换当前方案。

---

# 16. 测试要求

## 16.1 单元测试

至少覆盖：

- 文档解析。
- Chunk 切分。
- 内容哈希。
- Embedding 输出。
- ChromaDB 插入。
- ChromaDB 查询。
- AppID 过滤。
- 文档删除。
- Provider 调用。
- JSON Schema 校验。
- 证据引用校验。

## 16.2 集成测试

至少覆盖：

1. 导入一个真实 Markdown 文件。
2. 导入一个真实 PDF 文件。
3. 检索该文件内容。
4. 使用另一个 AppID 检索。
5. 检查跨游戏污染。
6. 生成一份召回策略。
7. 保存策略。
8. 查询策略历史。
9. 删除知识文档。
10. 验证删除后的检索结果。

## 16.3 回归测试

必须验证原有：

- Steam 数据采集。
- 评论分析。
- CCU 分析。
- 玩家流失分析。
- 原有 AI 面板。
- 现有图表。
- 页面路由。
- Docker 启动流程。

新增功能不能破坏现有功能。

---

# 17. 安全性与可靠性

## 17.1 文件上传

- 限制允许的文件类型。
- 限制文件大小。
- 校验文件内容。
- 防止路径穿越。
- 避免直接执行上传内容。
- 对解析失败进行异常处理。

## 17.2 Prompt Injection

知识库内容视为不可信数据。

即使公告、文档或用户上传文件中包含类似指令，也不能覆盖 System Prompt。

要求：

- 文档内容只能作为证据。
- 不允许文档指令改变系统行为。
- 不能因为知识库内容而泄露环境变量。
- 不能让模型执行知识库中的代码或命令。

## 17.3 日志

记录：

- 请求时间。
- AppID。
- 模型名称。
- 检索耗时。
- 生成耗时。
- 错误类型。
- 策略 ID。

不得记录：

- 完整 API Key。
- 用户敏感信息。
- 不必要的环境变量。
- 私人账号信息。

---

# 18. 最终交付物

Agent 完成后必须交付：

| 文件 / 模块 | 内容 |
|---|---|
| RAG 服务 | 文档解析、切分、Embedding、检索 |
| ChromaDB 集成 | 本地持久化向量存储 |
| Ollama Provider | 本地模型调用 |
| SiliconFlow Provider | 保留原有外部模型能力 |
| 知识库 API | 导入、查询、删除 |
| 召回策略 API | 生成、详情、历史 |
| 数据库迁移 | 新增所需表结构 |
| 前端页面 | AI 召回策略真实数据展示 |
| 自动化测试 | 单元测试与集成测试 |
| 检索评估集 | 20–30 个问题及标注 |
| 评估脚本 | Recall@5 等指标 |
| 部署配置 | Docker 与环境变量 |
| 实施报告 | `PHASE6_AI_RAG_REPORT.md` |

---

# 19. 最终验收清单

## 基础架构

- [ ] 保留 Flask。
- [ ] 保留 SQLite。
- [ ] 保留原有前端技术栈。
- [ ] Ollama 可以在 Windows 宿主机运行。
- [ ] Docker 可以访问 Ollama。
- [ ] ChromaDB 数据持久化正常。

## 知识库

- [ ] 支持 TXT、Markdown、PDF、DOCX 等约定格式。
- [ ] 文档可以正常解析。
- [ ] Chunk 切分正常。
- [ ] Embedding 正常。
- [ ] 支持文档去重。
- [ ] 支持文档更新。
- [ ] 支持文档删除。
- [ ] 支持 AppID 隔离。
- [ ] 可以追溯来源。

## RAG

- [ ] 可以检索真实知识。
- [ ] 能够返回真实来源。
- [ ] 无答案时能够拒答。
- [ ] 有检索评估集。
- [ ] 有 Recall@5 等评估结果。
- [ ] 没有未经验证的性能承诺。

## AI 召回策略

- [ ] 使用真实业务数据。
- [ ] 使用真实知识库证据。
- [ ] 区分事实与推理。
- [ ] 输出符合 JSON Schema。
- [ ] 证据引用通过校验。
- [ ] 策略能够保存。
- [ ] 策略历史能够查询。
- [ ] 状态明确为 suggested。
- [ ] 不伪装成已执行召回。

## 前端

- [ ] 接入真实 API。
- [ ] 展示动态数据证据。
- [ ] 展示知识库证据。
- [ ] 展示 AI 推理。
- [ ] 展示召回建议。
- [ ] 支持历史记录。
- [ ] 有加载状态。
- [ ] 有错误状态。
- [ ] 无虚构结果。

## 回归与交付

- [ ] 原有功能正常。
- [ ] Docker 部署正常。
- [ ] 自动化测试通过。
- [ ] 检索评估报告完成。
- [ ] 实施报告完成。
- [ ] 未修改 test 分支。
- [ ] 未擅自发布或合并 main。

---

# 20. Agent 最终报告要求

完成后生成：

`PHASE6_AI_RAG_REPORT.md`

报告必须包含：

1. 项目原有架构分析。
2. 新增模块与目录。
3. 最终技术选型。
4. Ollama 部署方式。
5. ChromaDB 配置。
6. BGE-M3 配置。
7. 知识库数据结构。
8. RAG 检索流程。
9. AppID 隔离实现。
10. 检索评估结果。
11. AI 召回策略生成流程。
12. API 接口说明。
13. 前端修改说明。
14. Docker 部署说明。
15. 自动化测试结果。
16. 已知问题。
17. 尚未实现的功能。
18. 后续优化建议。

**重要：报告必须区分已实现、已测试、未测试和未实现的内容。不得将计划中的功能描述成已经完成。**

---

# 最终技术决策

本阶段的核心方案确定为：

```text
Flask
  |
  +-- SQLite
  |
  +-- 自建轻量 RAG Pipeline
  |      |
  |      +-- Document Loader
  |      +-- Document Splitter
  |      +-- BGE-M3 Embedding
  |      +-- ChromaDB
  |      +-- Evidence Validator
  |
  +-- LLM Provider
  |      |
  |      +-- Ollama + Qwen3 4B
  |      +-- SiliconFlow
  |
  +-- Recall Strategy Generator
         |
         +-- Dynamic Data
         +-- Knowledge Evidence
         +-- AI Inference
         +-- Suggested Recall Actions
```

第一阶段：

**自建轻量 RAG + ChromaDB + BGE-M3 + Ollama。**

第二阶段：

**根据检索评估结果，再决定是否加入 SQLite FTS5 / BM25、RRF 和 BGE Reranker。**

不要为了追求架构复杂度而提前引入不必要的组件。

本阶段最重要的验收标准不是“能调用大模型”，而是：

**AI 生成的每一项召回建议，都能清楚说明使用了哪些真实业务数据、引用了哪些知识库证据，以及哪些部分只是模型推断。**