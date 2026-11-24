"""
AI 模块配置（Phase 6.1）。

全部从环境变量读取，带安全默认值；不在代码里出现任何密钥。
设计原则：
  - 配置只在后端存在，前端拿不到 base_url / key（AGENTS.md §10）；
  - 缺省值必须让「什么都没配」的系统给出明确的 unavailable，而不是崩溃或假装可用。
"""

import os

VALID_PROVIDERS = ("ollama", "siliconflow", "mock")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CHROMA_DIR = os.path.join(BACKEND_DIR, "data", "chroma")
DEFAULT_KNOWLEDGE_DB = os.path.join(BACKEND_DIR, "data", "steam.db")


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _optional_float(name, default=None):
    """留空 = 不启用。用 0.0 表示「不启用」会和「阈值设成 0」混淆，所以单独一个函数。"""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _env_int_alias(new_name, legacy_name, default, deprecations):
    """新变量优先，旧变量兜底，并记录一条弃用告警。

    为什么不直接删旧名：已有的 .env 与 CI 变量会静默失效，表现为
    「参数没生效」而不是启动报错——这比直接崩更难排查。
    """
    for name in (new_name, legacy_name):
        raw = os.environ.get(name)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            deprecations.append("%s=%r 不是整数，已忽略（回退默认值 %s）"
                                % (name, raw, default))
            continue
        if name == legacy_name:
            deprecations.append("%s 已弃用，请改用 %s（当前仍生效）"
                                % (legacy_name, new_name))
        return value
    return default


class RAGConfig:
    """检索侧配置（Phase 6.2）。

    与 LLM 配置刻意分开：embedding 与生成必须能各自独立失败。
    Ollama 挂掉时检索仍应可用——这是选 CPU embedding 而非 /api/embed 的原因。
    """

    def __init__(self):
        self.embedding_model = os.environ.get("EMBEDDING_MODEL") or "BAAI/bge-m3"
        self.embedding_device = (os.environ.get("EMBEDDING_DEVICE") or "cpu").strip().lower()
        self.embedding_batch_size = _int("EMBEDDING_BATCH_SIZE", 8)
        self.chroma_dir = os.environ.get("CHROMA_PERSIST_DIRECTORY") or DEFAULT_CHROMA_DIR
        self.collection = os.environ.get("CHROMA_COLLECTION") or "knowledge_chunks"
        # Phase 6.3：top_k 从 5 提到 10。阈值不再决定能否回答，候选数量就必须给够，
        # 否则证据门只能在 5 条里做判断，等于把召回损失转嫁给下游。
        # 50 是实测出来的：同样 31 道可答题，top_k=10 时 q23 的正确答案完全不出现
        # （HNSW 近似搜索，n_results 变了返回集合也会变），top_k=50 时它排第 7；
        # 同时误拦率 0.032 → 0.000（更深候选让证据门在 k04 上找到了所需表述）。
        # 代价只是多扫描几十条向量（毫秒级），生成侧另有 max_evidence 控制上下文。
        self.top_k = _int("RAG_TOP_K", 50)

        # 召回下限：只过滤「明显不相关」的候选，**不再作为回答许可条件**。
        # 默认留空 = 不过滤。实测依据：阈值 0.55 能把 4 道拒答题挡住，但 Recall 从
        # 0.929 掉到 0.814；而扩题后 u01 得分 0.6094，任何单一阈值都挡不住。
        # 新近度重排（6.3 遗留）：score + w × exp(-age/tau)。
        # 0.16 是扫描出来的：MRR 0.856→0.952、q06 首条名次 5→1、Recall 不变；
        # 0.16–0.30 是平台，0.40 起开始下降。代价是 q20 从第 1 名掉到第 2 名
        # （正确答案仍在 top-5 内）。要关闭就设 0。
        self.recency_weight = _float("RAG_RECENCY_WEIGHT", 0.16)
        self.recency_tau_days = _float("RAG_RECENCY_TAU_DAYS", 30.0)

        self.deprecations = []
        self.retrieval_min_score = _optional_float("RAG_RETRIEVAL_MIN_SCORE")
        if self.retrieval_min_score is None:
            legacy = _optional_float("RAG_SCORE_THRESHOLD")
            if legacy is not None:
                self.retrieval_min_score = legacy
                self.deprecations.append(
                    "RAG_SCORE_THRESHOLD 已弃用：它不再作为回答许可条件，"
                    "仅作为召回下限（新名 RAG_RETRIEVAL_MIN_SCORE，留空=不过滤）")
        self.max_context_chars = _int("RAG_MAX_CONTEXT_CHARS", 10000)
        self.chunk_size = _int("CHUNK_SIZE", 500)
        self.chunk_overlap = _int("CHUNK_OVERLAP", 80)
        self.knowledge_db = (os.environ.get("KNOWLEDGE_DB")
                             or os.environ.get("STEAM_DB") or DEFAULT_KNOWLEDGE_DB)

    def validate(self):
        problems = []
        if self.chunk_size < 50:
            problems.append("CHUNK_SIZE 过小（建议 ≥200）")
        if not (0 <= self.chunk_overlap < self.chunk_size):
            problems.append("CHUNK_OVERLAP 必须满足 0 ≤ overlap < chunk_size")
        if self.top_k <= 0:
            problems.append("RAG_TOP_K 必须为正整数")
        if self.retrieval_min_score is not None and not (0.0 <= self.retrieval_min_score <= 1.0):
            problems.append("RAG_RETRIEVAL_MIN_SCORE 必须在 0–1 之间，或留空表示不过滤")
        if not (0.0 <= self.recency_weight <= 0.5):
            problems.append("RAG_RECENCY_WEIGHT 必须在 0–0.5 之间（超过会压过语义分）")
        if self.recency_weight > 0 and self.recency_tau_days <= 0:
            problems.append("RAG_RECENCY_TAU_DAYS 必须为正数（衰减时间常数）")
        if self.embedding_device not in ("cpu", "cuda"):
            problems.append("EMBEDDING_DEVICE 只接受 cpu / cuda")
        return problems

    def public_view(self):
        """不含路径：chroma_dir 与 db 路径属于部署内部信息。"""
        return {
            "embedding_model": self.embedding_model,
            "embedding_device": self.embedding_device,
            "embedding_batch_size": self.embedding_batch_size,
            "collection": self.collection,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "top_k": self.top_k,
            # None 表示「不做召回过滤」。故意不用 0.0 表达，避免与「阈值就是 0」混淆
            "retrieval_min_score": self.retrieval_min_score,
            "recency_weight": self.recency_weight,
            "recency_tau_days": self.recency_tau_days,
            "max_context_chars": self.max_context_chars,
        }

    @property
    def score_threshold(self):
        """旧属性名，保留以免外部脚本静默失效。新代码请用 retrieval_min_score。"""
        return self.retrieval_min_score

    @score_threshold.setter
    def score_threshold(self, value):
        self.retrieval_min_score = value


class AIConfig:
    """一次性快照，避免每次请求都读一遍环境变量。"""

    def __init__(self):
        self.enabled = _bool("AI_ENABLED", False)
        self.provider = (os.environ.get("LLM_PROVIDER") or "ollama").strip().lower()

        # Ollama
        self.ollama_base_url = (os.environ.get("OLLAMA_BASE_URL")
                                or "http://127.0.0.1:11434").rstrip("/")
        self.ollama_model = os.environ.get("OLLAMA_MODEL") or "qwen3:4b"
        self.timeout = _int("OLLAMA_TIMEOUT", 120)
        # 弃用告警收集：健康检查会把它们回给运维，避免「配了旧变量但没生效」的哑火
        self.deprecations = []
        # 上下文窗口（输入+输出总窗口）与输出预算必须分开配，混用是 6.2 的根因之一
        self.context_length = _env_int_alias(
            "LLM_CONTEXT_LENGTH", "OLLAMA_NUM_CTX", 8192, self.deprecations)
        self.temperature = _float("OLLAMA_TEMPERATURE", 0.3)

        # 云端备用（可选）
        self.siliconflow_api_key = os.environ.get("SILICONFLOW_API_KEY") or ""
        self.siliconflow_base_url = (os.environ.get("SILICONFLOW_BASE_URL")
                                     or "https://api.siliconflow.cn/v1").rstrip("/")
        self.siliconflow_model = os.environ.get("SILICONFLOW_MODEL") or ""

        # 第二层语义复核开关，默认关闭。
        # 实测（36 题 × 3 轮，temperature=0）：程序层已能 5/5 拒答且 0 误拦；
        # 打开语义层后拒答仍是 5/5，却把 q06/q23/k03 三道可答题判成证据不足。
        # 也就是说它当前**只增加误拦、不增加拦截**，所以默认关。
        # 等引文校验/矛盾判据把误杀降下来，再改默认值。
        self.semantic_check = _bool("RAG_SEMANTIC_CHECK", False)

        # 生成参数
        self.max_output_tokens = _env_int_alias(
            "LLM_MAX_OUTPUT_TOKENS", "LLM_MAX_TOKENS", 3072, self.deprecations)

        # 送进上下文的证据条数。与 RAG_TOP_K 是两回事：
        # top_k 决定「召回多深」，这个决定「模型看几条」。
        # 实测 4B 模型在 10 条长证据下会退化成输出 "..."，5 条是能稳住的上限。
        self.max_evidence = _int("LLM_MAX_EVIDENCE", 5)
        self.min_actions = _int("STRATEGY_MIN_ACTIONS", 3)
        self.max_actions = _int("STRATEGY_MAX_ACTIONS", 5)

        # Thinking（Phase 6.2 Task 2）。默认关闭：实测同一提示下
        # 开思考消耗 580–664 token（答案本身 17 token），且在本项目的
        # 「按资料填 JSON」任务上没有可观测收益。需要复杂推理时可单独开启。
        self.enable_thinking = _bool("LLM_ENABLE_THINKING", False)

        # 模型驻留时间（Phase 6.2 Task 5）。Ollama 默认在空闲后卸载模型，
        # 下次请求要重新加载（实测 2.3–4 秒）。30m 让演示期间几乎不会踩到卸载。
        self.keep_alive = (os.environ.get("LLM_KEEP_ALIVE") or "30m").strip()

        # 检索（Phase 6.2）
        self.rag = RAGConfig()

    @property
    def num_ctx(self):
        """旧属性名，保留以免外部脚本静默失效。新代码请用 context_length。"""
        return self.context_length

    def validate(self):
        """返回配置层面的问题列表（空列表 = 没问题）。不抛异常，交给健康检查展示。"""
        problems = []
        if self.provider not in VALID_PROVIDERS:
            problems.append("LLM_PROVIDER=%r 不受支持，可选：%s"
                            % (self.provider, " / ".join(VALID_PROVIDERS)))
        if self.provider == "siliconflow" and not self.siliconflow_api_key:
            problems.append("LLM_PROVIDER=siliconflow 但未设置 SILICONFLOW_API_KEY")
        if self.provider == "siliconflow" and not self.siliconflow_model:
            problems.append("LLM_PROVIDER=siliconflow 但未设置 SILICONFLOW_MODEL")
        if self.timeout <= 0:
            problems.append("OLLAMA_TIMEOUT 必须为正数")
        if not self.keep_alive:
            problems.append("LLM_KEEP_ALIVE 不能为空（可填 30m / 0 / -1）")
        if self.context_length < 2048:
            problems.append("LLM_CONTEXT_LENGTH 过小（建议 ≥4096）")
        if not (1 <= self.max_evidence <= 10):
            problems.append("LLM_MAX_EVIDENCE 必须在 1–10 之间（超过 10 条 4B 模型会退化）")
        if self.max_output_tokens >= self.context_length:
            # 这不是「建议」，是硬冲突：输出上限撞满窗口时，输入永远放不下
            problems.append(
                "输出预算 LLM_MAX_OUTPUT_TOKENS=%d 必须小于上下文窗口 "
                "LLM_CONTEXT_LENGTH=%d" % (self.max_output_tokens, self.context_length))
        if not (0.0 <= self.temperature <= 1.5):
            problems.append("OLLAMA_TEMPERATURE 超出合理范围 0–1.5")
        problems.extend(self.rag.validate())
        return problems

    def public_view(self):
        """给健康检查用的可公开字段：不含 key、不含内部路径。"""
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model": (self.ollama_model if self.provider == "ollama"
                      else self.siliconflow_model if self.provider == "siliconflow"
                      else "mock"),
            "timeout_seconds": self.timeout,
            # 输出预算与上下文窗口分开报：这两个数字混在一起正是 6.2 的根因
            "context_length": self.context_length,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "enable_thinking": self.enable_thinking,
            "keep_alive": self.keep_alive,
            "max_evidence": self.max_evidence,
            "semantic_check": self.semantic_check,
            "deprecations": list(self.deprecations),
            "rag": self.rag.public_view(),
        }

    def __repr__(self):
        return "<AIConfig provider=%s model=%s enabled=%s>" % (
            self.provider, self.public_view()["model"], self.enabled)


def load_config():
    return AIConfig()
