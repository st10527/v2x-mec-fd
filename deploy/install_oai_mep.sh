#!/usr/bin/env bash
# 在 VM1 部署 OAI-MEP（MEC Platform）。
#
# OAI-MEP 提供兩項本專案賴以成立的平台能力（SPEC §2 Q6、§3.2）：
#   * MEP Gateway（Kong）—— UE 的統一入口，依路徑分流到各 MEC App
#   * Discovery and Service Registry —— MEC App A/B 互相發現 fd-service
# 這是 ETSI GS MEC 003 / 011 定義的能力，也是初賽「競賽平台關聯性」的落點。
# 因此元件用上游的，不自己實作（見 SPEC §15.7）。
#
# docker-compose 拉的是預建映像（kong:3.0-alpine、postgres:9.6、mongo、
# oaisoftwarealliance/oai-mep），不需自行編譯。
#
# 用法：
#   bash deploy/install_oai_mep.sh            # 安裝並啟動
#   bash deploy/install_oai_mep.sh --check    # 只檢查現況
#   bash deploy/install_oai_mep.sh --down     # 停掉
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
source "${ROOT}/deploy/versions.env"

ok()   { echo "  [OK]   $*"; }
info() { echo "  [..]   $*"; }
warn() { echo "  [WARN] $*"; }
die()  { echo "  [FAIL] $*" >&2; exit 1; }

SRC="${HOME}/oai-mep"        # 與教材的 ~/oai-mep 路徑一致
COMPOSE="ci-scripts/docker-compose.yaml"

show_state() {
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null | head -8 || true
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -q oai-mep-gateway; then
    echo
    echo "  Kong Admin host port : $(docker port oai-mep-gateway 8001/tcp | head -n1 | sed 's/.*://')"
    echo "  Kong Proxy host port : $(docker port oai-mep-gateway 80/tcp 2>/dev/null | head -n1 | sed 's/.*://')"
    echo "  Kong container IP    : $(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' oai-mep-gateway)"
  fi
}

case "${1:-install}" in
  --check)
    command -v docker >/dev/null && ok "docker $(docker --version | awk '{print $3}' | tr -d ,)" \
      || { warn "docker 未安裝"; exit 1; }
    [[ -d "$SRC" ]] && ok "${SRC} 存在" || warn "${SRC} 不存在"
    show_state; exit 0 ;;
  --down)
    [[ -d "$SRC" ]] || die "${SRC} 不存在"
    cd "$SRC" && docker compose -f "$COMPOSE" down
    ok "已停止"; exit 0 ;;
esac

echo "=== 1. Docker ==="
if command -v docker >/dev/null; then
  ok "已安裝：$(docker --version)"
else
  info "從 Docker 官方 repo 安裝（Ubuntu 內建的 docker.io 版本較舊）"
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
       ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
       docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  ok "已安裝：$(docker --version)"
fi
sudo systemctl enable --now docker >/dev/null 2>&1 || true
sudo usermod -aG docker "$USER" 2>/dev/null || true
ok "已將 ${USER} 加入 docker 群組（需重新登入生效；本腳本其餘步驟用 sudo）"

echo
echo "=== 2. 取得 OAI-MEP ==="
if [[ -d "${SRC}/.git" ]]; then
  ok "${SRC} 已存在"
else
  git clone --quiet --branch "$OAI_MEP_REF" "$OAI_MEP_REPO" "$SRC" \
    || git clone --quiet "$OAI_MEP_REPO" "$SRC"
  ok "已 clone 到 ${SRC}"
fi
echo "  commit $(git -C "$SRC" rev-parse --short HEAD)"
[[ -f "${SRC}/${COMPOSE}" ]] || die "找不到 ${SRC}/${COMPOSE}"

echo
echo "=== 3. 啟動（首次會拉映像，需數分鐘）==="
cd "$SRC"
sudo docker compose -f "$COMPOSE" up -d 2>&1 | tail -12
ok "compose up 完成"

echo
echo "=== 4. 等待 container 健康 ==="
for i in $(seq 1 40); do
  if sudo docker ps --format '{{.Names}}\t{{.Status}}' | grep oai-mep-gateway | grep -q healthy; then
    ok "oai-mep-gateway healthy"; break
  fi
  [[ $i -eq 40 ]] && warn "等待逾時，請用 --check 再看"
  sleep 5
done

echo
echo "=== 5. 現況 ==="
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | head -8
echo
GW_ADMIN=$(sudo docker port oai-mep-gateway 8001/tcp 2>/dev/null | head -n1 | sed 's/.*://')
GW_IP=$(sudo docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' oai-mep-gateway 2>/dev/null)
echo "  Kong Admin host port : ${GW_ADMIN:-取不到}"
echo "  Kong container IP    : ${GW_IP:-取不到}"
echo
echo "請把上面兩個值填進 docs/platform-notes.md §3（每次重建 container 都會變）"
echo "下一步：bash deploy/kong_routes.sh   # 建立 SPEC §3.3 的六條路由"
