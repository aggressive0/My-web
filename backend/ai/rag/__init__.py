"""
轻量 RAG 业务层（Phase 6.2）。

分层与依赖方向（与 AGENTS.md §3.2 一致）：

    document_loader  → 只负责把文件变成文本，不认识向量
    document_cleaner → 只做确定性清理，不认识切分
    document_splitter→ 只负责切分，不认识存储
    embedding_service→ 只负责文本→向量，不认识数据库
    vector_store     → 抽象接口；chroma_store 是唯一实现
    knowledge_repository → SQLite 元数据（知识库的「账本」）
    retriever        → 组合 embedder + store，产出可追溯的证据
    service          → 编排导入/删除/检索，供路由调用

两条不允许打破的约束：
1. 向量检索必须在查询阶段带 app_id 过滤，不能「先全查后筛」——那等于把
   别的游戏的内容先取出来再丢掉，隔离就只剩个说法。
2. 原始文本与向量分开存：SQLite 保存 content 与 content_hash，Chroma 只存
   chunk 文本与向量。模型换版本后可以只重算向量，不用重新拿原始文件。
"""

__all__ = [
    "document_cleaner",
    "document_loader",
    "document_splitter",
    "embedding_service",
    "vector_store",
    "chroma_store",
    "knowledge_repository",
    "retriever",
    "service",
]
