# 入门

[English](../getting-started.md) | **简体中文** | [Español](../es/getting-started.md)

**开发者预览 · v0.5.0-alpha**

> 说明：英文文档为权威版本（canonical），本页为简体中文翻译，仅供参照；界面截图显示英文界面，应用本身尚未本地化。

## 选择你的路径

本指南用于在 Windows 上从源码启动 Odessy。应用会用检出目录中的 Python 启动本地服务。如果用[已发布的 Windows 安装包](https://github.com/Trisoma/odessy/releases/tag/v0.5.0-alpha)，可以跳过开发工具，直接看[手册](GOLDEN_PATH.md)。

如果只是想确认 Python 能跑，先[用模拟模型试一下](CONTRIBUTING.md#不用模型密钥也能检查-python)。这一步不用模型密钥，也不用编译桌面端。

## 准备 Windows 源码检出

安装 Git、带 `py` 启动器的 Python 3.13、Node.js 22、Flutter 3.47.7 stable，以及 Visual Studio 的 **Desktop development with C++**（使用 C++ 的桌面开发）工作负载和 Windows SDK。用 `flutter doctor -v` 检查 Windows 构建就绪情况。键入式聊天不需要 GPU 或语音模型。依赖安装需要网络访问。

在 PowerShell 中，从你选择的父文件夹开始，使用一个新的检出：

```powershell
git clone --branch develop --single-branch https://github.com/Trisoma/odessy.git odessy-preview
Set-Location odessy-preview
$previewRepo = (Get-Location).Path.Replace('\', '/')
$previewPython = "$previewRepo/.venv/Scripts/python.exe"
py -3.13 -m venv .venv
& $previewPython -m pip install -e ".[test,ui]"
Push-Location tools
npm.cmd ci --no-audit --no-fund
npm.cmd run build
Pop-Location
flutter doctor -v
git rev-parse HEAD
```

如果任一命令失败，请停止。请将打印出的源码修订保存到任何缺陷报告中。你不需要激活虚拟环境，也不需要更改 PowerShell 执行策略。只有在构建各自的可选组件时才需要 Rust 和 Go；开始本地键入式聊天练习并不需要它们。

## 使用桌面托管的启动方式启动

在**同一个终端**中：

```powershell
$previewDefines = @(
    '--dart-define=ODESSY_LOCAL_DESKTOP=true'
    "--dart-define=ODESSY_DESKTOP_ROOT=$previewRepo"
    "--dart-define=ODESSY_DESKTOP_PYTHON=$previewPython"
    "--dart-define=ODESSY_SETUP_ROOT=$previewRepo"
    "--dart-define=ODESSY_SETUP_PYTHON=$previewPython"
)
Set-Location apps/desktop
flutter pub get
flutter run -d windows @previewDefines
```

Odessy 打开后，先按手册[连接模型并得到一条回复](GOLDEN_PATH.md#2-配置-deepseek)，再[运行一条本地命令并查看 Output](GOLDEN_PATH.md#3-在你的-windows-笔记本上运行命令)。

语音和 SSH 是可选的后续步骤。源码安装不会下载它们的资源，也不会构建可选的监控/密码辅助程序。它们各自的要求见[语音组件](../setup/voice-components.md)（English only）和 [SSH 安装](../setup/ssh-computers.md)（English only）。

## 诊断启动失败

如果打开桌面端后服务未连接，请确认在同一个 PowerShell 会话中使用了全部五个 define。直接运行 `flutter run -d windows` 会打开没有托管服务的客户端。源码启动使用 `build/local-desktop/state`，也可能在用户设置中记住所用配置的位置。见[桌面启动模式](../ui/README.md#connection-and-launch-modes)。

先查看 Settings 中的错误。要诊断源码版，请在检出目录的根目录打开 PowerShell。下面以 DeepSeek 为例，请换成你配置的后端。

```powershell
$previewRepo = (Get-Location).Path.Replace('\', '/')
$previewPython = "$previewRepo/.venv/Scripts/python.exe"
$env:ODESSY_CONFIG_FILE = "$previewRepo/build/local-desktop/state/odessy.env"
& $previewPython -m odessy doctor --model-backend deepseek
```

Doctor 可以探测已配置的端点并列出工具。它不会运行云端推理或测试音频。仅限当前会话的密钥留在桌面端，不要为了检查把它复制到文件里。

使用手册中的[故障排查与脱敏报告步骤](./GOLDEN_PATH.md#故障排查与报告)。有关确定性的组件测试或手动管理的 API 服务器，参见[高级开发检查](./CONTRIBUTING.md#高级组件与-api-检查)。Linux CI/源码路径在那里仍然可用；本 Windows 指南并不验证 Linux/macOS 桌面软件包。
