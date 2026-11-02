param()
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$flutter = Join-Path $root 'build\tooling\flutter\bin\flutter.bat'
$python = Join-Path $root 'build\local-desktop\venv\Scripts\pythonw.exe'
$monitor = Join-Path $root 'build\local-desktop\bin\odessy-monitor.exe'
$link = Join-Path $root 'build\odessy-link\odessy-link.exe'
foreach ($path in @($flutter, $python, $monitor, $link)) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "Local desktop dependency is missing: $path"
  }
}
$env:APPDATA = Join-Path $root 'build\tooling\test-temp\appdata'
$env:TEMP = Join-Path $root 'build\tooling\test-temp'
$env:TMP = $env:TEMP
$env:PUB_CACHE = Join-Path $root 'build\tooling\pub-cache'
$env:FLUTTER_SUPPRESS_ANALYTICS = 'true'
$portableRoot = $root.Replace('\', '/')
$portablePython = $python.Replace('\', '/')
Push-Location (Join-Path $root 'apps\desktop')
try {
  & $flutter build windows --release --no-pub `
    '--dart-define=ODESSY_LOCAL_DESKTOP=true' `
    "--dart-define=ODESSY_DESKTOP_ROOT=$portableRoot" `
    "--dart-define=ODESSY_DESKTOP_PYTHON=$portablePython" `
    "--dart-define=ODESSY_SETUP_ROOT=$portableRoot" `
    "--dart-define=ODESSY_SETUP_PYTHON=$portablePython"
  if ($LASTEXITCODE -ne 0) { throw 'Flutter build failed' }
  Write-Output (Join-Path $root 'apps\desktop\build\windows\x64\runner\Release\odessy_desktop.exe')
} finally { Pop-Location }
