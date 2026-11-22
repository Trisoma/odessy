# 已知限制

[English](../../LIMITATION.md) | **简体中文** | [Español](../es/LIMITATION.md)

> 说明：英文文档为权威版本（canonical），本页为简体中文翻译，仅供参照；界面截图显示英文界面，应用本身尚未本地化。

**Developer Preview · v0.5.0-alpha**

[当前支持矩阵](./current-status.md)是持续维护的能力与发布基线。本页概述影响采用的各种约束。

- **安装包：** [公开的 alpha 版](https://github.com/Trisoma/odessy/releases/tag/v0.5.0-alpha)早于 `08efca9` 的安全修复。要用这些修复，请[从源码启动](getting-started.md)。安装包版本和检查见[发布状态](current-status.md)。
- **检查点安全性：** 在目录句柄竞态保护实现之前，Windows 上在服务与写入边界处禁用恢复功能。创建、列出与预览仍然可用。直接恢复请求会返回不支持；正在运行的旧版服务必须更新后才会具备这一限制。纯文本快照不是完整备份，也无法撤销已执行的命令或外部影响。
- **引擎差异：** Custom 支持有边界的重启后延续。Harness 历史仍可读取，但无法通过该适配器在重启后继续。Harness 不会静默模拟 Custom 的访问模式与上下文注入。
- **授权：** 完全控制既不会授予新的工作区/远程权限，也不会绕过硬性拒绝。SSH 登录只启动只读监控。在显式选择 SSH 执行之后，Custom 可以运行经逐条审批的命令。配对、监控与执行授权彼此独立。
- **交互式 shell：** 显式批准的本地 shell 拥有用户级操作系统权限；它不会被逐条命令拦截或沙箱化。原生辅助程序的可用性取决于平台/软件包。
- **隔离性：** 用户空间路径检查并不等于完整的内核级隔离。Windows、Linux 与 Jetson 的隔离方式各不相同。不要把遗留的、未认证的明文 LAN 执行暴露给不可信网络。
- **隐私：** 云端配置会将请求数据与获准的观测数据发送给所选择的服务提供方。脱敏是尽力而为的。SQLite 历史与记忆属于本地配置数据，并非加密的凭据保险库。
- **硬件与语音：** 尚未确立通用的最低硬件规格，也未确立普遍适用的音频/服务提供方兼容性。模型/资产是可选组件，并受其自身许可证约束。macOS 与通用 MCU 控制尚未通过资格认定（unqualified）。
- **协作（Coordination）：** 现有的 Jobs/Handoffs 仍然可访问，但受支持的行为取决于引擎；不会仅仅因为协商功能或某个页面可用，就认为被推迟的 Harness 委派对等能力已经实现。

见[详细安全边界](../reference/current-limitations.mdx)（English only）、
[终端/检查点语义](../ui/terminal-checkpoints.md)（English only）、
[历史](../ui/conversation-history.md)（English only）与[历史证据](README.md)。
