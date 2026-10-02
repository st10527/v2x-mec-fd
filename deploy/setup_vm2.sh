#!/usr/bin/env bash
# VM2 環境設定：UERANSIM + SUMO + UE Client（SPEC §3.1）
#
# 指令來源：培訓營教材 p.55-57（UERANSIM gNB/UE 啟動與 uesimtun0 驗證）、
#           p.58-60（端到端 service chain 驗證）、p.63-65（SUMO 與平台範例）
# SPEC §15.7：教材指令直接沿用，不得改寫。
#
# 前提：本機須已備妥教材映像檔的 ~/UERANSIM（培訓營提供）與 SUMO 1.27.0。
#
# 用法：
#   bash deploy/setup_vm2.sh --check-only   # 只檢查
#   bash deploy/setup_vm2.sh                # 檢查 + 建 Python 環境 + 取平台範例
#   bash deploy/setup_vm2.sh --verify       # 端到端驗證（需 VM1 已就緒）
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
MODE="${1:-run}"
CHECK_ONLY=0; VERIFY=0
[[ "$MODE" == "--check-only" ]] && CHECK_ONLY=1
[[ "$MODE" == "--verify" ]] && VERIFY=1

ok()   { echo "  [OK]   $*"; }
warn() { echo "  [WARN] $*"; }
fail() { echo "  [FAIL] $*"; FAILED=1; }
FAILED=0

N6_APP="172.16.6.10"
N6_GW="172.16.6.100"
UE_IF="${UE_IF:-uesimtun0}"

echo "=== 1. SUMO（SPEC §4.2：1.27.0，勿升級）==="
if command -v sumo >/dev/null; then
  V=$(sumo --version 2>/dev/null | head -n1)
  ok "$V"
  echo "$V" | grep -q "1\.27\.0" || warn "版本非 1.27.0，SPEC §4.2 指定該版本"
  [[ -n "${SUMO_HOME:-}" ]] && ok "SUMO_HOME=${SUMO_HOME}" \
    || fail "SUMO_HOME 未設定，traci 會 import 失敗"
else
  fail "找不到 sumo（教材 p.63 以 sumo --version 確認，映像檔內應已安裝）"
fi

echo
echo "=== 2. UERANSIM（教材 p.55）==="
if [[ -d "$HOME/UERANSIM" ]]; then
  ok "~/UERANSIM 存在"
  for b in nr-gnb nr-ue; do
    [[ -x "$HOME/UERANSIM/build/$b" ]] && ok "build/$b 可執行" || fail "缺少 build/$b"
  done
else
  fail "~/UERANSIM 不存在 —— 教材指令假設培訓營映像檔已備妥"
fi
echo "  啟動方式（各佔一個終端機，本腳本不代勞）："
echo "    cd ~/UERANSIM && sudo ./build/nr-gnb -c config/free5gc-gnb.yaml"
echo "    cd ~/UERANSIM && sudo ./build/nr-ue  -c config/free5gc-ue.yaml"

echo
echo "=== 3. UE tun 介面（教材 p.57）==="
if command -v ip >/dev/null && ip a 2>/dev/null | grep -q "$UE_IF"; then
  ADDR=$(ip -4 addr show dev "$UE_IF" | grep -oE 'inet [0-9.]+' | awk '{print $2}')
  ok "$UE_IF = ${ADDR}（教材為 10.60.0.1/24）"
else
  warn "$UE_IF 尚未建立 —— nr-ue 還沒起來，或 UE 尚未註冊成功"
fi

echo
echo "=== 4. Python 環境 ==="
if [[ ! -d "${ROOT}/.venv" ]]; then
  if [[ $CHECK_ONLY -eq 1 ]]; then warn ".venv 不存在（--check-only 不建立）"; else
    python3 -m venv "${ROOT}/.venv"
    "${ROOT}/.venv/bin/pip" install -q --upgrade pip
    "${ROOT}/.venv/bin/pip" install -q -r "${ROOT}/requirements.txt"
    ok "已建立 .venv 並安裝相依"
  fi
else
  ok ".venv 已存在"
fi

echo
echo "=== 5. 平台範例 repo（教材 p.64，SPEC §1.3 的比較基準）==="
REF="${ROOT}/reference/SUMO-V2X-TTC"
if [[ -d "$REF" ]]; then
  ok "已存在 ${REF}"
elif [[ $CHECK_ONLY -eq 1 ]]; then
  warn "尚未取得（--check-only 不下載）"
else
  mkdir -p "${ROOT}/reference"
  git clone https://github.com/KevinTseng-0430/SUMO-V2X-TTC.git "$REF" \
    && ok "已取得平台範例" || warn "clone 失敗，可稍後手動取得"
fi
echo "  範例的 MEC 模式跑法（教材 p.65）："
echo "    python3 sumo_ue_sender.py --mode mec \\"
echo "      --mec-url http://${N6_GW}/mec/v2x/sumo/report \\"
echo "      --ue-interface ${UE_IF} --max-inflight 8 --gui --realtime"
echo "  Cloud 對照（教材 p.69）：--mode wan --mec-url http://140.120.108.48:18080/mec/v2x/sumo/report"

if [[ $VERIFY -eq 1 ]]; then
  echo
  echo "=== 6. 端到端驗證（教材 p.58）==="
  echo "  驗證 1：UE 直打 MEC App"
  curl -m 3 -s -o /dev/null -w "    ${N6_APP}:5002/healthz -> %{http_code}\n" \
       --interface "$UE_IF" "http://${N6_APP}:5002/healthz" || echo "    失敗"
  echo "  驗證 2：UE 經 MEP Gateway 到 MEC App"
  for n in a b; do
    curl -m 3 -s -o /dev/null -w "    ${N6_GW}/mec/v2x/${n}/healthz -> %{http_code}\n" \
         --interface "$UE_IF" "http://${N6_GW}/mec/v2x/${n}/healthz" || echo "    失敗"
  done
  echo "  成功路徑（教材 p.60）："
  echo "    UE -> ${N6_GW}:80/mec/... -> iptables DNAT -> Kong:80 -> MEC App (${N6_APP}:500x)"
fi

echo
echo "=== 下一步 ==="
echo "  .venv/bin/python -m training.build_dataset collect --scenario a"
echo "  .venv/bin/python ue/sumo_ue_sender.py --intersection A --interface ${UE_IF}"
[[ $FAILED -eq 0 ]] && { echo; echo "檢查通過"; } || { echo; echo "有項目未通過，見上方 [FAIL]"; exit 1; }
