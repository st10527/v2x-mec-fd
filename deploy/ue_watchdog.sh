#!/usr/bin/env bash
# VM2 的 UE 看門狗：UE 沒上線（沒有 uesimtun0）就重啟 gNB 與 UE。
#
# 由 ueransim-watchdog.timer 每 30 秒執行一次（deploy/install_services.sh 安裝）。
#
# 為什麼需要它（115/11/04 重開機測試實測）：兩台 VM 一起開機時，gNB 比 VM1 的 AMF 早起來，
# SCTP 連線被拒後 UERANSIM 的 gNB **不會重試**，但行程也不結束，systemd 的 Restart 管不到；
# UE 因此一直報 "Cell selection failure"（cell barred）。展示途中核網重啟也是同樣的狀況。
set -u
IF="${UE_IF:-uesimtun0}"
GRACE_S=40          # UE 剛啟動時給它時間完成註冊

ip -4 addr show "$IF" 2>/dev/null | grep -q 'inet 10\.60\.' && exit 0

since=$(systemctl show ueransim-ue -p ActiveEnterTimestampMonotonic --value)
now=$(awk '{printf "%d", $1 * 1000000}' /proc/uptime)
(( since > 0 && now - since < GRACE_S * 1000000 )) && exit 0

logger -t ue-watchdog "${IF} 不存在，重啟 gNB 與 UE"
systemctl restart ueransim-gnb
sleep 4
systemctl restart ueransim-ue
