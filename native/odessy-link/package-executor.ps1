# Build a Windows x64 executor package. No installer and no model dependencies.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$NodeDirectory,
    [string]$NodeServer,
    [string]$ToolServerDirectory,
    [string]$OutputDirectory='../../build/executor-package',
    [ValidateSet('release','debug')][string]$Release='release',
    [switch]$SkipBuild
)
$ErrorActionPreference='Stop'
$crate=$PSScriptRoot
$repo=[IO.Path]::GetFullPath((Join-Path $crate '../..'))
$output=if([IO.Path]::IsPathRooted($OutputDirectory)){[IO.Path]::GetFullPath($OutputDirectory)}else{[IO.Path]::GetFullPath((Join-Path $crate $OutputDirectory))}
# Never clear an existing path, even if it happens to contain a checksum file.
if(Test-Path -LiteralPath $output){throw "output already exists; choose a new directory: $output"}
$parent=Split-Path -Parent $output
if(-not(Test-Path -LiteralPath $parent -PathType Container)){throw 'output parent must exist'}
if(-not $ToolServerDirectory){
    if($NodeServer){$ToolServerDirectory=Split-Path -Parent (Split-Path -Parent (Split-Path -Parent ([IO.Path]::GetFullPath($NodeServer))))}
    else{$ToolServerDirectory=Join-Path $repo 'tools'}
}
$ToolServerDirectory=[IO.Path]::GetFullPath($ToolServerDirectory)
$NodeDirectory=[IO.Path]::GetFullPath($NodeDirectory)
function Assert-PlainTree([string]$root){
    if((Get-Item -LiteralPath $root).LinkType){throw "linked component refused: $root"}
    foreach($item in Get-ChildItem -LiteralPath $root -Recurse -Force){
        if($item.LinkType){throw "linked component refused: $($item.FullName)"}
        if($item.Name -match '^(\.env($|\.)|\.npmrc$|credentials\.json$)'){throw "private configuration refused: $($item.FullName)"}
    }
}
$node=Join-Path $NodeDirectory 'node.exe'
foreach($required in @($node,(Join-Path $NodeDirectory 'LICENSE'),(Join-Path $ToolServerDirectory 'dist/src/server.js'),(Join-Path $ToolServerDirectory 'package.json'),(Join-Path $ToolServerDirectory 'package-lock.json'),(Join-Path $repo 'LICENSE'),(Join-Path $repo 'THIRD-PARTY-NOTICES.md'))){
    if(-not(Test-Path -LiteralPath $required -PathType Leaf)){throw "missing required package component: $required"}
}
if(-not(Test-Path -LiteralPath (Join-Path $ToolServerDirectory 'node_modules') -PathType Container)){throw 'build the tool-server dependencies first'}
$version=& $node --version
if($LASTEXITCODE -ne 0 -or $version -notmatch '^v\d+\.\d+\.\d+$'){throw 'Node runtime could not execute --version'}
foreach($component in @('dist','node_modules')){Assert-PlainTree (Join-Path $ToolServerDirectory $component)}
if((Get-Content (Join-Path $ToolServerDirectory 'package.json') -Raw | ConvertFrom-Json).type -ne 'module'){throw 'tool server must retain its ESM package metadata'}
Push-Location $crate
try{
    if(-not $SkipBuild){
        $buildArgs=@('build','--locked','--bins')
        if($Release -eq 'release'){$buildArgs+='--release'}
        & cargo @buildArgs
        if($LASTEXITCODE -ne 0){throw 'executor build failed'}
    }
    $target=Join-Path $crate "target/$Release"
    foreach($name in @('odessy-executor.exe','odessy-link.exe')){
        if(-not(Test-Path -LiteralPath (Join-Path $target $name) -PathType Leaf)){throw "missing binary: $name"}
    }
    # Unique staging is never recursively deleted. A failed stage remains for inspection.
    $stage=$output+'.preparing-'+[Guid]::NewGuid().ToString('N')
    New-Item -ItemType Directory -Path $stage | Out-Null
    foreach($name in @('odessy-executor.exe','odessy-link.exe')){Copy-Item -LiteralPath (Join-Path $target $name) -Destination $stage}
    New-Item -ItemType Directory -Path (Join-Path $stage 'node') | Out-Null
    Copy-Item -LiteralPath $node -Destination (Join-Path $stage 'node/node.exe')
    Copy-Item -LiteralPath (Join-Path $NodeDirectory 'LICENSE') -Destination (Join-Path $stage 'node/LICENSE')
    $tools=Join-Path $stage 'tool-server'
    New-Item -ItemType Directory -Path $tools | Out-Null
    foreach($name in @('dist','node_modules','package.json','package-lock.json')){Copy-Item -LiteralPath (Join-Path $ToolServerDirectory $name) -Destination $tools -Recurse}
    # Prune in staging only. Native modules are already built; do not run lifecycle scripts.
    Push-Location $tools
    try{& npm.cmd prune --omit=dev --ignore-scripts --offline --no-audit --no-fund; if($LASTEXITCODE -ne 0){throw 'production dependency pruning failed'}}finally{Pop-Location}
    foreach($name in @('LICENSE','THIRD-PARTY-NOTICES.md')){Copy-Item -LiteralPath (Join-Path $repo $name) -Destination $stage}
    Copy-Item -LiteralPath (Join-Path $repo 'installer/assets/odessy-boat.ico') -Destination (Join-Path $stage 'odessy-executor.ico')
    @('Odessy Executor development preview (unsigned).','Open odessy-executor.exe. Choose one workspace, click Allow pairing, and enter its six-digit code on the other device. Approve the request here. Keep this window open to serve it.','Short-code pairing uses the experimental SPAKE2/Noise profile; it is not formally audited.','The coordinator separately reviews outbound execution consent. Enrollment alone grants no execution access.','No model, voice stack, Python or development toolchain is required by this package.') | Set-Content (Join-Path $stage 'README.txt')
    $inventory=@{schema_version=1;platform='windows';architecture='x64';node_version=$version;unsigned=$true;short_code_supported=$true;pairing_profile='spake2-noise-v1-experimental';source_revision=(& git -C $repo rev-parse HEAD);source_dirty=$true;components=@('odessy-executor','odessy-link','node','tool-server')}
    $inventory | ConvertTo-Json | Set-Content (Join-Path $stage 'inventory.json')
    $lines=foreach($file in Get-ChildItem -LiteralPath $stage -Recurse -File){
        $relative=[IO.Path]::GetRelativePath($stage,$file.FullName).Replace('\','/')
        "$( (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() )  $relative"
    }
    $lines | Sort-Object | Set-Content (Join-Path $stage 'checksums.txt') -Encoding utf8NoBOM
    $selftest=& (Join-Path $stage 'odessy-executor.exe') --selftest 2>&1 | Out-String
    Write-Host $selftest
    if($LASTEXITCODE -ne 0){throw "package self-test failed; retained stage: $stage"}
    Move-Item -LiteralPath $stage -Destination $output
    $archive=$output+'.zip'
    if(Test-Path -LiteralPath $archive){throw 'archive already exists; package directory was preserved'}
    Compress-Archive -LiteralPath $output -DestinationPath $archive -CompressionLevel Fastest
    "$( (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() )  $([IO.Path]::GetFileName($archive))" | Set-Content ($archive+'.sha256')
    Write-Host "package: $output"
    Write-Host "archive: $archive"
}finally{Pop-Location}
