Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-PkubaRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}

function Get-PkubaDocker {
    $resolved = Get-Command docker -ErrorAction SilentlyContinue
    if ($resolved) {
        return $resolved.Source
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'),
        'C:\Program Files\Docker\Docker\resources\bin\docker.exe',
        'D:\Program Files\Docker\Docker\resources\bin\docker.exe',
        'D:\Docker\Docker\resources\bin\docker.exe',
        'D:\softwares\Docker\Docker\resources\bin\docker.exe'
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    throw '未找到 Docker CLI。请启动 Docker Desktop，或将 docker.exe 加入 PATH。'
}

function Invoke-PkubaCompose {
    $Arguments = @($args)
    $docker = Get-PkubaDocker
    $root = Get-PkubaRoot
    $previousPath = $env:Path
    try {
        $env:Path = "$(Split-Path -Parent $docker);$env:Path"
        & $docker compose --project-directory $root -f (Join-Path $root 'infra\compose.dev.yml') @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose 失败，退出码：$LASTEXITCODE"
        }
    }
    finally {
        $env:Path = $previousPath
    }
}
