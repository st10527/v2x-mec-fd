#!/usr/bin/env bash
# VM2 的 UE 看門狗：UE 沒上線、或經 5G 送不出封包，就重啟 gNB 與 UE。
#
# 由 ueransim-watchdog.timer 每 30 秒執行一次（deploy/install_services.sh 安裝）。
#
# 為什麼需要它（115/11/04 重開機測試實測）：兩台 VM 一起開機時，gNB 比 VM1 的 AMF 早起來，
# SCTP 連線被拒後 UERANSIM 的 gNB **不會重試**，但行程也不結束，systemd 的 Restart 管不到；
# UE 因此一直報 "Cell selection failure"（cell barred）。展示途中核網重啟也是同樣的狀況。
#
# 只看介面在不在不夠（115/11/12 實際發生）：對 VM 做快照時 VM 會暫停幾秒，UE 與 gNB 間的
# 模擬無線連線因此 "Radio link failure"，UE 退回 CM-IDLE，但 uesimtun0 與 10.60.0.x 仍在——
# 介面看起來正常，封包卻一個都送不出去，資料面斷了 8 天才被學生發現。
# 所以要經 uesimtun0 真的 ping 到 VM1 的 N6 位址（走完 gNB → UPF → N6），才算正常。
set -u
IF="${UE_IF:-uesimtun0}"
TARGET="${UE_PING_TARGET:-172.16.6.10}"    # VM1 的 N6 位址（MEC App 綁定處），只依賴 5G 資料面
GRACE_S=40          # UE 剛啟動時給它時間完成註冊

if ip -4 addr show "$IF" 2>/dev/null | grep -q 'inet 10\.60\.'; then
  ping -c 3 -W 2 -I "$IF" "$TARGET" >/dev/null 2>&1 && exit 0
  REASON="${IF} 存在但經 5G ping 不到 ${TARGET}（資料面中斷）"
else
  REASON="${IF} 不存在"
fi

since=$(systemctl show ueransim-ue -p ActiveEnterTimestampMonotonic --value)
now=$(awk '{printf "%d", $1 * 1000000}' /proc/uptime)
(( since > 0 && now - since < GRACE_S * 1000000 )) && exit 0

logger -t ue-watchdog "${REASON}，重啟 gNB 與 UE"
systemctl restart ueransim-gnb
sleep 4
systemctl restart ueransim-ue
