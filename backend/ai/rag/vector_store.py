"""
向量存储抽象（Phase 6.2）。

方案 §2.3 明确要求：业务层不能直接依赖 ChromaDB 的内部对象，
否则「换 Qdrant」这句话就只是文档里的一行字。

两个口径必须写死，否则检索分数无法解释（方案 §6.3）：

1. **距离 → 相似度**：本项目的 store 统一使用 cosine 空间，
   Chroma 返回的 distance = 1 - cosine_similarity，
   因此 score = 1 - distance。score ∈ [-1, 1]，越大越相似。
   绝不把 distance 直接当相似度展示。
2. **app_id 过滤发生在检索阶段**，由 store 实现负责把它翻译成底层查询条件，
   不允许「先查全库再在 Python 里筛」。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Chunk:
    """待写入向量库的最小单元。"""

    chunk_id: str
    document_id: str
    app_id: int                 # 全局文档固定为 GLOBAL_APP_ID
    scope: str                  # game_specific | global
    chunk_index: int
    content: str
    embedding: list = field(default_factory=list)
    title: str = ""
    source_type: str = ""
    source_name: str = ""
    source_url: str = ""
    publish_date: str = ""
    status: str = "active"
    tags: str = ""              # 逗号分隔：Chroma 元数据不接受 list 类型
    embedding_model: str = ""

    def metadata(self):
        """Chroma 元数据只接受 str/int/float/bool，这里统一转换。"""
        return {
            "document_id": self.document_id,
            "app_id": int(self.app_id),
            "scope": self.scope,
            "chunk_index": int(self.chunk_index),
            "title": self.title or "",
            "source_type": self.source_type or "",
            "source_name": self.source_name or "",
            "source_url": self.source_url or "",
            "publish_date": self.publish_date or "",
            "status": self.status,
            "tags": self.tags or "",
            "embedding_model": self.embedding_model or "",
        }


GLOBAL_APP_ID = -1          # 全局文档在向量库里的 app_id；SQLite 侧存 NULL


@dataclass
class SearchHit:
    chunk_id: str
    document_id: str
    app_id: int
    scope: str
    chunk_index: int
    content: str
    score: float                # 1 - cosine_distance
    distance: float
    title: str = ""
    source_type: str = ""
    source_name: str = ""
    source_url: str = ""
    publish_date: str = ""

    def to_dict(self, with_content=True):
        data = {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "app_id": self.app_id,
            "scope": self.scope,
            "chunk_index": self.chunk_index,
            "title": self.title,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "publish_date": self.publish_date,
            "score": round(self.score, 4),
        }
        if with_content:
            data["content"] = self.content
        return data


class VectorStoreUnavailable(RuntimeError):
    """向量库不可用（依赖缺失 / 目录不可写 / collection 损坏）。"""


class VectorStore(ABC):
    name = "base"

    @abstractmethod
    def add_chunks(self, chunks):
        """批量 upsert。相同 chunk_id 覆盖写，保证重复导入幂等。"""

    @abstractmethod
    def search(self, query_vector, app_id, top_k=5, include_global=True):
        """按 app_id 过滤后检索。返回 SearchHit 列表，按 score 降序。"""

    @abstractmethod
    def delete_document(self, document_id):
        """按文档删除其全部 chunk。返回删除条数。"""

    @abstractmethod
    def count(self, app_id=None):
        ...

    @abstractmethod
    def health(self):
        """返回 {available, detail, ...}。不抛异常——健康检查自己崩掉最糟。"""
