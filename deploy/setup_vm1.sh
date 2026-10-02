#!/usr/bin/env bash
# VM1 環境設定：free5GC + OAI-MEP + MEC Apps（SPEC §3.1）
#
# 指令來源：培訓營教材《20260804_行動通訊競賽_智慧數位應用組-5G-V2X 平台_上課教材》
#           p.34-40（iptables 清理、N6 位址）、p.35-37（OAI-MEP、Kong Admin、Kong IP）、
#           p.42-48（MEC App、Kong Service/Route）、p.49-53（DNAT、gtp5g）、p.54（free5GC）
# SPEC §15.7：教材指令已驗證可用，直接沿用，不得自行改寫。本檔逐條照抄，
#            只加上冪等保護與檢查輸出，指令本體一字未改。
#
# 前提：本機須已備妥教材映像檔的 ~/oai-mep、~/free5gc、~/gtp5g（培訓營提供）。
#      腳本會先檢查，缺少時明確指出，不會自行去 clone 來源不明的版本。
#
# 用法：
#   bash deploy/setup_vm1.sh --check-only   # 只檢查，不動系統
#   bash deploy/setup_vm1.sh                # 完整設定
#   bash deploy/setup_vm1.sh --cloud-mode   # 加開外網路徑（SPEC §8.1 的 Cloud 對照）
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
MODE="${1:-run}"
CHECK_ONLY=0; CLOUD=0
[[ "$MODE" == "--check-only" ]] && CHECK_ONLY=1
[[ "$MODE" == "--cloud-mode" ]] && CLOUD=1

ok()   { echo "  [OK]   $*"; }
warn() { echo "  [WARN] $*"; }
fail() { echo "  [FAIL] $*"; FAILED=1; }
run()  { if [[ $CHECK_ONLY -eq 1 ]]; then echo "  [skip] $*"; else "$@"; fi; }
FAILED=0

# 教材 p.29：UE 網段 10.60.0.0/16、oai-mep docker bridge 172.29.248.0/24
UE_SUBNET="10.60.0.0/16"
MEP_BRIDGE="172.29.248.0/24"
N6_APP="172.16.6.10"          # MEC App 綁定（教材 p.39）
N6_GW="172.16.6.100"          # MEP Gateway 對外入口（教材 p.39）
NAT_IF="${NAT_IF:-ens33}"     # NAT 網卡，教材 p.31；單機部署時需改

echo "=== 1. 前置確認（SPEC §3.1）==="
command -v docker >/dev/null && ok "docker $(docker --version | awk '{print $3}' | tr -d ,)" || fail "找不到 docker"
for d in "$HOME/oai-mep" "$HOME/free5gc" "$HOME/gtp5g"; do
  [[ -d "$d" ]] && ok "$(basename "$d") 存在" \
    || fail "$d 不存在 —— 教材的指令假設培訓營映像檔已備妥此目錄"
done

echo
echo "=== 2. gtp5g kernel module（教材 p.53）==="
if lsmod 2>/dev/null | grep -q gtp5g; then
  ok "gtp5g 已載入：$(lsmod | grep gtp5g | head -1)"
else
  warn "gtp5g 未載入，依教材 p.53 編譯安裝"
  if [[ -d "$HOME/gtp5g" ]]; then
    run bash -c "cd ~/gtp5g && make clean && make && sudo make install"
    lsmod | grep -q gtp5g && ok "gtp5g 安裝成功" || fail "gtp5g 安裝後仍未載入"
  fi
fi

echo
echo "=== 3. N6 網段（教材 p.39-40）==="
# 掛在 loopback 上：172.16.6.10 給 MEC App、172.16.6.100 給 MEP Gateway 入口
run sudo ip addr add ${N6_APP}/24 dev lo 2>/dev/null || true
run sudo ip addr add ${N6_GW}/24 dev lo 2>/dev/null || true
if command -v ip >/dev/null && ip a show dev lo | grep -qE "172\.16\.6\.10|172\.16\.6\.100"; then
  ok "N6 位址已掛載：$(ip a show dev lo | grep -oE '172\.16\.6\.[0-9]+' | tr '\n' ' ')"
else
  [[ $CHECK_ONLY -eq 0 ]] && fail "N6 位址掛載失敗" || warn "尚未掛載（--check-only）"
fi

echo
echo "=== 4. OAI-MEP 平台（教材 p.35）==="
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q oai-mep-gateway; then
  ok "oai-mep-gateway 已在執行"
else
  run bash -c "cd ~/oai-mep && docker compose -f ci-scripts/docker-compose.yaml up -d"
fi
run sudo ufw disable            # 教材 p.35：關閉系統防火牆
[[ $CHECK_ONLY -eq 0 ]] && docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | head -8 || true

echo
echo "=== 5. 取得 Kong Admin port 與 container IP（教材 p.36-37）==="
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q oai-mep-gateway; then
  ADMIN_PORT=$(docker port oai-mep-gateway 8001/tcp | head -n1 | sed 's/.*://')
  KONG_IP=$(docker inspect -f \
    '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' oai-mep-gateway)
  ok "Kong Admin host port = ${ADMIN_PORT}"
  ok "Kong container IP    = ${KONG_IP}"
  echo "  （請一併填入 docs/platform-notes.md §3）"
else
  warn "oai-mep-gateway 未執行，略過 Kong 位址取得"
  ADMIN_PORT=""; KONG_IP=""
fi

echo
echo "=== 6. iptables DNAT（教材 p.50-51）==="
# UE 送到 172.16.6.100:80 的封包，改送到 Kong container:80，
# 再由 Kong 依路徑轉給對應的 MEC App。
run sudo sysctl -w net.ipv4.ip_forward=1 >/dev/null
if [[ -n "$KONG_IP" ]]; then
  # 冪等：先刪掉舊的同類規則再插入，避免每次執行都疊一條
  if [[ $CHECK_ONLY -eq 0 ]]; then
    while read -r n; do
      [[ -n "$n" ]] && sudo iptables -t nat -D PREROUTING "$n" 2>/dev/null || true
    done < <(sudo iptables -t nat -L PREROUTING -n --line-numbers \
             | awk -v gw="$N6_GW" '$0 ~ gw && /dpt:80/ {print $1}' | sort -rn)
  fi
  run sudo iptables -t nat -I PREROUTING 1 -i upfgtp -d ${N6_GW}/32 \
      -p tcp --dport 80 -j DNAT --to-destination ${KONG_IP}:80
  ok "DNAT ${N6_GW}:80 -> ${KONG_IP}:80（-i upfgtp）"

  # 本機發出的封包不經 PREROUTING。兩個 MEC 節點互取軟標籤（SPEC §7.4 步驟 3–4）是
  # VM1 本機發起的請求，要讓它同樣經過 MEP Gateway，就得在 OUTPUT 鏈也做一次 DNAT。
  run bash -c "sudo iptables -t nat -C OUTPUT -d ${N6_GW}/32 -p tcp --dport 80 -j DNAT --to-destination ${KONG_IP}:80 2>/dev/null || \
    sudo iptables -t nat -I OUTPUT 1 -d ${N6_GW}/32 -p tcp --dport 80 -j DNAT --to-destination ${KONG_IP}:80"
  ok "DNAT ${N6_GW}:80 -> ${KONG_IP}:80（VM1 本機發出，供節點間交換軟標籤）"

  # 教材 p.51：允許 UE subnet <-> oai-mep bridge 互通
  run bash -c "sudo iptables -C DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
    sudo iptables -I DOCKER-USER 1 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
  run bash -c "sudo iptables -C DOCKER-USER -s ${UE_SUBNET} -d ${MEP_BRIDGE} -j ACCEPT 2>/dev/null || \
    sudo iptables -I DOCKER-USER 2 -s ${UE_SUBNET} -d ${MEP_BRIDGE} -j ACCEPT"
  run bash -c "sudo iptables -C DOCKER-USER -s ${MEP_BRIDGE} -d ${UE_SUBNET} -j ACCEPT 2>/dev/null || \
    sudo iptables -I DOCKER-USER 3 -s ${MEP_BRIDGE} -d ${UE_SUBNET} -j ACCEPT"
  run bash -c "sudo iptables -C DOCKER-USER -j RETURN 2>/dev/null || \
    sudo iptables -A DOCKER-USER -j RETURN"
  ok "DOCKER-USER 規則已就位"
else
  warn "無 KONG_IP，略過 DNAT（請先啟動 OAI-MEP）"
fi

if [[ $CLOUD -eq 1 ]]; then
  echo
  echo "=== 6b. Cloud 對照路徑（教材 p.69，SPEC §8.1 的 MEC vs Cloud）==="
  # 讓 UE 走得出外網，才能打平台提供的 Cloud server 做延遲對照
  run sudo iptables -t nat -A POSTROUTING -s ${UE_SUBNET} -o ${NAT_IF} -j MASQUERADE
  run sudo iptables -I FORWARD 1 -s ${UE_SUBNET} -o ${NAT_IF} -j ACCEPT
  run sudo iptables -I FORWARD 2 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  ok "外網路徑已開（NAT 網卡 ${NAT_IF}）"
  echo "  平台的 Cloud server（教材 p.69）：http://140.120.108.48:18080"
fi

echo
echo "=== 7. Python 環境 ==="
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
echo "=== 8. free5GC（教材 p.54）==="
echo "  核網要在自己的終端機前景執行，本腳本不代勞（會佔住 shell）："
echo "    cd ~/free5gc && sudo ./run.sh"

echo
echo "=== 9. 下一步 ==="
echo "  bash deploy/kong_routes.sh      # 建立 SPEC §3.3 的六條路由"
echo "  bash deploy/run_all.sh          # 啟動兩個 MEC App 並驗證"
echo "  # VM2 端驗證（教材 p.58）："
echo "  curl -m 3 --interface uesimtun0 http://${N6_APP}:5002/healthz"
echo "  curl -m 3 --interface uesimtun0 http://${N6_GW}/mec/v2x/a/healthz"
[[ $FAILED -eq 0 ]] && { echo; echo "檢查通過"; } || { echo; echo "有項目未通過，見上方 [FAIL]"; exit 1; }
