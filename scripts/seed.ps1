[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot 'lib.ps1')
Invoke-PkubaCompose up -d db
Invoke-PkubaCompose run --rm api python manage.py migrate
Invoke-PkubaCompose run --rm api python manage.py seed_demo
