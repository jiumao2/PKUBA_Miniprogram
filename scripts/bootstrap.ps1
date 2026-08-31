[CmdletBinding()]
param([switch]$Rebuild)

. (Join-Path $PSScriptRoot 'lib.ps1')
$root = Get-PkubaRoot

Assert-PkubaNode
Wait-PkubaDocker

if (-not (Test-Path -LiteralPath (Join-Path $root '.env'))) {
    Copy-Item -LiteralPath (Join-Path $root '.env.example') -Destination (Join-Path $root '.env')
    Write-Host '已从 .env.example 创建本地 .env。'
}

$docker = Get-PkubaDocker
& npm install --prefix $root
if ($LASTEXITCODE -ne 0) { throw 'npm install 失败。' }

$imageExists = $false
& $docker image inspect 'pkuba-dev-api:latest' 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    $imageExists = $true
}
if ($Rebuild -or -not $imageExists) {
    try {
        Invoke-PkubaCompose build api
    }
    catch {
        throw 'API 镜像构建失败。若 Docker Hub 无法访问，请先在 Docker Desktop 中配置 HTTPS 代理或镜像源，然后重试 ./scripts/bootstrap.ps1 -Rebuild。'
    }
}
else {
    Write-Host '已找到本地 API 镜像，跳过联网重建。需要重建时使用 -Rebuild。'
}
Invoke-PkubaCompose up -d db mailpit
Invoke-PkubaCompose run --rm api python manage.py migrate
Write-Host '初始化完成。运行 scripts/start-local.ps1 打开管理网站和微信小程序。'
