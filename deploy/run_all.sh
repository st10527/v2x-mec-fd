#!/usr/bin/env bash
# 一鍵啟動整套系統（SPEC §9 的約束：重開機後一行指令還原）。
#
# SPEC §13.4 要求本腳本能在 10 分鐘內從冷開機還原至可展示狀態，
# 並在 12/11 場布時完整演練一次。
#
# 用法：
#   bash deploy/run_all.sh            # 啟動兩個 MEC App 並建立 Kong 路由
#   bash deploy/run_all.sh --stop     # 停掉
#   bash deploy/run_all.sh --status   # 看狀態
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
MEC_HOST="${MEC_HOST:-172.16.6.10}"
GW="${GW:-172.16.6.100}"
PID_DIR="${ROOT}/logs/.pids"
mkdir -p "$PID_DIR" "${ROOT}/logs"

start_node() {
  local node="$1" port="$2"
  local pid_file="${PID_DIR}/mec_${node}.pid"
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "  節點 ${node^^} 已在執行（pid $(cat "$pid_file")）"; return
  fi
  MEC_NODE="$node" nohup "$PY" -m uvicorn mec_app.app:app \
      --host "$MEC_HOST" --port "$port" --log-level info \
      > "${ROOT}/logs/mec_${node}.out" 2>&1 &
  echo $! > "$pid_file"
  echo "  節點 ${node^^} 啟動於 ${MEC_HOST}:${port}（pid $!）"
}

case "${1:-start}" in
  --stop)
    for f in "$PID_DIR"/*.pid; do
      [[ -e "$f" ]] || continue
      pid=$(cat "$f"); kill "$pid" 2>/dev/null && echo "  已停止 pid $pid"
      rm -f "$f"
    done
    ;;
  --status)
    for node in a b; do
      printf "節點 %s：" "${node^^}"
      curl -s --max-time 3 "http://${GW}/mec/v2x/${node}/healthz" || echo " 無回應"
      echo
      curl -s --max-time 3 "http://${GW}/mec/fd/${node}/status" | head -c 400 || true
      echo; echo
    done
    ;;
  *)
    echo "=== 1/3 啟動 MEC App ==="
    start_node a 5002
    start_node b 5003
    sleep 2

    echo "=== 2/3 建立 Kong 路由（SPEC §3.3）==="
    bash "${ROOT}/deploy/kong_routes.sh"

    echo "=== 3/3 驗證 ==="
    for node in a b; do
      printf "  /mec/v2x/%s/healthz -> " "$node"
      curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 \
        "http://${GW}/mec/v2x/${node}/healthz" || echo "失敗"
    done
    echo
    echo "接著在 VM2 送資料："
    echo "  python ue/sumo_ue_sender.py --intersection A --interface uesimtun0"
    echo "  python ue/sumo_ue_sender.py --intersection B --interface uesimtun0"
    ;;
esac
