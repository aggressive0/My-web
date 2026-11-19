---
schema_version: 2
updated: "2026-11-17T20:45:00+08:00"
git:
  branch: dev
  head: 6e89dbb
  dirty: true
  scope: "用户未提交：删除 docs/DATA_METRIC_DEFINITIONS.md、docs/DATA_SOURCE_AUDIT.md；writing-block.md 移入 docs/。backend/.env 未跟踪（.gitignore 第 15 行已忽略）"
captured: reconstructed
tasks:
  - id: TASK-001
    name: Phase 6 AI 召回（6.1 模型接入 / 6.2 RAG / 6.3 检索评估均已验收，6.4 策略生成未开始）
    status: active
---

# 1. Task & user intent

在 Steam 公开数据基础上做「热点情感 → 群体流失风险 → AI 召回建议」的可追溯 Demo。
Phase 1–5 已完成并上线；Phase 6.1（模型接入）**已在本机真实验收**，6.2（RAG）未开始。

> 「先处理 HANDOFF 里那条阻塞，完成6.1」 — user, 2026-11-17（本次会话）

> 「不得擅自执行发布、合并或推送到 main」 — `writing-block.md` §1.2-11（合并 #37 的授权已在上一轮用掉）

# 2. User-rejected approaches

- ❌ 逐条评论调 LLM 做情感分类 — 否决 2026-11-16：成本高、不可复现、CI 跑不稳；改选 MiniLM 句向量 + 逻辑回归分类头
- ❌ 引入 RAGFlow / Dify 作为 RAG 平台 — 否决 2026-11-17：ES+MySQL 常驻内存 ≥6–8GB，16GB 机器还要跑 Qwen3 4B + BGE-M3，会 OOM；且违反「不引入第二套后端」

# 3. Completed / claims

- [verified] 测试 122 项通过 → `tests/`；evidence: `python -m unittest discover -s tests -p "test_*.py"` → `Ran 122 tests ... OK`；scope: 8cba847
- [verified] 冒烟 92 项通过 → `tests/smoke.mjs`；evidence: `node tests/smoke.mjs` → `共 92 项，失败 0 项`；scope: 8cba847
- [verified] PR #37 已合并、Pages 部署成功 → main `92c4e60`；evidence: `gh api repos/aggressive0/My-web/commits/main`；deploy run 44078077471 success；scope: 2026-11-17T11:53Z
- [verified] data 分支 9 个数据文件由 CI 维护 → evidence: `gh api "repos/.../contents?ref=data" --jq length` → `9`；scope: 2026-11-17T19:56+08:00
- [reported] 线上四页真实数据面板（KPI/情感/主题/风险/时长分布）已上线；evidence: 2026-11-17 线上浏览器探测，未在本次 WRITE 重跑
- [reported] Phase 6.1 交付 8 个文件（config / 5 个 llm / routes / tests）；evidence: commit 8cba847
- [verified] Ollama 0.40.16 portable 部署 → `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`；evidence: 1,618,244,995 B 分块下载 65 块尺寸全对，解压 75 文件，`GET /api/tags` → `{"models":[]}`；scope: 2026-11-17T20:26+08:00
- [verified] qwen3:4b 已安装 → evidence: `ollama list` → `qwen3:4b 359d7dd4bcda 2.5 GB`；scope: 2026-11-17T20:33+08:00
- [verified] 6.1 验收：`POST /api/ai/llm/ping -d '{}'` → 200 `json_valid=true`、`provider=ollama`、`model=qwen3:4b`、`duration_ms=5581`；`GET /api/ai/health` → `llm.available=true`、`installed=["qwen3:4b"]`、`config_problems=[]`；scope: 127.0.0.1:5000
- [verified] 失败路径不伪装：杀掉 ollama 后 ping → 503 `LLM_UNAVAILABLE`，health → `available=false`（WinError 10061）；scope: 2026-11-17T20:36+08:00
- [verified] 回归 122 项通过 → evidence: `python -m unittest discover -s tests` → `Ran 122 tests ... OK`（14.2s）；scope: 改造后
- [verified] 内部地址泄漏已修：`LLMUnavailable.message` 与 `provider.health().detail` 不再含 base_url/端口/系统错误码，原始信息转存 `LLMError.detail` → 服务端日志；evidence: 实机 ping → 503 `"无法连接本地模型服务（Ollama 未启动或端口不可达）"`，health detail → `"连接被拒绝，服务可能未启动"`，`flask_ai.err` 保留 `base_url=... reason=ConnectionRefusedError(10061,...)`；回归 125 项 OK；scope: 2026-11-17T20:44+08:00
- [verified] Phase 6.2 交付 RAG：`backend/ai/rag/` 9 个模块 + `knowledge_routes` 5 个接口 + `tests/test_phase6_rag.py` 50 项；evidence: `Ran 175 tests ... OK`；scope: 2026-11-17T21:0x+08:00
- [verified] 真实 BGE-M3 就位：`pytorch_model.bin` 2165.9 MB（hf-mirror，223s）、dim=1024、`pooling_mode='cls'`、`max_seq_length=8192`；实测 CPU 编码 12.6 chunk/s，首次加载 48s；scope: 本机
- [verified] 端到端（真实模型 + 真实 Chroma + 真实 SQLite，走 HTTP）：导入 201 → `chunk_count=2`、`embedding_dim=1024`；重复导入 200 `created=false`；`app_id` 隔离成立（570 查不到 1203220 的内容）；`scope=global` 跨游戏可见（0.7128）；删除后该游戏 `candidates=0`；检索耗时 embedding 150–200ms / 向量检索 1–4ms；scope: 127.0.0.1:5000
- [verified] `/api/ai/health` 三项组件均真实上报：`llm=true(qwen3:4b)`、`embedding=true(bge-m3, cpu, cached)`、`vector_db=true(chroma, count)`；`config_problems=[]`
- [verified] Phase 6.3 交付：语料抓取 `scripts/fetch_knowledge_corpus.py`（109 条官方公告 / 6 款，全文）、30 题标注 `tests/fixtures/rag_questions.json` v2、评估脚本 `scripts/evaluate_retrieval.py`、指标模块 `backend/ai/rag/evaluation.py`、32 项测试；evidence: `Ran 207 tests ... OK`；scope: 2026-11-18T02:40Z
- [verified] 真实评估（v2 标注，882 chunks，文档级口径）：阈值 0.35 → Recall@5 **0.929** / Prec@5 0.238 / MRR **0.828** / 越界 **0** / 拒答 **0.000**；阈值 0.55 → Recall 0.814 / MRR 0.732 / 拒答 **1.000** / 每题保留 3.03 条；3 次运行记录已入 `rag_evaluation_runs`
- [verified] 修正两处标注缺陷（q05 只标 4 份但语料有 14 份等价封禁名单、q16 漏标 PMCF），新增 `match=any` 等价族口径；v1 Recall 0.853 → v2 0.929，差异全部来自这两题
- [reported] 人工评审未完成：`annotation_provenance.review_status = pending_human_review`，当前数字是起草基线，不是黄金标准成绩
- [verified] Phase 6.3 Task 0 完成 → `docs/PHASE6_3_PRECHECK_REPORT.md`：确认 `hnsw:space=cosine`、`score=1-distance`（0.5192+0.4808=1.0000）；四个失败问题在阈值 0.35 下各返回 5 条，**20/20 条候选都不含回答问题所需事实**；可回答最高 0.6267 vs 不可回答最高 0.5192
- [verified] 评测集冻结为 v3（48 题，含 intent/answer_shape）：`kb_factual_qa` 36（31 可答 / 5 应拒）、`sql_analytics` 4、`strategy_generation` 4、`out_of_scope` 2、`ambiguous` 2；评估器已按 intent 分流，路由类不进召回统计
- [verified] v3 基线（36 道 kb 题 / 882 chunks）：阈值 0.35 → Recall 0.941 / MRR 0.856 / 拒答 **0.000**；0.55 → Recall 0.812 / 拒答 0.800；**关键反证：新增不可回答题 `u01` 最高分 0.6094，高于 0.60 阈值，而可回答的 `q23` 仅 0.557 —— 分数与可回答性无单调关系，单一阈值必然同时造假阳与假阴**
- [verified] Phase 6.3 P0 完成：`RAG_SCORE_THRESHOLD` → `RAG_RETRIEVAL_MIN_SCORE`（默认留空=不过滤，旧名仍可读并告警）；`RAG_TOP_K` 5→10；`/api/knowledge/search` 移除 `insufficient_evidence`（改为 `retrieval_empty`），检索不再判断能否回答；新增 `backend/ai/rag/answer_shapes.py`（按问题推导答案形态）与 `evidence_gate.py`（程序层门控 + 第二层校验接口）
- [verified] 四题回归（真实语料、真实检索出的那几篇）：Q1 退款 → 缺 `policy_statement/applicability/conditions`；Q2 团队人数 → 缺 `count_unit/subject_relation`；Q3 机房 → 缺 `facility_relation`；**Q4 召回活动证据门会放行（形态齐备）→ 证明必须路由在前、门不能替代路由**
- [verified] 反向护栏：6 道可回答问题全部放行（维护窗口/阶段任务数/难度种类/年数/秒数/解锁方法），避免「全拒答」也能拿满分；回归 **289 项 OK**
- [verified] Phase 6.3 P1 完成：`ai/rag/intent.py`（规则路由，48/48）+ `answer_service.py`（路由→检索→证据门→生成→引用校验）+ `answer_schema.py` + `POST /api/knowledge/answer`；回归 **322 项 OK**
- [verified] 证据门实测（36 kb 题 / top_k=10）：拒答准确率 **0.600**（阈值方案 0.000）、误拦率 **0.032**、判定不了 16 题、Recall@5 保持 0.941；仍未达标 3 题：k04 属误拦（资格规则非步骤），u01/u02 属漏拦（形态齐备但答非所问，需第二层）
- [verified] 实机 `/api/knowledge/answer` 八种状态全部验证：answered（带 `E1→chunk` 引用）/ insufficient_evidence（缺 policy_statement 等）/ route_to_sql / strategy_generation_pending / out_of_scope / ambiguous_query / generation_failed
- [verified] 生成链路两个真实缺陷已修：① 模型在 10 条长证据下退化成 `"answer": "..."`，收敛到 3 条证据后恢复；② 占位符原本会被判成 answered，现按「去标点后 <6 字符」拦为 `empty_answer`，且引用校验先于内容校验
- [verified] 第二层语义复核完成：`ai/rag/semantic_check.py`（5×500 证据 + **逐字引用校验**），接线进 `/api/knowledge/answer` 与 `evaluate_retrieval.py --semantic`；实测拒答准确率 0.600→**0.800**，误拦率 0.032→**0.097**（多拦 u02，多误拦 q06/k05）
- [verified] 引文校验是必需的：不加这一步，4B 模型在两种输入下把 30 道可答题全部判 sufficient（含 u01/u02）。加了以后「充分」变成可核验断言，但模型仍会自相矛盾（q23 的 reason 写着「没有明确提到」，verdict 仍是 sufficient）
- [reported] 第二层在 4B 模型上**不稳定**：u01 在小样本被判 insufficient、全量评测又变 sufficient。要稳定需更强模型或 few-shot，属 6.5
- [verified] 回归 **337 项 OK**
- [verified] Phase 6.5 完成：`ai/recall/metrics.py`（从 `ccu`/`steam_reviews` 真算，窗口按数据实际跨度、缺失进 `missing`）+ `generator.py`（指标→知识→生成→Schema→**数值与引用双校验**→落库）+ `store.py`（`recall_strategies` 表）+ `routes/recall_routes.py`（generate / history / detail）；回归 **356 项 OK**
- [verified] 实机生成成功：app 1203220 → `status=generated`、27.9s、7 条动态指标、3 条知识证据（真实 chunk_id）、2 条措施、`execution_status=suggested`；`data_evidence` 数值与本地统计逐条一致（`ccu_change_24h_pct=-3.28`）；已落库并可经 history/detail 读回
- [reported] 开发库知识证据目前只有 2 篇自建测试文档（`永劫无间 1.3.0/1.4.0`）。**要让策略引用真实公告，需先把 `tests/fixtures/rag_corpus` 里的 109 条导入知识库**——现在生成的 claim 引的是测试文档
- [verified] 真实语料已入库：`scripts/import_knowledge_corpus.py --clean-fixtures` → 109 条公告、**103 篇唯一文档 / 882 chunks**（6 条内容完全重复被哈希去重）、0 失败、349.3s；清理掉 2 篇自建测试文档；Chroma 882 == SQLite 882，**孤儿向量 0**
- [verified] 策略重新生成（真实公告）：12.1s、7 条指标、3 条知识证据，引用 `NARAKA Update – Sep 3` 与 `Oct 22` 的真实 chunk（`doc_dca9446cde524db2#0037` 等）；逐条核对原文：奖励次数 3→2、新增 `[Recommended]` 标签、锻造/训练场帮助说明——**claim 与原文一致**，删除 1 条引用已删测试文档的旧策略
- [reported] 策略质量三个缺口（下一轮要修）：① 知识检索用**固定查询词** `KNOWLEDGE_QUERY`，与实际信号无关，所以引的是 9/10 月公告而非近期内容；② 只生成 **1 条措施**，但 `config.min_actions=3` 从未被 schema 校验使用；③ `publish_date` 仍不参与排序（6.3 遗留）
- [verified] 缺口②已修：`STRATEGY_MIN_ACTIONS/MAX_ACTIONS` 接进 `validate_strategy(min_actions,max_actions)` 与提示词（「必须给 3–5 条」）；实机生成措施数 **1 → 3**，18.7s 成功
- [verified] 缺口③已修：新增 `ai/rag/ranking.py`（加性奖励 `score + w·exp(-age/30d)`，默认权重 **0.16**）；扫描 0→0.40 共 10 个点：MRR 0.856→**0.952**、q06 首条名次 5→**1**、Recall 全程 0.941；0.16–0.30 为平台、0.40 起下降；**代价是 q20 从第 1 名掉到第 2 名**
- [reported] 新近度的边界：q07「最近的版本更新内容有哪些」线上仍把 10-22 排在前、11-05 不进 top-5——开放式问法下时间奖励压不过 chunk 分布，需另想办法（时间窗口过滤或 chunk 策略）
- [verified] 评测脚本修了一处自己造的坑：`--recency-sweep` 必须先把 `cfg.recency_weight` 置 0，否则在「已加权的分数」上再加一次，权重 0 那一行就不是真基线了
- [verified] 缺口①已修（信号化检索查询）：新增 `ai/recall/signals.py`（差评高频词 + 指标方向 → 查询词，不用模型）+ `MetricsCollector.recent_negative_texts`；返回里带 `knowledge_query` 与 `knowledge_query_basis` 可逐条核对
- [verified] 实测效果（app 1203220）：常量查询时引的是 9/10 月泛化公告；改为 `trios items` 后引到 **Sep 16 新增 Trios 模式**与 **Aug 13 Ye Xiu 终极技能**，策略第一条措施正是「优化 Trios 模式匹配机制」——信号→检索→引用链闭合
- [reported] 信号化的两个必要妥协：① 30 天内差评 <2 条时放宽到 90 天（NARAKA 实测 30 天仅 1 条，硬用会让机制空转），窗口写在 `basis.review_window_days`；② 脏话与情绪词必须过滤（实测 90 天窗口捞出 `bullshit`/`fucking`），情绪已由 `negative_ratio` 表达
- [verified] 回归 **392 项 OK**
- [verified] 两个入口合并：`/api/knowledge/answer` 的 `strategy_generation` 分支已接 `StrategyGenerator`，装配统一走 `ai/recall/factory.build_generator()`（与 `/api/recall/generate` 共用，参数不再可能分叉）；实机 23.9s → `status=strategy_generated`、`strategy_id=strategy_108d467e77b4`、3 条措施 / 3 条证据、历史 4→5 条；`/api/recall/generate` 回归仍 200
- [reported] 状态语义保持不变：生成器不可用时仍是 `strategy_generation_pending`，只有真的产出并落库才是 `strategy_generated`——两者混用会让调用方以为方案已存在
- [verified] 回归 **395 项 OK**
- [verified] 前端已接入（`front/static_html/solution.html`）：右侧「AI 生成方案」面板内嵌真实 AI 区块——游戏下拉（取 `/api/games`）、生成按钮、状态行、风险等级/指标数、知识证据、数据指标、限制说明、历史列表（点击载入详情）；生成后同时刷新上方方案卡（`planId/planTitle/planDesc/stepsTrack`），来源标记在「示例 / 本地模型生成」之间切换
- [reported] 删除了前端**浏览器直连 SiliconFlow 并要求粘贴 API Key** 的旧实现（密钥存 localStorage、请求绕过后端），改为统一走 `/api/recall/*`——与 writing-block §12.1「前端必须通过 Flask API 获取真实数据」一致
- [verified] 页面校验（无浏览器自动化条件下的三层）：页面 200 且含新 ID；内联脚本 `node --check` 通过；脚本引用的 35 个 ID 全部存在于 HTML，依赖符号（`svg/ICONS/state/byId/fmt/toast`）均已定义；接口契约逐字段核对（`games[].appid|name`、`history.items[].{strategy_id,summary,created_at,action_count,risk_level}`、`detail.strategy.{summary,risk_analysis,data_evidence,knowledge_evidence,recall_actions,limitations}`）
- [verified] u01/u02 闭环（主语约束 `subject_scope`）：关键信息必须与问题主语在 120 字符内共现，中英映射用「已知主语」小词典；实测拒答准确率 **0.600 → 1.000（5/5）**、误拦率保持 **0.000（0/31）**、Recall/MRR 不变、v3 题集**不达标题清零**
- [reported] 主语约束的边界：它是词典不是语义理解。第一版只对原句生效（活跃用户/多少人在玩/技术支持三种改写全漏），扩表后 6 种问法全部拦住；但词表外的说法（如「这游戏还有多少活人」）仍会漏，需第二层语义复核兜底（该层在 4B 上不稳定）
- [verified] 检索深度标定为 **50**（`RAG_TOP_K`；`/answer` 的 `top_k` 默认改为跟随配置，不再写死 10）：同 31 道可答题，top_k=10→50 时 MRR 0.856→0.861、**误拦率 0.032→0.000**、Recall 不变；`q23` 的正确答案在 50 深度下排第 7（top_k=10 时完全不出现，因 HNSW 近似搜索）
- [verified] 生成上下文改为配置项 `LLM_MAX_EVIDENCE=5`（原硬编码 3）：召回 50 条让证据门有得挑，上下文只放 5 条；实机 `answered` 3.4s、5 条证据、引用可追溯
- [verified] 修一处真实分类缺陷：`answer_service._generate` 的 `except Exception` 在 `import LLMError` 之前，把「模型服务不可用」也报成 `llm_call_failed`；已拆成 `llm_error`（带 code）与 `llm_call_failed`（带异常类型），并补测试
- [reported] **上一轮结论被推翻**：我说过「Reranker 救不了 q23」，那只看了 top_k=10；加深后 q23 排第 7，属可重排区间。Reranker 仍不部署，但判据改为可验证的三条（`docs/RAG_EVALUATION.md` §5sexies）
- [verified] 生产配置下的完整评估（top_k=50）：Recall@5 0.941 / MRR 0.861 / 路由 **1.000** / 拒答 0.600（3/5）/ **误拦 0.000** / 判定不了 16；仅剩 u01、u02 程序层拦不住（需第二层，而该层在 4B 上不稳定）
- [verified] Phase 6.2 性能专项 Task 1 完成 → `docs/PHASE6_2_PRECHECK_REPORT.md`；实测：`think` 生效（true 580 tok/7.1s vs false 17 tok/0.3s，未传=默认开）；当前配置 `num_ctx=4096/num_predict=2048` 遇 1892 token 的真实 RAG 上下文时 `content_len=0 / done_reason=length / JSON 非法`（28.4s），关思考后同一请求 2.3s 出 269 字符合法 JSON；显存 4096ctx=3033MB、8192ctx=3698MB 均 100% 驻留；`keep_alive` 生效（30m / 0 卸载）；服务重启后冷加载仅 3.95s——文档 §1.3 的 101s 是一次性磁盘冷读，非稳态
- [verified] Task 2 完成（关闭 Thinking）：`LLM_ENABLE_THINKING` 默认 false，Provider 显式发送 `think`；实机同一 1892 token 长上下文从「28.4s / content=0 / length / JSON 非法」变为「**0.93s / content=56 / stop / JSON 合法**」，token 2048→24；`/api/ai/llm/ping` 回传 `thinking_enabled`、`done_reason`、`truncated_but_valid` 与完整 `metrics`；scope: 127.0.0.1:5000
- [verified] T3/T4/T8 基础件：`backend/ai/recall/schema.py`（Pydantic v2，`extra=forbid`，`execution_status` 仅 `suggested`，证据必须带 `chunk_id`）+ `validation.py`（空文本/非法 JSON/截断/Schema/app_id/证据 六类错误分开报）；判据按用户确认的「`done_reason=length` **且** 解析失败才算失败」，`length` 但合法的结果标 `truncated_but_valid=true` 而非拒绝
- [verified] 回归 235 项通过（新增 24 项 recall + 8 项 thinking/metrics 测试）；`Ran 235 tests ... OK`
- [verified] Task 3 完成（Token 参数拆分）：`LLM_CONTEXT_LENGTH`（默认 8192）与 `LLM_MAX_OUTPUT_TOKENS`（默认 3072）分离；旧名 `OLLAMA_NUM_CTX`/`LLM_MAX_TOKENS` 仍生效但记入 `config_warnings`；`validate()` 新增「输出预算必须 < 上下文窗口」硬校验；实机 1892 token prompt 在 8192 窗口下 `eval=143–165 / content=262–299 / done=stop / JSON 合法`，稳态 2.42s（首跑 6.51s 是切换 num_ctx 触发的模型重载）；`size_vram=3698MB/3698MB` 全在显存；回归 244 项 OK
- [verified] 参数改动未破坏 SiliconFlow：`max_tokens` 仍读 `max_output_tokens`（同一属性，改名未漏改）
- [verified] Task 5 完成（`keep_alive` + 独立预热）：`LLM_KEEP_ALIVE` 默认 30m 并进请求体；`scripts/warmup_llm.py` 支持 `--status/--unload/--json`，脚本自己 `load_dotenv`；`health()` 新增 `loaded`（查 `/api/ps`，区分「已安装」与「已驻留」）；实机 `--unload` → 驻留 False → 预热 `load_ms=2114 / wall=2375ms / 9 token` → 驻留 True，`expires_at` 断言 +30 分钟
- [verified] Task 6 现状核对：无 Nginx/Gunicorn/Docker 中间层（Flask dev server 直连）；超时单一来源 `OLLAMA_TIMEOUT=300`；截断→422、非法 JSON→200+`json_valid=false`、超时→504 均已由测试覆盖；输出侧五个错误码（`INVALID_JSON/SCHEMA_MISMATCH/APP_ID_MISMATCH/EVIDENCE_INVALID/LLM_TRUNCATED`）统一映射 422，不再落到 503
- [verified] Task 7 完成（metrics 落库）：新增 `backend/ai/schema.sql` 的 `llm_call_logs`（20 字段）+ `backend/ai/call_log.py`（记录/统计/按 30 天清理）；`/api/ai/llm/ping` 成功/截断/失败三条路径都留痕；新增 `GET /api/ai/metrics?days&limit`；预热脚本也记 `purpose=warmup`；实机 3 条真实记录 stats `calls=3/failures=0/avg_eval_tokens=14.3/avg_tokens_per_s=81.6`
- [verified] 测试隔离：`tests/test_phase6.py` 用 `setUpModule` 把日志重定向到临时目录——此前跑一次测试就往开发库写假调用（实测 8 条 `wall_ms=1ms`），metrics 直接失去参考价值；修复后同一套测试前后行数不变（8→8）


# 4. Current blocker

- Status: resolved（2026-11-17T20:36+08:00）
- 原症状: 本机未安装 Ollama，`127.0.0.1:11434` 不可达；`winget install Ollama.Ollama` 被执行环境安全策略拦截
- 真实结构（原记录只写了第 1 条）:
  1. Ollama 二进制缺失
  2. `backend/.env` 根本不存在 → `AI_ENABLED` 恒 false，**即使装好 Ollama 也会 409**（此前无人记录，属于隐形阻塞）
  3. 带宽才是瓶颈，不是 winget：GitHub release 资产直连单流 208 KB/s 且反复挂死
- 处置: portable zip v0.40.16 → `%LOCALAPPDATA%\Programs\Ollama`（无管理员、不写 PATH、不注册自启、删目录即回滚）；4 路分块并行经 `127.0.0.1:7897`，14 分钟完成；`ollama pull qwen3:4b`（2,381.6 MB）经代理约 10 MB/s
- 验收证据: 见 §3 的五条 [verified]（部署 / 模型 / health+ping / 失败路径 / 回归）
- 残留风险（进 6.2/6.4 前必须处理）:
  - 首次冷启动单次请求 **101 s**（磁盘冷读 + CUDA 上下文初始化）；`.env` 已把 `OLLAMA_TIMEOUT` 由默认 120 提到 300，但 300 s 只是本机经验值，不是保证
  - qwen3:4b 是 thinking 模型：同一句 31 字符答案，thinking 消耗 434–1114 token（答案的 14–36 倍）。`LLM_MAX_TOKENS=2048` 在 6.4 真做策略生成时会先被 thinking 吃光 → `done_reason=length`，JSON 截断

# 5. Next steps

1. **前端 AI 面板（唯一剩余）**：`/api/recall/generate|history|detail` 与 `/api/knowledge/answer` 都已可用，但前端仍是静态文案，没有任何消费方
2. **策略质量的已知边界**：引用的证据受检索命中限制（NARAKA 的 90 天窗口只有 5 条差评样本）；`direction` 只在 CCU 跌幅 ≥3% 或差评率 ≥30% 时触发，这两个阈值本身也需要评估集标定
3. **策略不针对问题措辞**：`/answer` 的策略分支用当前信号生成通用方案，不解析用户的具体问法（如"只做召回不要拉新"）。要支持得给生成器加约束参数，属 6.5 之外的新需求
2. **前端 AI 面板**：接口已齐（generate / history / detail），但前端仍是静态「召回策略」文案，没有消费方
3. **6.3 遗留**：`k04` 的 procedure 形态需允许「资格规则」；第二层语义复核在 4B 模型上不稳定（u01 会翻转）
2. **性能专项状态**：T1–T7 全部完成并实测；**T8 只差「接入生成器」**——`validation.py` 的 `allowed_chunk_ids` 校验已就绪，但没有策略生成器可接（属 6.5）
2. **阈值决策待用户拍板**：0.35 实测拒答 0%，0.55 拒答 100% 但 Recall 从 0.929 掉到 0.814。这是「漏证据 vs 喂噪声」的业务取舍，不是技术最优解。**未擅自改配置**
2. **时间新近度缺失（6.3 暴露的最严重缺陷）**：`q06` MRR 仅 0.20、`q07` 直接失败，top-5 全是旧公告。`publish_date` 在 metadata 里但从不参与排序，玩家问「最近更新改了什么」必然踩坑。应加衰减排序并用 q06/q07 做回归
3. 6.4 需要的知识库缺口：库里**没有任何运营方法论**（q30 必然拒答）。要产出召回策略，必须先补 FAQ / 运营案例类文档，否则 6.5 只能凭空编
4. 人工评审 30 题标注（重点看「是否还有别的文档也能回答」）；补 chunk 级定位精度指标
5. 6.4 之前先定 thinking 策略：`think:false` 做成 Provider 配置项，或把 `LLM_MAX_TOKENS` 抬到 thinking 预算之上（实测 thinking 消耗 434–1114 token，是答案的 14–36 倍）。**未擅自改**
6. docs 口径：用户已删除 `DATA_METRIC_DEFINITIONS.md` 与 `DATA_SOURCE_AUDIT.md`，本轮按指示不动（见 §8）

# 6. Pitfalls & bans

- [pitfall] 2026-11-17 urllib 默认读 `https_proxy`（本机端口常死）把 `127.0.0.1` 当代理，报 `502 Tunnel failed`。Ban: 所有本地/内网 HTTP 调用显式 `ProxyHandler({})`
- [pitfall] 2026-11-17 `index.html` 三段 `<script>`，跨块调用 `updateKpi` 直接 `ReferenceError`（与历史 `setRiskPillars` 同类）。Ban: 跨块函数显式挂 `window.__x`
- [pitfall] 2026-11-17 用 `s.index('<div class="legend-risk">')` 定位命中更早的同名元素，切片变空串，`replace('')` 把内容插满全文。Ban: 位置切片必须 `index(x, start)` 且断言 `start<end`
- [pitfall] 2026-11-17 `merge` 后 deploy 首跑 `Found 0 artifact(s)` 失败，重跑即绿（Pages 产物瞬时问题）。Ban: 部署失败先看是否 artifact 缺失，再判断代码
- [pitfall] 2026-11-17 CI 冒烟 19–24s 失败为 Node 24 undici 内部崩溃（`assert(!this.paused)`），非断言问题。Ban: 先看失败耗时与堆栈，别急着改页面
- [pitfall] 2026-11-16 改口径（30 天窗口→保留有数据日期）后未重算历史，聚合仍是 0。Ban: 改聚合口径必须同时提供 rebuild 路径
- [pitfall] 2026-11-17 GitHub release 资产直连前几 MB 可达 2.7 MB/s，随后连接挂死（0 字节、无 RST），`--max-time 1800` 让 4 个 worker 白等 30 分钟。Ban: 分块下载必须 `--speed-limit 20000 --speed-time 30`，并优先走 `127.0.0.1:7897`（4 路叠加实测 1.1 MB/s，ollama registry 实测 10 MB/s）
- [pitfall] 2026-11-17 Windows PowerShell 5.1 下 `& curl.exe @arr` 的 splatting 不可靠，静默丢参数。Ban: 原生命令用 `& curl.exe $arr`（数组展开）或显式写参数
- [pitfall] 2026-11-17 `ollama pull` 主 blob 到 100% 后仍会因小 blob 的 HEAD 返回 `EOF` 整体失败，`ollama list` 为空。Ban: 失败先原样重跑，已完成 blob 命中缓存，别删 models 目录
- [pitfall] 2026-11-17 Qwen3 的思考内容在 `message.thinking`，**不会**污染 `message.content`——不要为「content 为空」做过早防御；但 thinking 与答案共用 `num_predict` 预算，会真实截断
- [pitfall] 2026-11-17 错误消息里拼 `base_url` 会经 `_error(e.code, e.message)` 原样回给浏览器，而 health 的 `detail` 同样直出。Ban: 异常消息与 health detail 只放归类后的可公开文案（`failure_reason`），地址/端口/系统错误码一律进 `LLMError.detail` 或服务端日志
- [pitfall] 2026-11-17 对已关闭的本地端口，Windows 有时 RST（ConnectionRefusedError）、有时直接吞包（connect 超时被 urllib 包成 `URLError(TimeoutError)`）。Ban: 断言具体失败原因会写出脆测试，只断言「已归类、不含地址」
- [pitfall] 2026-11-17 `snapshot_download(repo, local_files_only=True)` **仍会联网核对 revision**（本机实测报了「模型已缓存却不可用」的假状态）。Ban: 判缓存只用 `huggingface_hub.try_to_load_from_cache`，且必须校验**权重文件**存在——只看 config.json 会让半成品目录骗过健康检查
- [pitfall] 2026-11-17 `BAAI/bge-m3` 仓库**没有 safetensors**，权重只有 `pytorch_model.bin`；下载时若把 `*.bin` 写进 ignore，会得到一个 20MB 的配置空壳，加载时才炸
- [pitfall] 2026-11-17 Chroma 三条硬约束：collection 名 ≥3 字符且首尾字母数字（`'t'` 直接抛错）；metadata 不接受 None 与 list（全局文档 app_id 用 -1、tags 用逗号串）；cosine 空间返回的是 distance，相似度必须 `1 - distance`
- [pitfall] 2026-11-17 在 PowerShell 双引号里写 `{"$and": [...]}`，`$and` 会被当变量展开成空串，进而误判成「Chroma 不支持嵌套 where」。Ban: 含 `$` 的验证代码写进 .py 文件再跑，别塞进 `-c` 或双引号字符串
- [pitfall] 2026-11-18 **标注过窄会被测成「检索失败」**：封禁名单这类模板公告，同一段证据出现在 14 份文档里，只标 1–4 份会让 recall 直接归 0（q05/q16 实测）。Ban: 多文档标注必须机械枚举等价族（脚本扫语料）并用 `match=any`，否则你是在给自己的标注打分
- [pitfall] 2026-11-18 抓公告语料必须看内容再出题：首版混进 PlayGround.ru / PCGamesN 等第三方媒体（`feeds` 未过滤），且 `maxlength=1600` 把正文截断成残句。Ban: `feeds=steam_community_announcements` + `maxlength=0` + 丢弃正文 <200 字符
- [pitfall] 2026-11-18 索引构建实测 **2.6 chunk/s**（长公告），882 chunks 用了 340 s，远低于短文本基准 12.6 chunk/s。Ban: 重建知识库的耗时按 2.6 chunk/s 估算，别按测速最好值排期
- [pitfall] 2026-11-18 `/api/ai/llm/ping` 在「非法 JSON」时返回 **200 + `json_valid:false`**（诊断口径正确）。Ban: 6.5 的策略生成接口**不得复用**这个语义——那里必须按失败处理（422/`INVALID_JSON`），否则前端会把垃圾当成果
- [pitfall] 2026-11-18 改完代码忘了重启 Flask 会导致「字段消失」的假故障（本次 `loaded`/`keep_alive` 就是这样）。Ban: 验证新字段前先确认进程启动时间晚于最后一次改动
- [pitfall] 2026-11-18 测试会写生产监控表：`ApiTest` 调 ping 时没隔离，往 `backend/data/steam.db` 塞了 8 条 `wall_ms=1ms` 的假记录，metrics 立刻变噪声。Ban: 任何「测试会写库」的路径都必须 `setUpModule` 重定向到临时目录，并验证测试前后行数不变
- [pitfall] 2026-11-18 `load_dotenv` 只在 `backend/app.py` 里这件事**第三次咬人**：`evaluate_retrieval.py --semantic` 因为没加载 .env 导致 `AI_ENABLED=false`，第二层被静默跳过，跑完还显示「成功」。Ban: 任何独立脚本在 import `ai.config` 之前先 `load_dotenv(backend/.env)`，并在输出里打印实际启用的开关
- [pitfall] 2026-11-18 证据片段长度直接影响 4B 模型判断：截 300 字会把答案所在的后半段切掉，k01「每阶段 4 个挑战任务」被判成没提到。Ban: 复核用的 snippet ≥ 生产 chunk 长度（500），别为了省 token 截断到一半

# 7. Environment quickref

- Test: `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"` · Smoke: `node tests/smoke.mjs` · 前端 AI 冒烟: `node tests/solution-ai.smoke.mjs`（桩化 DOM 执行 solution.html 内联脚本，校验下拉/历史/生成/失败四条路径）· Run: `python -m http.server 8080`（front/static_html）+ `.venv/Scripts/python.exe backend/app.py`
- Ollama: `%LOCALAPPDATA%\Programs\Ollama\ollama.exe serve`（portable，无服务注册，需手动起；隐藏窗口启动时带上 `HTTPS_PROXY=http://127.0.0.1:7897` 才能拉模型）。本机配置在 `backend/.env`（未跟踪）：`AI_ENABLED=true`、`OLLAMA_TIMEOUT=300`、`LLM_ENABLE_THINKING=false`、`LLM_CONTEXT_LENGTH=8192`、`LLM_MAX_OUTPUT_TOKENS=3072`
- 配置命名（6.2 Task 3 起）：窗口用 `LLM_CONTEXT_LENGTH`、输出用 `LLM_MAX_OUTPUT_TOKENS`；旧名 `OLLAMA_NUM_CTX` / `LLM_MAX_TOKENS` 仍兼容但会在 `/api/ai/health` 的 `config_warnings` 里告警
- 模型: `qwen3:4b` 2.5 GB 在 `%USERPROFILE%\.ollama\models`；RTX 5070 Laptop 8 GB / CUDA0（cuda_v13，driver 13.3）；实测 80 tok/s，冷启动 101 s，热 5.6–14 s
- Embedding: `BAAI/bge-m3` 2165.9 MB 在 `%USERPROFILE%\.cache\huggingface\hub\models--BAAI--bge-m3`；CPU 编码 12.6 chunk/s，进程内首次加载 48 s；dim 1024，CLS 池化，max_seq_length 8192
- RAG 存储: 向量库 `backend/data/chroma/`（已在 .gitignore）；元数据复用 `backend/data/steam.db` 的 `knowledge_documents` / `knowledge_chunks`；服务构造时幂等建表
- 知识库接口: `POST /api/knowledge/import`（multipart 或 JSON）、`GET /api/knowledge/documents`、`GET|DELETE /api/knowledge/documents/<id>`、`POST /api/knowledge/search`
- 检索评估: `.venv/Scripts/python.exe scripts/evaluate_retrieval.py`（约 6 分钟，含重建 882 chunks 临时索引）；语料刷新 `scripts/fetch_knowledge_corpus.py [--force]`
- 评估资产: 语料 `tests/fixtures/rag_corpus/*.json`（109 条，已跟踪）、题目 `tests/fixtures/rag_questions.json`（v2，30 题）、明细产物 `tests/artifacts/rag_eval_*.json`（已忽略）、报告 `docs/RAG_EVALUATION.md`
- 网络：`api.github.com` 常通，`github.com:443` 频繁超时；`git push` 需 `-c http.proxy= -c https.proxy= -c http.version=HTTP/1.1` 并重试 3–6 次
- 下载源：GitHub 资产/仓库直连不可靠（208 KB/s + 挂死），走 `127.0.0.1:7897` 可控；`huggingface.co` 不可达（12 s 超时），用 `hf-mirror.com`（1 s 内 200）
- Python 3.13.9（venv 有 torch/ST/sklearn）；采集链路仍零第三方依赖
- 计划任务 `NEXUS-SteamCCU`：5 分钟一轮 6 款，配置在 `backend/config/games.yaml`

# 8. Open questions / PROMOTE candidates

- RAG 方案待定：Chroma + 薄封装（推荐）vs 临时装 AnythingLLM 做对照；等待用户确认
- docs 现状（本轮确认）：`DATA_METRIC_DEFINITIONS.md` 与 `DATA_SOURCE_AUDIT.md` 由用户主动删除（未提交），本轮不恢复、不重写；`DATA_MIGRATION_PLAN.md` 仍在维护、有效
- 6.4 前的口径问题：thinking 预算 vs `LLM_MAX_TOKENS`，二选一必须显式决定，不能留给模型自己碰运气
- PROMOTE 候选：`urllib 必须 ProxyHandler({})` 与 `跨 <script> 作用域` 两条已具备「换任务仍适用」性质，建议写入 `AGENTS.md` §3；待用户确认
