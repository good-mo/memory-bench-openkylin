"""agents 包：可插拔的评测智能体实现。"""

from agents.dummy_agent import BadDummyAgent, DummyAgent

__all__ = ["DummyAgent", "BadDummyAgent"]