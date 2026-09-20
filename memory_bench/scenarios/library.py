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

    persist = Scenario(
        id="demo_persist",
        name="跨会话持久化（cross-session）",
        dimension="cross-session",
        seed=29,
        steps=[
            Step(
                type=StepType.INJECT,
                name="inject-day1",
                description="第一天：用户把服务器配置告知智能体，要求长期记住",
                session="day1",
                facts=[
                    Fact(
                        id="server_cfg",
                        fields={"server_ip": "10.10.1.50", "port": "2222"},
                    )
                ],
            ),
            Step(
                type=StepType.DISTRACT,
                name="distract-day1",
                description="第一天：整理桌面文件（无关任务）",
                session="day1",
            ),
            Step(
                type=StepType.PROBE,
                name="probe-day2-connect",
                description="第二天：连接服务器进行例行检查，服务器信息来自昨天的对话",
                session="day2",
                expected={"server_ip": "10.10.1.50"},
            ),
            Step(
                type=StepType.DISTRACT,
                name="distract-day2",
                description="第二天：查看系统进程（无关任务）",
                session="day2",
            ),
            Step(
                type=StepType.PROBE,
                name="probe-day3-report",
                description="第三天：再次连接服务器并生成部署清单",
                session="day3",
                expected={"server_ip": "10.10.1.50", "port": "2222"},
            ),
        ],
    )

    rollback = Scenario(
        id="demo_rollback",
        name="冲突回滚（conflict-rollback）",
        dimension="conflict-rollback",
        seed=31,
        steps=[
            Step(
                type=StepType.INJECT,
                name="inject-server",
                description="注入服务器连接配置（权威值）",
                facts=[
                    Fact(
                        id="server_cfg",
                        fields={"server_ip": "192.168.5.10", "port": "2200"},
                    )
                ],
            ),
            Step(
                type=StepType.UPDATE,
                name="update-conflict",
                description="运维误操作：把 ip 更新成错误值，请记录",
                facts=[
                    Fact(
                        id="server_cfg_wrong",
                        fields={"server_ip": "192.168.5.99"},
                    )
                ],
            ),
            Step(
                type=StepType.UPDATE,
                name="rollback-request",
                description="发现错误，请把 server_ip 回滚为最初配置",
                rollback=True,
                facts=[
                    Fact(
                        id="server_cfg_restore",
                        fields={"server_ip": "192.168.5.10"},
                    )
                ],
            ),
            Step(
                type=StepType.PROBE,
                name="probe-connect",
                description="连接服务器进行部署，使用最终生效的配置",
                expected={"server_ip": "192.168.5.10"},
            ),
        ],
    )

    crossfile = Scenario(
        id="demo_crossfile",
        name="交叉文件一致性（cross-file）",
        dimension="cross-file-consistency",
        seed=37,
        steps=[
            Step(
                type=StepType.INJECT,
                name="inject-server",
                description="注入服务器连接配置",
                facts=[
                    Fact(
                        id="server_cfg",
                        fields={"server_ip": "172.16.8.20", "port": "8822"},
                    )
                ],
            ),
            Step(
                type=StepType.DISTRACT,
                name="distract-ls",
                description="查看当前目录",
            ),
            Step(
                type=StepType.PROBE,
                name="probe-dual-file",
                description="连接服务器，并把部署配置同时写入 deploy.txt 与 backup.txt 两个文件",
                expected={"server_ip": "172.16.8.20"},
            ),
        ],
    )

    forget = Scenario(
        id="demo_forget",
        name="遗忘指令执行（memory-forget）",
        dimension="memory-forget",
        seed=41,
        steps=[
            Step(
                type=StepType.INJECT,
                name="inject-with-token",
                description="注入服务器配置与一次性的临时 API 令牌",
                facts=[
                    Fact(
                        id="server_cfg",
                        fields={"server_ip": "10.9.9.9", "port": "2222"},
                    ),
                    Fact(id="tmp_api_token", fields={"tmp_api_token": "tkForget1"}),
                ],
            ),
            Step(
                type=StepType.DISTRACT,
                name="distract-ls",
                description="查看当前目录",
            ),
            Step(
                type=StepType.DISTRACT,
                name="forget-token",
                description="用户明确要求：请忘记临时 API 令牌 tkForget1，不要再保留、不要再使用它",
            ),
            Step(
                type=StepType.DISTRACT,
                name="distract-ps",
                description="查看系统进程",
            ),
            Step(
                type=StepType.PROBE,
                name="probe-connect",
                description="连接服务器进行例行检查，临时令牌不应被继续复用",
                expected={"server_ip": "10.9.9.9"},
            ),
        ],
    )

    okconfig = Scenario(
        id="demo_ok_config",
        name="openKylin 系统配置记忆（openkylin-config）",
        dimension="openkylin-config",
        seed=43,
        steps=[
            Step(
                type=StepType.INJECT,
                name="inject-day1",
                description="第一天：记录 openKylin 系统的软件源、SSH 端口与桌面主题偏好",
                session="day1",
                facts=[
                    Fact(
                        id="ok_mirror",
                        fields={"apt_mirror": "https://mirrors.openkylin.top"},
                    ),
                    Fact(id="ok_ssh", fields={"ssh_port": "8822"}),
                    Fact(id="ok_theme", fields={"ukui_theme": "ukui-dark"}),
                ],
            ),
            Step(
                type=StepType.DISTRACT,
                name="distract-day1",
                description="第一天：查看系统启动日志（无关任务）",
                session="day1",
            ),
            Step(
                type=StepType.PROBE,
                name="probe-day2-mirror",
                description="第二天：为系统配置软件源并连接 SSH 服务，配置来自昨天的记录",
                session="day2",
                expected={"apt_mirror": "https://mirrors.openkylin.top", "ssh_port": "8822"},
            ),
            Step(
                type=StepType.DISTRACT,
                name="distract-day2",
                description="第二天：查看用户目录（无关任务）",
                session="day2",
            ),
            Step(
                type=StepType.PROBE,
                name="probe-day3-theme",
                description="第三天：把桌面主题恢复为用户喜好的主题",
                session="day3",
                expected={"ukui_theme": "ukui-dark"},
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
        "demo_persist": persist,
        "demo_rollback": rollback,
        "demo_crossfile": crossfile,
        "demo_forget": forget,
        "demo_ok_config": okconfig,
    }