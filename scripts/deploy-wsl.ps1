[CmdletBinding()]
param(
    [string]$Distro = 'Ubuntu-24.04',
    [int]$WebPort = 8088,
    [int]$MailPort = 8089,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env.wsl.local'

function Read-DotEnv([string]$Path) {
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $values }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if (-not $line -or $line.TrimStart().StartsWith('#') -or -not $line.Contains('=')) { continue }
        $name, $value = $line.Split('=', 2)
        $values[$name.Trim()] = $value.Trim().Trim('"')
    }
    return $values
}

$existing = Read-DotEnv $envFile

$distros = (& wsl.exe --list --quiet) -replace "`0", '' | ForEach-Object { $_.Trim() }
if ($Distro -notin $distros) {
    throw "未找到 WSL 发行版：$Distro"
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

function New-RandomHex([int]$ByteCount) {
    return [Convert]::ToHexString(
        [Security.Cryptography.RandomNumberGenerator]::GetBytes($ByteCount)
    ).ToLowerInvariant()
}

$projectEnv = Read-DotEnv (Join-Path $root '.env')
$projectConfig = Get-Content -Raw -LiteralPath (Join-Path $root 'apps\miniapp\project.config.json') | ConvertFrom-Json
$repoWsl = Convert-ToWslPath $root
$dbPassword = if ($existing.PKUBA_DB_PASSWORD) { $existing.PKUBA_DB_PASSWORD } else { New-RandomHex 24 }
$djangoSecret = if ($existing.DJANGO_SECRET_KEY) { $existing.DJANGO_SECRET_KEY } else { New-RandomHex 48 }
$wechatAppId = if ($projectEnv.WECHAT_APP_ID) { $projectEnv.WECHAT_APP_ID } else { $projectConfig.appid }
$wechatSecret = if ($projectEnv.WECHAT_APP_SECRET) { $projectEnv.WECHAT_APP_SECRET } else { '' }
$qwenApiKey = if ($env:QWEN_API_KEY) {
    $env:QWEN_API_KEY
}
elseif ($projectEnv.QWEN_API_KEY) {
    $projectEnv.QWEN_API_KEY
}
elseif ($existing.QWEN_API_KEY) {
    $existing.QWEN_API_KEY
}
else {
    ''
}
$qwenBaseUrl = if ($projectEnv.QWEN_BASE_URL) { $projectEnv.QWEN_BASE_URL } else { 'https://dashscope.aliyuncs.com/compatible-mode/v1' }
$qwenModel = if ($projectEnv.QWEN_MODEL) { $projectEnv.QWEN_MODEL } else { 'qwen3.8-max' }
$qwenReasoningEffort = if ($projectEnv.QWEN_REASONING_EFFORT) { $projectEnv.QWEN_REASONING_EFFORT } else { 'xhigh' }
$scoresheetRecognitionUpscaleTargetPixels = if ($projectEnv.SCORESHEET_RECOGNITION_UPSCALE_TARGET_PIXELS) { $projectEnv.SCORESHEET_RECOGNITION_UPSCALE_TARGET_PIXELS } else { '8000000' }
$scoresheetRecognitionTimeoutSeconds = if ($projectEnv.SCORESHEET_RECOGNITION_TIMEOUT_SECONDS) { $projectEnv.SCORESHEET_RECOGNITION_TIMEOUT_SECONDS } else { '180' }
$gitCommit = if ($env:PKUBA_GIT_COMMIT) {
    $env:PKUBA_GIT_COMMIT
}
else {
    (& git -C $root rev-parse HEAD).Trim()
}
if (-not $gitCommit) { $gitCommit = 'unknown' }

$envLines = @(
    "PKUBA_DB_PASSWORD=$dbPassword"
    "DJANGO_SECRET_KEY=$djangoSecret"
    "WECHAT_APP_ID=$wechatAppId"
    "WECHAT_APP_SECRET=$wechatSecret"
    "QWEN_API_KEY=$qwenApiKey"
    "QWEN_BASE_URL=$qwenBaseUrl"
    "QWEN_MODEL=$qwenModel"
    "QWEN_REASONING_EFFORT=$qwenReasoningEffort"
    "SCORESHEET_RECOGNITION_UPSCALE_TARGET_PIXELS=$scoresheetRecognitionUpscaleTargetPixels"
    "SCORESHEET_RECOGNITION_TIMEOUT_SECONDS=$scoresheetRecognitionTimeoutSeconds"
    "PKUBA_GIT_COMMIT=$gitCommit"
    "PKUBA_WEB_PORT=$WebPort"
    "PKUBA_MAIL_PORT=$MailPort"
)
[IO.File]::WriteAllText(
    $envFile,
    (($envLines -join "`n") + "`n"),
    [Text.UTF8Encoding]::new($false)
)

if (-not $SkipInstall) {
    $proxyPort = ''
    if ($env:HTTPS_PROXY) {
        $proxyPort = ([Uri]$env:HTTPS_PROXY).Port
    }
    $installArguments = @(
        '-d', $Distro, '-u', 'root', '--',
        'env', "PKUBA_DOCKER_PROXY_PORT=$proxyPort",
        'bash', "$repoWsl/scripts/wsl/install-deps.sh"
    )
    & wsl.exe @installArguments
    if ($LASTEXITCODE -ne 0) { throw 'WSL Docker 安装或启动失败。' }
}

& wsl.exe -d $Distro -u root -- bash "$repoWsl/scripts/wsl/deploy-local.sh"
if ($LASTEXITCODE -ne 0) { throw 'PKUBA WSL 部署失败。' }

$keepAlivePattern = 'scripts/wsl/keepalive.sh'
$keepAliveProcess = Get-CimInstance Win32_Process -Filter "Name = 'wsl.exe'" |
    Where-Object { $_.CommandLine -like "*$keepAlivePattern*" } |
    Select-Object -First 1
if (-not $keepAliveProcess) {
    $keepAliveArguments = @(
        '-d', $Distro, '-u', 'root', '--',
        'bash', "$repoWsl/scripts/wsl/keepalive.sh"
    )
    Start-Process -FilePath "$env:SystemRoot\System32\wsl.exe" -ArgumentList $keepAliveArguments -WindowStyle Hidden
    Start-Sleep -Seconds 1
}

$wslIp = (& wsl.exe -d $Distro -- hostname -I).Trim().Split(' ')[0]
if (-not $wslIp) { throw '无法读取 WSL IPv4 地址。' }
foreach ($port in @($WebPort, $MailPort)) {
    & netsh.exe interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=$port | Out-Null
    & netsh.exe interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=$port connectaddress=$wslIp connectport=$port | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "无法建立 localhost:$port 到 WSL 的端口转发；请在管理员 PowerShell 中重试。"
    }
}

$previousApiBase = $env:PKUBA_API_BASE_URL
try {
    $env:PKUBA_API_BASE_URL = "http://localhost:$WebPort"
    Push-Location $root
    try {
        & npm --workspace @pkuba/miniapp run build:weapp
        if ($LASTEXITCODE -ne 0) { throw '微信小程序构建失败。' }
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

$health = Invoke-RestMethod -Uri "http://localhost:$WebPort/api/v1/health/ready" -TimeoutSec 10
if ($health.status -ne 'ok') { throw 'WSL API 健康检查未通过。' }

Write-Host ''
Write-Host "管理网站：http://localhost:$WebPort/"
Write-Host "API 文档：http://localhost:$WebPort/api/v1/docs"
Write-Host "邮件调试：http://localhost:$MailPort/"
Write-Host "微信小程序项目：$(Join-Path $root 'apps\miniapp')"
Write-Host '本次部署未导入、生成或修改任何业务数据和管理员账号。'
if (-not $wechatSecret) {
    Write-Warning 'WECHAT_APP_SECRET 尚未配置；公开页面和网页管理员登录可用，小程序微信身份登录需补充后重新部署。'
}
if (-not $qwenApiKey) {
    Write-Warning 'QWEN_API_KEY 尚未配置；记录表会进入可人工录入的识别失败状态，不会调用外部模型。'
}
