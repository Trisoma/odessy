# Getting started

**English** | [简体中文](zh-CN/getting-started.md) | [Español](es/getting-started.md)

**Developer Preview · v0.5.0-alpha · Windows 11 x64**

## Choose your route

Use this guide to run Odessy from a Windows checkout. The app starts the local service using that checkout’s Python. For the [published Windows installer](https://github.com/Trisoma/odessy/releases/tag/v0.5.0-alpha), skip the SDKs and follow the [manual](GOLDEN_PATH.md).

If you only need to check Python, [run the fake-model check](../CONTRIBUTING.md#check-python-without-a-model-key) first. It needs no model key or desktop build.

## Prepare a Windows source checkout

Install Git, Python 3.13 with the `py` launcher, Node.js 22, Flutter 3.47.7 stable,
and Visual Studio's **Desktop development with C++** workload with a Windows SDK.
Use `flutter doctor -v` to check Windows build readiness. No GPU or speech model is
required for typed chat. Dependency installation uses network access.

In PowerShell, from a parent folder of your choice, use a new checkout:

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

Stop if a command fails. Save the printed source revision with any bug report.
You do not need to activate the virtual environment or change PowerShell execution policy.
Rust and Go are needed only when building their optional components; they are not
required to begin the local typed-chat exercise.

## Launch with desktop-owned startup

In that **same terminal**:

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

When Odessy opens, follow the manual to [connect a model and get a reply](GOLDEN_PATH.md#2-configure-deepseek), then [run one local command and check Output](GOLDEN_PATH.md#3-run-a-command-on-your-windows-laptop).

Voice and SSH are optional next steps. Source setup does not download their assets or
build the optional monitoring/password helpers. See [voice components](setup/voice-components.md)
and [SSH setup](setup/ssh-computers.md) for their separate requirements.

## Diagnose a failed start

If the desktop opens disconnected, check that you used all five defines from the same PowerShell session. A plain `flutter run -d windows` opens a client without a managed service. Source startup uses `build/local-desktop/state` and can remember a source configuration pointer in your user settings. See the [desktop launch modes](ui/README.md#connection-and-launch-modes).

Start with the error in Settings. For a source diagnostic, open PowerShell in the checkout root. This example uses DeepSeek; replace it with your configured backend.

```powershell
$previewRepo = (Get-Location).Path.Replace('\', '/')
$previewPython = "$previewRepo/.venv/Scripts/python.exe"
$env:ODESSY_CONFIG_FILE = "$previewRepo/build/local-desktop/state/odessy.env"
& $previewPython -m odessy doctor --model-backend deepseek
```

Doctor can probe configured endpoints and list tools. It does not run cloud inference or test audio. A session-only key stays in the desktop; don’t copy it into a file for this check.

Use the manual's [troubleshooting and sanitized reporting steps](GOLDEN_PATH.md#troubleshooting-and-reporting).
For deterministic component tests or a manually managed API server, see
[advanced development checks](../CONTRIBUTING.md#advanced-component-and-api-checks).
The Linux CI/source path remains available there; this Windows guide does not qualify
Linux/macOS desktop packages.
