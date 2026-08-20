[CmdletBinding()]
param(
    [string]$AdminUsername = '',
    [switch]$NoBrowser,
    [switch]$NoWechat,
    [switch]$UseWechatCli
)

. (Join-Path $PSScriptRoot 'lib.ps1')
$root = Get-PkubaRoot
$apiHealthUrl = 'http://127.0.0.1:8000/api/v1/health'
$adminUrl = 'http://127.0.0.1:5173'
$miniappRoot = Join-Path $root 'apps\miniapp'

Assert-PkubaNode
Wait-PkubaDocker
if (-not (Test-Path -LiteralPath (Join-Path $root 'node_modules'))) {
    throw '尚未安装前端依赖。请先运行 ./scripts/bootstrap.ps1。'
}

Invoke-PkubaCompose up -d db mailpit api
Invoke-PkubaCompose exec -T api python manage.py migrate --noinput
Invoke-PkubaCompose exec -T api python manage.py seed_demo --if-empty
Wait-PkubaUrl -Url $apiHealthUrl -TimeoutSeconds 60

if ($AdminUsername) {
    Invoke-PkubaCompose run --rm api python manage.py create_local_admin $AdminUsername
}

Push-Location $root
try {
    & npm --workspace @pkuba/miniapp run build:weapp
    if ($LASTEXITCODE -ne 0) { throw '微信小程序构建失败。' }
}
finally {
    Pop-Location
}

try {
    Invoke-WebRequest -UseBasicParsing -Uri $adminUrl -TimeoutSec 2 | Out-Null
}
catch {
    $runner = Join-Path $PSScriptRoot 'run-admin.ps1'
    $windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = "-NoExit -NoProfile -ExecutionPolicy Bypass -File `"$runner`""
    Start-Process -FilePath $windowsPowerShell -ArgumentList $arguments | Out-Null
}
Wait-PkubaUrl -Url $adminUrl -TimeoutSeconds 60

if (-not $NoBrowser) {
    Start-Process $adminUrl | Out-Null
}

if (-not $NoWechat) {
    if ($UseWechatCli) {
        $wechatCli = Get-PkubaWechatCli
        $wechatOutput = & $wechatCli open --project $miniappRoot --lang zh 2>&1
        $wechatOutput | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0 -or ($wechatOutput -join "`n") -match 'service port disabled|服务端口已关闭') {
            throw '微信开发者工具 CLI 服务端口未开启。请在工具中手动导入项目，或开启服务端口后重试 -UseWechatCli。'
        }
    }
    else {
        $wechatDevTools = Get-PkubaWechatDevTools
        Start-Process -FilePath $wechatDevTools | Out-Null
        Write-Host "微信小程序项目目录：$miniappRoot"
    }
}

Write-Host ''
Write-Host "管理网站：$adminUrl"
Write-Host "API 文档：http://127.0.0.1:8000/api/v1/docs"
Write-Host "邮件调试：http://127.0.0.1:8025"
Write-Host "微信小程序项目：$miniappRoot"
if (-not $AdminUsername) {
    Write-Host '尚未指定登录账号。如需创建超级管理员，请运行：'
    Write-Host '  ./scripts/create-admin.ps1 -Username jiumao'
}
