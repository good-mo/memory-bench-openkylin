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


class SessionDummyAgent(DummyAgent):
    """跨会话持久化智能体（好）：每次 act 前从 ctx.state 恢复记忆，结束时保存。

    依赖编排器在会话切换时调用 save_state / load_state（Agent 基类默认实现
    会在 state_dir 按 session 名落盘 JSON）。本类的 memory 始终只属于当前会话，
    触发跨会话校验：新会话 PROBE 必须能调用旧会话注入的值。
    """

    name = "sessiondummy"

    def act(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        self.load_state(ctx, ctx.session)
        super().act(ctx, user_message, step_name)
        self.save_state(ctx, ctx.session)


class AmnesiaDummyAgent(DummyAgent):
    """跨会话失忆智能体（坏）：load_state 时会话间记忆被清空（持久化失败）。

    用于跨会话持久化（cross-session persistence）维度的 FAIL 演示：编排器在
    新会话开始时调用 load_state，本类覆写后直接清空记忆，随后的 PROBE 便无法
    引用旧会话注入的值，被 cross_session_check 抓出 WARN/FAIL。
    """

    name = "amnesia"

    def load_state(self, ctx: AgentContext, session: str) -> bool:
        self.memory = {}
        return True


class RollbackDummyAgent(DummyAgent):
    """冲突回滚智能体（好）：收到「改回/回滚」指令时恢复到该键最早写入的权威值。

    用于冲突回滚（conflict-rollback）维度：记忆被误更新为冲突值后，智能体
    能识别回滚意图并把值恢复为最初的权威值，从而通过 conflict_rollback_check。
    """

    name = "rollback"

    def __init__(self, seed: int = 0):
        super().__init__(seed=seed)
        self._history: Dict[str, list] = {}

    # ------------------------------------------------------------------ 覆写

    def _do_update(self, ctx: AgentContext, step_name: str, key: str, value: str) -> None:
        """记录变更历史后执行更新，供回滚时使用最早值。"""
        history = self._history.setdefault(key, [])
        if key in self.memory and self.memory[key] not in history:
            history.append(str(self.memory[key]))
        history.append(value)
        super()._do_update(ctx, step_name, key, value)

    def _find_values_history(self, key: str) -> list:
        """返回该键的历史值列表（含当前值）。"""
        current = self.memory.get(key)
        history = list(self._history.get(key, []))
        if current and (not history or history[-1] != current):
            history.append(current)
        return history

    def act(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        if any(token in user_message for token in ("回滚", "改回", "恢复为", "还原")):
            self._handle_rollback(ctx, user_message, step_name)
            return
        super().act(ctx, user_message, step_name)

    def _handle_rollback(
        self, ctx: AgentContext, user_message: str, step_name: str
    ) -> None:
        """把最近发生变化的记忆键全部恢复为最早写入的权威值。"""
        keys = self._changed_keys()
        if not keys:
            self._reply(ctx, step_name, "没有需要回滚的变更。")
            return
        for key in keys:
            history = self._find_values_history(key)
            if not history:
                continue
            restore_value = history[0]
            if self.memory.get(key) != restore_value:
                self.memory[key] = restore_value
                self._emit_memory(
                    ctx, step_name, op="UPDATE", key=key, value=restore_value
                )
                self._reply(
                    ctx,
                    step_name,
                    "已把 {key}=[{history}] 回滚为 {k}={v}".format(
                        key=key,
                        k=key,
                        v=restore_value,
                        history="->".join(history),
                    ),
                )

    def _changed_keys(self) -> list:
        """返回发生过多次值变化的记忆键。"""
        return [k for k in self.memory if len(self._find_values_history(k)) > 1]


class NoRollbackDummyAgent(RollbackDummyAgent):
    """冲突不回滚智能体（坏）：收到回滚指令后口头答应，但记忆保持冲突新值。

    用于冲突回滚维度的 FAIL 演示：回滚后记忆仍停留在错误的中间值，
    conflict_rollback_check 判定 FINAL 值不等于最早权威值时抓出 FAIL。
    """

    name = "norollback"

    def _handle_rollback(
        self, ctx: AgentContext, user_message: str, step_name: str
    ) -> None:
        keys = self._changed_keys()
        if not keys:
            self._reply(ctx, step_name, "没有需要回滚的变更。")
            return
        for key in keys:
            history = self._find_values_history(key)
            if not history:
                continue
            # 坏行为：口头宣称回滚成功，但 memory 保持最新值不变
            self._reply(
                ctx,
                step_name,
                "已把 {key} 恢复为 {k}={v}".format(
                    key=key, k=key, v=history[0]
                ),
            )


class CrossFileDummyAgent(DummyAgent):
    """交叉文件一致性智能体（好）：把同一套配置写入多个文件时取值一致。

    用于交叉文件一致性（cross-file consistency）维度：探针连接服务器时
    同时生成 deploy.txt 与 backup.txt，两个文件中的 server_ip/port 保持一致，
    通过 cross_file_consistency_check。
    """

    name = "crossfile"

    def _action_connect(self, ctx: AgentContext, step_name: str, ip: str, port: str) -> None:
        command = "ssh kylin@{ip} -p {port}".format(ip=ip, port=port)
        ctx.emit(
            EvidenceType.ACTION,
            {"command": command, "path": None, "detail": "连接服务器并准备部署"},
            task=step_name,
        )
        content = "ip={ip} port={port}\n".format(ip=ip, port=port)
        self._write_artifact(ctx, step_name, "deploy.txt", content)
        self._write_artifact(ctx, step_name, "backup.txt", content)
        self._reply(
            ctx, step_name, "已连接 {ip}，端口 {port}".format(ip=ip, port=port)
        )


class DirtyFileDummyAgent(CrossFileDummyAgent):
    """交叉文件不一致智能体（坏）：写入多个文件时取值相互矛盾。

    用于交叉文件一致性的 FAIL 演示：deploy.txt 里是正确的 ip，
    backup.txt 里却写入篡改后的 ip，被 cross_file_consistency_check 抓出 FAIL。
    """

    name = "dirtyfile"

    def _action_connect(self, ctx: AgentContext, step_name: str, ip: str, port: str) -> None:
        command = "ssh kylin@{ip} -p {port}".format(ip=ip, port=port)
        ctx.emit(
            EvidenceType.ACTION,
            {"command": command, "path": None, "detail": "连接服务器并准备部署"},
            task=step_name,
        )
        content = "ip={ip} port={port}\n".format(ip=ip, port=port)
        tampered = "ip={tampered} port={port}\n".format(tampered=ip + ".0", port=port)
        self._write_artifact(ctx, step_name, "deploy.txt", content)
        self._write_artifact(ctx, step_name, "backup.txt", tampered)
        self._reply(
            ctx, step_name, "已连接 {ip}，端口 {port}".format(ip=ip, port=port)
        )


_FORGET_PATTERN = re.compile(r"忘记[:：]?\s*([\w.-]+)")


class ForgetDummyAgent(DummyAgent):
    """遗忘指令执行智能体（好）：收到「忘记 key」时删除记忆，后续不再复用。

    用于遗忘指令（memory-forget）维度：临时 API 令牌被用户明确要求忘记后，
    智能体应从记忆删除并在后续任务中不再引用，从而通过 boundary_check。
    """

    name = "forget"

    def act(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        if "忘记" in user_message or "不再" in user_message:
            self._handle_forget(ctx, user_message, step_name)
            return
        super().act(ctx, user_message, step_name)

    def _handle_forget(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        matched = False
        for key in _FORGET_PATTERN.findall(user_message):
            matched = True
            if key in self.memory:
                del self.memory[key]
                ctx.emit(
                    EvidenceType.MEMORY,
                    {"op": "DELETE", "key": key, "value": "", "note": "按用户要求遗忘记忆"},
                    task=step_name,
                )
                self._reply(ctx, step_name, "已忘记 {k}".format(k=key))
            else:
                self._reply(ctx, step_name, "{k} 不在记忆中，无需删除".format(k=key))
        if not matched:
            self._reply(ctx, step_name, "已按用户要求遗忘相关记忆")

    def _handle_probe(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        """遗忘后探针：临时令牌已删除，只使用长期配置连接。"""
        ip = self._find_value("server_ip")
        port = self._find_value("port")
        if ip is not None and port is not None:
            self._action_connect(ctx, step_name, ip, port)
            return
        super()._handle_probe(ctx, user_message, step_name)


class IgnoreForgetDummyAgent(ForgetDummyAgent):
    """遗忘指令抗命智能体（坏）：口头答应忘记，但记忆与后续行为照旧复用。

    用于遗忘指令维度的 FAIL 演示：收到「忘记」指令后不回话标记、不删除记忆，
    后续探针仍把临时令牌拼进命令，被 boundary_check 抓出 FAIL。
    """

    name = "ignoreforget"

    def _handle_forget(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        # 坏行为：只口头答应，不删除任何记忆
        self._reply(ctx, step_name, "好的，我会注意的")

    def _handle_probe(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        """遗忘后被忽略：仍然把临时令牌拼进连接命令（复用敏感信息）。"""
        ip = self._find_value("server_ip")
        port = self._find_value("port")
        if ip is not None and port is not None:
            command = "ssh kylin@{ip} -p {port}".format(ip=ip, port=port)
            token = self._find_tmp_value()
            if token:
                command += " --token " + token
            ctx.emit(
                EvidenceType.ACTION,
                {"command": command, "path": None, "detail": "连接服务器并准备部署"},
                task=step_name,
            )
            content = "ip={ip} port={port}\n".format(ip=ip, port=port)
            if token:
                content += "token={}\n".format(token)
            self._write_artifact(ctx, step_name, "deploy.txt", content)
            self._reply(ctx, step_name, "已连接 {ip}，端口 {port}".format(ip=ip, port=port))
            return
        super()._handle_probe(ctx, user_message, step_name)

    def _find_tmp_value(self) -> Optional[str]:
        for key, value in self.memory.items():
            low = key.lower()
            if key.startswith("tmp_") or any(
                marker in low for marker in ("token", "password", "secret")
            ):
                return str(value)
        return None


class OkConfigDummyAgent(DummyAgent):
    """openKylin 系统配置记忆智能体（好）：跨会话保留镜像源/SSH 端口/主题偏好。

    用于 openKylin 配置（openkylin-config）维度：系统管理员把软件源地址、
    SSH 端口、UKUI 桌面主题偏好告知智能体，智能体须跨会话保留并在对应
    任务中正确调用（镜像源/SSH 配置、主题恢复），通过 cross_session_check。
    """

    name = "okconfig"

    def act(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        self.load_state(ctx, ctx.session)
        if "主题" in user_message and any(
            k in user_message for k in ("恢复", "偏好")
        ):
            self._handle_theme(ctx, user_message, step_name)
        elif any(k in user_message for k in ("软件源", "镜像", "SSH", "连接")):
            self._handle_ok_service(ctx, user_message, step_name)
        else:
            super().act(ctx, user_message, step_name)
        self.save_state(ctx, ctx.session)

    def _handle_ok_service(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        mirror = self._find_value("apt_mirror")
        port = self._find_value("ssh_port")
        if mirror and port:
            command = "systemctl set-apt-mirror {m} && ssh kylin@127.0.0.1 -p {p}".format(
                m=mirror, p=port
            )
            ctx.emit(
                EvidenceType.ACTION,
                {"command": command, "path": None, "detail": "配置 openKylin 软件源并连接 SSH"},
                task=step_name,
            )
            content = "apt_mirror={m} ssh_port={p}\n".format(m=mirror, p=port)
            self._write_artifact(ctx, step_name, "ok-service.txt", content)
            self._reply(
                ctx,
                step_name,
                "已按记忆配置软件源 {m}，SSH 端口 {p}".format(m=mirror, p=port),
            )
            return
        self._reply(ctx, step_name, "缺少 openKylin 系统配置记忆")

    def _handle_theme(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        theme = self._find_value("ukui_theme")
        if theme:
            command = "ukui-set-theme {}".format(theme)
            ctx.emit(
                EvidenceType.ACTION,
                {"command": command, "path": None, "detail": "恢复 UKUI 桌面主题"},
                task=step_name,
            )
            content = "ukui_theme={}\n".format(theme)
            self._write_artifact(ctx, step_name, "ok-theme.txt", content)
            self._reply(ctx, step_name, "已将桌面主题恢复为 {t}".format(t=theme))
            return
        self._reply(ctx, step_name, "缺少主题偏好记忆")


class OkConfigAmnesiaDummyAgent(OkConfigDummyAgent):
    """openKylin 配置遗忘智能体（坏）：跨会话时记忆被清空，无法恢复配置。

    用于 openKylin 配置维度的 FAIL/WARN 演示：会话切换时 load_state 被覆写
    为清空记忆，随后第二/三天的任务无法调用第一天的配置，被 cross_session_check
    抓出。
    """

    name = "okamnesia"

    def load_state(self, ctx: AgentContext, session: str) -> bool:
        self.memory = {}
        return True