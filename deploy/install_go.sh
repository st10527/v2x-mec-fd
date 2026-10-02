#!/usr/bin/env bash
# 安裝 Go（free5GC v4.1.0 需要 1.24；Ubuntu 22.04 的 apt 只有 1.18）。
#
# checksum 從 go.dev 官方 JSON 即時取得再比對，不在 repo 裡寫死 hash——
# 寫死的 hash 一旦版本改動就會變成謊話。
#
# 用法：bash deploy/install_go.sh [--check]
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(pwd)/deploy/versions.env"

ok()   { echo "  [OK]   $*"; }
info() { echo "  [..]   $*"; }
die()  { echo "  [FAIL] $*" >&2; exit 1; }

TARBALL="go${GO_VERSION}.linux-amd64.tar.gz"
GOROOT="/usr/local/go"

if [[ -x "${GOROOT}/bin/go" ]]; then
  CUR=$("${GOROOT}/bin/go" version | awk '{print $3}' | sed 's/^go//')
  if [[ "$CUR" == "$GO_VERSION" ]]; then
    ok "Go ${GO_VERSION} 已安裝"
    [[ "${1:-}" == "--check" ]] && exit 0
    exit 0
  fi
  info "已安裝 Go ${CUR}，將換成 ${GO_VERSION}"
fi
[[ "${1:-}" == "--check" ]] && die "Go ${GO_VERSION} 未安裝"

echo "=== 1. 取得官方 checksum ==="
SUM=$(python3 - "$TARBALL" <<'GOJSON' 2>/dev/null || true
import json, sys, urllib.request
want = sys.argv[1]
data = json.load(urllib.request.urlopen(
    "https://go.dev/dl/?mode=json&include=all", timeout=30))
for rel in data:
    for f in rel.get("files", []):
        if f.get("filename") == want:
            print(f.get("sha256", ""))
            raise SystemExit
GOJSON
)
[[ -n "$SUM" ]] || die "取不到 ${TARBALL} 的官方 sha256，請確認 GO_VERSION 是否存在"
ok "官方 sha256 = ${SUM}"

echo
echo "=== 2. 下載 ==="
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
curl -fL --progress-bar -o "${TMP}/${TARBALL}" "${GO_URL_BASE}/${TARBALL}"
ACTUAL=$(sha256sum "${TMP}/${TARBALL}" | awk '{print $1}')
[[ "$ACTUAL" == "$SUM" ]] || die "checksum 不符：期望 ${SUM}，實際 ${ACTUAL}"
ok "checksum 比對通過"

echo
echo "=== 3. 安裝到 ${GOROOT} ==="
sudo rm -rf "$GOROOT"
sudo tar -C /usr/local -xzf "${TMP}/${TARBALL}"
ok "$("${GOROOT}/bin/go" version)"

echo
echo "=== 4. PATH ==="
echo 'export PATH=$PATH:/usr/local/go/bin:$HOME/go/bin' \
  | sudo tee /etc/profile.d/golang.sh >/dev/null
sudo chmod 644 /etc/profile.d/golang.sh
ok "已寫入 /etc/profile.d/golang.sh（新登入生效）"
echo
echo "本次 shell 立即生效：export PATH=\$PATH:/usr/local/go/bin"
