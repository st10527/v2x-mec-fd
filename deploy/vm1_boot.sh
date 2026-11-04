#!/usr/bin/env bash
# VM1 開機還原（SPEC §13.4：冷開機後自動回到可展示狀態）。
#
# 由 v2x-vm1-boot.service 在開機時執行（deploy/install_services.sh 安裝），也可手動跑：
#   bash deploy/vm1_boot.sh
#
# 為什麼需要它（115/10/13 實際發生）：VM 重開機後
#   * Kong 容器非正常關機留下 /usr/local/kong/worker_events.sock，重啟同一個容器時
#     nginx 一直 bind 失敗，卡在 Restarting 迴圈三週沒人發現；
#   * oai-mep-registry 沒有自己起來；
#   * iptables 的 DNAT 規則不會跨重開機保存，UE 的封包到不了 Kong。
# free5GC、MEC App 已是 systemd 服務，會自己起來；缺的是上面三項。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
OAI_MEP="${HOME}/oai-mep"

echo "=== 1. OAI-MEP：重建容器 ==="
# 重建而非重啟：新容器的檔案系統沒有殘留的 socket；Kong 的設定與路由存在 postgres，不受影響。
cd "$OAI_MEP"
sudo docker compose -f ci-scripts/docker-compose.yaml up -d --force-recreate 2>&1 | tail -6
cd "$ROOT"

echo "=== 2. 等 Kong 就緒 ==="
for i in $(seq 1 60); do
  [[ "$(sudo docker inspect -f '{{.State.Health.Status}}' oai-mep-gateway 2>/dev/null)" == "healthy" ]] && break
  sleep 3
done
sudo docker inspect -f '  oai-mep-gateway {{.State.Health.Status}}' oai-mep-gateway

echo "=== 2b. Service Registry ==="
# 上游 oai-mep-registry 啟動時一律「新建」Kong 的 service_registry 服務，已存在就回 409 並退出，
# 所以第二次開機起它都起不來。先刪掉舊的登記（routes 再 service），再啟動它重新登記。
ADMIN="http://127.0.0.1:$(sudo docker port oai-mep-gateway 8001/tcp | head -1 | sed 's/.*://')"
for r in $(curl -s "${ADMIN}/services/service_registry/routes" \
           | python3 -c 'import json,sys; [print(x["id"]) for x in json.load(sys.stdin).get("data", [])]' 2>/dev/null); do
  curl -s -o /dev/null -X DELETE "${ADMIN}/routes/${r}"
done
curl -s -o /dev/null -X DELETE "${ADMIN}/services/service_registry"
sudo docker start oai-mep-registry >/dev/null
sleep 8
sudo docker inspect -f '  oai-mep-registry {{.State.Status}}' oai-mep-registry

echo "=== 3. N6 位址與 DNAT（iptables 不跨重開機保存）==="
bash deploy/setup_vm1.sh | grep -E "OK|FAIL|WARN"

echo "=== 4. Kong 路由 ==="
bash deploy/kong_routes.sh | tail -3

echo "=== 5. MEC App ==="
sudo systemctl restart mec-app-a mec-app-b
sleep 3
for n in a b; do
  printf "  /mec/v2x/%s/healthz -> " "$n"
  curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 "http://172.16.6.100/mec/v2x/${n}/healthz" || echo "失敗"
done
