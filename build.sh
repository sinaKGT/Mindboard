#!/usr/bin/env bash
# Builds the interface into the Python package, so the wheel ships it prebuilt.
set -euo pipefail
cd "$(dirname "$0")/web"
npm install --no-audit --no-fund
npx tsc --noEmit
npx vite build
echo "built into src/mindboard/web/"
