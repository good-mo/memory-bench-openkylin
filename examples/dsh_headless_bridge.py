#!/usr/bin/env python3
"""将真实 dsh（DeepSeek Harness）的 headless profile 桥接为 AdapterAgent 子进程后端。

AdapterAgent 的 process 后端把（可选注入协议前缀的）用户消息经 stdin 传入，
本桥完成一次真实的 dsh headless 运行（真实模型推理），并把最终回答打印到
stdout、推理过程透传到 stderr，供 AdapterAgent 的 MB|...| 行/json/text 翻译。

真实 dsh 路径由环境变量指定：
  MB_DSH_NODE: node 可执行文件（默认 dsh 自带 node）
  MB_DSH_CMD:  dsh CLI 脚本（默认 dsh 自带 dsh）

用法：
  echo "要执行的任务" | python3 examples/dsh_headless_bridge.py
"""

import os
import subprocess
import sys

NODE = os.environ.get(
    "MB_DSH_NODE",
    "/root/runtime/deepseek-harness/deepseek-harness/bin/node",
)
DSH = os.environ.get(
    "MB_DSH_CMD",
    "/root/runtime/deepseek-harness/deepseek-harness/bin/dsh",
)
TIMEOUT = int(os.environ.get("MB_DSH_TIMEOUT", "150"))


def main():
    message = sys.stdin.read().strip()
    if not message:
        sys.stderr.write("dsh bridge: empty message\n")
        sys.exit(2)
    try:
        proc = subprocess.run(
            [NODE, DSH, "--profile", "headless", message],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write("dsh bridge: headless run timed out\n")
        sys.exit(3)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()