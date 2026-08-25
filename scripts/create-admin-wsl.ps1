[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Username,
    [string]$Distro = 'Ubuntu-24.04'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env.wsl.local'
if (-not (Test-Path -LiteralPath $envFile)) {
    throw '请先运行 ./scripts/deploy-wsl.ps1 创建并启动 WSL 环境。'
}
if ($Username -notmatch '^[\p{L}\p{N}_.@+-]{2,32}$') {
    throw '管理员用户名需为 2-32 个字母、数字或 _.@+- 字符。'
}

function Convert-ToWslPath([string]$WindowsPath) {
    $resolved = [IO.Path]::GetFullPath($WindowsPath)
    if ($resolved -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "仅支持转换本地盘符路径：$WindowsPath"
    }
    $drive = $Matches[1].ToLowerInvariant()
    $tail = $Matches[2].Replace('\', '/')
    return "/mnt/$drive/$tail"
}

$repoWsl = Convert-ToWslPath $root
& wsl.exe -d $Distro -u root -- bash -lc @"
set -euo pipefail
cd '$repoWsl'
docker compose --project-name pkuba-wsl --project-directory '$repoWsl' \
  --env-file '$repoWsl/.env.wsl.local' -f '$repoWsl/infra/compose.wsl.yml' \
  exec api python manage.py create_local_admin '$Username'
"@
if ($LASTEXITCODE -ne 0) { throw 'WSL 本地管理员创建失败。' }
