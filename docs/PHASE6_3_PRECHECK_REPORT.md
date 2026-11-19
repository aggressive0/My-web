# Phase 6.3 基线报告（Task 0：执行前检查与问题复现）

> 依据：`docs/PHASE6_3_RAG_ABSTENTION_AND_RETRIEVAL_OPTIMIZATION.md.md` §3
> 执行时间：2026-11-18 12:0x（本机）
> 原则：只取证，不改代码。所有数字都来自本机实测，不引用估算。

---

## 0. 结论摘要

| # | 结论 | 影响 |
|---|---|---|
| 1 | 分数语义已确认：`hnsw:space=cosine`，业务层 `score = 1 - distance`，两者和为 1（实测 0.5192 + 0.4808） | 阈值方向正确，不存在「拿距离当相似度」的翻转错误 |
| 2 | 四个失败问题在阈值 0.35 下**全部返回 5 条**，但**没有任何一条包含回答问题所需的关键事实**（20/20 条均无） | 问题不是「阈值太低」，是「主题相关被当成事实可回答」——文档 §1.1 的判断成立 |
| 3 | 可回答问题的最高分 **0.6267**，不可回答问题的最高分 **0.5192**，间隔 0.1075 | 单看这 4 题，0.55 能分开；但 6.3 评测显示答案侧有分数更低的题（recall 0.929→0.737），**单一阈值必然冲突** |
| 4 | 当前「拒答」的唯一判据是 `len(kept) == 0`，等价于「没有任何结果过阈值」 | 这不是拒答机制，是空结果的副产品；需要独立门控 |
| 5 | 无 Reranker、无问题路由、无证据充分性判断、无分级拒答 | 文档提出的四层结构确实是缺失的 |
| 6 | `RAG_SCORE_THRESHOLD` 的过滤发生在 **Python 侧**（`retriever.py`），不在 Chroma 的 `where` 里 | 意味着「阈值」与「AppID 隔离」是两套独立机制，后者才在检索阶段生效 |

---

## 1. 当前 RAG 实际执行流程（逐段对应代码）

```text
用户问题
  │
  ▼
Retriever.search()                    backend/ai/rag/retriever.py
  │  ├─ 空查询检查
  │  ├─ EmbeddingService.encode([query])        BGE-M3，CPU，一次前向
  │  ├─ ChromaVectorStore.search(top_k=5)
  │  │     where = {status=active} AND ({app_id=X} OR {scope=global})
  │  │     返回 distance，换算 score = 1 - distance
  │  ├─ 按 score 降序排序，取前 top_k
  │  ├─ **Python 侧**按 score >= threshold 过滤        ← RAG_SCORE_THRESHOLD 唯一生效点
  │  ├─ 孤儿检查：文档在 SQLite 里是否仍 active
  │  └─ insufficient_evidence = (kept 为空)
  ▼
返回 {hits, stats, timings_ms, insufficient_evidence}
```

关键点：**没有生成环节**。目前没有任何代码把检索结果交给 LLM 生成回答（策略生成模块未实现）。所谓「无答案问题进入最终回答流程」，在当前代码里等价于「检索返回了 5 条记录，调用方无从判断它们能不能回答」。

---

## 2. 组件与参数（实测）

| 项目 | 值 | 来源 |
|---|---|---|
| Embedding 模型 | `BAAI/bge-m3`，dim **1024**，CLS 池化，max_seq_length 8192 | `/api/ai/health` + 实测 |
| Embedding 设备 | CPU（`EMBEDDING_DEVICE=cpu`） | `backend/.env` |
| 单次查询编码 | **150–200 ms** | 6.3 评测产物 |
| 向量库 | ChromaDB 1.5.9，`PersistentClient` | `chroma_store.py` |
| 距离函数 | **cosine**（`collection.metadata = {'hnsw:space': 'cosine'}`） | 本次实测 |
| 分数转换 | `score = 1.0 - distance` | `chroma_store.py:search` |
| 向量检索耗时 | **1–4 ms** | 6.3 评测产物 |
| 索引规模（基线语料） | 882 chunks / 109 文档 | 本次实测 |

分数转换的数值验证（同一 chunk，命中排名 1）：

```text
score = 0.5192    distance = 0.4808    score + distance = 1.0000
```

**没有归一化、截断或二次压缩**。score ∈ [-1, 1]，实践中落在 0.3–0.7。

---

## 3. 四个失败案例的真实检索结果

复现条件：6 款游戏 109 条官方公告（`tests/fixtures/rag_corpus/`）导入临时索引，`top_k=5`，`threshold=0`（不过滤，取原始候选）。开发库 `backend/data/steam.db` 里只有 3 份测试文档，不具备复现条件，故使用评测语料。

### Q1 退款政策（app_id=1203220）

| 排名 | score | distance | 文档标题 | 含「refund/退款」 |
|---|---|---|---|---|
| 1 | 0.5192 | 0.4808 | NARAKA Banned Players List September 15th | 无 |
| 2 | 0.5177 | 0.4823 | NARAKA Banned Players List August 25th | 无 |
| 3 | 0.5169 | 0.4831 | NARAKA Banned Players List September 29th | 无 |
| 4 | 0.5168 | 0.4832 | NARAKA Banned Players List August 18th | 无 |
| 5 | 0.5164 | 0.4836 | NARAKA Banned Players List September 23rd | 无 |

阈值 0.35 保留 **5** 条；阈值 0.55 保留 **0** 条。

为什么分数这么高：封禁名单开头是 "committed to creating a fair and healthy competitive environment"，与「游戏政策/玩家权益」在语义空间里高度接近——**但一个字都没提退款**。这正是「主题相关 ≠ 事实可回答」的教科书案例。

### Q2 团队人数（app_id=230410）

| 排名 | score | 文档标题 | 含人数事实 |
|---|---|---|---|
| 1 | 0.4694 | The Icebind is live now! | 无 |
| 2 | 0.4373 | October in Warframe | 无 |
| 3 | 0.4110 | Devstream 198 Overview | 无 |
| 4 | 0.4091 | TennoGen Sale 2026 | 无 |
| 5 | 0.4058 | PMCF Quest 2026 | 无 |

阈值 0.35 保留 5 条；0.55 保留 0 条。注意「Devstream 198 Overview」提到 "our first of two dedicated Devstreams"——出现了团队主体的指代，但没有任何人数、编制或规模信息。

### Q3 机房位置（app_id=578080）

| 排名 | score | 文档标题 | 含机房/数据中心事实 |
|---|---|---|---|
| 1 | 0.4054 | 2027 PUBG Esports: What's Changing | 无 |
| 2 | 0.3821 | Twisted Minds crowned as PGS 12 Champions | 无 |
| 3 | 0.3800 | Twisted Minds crowned as PGS 12 Champions | 无 |
| 4 | 0.3781 | PGS 11 Survival Stage is LIVE!! | 无 |
| 5 | 0.3728 | Twisted Minds crowned as PGS 12 Champions | 无 |

阈值 0.35 保留 5 条；0.55 保留 0 条。命中的是「赛区划分」（EMEA/美洲/印度），与「机房物理位置」是两件事——**赛区 ≠ 机房**，这是最容易糊弄过去的一类错误。

### Q4 召回活动设计（app_id=1203220）

| 排名 | score | 文档标题 | 含运营方法论 |
|---|---|---|---|
| 1 | 0.4769 | NARAKA Update – October 22nd | 无 |
| 2 | 0.4601 | NARAKA Update – October 22nd | 无 |
| 3 | 0.4572 | NARAKA Update – August 13th | 无 |
| 4 | 0.4565 | NARAKA Update – October 22nd | 无 |
| 5 | 0.4499 | NARAKA Update – September 3rd | 无 |

阈值 0.35 保留 5 条；0.55 保留 0 条。命中的是版本更新公告——里面有「活动」（游戏内事件），但**没有「怎么设计召回活动」的方法论**。

### 对照组：可回答的问题

问题「11 月 5 日更新，维护时间窗口是几点到几点？」（app_id=1203220）：

| 排名 | score | 文档 |
|---|---|---|
| 1 | 0.6267 | NARAKA Update – November 5th |
| 2 | 0.6212 | （同文档另一 chunk） |
| 3 | 0.6112 | （同文档另一 chunk） |
| 4 | 0.6075 | （同文档另一 chunk） |
| 5 | 0.6028 | NARAKA Update – October 22nd |

**可回答最高 0.6267 vs 不可回答最高 0.5192，间隔 0.1075。**

这个间隔说明：在这 4 题上，一个 0.55 的阈值能完美分开。但它同时说明另一件事——**间隔只有 0.1，任何分数波动（换 chunk 大小、换文档长度、换语言）都会让它失效**。6.3 的阈值扫描已经验证了这一点：0.55 时 Recall 从 0.929 掉到 0.814，因为有 3 道可回答题的分数低于 0.55。

---

## 4. `RAG_SCORE_THRESHOLD` 的实际生效位置

| 项目 | 现状 |
|---|---|
| 配置项 | `RAG_SCORE_THRESHOLD`（默认 **0.35**），`backend/ai/config.py` |
| 生效位置 | `backend/ai/rag/retriever.py` 的 `search()`，**Python 侧列表过滤** |
| Chroma 的 `where` 里 | **没有**分数条件（Chroma 也不支持按 distance 过滤） |
| 是否参与排序 | 否，仅过滤 |
| 拒答判据 | `insufficient_evidence = (kept == 0)` |

这条很关键：**阈值不在检索阶段生效**。检索永远先取 `top_k=5`，然后才在 Python 里丢掉低分的。所以在阈值 0.35 下，无答案问题拿到的仍是完整的 5 条候选——只是没人告诉下游「这 5 条不构成答案」。

---

## 5. 当前拒答机制

**没有独立的拒答机制。** 唯一信号是 `insufficient_evidence`（布尔），它等价于「过滤后一条不剩」。这带来两个问题：

1. 只要有任何一条分数 ≥ 0.35，系统就认为「有证据」——哪怕内容是封禁名单而问题问的是退款。
2. 没有任何地方定义「证据不足」和「证据冲突」的区别，也没有分级。

---

## 6. 元数据与隔离（现状）

`Chunk.metadata()` 写入 Chroma 的字段：

```text
document_id, app_id, scope(game_specific|global), chunk_index, title,
source_type, source_name, source_url, publish_date, status, tags, embedding_model
```

- **AppID 过滤**：在 Chroma `where` 里，检索阶段生效：`{$and: [{status:active}, {$or: [{app_id: X}, {scope: global}]}]}`。本次 30 题评测 **0 次越界**。
- **来源类型**：`source_type` 已有（`announcement` 等），可用于 §8.4-C 的来源可靠性分级。
- **日期**：`publish_date` 已存，但**从未参与排序或校验**（6.3 已暴露「最近」类问题 MRR 仅 0.20）。
- **Reranker**：无。
- **问题路由**：无。

---

## 7. 性能基线

| 环节 | 实测 |
|---|---|
| 查询编码（BGE-M3 CPU） | 150–200 ms |
| 向量检索（Chroma，882 chunks） | 1–4 ms |
| 索引构建 | 2.6 chunk/s（882 chunks ≈ 340 s） |
| LLM 单次生成（qwen3:4b，关闭思考） | 0.3–2.4 s |
| 模型冷加载 | 2.1–4.0 s（首次部署、磁盘冷读时可达 ~100 s，一次性） |

如果要加 Reranker（`bge-reranker-v2-m3`，约 2.3 GB），按 CPU 推理估算 20 个候选需要 1.5–3 s/query——**比检索本身慢两个数量级**。这是本阶段最需要权衡的成本项。

---

## 8. 对文档架构的判断（哪里对、哪里有坑）

### 判断正确的部分

1. **职责拆分方向正确**：单一阈值同时承担召回与拒答，必然冲突。实测 0.55 能拦住 4 题，但代价是 3 道可回答题（含 q12 短公告、q23 跨语言）跌破阈值。
2. **「主题相关 ≠ 可回答」被四组数据坐实**：20/20 条候选都不含关键事实，分数却全部 0.37–0.52。
3. **`evidence_ids` 必须来自本次候选**：与 6.2 已实现的 `allowed_chunk_ids` 校验一致。
4. **来源不能代替内容检查**（§8.4-C 的注）：与实测吻合——4 题命中的全是官方公告，来源满分，内容零分。

### 三处必须先解决的问题

**（一）Reranker 不是关键路径，不应排在第一优先。**
四题的 top-5 全是**同一游戏、同一类文档**，Reranker 只能在它们之间重排——重排的结果仍然是「5 条都不含答案」。Reranker 改善的是排序质量，不生产证据。若先做 Reranker，拒答准确率不会从 0% 变成 100%。
**建议顺序：证据门（Task 4）→ 路由（Task 1）→ 候选召回（Task 2）→ Reranker（Task 3，可选）**。文档给的顺序是 1→2→3→4，我建议把 4 提前到 3 之前。

**（二）§8.4-B「关键事实覆盖」目前不可执行。**
文档说「不同问题可能有不同的必要信息，不要对所有问题使用同一份固定字段清单」——这是对的，但没给出**怎么得出每个问题需要哪些事实**。如果不定义，这一层就只能交给 LLM 自由心证，直接违反 §8.6。
可行的落地方式：先按问题类型（人数/时间/地点/政策/方法）映射到「答案形态」（数字+单位 / 日期 / 地名 / 具备政策条款语义的段落 / 步骤或建议），用**形态检测**做第一层，LLM 只做第二层复核。本次四题里，Q1–Q3 用形态检测就能全部拦住（20 条候选里没有一条含数字+人员单位、地名、退款字样）。

**（三）拒答分级需要新的状态码，会影响 API 契约。**
现有接口返回 `success: true` + `insufficient_evidence: bool`。加入 `insufficient / conflicting / out_of_scope / ambiguous` 之后，前端与下游的判定逻辑必须同步改。**这是破坏性变更**，需要明确是对现有 `/api/knowledge/search` 扩展字段（向后兼容）还是新增接口。

---

## 9. 建议修改的文件

| 文件 | 改动 | 优先级 |
|---|---|---|
| `backend/ai/rag/evidence_gate.py` | 新建：证据充分性门控（第一层规则 + 第二层可选 LLM） | 高 |
| `backend/ai/rag/intent.py` | 新建：问题路由（规则优先，LLM 兜底） | 高 |
| `backend/ai/rag/retriever.py` | 拆分「召回阈值」与「证据门」；召回阈值下调、不再独自决定拒答 | 高 |
| `backend/ai/rag/service.py` | `search()` 接入路由与门控，返回分级状态 | 高 |
| `backend/ai/config.py` | 新增 `RAG_RECALL_THRESHOLD`（候选召回）、保留 `RAG_SCORE_THRESHOLD` 作兼容别名 | 中 |
| `tests/fixtures/rag_questions.json` | 扩充到覆盖 SQL 类、策略类、多文档证据类（现有 30 题里 14 道是 patch） | 中 |
| `scripts/evaluate_retrieval.py` | 增加拒答准确率、分级分布、证据覆盖指标 | 中 |
| `backend/ai/rag/reranker.py` | 新建（**可选，最后做**） | 低 |
| `backend/routes/knowledge_routes.py` | 搜索接口增加 `abstention` / `evidence_status` 字段 | 依赖 §8（三）的决策 |

---

## 10. 尚未确认的问题（需要拍板）

1. **阈值拆分口径**：`RAG_SCORE_THRESHOLD=0.35` 是保留为「召回阈值」（下调到 0.25–0.30）并新增独立的证据判定，还是把它直接改名为召回阈值、彻底取消它的拒答职责？
2. **是否引入 Reranker**：2.3 GB 模型 + 1.5–3 s/query 的延迟。在四题全部失败的情况下，它对拒答准确率的贡献接近 0。
3. **拒答分级的 API 契约**：扩展现有字段（兼容）还是新增 `/api/knowledge/answer` 接口（干净但重复）？
4. **评测集扩充范围**：现有 30 题是否足够支撑「SQL 类 / 策略类」的验收？如果不够，需要先补题还是先做功能？

---

## 11. 本次 Task 0 没有做的事

- 没有修改任何代码、配置或数据库
- 没有引入 Reranker 或任何新模型
- 没有改动 `RAG_SCORE_THRESHOLD`（仍是 0.35）
- 没有跑生成环节（当前代码里不存在）
