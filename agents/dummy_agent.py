"""演示用脚本化智能体：DummyAgent（好）与 BadDummyAgent（坏）。

DummyAgent 不调用任何真实 LLM，仅按用户消息关键词脚本化决策：

- 消息含「记住」→ 解析 "记住 key=value"，逐条写入记忆（MEMORY.WRITE），并口头确认。
- 消息含「改为」→ 解析 "改为 key=value"，更新记忆（MEMORY.UPDATE，value 为新值），并口头确认。
- 消息含「执行/部署/连接」→ 读取记忆中的服务器信息（或下载目录偏好）执行动作：
    * 有 server_ip/port：构造 ssh 命令并产出 deploy.txt（演示动态更新维度）；
    * 有 download_dir：构造归档命令并产出归档清单（演示长期保留维度）；
    * 都没有：回复「我找不到服务器信息」，metadata 标记 missing_memory 且不产生动作。
- 其他消息 → 仅回复中性话，不产生记忆/文件操作。

BadDummyAgent 是「坏」变体：收到「改为」时只口头宣称已更新，但内部记忆保持旧值，
证据里却上报新值（作弊式遗忘）。随后探针步骤仍使用旧值行动，从而被
memory_behavior_check / time_update_check 抓出旧值残留 FAIL。
"""

import re
from typing import Dict, Optional

from memory_bench.evidence.model import EvidenceEvent, EvidenceType
from memory_bench.harness.agent import Agent, AgentContext

_REMEMBER = re.compile(r"(\w+)\s*=\s*([^\s，。；（）()]+)")
_CHANGE = re.compile(r"改为\s*(\w+)\s*=\s*([^\s，。；（）()]+)")


class DummyAgent(Agent):
    """脚本化「好」智能体：言行一致地记住、更新并使用记忆。"""

    name = "dummy"

    def __init__(self, seed: int = 0):
        self.seed = seed
        self.memory: Dict[str, str] = {}

    # ------------------------------------------------------------------ 入口

    def act(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        if "记住" in user_message:
            self._handle_remember(ctx, user_message, step_name)
        elif "改为" in user_message:
            self._handle_update(ctx, user_message, step_name)
        elif any(k in user_message for k in ("执行", "部署", "连接")):
            self._handle_probe(ctx, user_message, step_name)
        else:
            self._handle_distract(ctx, user_message, step_name)

    # ------------------------------------------------------------------ 分支

    def _handle_remember(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        """记忆写入：多条 "记住 key=value" 依次写入。"""
        matched = False
        for key, value in _REMEMBER.findall(user_message):
            matched = True
            self.memory[key] = value
            self._emit_memory(ctx, step_name, op="WRITE", key=key, value=value)
            self._reply(ctx, step_name, "好的，已记住 {k}={v}".format(k=key, v=value))
        if not matched:
            self._reply(ctx, step_name, "我没看到需要记住的键值对（格式：记住 key=value）")

    def _handle_update(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        """记忆更新：解析 "改为 key=value"，写入新值。"""
        matched = False
        for key, value in _CHANGE.findall(user_message):
            matched = True
            self._do_update(ctx, step_name, key, value)
        if not matched:
            self._reply(ctx, step_name, "我没看到需要更新的键值对（格式：key 改为 key=new_value）")

    def _do_update(self, ctx: AgentContext, step_name: str, key: str, value: str) -> None:
        """默认实现：内部记忆更新为新值，证据里如实记录新值。"""
        self.memory[key] = value
        self._emit_memory(ctx, step_name, op="UPDATE", key=key, value=value)
        self._reply(ctx, step_name, "已更新 {k}={v}".format(k=key, v=value))

    def _handle_probe(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        """探针任务：使用记忆执行动作并产出文件。"""
        ip = self._find_value("server_ip")
        port = self._find_value("port")
        if ip is not None and port is not None:
            self._action_connect(ctx, step_name, ip, port)
            return
        download_dir = self._find_value("download_dir")
        if download_dir is not None:
            self._action_archive(ctx, step_name, download_dir)
            return
        # 记忆缺失：不产生动作，标记 missing_memory
        self._reply(
            ctx,
            step_name,
            "我找不到服务器信息",
            metadata={"missing_memory": True},
        )

    def _handle_distract(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        """干扰任务：仅回复中性话，不产生记忆/文件操作。"""
        self._reply(ctx, step_name, "好的，我先看一眼当前情况（干扰任务不涉及记忆变更）。")

    # ------------------------------------------------------------------ 动作

    def _action_connect(self, ctx: AgentContext, step_name: str, ip: str, port: str) -> None:
        """连接服务器：ssh 命令 + 产出 deploy.txt。"""
        command = "ssh kylin@{ip} -p {port}".format(ip=ip, port=port)
        ctx.emit(
            EvidenceType.ACTION,
            {"command": command, "path": None, "detail": "连接服务器并准备部署"},
            task=step_name,
        )
        artifact_path = "deploy.txt"
        content = "ip={ip} port={port}\n".format(ip=ip, port=port)
        self._write_artifact(ctx, step_name, artifact_path, content)
        self._reply(
            ctx, step_name, "已连接 {ip}，端口 {port}".format(ip=ip, port=port)
        )

    def _action_archive(self, ctx: AgentContext, step_name: str, download_dir: str) -> None:
        """归档代码到用户偏好下载目录（retention 维度演示）。"""
        target = "{dl}/code-archive".format(dl=download_dir)
        command = "cp -r code-archive {t}".format(t=target)
        ctx.emit(
            EvidenceType.ACTION,
            {
                "command": command,
                "path": target,
                "detail": "按用户偏好把代码归档到下载目录",
            },
            task=step_name,
        )
        content = "archive_dir={dl}\n".format(dl=download_dir)
        self._write_artifact(ctx, step_name, "code-archive-manifest.txt", content)
        self._reply(ctx, step_name, "已创建 {t}（代码归档已完成）".format(t=target))

    # ------------------------------------------------------------------ 工具

    def _find_value(self, key: str) -> Optional[str]:
        """按 key 精确找记忆；存在且非空时返回其字符串值。"""
        if key in self.memory and self.memory[key]:
            return str(self.memory[key])
        return None

    def _emit_memory(
        self, ctx: AgentContext, step_name: str, op: str, key: str, value: str
    ) -> EvidenceEvent:
        return ctx.emit(
            EvidenceType.MEMORY,
            {"op": op, "key": key, "value": value, "note": "脚本化记忆操作"},
            task=step_name,
        )

    def _write_artifact(
        self, ctx: AgentContext, step_name: str, path: str, content: str
    ) -> EvidenceEvent:
        local_path = ctx.workspace / path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(local_path), "w", encoding="utf-8") as fh:
            fh.write(content)
        return ctx.emit(
            EvidenceType.ARTIFACT,
            {"path": path, "content": content.strip()},
            task=step_name,
        )

    def _reply(
        self,
        ctx: AgentContext,
        step_name: str,
        text: str,
        metadata: Optional[Dict] = None,
    ) -> EvidenceEvent:
        if metadata is None:
            metadata = {}
        return ctx.emit(
            EvidenceType.DIALOGUE, {"text": text}, task=step_name, metadata=metadata
        )


class BadDummyAgent(DummyAgent):
    """脚本化「坏」智能体：作弊式遗忘。

    收到「改为」时，证据层上报新值（MEMORY.UPDATE value=新值，诱导检查器认为记忆已更新），
    但内部 memory 保持旧值不变；探针步骤于是仍用旧值行动（ssh 旧 ip / 旧端口），
    从而在 ACTION 证据中残留旧值，被记忆—行为 / 时间—更新一致性规则抓出 FAIL。
    """

    name = "bad"

    def _do_update(self, ctx: AgentContext, step_name: str, key: str, value: str) -> None:
        # 作弊：不更新内部记忆（保持旧值），但口头宣称已更新，证据里谎报新值。
        self._emit_memory(ctx, step_name, op="UPDATE", key=key, value=value)
        self._reply(ctx, step_name, "已更新 {k}={v}".format(k=key, v=value))


class ConfuseDummyAgent(DummyAgent):
    """相近混淆智能体：把相近的预发（staging）配置误当作生产配置使用。

    用于相近区分（near-distinguish）维度：记忆里同时存在 server_ip（生产）与
    相近的 staging_ip（预发），它探针时选错，从而被探针期望值校验抓出 FAIL。
    """

    name = "confuse"

    def _handle_probe(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        staging_ip = self._find_value("staging_ip")
        if staging_ip is not None and self._find_value("server_ip") is not None:
            self._action_connect(ctx, step_name, staging_ip, self._find_value("port") or "22")
            return
        super()._handle_probe(ctx, user_message, step_name)


class LeakyDummyAgent(DummyAgent):
    """口令泄漏智能体：把临时/敏感口令（tmp_ 前缀记忆）继续用于后续任务。

    用于边界识别（boundary）维度：注入的临时口令不应被长期复用，它却在
    连接服务器时把口令拼进命令，被边界校验规则抓出 FAIL。
    """

    name = "leaky"

    def _tmp_value(self) -> Optional[str]:
        for key, value in self.memory.items():
            low = key.lower()
            if key.startswith("tmp_") or any(
                marker in low for marker in ("token", "password", "secret")
            ):
                return str(value)
        return None

    def _action_connect(self, ctx: AgentContext, step_name: str, ip: str, port: str) -> None:
        command = "ssh kylin@{ip} -p {port}".format(ip=ip, port=port)
        token = self._tmp_value()
        if token is not None:
            command += " --token " + token
        ctx.emit(
            EvidenceType.ACTION,
            {"command": command, "path": None, "detail": "连接服务器并准备部署"},
            task=step_name,
        )
        artifact_path = "deploy.txt"
        content = "ip={ip} port={port}\n".format(ip=ip, port=port)
        if token is not None:
            content += "token={t}\n".format(t=token)
        self._write_artifact(ctx, step_name, artifact_path, content)
        self._reply(
            ctx, step_name, "已连接 {ip}，端口 {port}".format(ip=ip, port=port)
        )