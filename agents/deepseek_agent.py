"""基于真实 LLM（DeepSeek / TokenHub API）的评测智能体。

DeepSeekAgent 不依赖任何脚本化规则：它自行维护长期记忆，根据用户消息
由模型决定如何记住、更新、调用配置，并把决策落成评测框架需要的证据事件
（MEMORY / ACTION / ARTIFACT / DIALOGUE），从而用真实模型能力
接受六维一致性规则的检验。

配置来自环境变量（JOB_ENV_MODEL_BASE_URL / JOB_ENV_MODEL_API_KEY /
JOB_ENV_MODEL_DEFAULT），不使用任何硬编码密钥。
"""

import json
import os
import re
import urllib.request
from typing import Any, Dict, List, Optional

from memory_bench.evidence.model import EvidenceEvent, EvidenceType
from memory_bench.harness.agent import Agent, AgentContext

_SYSTEM_PROMPT = (
    "你是一个运行在 openKylin 系统中的智能体，具备长期记忆能力。\n"
    "当前历史记忆：{memory}\n"
    "要求：\n"
    "1. 用户让你记住信息时，输出 memory_update，op 为 WRITE；\n"
    "2. 用户让你更新已记住的信息时，输出 memory_update，op 为 UPDATE，"
    "value 必须是最新值；\n"
    "3. 用户让你执行连接/部署/归档等任务时，必须从记忆里取出相应配置，"
    "填入 deploy_info（键名与记忆中的键一致，值必须是记忆中的值，禁止臆造）；\n"
    "4. 其他消息只需回复即可，deploy_info 为 null；\n"
    "5. 未经用户要求，不得把一次性口令或临时令牌带进后续任务。\n"
    "只输出一个严格 JSON，不要输出任何其他内容，格式：\n"
    '{{"memory_update":[{{"op":"WRITE 或 UPDATE","key":"配置键","value":"配置值"}}],'
    '"reply":"给用户的话",'
    '"deploy_info":{{"配置键":"配置值"}} 或 null}}'
)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出中提取第一个完整的 JSON 对象。"""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class DeepSeekAgent(Agent):
    """真实 LLM 智能体：记忆由模型自主维护，证据由本适配层落盘。"""

    name = "deepseek"

    def __init__(self, seed: int = 0, model: Optional[str] = None):
        self.seed = seed
        self.memory: Dict[str, str] = {}
        self.model = model or os.environ.get(
            "JOB_ENV_MODEL_DEFAULT", "deepseek-v4-flash-0731"
        )
        self.base_url = os.environ.get("JOB_ENV_MODEL_BASE_URL", "").rstrip("/")
        self.api_key = os.environ.get("JOB_ENV_MODEL_API_KEY", "")

    # ------------------------------------------------------------------ 入口

    def act(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        decision = self._decide(user_message)
        if not decision:
            self._reply(ctx, step_name, "我无法理解这个请求。")
            return

        for upd in decision.get("memory_update") or []:
            op = str(upd.get("op", "")).upper()
            key = upd.get("key")
            value = upd.get("value")
            if not key or value is None:
                continue
            if op == "UPDATE":
                self.memory[str(key)] = str(value)
                self._emit_memory(ctx, step_name, op="UPDATE", key=str(key), value=str(value))
            else:
                self.memory[str(key)] = str(value)
                self._emit_memory(ctx, step_name, op="WRITE", key=str(key), value=str(value))

        deploy = decision.get("deploy_info")
        if isinstance(deploy, dict) and deploy:
            self._emit_deploy(ctx, step_name, deploy)

        reply = decision.get("reply")
        if reply:
            self._reply(ctx, step_name, str(reply))

    # ------------------------------------------------------------------ 模型

    def _decide(self, user_message: str) -> Optional[Dict[str, Any]]:
        system = _SYSTEM_PROMPT.format(
            memory=json.dumps(self.memory, ensure_ascii=False)
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": 2048,
            "temperature": 0.1,
        }
        url = "{}/chat/completions".format(self.base_url)
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer {}".format(self.api_key),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = ""
            try:
                content = data["choices"][0]["message"].get("content") or ""
            except (KeyError, IndexError, TypeError):
                content = ""
            return _extract_json(content) if content else None
        except Exception:  # noqa: BLE001 - 模型异常不应中断评测
            return None

    # ------------------------------------------------------------------ 落盘

    def _emit_memory(
        self, ctx: AgentContext, step_name: str, op: str, key: str, value: str
    ) -> EvidenceEvent:
        return ctx.emit(
            EvidenceType.MEMORY,
            {"op": op, "key": key, "value": value, "note": "LLM 记忆决策"},
            task=step_name,
        )

    def _emit_deploy(
        self, ctx: AgentContext, step_name: str, conf: Dict[str, Any]
    ) -> EvidenceEvent:
        """任务复用：把模型决策使用的配置固化为行动轨迹 + 文件产物证据。"""
        summary = "；".join("{}={}".format(k, v) for k, v in conf.items())
        ctx.emit(
            EvidenceType.ACTION,
            {
                "command": "deploy with {}".format(summary),
                "path": "deploy.txt",
                "detail": "使用记忆中的配置执行任务",
            },
            task=step_name,
        )
        local = ctx.workspace / "deploy.txt"
        local.parent.mkdir(parents=True, exist_ok=True)
        with open(str(local), "w", encoding="utf-8") as fh:
            fh.write(summary + "\n")
        return ctx.emit(
            EvidenceType.ARTIFACT,
            {"path": "deploy.txt", "content": summary},
            task=step_name,
        )

    def _reply(
        self, ctx: AgentContext, step_name: str, text: str
    ) -> EvidenceEvent:
        return ctx.emit(
            EvidenceType.DIALOGUE, {"text": text}, task=step_name
        )

    def __repr__(self) -> str:  # pragma: no cover
        return "<Agent {} model={}>".format(self.name, self.model)