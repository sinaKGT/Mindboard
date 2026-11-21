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
    uv venv --clear --python "$PY" "$VENV" >/dev/null
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

# The interface is built from web/ into src/mindboard/web/, which git does not
# carry (it is generated). So a fresh copy has none, and a pull can leave it
# behind the code. Build it when it is missing or older than web/'s files.
INTERFACE="src/mindboard/web/index.html"
interface_stale() {
  [ ! -f "$INTERFACE" ] && return 0
  [ -n "$(find web/src web/index.html web/package.json -newer "$INTERFACE" -print -quit 2>/dev/null)" ]
}
build_interface() {
  if ! interface_stale; then
    say "interface     up to date"
    return
  fi
  if ! command -v npm >/dev/null 2>&1; then
    if [ -f "$INTERFACE" ]; then
      say "interface     older than the code - install Node.js to rebuild it (brew install node)"
      return
    fi
    fail "The interface needs building, which needs Node.js. Install it (on a Mac: brew install node), then run this again."
  fi
  say "interface     building (a minute the first time)"
  ./build.sh >/dev/null 2>&1 || fail "The interface did not build. Run ./build.sh to see why."
  [ -f "$INTERFACE" ] || fail "The interface did not build. Run ./build.sh to see why."
  say "interface     built"
}

# A "mindboard" command in the terminal: plain "mindboard" starts the app,
# "mindboard <command>" runs that command. Offered once, only when someone is
# at the terminal to answer (MINDBOARD_SHORTCUT=yes|no answers for them), and
# pointed at this copy of Mindboard, wherever its folder is.
MARK_START='# >>> mindboard shortcut >>>'
MARK_END='# <<< mindboard shortcut <<<'
SHORTCUT=""
add_shortcut() {
  case "$(basename "${SHELL:-}")" in
    zsh)  RC="$HOME/.zshrc" ;;
    bash) if [ "$(uname)" = Darwin ]; then RC="$HOME/.bash_profile"; else RC="$HOME/.bashrc"; fi ;;
    *)    return 0 ;;
  esac
  case "$VENV" in
    /*) MB_BIN="$VENV/bin/mindboard" ;;
    *)  MB_BIN="$(pwd)/$VENV/bin/mindboard" ;;
  esac
  if [ -f "$RC" ] && grep -qF "$MARK_START" "$RC"; then
    if grep -qF "\"$MB_BIN\"" "$RC"; then
      say "shortcut      the mindboard command is already in $RC"
      SHORTCUT=yes
      return 0
    fi
    # it points at a copy that has moved: take the old one out, add it again
    sed -i.bak "/$MARK_START/,/$MARK_END/d" "$RC" && rm -f "$RC.bak"
    ANSWER=yes
  else
    ANSWER="${MINDBOARD_SHORTCUT:-}"
    if [ -z "$ANSWER" ]; then
      [ -t 0 ] && [ -t 1 ] || return 0
      printf '\n  Add a "mindboard" command to your terminal, to start Mindboard from anywhere? [Y/n] '
      read -r ANSWER || return 0
    fi
  fi
  case "$ANSWER" in
    [nN]*) say "shortcut      skipped - add it later by running this again"; return 0 ;;
  esac
  {
    printf '\n%s\n' "$MARK_START"
    printf '# "mindboard" starts the app; "mindboard <command>" runs that command\n'
    printf 'mindboard() {\n  if [ $# -eq 0 ]; then\n    "%s" run\n  else\n    "%s" "$@"\n  fi\n}\n' "$MB_BIN" "$MB_BIN"
    printf '%s\n' "$MARK_END"
  } >> "$RC"
  say "shortcut      added to $RC"
  SHORTCUT=yes
}

# An install can look successful and still be unusable - a stale or half-written
# environment leaves an empty `mindboard` folder in site-packages, and every
# submodule disappears. Prove it runs before saying it worked.
usable() { [ -x "$VENV/bin/mindboard" ] && "$VENV/bin/mindboard" version >/dev/null 2>&1; }

printf '\n\033[1m  Installing Mindboard\033[0m\n\n'

# A new enough Python. Homebrew installs it under its version - python3.12 -
# and a Mac's own python3 is 3.9, so the versioned names come first, and
# Homebrew's folders are looked in too, for a shell that does not have them
# on its PATH yet.
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python \
                 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
                 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10 \
                 /usr/local/bin/python3.13 /usr/local/bin/python3.12 \
                 /usr/local/bin/python3.11 /usr/local/bin/python3.10; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$(command -v "$candidate")"; break
    fi
  fi
done
[ -n "$PY" ] || fail "Python 3.10 or newer is required (a Mac's own python3 is 3.9). Install it - on a Mac: brew install python@3.12 - then run this again."
say "python        $($PY --version 2>&1)"

if usable; then update_environment; else new_environment; fi
if ! usable; then
  say "environment   did not come out usable - rebuilding it from scratch"
  rm -rf "$VENV"
  new_environment
  usable || fail "Mindboard installed but will not run. Delete '$VENV' and run this again."
fi
say "packages      installed and verified"
build_interface

printf '\n'
"$VENV/bin/mindboard" install || true
add_shortcut

printf '\n\033[1m  Run it with\033[0m\n'
if [ -n "$SHORTCUT" ]; then
  printf '    mindboard          (in a new terminal window)\n'
fi
printf '    %s run\n' "$VENV/bin/mindboard"
printf '    or just run ./Mindboard.command\n\n'
