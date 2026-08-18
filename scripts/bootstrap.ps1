[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot 'lib.ps1')
$root = Get-PkubaRoot

if (-not (Test-Path -LiteralPath (Join-Path $root '.env'))) {
    Copy-Item -LiteralPath (Join-Path $root '.env.example') -Destination (Join-Path $root '.env')
    Write-Host '已从 .env.example 创建本地 .env。'
}

Get-PkubaDocker | Out-Null
& npm install --prefix $root
if ($LASTEXITCODE -ne 0) { throw 'npm install 失败。' }

Invoke-PkubaCompose build api
Invoke-PkubaCompose up -d db mailpit
Invoke-PkubaCompose run --rm api python manage.py migrate
Write-Host '初始化完成。运行 scripts/dev.ps1 启动开发服务。'
