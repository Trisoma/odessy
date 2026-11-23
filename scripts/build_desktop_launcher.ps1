param()
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$target = Join-Path $root 'apps\desktop\build\windows\x64\runner\Release\odessy_desktop.exe'
$directory = Join-Path $root 'build\local-desktop'
$output = Join-Path $directory 'Odessy.exe'
$source = Join-Path $directory 'desktop-launcher-source.cs'
$icon = Join-Path $root 'apps\desktop\windows\runner\resources\app_icon.ico'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
foreach ($path in @($target, $icon, $compiler)) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required file not found: $path" }
}
if (Test-Path -LiteralPath $source) { throw "Temporary source already exists: $source" }
$encodedTarget = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($target))
$code = @"
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;
[assembly: AssemblyTitle("Odessy")]
[assembly: AssemblyDescription("Odessy Desktop launcher")]
[assembly: AssemblyProduct("Odessy")]
[assembly: AssemblyCompany("Odessy")]
[assembly: AssemblyVersion("0.5.0.0")]
[assembly: AssemblyFileVersion("0.5.0.0")]
internal static class Program {
  [STAThread]
  private static void Main() {
    try {
      string target = Encoding.UTF8.GetString(Convert.FromBase64String("$encodedTarget"));
      if (!File.Exists(target)) throw new FileNotFoundException();
      Process.Start(new ProcessStartInfo(target) {
        WorkingDirectory = Path.GetDirectoryName(target), UseShellExecute = true
      });
    } catch (Exception) {
      MessageBox.Show("Odessy could not start. Check that the project and its local runtime are available.",
                      "Odessy", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
  }
}
"@
try {
  [IO.File]::WriteAllText($source, $code, [Text.UTF8Encoding]::new($false))
  & $compiler /nologo /target:winexe /optimize+ /reference:System.Windows.Forms.dll "/win32icon:$icon" "/out:$output" $source
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $output -PathType Leaf)) { throw 'Desktop launcher build failed' }
  Write-Output $output
} finally {
  if (Test-Path -LiteralPath $source) { Remove-Item -LiteralPath $source -Force }
}
