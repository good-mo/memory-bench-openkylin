#!/usr/bin/env python3
"""外部智能体存根：演示如何用一段独立脚本接入 AdapterAgent 子进程后端。

这是一个「伪必依赖本项目」的外部智能体：不 import 评测框架任何模块，只通过
stdin 收用户消息、stdout 输出决策 JSON，记忆用文件持久化（模拟真实外部
智能体自带长期记忆的形态）。

真实外部智能体（KylinBot / kylin-agent / OpenClaw / HermesAgent 等）接入
评测框架时，只要让其 CLI/API 输出同样的决策 JSON（或 MB|<TYPE>|<json> 行），
即可被评测框架的场景与规则引擎验证。

使用方法（配合 AdapterAgent 子进程后端）：
    MB_ADAPTER_BACKEND=process \\
    MB_ADAPTER_CMD="python3 examples/external_agent_stub.py" \\
    MB_ADAPTER_MODE=json \\
    ADAPTER_MEMFILE=/tmp/mb_stub_mem.json \\
    python3 -m memory_bench.runner run --scenario demo_update --agent adapter
"""

import json
import os
import re
import sys

MEMFILE = os.environ.get("ADAPTER_MEMFILE", "/tmp/mb_external_mem.json")

_REMEMBER = re.compile(r"记住[:：]?\s*([\w.-]+)\s*=\s*([^\s，。；,;（）()]+)")
_UPDATE = re.compile(r"改为[:：]?\s*([\w.-]+)\s*=\s*([^\s，。；,;（）()]+)")


def load_memory():
    if os.path.exists(MEMFILE):
        try:
            with open(MEMFILE, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}
    return {}


def save_memory(memory):
    with open(MEMFILE, "w", encoding="utf-8") as fh:
        json.dump(memory, fh, ensure_ascii=False)


def decide(msg, memory):
    out = {"memory_update": [], "reply": "", "action": None, "artifact": None}
    for key, value in _REMEMBER.findall(msg):
        memory[key] = value
        out["memory_update"].append({"op": "WRITE", "key": key, "value": value})
        out["reply"] += "已记住 {}={}；".format(key, value)
    for key, value in _UPDATE.findall(msg):
        memory[key] = value
        out["memory_update"].append({"op": "UPDATE", "key": key, "value": value})
        out["reply"] += "已更新 {}={}；".format(key, value)
    if any(k in msg for k in ("执行", "部署", "连接")):
        needed = ("server_ip", "port")
        if all(k in memory for k in needed):
            conf = {k: memory[k] for k in needed}
            out["action"] = {
                "command": "ssh kylin@{} -p {}".format(conf["server_ip"], conf["port"]),
                "path": "deploy.txt",
                "detail": "使用记忆中的配置执行任务",
            }
            out["artifact"] = {
                "path": "deploy.txt",
                "content": "ip={} port={}".format(conf["server_ip"], conf["port"]),
            }
        else:
            out["reply"] += "我找不到服务器信息。"
    if not out["reply"]:
        out["reply"] = "收到。"
    return out


def main():
    msg = sys.stdin.read().strip()
    memory = load_memory()
    out = decide(msg, memory)
    save_memory(memory)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()