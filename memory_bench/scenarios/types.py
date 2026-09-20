"""场景数据模型：Scenario / Step / Fact 定义与 JSON 加载校验。"""

import enum
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


class StepType(enum.Enum):
    INJECT = "INJECT"          # 注入事实
    DISTRACT = "DISTRACT"      # 干扰任务
    UPDATE = "UPDATE"          # 更新事实
    PROBE = "PROBE"            # 探针任务
    CHECKPOINT = "CHECKPOINT"  # 检查点


@dataclass
class Fact:
    """一条长期记忆事实，如 {"server_ip": "192.168.1.100", "port": "22"}。"""

    id: str
    fields: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "fields": self.fields}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Fact":
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise ValueError("Fact.id 必填且必须为非空字符串")
        fields = data.get("fields")
        if not isinstance(fields, dict):
            raise ValueError("Fact.fields 必须是对象/字典")
        return cls(id=data["id"], fields={str(k): str(v) for k, v in fields.items()})

    def __repr__(self) -> str:  # pragma: no cover
        return "Fact({})".format(self.id)


_STEP_TYPE_VALUES = {t.value for t in StepType}


@dataclass
class Step:
    """场景中的一个步骤。

    session: 可选字段，指定该步骤所属会话（用于跨会话持久化评测）。
        为 None 时沿用编排器上下文中的当前会话。
    rollback: 可选标志，UPDATE 步骤为 True 时表示对相关键执行「回滚」
        （恢复为最早权威值）而非普通覆盖，供冲突回滚评测使用。
    """

    type: StepType
    name: str
    description: str
    facts: List[Fact] = field(default_factory=list)
    expected: Dict[str, str] = field(default_factory=dict)
    session: Optional[str] = None
    rollback: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "type": self.type.value,
            "name": self.name,
            "description": self.description,
            "facts": [f.to_dict() for f in self.facts],
            "expected": self.expected,
        }
        if self.session is not None:
            data["session"] = self.session
        if self.rollback:
            data["rollback"] = True
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Step":
        if not isinstance(data.get("type"), str):
            raise ValueError("Step.type 必填")
        if data["type"] not in _STEP_TYPE_VALUES:
            raise ValueError("Step.type 非法：{}".format(data["type"]))
        for field_name in ("name", "description"):
            if not isinstance(data.get(field_name), str):
                raise ValueError("Step.{} 必填且必须为字符串".format(field_name))
        facts = [Fact.from_dict(f) for f in data.get("facts", [])]
        expected = data.get("expected", {})
        if not isinstance(expected, dict):
            raise ValueError("Step.expected 必须是对象/字典")
        session = data.get("session")
        if session is not None and not isinstance(session, str):
            raise ValueError("Step.session 必须是字符串或空")
        rollback = bool(data.get("rollback", False))
        return cls(
            type=StepType(data["type"]),
            name=data["name"],
            description=data["description"],
            facts=facts,
            expected={str(k): str(v) for k, v in expected.items()},
            session=session,
            rollback=rollback,
        )

    def __repr__(self) -> str:  # pragma: no cover
        return "Step({}: {})".format(self.type.value, self.name)


@dataclass
class Scenario:
    """一个评测场景（多条步骤组成的长期记忆测评流程）。

    oas: 可选，OAS/OpenAPI 文档（dict 或文件路径字符串）。
        提供后启用「外部工具调用」开发维度（M7）：
        工具契约中的 x-memory 标记决定哪些参数复用记忆、哪些敏感参数不可复用。
    """

    id: str
    name: str
    dimension: str
    seed: int
    steps: List[Step] = field(default_factory=list)
    oas: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "name": self.name,
            "dimension": self.dimension,
            "seed": self.seed,
            "steps": [s.to_dict() for s in self.steps],
        }
        if self.oas is not None:
            data["oas"] = self.oas
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Scenario":
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise ValueError("Scenario.id 必填且必须为非空字符串")
        for field_name in ("name", "dimension"):
            if not isinstance(data.get(field_name), str):
                raise ValueError("Scenario.{} 必填且必须为字符串".format(field_name))
        if not isinstance(data.get("seed"), int):
            raise ValueError("Scenario.seed 必须是整数")
        if not isinstance(data.get("steps"), list) or not data["steps"]:
            raise ValueError("Scenario.steps 必须是非空列表")
        steps = [Step.from_dict(s) for s in data["steps"]]
        oas = data.get("oas")
        return cls(
            id=data["id"],
            name=data["name"],
            dimension=data["dimension"],
            seed=int(data["seed"]),
            steps=steps,
            oas=oas,
        )

    def __repr__(self) -> str:  # pragma: no cover
        return "Scenario({}: {}[{}])".format(self.id, self.dimension, self.name)


def load_scenario(path_or_dict: Union[str, os.PathLike, Dict[str, Any]]) -> Scenario:
    """加载场景：支持 JSON 文件路径或 dict，逐字段校验。

    - 传入字符串/路径：读取 JSON 文件内容后加载。
    - 传入 dict：直接加载。
    """
    if isinstance(path_or_dict, dict):
        return Scenario.from_dict(path_or_dict)
    path = os.fspath(path_or_dict)
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("场景 JSON 顶层必须是对象")
    return Scenario.from_dict(data)