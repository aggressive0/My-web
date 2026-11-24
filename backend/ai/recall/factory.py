"""
策略生成器的装配（Phase 6.5）。

存在的唯一理由：`/api/recall/generate` 与 `/api/knowledge/answer` 都要造一个生成器，
两处各写一遍参数，迟早会出现「一个接口用 min_actions=3、另一个用默认 1」这种
看不见的分叉——这次就是因为两个入口参数不一致才被发现要合并的。
"""

from .generator import StrategyGenerator
from .metrics import MetricsCollector
from .store import RecallStrategyStore

# 同一个库路径只建一个 store（它就是 sqlite 连接的包装，没有状态）
_STORES = {}


def build_generator(provider, rag, cfg, store=None):
    """按 AI 配置装配生成器。provider 为 None 时仍返回生成器，由它如实报失败。"""
    db_path = cfg.rag.knowledge_db
    if store is None:
        store = _STORES.get(db_path)
        if store is None:
            store = RecallStrategyStore(db_path)
            store.init_schema()
            _STORES[db_path] = store
    return StrategyGenerator(
        provider, rag, MetricsCollector(db_path), store=store,
        min_actions=cfg.min_actions, max_actions=cfg.max_actions)
