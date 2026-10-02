#!/usr/bin/env bash
# 把 free5GC / UERANSIM 裝成 systemd 服務。
#
# 為什麼不用 nohup：這些行程要在展示期間穩定長跑，nohup 在 ssh 斷線時常被收掉，
# 而且沒有重啟、沒有統一 log。systemd 兩者都給，也讓 SPEC §9 的
# 「重開機後一行指令還原」真正成立——設成 enable 後開機自動起來。
#
# 角色由主機名自動判斷，也可用 --role vm1|vm2 覆寫。
#
# 用法：
#   bash deploy/install_services.sh              # 安裝並啟動
#   bash deploy/install_services.sh --status     # 看狀態
#   bash deploy/install_services.sh --stop       # 停止
#   bash deploy/install_services.sh --logs       # 看即時 log
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

ok()   { echo "  [OK]   $*"; }
info() { echo "  [..]   $*"; }
die()  { echo "  [FAIL] $*" >&2; exit 1; }

ROLE=""
case "${1:-}" in
  --role) ROLE="${2:-}" ;;
  *) case "$(hostname)" in
       *free5gc*|*vm1*) ROLE="vm1" ;;
       *ueransim*|*vm2*) ROLE="vm2" ;;
     esac ;;
esac

vm1_units() { echo "free5gc"; }
vm2_units() { echo "ueransim-gnb ueransim-ue"; }
units() { [[ "$ROLE" == "vm1" ]] && vm1_units || vm2_units; }

case "${1:-install}" in
  --status)
    for u in $(units); do
      printf "  %-16s %s\n" "$u" "$(systemctl is-active "$u" 2>/dev/null || echo inactive)"
    done
    exit 0 ;;
  --stop)
    for u in $(units); do sudo systemctl stop "$u" 2>/dev/null && ok "已停止 $u"; done
    exit 0 ;;
  --logs)
    sudo journalctl -f -u "$(units | awk '{print $1}')"
    exit 0 ;;
esac

[[ -n "$ROLE" ]] || die "無法判斷角色，請用 --role vm1|vm2"
info "角色：${ROLE}（$(hostname)）"

if [[ "$ROLE" == "vm1" ]]; then
  F5GC="${HOME}/free5gc"
  [[ -x "${F5GC}/run.sh" ]] || die "找不到 ${F5GC}/run.sh"
  # 用我們自己的啟動器而非上游 run.sh，理由見 deploy/free5gc_start.sh 檔頭
  install -m 755 "${ROOT}/deploy/free5gc_start.sh" /usr/local/bin/free5gc-start 2>/dev/null \
    || sudo install -m 755 "${ROOT}/deploy/free5gc_start.sh" /usr/local/bin/free5gc-start
  sudo tee /etc/systemd/system/free5gc.service >/dev/null <<EOF
[Unit]
Description=free5GC core network (SPEC 3.1 VM1)
After=network-online.target mongod.service v2x-n6-addr.service
Wants=network-online.target
Requires=mongod.service

[Service]
Type=simple
WorkingDirectory=${F5GC}
ExecStart=/usr/local/bin/free5gc-start
Environment=F5GC_DIR=${F5GC}
Restart=on-failure
RestartSec=5
KillMode=mixed
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF
  ok "已寫入 free5gc.service"
  sudo systemctl daemon-reload
  sudo systemctl enable free5gc >/dev/null 2>&1 || true
  # 收掉手動起的殘留。NF 的 cmdline 是 "./bin/amf ..."（相對路徑），
  # 用完整路徑當 pattern 抓不到，必須連 run.sh 一起清，否則新舊兩份會搶 port，
  # 症狀是 NF 報 "address already in use"、UE 收到 Registration Reject [CONGESTION]。
  sudo pkill -f 'free5gc/run.sh' 2>/dev/null || true
  sudo pkill -f 'bin/(amf|smf|upf|nrf|udm|udr|ausf|pcf|nssf|chf|nef|n3iwf|tngf)' 2>/dev/null || true
  sleep 3
  sleep 2
  sudo systemctl restart free5gc
  ok "free5gc 已啟動"

else
  U="${HOME}/UERANSIM"
  [[ -x "${U}/build/nr-gnb" ]] || die "找不到 ${U}/build/nr-gnb"

  sudo tee /etc/systemd/system/ueransim-gnb.service >/dev/null <<EOF
[Unit]
Description=UERANSIM gNB (SPEC 3.1 VM2)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${U}
ExecStart=${U}/build/nr-gnb -c ${U}/config/free5gc-gnb.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  # UE 必須等 gNB 起來才連得上，故 After + Requires
  sudo tee /etc/systemd/system/ueransim-ue.service >/dev/null <<EOF
[Unit]
Description=UERANSIM UE (SPEC 3.1 VM2)
After=ueransim-gnb.service
Requires=ueransim-gnb.service

[Service]
Type=simple
WorkingDirectory=${U}
ExecStartPre=/bin/sleep 3
ExecStart=${U}/build/nr-ue -c ${U}/config/free5gc-ue.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  ok "已寫入 ueransim-gnb.service 與 ueransim-ue.service"
  sudo systemctl daemon-reload
  sudo systemctl enable ueransim-gnb ueransim-ue >/dev/null 2>&1 || true
  sudo pkill -f 'nr-gnb|nr-ue' 2>/dev/null || true
  sleep 2
  sudo systemctl restart ueransim-gnb
  sleep 4
  sudo systemctl restart ueransim-ue
  ok "gNB 與 UE 已啟動"
fi

echo
for u in $(units); do
  printf "  %-16s %s\n" "$u" "$(systemctl is-active "$u" 2>/dev/null || echo inactive)"
done
echo
echo "看 log：sudo journalctl -u <服務名> -f"
