#!/usr/bin/env bash
# 在 VM2 自行建置 UERANSIM（5G UE 與 gNB 模擬器）。
#
# 版本鎖定於 deploy/versions.env。與 free5GC 一樣：元件用上游的，
# 建置與設定自己來。
#
# 用法：
#   bash deploy/install_ueransim.sh            # 完整安裝
#   bash deploy/install_ueransim.sh --check    # 只檢查現況
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
source "${ROOT}/deploy/versions.env"

ok()   { echo "  [OK]   $*"; }
info() { echo "  [..]   $*"; }
warn() { echo "  [WARN] $*"; }
die()  { echo "  [FAIL] $*" >&2; exit 1; }

SRC="${HOME}/UERANSIM"       # 與教材的 ~/UERANSIM 路徑一致

if [[ "${1:-}" == "--check" ]]; then
  for b in nr-gnb nr-ue nr-cli; do
    [[ -x "${SRC}/build/${b}" ]] && ok "build/${b} 就緒" || warn "缺 build/${b}"
  done
  exit 0
fi

echo "=== 1. 建置相依 ==="
# UERANSIM 需要 SCTP（NGAP 走 SCTP）與 cmake
DEPS=(make gcc g++ cmake libsctp-dev lksctp-tools iproute2 git)
NEED=()
for p in "${DEPS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || NEED+=("$p"); done
if [[ ${#NEED[@]} -gt 0 ]]; then
  info "安裝：${NEED[*]}"
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${NEED[@]}"
fi
ok "相依就緒（含 libsctp-dev，NGAP 走 SCTP 必需）"

echo
echo "=== 2. 取得原始碼（${UERANSIM_VERSION}）==="
if [[ -d "${SRC}/.git" ]]; then
  git -C "$SRC" fetch --tags --quiet
  git -C "$SRC" checkout --quiet "$UERANSIM_VERSION"
  ok "已切換到 ${UERANSIM_VERSION}"
else
  git clone --quiet --branch "$UERANSIM_VERSION" --depth 1 "$UERANSIM_REPO" "$SRC"
  ok "已 clone ${UERANSIM_VERSION}"
fi
echo "  commit $(git -C "$SRC" rev-parse --short HEAD)"

echo
echo "=== 3. 編譯（C++，需數分鐘）==="
cd "$SRC"
if make -j"$(nproc)" 2>&1 | tail -8; then
  ok "編譯完成"
else
  die "編譯失敗，見上方輸出"
fi

echo
echo "=== 4. 產物 ==="
for b in nr-gnb nr-ue nr-cli nr-binder; do
  [[ -x "${SRC}/build/${b}" ]] && ok "build/${b}" || warn "缺 build/${b}"
done
"${SRC}/build/nr-gnb" --version 2>&1 | head -2 || true

echo
echo "完成。設定檔在 ${SRC}/config/：free5gc-gnb.yaml、free5gc-ue.yaml"
echo "啟動（各佔一個終端機）："
echo "  sudo ${SRC}/build/nr-gnb -c ${SRC}/config/free5gc-gnb.yaml"
echo "  sudo ${SRC}/build/nr-ue  -c ${SRC}/config/free5gc-ue.yaml"
