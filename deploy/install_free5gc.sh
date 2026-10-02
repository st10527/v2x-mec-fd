#!/usr/bin/env bash
# 在 VM1 自行建置 free5GC 核心網路（不依賴培訓營映像檔）。
#
# 版本鎖定於 deploy/versions.env。元件本身仍是競賽平台指定的上游專案，
# 但建置、設定與版本控制都在我們手上——決賽須繳交完整程式碼供評審辨識
# 原創性，「能從零重現」本身就是證據。
#
# 前置：需先跑 install_gtp5g.sh（UPF 依賴 gtp5g kernel module）與 install_go.sh。
#
# 用法：
#   bash deploy/install_free5gc.sh            # 完整安裝
#   bash deploy/install_free5gc.sh --check    # 只檢查現況
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
source "${ROOT}/deploy/versions.env"

ok()   { echo "  [OK]   $*"; }
info() { echo "  [..]   $*"; }
warn() { echo "  [WARN] $*"; }
die()  { echo "  [FAIL] $*" >&2; exit 1; }

SRC="${HOME}/free5gc"          # 與教材的 ~/free5gc 路徑一致，指令可共用
export PATH="$PATH:/usr/local/go/bin:$HOME/go/bin"

echo "=== 0. 前置檢查 ==="
lsmod | grep -q '^gtp5g' \
  && ok "gtp5g 已載入（$(cat /sys/module/gtp5g/version 2>/dev/null))" \
  || die "gtp5g 未載入，請先執行 deploy/install_gtp5g.sh"
command -v go >/dev/null && ok "$(go version)" \
  || die "找不到 go，請先執行 deploy/install_go.sh"

if [[ "${1:-}" == "--check" ]]; then
  [[ -x "${SRC}/bin/upf" || -x "${SRC}/NFs/upf/build/bin/upf" ]] \
    && ok "free5GC 已建置於 ${SRC}" || warn "free5GC 尚未建置"
  systemctl is-active --quiet mongod && ok "mongod 執行中" || warn "mongod 未執行"
  exit 0
fi

echo
echo "=== 1. 建置相依 ==="
DEPS=(git gcc g++ cmake autoconf libtool pkg-config libmnl-dev libyaml-dev
      wget curl gnupg ca-certificates)
NEED=()
for p in "${DEPS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || NEED+=("$p"); done
if [[ ${#NEED[@]} -gt 0 ]]; then
  info "安裝：${NEED[*]}"
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${NEED[@]}"
fi
ok "相依就緒"

echo
echo "=== 2. MongoDB ${MONGODB_VERSION} ==="
if command -v mongod >/dev/null; then
  ok "已安裝：$(mongod --version | head -1)"
else
  info "加入 MongoDB 上游 repo（Ubuntu 22.04 官方庫已不含 mongodb）"
  curl -fsSL "https://pgp.mongodb.com/server-${MONGODB_VERSION}.asc" \
    | sudo gpg -o "/usr/share/keyrings/mongodb-server-${MONGODB_VERSION}.gpg" --dearmor --yes
  echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-${MONGODB_VERSION}.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/${MONGODB_VERSION} multiverse" \
    | sudo tee "/etc/apt/sources.list.d/mongodb-org-${MONGODB_VERSION}.list" >/dev/null
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mongodb-org
  ok "已安裝：$(mongod --version | head -1)"
fi
sudo systemctl enable --now mongod >/dev/null 2>&1 || true
systemctl is-active --quiet mongod && ok "mongod 執行中" || die "mongod 啟動失敗"

echo
echo "=== 3. 取得 free5GC 原始碼（${FREE5GC_VERSION}）==="
if [[ -d "${SRC}/.git" ]]; then
  git -C "$SRC" fetch --tags --quiet
  git -C "$SRC" checkout --quiet "$FREE5GC_VERSION"
  git -C "$SRC" submodule update --init --recursive --quiet
  ok "已切換到 ${FREE5GC_VERSION}"
else
  git clone --quiet --recursive --branch "$FREE5GC_VERSION" "$FREE5GC_REPO" "$SRC"
  ok "已 clone ${FREE5GC_VERSION}"
fi
echo "  commit $(git -C "$SRC" rev-parse --short HEAD)"

echo
echo "=== 4. 編譯（Go 編十餘個 NF，需數分鐘）==="
cd "$SRC"
if make 2>&1 | tail -15; then
  ok "編譯完成"
else
  die "編譯失敗，見上方輸出"
fi

echo
echo "=== 5. 產物 ==="
if [[ -d "${SRC}/bin" ]]; then
  ls -1 "${SRC}/bin" | tr '\n' ' '; echo
  ok "$(ls -1 "${SRC}/bin" | wc -l) 個 NF 執行檔"
else
  warn "找不到 ${SRC}/bin"
fi

echo
echo "完成。下一步：設定 UE 用戶資料與 N6/ULCL，再啟動核網"
echo "  cd ${SRC} && sudo ./run.sh"
