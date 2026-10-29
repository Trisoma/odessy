# Assemble the Windows executor package and write its checksum manifest.
#
# Nothing is invented here: every required component must already exist, and the
# resulting package is verified with `odessy-executor --selftest` before this script
# reports success. A missing component is an error, not a warning.
#
#   pwsh -File native/odessy-link/package-executor.ps1 `
#       -NodeDirectory C:\odessy\node `
#       -NodeServer C:\odessy\tool-server\index.js `
#       -OutputDirectory build\executor-package
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$NodeDirectory,
    [Parameter(Mandatory = $true)][string]$NodeServer,
    [string]$OutputDirectory = 'build/executor-package',
    [string]$Release = 'release'
)

$ErrorActionPreference = 'Stop'
$crate = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $crate
try {
    $exe = if ($IsWindows -or $env:OS -eq 'Windows_NT') { 'odessy-executor.exe' } else { 'odessy-executor' }
    $linkExe = 'odessy-link.exe'
    $target = Join-Path $crate "target/$Release"

    # 1. Build both binaries. The window and the pairing component are one product.
    Write-Host "building odessy-link and odessy-executor ($Release)…"
    & cargo build --$Release --bins
    if ($LASTEXITCODE -ne 0) { throw 'cargo build failed; the package was not assembled' }

    foreach ($required in @((Join-Path $target $exe), (Join-Path $target $linkExe))) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "missing component: $required"
        }
    }
    if (-not (Test-Path -LiteralPath $NodeDirectory -PathType Container)) {
        throw "missing Node runtime directory: $NodeDirectory"
    }
    if (-not (Test-Path -LiteralPath $NodeServer -PathType Leaf)) {
        throw "missing tool server entry: $NodeServer"
    }

    # 2. Lay the package out. Paths are relative so the manifest is portable.
    $output = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
        $OutputDirectory
    } else {
        Join-Path $crate $OutputDirectory
    }
    # Never recursively delete a directory this script did not create: the path must sit
    # under build/, or already look like one of our packages.
    $outputFull = [System.IO.Path]::GetFullPath($output).TrimEnd('\', '/')
    $buildRoot = [System.IO.Path]::GetFullPath((Join-Path $crate '../../build')).TrimEnd('\', '/')
    $ours = Test-Path -LiteralPath (Join-Path $outputFull 'checksums.txt')
    if (-not $ours -and -not $outputFull.StartsWith($buildRoot + [System.IO.Path]::DirectorySeparatorChar, 'OrdinalIgnoreCase')) {
        throw "refusing to clear '$outputFull': it is neither inside build/ nor an existing executor package"
    }
    if ((Split-Path -Leaf $outputFull) -notmatch '^[A-Za-z0-9._-]+$') {
        throw "refusing to use the unusual package directory name '$outputFull'"
    }
    if (Test-Path -LiteralPath $outputFull) { Remove-Item -LiteralPath $outputFull -Recurse -Force }
    New-Item -ItemType Directory -Path $outputFull | Out-Null
    $output = $outputFull

    # 3. Refuse placeholder components. A package that looks complete but contains
    # stubs is worse than no package.
    $nodeExe = Join-Path $NodeDirectory 'node.exe'
    if (-not (Test-Path -LiteralPath $nodeExe -PathType Leaf)) {
        throw "missing Node runtime: $nodeExe"
    }
    $nodeInfo = Get-Item -LiteralPath $nodeExe
    $nodeHeader = [System.IO.File]::ReadAllBytes($nodeExe)[0..1]
    if ($nodeInfo.Length -lt 1MB -or -not ($nodeHeader[0] -eq 0x4D -and $nodeHeader[1] -eq 0x5A)) {
        throw "$nodeExe is not a redistributable Node runtime (too small or not a PE binary)"
    }
    $serverInfo = Get-Item -LiteralPath $NodeServer
    if ($serverInfo.Length -lt 1KB) {
        throw "$NodeServer is a placeholder: the built tool server must be at least 1 KiB"
    }
    foreach ($notice in @('THIRD-PARTY-NOTICES.md', 'LICENSE')) {
        $source = Join-Path (Split-Path -Parent (Split-Path -Parent $crate)) $notice
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "$notice is missing; a package without notices is not releasable"
        }
    }

    Copy-Item -LiteralPath (Join-Path $target $exe) -Destination $output
    Copy-Item -LiteralPath (Join-Path $target $linkExe) -Destination $output
    Copy-Item -LiteralPath $NodeServer -Destination (Join-Path $output 'tool-server.js')
    Copy-Item -LiteralPath $NodeDirectory -Destination (Join-Path $output 'node') -Recurse

    foreach ($notice in @('THIRD-PARTY-NOTICES.md', 'LICENSE')) {
        $source = Join-Path (Split-Path -Parent (Split-Path -Parent $crate)) $notice
        Copy-Item -LiteralPath $source -Destination $output
    }
    $icon = Join-Path $crate 'assets/odessy-executor.ico'
    if (Test-Path -LiteralPath $icon -PathType Leaf) {
        Copy-Item -LiteralPath $icon -Destination $output
    } else {
        Write-Warning 'no executor icon was found; the window uses the default icon'
    }

    # 3. Write the checksum manifest the executor verifies before it starts.
    $lines = foreach ($file in Get-ChildItem -LiteralPath $output -Recurse -File) {
        if ($file.Name -eq 'checksums.txt') { continue }
        $relative = [System.IO.Path]::GetRelativePath($output, $file.FullName) -replace '\\', '/'
        $digest = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$digest  $relative"
    }
    if (-not $lines) { throw 'the package contains no files; refusing to write an empty manifest' }
    $lines | Sort-Object | Set-Content -LiteralPath (Join-Path $output 'checksums.txt') -Encoding utf8NoBOM

    # 4. Prove it: the packaged executor must resolve and answer.
    Write-Host 'verifying the assembled package…'
    $selftest = & (Join-Path $output $exe) --selftest 2>&1 | Out-String
    Write-Host $selftest
    if ($LASTEXITCODE -ne 0) { throw 'the packaged executor failed its self-test' }
    if ($selftest -notmatch 'checksums: \d+ files verified') {
        throw 'the packaged executor could not verify its own checksums'
    }

    Write-Host "package: $output"
    Write-Host "manifest: $(Join-Path $output 'checksums.txt')"
}
finally {
    Pop-Location
}
