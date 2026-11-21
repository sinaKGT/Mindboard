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

# The interface is built from web\ into src\mindboard\web\, which git does not
# carry (it is generated), so a fresh copy has none and a pull can leave it
# behind the code. Built here when missing or older than web\'s files - an
# update used to keep the old one, as `mindboard install` builds only a
# missing one (DESIGN.md 74). Building needs Node.js; offered through winget.
$Interface = 'src\mindboard\web\index.html'

function Test-InterfaceStale {
  if (-not (Test-Path $Interface)) { return $true }
  $built = (Get-Item $Interface).LastWriteTime
  $newer = @(Get-ChildItem -Path 'web\src', 'web\index.html', 'web\package.json' -Recurse -File `
      -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt $built } | Select-Object -First 1)
  return ($newer.Count -gt 0)
}

function Find-Npm {
  $cmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  # just installed by winget: on disk, not yet on this window's PATH
  foreach ($dir in @("$env:ProgramFiles\nodejs", "${env:ProgramFiles(x86)}\nodejs", "$env:LOCALAPPDATA\Programs\nodejs")) {
    $npm = Join-Path $dir 'npm.cmd'
    if (Test-Path $npm) { $env:PATH = "$dir;$env:PATH"; return $npm }
  }
  return $null
}

# MINDBOARD_NODE=yes|no answers for someone not at the keyboard (an agent).
function Install-Node {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $false }
  $answer = $env:MINDBOARD_NODE
  if (-not $answer) {
    if (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected) { return $false }
    $answer = Read-Host "  Building the interface needs Node.js. Install it now with winget? [Y/n]"
  }
  if ($answer -match '^(n|no)$') { return $false }
  Say "node.js       installing with winget (a minute) ..."
  $ErrorActionPreference = 'Continue'
  winget install --id OpenJS.NodeJS.LTS -e --source winget --silent `
    --accept-package-agreements --accept-source-agreements | Out-Null
  return [bool](Find-Npm)
}

function Build-Interface {
  if (-not (Test-InterfaceStale)) { Say "interface     up to date"; return }
  $npm = Find-Npm
  if (-not $npm -and -not (Install-Node)) {
    if (Test-Path $Interface) {
      Say "interface     older than the code - install Node.js to rebuild it (winget install OpenJS.NodeJS.LTS)"
      return
    }
    Fail ("The interface needs building, which needs Node.js. Install it (winget install " +
          "OpenJS.NodeJS.LTS, or from nodejs.org), then run this again.")
  }
  $npm = Find-Npm
  Say "interface     building (a minute the first time)"
  # npm writes progress to stderr; under 'Stop' PowerShell 5.1 would take
  # that for a failure. The exit code says whether it worked.
  $ErrorActionPreference = 'Continue'
  Push-Location web
  try {
    $out = & $npm install --no-audit --no-fund 2>&1
    if ($LASTEXITCODE -eq 0) { $out = & $npm run build 2>&1 }
    $ok = ($LASTEXITCODE -eq 0)
  } finally { Pop-Location }
  # the build empties the output folder, placeholder too; put it back, or git
  # sees a changed file and `mindboard update` refuses to run
  $keep = 'src\mindboard\web\.gitkeep'
  if (-not (Test-Path $keep)) { New-Item -ItemType File -Path $keep | Out-Null }
  if (-not $ok -or -not (Test-Path $Interface)) {
    $out | Select-Object -Last 15 | ForEach-Object { Write-Host "    $_" }
    Fail "The interface did not build - the lines above say why."
  }
  Say "interface     built"
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
Build-Interface

$venvMb = Join-Path $Venv 'Scripts\mindboard.exe'
Write-Host ""
& $venvMb install

Write-Host ""
Write-Host "  Run it with" -ForegroundColor White
Write-Host "    $venvMb run"
Write-Host "  or just double-click Mindboard.bat"
Write-Host ""
