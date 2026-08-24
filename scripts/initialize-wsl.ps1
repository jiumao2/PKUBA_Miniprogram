[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('Demo', 'Legacy2026')]
    [string]$Mode,
    [string]$Distro = 'Ubuntu-24.04',
    [string]$LegacySource = '',
    [Parameter(Mandatory)]
    [ValidateSet('INITIALIZE_LOCAL_DATA')]
    [string]$Confirmation
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env.wsl.local'
if (-not (Test-Path -LiteralPath $envFile)) {
    throw '请先运行 ./scripts/deploy-wsl.ps1 创建并启动 WSL 环境。'
}

function Convert-ToWslPath([string]$WindowsPath) {
    $resolved = [IO.Path]::GetFullPath($WindowsPath)
    if ($resolved -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "仅支持转换本地盘符路径：$WindowsPath"
    }
    $drive = $Matches[1].ToLowerInvariant()
    $tail = $Matches[2].Replace('\', '/')
    return "/mnt/$drive/$tail"
}

$distros = (& wsl.exe --list --quiet) -replace "`0", '' | ForEach-Object { $_.Trim() }
if ($Distro -notin $distros) { throw "未找到 WSL 发行版：$Distro" }

$repoWsl = Convert-ToWslPath $root
$modeArgument = if ($Mode -eq 'Demo') { 'demo' } else { 'legacy-2026' }
$legacyWsl = ''
if ($Mode -eq 'Legacy2026') {
    if (-not $LegacySource) {
        $LegacySource = Join-Path (Split-Path -Parent $root) '北大篮协小程序\Backup'
    }
    if (-not (Test-Path -LiteralPath $LegacySource -PathType Container)) {
        throw "旧数据目录不存在：$LegacySource"
    }
    $legacyWsl = Convert-ToWslPath $LegacySource
}

$arguments = @(
    '-d', $Distro, '-u', 'root', '--',
    'env', 'PKUBA_CONFIRM_LOCAL_INITIALIZATION=INITIALIZE_LOCAL_DATA',
    'bash', "$repoWsl/scripts/wsl/initialize-local.sh", $modeArgument
)
if ($legacyWsl) { $arguments += $legacyWsl }
& wsl.exe @arguments
if ($LASTEXITCODE -ne 0) { throw 'WSL 本地数据初始化失败。' }

Write-Host '本地业务数据初始化完成。之后运行 deploy-wsl.ps1 不会再次导入或改写这些数据。'
