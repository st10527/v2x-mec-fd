#!/usr/bin/env bash
# 跑完整測試套件。改動 mec_app/ 或 training/ 之後都該跑一次。
#
# 全部不需要 SUMO、不需要 Kong、不需要 free5GC，在開發機上幾十秒跑完。
# 需要 SUMO 的只有 build_dataset 的 collect 子命令與 UE 的即時模式，
# 那兩者在 VM2 上驗證。
set -euo pipefail
cd "$(dirname "$0")/.."
PY="./.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

fail=0
for t in tests/test_core.py tests/test_build_dataset.py \
         tests/test_app.py tests/test_pipeline.py tests/test_integration.py \
         tests/test_student_guide.py tests/test_lead_time.py tests/test_tune_fd.py tests/test_events.py; do
  echo "=============================================================="
  echo ">>> $t"
  if ! "$PY" "$t" 2>&1 | tail -n 4; then fail=1; fi
done
echo "=============================================================="
[[ $fail -eq 0 ]] && echo "全部測試通過" || { echo "有測試失敗"; exit 1; }
