"""通用外部智能体适配器（AdapterAgent 骨架）。

AdapterAgent 把任意外部智能体接入评测框架，无需改动场景与规则引擎：

- backend="process"：以子进程方式调用外部智能体的可执行入口。命令模板中
  包含 ``{user_message}`` 时用消息内容填充后执行；否则把 user_message 经
  stdin 送入，分析 stdout/stderr。
- backend="http"：以 HTTP 方式调用外部智能体的 API。请求体模板中的
  ``{user_message}`` / ``{memory_json}`` 会被替换为当前消息与当前记忆。

证据翻译（evidence translation）是接入外部智能体的关键一步：规则引擎只认
MEMORY / ACTION / ARTIFACT / DIALOGUE 四类结构化证据，而外部智能体通常
只输出自由文本。本骨架提供三种翻译模式：

- mode="json"（兼容 deepseek 决策格式）：{memory_update, reply, action, artifact}。
- mode="mb"：识别一行 ``MB|<TYPE>|<json>`` 的机器可读协议，外部智能体只要
  打印这类行即可零解析接入。
- mode="text"：启发式正则，从自然语言抽取「记住 k=v」「k 改为 v」等语义。

默认 mode="auto"：依次尝试 json → mb → text，全部失败时把原文落为 DIALOGUE，
保证每次交互都产出证据、评测不中断。子类可覆写 _apply_text 扩展自然语言解析。

运行配置可在构造时传入，也支持环境变量（MB_ADAPTER_BACKEND /
MB_ADAPTER_CMD / MB_ADAPTER_URL / MB_ADAPTER_METHOD / MB_ADAPTER_TOKEN /
MB_ADAPTER_TEMPLATE / MB_ADAPTER_MODE / MB_ADAPTER_OUTPUT），便于 CLI 直接使用。
MB_ADAPTER_OUTPUT 控制子进程后端取回哪些输出：both（默认，stdout+stderr）/
stdout / stderr。
"""

import json
import os
import re
import shlex
import subprocess
import urllib.request
from typing import Any, Dict, Optional

from memory_bench.evidence.model import EvidenceType
from memory_bench.harness.agent import Agent, AgentContext

_MEM_REMEMBER = re.compile(r"记住[:：]?\s*([\w.-]+)\s*=\s*([^\s，。；,;（）()]+)")
_MEM_UPDATE = re.compile(r"改为[:：]?\s*([\w.-]+)\s*=\s*([^\s，。；,;（）()]+)")
_MB_LINE = re.compile(r"^\s*MB\|([A-Z]+)\|(.*?)\s*$")
_MB_TYPES = {"MEMORY", "ACTION", "ARTIFACT", "DIALOGUE"}
_MEMORY_OPS = {"WRITE", "UPDATE", "READ", "DELETE", "QUERY"}


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """从文本中提取第一个完整的 JSON 对象。"""
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


class AdapterAgent(Agent):
    """外部智能体适配器：子进程 / HTTP 双后端 + 三种证据翻译模式。"""

    name = "adapter"

    def __init__(
        self,
        seed: int = 0,
        backend: Optional[str] = None,
        command: Optional[str] = None,
        url: Optional[str] = None,
        method: str = "POST",
        token: Optional[str] = None,
        template: Optional[str] = None,
        mode: Optional[str] = None,
        output: Optional[str] = None,
        prompt_prefix: Optional[str] = None,
        timeout: int = 60,
    ):
        self.seed = seed
        self.backend = (
            backend or os.environ.get("MB_ADAPTER_BACKEND") or "process"
        ).lower()
        self.command = command or os.environ.get("MB_ADAPTER_CMD")
        self.url = url or os.environ.get("MB_ADAPTER_URL")
        self.method = (
            method or os.environ.get("MB_ADAPTER_METHOD") or "POST"
        ).upper()
        self.token = (
            token if token is not None else os.environ.get("MB_ADAPTER_TOKEN", "")
        )
        self.template = (
            template
            or os.environ.get("MB_ADAPTER_TEMPLATE")
            or '{"user_message": "{user_message}"}'
        )
        self.mode = (mode or os.environ.get("MB_ADAPTER_MODE") or "auto").lower()
        self.output = (output or os.environ.get("MB_ADAPTER_OUTPUT") or "both").lower()
        self.prompt_prefix = (
            prompt_prefix
            if prompt_prefix is not None
            else os.environ.get("MB_ADAPTER_PROMPT_PREFIX", "")
        )
        self.timeout = int(os.environ.get("MB_ADAPTER_TIMEOUT") or timeout)
        self.memory: Dict[str, str] = {}

    # ------------------------------------------------------------------ 入口

    def act(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        sending = user_message
        if self.prompt_prefix:
            sending = self.prompt_prefix + "\n" + user_message
        try:
            raw = self._invoke(sending)
        except Exception as exc:  # noqa: BLE001 - 外部调用失败不应中断评测
            ctx.emit(
                EvidenceType.DIALOGUE,
                {"text": "外部智能体调用失败：{}".format(exc)},
                task=step_name,
                metadata={"adapter_error": str(exc)},
            )
            return
        text = (raw or "").strip()
        if not text:
            ctx.emit(
                EvidenceType.DIALOGUE, {"text": "(外部智能体无输出)"}, task=step_name
            )
            return
        self._translate(ctx, text, step_name)

    # ------------------------------------------------------------------ 后端

    def _invoke(self, user_message: str) -> str:
        if self.backend == "http":
            return self._invoke_http(user_message)
        return self._invoke_process(user_message)

    def _invoke_process(self, user_message: str) -> str:
        """子进程后端：命令含 {user_message} 则填充为参数，否则 stdin 送入。"""
        if not self.command:
            raise ValueError("backend=process 需要 command（或环境变量 MB_ADAPTER_CMD）")
        argv = shlex.split(self.command)
        stdin_data = user_message
        if "{user_message}" in self.command:
            argv = shlex.split(self.command.format(user_message=user_message))
            stdin_data = None
        proc = subprocess.run(
            argv,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if self.output == "stdout":
            return proc.stdout or ""
        if self.output == "stderr":
            return proc.stderr or ""
        return (proc.stdout or "") + "\n" + (proc.stderr or "")

    def _invoke_http(self, user_message: str) -> str:
        """HTTP 后端：请求体模板替换 {user_message} / {memory_json} 后 POST。"""
        if not self.url:
            raise ValueError("backend=http 需要 url（或环境变量 MB_ADAPTER_URL）")
        body = self.template.replace("{user_message}", user_message).replace(
            "{memory_json}", json.dumps(self.memory, ensure_ascii=False)
        )
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer {}".format(self.token)
        req = urllib.request.Request(
            self.url,
            data=body.encode("utf-8"),
            headers=headers,
            method=self.method,
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8")

    # ------------------------------------------------------------------ 翻译

    def _translate(self, ctx: AgentContext, text: str, step_name: str) -> None:
        mode = self.mode
        if mode in ("auto", "json"):
            parsed = _extract_json(text)
            if parsed and self._apply_json(ctx, parsed, step_name):
                return
        if mode in ("auto", "mb"):
            if self._apply_mb_lines(ctx, text, step_name):
                return
        if mode in ("auto", "text"):
            if self._apply_text(ctx, text, step_name):
                return
        ctx.emit(EvidenceType.DIALOGUE, {"text": text}, task=step_name)

    def _apply_json(
        self, ctx: AgentContext, parsed: Dict[str, Any], step_name: str
    ) -> bool:
        """json 模式：deepseek 决策格式 {memory_update, reply, action, artifact}。"""
        emitted = False
        for upd in parsed.get("memory_update") or []:
            key = upd.get("key")
            value = upd.get("value")
            if not key or value is None:
                continue
            op = str(upd.get("op", "WRITE")).upper()
            if op not in _MEMORY_OPS:
                op = "WRITE"
            self.memory[str(key)] = str(value)
            ctx.emit(
                EvidenceType.MEMORY,
                {
                    "op": op,
                    "key": str(key),
                    "value": str(value),
                    "note": "外部智能体记忆决策",
                },
                task=step_name,
            )
            emitted = True
        action = parsed.get("action")
        if isinstance(action, dict) and action.get("command"):
            ctx.emit(
                EvidenceType.ACTION,
                {
                    "command": str(action.get("command")),
                    "path": action.get("path"),
                    "detail": str(action.get("detail") or ""),
                },
                task=step_name,
            )
            emitted = True
        artifact = parsed.get("artifact")
        if isinstance(artifact, dict) and artifact.get("path"):
            self._write_artifact(
                ctx,
                step_name,
                str(artifact["path"]),
                str(artifact.get("content") or ""),
            )
            emitted = True
        reply = parsed.get("reply")
        if isinstance(reply, str) and reply.strip():
            ctx.emit(EvidenceType.DIALOGUE, {"text": reply}, task=step_name)
            emitted = True
        return emitted

    def _apply_mb_lines(self, ctx: AgentContext, text: str, step_name: str) -> bool:
        """mb 模式：识别 ``MB|<TYPE>|<json>`` 机器可读行。"""
        emitted = False
        for line in text.splitlines():
            match = _MB_LINE.match(line)
            if not match:
                continue
            etype = match.group(1)
            if etype not in _MB_TYPES:
                continue
            try:
                payload = json.loads(match.group(2)) or {}
            except ValueError:
                continue
            self._emit_mb(ctx, step_name, etype, payload)
            emitted = True
        return emitted

    def _emit_mb(self, ctx: AgentContext, step_name: str, etype: str, payload: Dict) -> None:
        if etype == "MEMORY":
            key = payload.get("key")
            value = payload.get("value")
            if not key or value is None:
                return
            op = str(payload.get("op", "WRITE")).upper()
            if op not in _MEMORY_OPS:
                op = "WRITE"
            self.memory[str(key)] = str(value)
            ctx.emit(
                EvidenceType.MEMORY,
                {
                    "op": op,
                    "key": str(key),
                    "value": str(value),
                    "note": str(payload.get("note") or "外部智能体记忆操作"),
                },
                task=step_name,
            )
        elif etype == "ACTION":
            ctx.emit(
                EvidenceType.ACTION,
                {
                    "command": str(payload.get("command") or ""),
                    "path": payload.get("path"),
                    "detail": str(payload.get("detail") or ""),
                },
                task=step_name,
            )
        elif etype == "ARTIFACT":
            path = payload.get("path")
            if path:
                self._write_artifact(
                    ctx, step_name, str(path), str(payload.get("content") or "")
                )
        elif etype == "DIALOGUE":
            ctx.emit(
                EvidenceType.DIALOGUE,
                {"text": str(payload.get("text") or "")},
                task=step_name,
            )

    def _apply_text(self, ctx: AgentContext, text: str, step_name: str) -> bool:
        """text 模式：自然语言启发式，抽取「记住 k=v」「k 改为 v」。"""
        emitted = False
        for key, value in _MEM_REMEMBER.findall(text):
            self.memory[key] = value
            ctx.emit(
                EvidenceType.MEMORY,
                {
                    "op": "WRITE",
                    "key": key,
                    "value": value,
                    "note": "文本启发式识别到记忆写入",
                },
                task=step_name,
            )
            emitted = True
        for key, value in _MEM_UPDATE.findall(text):
            self.memory[key] = value
            ctx.emit(
                EvidenceType.MEMORY,
                {
                    "op": "UPDATE",
                    "key": key,
                    "value": value,
                    "note": "文本启发式识别到记忆更新",
                },
                task=step_name,
            )
            emitted = True
        return emitted

    # ------------------------------------------------------------------ 工具

    def _write_artifact(self, ctx: AgentContext, step_name: str, path: str, content: str) -> None:
        local = ctx.workspace / path
        local.parent.mkdir(parents=True, exist_ok=True)
        with open(str(local), "w", encoding="utf-8") as fh:
            fh.write(content)
        ctx.emit(
            EvidenceType.ARTIFACT,
            {"path": path, "content": content.strip()},
            task=step_name,
        )

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return "<Agent {} backend={} mode={}>".format(self.name, self.backend, self.mode)