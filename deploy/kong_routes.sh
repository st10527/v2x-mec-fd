#!/usr/bin/env bash
# 建立 SPEC §3.3 的全部 Kong Service 與 Route，一次到位。
#
# SPEC §15.7：教材中的 Kong Service/Route 建立指令已驗證可用，直接沿用。
# 本腳本只是把 SPEC §3.3 的路由表逐列餵進 Kong Admin API，寫法與教材一致。
#
# 冪等：重複執行不會建出重複的 service/route（先刪再建）。
# 決賽當天沒有時間逐條手下指令，這支必須一行就能把路由全部還原。
#
# 用法：  bash deploy/kong_routes.sh [--dry-run]
set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

# Kong Admin port 不固定，每次由 container 查（SPEC §3.3 的教材寫法，勿改）
if ADMIN_PORT=$(docker port oai-mep-gateway 8001/tcp 2>/dev/null | head -n1 | sed 's/.*://') \
   && [[ -n "$ADMIN_PORT" ]]; then
  :
elif [[ $DRY_RUN -eq 1 ]]; then
  ADMIN_PORT="8001"            # dry-run 在沒有 docker 的開發機上也要跑得完
  echo "（dry-run：找不到 oai-mep-gateway container，Admin port 暫以 8001 代入）"
else
  echo "找不到 oai-mep-gateway container 的 Admin port。" >&2
  echo "請確認 OAI-MEP 已啟動（docker ps | grep oai-mep-gateway），見 SPEC §3.3。" >&2
  exit 1
fi
ADMIN="http://127.0.0.1:${ADMIN_PORT}"
echo "Kong Admin: ${ADMIN}"

# MEC App 綁定位址（SPEC §3.1）
MEC_HOST="172.16.6.10"

# 名稱 | 上游 port | 路由路徑      —— 對應 SPEC §3.3 的表
ROUTES=(
  "mec-v2x-a|5002|/mec/v2x/a"
  "mec-v2x-b|5003|/mec/v2x/b"
  "mec-fd-a|5002|/mec/fd/a"
  "mec-fd-b|5003|/mec/fd/b"
  "mec-dash|5010|/mec/dash"
  "mec-health|5000|/mec/health"
)

run() {
  if [[ $DRY_RUN -eq 1 ]]; then echo "  [dry-run] $*"; else "$@"; fi
}

for entry in "${ROUTES[@]}"; do
  IFS='|' read -r NAME PORT PATH_ <<< "$entry"
  URL="http://${MEC_HOST}:${PORT}"
  echo "--- ${NAME}  ${PATH_} -> ${URL}"

  # 先刪除既有的同名 service/route，確保冪等
  run curl -s -X DELETE "${ADMIN}/routes/${NAME}-route"  > /dev/null || true
  run curl -s -X DELETE "${ADMIN}/services/${NAME}"      > /dev/null || true

  run curl -s -X POST "${ADMIN}/services" \
      --data "name=${NAME}" \
      --data "url=${URL}" > /dev/null

  # strip_path=true：Kong 會把路由前綴切掉再轉給 App，
  # 因此 App 端看到的是 /bsm、/logits、/status（SPEC §3.3）
  run curl -s -X POST "${ADMIN}/services/${NAME}/routes" \
      --data "name=${NAME}-route" \
      --data "paths[]=${PATH_}" \
      --data "strip_path=true" > /dev/null
done

echo
echo "已建立 ${#ROUTES[@]} 條路由。驗證："
echo "  curl -i http://172.16.6.100/mec/health/healthz     # 應回 200"
echo "  curl -i http://172.16.6.100/mec/fd/a/status        # 應回 FD 狀態"
if [[ $DRY_RUN -eq 0 ]]; then
  curl -s "${ADMIN}/routes" | python3 -c '
import json, sys
d = json.load(sys.stdin)
rows = []
for r in d.get("data", []):
    # Kong 允許 route 沒有 name，直接 sorted() 會在 None 上爆掉
    rows.append((r.get("name") or "(未命名)", ",".join(r.get("paths") or [])))
for name, paths in sorted(rows):
    print(f"  {name:<14} {paths}")
print(f"  共 {len(rows)} 條")'
fi
