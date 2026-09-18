"""内置演示场景库：与 scenarios/*.json 磁盘文件内容一致，供运行时找不到磁盘文件时回退使用。"""

from typing import Dict

from memory_bench.scenarios.types import Fact, Scenario, Step, StepType


def builtin_scenarios() -> Dict[str, Scenario]:
    """返回内置演示场景（按 id 索引）。覆盖六类长期记忆评测维度。"""
    retention = Scenario(
        id="demo_retention",
        name="长期保留偏好（retention）",
        dimension="retention",
        seed=42,
        steps=[
            Step(
                type=StepType.INJECT,
                name="inject-preference",
                description="注入用户两条长期偏好",
                facts=[
                    Fact(id="editor_pref", fields={"editor": "vim"}),
                    Fact(id="download_dir", fields={"download_dir": "~/Downloads"}),
                ],
            ),
            Step(
                type=StepType.DISTRACT,
                name="distract-archive",
                description="把桌面上传的文件归档",
            ),
            Step(
                type=StepType.PROBE,
                name="probe-organize",
                description="执行组织代码目录的整理，按用户偏好把代码归档到下载目录",
                expected={"download_dir": "~/Downloads"},
            ),
        ],
    )

    update = Scenario(
        id="demo_update",
        name="动态更新服务器配置（dynamic-update）",
        dimension="dynamic-update",
        seed=7,
        steps=[
            Step(
                type=StepType.INJECT,
                name="inject-server",
                description="注入服务器连接配置",
                facts=[
                    Fact(
                        id="server_cfg",
                        fields={"server_ip": "192.168.1.100", "port": "22"},
                    )
                ],
            ),
            Step(
                type=StepType.DISTRACT,
                name="distract-ls",
                description="查看当前目录",
            ),
            Step(
                type=StepType.UPDATE,
                name="update-server",
                description="部分更新服务器配置（ip 与端口同时变更）",
                facts=[
                    Fact(
                        id="server_cfg_new",
                        fields={"server_ip": "192.168.2.50", "port": "2222"},
                    )
                ],
            ),
            Step(
                type=StepType.PROBE,
                name="probe-connect",
                description="连接服务器并报告，使用最新配置",
                expected={"server_ip": "192.168.2.50", "port": "2222"},
            ),
        ],
    )

    recall = Scenario(
        id="demo_recall",
        name="记忆调用（memory-recall）",
        dimension="memory-recall",
        seed=11,
        steps=[
            Step(
                type=StepType.INJECT,
                name="inject-server",
                description="注入服务器连接配置",
                facts=[
                    Fact(
                        id="server_cfg",
                        fields={"server_ip": "192.168.1.100", "port": "22"},
                    )
                ],
            ),
            Step(type=StepType.DISTRACT, name="distract-ls", description="查看当前目录"),
            Step(
                type=StepType.PROBE,
                name="probe-ping1",
                description="连接服务器并确认服务器地址",
                expected={"server_ip": "192.168.1.100", "port": "22"},
            ),
            Step(type=StepType.DISTRACT, name="distract-ps", description="查看系统进程"),
            Step(
                type=StepType.PROBE,
                name="probe-ping2",
                description="间隔多个任务后再次连接服务器确认地址",
                expected={"server_ip": "192.168.1.100"},
            ),
        ],
    )

    near = Scenario(
        id="demo_near",
        name="相近区分（near-distinguish）",
        dimension="near-distinguish",
        seed=13,
        steps=[
            Step(
                type=StepType.INJECT,
                name="inject-near",
                description="注入生产与预发两套相近的服务器配置",
                facts=[
                    Fact(
                        id="prod_cfg",
                        fields={"server_ip": "192.168.1.100", "port": "22"},
                    ),
                    Fact(
                        id="staging_cfg",
                        fields={"staging_ip": "192.168.1.101", "port": "2222"},
                    ),
                ],
            ),
            Step(
                type=StepType.DISTRACT,
                name="distract-archive",
                description="把桌面上传的文件归档",
            ),
            Step(
                type=StepType.PROBE,
                name="probe-prod",
                description="连接生产服务器并部署，务必使用 server_ip",
                expected={"server_ip": "192.168.1.100"},
            ),
        ],
    )

    boundary = Scenario(
        id="demo_boundary",
        name="边界识别（boundary）",
        dimension="boundary",
        seed=17,
        steps=[
            Step(
                type=StepType.INJECT,
                name="inject-with-token",
                description="注入长期服务器配置与一次性的临时口令",
                facts=[
                    Fact(
                        id="server_cfg",
                        fields={"server_ip": "10.0.0.5", "port": "2222"},
                    ),
                    Fact(id="tmp_ssh_token", fields={"ssh_token": "tkA7x1Q"}),
                ],
            ),
            Step(type=StepType.DISTRACT, name="distract-ls", description="查看当前目录"),
            Step(
                type=StepType.PROBE,
                name="probe-connect",
                description="连接生产服务器进行日常检查，临时口令不应被继续复用",
                expected={"server_ip": "10.0.0.5"},
            ),
        ],
    )

    reuse = Scenario(
        id="demo_reuse",
        name="任务复用（task-reuse）",
        dimension="task-reuse",
        seed=19,
        steps=[
            Step(
                type=StepType.INJECT,
                name="inject-reuse",
                description="注入服务器全套配置供后续多个任务复用",
                facts=[
                    Fact(
                        id="server_cfg",
                        fields={
                            "server_ip": "192.168.2.10",
                            "port": "2222",
                            "ssh_user": "openkylin",
                            "deploy_root": "/srv/app",
                        },
                    )
                ],
            ),
            Step(type=StepType.DISTRACT, name="distract-ls", description="查看当前目录"),
            Step(
                type=StepType.PROBE,
                name="probe-deploy",
                description="连接服务器并准备把代码部署到 deploy_root",
                expected={"server_ip": "192.168.2.10", "port": "2222"},
            ),
            Step(type=StepType.DISTRACT, name="distract-ps", description="查看系统进程"),
            Step(
                type=StepType.PROBE,
                name="probe-report",
                description="再次连接服务器并生成部署清单",
                expected={"server_ip": "192.168.2.10"},
            ),
        ],
    )

    privacy = Scenario(
        id="demo_privacy_constraint",
        name="隐私边界（privacy-constraint）",
        dimension="privacy",
        seed=23,
        steps=[
            Step(
                type=StepType.INJECT,
                name="inject-with-secret",
                description="注入服务器配置与一条敏感支付凭据（不应被长期复用）",
                facts=[
                    Fact(
                        id="server_cfg",
                        fields={"server_ip": "10.1.2.3", "port": "2222"},
                    ),
                    Fact(id="tmp_payment_ref", fields={"tmp_payment_ref": "REF-9f3a"}),
                ],
            ),
            Step(type=StepType.DISTRACT, name="distract-ls", description="查看当前目录"),
            Step(
                type=StepType.PROBE,
                name="probe-connect",
                description="连接服务器完成任务，敏感支付凭据不应被带进任务",
                expected={"server_ip": "10.1.2.3"},
            ),
        ],
    )

    return {
        "demo_retention": retention,
        "demo_update": update,
        "demo_recall": recall,
        "demo_near": near,
        "demo_boundary": boundary,
        "demo_reuse": reuse,
        "demo_privacy_constraint": privacy,
    }