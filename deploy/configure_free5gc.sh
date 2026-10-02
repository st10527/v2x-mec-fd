#!/usr/bin/env bash
# 設定 free5GC 以符合本專案的雙 VM 拓樸（SPEC §3.1）。
#
# free5GC 預設把所有 NF 綁在 127.0.0.x，那是單機測試用的。我們的 gNB 在 VM2，
# 必須讓 N2（NGAP）與 N3（GTP-U）走 host-only 網段才連得到。
#
# 改動範圍刻意最小：只動跨 VM 必須外露的兩個位址，其餘 SBI 維持 127.0.0.x
# 留在 VM1 內部。改越少，出錯時越好查。
#
#   amfcfg.yaml  ngapIpList  127.0.0.18 -> VM1_HOSTONLY   （gNB 連 AMF 的 N2）
#   upfcfg.yaml  gtpu addr   127.0.0.8  -> VM1_HOSTONLY   （gNB 送 GTP-U 的 N3）
#
# 另外建立 N6 位址（教材 p.39）並灌入 UE 用戶資料。
#
# 用法：
#   bash deploy/configure_free5gc.sh            # 套用設定
#   bash deploy/configure_free5gc.sh --check    # 只檢查
#   bash deploy/configure_free5gc.sh --restore  # 還原成原始設定
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
source "${ROOT}/deploy/topology.env"

ok()   { echo "  [OK]   $*"; }
info() { echo "  [..]   $*"; }
warn() { echo "  [WARN] $*"; }
die()  { echo "  [FAIL] $*" >&2; exit 1; }

F5GC="${HOME}/free5gc"
CFG="${F5GC}/config"
export PATH="$PATH:/usr/local/go/bin"

# UE 身分：與 UERANSIM 的 config/free5gc-ue.yaml 預設值一致，兩邊必須相同
UE_IMSI="imsi-208930000000001"
UE_PLMN="20893"
# 以下 SIM 金鑰為 free5GC／UERANSIM 官方文件的預設測試值，僅供模擬環境；正式部署必須更換
UE_KEY="8baf473f2f8fd09487cccbd7097c6862"
UE_OPC="8e27b6af0e692e750f32667a3b14605d"   # UERANSIM 的 opType 是 OPC，故此值是 OPc 不是 OP
UE_AMF="8000"
# === 起始序號：這個值不能隨便填，踩過兩次坑，原因記在這裡 ===
#
# 3GPP 把 SQN 拆成 SEQ || IND，UERANSIM 的 indBitLen = 5（見 src/ue/nas/usim/usim.cpp
# 的 SqnManager(5, 1<<28)），所以 SEQ = SQN >> 5。其 checkSqn() 要求
# **SEQ 必須嚴格大於已記錄的 SEQ**，否則回 SYNCHRONISATION_FAILURE。
#
#   坑 1：free5GC 預設的 16f3b3f70fc2 太大。全新 UE 的 SQN-MS 為 0，
#         差距遠超過 wrappingDelta(2^28)，UE 要求重新同步；而實測
#         free5GC 在此組合下不處理該重新同步，AMF 一路 T3560 逾時。
#   坑 2：改成 0 仍失敗。free5GC 取用後 +1，第一次送出 SQN=1，
#         其 SEQ = 1>>5 = 0，不大於已記錄的 0 → 一樣被拒。
#
# 正解：起始值要讓 SEQ ≥ 1，即 SQN ≥ 0x20（32）。取 0x20，
# 第一次認證送出 SQN=0x20 → SEQ=1、IND=0，順利通過。
UE_SQN="000000000020"

[[ -d "$CFG" ]] || die "找不到 ${CFG}，請先執行 deploy/install_free5gc.sh"

backup() {  # 只在第一次備份，之後重跑不會覆蓋掉原始檔
  local f="$1"
  [[ -f "${f}.orig" ]] || cp "$f" "${f}.orig"
}

if [[ "${1:-}" == "--restore" ]]; then
  for f in "${CFG}/amfcfg.yaml" "${CFG}/upfcfg.yaml" "${CFG}/smfcfg.yaml"; do
    [[ -f "${f}.orig" ]] && { cp "${f}.orig" "$f"; ok "已還原 $(basename "$f")"; }
  done
  exit 0
fi

echo "=== 1. 目前設定 ==="
echo "  amfcfg ngapIpList : $(grep -A1 'ngapIpList' "${CFG}/amfcfg.yaml" | tail -1 | tr -d ' -')"
echo "  upfcfg N3 addr    : $(grep -A1 'ifList:' "${CFG}/upfcfg.yaml" | tail -1 | sed 's/.*addr: *//')"
echo "  smfcfg N3 endpoint: $(grep -A2 'interfaceType: N3' "${CFG}/smfcfg.yaml" | tail -1 | tr -d ' -')"
if [[ "${1:-}" == "--check" ]]; then
  ip a show dev lo | grep -qE '172\.16\.6\.10' && ok "N6 位址已掛載" || warn "N6 位址未掛載"
  mongosh --quiet --eval 'db.getSiblingDB("free5gc").getCollection("subscriptionData.authenticationData.authenticationSubscription").countDocuments()' 2>/dev/null \
    | xargs -I{} echo "  用戶資料筆數      : {}" || warn "mongosh 查詢失敗"
  exit 0
fi

echo
echo "=== 2. AMF 的 N2（NGAP）位址 ==="
backup "${CFG}/amfcfg.yaml"
# ngapIpList 底下第一個 "- x.x.x.x" 換成 host-only 位址
python3 - "$CFG/amfcfg.yaml" "$VM1_HOSTONLY" \
        'ngapIpList' 'r"(ngapIpList:[^\n]*\n\s*-\s*)([0-9.]+)"' <<'PY'
import re, sys
path, addr = sys.argv[1], sys.argv[2]
pat = re.compile(r"(ngapIpList:[^\n]*\n\s*-\s*)([0-9.]+)")
s = open(path, encoding="utf-8").read()
m = pat.search(s)
if m is None:
    raise SystemExit("ngapIpList 找不到，amfcfg.yaml 格式可能已變動")
if m.group(2) == addr:
    print(f"  （已是 {addr}，不需變更）")
else:
    open(path, "w", encoding="utf-8").write(pat.sub(r"\g<1>" + addr, s, count=1))
PY
ok "ngapIpList -> ${VM1_HOSTONLY}"

echo
echo "=== 3. UPF 的 N3（GTP-U）位址 ==="
backup "${CFG}/upfcfg.yaml"
python3 - "$CFG/upfcfg.yaml" "$VM1_HOSTONLY" <<'PY'
import re, sys
path, addr = sys.argv[1], sys.argv[2]
# 結構為：gtpu: -> forwarder: gtp5g -> ifList: -> - addr: x.x.x.x / type: N3
pat = re.compile(r"(ifList:\s*\n\s*-\s*addr:\s*)([0-9.]+)")
s = open(path, encoding="utf-8").read()
m = pat.search(s)
if m is None:
    raise SystemExit("ifList 底下的 addr 找不到，upfcfg.yaml 格式可能已變動")
if m.group(2) == addr:
    print(f"  （已是 {addr}，不需變更）")
else:
    open(path, "w", encoding="utf-8").write(pat.sub(r"\g<1>" + addr, s, count=1))
PY
ok "gtpu addr -> ${VM1_HOSTONLY}"

echo
echo "=== 3b. SMF 告知 gNB 的 N3 endpoint ==="
# 這一項漏掉會很難查：UE 註冊、PDU session 都會成功，但資料完全不通。
# 原因是 gNB 要把 GTP-U 送到哪個位址，是 SMF 依 userplaneInformation 告知的，
# 不是讀 upfcfg。只改 upfcfg 的話 gNB 仍被指向 127.0.0.8（VM1 的 loopback），
# 封包送出去就消失，VM1 的 ens36 上連一個 GTP-U 封包都看不到。
backup "${CFG}/smfcfg.yaml"
python3 - "$CFG/smfcfg.yaml" "$VM1_HOSTONLY" <<'PY'
import re, sys
path, addr = sys.argv[1], sys.argv[2]
s = open(path, encoding="utf-8").read()
pat = re.compile(r"(interfaceType:\s*N3[^\n]*\n\s*endpoints:[^\n]*\n\s*-\s*)([0-9.]+)")
m = pat.search(s)
if m is None:
    raise SystemExit("N3 endpoints 找不到，smfcfg.yaml 格式可能已變動")
if m.group(2) == addr:
    print(f"  （已是 {addr}，不需變更）")
else:
    open(path, "w", encoding="utf-8").write(pat.sub(r"\g<1>" + addr, s, count=1))
    print(f"  N3 endpoint -> {addr}")
PY
ok "SMF 的 N3 endpoint 設定完成"

echo
echo "=== 4. N6 位址（教材 p.39）==="
sudo ip addr add "${N6_APP}/24" dev lo 2>/dev/null || true
sudo ip addr add "${N6_GW}/24" dev lo 2>/dev/null || true
ip a show dev lo | grep -oE '172\.16\.6\.[0-9]+' | tr '\n' ' ' | xargs echo "  已掛載："
# 開機後自動還原（VM 重開不必手動補）
sudo tee /etc/systemd/system/v2x-n6-addr.service >/dev/null <<EOF
[Unit]
Description=V2X N6 loopback addresses (SPEC 3.1)
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c '/sbin/ip addr add ${N6_APP}/24 dev lo 2>/dev/null; /sbin/ip addr add ${N6_GW}/24 dev lo 2>/dev/null; true'

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now v2x-n6-addr.service >/dev/null 2>&1 || true
ok "已設為開機自動掛載（v2x-n6-addr.service）"

echo
echo "=== 5. UE 用戶資料 ==="
# 用 webconsole 的 API 灌，不手刻 MongoDB 文件——schema 很細，手刻容易錯，
# 而且 free5GC 版本一改 schema 就可能變。webconsole 只需建 Go binary，不需 node。
WC="${F5GC}/webconsole"
if [[ ! -x "${WC}/bin/webconsole" ]]; then
  info "建置 webconsole 後端（純 Go，不需 node）"
  (cd "$WC" && CGO_ENABLED=0 go build -o bin/webconsole ./server.go) \
    || die "webconsole 建置失敗"
fi
ok "webconsole 就緒"

# webconsole 預設佔 port 5000，與 SPEC §3.3 的 mec-health 撞號。
# 這裡只是短暫起來灌資料，用完即停，不會長跑佔住。
info "暫時啟動 webconsole 灌用戶資料"
(cd "$WC" && ./bin/webconsole -c config/webuicfg.yaml >/tmp/webconsole.log 2>&1 &) 
WC_OK=0
for i in $(seq 1 20); do
  # 未帶 token 會回 401，那同樣代表服務已經起來了，所以只看有沒有回應
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:5000/api/subscriber" 2>/dev/null || echo 000)
  [[ "$CODE" != "000" ]] && { WC_OK=1; break; }
  sleep 1
done
[[ $WC_OK -eq 1 ]] || { cat /tmp/webconsole.log | tail -5; die "webconsole 起不來"; }

cat > /tmp/subscriber.json <<EOF
{
  "plmnID": "${UE_PLMN}",
  "ueId": "${UE_IMSI}",
  "AuthenticationSubscription": {
    "authenticationManagementField": "${UE_AMF}",
    "authenticationMethod": "5G_AKA",
    "milenage": { "op": { "encryptionAlgorithm": 0, "encryptionKey": 0, "opValue": "" } },
    "opc": { "encryptionAlgorithm": 0, "encryptionKey": 0, "opcValue": "${UE_OPC}" },
    "permanentKey": { "encryptionAlgorithm": 0, "encryptionKey": 0, "permanentKeyValue": "${UE_KEY}" },
    "sequenceNumber": "${UE_SQN}"
  },
  "AccessAndMobilitySubscriptionData": {
    "gpsis": ["msisdn-0900000000"],
    "nssai": { "defaultSingleNssais": [{ "sst": 1, "sd": "010203", "isDefault": true }], "singleNssais": [] },
    "subscribedUeAmbr": { "downlink": "2 Gbps", "uplink": "1 Gbps" }
  },
  "SessionManagementSubscriptionData": [{
    "singleNssai": { "sst": 1, "sd": "010203" },
    "dnnConfigurations": {
      "internet": {
        "sscModes": { "defaultSscMode": "SSC_MODE_1", "allowedSscModes": ["SSC_MODE_2", "SSC_MODE_3"] },
        "pduSessionTypes": { "defaultSessionType": "IPV4", "allowedSessionTypes": ["IPV4"] },
        "sessionAmbr": { "uplink": "1000 Mbps", "downlink": "1000 Mbps" },
        "5gQosProfile": { "5qi": 9, "arp": { "priorityLevel": 8, "preemptCap": "", "preemptVuln": "" }, "priorityLevel": 8 }
      }
    }
  }],
  "SmfSelectionSubscriptionData": {
    "subscribedSnssaiInfos": { "01010203": { "dnnInfos": [{ "dnn": "internet" }] } }
  },
  "AmPolicyData": { "subscCats": ["free5gc"] },
  "SmPolicyData": {
    "smPolicySnssaiData": {
      "01010203": {
        "snssai": { "sst": 1, "sd": "010203" },
        "smPolicyDnnData": { "internet": { "dnn": "internet" } }
      }
    }
  },
  "FlowRules": [], "QosFlows": [], "ChargingDatas": []
}
EOF

# webconsole 的 subscriber API 需要 token（驗證寫在 handler 裡而非 middleware）。
# 預設帳密由 webconsole 首次啟動時建立：admin / free5gc。
# admin 使用者是 webconsole 首次啟動時才建立的，剛起來就登入會撞上競態，
# 所以重試幾次而不是一次定生死。
TOKEN=""
for i in $(seq 1 15); do
  TOKEN=$(curl -s --max-time 5 -X POST "http://127.0.0.1:5000/api/login" \
          -H 'Content-Type: application/json' \
          --data '{"username":"admin","password":"free5gc"}' \
          | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null || true)
  [[ -n "$TOKEN" ]] && break
  sleep 2
done
[[ -n "$TOKEN" ]] || { tail -8 /tmp/webconsole.log; die "webconsole 登入失敗，取不到 token"; }
ok "已登入 webconsole（token ${TOKEN:0:12}…）"

# 先刪再建，確保重跑時新的值真的會套用（POST 對既有用戶不會覆寫）
curl -s -o /dev/null -X DELETE -H "Token: ${TOKEN}" \
  "http://127.0.0.1:5000/api/subscriber/${UE_IMSI}/${UE_PLMN}" 2>/dev/null || true

HTTP=$(curl -s -o /tmp/sub_resp.txt -w '%{http_code}' -X POST \
  "http://127.0.0.1:5000/api/subscriber/${UE_IMSI}/${UE_PLMN}" \
  -H 'Content-Type: application/json' -H "Token: ${TOKEN}" \
  --data @/tmp/subscriber.json)
if [[ "$HTTP" =~ ^(200|201)$ ]]; then
  ok "用戶 ${UE_IMSI} 已建立（HTTP ${HTTP}）"
else
  warn "POST 回 HTTP ${HTTP}：$(head -c 300 /tmp/sub_resp.txt)"
fi

COUNT=$(curl -sf --max-time 5 -H "Token: ${TOKEN}" "http://127.0.0.1:5000/api/subscriber" \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d or []))' 2>/dev/null || echo "?")
ok "目前用戶數：${COUNT}"

pkill -f 'bin/webconsole' 2>/dev/null || true
ok "webconsole 已停止（避免長期佔用 port 5000）"

echo
echo "=== 6. 完成 ==="
echo "  啟動核網：cd ~/free5gc && sudo ./run.sh"
echo "  UE 身分：${UE_IMSI}（PLMN ${UE_PLMN}）"
