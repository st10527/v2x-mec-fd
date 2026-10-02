#!/usr/bin/env bash
# 包裝：實際邏輯在 build_networks.py（Windows 沒有 bash，所以主體改用 Python）。
# 用法：bash sumo/build_networks.sh [場景名...]
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3
[[ -x .venv/bin/python ]] && PY=.venv/bin/python
exec "$PY" sumo/build_networks.py "$@"
