# Installs Mindboard into its own environment inside this folder.
# Nothing is installed system-wide and nothing leaves this machine.
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$Venv = if ($env:MINDBOARD_VENV) { $env:MINDBOARD_VENV } else { '.venv' }

function Say  ($m) { Write-Host "  $m" }
function Fail ($m) { Write-Host ""; Write-Host "  $m" -ForegroundColor Red; exit 1 }

function New-Environment {
  param([string]$Path)
  $venvPy = Join-Path $Path 'Scripts\python.exe'
  if (Get-Command uv -ErrorAction SilentlyContinue) {
    Say "environment   uv -> $Path"
    # --clear says what we mean: only ever called for a missing or broken
    # environment. Without it, newer uv asks - and answers "replace" itself.
    uv venv --clear $Path | Out-Null
    uv pip install --quiet --python $venvPy -e .
  } else {
    Say "environment   venv -> $Path  (install uv for a faster setup)"
    if (Test-Path $Path) { Remove-Item -Recurse -Force $Path }
    if ($script:PyArg) { & $script:PyExe $script:PyArg -m venv $Path }
    else { & $script:PyExe -m venv $Path }
    & $venvPy -m pip install --quiet --upgrade pip
    & $venvPy -m pip install --quiet -e .
  }
}

# Updating keeps the environment and reinstalls the package into it. Replacing
# a working environment to update it is how a reinstall launched from the menu
# - which runs from that very environment - deleted half of it and stopped:
# Windows will not remove a program that is running.
function Update-Environment {
  param([string]$Path)
  $venvPy = Join-Path $Path 'Scripts\python.exe'
  Say "environment   updating $Path"
  if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install --quiet --python $venvPy -e .
  } else {
    & $venvPy -m pip install --quiet -e .
  }
}

# Programs running from the environment hold its files open. Wait a moment for
# them - the menu closes itself after starting this - then say so plainly.
function Wait-Unused {
  param([string]$Path)
  $full = (Resolve-Path -LiteralPath $Path -ErrorAction SilentlyContinue)
  if (-not $full) { return }
  $prefix = $full.Path.TrimEnd('\') + '\'
  $self = $PID
  $busyNow = { Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Id -ne $self -and $_.Path -and $_.Path.StartsWith($prefix, 'OrdinalIgnoreCase') } }
  # the menu that started this closes itself: give it a few seconds
  for ($i = 0; $i -lt 6; $i++) {
    if (-not (& $busyNow)) { return }
    if ($i -eq 0) { Say "waiting       for the menu to close ..." }
    Start-Sleep -Seconds 1
  }
  # anything still open is another Mindboard window: say what, and offer to stop it
  $busy = & $busyNow
  Write-Host ""
  Write-Host "  Mindboard is still running from this folder, so it cannot be updated yet:"
  foreach ($p in $busy) {
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)" -ErrorAction SilentlyContinue).CommandLine
    $what = if ($cmd -match ' run') { "Mindboard itself (a board or the Experts open in a browser)" }
            elseif ($cmd -match ' menu') { "the Mindboard menu, in another window" }
            else { $p.ProcessName }
    Write-Host ("    - {0}   (process {1})" -f $what, $p.Id)
  }
  Write-Host ""
  $answer = Read-Host "  Stop it and update now? Your work is already saved. [Y/n]"
  if ($answer -match '^(n|no)$') {
    Fail ("Not updated. Close Mindboard (press Enter in its window, or close the window), " +
          "then choose Reinstall / update again.")
  }
  $busy | Stop-Process -Force -ErrorAction SilentlyContinue
  for ($i = 0; $i -lt 10; $i++) {
    if (-not (& $busyNow)) { Say "stopped       the running Mindboard"; return }
    Start-Sleep -Milliseconds 500
  }
  Fail "Could not stop the running Mindboard - close its window, then run this again."
}

# An install can look successful and still be unusable - a stale or half-written
# environment leaves an empty `mindboard` folder in site-packages, and every
# submodule disappears. Prove it runs before saying it worked.
function Test-Environment {
  param([string]$Path)
  $mb = Join-Path $Path 'Scripts\mindboard.exe'
  if (-not (Test-Path $mb)) { return $false }
  try { & $mb version *> $null } catch { return $false }
  return ($LASTEXITCODE -eq 0)
}

Write-Host ""
Write-Host "  Installing Mindboard" -ForegroundColor White
Write-Host ""

$script:PyExe = $null
$script:PyArg = $null
foreach ($candidate in @('py -3', 'python', 'python3')) {
  $exe, $arg = $candidate.Split(' ', 2)
  if (Get-Command $exe -ErrorAction SilentlyContinue) {
    $check = "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
    if ($arg) { & $exe $arg -c $check } else { & $exe -c $check }
    if ($LASTEXITCODE -eq 0) { $script:PyExe = $exe; $script:PyArg = $arg; break }
  }
}
if (-not $script:PyExe) {
  Fail "Python 3.10 or newer is required. Install it from python.org, then run this again."
}

$version = if ($script:PyArg) { & $script:PyExe $script:PyArg --version } else { & $script:PyExe --version }
Say "python        $version"

Wait-Unused $Venv
if (Test-Environment $Venv) { Update-Environment $Venv } else { New-Environment $Venv }
if (-not (Test-Environment $Venv)) {
  Say "environment   did not come out usable - rebuilding it from scratch"
  Remove-Item -Recurse -Force $Venv -ErrorAction SilentlyContinue
  New-Environment $Venv
  if (-not (Test-Environment $Venv)) {
    Fail ("Mindboard installed but will not run. Delete the '$Venv' folder, then run " +
          "this again. If it still fails, the errors above say why.")
  }
}
Say "packages      installed and verified"

$venvMb = Join-Path $Venv 'Scripts\mindboard.exe'
Write-Host ""
& $venvMb install

Write-Host ""
Write-Host "  Run it with" -ForegroundColor White
Write-Host "    $venvMb run"
Write-Host "  or just double-click Mindboard.bat"
Write-Host ""
