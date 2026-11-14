# Rebind existing cached Dart dependencies to a new worktree, without downloads.
param([Parameter(Mandatory=$true)][string]$CacheCheckout)
$ErrorActionPreference = 'Stop'
$targetRoot = Split-Path $PSScriptRoot -Parent
$cacheRoot = (Resolve-Path -LiteralPath $CacheCheckout).Path
foreach ($relative in @('packages/odessy_client','apps/desktop')) {
    $source = Join-Path $cacheRoot "$relative/.dart_tool/package_config.json"
    $sourceUri = [uri]::new((Resolve-Path -LiteralPath $source).Path)
    $config = Get-Content -LiteralPath $source -Raw | ConvertFrom-Json
    foreach ($entry in $config.packages) {
        if ($entry.name -eq 'odessy_client') {
            $entry.rootUri = ([uri]::new((Join-Path $targetRoot 'packages/odessy_client') + '/')).AbsoluteUri
        } elseif ($entry.name -eq 'odessy_desktop') {
            $entry.rootUri = ([uri]::new((Join-Path $targetRoot 'apps/desktop') + '/')).AbsoluteUri
        } else {
            $entry.rootUri = ([uri]::new($sourceUri, $entry.rootUri)).AbsoluteUri
        }
    }
    $destination = Join-Path $targetRoot "$relative/.dart_tool"
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    $config | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $destination 'package_config.json') -Encoding utf8
    Write-Output "Bound cached Dart configuration: $relative"
}
