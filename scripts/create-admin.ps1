[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Username,
    [string]$DisplayName = '本地超级管理员'
)

. (Join-Path $PSScriptRoot 'lib.ps1')

Invoke-PkubaCompose run --rm api python manage.py create_local_admin $Username --display-name $DisplayName
