[CmdletBinding()]
param([switch]$BackendOnly)

. (Join-Path $PSScriptRoot 'lib.ps1')
$root = Get-PkubaRoot

Invoke-PkubaCompose up -d db mailpit api
if (-not $BackendOnly) {
    Push-Location $root
    try {
        & npm run dev
        if ($LASTEXITCODE -ne 0) { throw '前端开发进程异常退出。' }
    }
    finally {
        Pop-Location
    }
}
