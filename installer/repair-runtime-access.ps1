param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$UserSid,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$resultPath = $null
trap {
    if ($resultPath) { @{success=$false;error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8 }
    Write-Error $_ -ErrorAction Continue
    exit 1
}
$root = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
if (-not [IO.Path]::IsPathRooted($InstallDir) -or $root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\')) { throw 'Use a dedicated installation directory.' }
foreach ($protected in @($env:WINDIR,$env:ProgramFiles,${env:ProgramFiles(x86)},$env:USERPROFILE)) {
    if ($protected) {
        $absolute = [IO.Path]::GetFullPath($protected).TrimEnd('\')
        if ($root -eq $absolute -or ($protected -ne $env:USERPROFILE -and $root.StartsWith($absolute + '\',[StringComparison]::OrdinalIgnoreCase))) { throw 'Protected directory refused.' }
    }
}
$walk = $root
while ($walk) {
    $item = Get-Item -LiteralPath $walk -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked installation path refused.' }
    $walk = [IO.Path]::GetDirectoryName($walk)
}
$sid = New-Object Security.Principal.SecurityIdentifier($UserSid)
if (-not $sid.IsAccountSid()) { throw 'A specific account SID is required.' }
$recordPath = Join-Path $root '.installer\state.json'
$state = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
if ($state.schema_version -ne 1 -or $state.status -ne 'completed' -or -not $state.summary.workflow_complete -or
    [IO.Path]::GetFullPath($state.plan.install_dir).TrimEnd('\') -ne $root -or
    @($state.steps | Where-Object { $_.status -ne 'completed' }).Count -gt 0) { throw 'A completed installation at its original path is required.' }
$runtime = Join-Path $root 'runtime'
if ((Get-Item -LiteralPath $runtime -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked runtime refused.' }
Write-Output ('Target: ' + $runtime)
Write-Output ('Account SID: ' + $sid.Value)
Write-Output 'Change: add only ReadAndExecute + Synchronize for this account, inherited by runtime children. No ownership change or global reset.'
if (-not $Apply) { return }
$resultPath = Join-Path $root '.installer\runtime-access-repair-result.json'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'One-time repair of an old administrator-owned runtime requires UAC approval. New installations do not.' }
$versionPath = Join-Path $runtime 'odessy-runtime.json'
$version = Get-Content -LiteralPath $versionPath -Raw | ConvertFrom-Json
if ($version.schema_version -ne 1 -or $version.versions.'python-runtime'.bits -ne 64 -or $version.versions.'node-runtime'.bits -ne 64) { throw 'Invalid runtime marker.' }
$items = @(Get-ChildItem -LiteralPath $runtime -Recurse -Force -ErrorAction Stop)
if (@($items | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { throw 'Linked runtime content refused.' }
$acl = Get-Acl -LiteralPath $runtime
$originalOwner = $acl.Owner
$backup = Join-Path $root ('.installer\runtime-access-before-' + [Guid]::NewGuid().ToString('N') + '.json')
@{target=$runtime;user_sid=$sid.Value;owner=$originalOwner;sddl=$acl.Sddl} | ConvertTo-Json | Set-Content -LiteralPath $backup -Encoding UTF8
$rights = [Security.AccessControl.FileSystemRights]::ReadAndExecute -bor [Security.AccessControl.FileSystemRights]::Synchronize
$inherit = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
$rule = New-Object Security.AccessControl.FileSystemAccessRule($sid,$rights,$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $runtime -AclObject $acl
if ((Get-Acl -LiteralPath $runtime).Owner -ne $originalOwner) { throw 'Unexpected ownership change.' }
Write-Output ('ACL backup: ' + $backup)
Write-Output 'Repair completed. Close the administrator terminal and verify from an ordinary process.'

@{success=$true;target=$runtime;user_sid=$sid.Value;owner_unchanged=$true;backup=$backup} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
