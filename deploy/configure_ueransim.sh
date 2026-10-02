#!/usr/bin/env bash
# 設定 UERANSIM 以符合本專案的雙 VM 拓樸（SPEC §3.1）。
#
# UERANSIM 預設全綁 127.0.0.1（單機測試用）。我們的核網在 VM1，
# gNB 必須用 host-only 位址對外，並指向 VM1 的 AMF。
#
#   free5gc-gnb.yaml  linkIp/ngapIp/gtpIp  127.0.0.1 -> VM2_HOSTONLY
#                     amfConfigs[0].address 127.0.0.1 -> VM1_HOSTONLY
#   free5gc-ue.yaml   gnbSearchList[0]     127.0.0.1 -> VM2_HOSTONLY
#
# UE 的身分（SUPI / key / OPc）維持 UERANSIM 預設值不動，
# configure_free5gc.sh 灌進 free5GC 的用戶資料就是照這組值建的，兩邊必須一致。
#
# 用法：
#   bash deploy/configure_ueransim.sh            # 套用
#   bash deploy/configure_ueransim.sh --check    # 只檢查
#   bash deploy/configure_ueransim.sh --restore  # 還原
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
source "${ROOT}/deploy/topology.env"

ok()   { echo "  [OK]   $*"; }
warn() { echo "  [WARN] $*"; }
die()  { echo "  [FAIL] $*" >&2; exit 1; }

CFG="${HOME}/UERANSIM/config"
GNB="${CFG}/free5gc-gnb.yaml"
UE="${CFG}/free5gc-ue.yaml"
[[ -f "$GNB" && -f "$UE" ]] || die "找不到 ${CFG} 下的設定檔，請先執行 deploy/install_ueransim.sh"

show() {
  echo "  gnb linkIp/ngapIp/gtpIp : $(grep -E '^linkIp|^ngapIp|^gtpIp' "$GNB" | sed 's/.*: *//' | tr '\n' ' ')"
  echo "  gnb amfConfigs address  : $(grep -A2 'amfConfigs' "$GNB" | grep 'address' | sed 's/.*: *//')"
  echo "  ue  gnbSearchList       : $(grep -A1 'gnbSearchList' "$UE" | tail -1 | tr -d ' -')"
}

echo "=== 1. 目前設定 ==="
show
[[ "${1:-}" == "--check" ]] && exit 0

if [[ "${1:-}" == "--restore" ]]; then
  for f in "$GNB" "$UE"; do
    [[ -f "${f}.orig" ]] && { cp "${f}.orig" "$f"; ok "已還原 $(basename "$f")"; }
  done
  exit 0
fi

for f in "$GNB" "$UE"; do [[ -f "${f}.orig" ]] || cp "$f" "${f}.orig"; done

echo
echo "=== 2. gNB 位址 ==="
python3 - "$GNB" "$VM2_HOSTONLY" "$VM1_HOSTONLY" <<'PY'
import re, sys
path, gnb_ip, amf_ip = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(path, encoding="utf-8").read()
changed = []
# gNB 自身的三個介面位址
for key in ("linkIp", "ngapIp", "gtpIp"):
    pat = re.compile(rf"(^{key}:\s*)([0-9.]+)", re.M)
    m = pat.search(s)
    if m is None:
        raise SystemExit(f"{key} 找不到，free5gc-gnb.yaml 格式可能已變動")
    if m.group(2) != gnb_ip:
        s = pat.sub(rf"\g<1>{gnb_ip}", s, count=1)
        changed.append(f"{key}={gnb_ip}")
# amfConfigs 底下第一個 address
pat = re.compile(r"(amfConfigs:\s*\n\s*-\s*address:\s*)([0-9.]+)")
m = pat.search(s)
if m is None:
    raise SystemExit("amfConfigs address 找不到，格式可能已變動")
if m.group(2) != amf_ip:
    s = pat.sub(rf"\g<1>{amf_ip}", s, count=1)
    changed.append(f"amf={amf_ip}")
open(path, "w", encoding="utf-8").write(s)
print("  變更：" + (", ".join(changed) if changed else "無（已是目標值）"))
PY
ok "gNB 設定完成"

echo
echo "=== 3. UE 的 gNB 搜尋清單 ==="
python3 - "$UE" "$VM2_HOSTONLY" <<'PY'
import re, sys
path, gnb_ip = sys.argv[1], sys.argv[2]
s = open(path, encoding="utf-8").read()
pat = re.compile(r"(gnbSearchList:\s*\n\s*-\s*)([0-9.]+)")
m = pat.search(s)
if m is None:
    raise SystemExit("gnbSearchList 找不到，free5gc-ue.yaml 格式可能已變動")
if m.group(2) == gnb_ip:
    print(f"  （已是 {gnb_ip}，不需變更）")
else:
    open(path, "w", encoding="utf-8").write(pat.sub(rf"\g<1>{gnb_ip}", s, count=1))
    print(f"  gnbSearchList -> {gnb_ip}")
PY
ok "UE 設定完成"

echo
echo "=== 4. 套用後 ==="
show

echo
echo "=== 5. 啟動方式（各佔一個終端機，需 sudo 建 tun 介面）==="
echo "  sudo ~/UERANSIM/build/nr-gnb -c ${GNB}"
echo "  sudo ~/UERANSIM/build/nr-ue  -c ${UE}"
echo
echo "成功訊號："
echo "  gNB：NG Setup procedure is successful"
echo "  UE ：PDU Session establishment is successful, TUN interface[${UE_IF}, 10.60.0.1] is up"
