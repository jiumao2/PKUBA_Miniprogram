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

function Assert-PkubaNode {
    $node = Get-Command node -ErrorAction SilentlyContinue
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $node -or -not $npm) {
        throw '未找到 Node.js 或 npm。请安装 Node.js 24 后重新运行。'
    }

    $nodeVersion = (& $node.Source --version).Trim()
    $npmVersion = (& $npm.Source --version).Trim()
    if ($nodeVersion -notmatch '^v24\.') {
        throw "当前 Node.js 为 $nodeVersion；本项目要求 Node.js 24。"
    }
    if ($npmVersion -notmatch '^11\.') {
        throw "当前 npm 为 $npmVersion；本项目要求 npm 11。"
    }
}

function Get-PkubaDockerDesktop {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'),
        'C:\Program Files\Docker\Docker\Docker Desktop.exe',
        'D:\Program Files\Docker\Docker\Docker Desktop.exe',
        'D:\Docker\Docker\Docker Desktop.exe'
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Test-PkubaDockerReady {
    $docker = Get-PkubaDocker
    & $docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Wait-PkubaDocker {
    param([int]$TimeoutSeconds = 120)

    if (Test-PkubaDockerReady) {
        return
    }

    $desktop = Get-PkubaDockerDesktop
    if (-not $desktop) {
        throw 'Docker Desktop 尚未运行，并且未找到 Docker Desktop.exe。'
    }
    Write-Host '正在启动 Docker Desktop，请稍候……'
    Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        if (Test-PkubaDockerReady) {
            return
        }
    }
    throw "Docker Desktop 在 $TimeoutSeconds 秒内没有就绪。请打开 Docker Desktop 查看错误。"
}

function Get-PkubaWechatCli {
    $candidates = @(
        'C:\Program Files (x86)\Tencent\微信web开发者工具\cli.bat',
        'C:\Program Files (x86)\Tencent\微信开发者工具\cli.bat',
        'C:\Program Files\Tencent\微信web开发者工具\cli.bat',
        'C:\Program Files\Tencent\微信开发者工具\cli.bat',
        (Join-Path $env:LOCALAPPDATA '微信开发者工具\cli.bat'),
        (Join-Path $env:LOCALAPPDATA 'Programs\微信开发者工具\cli.bat')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    throw '未找到微信开发者工具 cli.bat。请先安装微信开发者工具稳定版。'
}

function Get-PkubaWechatDevTools {
    $candidates = @(
        'C:\Program Files (x86)\Tencent\微信web开发者工具\微信开发者工具.exe',
        'C:\Program Files (x86)\Tencent\微信开发者工具\微信开发者工具.exe',
        'C:\Program Files\Tencent\微信web开发者工具\微信开发者工具.exe',
        'C:\Program Files\Tencent\微信开发者工具\微信开发者工具.exe',
        (Join-Path $env:LOCALAPPDATA '微信开发者工具\微信开发者工具.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\微信开发者工具\微信开发者工具.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    throw '未找到微信开发者工具主程序。请先安装微信开发者工具稳定版。'
}

function Wait-PkubaUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }
    throw "服务在 $TimeoutSeconds 秒内没有就绪：$Url"
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
