#!/usr/bin/env bash
# 在 VM1 建置並安裝 gtp5g（free5GC 的 GTP-U kernel module）。
#
# 這是整條建置鏈風險最高的一步：gtp5g 是 out-of-tree kernel module，
# 對核心版本敏感。先做這一步，編得過才值得往下走。
#
# 官方支援 kernel 5.4–7.0.x（github.com/free5gc/gtp5g，115/09/22 查閱）。
#
# 用法（在 VM1 上）：
#   bash deploy/install_gtp5g.sh            # 建置並安裝
#   bash deploy/install_gtp5g.sh --check    # 只檢查現況
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
source "${ROOT}/deploy/versions.env"

ok()   { echo "  [OK]   $*"; }
info() { echo "  [..]   $*"; }
warn() { echo "  [WARN] $*"; }
die()  { echo "  [FAIL] $*" >&2; exit 1; }

SRC="${HOME}/src/gtp5g"

echo "=== 環境 ==="
KVER=$(uname -r)
echo "  kernel        ${KVER}"
echo "  gtp5g 目標版本 ${GTP5G_VERSION}"
[[ -d "/usr/src/linux-headers-${KVER}" ]] \
  && ok "kernel headers 就緒" \
  || die "缺 /usr/src/linux-headers-${KVER}，請先 apt install linux-headers-\$(uname -r)"

if lsmod | grep -q '^gtp5g'; then
  CUR=$(cat /sys/module/gtp5g/version 2>/dev/null || echo "未知")
  ok "gtp5g 已載入（版本 ${CUR}）"
  [[ "${1:-}" == "--check" ]] && exit 0
else
  [[ "${1:-}" == "--check" ]] && { warn "gtp5g 未載入"; exit 1; }
fi

echo
echo "=== 1. 建置相依 ==="
NEED=()
for p in build-essential "linux-headers-${KVER}" git; do
  dpkg -s "$p" >/dev/null 2>&1 || NEED+=("$p")
done
if [[ ${#NEED[@]} -gt 0 ]]; then
  info "安裝：${NEED[*]}"
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${NEED[@]}"
fi
ok "相依就緒"

echo
echo "=== 2. 取得原始碼（${GTP5G_VERSION}）==="
mkdir -p "$(dirname "$SRC")"
if [[ -d "${SRC}/.git" ]]; then
  git -C "$SRC" fetch --tags --quiet
  git -C "$SRC" checkout --quiet "$GTP5G_VERSION"
  ok "已切換到 ${GTP5G_VERSION}"
else
  git clone --quiet --branch "$GTP5G_VERSION" --depth 1 "$GTP5G_REPO" "$SRC"
  ok "已 clone ${GTP5G_VERSION}"
fi
echo "  commit $(git -C "$SRC" rev-parse --short HEAD)"

echo
echo "=== 3. 編譯 ==="
make -C "$SRC" clean >/dev/null 2>&1 || true
if make -C "$SRC" 2>&1 | tail -5; then
  [[ -f "${SRC}/gtp5g.ko" ]] || die "編譯結束但找不到 gtp5g.ko"
  ok "編譯成功：$(ls -lh "${SRC}/gtp5g.ko" | awk '{print $5}')"
else
  die "編譯失敗。若為核心 API 不相容，改試較新的 GTP5G_VERSION（例如 v0.10.2）"
fi

echo
echo "=== 4. 安裝並載入 ==="
sudo rmmod gtp5g 2>/dev/null || true
sudo make -C "$SRC" install >/dev/null 2>&1 || die "make install 失敗"
sudo modprobe gtp5g 2>/dev/null || sudo insmod "${SRC}/gtp5g.ko" 2>/dev/null || true
lsmod | grep -q '^gtp5g' || die "模組安裝後仍未載入"
ok "已載入：$(lsmod | grep '^gtp5g')"
ok "模組版本：$(cat /sys/module/gtp5g/version 2>/dev/null || echo 未知)"

echo
echo "=== 5. 開機自動載入 ==="
echo gtp5g | sudo tee /etc/modules-load.d/gtp5g.conf >/dev/null
ok "已寫入 /etc/modules-load.d/gtp5g.conf"

echo
echo "完成。驗證：lsmod | grep gtp5g"
