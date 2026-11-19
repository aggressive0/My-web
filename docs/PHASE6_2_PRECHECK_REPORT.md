# Phase 6.2 预检报告（Task 1：现状检查）

> 依据：`docs/PHASE6_2_LLM_THINKING_AND_PERFORMANCE_FIX.md` §3、§15 Task 1
> 执行时间：2026-11-18 10:5x（本机时间）
> 原则：所有结论都有本节记录的实测数据支撑；与文档假设不一致的地方明确指出，不迁就文档。

---

## 0. 结论摘要

| # | 结论 | 证据位置 |
|---|---|---|
| 1 | **当前代码从未发送 `think` 参数**，模型按默认开启思考（580–664 token） | §3.1 |
| 2 | **文档描述的问题已完整复现**：当前配置下模型把 2048 token 预算全部烧在思考上，`content_len=0`、`done_reason=length`、JSON 一个字都没输出 | §3.2 A 组 |
| 3 | **关掉思考后同一请求 2.3 秒返回 269 字符合法 JSON**，预算只用了 145/2048 | §3.2 B 组 |
| 4 | **文档 §1.3「首次请求 101 秒」是误读**：实测服务启动后首次请求 4.39 秒（加载 3.95 秒）。101 秒是一次性的磁盘冷读 + 首次 CUDA 上下文初始化，不是稳态 | §3.4 |
| 5 | **`/no_think` 在本模型包上不可用**：模板里没有该开关，只有 `$.IsThinkSet`（由 API `think` 参数驱动） | §3.5 |
| 6 | 模型 100% 在显存（4096 ctx 占 3033 MB，8192 ctx 占 3698 MB），**文档里「显存不足/层被卸载到 CPU」的假设不成立** | §3.3 |
| 7 | **`.env` 只在 `backend/app.py` 加载**，任何脚本单独 `import ai.config` 拿到的是代码默认值（timeout=120 而非 .env 的 300），预热脚本必须自己 `load_dotenv()` | §2.3 |
| 8 | **T3/T4/T8 的目标对象尚不存在**：没有策略生成模块、没有 Pydantic 模型、没有 `recall_strategies` 表。本阶段只能新建基础设施，不存在「改造」 | §2.6 |

---

## 1. 环境

| 项目 | 实测值 |
|---|---|
| Ollama 版本 | `0.40.16`（portable，`%LOCALAPPDATA%\Programs\Ollama`） |
| 模型 | `qwen3:4b`，digest `359d7dd4bcda`，2,497,293,931 B，Q4_K_M，4.02B 参数 |
| 模型能力（`/api/tags`） | `["completion","tools","thinking"]` |
| 模型元数据（`/api/show`） | 36 层 / 8 KV heads / embedding 2560 / 原生 ctx 262144 |
| 模型内置默认参数 | `temperature 0.6, top_k 20, top_p 0.95, repeat_penalty 1` |
| GPU | RTX 5070 Laptop 8 GB，驱动 610.74，CUDA0（cuda_v13） |
| 现有测试 | `Ran 207 tests ... OK`（26.8 s） |

---

## 2. 代码侧现状（逐项对应 §3.1 的十个定位点）

### 2.1 LLM Provider 路径与接口

| 项目 | 现状 |
|---|---|
| Provider 抽象 | `backend/ai/llm/base.py`（`BaseLLMProvider.generate/health`） |
| Ollama 实现 | `backend/ai/llm/ollama_provider.py` |
| 请求接口 | **Ollama 原生 `POST /api/chat`**（非 `/api/generate`，非 OpenAI-compatible） |
| 工厂 | `backend/ai/llm/provider_factory.py`（`ollama` / `siliconflow` / `mock`） |
| 无鉴权保护 | `/api/ai/*` 与 `/api/knowledge/*` 均无鉴权（本地 Demo 可接受，部署前必须加） |

当前 payload（`ollama_provider.py:76-88`）：

```python
payload = {
    "model": self.model,
    "messages": messages,
    "stream": False,
    "format": "json",
    "options": {"temperature": ..., "num_ctx": ..., "num_predict": ...},
}
```

**缺 `think`，缺 `keep_alive`，不读 `done_reason`。**（`grep "think|keep_alive" backend/ai/llm/*.py` → 0 命中）

### 2.2 Token 参数（当前生效值）

| 配置项 | 来源 | 进程中实际值 | 代码默认值 |
|---|---|---|---|
| `num_predict` | `LLM_MAX_TOKENS` → `config.max_output_tokens` | **2048** | 2048 |
| `num_ctx` | `OLLAMA_NUM_CTX` → `config.num_ctx` | **4096** | 4096 |
| `temperature` | `OLLAMA_TEMPERATURE` | 0.3 | 0.3 |
| 使用点 | `ollama_provider.py:88-89`、`siliconflow_provider.py:51` | — | — |

两个名字混用的风险：`LLM_MAX_TOKENS` 同时被 Ollama（`num_predict`）和 SiliconFlow（`max_tokens`）消费，改名必须同步改两处，否则 SiliconFlow 会静默丢配置。

### 2.3 超时配置

| 层 | 值 | 说明 |
|---|---|---|
| Flask → Provider | `.env` 的 `OLLAMA_TIMEOUT=300`（进程内已确认） | 代码默认仍写 120 |
| Provider → Ollama | `urllib` `timeout=self.timeout` | 单次请求总超时 |
| 中间层 | **不存在** | 无 Nginx / Gunicorn / Docker；Flask dev server 直连 |
| 脚本侧 | — | **`load_dotenv()` 只在 `backend/app.py` 调用**，独立脚本拿到 120 |

### 2.4 是否启用 Thinking

**当前是开启的，且不是显式配置的结果，而是"没传参数"的默认行为。** 详见 §3.1 的 A/B 数据。

### 2.5 JSON 校验现状

| 层 | 实现 |
|---|---|
| Provider | 只剥 ```json 围栏，不做结构校验（刻意如此，职责边界正确） |
| 路由 `/api/ai/llm/ping` | `json.loads(text)`，失败只回 `json_valid: false` |
| Pydantic | **全仓库 0 处**（`grep BaseModel` → 0） |
| `done_reason` | **从未读取** |
| 截断识别 | **不存在** |

后果：`done_reason=length` + `content=""` 的失败会被记为「模型返回空内容」，而不是「预算不足被截断」——两者的处置完全不同。

### 2.6 策略生成与 RAG 上下文

| 项目 | 现状 |
|---|---|
| 召回策略生成模块 | **不存在**（无 `recall_generator`，无 `recall_strategies` 表） |
| RAG 上下文组织 | **不存在**（只有 `POST /api/knowledge/search` 返回命中列表，没有拼 context 的代码） |
| 知识库 | 已可用（6.2 交付），6.3 实测 Recall@5 0.929 / 阈值 0.55 时拒答 100% |
| `STRATEGY_MIN_ACTIONS/MAX_ACTIONS` | 已在 `config.py` 定义，但**无任何调用方** |

即：T3（Schema）、T4（截断分类）、T8（证据校验）**没有可改造的宿主**，只能新建。这一点必须写进任务边界，否则会出现一堆没有调用方的「基础设施」。

### 2.7 Docker 与前端

| 项目 | 现状 |
|---|---|
| Docker | **无 Dockerfile / docker-compose**（仓库内只有 `games.yaml` 匹配 yaml）。§3.1 第 10 点「容器如何访问宿主机 Ollama」不适用 |
| 前端 AI 面板 | **不存在**。`index.html` / `analysis.html` 只有「召回策略」导航与静态文案，没有任何 `api/ai/*` 或 `api/knowledge/*` 请求 |
| 结论 | §14 里的「前端 AI 面板」不在本阶段可交付范围；预热接口如要做，也没有前端调用方 |

---

## 3. 实测证据

### 3.1 Thinking 参数是否真的生效

同一提示（要求只输出一个 31 字符 JSON），仅改 `think`：

| 条件 | eval_count | thinking 字符 | content 字符 | 墙钟 | done_reason |
|---|---|---|---|---|---|
| `think: true` | 580 | 1051 | 31 | 7.12 s | stop |
| `think: false` | **17** | **0** | 37 | **0.32 s** | stop |
| **未传（当前代码）** | 664 | 1195 | 31 | 8.12 s | stop |

结论：参数生效，不是服务端静默忽略；**未传时默认开思考**，成本是 34–39 倍 token。

### 3.2 Token 预算冲突（真实长 prompt）

prompt 规模：system 40 字 + user 2952 字（含 2871 字模拟 RAG 上下文），**prompt_eval_count = 1892 token**。

| 组 | num_ctx | num_predict | think | eval_count | content | thinking | done_reason | JSON 合法 | 墙钟 | 显存 |
|---|---|---|---|---|---|---|---|---|---|---|
| **A 当前配置** | 4096 | 2048 | 未传 | 2048（撞上限） | **0** | 3334 | **length** | **否** | 28.4 s | 3033 MB |
| **B 仅关思考** | 4096 | 2048 | false | 145 | 269 | 0 | stop | 是 | **2.3 s** | 3033 MB |
| **C 文档建议** | 8192 | 3072 | false | 146 | 266 | 0 | stop | 是 | 8.9 s¹ | 3698 MB |
| **D 建议 ctx 但开思考** | 8192 | 3072 | 未传 | 2208 | 217 | 3452 | stop | 是 | 39.3 s | 3698 MB |

¹ C 组的 8.9 s 包含切换 `num_ctx` 触发的模型重载；稳态与 B 组同量级。

**A 组就是文档 §1.2 的场景，且比文档描述的更糟**：不是「JSON 被截断」，而是 JSON **完全没有开始输出**（`content_len=0`）。
**D 组说明抬高预算能救命，但不解决浪费**：多花 37 秒思考，产出与 B 组几乎一样。

### 3.3 显存与加载位置

| 场景 | size | size_vram | 结论 |
|---|---|---|---|
| num_ctx=4096 | 3033 MB | 3033 MB | 100% 在 GPU |
| num_ctx=8192 | 3698 MB | 3698 MB | 100% 在 GPU，+665 MB |

8 GB 卡上仍有约 3 GB 余量。**「显存不足导致部分层卸载到 CPU」在 4B/Q4_K_M 下不成立**；如果将来同时跑多请求（`OLLAMA_NUM_PARALLEL > 1`）需重新测。

### 3.4 冷启动到底多慢

| 场景 | load_duration | 总墙钟 |
|---|---|---|
| 服务刚启动后首个请求 | **3.95 s** | 4.39 s |
| `keep_alive=0` 卸载后重载 | 2.25 s | 2.62 s |
| 昨天（2026-11-17）记录 | — | 101 s |

为什么差 25 倍：昨天是模型文件**首次**从磁盘读入（2.3 GB 权重 + 1.6 GB 的 Ollama 二进制文件刚下载完，OS page cache 是冷的），加上首次 CUDA 上下文初始化。今天文件已在 page cache 里。

结论：**101 秒是一次性成本，不是可优化的稳态问题。** 真正会重复发生的是「空闲 5 分钟后被卸载 → 下次请求多 2.3–4 秒」。`keep_alive` 解决的是这一项，收益是 2–4 秒，不是 97 秒。

### 3.5 `keep_alive` 与 `/no_think`

| 测试 | 结果 |
|---|---|
| 请求带 `keep_alive: "30m"` | `/api/ps` 的 `expires_at` = 当前 +30 分钟，生效 |
| 请求带 `keep_alive: 0` | `/api/ps` 立即为空，模型卸载 |
| 模板里的 thinking 开关 | 只有 `$.IsThinkSet`（由 API `think` 驱动）；**没有** `enable_thinking`，**没有** `/no_think` |

所以文档 §4.5 提到的 `/no_think` 提示词在本模型包上**没有可用开关**，必须依赖 API 参数。

### 3.6 截断判定不能只看 `done_reason`

补充实测（短输出）：

| 条件 | content 长度 | done_reason | JSON 是否合法 |
|---|---|---|---|
| think=true, num_predict=16 | 0 | length | 否 |
| think=false, num_predict=16 | 37（完整） | **length** | **是** |

第二行说明：`done_reason=length` **不等于**结果不可用。若按文档 T4 字面执行「`done_reason=length` 不允许作为成功结果保存」，会把合法结果误杀。正确判据应是：`done_reason=length` **且** 内容无法通过 JSON/Schema 校验，才判定失败。

---

## 4. 根因假设（按性价比排序）

| # | 根因 | 影响 | 修复成本 |
|---|---|---|---|
| R1 | Provider 从不发送 `think`，思考默认开启 | 每次生成浪费 ~600+ token 与 5–37 秒 | 极低（1 个字段） |
| R2 | `num_ctx=4096` 是输入+输出总窗口，1892 token 的 prompt 只剩 2204；而 `num_predict=2048` 让「预算看似够」 | 一旦叠上思考就必然截断/空输出 | 低（拆两个参数） |
| R3 | 只做 `json.loads`，不读 `done_reason` | 预算不足被误报为「模型返回空内容」，无法定位 | 低 |
| R4 | `.env` 只在 `app.py` 加载 | 预热脚本/CLI 拿到默认值（timeout 120），与线上不一致 | 极低 |
| R5 | 无 `keep_alive`，空闲 5 分钟卸载 | 每次空闲后首请求多 2.3–4 s | 极低 |
| R6 | 无 Pydantic Schema、无策略生成模块（T3/T4/T8 宿主缺失） | 后续 6.5 无法保证结构化输出 | 中（需新建） |

---

## 5. 与文档假设不一致的三处（必须更正，否则后续验收会对着错的靶子打）

1. **§1.3「首次请求 101 秒，超时余量不足」** → 实测冷加载 3.95 s。101 s 是一次性磁盘冷读。T6 仍要做（部署后首请求仍需覆盖），但**不能以「优化 101 秒」为验收标准**，那是无法复现的。
2. **§1.3「GPU 显存不足或部分层卸载到 CPU」** → 实测 100% 显存驻留，4096/8192 ctx 都成立。该假设不成立。
3. **§4.5「可用 `/no_think` 作为兼容性辅助」** → 本模型包模板无此开关。只能靠 API 参数，且必须像文档说的那样「通过接口测试确认生效」——本文 §3.1 已完成该确认。

---

## 6. 建议修改的文件（真实路径）

| 文件 | 改动 | 必要性 |
|---|---|---|
| `backend/ai/config.py` | 新增 `LLM_ENABLE_THINKING`（默认 false）、`LLM_KEEP_ALIVE`（默认 30m）；`LLM_MAX_TOKENS` → `LLM_MAX_OUTPUT_TOKENS`（保留旧名兼容）、`OLLAMA_NUM_CTX` → `LLM_CONTEXT_LENGTH`（保留旧名） | 必做 |
| `backend/ai/llm/ollama_provider.py` | payload 增加 `think` / `keep_alive`；解析并回传 `done_reason`、`prompt_eval_count`、`eval_count`、各段耗时 | 必做 |
| `backend/ai/llm/base.py` | `LLMError` 增加 `LLM_TRUNCATED`；`generate()` 返回体带 metrics（或 provider 上挂 `last_metrics`） | 必做 |
| `backend/ai/llm/siliconflow_provider.py` | 跟随 `max_output_tokens` 改名（`max_tokens` 语义不变） | 必做（漏改会静默丢配置） |
| `backend/routes/ai_routes.py` | `/api/ai/health` 增加 `thinking_enabled` / `keep_alive` / 模型是否驻留；`/api/ai/llm/ping` 回传 `done_reason` 与 token 统计 | 必做 |
| `scripts/warmup_llm.py` | 新建：`load_dotenv()` + 一次最小请求 + 打印 `load_duration`，退出码区分「已就绪/失败」 | 建议 |
| `.env.example` | 新配置项与注释（说明 `num_predict` 与 `num_ctx` 的区别） | 必做 |
| `backend/.env` | 本机同步（不入库） | 必做 |
| `tests/test_phase6.py` | 新增：`think` 是否进 payload、`keep_alive` 是否进 payload、`done_reason=length` + 空内容的分类、旧配置名兼容 | 必做 |
| `backend/ai/recall/schema.py` | 新建 Pydantic Schema（T3）。**无调用方**，为 6.5 预留 | 待确认 |

**不建议现在做**：前端 AI 面板（不存在，且没有 API 消费方）、预热 HTTP 接口（多一个无鉴权端点，脚本足够）、Docker（本机根本没有容器化）。

---

## 7. 需要用户拍板的三件事

1. **参数路线**：先只关思考（`think=false`，实测 2.3 s 成功），还是同时抬到 `num_ctx=8192 / num_predict=3072`？
   我的建议：**分两步**。Task 2 只加 `think=false` 并复测；Task 3 再抬窗口中预算，用真实 RAG 上下文（1892 token 级）复测。理由：B 组已证明单靠关思考就能解决问题，一次改两个变量会让回归定位变难。
2. **是否本阶段就建 Pydantic Schema**：现在没有生成器，Schema 只能被测试使用。建了是「为 6.5 铺路」，不建则 T3 无法验收。
3. **`done_reason=length` 的判定口径**：按文档字面「一律不算成功」，还是按实测「length + 解析失败才算失败」？后者会保留合法结果，但也可能放过「JSON 刚好闭合但内容被砍半」的情况。

---

## 8. 本次预检没有做的事

- 没有修改任何代码、配置或 `.env`
- 没有升级 Ollama（0.40.16 已支持 `think` 与 `keep_alive`，无需升级）
- 没有跑 A/B 质量对比（文档 §12，需要 10 次 × 2 组，留给 Task 2/3 之后）
- 没有评估 thinking 对策略**质量**的影响——只测了它对耗时与 token 的影响。质量差异目前无证据，因此不能声称关闭思考会降低质量
