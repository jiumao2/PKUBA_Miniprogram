[CmdletBinding()]
param(
    [string]$Distro = 'Ubuntu-24.04',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bashScript = Join-Path $root 'scripts\wsl\check-api.sh'

function Convert-ToWslPath([string]$WindowsPath) {
    $resolved = [IO.Path]::GetFullPath($WindowsPath)
    if ($resolved -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "Only local drive paths are supported: $WindowsPath"
    }
    $drive = $Matches[1].ToLowerInvariant()
    $tail = $Matches[2].Replace('\', '/')
    return "/mnt/$drive/$tail"
}
$bashScriptWsl = Convert-ToWslPath $bashScript

$arguments = @('-d', $Distro, '-u', 'root', '--', 'bash', $bashScriptWsl) + $PytestArgs
& wsl.exe @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'WSL API checks failed.'
}

$previousApiBase = $env:PKUBA_API_BASE_URL
try {
    $env:PKUBA_API_BASE_URL = 'http://localhost:8088'
    Push-Location $root
    try {
        & npm run generate:api
        if ($LASTEXITCODE -ne 0) { throw 'OpenAPI TypeScript client generation failed.' }
        & npm run typecheck
        if ($LASTEXITCODE -ne 0) { throw 'Frontend type checks failed.' }
        & npm test
        if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed.' }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend builds failed.' }
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($null -eq $previousApiBase) {
        Remove-Item Env:PKUBA_API_BASE_URL -ErrorAction SilentlyContinue
    }
    else {
        $env:PKUBA_API_BASE_URL = $previousApiBase
    }
}
