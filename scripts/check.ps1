[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot 'lib.ps1')
$root = Get-PkubaRoot

Invoke-PkubaCompose build api
Invoke-PkubaCompose up -d db
Invoke-PkubaCompose run --rm api ruff check .
Invoke-PkubaCompose run --rm api python manage.py makemigrations --check --dry-run
Invoke-PkubaCompose run --rm api pytest

Push-Location $root
try {
    & npm run typecheck
    if ($LASTEXITCODE -ne 0) { throw '前端类型检查失败。' }
    & npm test
    if ($LASTEXITCODE -ne 0) { throw '前端测试失败。' }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败。' }
}
finally {
    Pop-Location
}
