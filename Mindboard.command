#!/usr/bin/env bash
# Double-click this file to use Mindboard.
# First run sets everything up; after that it just opens the menu.
cd "$(dirname "$0")"

VENV="${MINDBOARD_VENV:-.venv}"
MB="$VENV/bin/mindboard"

if [ ! -x "$MB" ]; then
  printf '\n  Setting Mindboard up for the first time. This takes a minute.\n\n'
  ./install.sh || true
fi

if [ ! -x "$MB" ]; then
  printf '\n  Setup did not finish - the messages above say why.\n\n'
  read -r -p "  press Enter to close "
  exit 1
fi

"$MB" menu || {
  printf '\n  Mindboard closed with an error.\n'
  read -r -p "  press Enter to close "
}
