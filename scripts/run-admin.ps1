[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot 'lib.ps1')
$root = Get-PkubaRoot

Push-Location $root
try {
    & npm run dev:admin
    if ($LASTEXITCODE -ne 0) { throw '管理网站开发服务异常退出。' }
}
finally {
    Pop-Location
}
