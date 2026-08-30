# Installs Mindboard into its own environment inside this folder.
# Nothing is installed system-wide and nothing leaves this machine.
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$Venv = if ($env:MINDBOARD_VENV) { $env:MINDBOARD_VENV } else { '.venv' }

function Say  ($m) { Write-Host "  $m" }
function Fail ($m) { Write-Host ""; Write-Host "  $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  Installing Mindboard" -ForegroundColor White
Write-Host ""

$py = $null
foreach ($candidate in @('py -3', 'python', 'python3')) {
  $exe, $arg = $candidate.Split(' ', 2)
  if (Get-Command $exe -ErrorAction SilentlyContinue) {
    $check = "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
    if ($arg) { & $exe $arg -c $check } else { & $exe -c $check }
    if ($LASTEXITCODE -eq 0) { $py = $candidate; break }
  }
}
if (-not $py) { Fail "Python 3.10 or newer is required. Install it from python.org, then run this again." }

$pyExe, $pyArg = $py.Split(' ', 2)
$version = if ($pyArg) { & $pyExe $pyArg --version } else { & $pyExe --version }
Say "python        $version"

$venvPy = Join-Path $Venv 'Scripts\python.exe'
$venvMb = Join-Path $Venv 'Scripts\mindboard.exe'

if (Get-Command uv -ErrorAction SilentlyContinue) {
  Say "environment   uv -> $Venv"
  uv venv $Venv | Out-Null
  uv pip install --quiet --python $venvPy -e .
} else {
  Say "environment   venv -> $Venv  (install uv for a faster setup)"
  if ($pyArg) { & $pyExe $pyArg -m venv $Venv } else { & $pyExe -m venv $Venv }
  & $venvPy -m pip install --quiet --upgrade pip
  & $venvPy -m pip install --quiet -e .
}
Say "packages      installed"

Write-Host ""
& $venvMb install

Write-Host ""
Write-Host "  Run it with" -ForegroundColor White
Write-Host "    $venvMb run"
Write-Host ""
