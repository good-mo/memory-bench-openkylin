"""工具契约解析：把 OAS/OpenAPI 文档转成可评测的工具规格。

M7 开放接入：被测智能体通过「外部工具调用」暴露能力时，用 OpenAPI
描述工具契约（operationId / 参数 / 扩展标记）。本模块解析 OAS 3.x
（JSON 形式）并提取两 类 `x-memory` 扩展标记：

- `x-memory.remember`：声明该工具参数的值应当来自智能体长期记忆
  （如"ttl 默认值用我记住的偏好"），供「工具调用长期记忆」规则校验；
- `x-memory.sensitive`：声明该参数含敏感/一次性信息，不得在后续
  工具调用中长期复用，供「工具敏感参数边界」校验。

仅用 Python 标准库解析 JSON（OAS 3 的 JSON 子集完全合法）。
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_X_MEMORY = "x-memory"


@dataclass
class ToolParam:
    """工具的一个参数。"""

    name: str
    location: str                      # query / path / header / cookie / body
    required: bool = False
    memory_key: Optional[str] = None   # x-memory.remember：应从哪条记忆键取值
    sensitive: bool = False            # x-memory.sensitive：敏感/一次性，不得复用
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "name": self.name,
            "location": self.location,
            "required": self.required,
        }
        if self.memory_key:
            data["memory_key"] = self.memory_key
        if self.sensitive:
            data["sensitive"] = True
        if self.description:
            data["description"] = self.description
        return data


@dataclass
class ToolSpec:
    """一个外部工具（OAS operation）。"""

    operation_id: str
    method: str
    path: str
    summary: str = ""
    params: List[ToolParam] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "method": self.method,
            "path": self.path,
            "summary": self.summary,
            "params": [p.to_dict() for p in self.params],
        }

    def param(self, name: str) -> Optional[ToolParam]:
        for p in self.params:
            if p.name == name:
                return p
        return None


@dataclass
class OasSpec:
    """解析后的 OAS 文档。"""

    title: str = ""
    version: str = ""
    tools: Dict[str, ToolSpec] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "version": self.version,
            "tools": {k: v.to_dict() for k, v in self.tools.items()},
        }

    def tool(self, operation_id: str) -> Optional[ToolSpec]:
        return self.tools.get(operation_id)


_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")


def _parse_params(raw: Any) -> List[ToolParam]:
    if raw is None:
        return []
    params: List[ToolParam] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        if not name:
            continue
        x_memory = item.get(_X_MEMORY)
        if not isinstance(x_memory, dict):
            x_memory = {}
        memory_key = x_memory.get("remember")
        sensitive = bool(x_memory.get("sensitive", False))
        schema = item.get("schema") or {}
        if not isinstance(schema, dict):
            schema = {}
        params.append(
            ToolParam(
                name=name,
                location=str(item.get("in", "query")),
                required=bool(item.get("required", False)),
                memory_key=str(memory_key) if memory_key else None,
                sensitive=sensitive,
                description=str(item.get("description", "") or schema.get("description", "")),
            )
        )
    return params


def parse_oas(doc: Dict[str, Any]) -> OasSpec:
    """解析 OAS 3.x 文档（dict），返回 OasSpec。"""
    if not isinstance(doc, dict):
        raise ValueError("OAS 文档顶层必须是对象")
    version = str(doc.get("openapi", ""))
    if not version.startswith("3."):
        raise ValueError("仅支持 OpenAPI 3.x 文档，实际：{}".format(version or "未知"))

    info = doc.get("info") or {}
    spec = OasSpec(
        title=str(info.get("title", "")),
        version=str(info.get("version", "")),
    )

    paths = doc.get("paths")
    if paths is None:
        raise ValueError("OAS paths 缺失")
    if not isinstance(paths, dict):
        raise ValueError("OAS paths 必须是对象")
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in _METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId", ""))
            if not operation_id:
                operation_id = "{}_{}".format(method.upper(), path.replace("/", "_"))
            tool = ToolSpec(
                operation_id=operation_id,
                method=method,
                path=path,
                summary=str(operation.get("summary", "")),
                params=_parse_params(operation.get("parameters")),
            )
            spec.tools[operation_id] = tool
    return spec


def load_oas(path: str) -> OasSpec:
    """从 JSON 文件加载 OAS 文档。"""
    if not os.path.isfile(path):
        raise ValueError("OAS 文档不存在：{}".format(path))
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        raise ValueError("OAS 文档顶层必须是对象：{}".format(path))
    return parse_oas(doc)