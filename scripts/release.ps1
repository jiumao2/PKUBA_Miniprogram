[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^v\d+\.\d+\.\d+$')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Invoke-Git([string[]]$Arguments) {
    & git -C $root @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git 命令失败：git $($Arguments -join ' ')"
    }
}

$branch = (& git -C $root branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $branch -ne 'main') {
    throw '正式版本只能从 main 分支创建。'
}

$changes = @(& git -C $root status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) { throw '无法读取 Git 工作区状态。' }
if ($changes.Count -gt 0) {
    throw "工作区不是干净状态，拒绝创建正式标签：`n$($changes -join "`n")"
}

Invoke-Git @('fetch', 'origin', 'main', '--tags')
$head = (& git -C $root rev-parse HEAD).Trim()
$originMain = (& git -C $root rev-parse origin/main).Trim()
if ($head -ne $originMain) {
    throw "本地 main 与 origin/main 不一致。HEAD=$head origin/main=$originMain"
}

& git -C $root show-ref --verify --quiet "refs/tags/$Version"
if ($LASTEXITCODE -eq 0) {
    throw "标签已经存在：$Version"
}

Invoke-Git @('tag', '--annotate', $Version, '--message', "Release $Version")
Invoke-Git @('push', 'origin', "refs/tags/$Version")

Write-Host ''
Write-Host "已推送版本标签：$Version"
Write-Host 'GitHub 将自动执行完整 CI、构建不可变镜像并部署生产服务器。'
Write-Host 'Actions：https://github.com/jiumao2/PKUBA_Miniprogram/actions'
