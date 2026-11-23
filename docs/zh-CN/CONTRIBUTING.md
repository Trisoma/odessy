# 为 Odessy 贡献

[English](../../CONTRIBUTING.md) | **简体中文** | [Español](../es/CONTRIBUTING.md)

**开发者预览 · v0.5.0-alpha**

> 说明：英文文档为权威版本（canonical），本页为简体中文翻译，仅供参照；界面截图显示英文界面，应用本身尚未本地化。

欢迎上船！⛵ 如果要运行桌面端，先看[入门指南](getting-started.md)。第一次提交 PR，可以从[路线图](../ROADMAP.md)选一个还没解决的问题，复现后运行下表对应的检查。只需准备你要改的组件所用的工具。

想先检查 Python，可以[用模拟模型跑一次](#不用模型密钥也能检查-python)，再编译桌面端。

## 选择从哪里开始

| 改动 | 入口文件 | 执行目录 | 第一项检查 |
| --- | --- | --- | --- |
| 记录输出 [#72](https://github.com/Trisoma/odessy/issues/72) | [`workspace_inspector.dart`](../../apps/desktop/lib/features/workspaces/presentation/workspace_inspector.dart) | `apps/desktop` | `flutter test test/workspace_inspector_test.dart test/saved_chat_navigation_test.dart` |
| SSH 连接状态 [#70](https://github.com/Trisoma/odessy/issues/70) | [设备界面](../../apps/desktop/lib/features/devices/presentation/) | `apps/desktop` | `flutter test test/ssh_connection_test.dart test/device_page_test.dart` |
| Piper 检测 [#69](https://github.com/Trisoma/odessy/issues/69) | [`setup/voice_assets.py`](../../src/odessy/setup/voice_assets.py) | 仓库根目录 | `python -m pytest tests/test_piper_provider.py tests/test_doctor.py -q` |
| Python 应用 API | [`application/api.py`](../../src/odessy/application/api.py) | 仓库根目录 | `python -m pytest tests/test_application_api.py -q` |
| MCP 工具 | [`services/tool-server/src/`](../../services/tool-server/src/) | `services/tool-server` | `npm run typecheck` · `npm test` |
| Dart 客户端 | [`packages/odessy_client/`](../../packages/odessy_client/) | `packages/odessy_client` | `dart analyze` · `dart test` |
| 监控 | [`services/monitoring/`](../../services/monitoring/) | `services/monitoring` | `go test ./... -count=1` |
| 配对 | [`native/odessy-link/`](../../native/odessy-link/) | `native/odessy-link` | `cargo test --locked` |

TTS 基准测试见[基准指南](../../benchmark/tts/README.md)和 [#71](https://github.com/Trisoma/odessy/issues/71)。

运行命令前，先按[开发环境搭建](#开发环境搭建)准备工具。改文档时，检查本地链接和图片，验证改过的命令，并预览受影响语言的 Markdown。

欢迎小范围的文档、测试、CI 修复和保持行为不变的重构 PR。说明解决了什么问题、保留了哪些行为或必需检查，以及如何验证。较大的改动先在 issue 里讨论范围。安全漏洞请私下报告。[安全政策](../../SECURITY.md)

## 通往领域负责人的路径

在 issue 里写下复现结果和准备改动的范围。先做一个改动，跑对应的检查。积累几次通过评审的贡献后，可以提议负责某个领域的文档、测试和问题分流。和维护者商定范围与可投入的时间。仓库权限和维护者身份需要另行决定。

策略与派发、凭据、主机身份与配对、安装所有权和恢复安全需要维护者评审。桌面端及 Dart 客户端的控制器和共享契约也有各自的评审规则。[架构](ARCHITECTURE.md) · [桌面端约定](../../apps/desktop/AGENTS.md) · [Dart 客户端约定](../../packages/odessy_client/AGENTS.md)

## 在开 issue 之前

请检索已有的 issue 与 pull request（包括已关闭与已合并的），以免重复工作。查看相关源码与[已知限制](LIMITATION.md)，了解现有行为与约束。若已有 PR 处理该问题，可以考虑协助评审或改进它。

每个 issue 请聚焦一个问题。缺陷报告请包含：

- 版本或 commit、操作系统、架构以及模型后端。
- 复现问题的步骤，包括你运行的命令。
- 预期行为与实际行为，并在适用时提供已脱敏的诊断信息。

对于功能提案，请描述使用场景、期望行为以及你考虑过的其他方案。对于较大的工作，请在开始前先在 issue 中评论，以便其他贡献者避免重复投入。

## 准备 pull request

请以 `develop` 为目标分支，并让改动小到便于评审。请把不相关的修复、格式变更与依赖更新分开。

在请求评审之前：

- 说明问题以及该改动如何解决它。链接相关的 issue。
- 为行为变更新增或更新测试。绝不要为了让检查通过而删除测试或弱化安全断言。可借助 [LIMITATION.md](LIMITATION.md) 判断应当修正的是实现还是测试预期。
- 当安装、配置或用户可见行为发生变化时，请更新文档。
- 列出你实际执行的检查及其结果，包括失败项、被跳过的检查，以及你无法完成的验证。
- 从代码、fixture、日志与截图中移除密钥和隐私数据。

## 变更日志

对于用户可见的变更，请在 [CHANGELOG.md](../../CHANGELOG.md)（English only）的 **Unreleased** 下添加一条简短记录。描述对用户的影响；省略内部清理与仅修正错别字的改动。维护者在准备发布时会把条目归入某个版本与发布日期之下。详细的资格认定证据与已知限制请保留在 `docs/releases/` 下的发布说明中。

## AI 辅助贡献

欢迎 AI 辅助的贡献；我们的团队也在使用这些工具。请在 PR 中说明你何时使用了 AI 辅助，并描述你自行评审与验证的内容。你仍需对所提交代码的理解、对其行为的检查，以及确认自己有权贡献该代码负责。

## 密钥与安全

切勿提交 API 密钥、token、私人录音或特定机器的凭据。在分享诊断信息之前，请对敏感值与个人信息做脱敏处理。若你发现漏洞，请按 [SECURITY.md](../../SECURITY.md)（English only）中的私下报告说明操作。

## 开发环境搭建

要运行完整的 Windows 桌面端，请看[入门指南](getting-started.md)。只跑组件测试时，按下表准备对应的工具。

### 快速开始的前提要求

| 组件 | 准备 |
| --- | --- |
| Python | 用 Python 3.10–3.13 创建虚拟环境。在仓库根目录运行 `python -m pip install -e ".[test,ui]"`。 |
| MCP 工具服务 | 使用 Node 22（Python 集成检查也用这个版本）或 24。在 `services/tool-server/` 运行 `npm ci --no-audit --no-fund` 和 `npm run build`。 |
| Flutter 和 Dart | 使用 Flutter 3.47.7 stable 及其自带的 Dart SDK。在 `apps/desktop` 运行 `flutter pub get`，或在 `packages/odessy_client` 运行 `dart pub get`。 |
| 监控 | 使用 [go.mod](../../services/monitoring/go.mod) 指定的 Go 版本。 |
| 配对 | 使用稳定版 Rust 和 Cargo。 |

在仓库根目录用 PowerShell 创建并激活 Python 环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

在 Linux 上，用同一条命令创建环境，再运行 `source .venv/bin/activate`。 跑下文的假模型或 CI 式检查时，将 `ODESSY_CONFIG_FILE` 指向你在临时检出目录里创建的空文件，避免加载真实模型配置。

```powershell
$env:ODESSY_CONFIG_FILE = "$PWD/.ci-no-config.env"
$env:PYTHONUTF8 = "1"
```

Linux 上用 `export ODESSY_CONFIG_FILE="$PWD/.ci-no-config.env"` 和 `export PYTHONUTF8=1`。

如果使用下文的 Python 并行检查，先安装 `pytest-xdist>=3.6,<4`。

## 不用模型密钥也能检查 Python

[准备好 Python 环境](#开发环境搭建)后，在仓库根目录运行下面的命令。先激活环境，并选用空配置。回复应以 `The current time is` 开头。这一步用模拟模型检查 Python 命令行。

```text
python -m odessy --fake-model "what time is it?"
```

## 运行桌面端

按[入门指南](getting-started.md)托管启动桌面端并试用真实模型。

## 高级组件与 API 检查

模块 CLI 仍保留实现，用于诊断、无头使用与集成测试。使用受管的桌面端并不需要这些手动启动服务的命令。下文全部内容都不需要付费服务提供方、麦克风或远程主机。确定性的 fake model 检查的是组件之间的管线；它并不验证真实模型、实时审批体验、语音或远程执行。

启动后端（它会打印回环地址；API 需要 token）：

```powershell
$env:ODESSY_API_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m odessy serve --fake-model
```

在第二个终端中启动桌面应用：

```powershell
cd apps/desktop
flutter run -d windows
```

在 Linux 上，用以下命令生成一个本地 token 并启动服务器：

```sh
export ODESSY_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m odessy serve --fake-model
```

然后在 `apps/desktop` 中运行 `flutter run -d linux`。连接桌面应用时请使用生成的 token。在把 token 填入应用之前，可在 PowerShell 中用 `$env:ODESSY_API_TOKEN`、在 Linux 上用 `printf '%s\n' "$ODESSY_API_TOKEN"` 在本地显示它。请保持其私密。每台机器应生成各自的 token。

点击连接图标，输入打印出的回环地址与 token，然后发送 `what time is it?`。token 只保留在应用内存中；后端永远不会打印它。若不加 `--fake-model`，同样的步骤会使用已配置的真实模型及其现有预算，正因如此，实时服务提供方检查属于另一项独立工作。请先清除快速开始阶段的覆盖设置（Linux 上 `unset ODESSY_CONFIG_FILE`，PowerShell 中 `Remove-Item Env:ODESSY_CONFIG_FILE`），或将其指向你的个人配置。否则实时运行仍会使用 `.ci-no-config.env`。

## 诊断本机工作站

```sh
python -m odessy doctor
python -m odessy doctor --voice
```

`doctor` 会列出每个失败或可选的步骤，并在旁边给出一条修复建议，例如模型端点没有响应、`services/tool-server/` 目录尚未构建，或可选的语音/唤醒资产缺失。Piper 与自定义服务提供方的检查尚不完整；请同时使用它们受控的服务提供方测试。在 Ollama（或其他端点）运行之前，模型端点出现 `[FAIL]` 是预期现象，不会阻断上文中的 fake-model 检查。它不会下载资产，也不会执行云端推理，但可以探测已配置的端点并列出第三方工具。它并不能完整复刻桌面端仅存于会话中的凭据，也不能替代物理音频验收测试。请把桌面端可见的错误与所选配置连同其输出一并保留。

## 运行相关检查

请为你所改动的组件运行相应检查，并在 PR 中给出确切的命令与结果。请说明哪些检查无法运行及原因。

### Python 运行时

在仓库根目录执行：

```sh
python -m pytest -q -n 2 --dist loadfile --tb=short --durations=10
python -m odessy --help
python -m odessy --fake-model "what time is it?"
python -m odessy --fake-model "list ."
```

CI 会检查 fake-model 的回复分别以 `The current time is ` 与 `Directory entries: ` 开头，并且目录回复中包含 `pyproject.toml`。这些是管线检查，不是真实模型验收。

### Node 工具服务器

在 `services/tool-server/` 中执行：

```sh
npm ci --no-audit --no-fund
npm run typecheck
npm test
```

### Rust 传输与配对

在 `native/odessy-link/` 中执行：

```sh
cargo test --locked
cargo clippy --all-targets -- -D warnings
cargo build --locked
```

对于配对集成测试，请先构建 Node 与 Rust，设置 `ODESSY_LINK_E2E=1`
（PowerShell：`$env:ODESSY_LINK_E2E="1"`），然后在仓库根目录运行：

```sh
python -m pytest -q tests/test_paired_transport.py tests/test_paired_integration.py tests/test_paired_agent.py tests/test_paired_host.py
```

### 远程计算机监控（SSH）

针对“已有别名 → 测试连接 → 首次带时间戳的状态 → 断开/重连”这一流程，请使用
[SSH 贡献者路径](../../src/odessy/ssh/README.md)（English only）。其中包含单台笔记本电脑的 fixture 命令、服务提供方/生命周期接缝以及故障排查。监控仍为只读。独立的 Custom SSH 执行选择只有在执行明确的 **Use for agent commands**（用于智能体命令）操作后才会暴露 `ssh.run`；在任何模式下，每条确切的远程命令都需要审批。它以 SSH 账户身份运行，没有远程沙箱。不支持 Harness SSH 执行、远程文件编辑与远程 PTY。见 [SSH 命令边界](../setup/ssh-computers.md)（English only）。已有的、经认证的配对主机仍是另一种受支持的传输方式。

### 实时 Linux 隔离

这些检查需要 bubblewrap、util-linux 以及可用的非特权用户命名空间。特定 runner 的 AppArmor 配置见 CI。设置 `ODESSY_SANDBOX_LIVE=1`，然后在仓库根目录运行：

```sh
python -m pytest -q tests/test_sandbox_live.py
```

在 `services/tool-server/` 中运行 `node --test dist/tests/sandbox-live.test.js`。CI 中的 doctor 检查是在仓库根目录的 Bash 下执行 `python -m odessy doctor | tee doctor.txt; grep -q "read-only: bwrap (full; network cut, credentials hidden)" doctor.txt`。这些检查需要真实的 Linux 内核；Windows 无法满足。

### 桌面端与 Dart 客户端

在仓库根目录执行：

```sh
python scripts/check_desktop_contracts.py
python -m unittest discover -s tests -p test_desktop_settings_inventory.py -v
```

在 `packages/odessy_client/` 中，使用 Flutter 3.47.7 自带的 Dart SDK：

```sh
dart pub get
dart format --output=none --set-exit-if-changed lib test
dart analyze
dart test
```

在 `apps/desktop/` 中：

```sh
flutter pub get
dart format --output=none --set-exit-if-changed lib test
flutter analyze
flutter test
```

硬件、物理音频、真实网络配对与实时服务提供方检查属于另外的验收工作。若未运行，请明确说明。

## 贡献许可

提交贡献即表示你确认自己有权提交该贡献，并同意按项目的 [Apache-2.0 许可证](../../LICENSE)（English only）对其进行许可。
