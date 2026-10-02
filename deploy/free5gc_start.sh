#!/usr/bin/env bash
# 啟動 free5GC 的所有 NF，供 systemd 當主行程使用。
#
# 為什麼不直接用 free5GC 附的 run.sh：
#   它結尾是 `wait ${PID_LIST}`，陣列少了 [@]，只會等到第一個 PID。
#   那個行程一結束，run.sh 就 exit 0，systemd 認定服務停止並把其餘 NF
#   全部 SIGKILL 掉。症狀是核網看似起來了幾秒又整組消失，
#   UE 端只看到 Registration Reject [CONGESTION]，很難往回追。
#
#   本腳本修正該問題（wait "${PIDS[@]}"），並加上：
#   * SIGTERM/SIGINT 時主動收掉所有子行程，systemd stop 才乾淨
#   * 任一 NF 意外結束就整組退出，讓 systemd 的 Restart 接手，
#     而不是留下半死不活的核網
#
# NF 清單與啟動順序沿用上游 run.sh（NRF 必須最先，其餘才註冊得上）。
#
# 用法：bash deploy/free5gc_start.sh
set -uo pipefail

F5GC="${F5GC_DIR:-${HOME}/free5gc}"
NF_LIST="${NF_LIST:-nrf amf smf udr pcf udm nssf ausf chf nef}"
LOG_DIR="${F5GC}/log/$(date +%Y%m%d_%H%M%S)"

cd "$F5GC" || { echo "[FAIL] 找不到 ${F5GC}" >&2; exit 1; }
mkdir -p "$LOG_DIR"

PIDS=()

cleanup() {
  echo "[..] 收到停止訊號，關閉所有 NF"
  for p in "${PIDS[@]}"; do kill -TERM "$p" 2>/dev/null || true; done
  sleep 2
  for p in "${PIDS[@]}"; do kill -KILL "$p" 2>/dev/null || true; done
  # sudo 起的 UPF 是孫行程，要另外清
  pkill -KILL -f './bin/upf' 2>/dev/null || true
  # 順手移除 gtp5g 的 link，否則下次啟動 UPF 會報 "file exists"
  ip link del upfgtp 2>/dev/null || true
  exit 0
}
trap cleanup TERM INT

# 清掉上一輪殘留。這兩步都是重啟時必踩的坑：
#
# 1. 殘留的 NF 行程會佔住 127.0.0.x:8000 等 port，新的一組報
#    "address already in use"，UE 端只看到 Registration Reject [CONGESTION]。
# 2. 殘留的 upfgtp 介面會讓 UPF 報
#    "open Gtp5g: open link: create: file exists" 而無法啟動——
#    gtp5g 的 link 由 UPF 建立，UPF 非正常結束時不會自己清掉。
echo "[..] 清理上一輪殘留"
pkill -TERM -f './bin/(amf|smf|upf|nrf|udm|udr|ausf|pcf|nssf|chf|nef)' 2>/dev/null || true
sleep 1
pkill -KILL -f './bin/(amf|smf|upf|nrf|udm|udr|ausf|pcf|nssf|chf|nef)' 2>/dev/null || true
ip link del upfgtp 2>/dev/null && echo "[..] 已移除殘留的 upfgtp 介面" || true

# UPF 需要 root 才能操作 gtp5g 的 netlink 介面
echo "[..] 啟動 upf"
sudo -E ./bin/upf -c ./config/upfcfg.yaml -l "${LOG_DIR}/free5gc.log" &
PIDS+=($!)
sleep 1

for NF in ${NF_LIST}; do
  CFG="./config/${NF}cfg.yaml"
  [[ -f "$CFG" ]] || { echo "[WARN] 略過 ${NF}（找不到 ${CFG}）"; continue; }
  echo "[..] 啟動 ${NF}"
  ./bin/"${NF}" -c "$CFG" -l "${LOG_DIR}/free5gc.log" &
  PIDS+=($!)
  sleep 0.3
done

echo "[OK] 已啟動 ${#PIDS[@]} 個行程，log 位於 ${LOG_DIR}"

# 任一 NF 結束就整組退出，交給 systemd 重啟。
# 半死不活的核網比整組重啟難查得多。
wait -n "${PIDS[@]}"
echo "[WARN] 有 NF 結束，關閉其餘行程"
cleanup
