"""openKylin 智能体框架适配器（OpenKylinAgent）。

把 openKylin 智能体操作系统上运行的智能体接入评测框架：

- backend="http"：调用 openKylin 智能体服务 API（OpenAI 兼容或自定义 REST），
  请求体模板可替换 {user_message} / {memory_json} / {system_prompt}。
- backend="process"：调用 openKylin 智能体 CLI 可执行入口，
  命令含 {user_message} 时以参数填充，否则消息经 stdin 送入。

证据翻译复用 AdapterAgent 的三模式（json / MB 行协议 / text 启发式），
缺省优先引导 openKylin 智能体打印机器可读的 MB|...| 行，保证零解析接入。

与 OS 审计联动（核心差异）：
    传入 audit_collector（memory_bench.audit.AuditCollector）后，每次 act
    结束都会采集一次 OS 侧审计证据（journal / 进程 / 工作区文件差异），
    与智能体主动 emit 的证据写入同一 EvidenceStore，实现
    「智能体自证 ↔ OS 客观证据」交叉印证。

配置优先环境变量（MK_OK_*：memory-bench openkylin）：
    MK_OK_BACKEND / MK_OK_CMD / MK_OK_URL / MK_OK_METHOD / MK_OK_TOKEN /
    MK_OK_TEMPLATE / MK_OK_MODE / MK_OK_TIMEOUT / MK_OK_AUDIT / MK_OK_SYSTEM_PROMPT
"""

import json
import os
import re
import urllib.request
from typing import Any, Dict, Optional

import agents.adapter_agent as adapter

from memory_bench.evidence.model import EvidenceType
from memory_bench.harness.agent import AgentContext

_DEFAULT_SYSTEM_PROMPT = (
    "你是运行在 openKylin 智能体操作系统上的智能体。"
    "你具备长期记忆能力，可读写系统配置、执行命令并产出文件。\n"
    "请在每次回复时遵循机器可读协议：每行形如 "
    "MB|MEMORY|{\"op\":\"WRITE|UPDATE\",\"key\":\"...\",\"value\":\"...\"}、"
    "MB|ACTION|{\"command\":\"...\"}、MB|ARTIFACT|{\"path\":\"...\",\"content\":\"...\"}"
    "，最后用 MB|DIALOGUE|{\"text\":\"给用户的回复\"} 回复用户。\n"
    "执行动作前必须从记忆取出配置值并保持一致，禁止臆造。"
)

_DEFAULT_HTTP_TEMPLATE = (
    '{"messages":[{"role":"user","content":"{system_prompt}\\n用户消息：{user_message}"}],'
    ' "memory": {memory_json}}'
)

_MEM_REMEMBER = re.compile(r"记住[:：]?\s*([\w.-]+)\s*=\s*([^\s，。；,;（）()]+)")
_MEM_UPDATE = re.compile(r"改为[:：]?\s*([\w.-]+)\s*=\s*([^\s，。；,;（）()]+)")


class OpenKylinAgent(adapter.AdapterAgent):
    """openKylin 智能体框架适配器。

    继承 AdapterAgent 的双后端与三模式翻译，额外带来：
    - openKylin 默认系统提示与 HTTP 请求体模板；
    - 跨会话记忆持久化（复用 Agent 基类 save_state / load_state）；
    - 可选 OS 审计联动：act 后采集 OS 侧证据，交叉印证智能体行为。
    """

    name = "openkylin"

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
        system_prompt: Optional[str] = None,
        timeout: int = 90,
        audit_collector: Optional["Any"] = None,
        audit_session: str = "",
    ):
        backend = backend or os.environ.get("MK_OK_BACKEND") or "http"
        command = command or os.environ.get("MK_OK_CMD")
        url = url or os.environ.get("MK_OK_URL")
        method = (method or os.environ.get("MK_OK_METHOD") or "POST").upper()
        token = token if token is not None else os.environ.get("MK_OK_TOKEN", "")
        template = template or os.environ.get("MK_OK_TEMPLATE") or _DEFAULT_HTTP_TEMPLATE
        mode = mode or os.environ.get("MK_OK_MODE") or "auto"
        output = output or os.environ.get("MK_OK_OUTPUT") or "both"
        timeout = int(os.environ.get("MK_OK_TIMEOUT") or timeout)
        self.system_prompt = (
            system_prompt
            or os.environ.get("MK_OK_SYSTEM_PROMPT")
            or _DEFAULT_SYSTEM_PROMPT
        )
        self.audit_collector = audit_collector
        self.audit_session = audit_session

        super().__init__(
            seed=seed,
            backend=backend,
            command=command,
            url=url,
            method=method,
            token=token,
            template=template,
            mode=mode,
            output=output,
            timeout=timeout,
        )
        if prompt_prefix is not None:
            self.prompt_prefix = prompt_prefix
        elif os.environ.get("MK_OK_PROMPT_PREFIX"):
            self.prompt_prefix = os.environ.get("MK_OK_PROMPT_PREFIX")
        self.memory: Dict[str, str] = {}

    # ------------------------------------------------------------------ 入口

    def act(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        """调用 openKylin 智能体并把响应翻译为证据；若配置审计则采集 OS 证据。"""
        super().act(ctx, user_message, step_name)
        audit = self.audit_collector
        if audit is not None:
            audit_session = self.audit_session or ctx.session
            audit.collect(session=audit_session, task=step_name)

    # ------------------------------------------------------------------ 覆写

    def _invoke_http(self, user_message: str) -> str:
        """HTTP 后端：替换 {user_message} / {memory_json} / {system_prompt}。

        注意 {user_message} 与 {system_prompt} 在模板中处于 JSON 字符串内，
        替换前必须做 JSON 转义，避免换行/引号破坏请求体。
        """
        if not self.url:
            raise ValueError("backend=http 需要 url（或环境变量 MK_OK_URL）")

        def _json_encode_text(value: Any) -> str:
            # 模板中该值两侧已有 JSON 双引号，这里仅需转义内容、剥离外层引号
            return json.dumps(str(value), ensure_ascii=False)[1:-1]

        user_json = _json_encode_text(user_message)
        prompt_json = _json_encode_text(self.system_prompt)
        memory_json = json.dumps(self.memory, ensure_ascii=False)

        # 模板形如 {"content": "{user_message}"}，故替换时用转义后的 JSON 字符串。
        body = (
            self.template.replace("{user_message}", user_json)
            .replace("{system_prompt}", prompt_json)
            .replace("{memory_json}", memory_json)
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

    def _apply_text(self, ctx: AgentContext, text: str, step_name: str) -> bool:
        """text 模式：openKylin 智能体的自然语言启发式抽取。"""
        emitted = False
        for key, value in _MEM_REMEMBER.findall(text):
            self.memory[key] = value
            ctx.emit(
                EvidenceType.MEMORY,
                {
                    "op": "WRITE",
                    "key": key,
                    "value": value,
                    "note": "openKylin 文本启发式识别到记忆写入",
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
                    "note": "openKylin 文本启发式识别到记忆更新",
                },
                task=step_name,
            )
            emitted = True
        return emitted

    def __repr__(self) -> str:  # pragma: no cover
        audit_on = "audit=on" if self.audit_collector else "audit=off"
        return "<OpenKylinAgent backend={} mode={} {}>".format(
            self.backend, self.mode, audit_on
        )