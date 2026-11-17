---
schema_version: 2
updated: "2026-11-17T19:54:32+08:00"
git:
  branch: dev
  head: 8cba847
  dirty: false
  scope: "snapshot before handoff write"
captured: reconstructed
tasks:
  - id: TASK-001
    name: Phase 6 AI 召回（6.1 已完成，6.2 起未开始）
    status: active
---

# 1. Task & user intent

在 Steam 公开数据基础上做「热点情感 → 群体流失风险 → AI 召回建议」的可追溯 Demo。
Phase 1–5 已完成并上线；Phase 6 进入 AI 部分，6.1（模型接入）代码完成，6.2（RAG）未开始。

> 「先合并 PR #37，用我安装的skill写一份交接文档」 — user, 2026-11-17

> 「不得擅自执行发布、合并或推送到 main」 — `writing-block.md` §1.2-11（本轮已授权合并 #37）

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

# 4. Current blocker

- Status: blocked
- Symptom: 本机未安装 Ollama，`127.0.0.1:11434` 不可达；`winget install Ollama.Ollama` 被执行环境安全策略拦截
- Location: `backend/ai/llm/ollama_provider.py` 运行时依赖；验收项「Flask 调通本地 Qwen3」未验证
- Repro / evidence: `curl -s -o /dev/null -w "%{http_code}" --noproxy '*' http://127.0.0.1:11434/api/tags` → `000`

# 5. Next steps

1. 人工安装 Ollama 并拉模型 → expected: `ollama list` 出现 qwen3:4b；verify: `ollama list`
2. 设 `AI_ENABLED=true` 后验证链路 → expected: `json_valid: true`；verify: `curl -X POST localhost:5000/api/ai/llm/ping -d '{}'`
3. Phase 6.2：Chroma + BGE-M3 + 文档导入/检索（app_id 隔离）→ expected: 新增 `backend/ai/rag/` 与 6.2 测试全绿；verify: `unittest discover`
4. 三份 docs 口径刷新（见 §8）→ expected: METRIC 升 v2、AUDIT 标历史基线；verify: `grep -n "四款" docs/*.md` 无命中

# 6. Pitfalls & bans

- [pitfall] 2026-11-17 urllib 默认读 `https_proxy`（本机端口常死）把 `127.0.0.1` 当代理，报 `502 Tunnel failed`。Ban: 所有本地/内网 HTTP 调用显式 `ProxyHandler({})`
- [pitfall] 2026-11-17 `index.html` 三段 `<script>`，跨块调用 `updateKpi` 直接 `ReferenceError`（与历史 `setRiskPillars` 同类）。Ban: 跨块函数显式挂 `window.__x`
- [pitfall] 2026-11-17 用 `s.index('<div class="legend-risk">')` 定位命中更早的同名元素，切片变空串，`replace('')` 把内容插满全文。Ban: 位置切片必须 `index(x, start)` 且断言 `start<end`
- [pitfall] 2026-11-17 `merge` 后 deploy 首跑 `Found 0 artifact(s)` 失败，重跑即绿（Pages 产物瞬时问题）。Ban: 部署失败先看是否 artifact 缺失，再判断代码
- [pitfall] 2026-11-17 CI 冒烟 19–24s 失败为 Node 24 undici 内部崩溃（`assert(!this.paused)`），非断言问题。Ban: 先看失败耗时与堆栈，别急着改页面
- [pitfall] 2026-11-16 改口径（30 天窗口→保留有数据日期）后未重算历史，聚合仍是 0。Ban: 改聚合口径必须同时提供 rebuild 路径

# 7. Environment quickref

- Test: `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"` · Smoke: `node tests/smoke.mjs` · Run: `python -m http.server 8080`（front/static_html）+ `.venv/Scripts/python.exe backend/app.py`
- 网络：`api.github.com` 常通，`github.com:443` 频繁超时；`git push` 需 `-c http.proxy= -c https.proxy= -c http.version=HTTP/1.1` 并重试 3–6 次
- Python 3.13.9（venv 有 torch/ST/sklearn）；采集链路仍零第三方依赖
- 计划任务 `NEXUS-SteamCCU`：5 分钟一轮 6 款，配置在 `backend/config/games.yaml`

# 8. Open questions / PROMOTE candidates

- RAG 方案待定：Chroma + 薄封装（推荐）vs 临时装 AnythingLLM 做对照；等待用户确认
- 三份 docs 时效（见回复）：`DATA_METRIC_DEFINITIONS.md` 需升 v2（补情感/主题/风险/时长分布，且「四款」已过时）；`DATA_SOURCE_AUDIT.md` 应标为 2026-11-16 历史基线（覆盖率 11%、84 项断言均已过期）；`DATA_MIGRATION_PLAN.md` 仍在维护、有效
- PROMOTE 候选：`urllib 必须 ProxyHandler({})` 与 `跨 <script> 作用域` 两条已具备「换任务仍适用」性质，建议写入 `AGENTS.md` §3；待用户确认
