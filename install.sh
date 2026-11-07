#!/usr/bin/env bash
# Installs Mindboard into its own environment inside this folder.
# Nothing is installed system-wide and nothing leaves this machine.
set -euo pipefail
cd "$(dirname "$0")"

VENV="${MINDBOARD_VENV:-.venv}"
say()  { printf '  %s\n' "$1"; }
fail() { printf '\n  \033[31m%s\033[0m\n' "$1"; exit 1; }

# Only for a missing or broken environment: --clear says so, where newer uv
# would otherwise ask, and answer "replace" by itself.
new_environment() {
  if command -v uv >/dev/null 2>&1; then
    say "environment   uv -> $VENV"
    uv venv --clear "$VENV" >/dev/null
    uv pip install --quiet --python "$VENV/bin/python" -e .
  else
    say "environment   venv -> $VENV  (install uv for a faster setup)"
    rm -rf "$VENV"
    "$PY" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet -e .
  fi
}

# A working environment is updated, never replaced.
update_environment() {
  say "environment   updating $VENV"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --quiet --python "$VENV/bin/python" -e .
  else
    "$VENV/bin/python" -m pip install --quiet -e .
  fi
}

# An install can look successful and still be unusable - a stale or half-written
# environment leaves an empty `mindboard` folder in site-packages, and every
# submodule disappears. Prove it runs before saying it worked.
usable() { [ -x "$VENV/bin/mindboard" ] && "$VENV/bin/mindboard" version >/dev/null 2>&1; }

printf '\n\033[1m  Installing Mindboard\033[0m\n\n'

PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
      PY="$candidate"; break
    fi
  fi
done
[ -n "$PY" ] || fail "Python 3.10 or newer is required. Install it, then run this again."
say "python        $($PY --version 2>&1)"

if usable; then update_environment; else new_environment; fi
if ! usable; then
  say "environment   did not come out usable - rebuilding it from scratch"
  rm -rf "$VENV"
  new_environment
  usable || fail "Mindboard installed but will not run. Delete '$VENV' and run this again."
fi
say "packages      installed and verified"

printf '\n'
"$VENV/bin/mindboard" install || true

printf '\n\033[1m  Run it with\033[0m\n'
printf '    %s run\n' "$VENV/bin/mindboard"
printf '    or just run ./Mindboard.command\n\n'
