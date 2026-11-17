"""
Mock Provider：只给测试与离线演示用，**不允许在生产配置里启用**。

它存在的理由很具体：CI 里没有 Ollama，若测试依赖真实模型，
要么测试从不运行，要么 CI 长期红。用 mock 把「协议与校验」测透，
真实模型留给本地集成测试（方案 §15 的要求）。
"""

from .base import BaseLLMProvider, InvalidOutput

# 一份最小但结构完整的策略 JSON，用于打通「生成 → 校验 → 持久化」链路
CANNED = """{
  "app_id": 1203220,
  "game_name": "永劫无间",
  "strategy_name": "匹配体验说明与回流沟通",
  "execution_status": "suggested",
  "target_group": {
    "description": "近期在公开评论中表达匹配体验不满的玩家群体",
    "basis": ["评论主题候选：服务器与连接"],
    "limitations": ["无法识别具体玩家身份"]
  },
  "problem_analysis": {
    "main_problem": "匹配与连接相关负面反馈集中",
    "confidence": "medium"
  },
  "recall_actions": [
    {"title": "发布匹配机制说明", "objective": "回应疑虑", "action_type": "community_communication", "description": "在官方社区说明匹配逻辑", "priority": "high"},
    {"title": "补偿性登录活动", "objective": "拉回短期活跃", "action_type": "in_game_event", "description": "限时登录奖励", "priority": "medium"},
    {"title": "反馈收集帖", "objective": "定向收集问题", "action_type": "community_communication", "description": "置顶收集帖并公开回复率", "priority": "medium"}
  ],
  "recall_copy": [{"channel": "community", "title": "关于匹配体验的说明", "content": "示例文案"}],
  "evaluation_metrics": [{"name": "负面评论占比", "description": "观察变化", "measurement_window": "7_days", "limitation": "不能单独作为召回转化率"}],
  "limitations": ["本策略基于公开数据", "尚未执行任何运营活动"]
}"""


class MockProvider(BaseLLMProvider):
    name = "mock"

    def __init__(self, config, payload=None, fail=None):
        super().__init__(config)
        self.payload = payload if payload is not None else CANNED
        self.fail = fail          # 传异常实例即可模拟失败路径

    def generate(self, prompt, system_prompt="", response_schema=None):
        if self.fail:
            raise self.fail
        if self.payload is None:
            raise InvalidOutput("mock 未配置 payload")
        self.last_duration_ms = 1
        return self.payload

    def health(self):
        return {"available": True, "model": "mock", "detail": "mock provider（仅测试）"}
