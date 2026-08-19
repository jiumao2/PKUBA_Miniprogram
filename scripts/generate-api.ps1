[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot 'lib.ps1')
$root = Get-PkubaRoot

Invoke-PkubaCompose run --rm api python manage.py export_openapi --output /workspace/docs/openapi.json
Push-Location $root
try {
    & npm run generate:api
    if ($LASTEXITCODE -ne 0) { throw 'OpenAPI TypeScript 客户端生成失败。' }
}
finally {
    Pop-Location
}
