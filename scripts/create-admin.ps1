[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Username
)

. (Join-Path $PSScriptRoot 'lib.ps1')

Invoke-PkubaCompose run --rm api python manage.py create_local_admin $Username
