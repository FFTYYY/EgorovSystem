# Install the Egorov client (EGO, egolocal-run) on Windows.
# Works on machines without Python: uv downloads a standalone Python if needed.
#
#   powershell -ExecutionPolicy Bypass -File install-client.ps1
#   $env:EGOROV_SOURCE = 'git+https://github.com/FFTYYY/EgorovSystem'; .\install-client.ps1
$ErrorActionPreference = 'Stop'

$source = if ($env:EGOROV_SOURCE) { $env:EGOROV_SOURCE } else { 'egorovsystem>=1.0' }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host '==> Installing uv (https://docs.astral.sh/uv/)'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host '==> Installing the Egorov client'
uv tool install --force --python 3.12 $source
uv tool update-shell | Out-Null

Write-Host ''
Write-Host 'Done. Open a new terminal, then point this machine at your server:'
Write-Host '  setx EGOROV_SERVER "egorov.example.com"'
Write-Host '  setx EGOROV_PWD "ego1...."        # a device token from the admin UI'
Write-Host '  EGO --whoami'
Write-Host ''
Write-Host 'To use Egorov["name"] from your own Python code, also run in that environment:'
Write-Host '  pip install -U egorovsystem'
