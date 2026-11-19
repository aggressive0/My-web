# PHASE6_2_LLM_THINKING_AND_PERFORMANCE_FIX.md

> **项目阶段：** Phase 6.2 — LLM Thinking 与推理性能专项修复  
> **适用项目：** 基于热点情感的玩家流失阻断与精准召回方案  
> **目标模型：** Ollama + Qwen3 4B（当前方案，暂不更换）  
> **主要任务：** 关闭不必要的 Thinking、解决输出 Token 预算冲突、保证 JSON 结构化输出、优化冷启动和请求超时  
> **执行原则：** 先检查、后修改；小步实施；每项修改都必须有测试和数据验证。

---

## 1. 项目背景与问题定义

当前 Phase 6.2 已完成基础实现，但在实际调用 Ollama 模型时出现以下问题。

### 1.1 Thinking 占用输出 Token

同一句约 31 个字符的简单答案，模型 Thinking 阶段消耗约 434–1114 Token。

Thinking Token 与最终回答共用 `num_predict` 输出预算，导致模型实际可用于生成最终 JSON 的 Token 数量不足。

### 1.2 召回策略 JSON 被截断

当前配置：

```env
LLM_MAX_TOKENS=2048
```

生成真实召回策略时，Thinking 过程可能先消耗大部分预算，最终出现：

```text
done_reason=length
```

表现为 JSON 内容不完整、解析失败，或者缺少必要字段。

### 1.3 Ollama 冷启动时间过长

首次请求耗时约 101 秒，默认 120 秒请求超时余量不足。

可能原因包括：

- 模型首次加载耗时较长。
- 模型被频繁卸载，导致重复加载。
- GPU 显存不足或部分模型层被卸载到 CPU。
- Thinking 产生过多 Token。
- Prompt 或 RAG 上下文过长。
- Docker、Flask、代理层存在额外等待。

目前不能直接认定 101 秒全部来自模型加载，需要通过 Ollama 性能统计进一步定位。

---

## 2. 本阶段目标

本阶段不是更换模型，而是在现有 Ollama + Qwen3 4B 方案上完成性能和可靠性修复。

| 编号 | 目标 | 验收标准 |
|---|---|---|
| T1 | 关闭不必要的 Thinking | 普通策略生成请求明确传递 `think=false` |
| T2 | 解决 Token 预算冲突 | 最终输出有足够预算，不再被 Thinking 无意义消耗 |
| T3 | 保证 JSON 输出 | 生成结果通过 JSON 解析和 Pydantic Schema 校验 |
| T4 | 识别截断错误 | `done_reason=length` 不允许作为成功结果保存 |
| T5 | 降低重复冷启动 | 支持 `keep_alive` 和独立预热 |
| T6 | 优化请求超时 | 首次加载、预热、热启动请求具有合理超时 |
| T7 | 建立性能监控 | 保存关键耗时、Token 数和结束原因 |
| T8 | 保证证据有效性 | 无效 RAG 证据不能进入正式策略结果 |

### 2.1 非目标

本阶段不实施以下改动：

- 不更换 Qwen3 4B。
- 不迁移 Ollama 到其他推理框架。
- 不引入 vLLM、SGLang 等推理服务。
- 不迁移 Flask 到 FastAPI。
- 不迁移 SQLite。
- 不强制引入 Celery、Redis 等异步基础设施。
- 不将 RAG 检索、LLM 推理和业务分析重新设计为微服务。
- 不改变已有 Steam 数据采集和分析逻辑。
- 不将群体级分析包装成玩家级精准识别。

---

## 3. 执行前检查

**Agent 必须先检查现有实现，不能直接按照文档创建重复模块或覆盖已有代码。**

### 3.1 检查项目结构

阅读：

- `AGENTS.md`
- `README.md`
- 现有 Flask 入口和路由。
- 当前 LLM Provider 实现。
- Ollama 调用封装。
- AI 召回策略生成模块。
- RAG 检索模块。
- Pydantic 数据模型（如果已经存在）。
- Docker Compose 配置。
- `.env.example` 或其他配置模板。
- 前端 AI 面板及其请求逻辑。

重点定位：

1. `LLM_MAX_TOKENS` 的读取和使用位置。
2. Ollama 请求的实际 URL。
3. 当前使用 `/api/chat`、`/api/generate` 还是 OpenAI-compatible 接口。
4. 是否支持向请求体传递 `think`。
5. 是否设置 `keep_alive`。
6. 是否存在请求超时配置。
7. 是否读取 Ollama 返回的性能统计。
8. 是否已经存在 JSON Schema 或 Pydantic 校验。
9. 当前策略生成结果如何保存到 SQLite。
10. Docker 容器如何访问宿主机 Ollama。

### 3.2 检查 Ollama 环境

在宿主机执行：

```bash
ollama --version
ollama list
```

确认模型：

```bash
ollama show qwen3:4b
```

使用 Ollama API 查看模型信息：

```bash
curl http://localhost:11434/api/show \
  -d '{"model":"qwen3:4b"}'
```

检查返回内容中的模型能力、模板和 Thinking 相关信息。

重点确认：

- 当前 Ollama 版本。
- 当前模型实际名称。
- 模型是否支持 Thinking 控制。
- API 是否支持 `think` 参数。
- 是否支持结构化输出 `format`。
- 模型是否可以正常加载。
- 是否存在其他模型占用显存。

**版本注意：** Qwen3 与 Ollama 的 Thinking 控制能力存在版本要求。先检查当前环境；如果版本不支持所需参数，记录原因并提出升级建议，不要未经确认直接升级生产环境。

### 3.3 生成执行前检查报告

Agent 应先输出：

```text
PHASE6_2_PRECHECK_REPORT.md
```

至少包含：

- 当前 LLM Provider 文件路径。
- 当前 Ollama 请求接口。
- 当前模型名称和 Ollama 版本。
- 当前 Token 参数。
- 当前超时配置。
- 当前是否启用 Thinking。
- 当前 JSON 校验实现。
- 当前 RAG 上下文组织方式。
- 当前 Docker 网络访问方式。
- 当前问题的初步原因。
- 建议修改的文件列表。

完成检查后再开始修改。

---

## 4. 修复一：关闭不必要的 Thinking

### 4.1 核心方案

对于本项目的召回策略生成，主要任务是：

1. 获取真实业务指标。
2. 检索相关知识库证据。
3. 根据上下文生成结构化召回策略。
4. 返回符合 Schema 的 JSON。

这类任务需要模型进行一定程度的分析，但不一定需要输出冗长的 Thinking 内容。

因此优先通过 Ollama API 明确关闭 Thinking。

### 4.2 推荐配置

在现有环境配置中增加：

```env
# 是否启用模型 Thinking
LLM_ENABLE_THINKING=false

# 模型名称
LLM_MODEL=qwen3:4b

# 最大输出 Token
LLM_MAX_OUTPUT_TOKENS=3072

# 上下文窗口
LLM_CONTEXT_LENGTH=8192

# 模型保持加载时间
LLM_KEEP_ALIVE=30m
```

以上数值是初始测试参数，不是不可更改的最终值。

注意：

- `LLM_MAX_OUTPUT_TOKENS` 控制生成预算。
- `LLM_CONTEXT_LENGTH` 控制输入上下文窗口。
- 两者不能混为一谈。
- 应结合实际显存和系统内存测试调整。

### 4.3 Ollama API 调整

如果当前使用 Ollama 原生 `/api/chat`，请求示例：

```python
payload = {
    "model": settings.LLM_MODEL,
    "messages": messages,
    "stream": False,
    "think": False,
    "keep_alive": settings.LLM_KEEP_ALIVE,
    "options": {
        "num_ctx": settings.LLM_CONTEXT_LENGTH,
        "num_predict": settings.LLM_MAX_OUTPUT_TOKENS,
        "temperature": 0
    }
}
```

如果当前 Provider 使用 OpenAI-compatible 接口：

- 先确认该接口是否能够透传 `think` 参数。
- 如果不能，优先考虑为 Ollama 增加原生 API Provider。
- 不要假设所有 OpenAI-compatible 接口都支持 Ollama 的扩展参数。
- 保留现有 Provider 抽象，不破坏 SiliconFlow 等已有接口。

### 4.4 Provider 设计要求

应当将 Thinking 配置放在 Provider 层，而不是在各个业务路由中重复设置。

示意：

```python
class OllamaProvider:
    def chat(self, messages, **kwargs):
        payload = self.build_payload(messages, **kwargs)

        payload["think"] = settings.LLM_ENABLE_THINKING
        payload["keep_alive"] = settings.LLM_KEEP_ALIVE

        return self._request(payload)
```

注意：

- 以上是实现示意，Agent 应以当前代码结构为准。
- 不要机械复制示例导致接口重复。
- Thinking 配置应支持环境变量覆盖。
- 默认关闭 Thinking。
- 如未来确实有复杂推理需求，可以针对特定任务单独开启。

### 4.5 兼容性处理

可在适用的 Qwen3 Prompt 模板中使用：

```text
/no_think
```

但它只能作为兼容性辅助，不能代替 API 参数控制。

最终必须通过请求日志或接口测试确认 `think=false` 确实生效。

---

## 5. 修复二：重新设计 Token 预算

### 5.1 参数拆分

不再使用含义模糊的单一 Token 配置。

推荐拆分：

| 配置 | 用途 | 初始建议 |
|---|---|---:|
| `LLM_MAX_OUTPUT_TOKENS` | 最终输出预算 | 3072 |
| `LLM_CONTEXT_LENGTH` | 输入上下文窗口 | 8192 |
| `LLM_RAG_TOP_K` | 初始检索候选数量 | 5 |
| `LLM_MAX_EVIDENCE` | 最终证据数量 | 3–5 |
| `LLM_MAX_ACTIONS` | 单条策略措施数量上限 | 3 |

具体参数需要在实际模型和机器环境下测试。

### 5.2 输出内容控制

为了避免策略生成过长，应限制输出规模：

- 每次最多生成 3 条召回策略。
- 每条策略最多 3 个具体措施。
- 每条策略必须提供目标群体、触发依据和执行建议。
- 每条策略最多列出 3 个核心评估指标。
- 避免生成重复的背景解释。
- 不要求模型重复输出完整原始数据。
- 不要求模型重复输出全部检索文档。

### 5.3 上下文压缩

LLM 输入由三部分组成：

```text
System Prompt
    +
真实业务指标（SQL）
    +
RAG 检索证据
```

要求：

**真实业务指标：**

- 由现有 SQLite 查询和业务分析模块提供。
- 只传入生成策略必要的指标。
- 使用紧凑 JSON 表示。
- 不把整个数据库查询结果直接塞进 Prompt。

**RAG 证据：**

- 初始候选 Top-K：5。
- 最终提供 3–5 条证据。
- 去除重复内容。
- 优先使用与目标游戏 AppID 匹配的资料。
- 优先保留近期公告、补丁和运营资料。
- 限制单条证据的文本长度。
- 保留来源 ID 和原始出处。

**重要约束：**

SQL 动态数据与 RAG 文档证据必须分开处理。

RAG 不负责生成实时 CCU、评论数量或流失率等动态指标。

---

## 6. 修复三：使用 JSON Schema 保证结构化输出

### 6.1 设计原则

不能只依赖 Prompt 中的：

```text
请严格返回 JSON，不要输出 Markdown。
```

应使用 Ollama 的结构化输出能力，将 JSON Schema 传入模型请求。

建议通过 Pydantic 定义数据结构，再生成 JSON Schema。

### 6.2 数据结构

以下为建议结构，Agent 应优先复用现有业务模型，避免创建重复定义。

```python
from pydantic import BaseModel, Field
from typing import Literal


class RecallAction(BaseModel):
    title: str
    description: str
    target_group: str


class RecallStrategy(BaseModel):
    strategy_name: str
    objective: str
    trigger_evidence: list[str]
    target_segment: str
    actions: list[RecallAction] = Field(max_length=3)
    evaluation_metrics: list[str] = Field(max_length=3)


class RecallStrategyResult(BaseModel):
    app_id: int
    summary: str
    strategies: list[RecallStrategy] = Field(max_length=3)
    evidence_sources: list[str]
    execution_status: Literal["suggested"]
```

### 6.3 Ollama 请求示例

```python
schema = RecallStrategyResult.model_json_schema()

payload = {
    "model": settings.LLM_MODEL,
    "messages": messages,
    "stream": False,
    "think": False,
    "format": schema,
    "keep_alive": settings.LLM_KEEP_ALIVE,
    "options": {
        "num_ctx": settings.LLM_CONTEXT_LENGTH,
        "num_predict": settings.LLM_MAX_OUTPUT_TOKENS,
        "temperature": 0
    }
}
```

注意：

- Ollama 的结构化输出支持 JSON Schema。
- 必须确认当前 Ollama 版本支持对应参数。
- Schema 应尽量保持简单，避免不必要的复杂嵌套。
- Schema 约束结构，不保证事实正确。
- Schema 约束结构，也不代表证据来源真实有效。

### 6.4 输出二次校验

模型响应后必须经过：

```python
result = RecallStrategyResult.model_validate_json(response_text)
```

验证通过后才允许进入业务保存流程。

需要额外验证：

1. AppID 是否与当前请求一致。
2. 策略数量是否符合限制。
3. 每条策略是否有实际措施。
4. `evidence_sources` 是否对应真实检索结果。
5. 引用的来源 ID 是否存在。
6. `execution_status` 是否为 `suggested`。

不能因为 JSON 格式合法，就认为策略内容可靠。

---

## 7. 修复四：区分不同生成失败原因

当前不能将所有模型失败统一处理成“AI 生成失败”。

至少需要区分以下情况。

### 7.1 输出被截断

条件：

```text
done_reason == "length"
```

处理：

- 标记为 `truncated`。
- 不保存为成功策略。
- 不直接展示为完整策略。
- 记录实际生成 Token 数。
- 可以进行一次受控重试。

重试时优先检查：

- 是否关闭 Thinking。
- 输出预算是否足够。
- Prompt 是否过长。
- 输出结构是否过于复杂。

不能无限重试。

### 7.2 JSON 解析失败

条件：

```python
json.loads(response_text)
```

解析失败。

处理：

- 标记为 `invalid_json`。
- 保存必要错误日志。
- 可以进行一次 JSON 修复请求。
- 修复请求只能使用原始模型输出和原始 Schema。
- 修复结果仍然必须通过 Schema 校验。

禁止直接使用字符串截取、正则表达式等方式将不完整 JSON 拼成成功结果。

### 7.3 Schema 校验失败

条件：

```python
RecallStrategyResult.model_validate_json(response_text)
```

验证失败。

处理：

- 标记为 `schema_validation_failed`。
- 记录具体字段错误。
- 不保存为成功策略。
- 根据错误类型决定是否允许一次重试。

### 7.4 证据校验失败

条件：

- 引用的证据 ID 不存在。
- 引用证据与当前 AppID 不匹配。
- 引用的资料没有实际出现在本次检索结果中。
- 模型生成了无法追溯的来源。

处理：

- 标记为 `evidence_validation_failed`。
- 不允许作为可信策略保存。
- 不允许伪造证据来源。

### 7.5 统一状态建议

如果现有数据库结构允许，可采用以下状态：

```text
pending
generating
completed
truncated
invalid_json
schema_validation_failed
evidence_validation_failed
failed
```

注意：如果当前数据库已经存在任务状态字段，应尽量兼容，不要为了新增状态破坏历史数据。

---

## 8. 修复五：优化 Ollama 冷启动

### 8.1 使用 keep_alive

推荐初始配置：

```env
LLM_KEEP_ALIVE=30m
```

Ollama 请求中：

```json
{
  "keep_alive": "30m"
}
```

作用：

- 模型加载后保留一段时间。
- 减少连续请求之间重复加载模型。
- 改善演示场景的热启动表现。

如果 Demo 期间需要持续保持模型加载，可以评估：

```json
{
  "keep_alive": -1
}
```

但必须考虑当前机器只有 8GB 显存，不能默认让多个模型长期驻留。

建议：

- Qwen3 4B 作为主要常驻模型。
- Embedding 模型按需调用。
- BGE-M3 可以优先考虑 CPU 或分时运行。
- 避免 Qwen3 和多个大型模型同时占用显存。

### 8.2 增加独立预热机制

不建议在 Flask 启动过程中同步等待模型加载完成。

建议增加独立脚本：

```text
scripts/
    warmup_llm.py
```

运行：

```bash
python scripts/warmup_llm.py
```

预热请求使用短 Prompt：

```text
请返回 {"status":"ready"}。
```

预热请求要求：

- 关闭 Thinking。
- 设置合理的 `num_predict`。
- 使用与正式任务一致的模型。
- 设置 `keep_alive`。
- 记录加载耗时。
- 记录预热是否成功。

预热请求的输出只用于检查模型可用性，不进入正式业务数据。

### 8.3 可选预热 API

如果现有系统已有管理接口，可以增加：

```http
POST /api/ai/warmup
```

返回示例：

```json
{
  "status": "ready",
  "model": "qwen3:4b",
  "load_duration_ms": 0,
  "total_duration_ms": 0
}
```

以上数字仅为结构示意，不代表真实运行结果。

接口要求：

- 仅管理员或本地开发环境允许调用。
- 不允许普通用户频繁触发。
- 不阻塞 Flask 主启动过程。
- 应避免并发重复预热。
- 记录最后一次预热时间和结果。

如果已有健康检查接口，可扩展现有接口，不必重复创建。

### 8.4 前端状态

AI 面板应能够区分：

| 状态 | 用户可见提示 |
|---|---|
| unavailable | 模型服务不可用 |
| loading | 模型正在加载 |
| warming_up | 模型预热中 |
| ready | 模型已就绪 |
| generating | 正在生成召回策略 |
| failed | 模型调用失败 |

不要在模型尚未就绪时直接显示“正在生成策略”。

---

## 9. 修复六：优化请求超时

### 9.1 初始超时建议

| 请求类型 | 初始建议 |
|---|---:|
| 健康检查 | 5–10 秒 |
| 模型预热 | 180–240 秒 |
| 普通 AI 分析 | 120–180 秒 |
| 首次召回策略生成 | 240–300 秒 |
| 模型已预热后的策略生成 | 90–150 秒 |

这些是初始测试范围，必须根据实际运行统计调整。

### 9.2 Python 请求配置

如果当前使用 `requests`：

```python
response = requests.post(
    ollama_url,
    json=payload,
    timeout=(10, 240)
)
```

注意：

`timeout=(10, 240)` 通常表示连接超时和读取超时，并不等同于严格的 240 秒总任务时间。

需要结合当前客户端的实际实现检查。

### 9.3 检查完整请求链路

不能只修改 Python 请求超时。

必须检查：

```text
浏览器
  ↓
前端请求超时
  ↓
Flask 路由
  ↓
Gunicorn / WSGI（如果使用）
  ↓
Nginx / 反向代理（如果使用）
  ↓
Docker 网络
  ↓
宿主机 Ollama
  ↓
模型推理
```

逐层确认：

- 前端 `fetch` 是否设置超时或 `AbortController`。
- Flask 是否存在请求超时限制。
- Gunicorn 是否设置较短 worker timeout。
- Nginx 是否设置较短 `proxy_read_timeout`。
- Docker 是否能正常访问宿主机 `11434` 端口。
- Ollama 是否在请求过程中持续运行。

不存在的组件不需要为了本阶段强行引入。

### 9.4 前端体验

生成策略期间：

- 显示“正在准备模型”或“正在生成策略”。
- 不要让用户误以为页面卡死。
- 超时后显示明确错误。
- 提供重试入口。
- 不允许超时后展示残缺 JSON。
- 不允许将失败结果伪装成成功策略。

如果后续实测生成时间仍然经常超过 60 秒，再评估异步任务与轮询机制。

当前阶段暂不强制引入 Celery 或 Redis。

---

## 10. 修复七：记录 Ollama 性能指标

### 10.1 必须记录的字段

Ollama 响应中应尽量保留以下字段：

| 字段 | 作用 |
|---|---|
| `total_duration` | 总耗时 |
| `load_duration` | 模型加载耗时 |
| `prompt_eval_duration` | 输入处理耗时 |
| `eval_duration` | 输出生成耗时 |
| `prompt_eval_count` | 输入 Token 数 |
| `eval_count` | 输出 Token 数 |
| `done_reason` | 生成结束原因 |

如果 API 返回 Thinking 内容或相关统计，也应在调试模式中记录，但不要默认把完整内部 Thinking 内容保存到普通业务日志中。

### 10.2 性能日志示例

建议统一记录：

```python
logger.info(
    "LLM request completed",
    extra={
        "model": model_name,
        "total_duration": total_duration,
        "load_duration": load_duration,
        "prompt_eval_duration": prompt_eval_duration,
        "eval_duration": eval_duration,
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
        "done_reason": done_reason,
    }
)
```

注意：

- 时间单位应统一。
- 可以在日志中转换为毫秒。
- 不记录 API Key。
- 不记录敏感配置。
- 不要把完整用户上下文和私密 Prompt 写入普通日志。
- 生产日志应控制详细程度。

### 10.3 根据指标定位问题

| 现象 | 优先排查方向 |
|---|---|
| `load_duration` 很高 | 模型重复卸载、首次加载、显存资源 |
| `eval_duration` 很高 | Thinking、输出 Token、GPU/CPU offload |
| `prompt_eval_duration` 很高 | Prompt 过长、RAG 上下文过大 |
| `eval_count` 异常高 | 输出冗长、Thinking 未关闭 |
| `done_reason=length` | 输出预算不足或输出过长 |
| 总耗时高但模型耗时正常 | Docker、Flask、代理或前端等待 |

不要仅凭总耗时判断瓶颈。

---

## 11. 修复八：保证 RAG 上下文质量

本阶段顺带检查 RAG 输入是否过长，但不重构整个 RAG 系统。

### 11.1 检索流程

```text
用户请求
   ↓
识别目标 AppID
   ↓
查询真实动态业务指标（SQLite）
   ↓
检索知识库候选资料
   ↓
AppID 过滤
   ↓
去重与排序
   ↓
选取 3–5 条证据
   ↓
压缩 Prompt
   ↓
调用 Ollama
   ↓
JSON Schema 校验
   ↓
证据有效性校验
   ↓
保存策略
```

### 11.2 必须遵守的规则

- RAG 文档必须带有来源信息。
- 文档必须带有适用 AppID 或明确的全局适用范围。
- 检索时必须进行 AppID 过滤。
- 不允许将一个游戏的公告错误引用到另一个游戏。
- 不允许模型生成不存在的来源 ID。
- 不允许模型将推测内容描述为已验证事实。
- 动态业务指标仍然来自 SQL，不由 RAG 推断。

### 11.3 不要在本阶段过度优化

以下能力可以作为后续工作：

- BM25 + Dense Hybrid Search。
- RRF。
- BGE Reranker v2-m3。
- 更复杂的时间衰减排序。
- 大规模检索评测体系。

本阶段优先保证：

1. 输入数据正确。
2. 证据来源真实。
3. AppID 过滤正确。
4. Prompt 不过长。
5. 输出能够通过 Schema 校验。

---

## 12. A/B 测试方案

不能仅凭一次请求成功就认定修复完成。

使用相同模型、相同 Prompt、相同业务数据进行对照测试。

### 12.1 测试组

| 组别 | Thinking | 输出预算 | keep_alive | JSON Schema | 预热 |
|---|---|---:|---|---|---|
| A | 开启 | 2048 | 默认 | 无 | 无 |
| B | 关闭 | 2048 | 默认 | 无 | 无 |
| C | 关闭 | 3072 | 30m | 无 | 无 |
| D | 关闭 | 3072 | 30m | 有 | 有 |

### 12.2 测试次数

每组至少进行 3 次。

分别记录：

- 冷启动耗时。
- 热启动耗时。
- 总耗时。
- 模型加载耗时。
- 输入 Token 数。
- 输出 Token 数。
- `done_reason`。
- JSON 解析是否成功。
- Schema 校验是否成功。
- 证据校验是否成功。

测试时需要控制：

- 相同模型版本。
- 相同业务数据。
- 相同 Prompt。
- 相同 RAG 检索结果。
- 相同硬件环境。
- 相同并发条件。

测试组之间应避免模型加载状态造成不公平比较。

### 12.3 测试结果表

Agent 应生成：

```text
PHASE6_2_PERFORMANCE_TEST_REPORT.md
```

推荐记录格式：

| 测试组 | 冷启动耗时 | 热启动耗时 | 输出 Token | done_reason | JSON | Schema | 证据 |
|---|---:|---:|---:|---|---|---|---|
| A | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| B | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| C | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| D | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |

不允许填写未经实际测试的虚构数据。

---

## 13. 自动化测试要求

至少增加以下测试。

### 13.1 Thinking 参数测试

验证：

- 默认关闭 Thinking。
- Ollama 请求包含 `think=false`。
- 配置开启时能够正确传递 `think=true`。
- 不同 Provider 不会错误接收不支持的参数。

### 13.2 Token 配置测试

验证：

- `LLM_MAX_OUTPUT_TOKENS` 正确读取。
- `LLM_CONTEXT_LENGTH` 正确读取。
- `num_predict` 使用输出预算。
- `num_ctx` 使用上下文预算。
- 两个配置互不覆盖。

### 13.3 JSON Schema 测试

覆盖：

- 正常 JSON。
- 缺少必填字段。
- 字段类型错误。
- 策略数量超限。
- 措施数量超限。
- 非法 `execution_status`。
- 非法 AppID。

### 13.4 截断处理测试

模拟：

```json
{
  "done_reason": "length",
  "response": "{\"strategies\":["
}
```

验证：

- 返回截断状态。
- 不保存为成功策略。
- 前端不显示为完整结果。
- 日志保留必要诊断信息。

### 13.5 证据校验测试

验证：

- 有效证据可以通过。
- 不存在的证据 ID 被拒绝。
- 跨 AppID 证据被拒绝。
- 模型伪造的证据来源不能通过。
- 空证据是否允许，应由当前业务规则明确决定。

### 13.6 预热测试

验证：

- 预热成功。
- 预热失败。
- Ollama 不可用。
- 重复预热请求不会产生失控并发。
- 预热失败不影响 Flask 正常启动。

### 13.7 回归测试

确保以下原有能力不受影响：

- Steam 数据采集。
- CCU 分析。
- 评论分析。
- 留存分析。
- 流失分析。
- AI 面板加载。
- 召回策略历史记录。
- Docker 本地部署。

---

## 14. 建议修改文件

以下为建议范围，最终以执行前检查报告为准。

| 文件或目录 | 预期改动 |
|---|---|
| `.env.example` | 增加 Thinking、Token、上下文、keep_alive 配置 |
| 现有配置模块 | 统一读取和校验配置 |
| 现有 Ollama Provider | 增加 `think=false`、`keep_alive`、Schema 参数 |
| 现有 LLM 服务 | 记录性能统计、统一异常处理 |
| 召回策略数据模型 | 增加或完善 Pydantic Schema |
| 召回策略生成模块 | 增加 Schema 校验和证据校验 |
| AI API 路由 | 扩展健康检查或预热接口 |
| `scripts/warmup_llm.py` | 增加独立预热脚本 |
| 前端 AI 面板 | 增加模型状态和错误反馈 |
| 自动化测试目录 | 增加 Thinking、截断、Schema、预热测试 |

不要为了符合上述文件名称而重复创建已有功能。

---

## 15. Agent 分阶段执行任务

### Task 1：现状检查

- 阅读项目说明和开发规范。
- 定位 LLM Provider。
- 检查 Ollama 版本。
- 检查当前 Token 与超时配置。
- 运行现有测试。
- 生成 `PHASE6_2_PRECHECK_REPORT.md`。

验收：明确所有需要修改的真实文件路径和根因假设。

### Task 2：关闭 Thinking

- 增加 `LLM_ENABLE_THINKING`。
- 默认关闭。
- 在 Ollama Provider 传递 `think=false`。
- 检查 API 参数是否真正生效。
- 增加单元测试。

验收：普通策略生成请求不再产生不必要的 Thinking 输出。

### Task 3：Token 参数拆分

- 将原有 `LLM_MAX_TOKENS` 迁移为清晰的输出预算配置。
- 增加上下文窗口配置。
- 检查旧配置兼容性。
- 增加参数测试。

验收：`num_predict` 和 `num_ctx` 使用不同配置。

### Task 4：JSON Schema

- 检查并完善 Pydantic 模型。
- 将 Schema 传入 Ollama。
- 对输出执行二次验证。
- 增加 AppID 和证据校验。

验收：成功结果必须同时通过 JSON、Schema 和证据校验。

### Task 5：冷启动与预热

- 增加 `keep_alive`。
- 增加预热脚本。
- 视现有架构增加管理预热接口。
- 不让 Flask 启动过程同步等待模型加载。

验收：可以独立预热模型，并能确认模型是否就绪。

### Task 6：超时和错误处理

- 检查前后端请求超时。
- 检查 Gunicorn/Nginx 等实际存在的中间层。
- 设置合理的首次请求和热启动超时。
- 分类处理截断、JSON 错误和证据错误。

验收：不会把超时、截断或非法 JSON 当成成功结果。

### Task 7：性能测试

- 执行 A/B 测试。
- 记录冷启动和热启动性能。
- 比较 Thinking Token 消耗。
- 比较 JSON 成功率。
- 生成 `PHASE6_2_PERFORMANCE_TEST_REPORT.md`。

验收：报告中的所有数据均来自真实运行。

### Task 8：回归与交付

- 执行自动化测试。
- 执行 Docker 部署验证。
- 检查前端 AI 面板。
- 检查原有 Steam 分析模块。
- 输出最终修改报告。

最终生成：

```text
PHASE6_2_LLM_THINKING_AND_PERFORMANCE_REPORT.md
```

---

## 16. 最终验收标准

### 必须通过

- [ ] 默认关闭 Qwen3 Thinking。
- [ ] Ollama 请求确实传递 `think=false`。
- [ ] 输出 Token 与上下文 Token 分开配置。
- [ ] 正式策略生成使用 JSON Schema。
- [ ] 模型输出通过 Pydantic 校验。
- [ ] `done_reason=length` 被识别为截断。
- [ ] 截断结果不进入成功策略记录。
- [ ] 无效证据不能通过校验。
- [ ] `keep_alive` 配置生效。
- [ ] 可以独立执行模型预热。
- [ ] Flask 启动不被模型冷启动阻塞。
- [ ] 关键性能统计被记录。
- [ ] 前端能够展示模型加载和生成状态。
- [ ] 前端不会展示残缺 JSON。
- [ ] Docker 环境能够访问宿主机 Ollama。
- [ ] 原有 Steam 分析功能通过回归测试。

### 性能目标

以下是本阶段希望达到的初步目标，最终应结合实际硬件和测试结果确定：

| 指标 | 目标方向 |
|---|---|
| Thinking 消耗 | 普通策略任务关闭不必要的 Thinking |
| 热启动耗时 | 明显低于当前首次请求的 101 秒 |
| 首次请求 | 不因默认 120 秒超时而轻易失败 |
| JSON 成功率 | 测试集内全部通过 JSON 解析 |
| Schema 成功率 | 测试集内全部通过 Schema 校验 |
| 证据有效性 | 所有成功策略引用均可追溯 |
| 截断处理 | 不允许截断结果伪装成成功结果 |

不要为了达到耗时目标牺牲证据正确性和策略质量。

---

## 17. 本地硬件与部署约束

本项目本地环境：

```text
OS: Windows 11
CPU: AMD Ryzen 9 8940HX
RAM: 16GB
GPU: NVIDIA RTX 5070 Laptop GPU
VRAM: 8GB
Storage: 1TB NVMe
```

部署建议：

```text
Windows 11
│
├── Ollama（宿主机原生运行）
│   └── Qwen3 4B
│
└── Docker Desktop + WSL2
    ├── Flask
    ├── SQLite
    ├── ChromaDB
    └── Frontend
```

Docker 容器访问宿主机 Ollama：

```text
http://host.docker.internal:11434
```

要求：

- 保持当前 Docker 部署方式。
- 不将 Ollama 强行迁入 Docker。
- 不在容器中重复下载一份模型。
- 检查宿主机端口访问和防火墙配置。
- 控制模型常驻数量。
- 避免多个大型模型同时抢占 8GB 显存。
- 监控系统内存和 GPU 显存。

---

## 18. 代码质量与安全要求

1. 不要在前端保存模型 API Key。
2. 不要将 API Key 写入 Git。
3. 不要将完整模型 Thinking 内容作为普通业务日志。
4. 不要把虚构数据混入真实 Steam 数据。
5. 不要将建议的召回策略描述为已执行的召回行动。
6. 不要将群体级分析描述为识别到具体流失玩家。
7. 不要因为 JSON Schema 校验通过就跳过证据检查。
8. 不要因为一次模型调用成功就认为性能问题已解决。
9. 不要破坏现有 SQLite 历史数据。
10. 不要引入与本阶段目标无关的大型依赖。

---

## 19. Git 与交付约束

Agent 必须遵循项目既有分支规范。

- 修改前检查当前分支和工作区状态。
- 不覆盖用户未提交的本地修改。
- 不擅自修改 `test` 分支。
- 未经明确授权，不合并到 `main`。
- 未经明确授权，不执行发布操作。
- 不擅自推送远端分支。
- 如需提交，先汇报修改文件和测试结果。

### 最终交付内容

必须交付：

```text
PHASE6_2_PRECHECK_REPORT.md

PHASE6_2_PERFORMANCE_TEST_REPORT.md

PHASE6_2_LLM_THINKING_AND_PERFORMANCE_REPORT.md
```

同时提供：

1. 修改文件清单。
2. 核心改动说明。
3. 新增环境变量说明。
4. Ollama 版本与模型信息。
5. 实际 A/B 测试数据。
6. 自动化测试结果。
7. Docker 验证结果。
8. 当前遗留问题。
9. 后续优化建议。

如果某项测试因为环境限制无法执行，必须明确标记为“未验证”，不能写成“已通过”。

---

## 20. 本阶段最终实施优先级

| 优先级 | 任务 | 原因 |
|---|---|---|
| P0 | 关闭 Thinking | 直接解决输出预算被消耗的问题 |
| P0 | 记录 Ollama 性能统计 | 确定 101 秒耗时的真实来源 |
| P0 | 拦截 `done_reason=length` | 防止残缺策略被保存 |
| P1 | JSON Schema | 提高结构化输出可靠性 |
| P1 | keep_alive + 预热 | 降低重复冷启动 |
| P1 | 调整端到端超时 | 避免首次请求轻易超时 |
| P1 | 证据校验 | 防止模型编造来源 |
| P2 | Prompt 和 RAG 上下文压缩 | 降低输入处理成本 |
| P2 | 异步任务与轮询 | 仅在同步调用仍然过慢时考虑 |
| P3 | Hybrid Search / Reranker | 后续检索质量优化 |

**最终原则：先关闭无意义的 Thinking，再建立结构化输出约束，最后通过预热、超时和性能监控解决冷启动问题。不要在尚未完成这三项基础修复前更换模型或重构整套 AI 架构。**