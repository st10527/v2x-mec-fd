#!/usr/bin/env bash
# 在實體主機上以 KVM/libvirt 建出 SPEC §3.1 的兩台 VM。
#
# 為什麼要 VM 而不是裸機直裝（決策紀錄，會被問到）：
#   1. SPEC §3.1 的拓樸需要兩台機器（VM1 核網+MEC、VM2 UE+SUMO），裸機只有一台；
#   2. 實體主機是共用實驗機，教材要求 ufw disable、改 ip_forward、插 iptables DNAT、
#      裝 kernel module，這些是全機層級改動，不該落在別人的專案上；
#   3. 快照可回滾，核網設定弄壞時幾秒還原；
#   4. Ubuntu 22.04.5 是教材驗證過的環境（free5GC v4.1.0 的 Go / 函式庫組合），
#      對齊之後 SPEC §15.7「教材指令原樣沿用」才站得住。
#
#   註：**不是**因為 kernel 版本。gtp5g 官方說明支援 5.4 ~ 7.0.x，實體主機的
#   6.17 本來就在範圍內。這一點先前判斷有誤，於此更正備查。
#
# 用 cloud image + cloud-init，不用 ISO 安裝精靈：全程無互動、可重複執行，
# 符合 SPEC §9「重開機後一行指令還原整個系統」的要求。
#
# 用法（在實體主機上）：
#   sudo bash deploy/provision_vms.sh              # 完整佈建
#   sudo bash deploy/provision_vms.sh --status     # 看狀態
#   sudo bash deploy/provision_vms.sh --destroy    # 砍掉重來（會問確認）
#   bash deploy/provision_vms.sh --snapshot <名稱>  # 兩台一起做快照
#   bash deploy/provision_vms.sh --revert <名稱>    # 兩台一起還原
#
# 快照是選 VM 的理由之一：核網或 iptables 設定弄壞時幾秒還原，不必重裝。
# 建議在每個里程碑前先做一次，例如 clean-22045 / after-gtp5g / after-free5gc。
set -euo pipefail

# ---------------------------------------------------------------------------
# 參數（對齊教材 p.28-33 與 SPEC §3.1）
# ---------------------------------------------------------------------------
VM_USER="${VM_USER:-v2x}"
IMG_URL="https://cloud-images.ubuntu.com/releases/22.04/release/ubuntu-22.04-server-cloudimg-amd64.img"
POOL="/var/lib/libvirt/images"
BASE="${POOL}/jammy-base.img"

NAT_NET="v2x-nat"          # 教材 p.29：NAT 172.16.211.0/24
NAT_CIDR="172.16.211"
HO_NET="v2x-hostonly"      # 教材 p.29：Host-only 192.168.113.0/24
HO_CIDR="192.168.113"

# 名稱 | host-only IP | vCPU | RAM(MiB) | 磁碟(GB) | NAT MAC | HO MAC
VMS=(
  "vm1-free5gc|${HO_CIDR}.131|4|8192|40|52:54:00:21:01:01|52:54:00:21:01:02"
  "vm2-ueransim|${HO_CIDR}.132|4|8192|40|52:54:00:21:02:01|52:54:00:21:02:02"
)

ok()   { echo "  [OK]   $*"; }
info() { echo "  [..]   $*"; }
warn() { echo "  [WARN] $*"; }
die()  { echo "  [FAIL] $*" >&2; exit 1; }

# 一律操作系統層級的 libvirt（非使用者 session）
export LIBVIRT_DEFAULT_URI="qemu:///system"
CALLER="${SUDO_USER:-$(id -un)}"
CALLER_HOME=$(getent passwd "$CALLER" | cut -d: -f6)

# ---------------------------------------------------------------------------
# --status / --destroy
# ---------------------------------------------------------------------------
case "${1:-provision}" in
  --status)
    echo "=== VM ==="; virsh list --all 2>/dev/null || echo "（libvirt 尚未安裝）"
    echo; echo "=== 網路 ==="; virsh net-list --all 2>/dev/null || true
    echo; echo "=== host-only 位址 ==="
    for e in "${VMS[@]}"; do
      IFS='|' read -r N IP _ <<< "$e"
      printf "  %-14s %s  " "$N" "$IP"
      ping -c1 -W1 "$IP" >/dev/null 2>&1 && echo "可達" || echo "不可達"
    done
    echo; echo "=== 快照 ==="
    for e in "${VMS[@]}"; do
      IFS='|' read -r N _ <<< "$e"
      printf "  %-14s " "$N"
      virsh snapshot-list "$N" --name 2>/dev/null | grep -v '^$' | tr '\n' ' '
      echo
    done
    exit 0 ;;
  --snapshot)
    SNAP="${2:-}"; [[ -n "$SNAP" ]] || die "用法：bash $0 --snapshot <名稱>"
    for e in "${VMS[@]}"; do
      IFS='|' read -r N _ <<< "$e"
      virsh snapshot-create-as "$N" "$SNAP" --atomic >/dev/null \
        && ok "$N 快照 ${SNAP} 已建立" || warn "$N 快照失敗"
    done
    exit 0 ;;
  --revert)
    SNAP="${2:-}"; [[ -n "$SNAP" ]] || die "用法：bash $0 --revert <名稱>"
    read -rp "確定把兩台 VM 還原到快照 ${SNAP}？之後的變更會消失。輸入 yes 繼續：" a
    [[ "$a" == "yes" ]] || { echo "已取消"; exit 0; }
    for e in "${VMS[@]}"; do
      IFS='|' read -r N _ <<< "$e"
      virsh snapshot-revert "$N" "$SNAP" >/dev/null \
        && ok "$N 已還原到 ${SNAP}" || warn "$N 還原失敗"
    done
    exit 0 ;;
  --destroy)
    [[ $EUID -eq 0 ]] || die "請以 sudo 執行：sudo bash $0 --destroy"
    read -rp "確定要刪除兩台 VM 與其磁碟？輸入 yes 繼續：" a
    [[ "$a" == "yes" ]] || { echo "已取消"; exit 0; }
    for e in "${VMS[@]}"; do
      IFS='|' read -r N _ <<< "$e"
      virsh destroy "$N" 2>/dev/null || true
      virsh undefine "$N" --remove-all-storage --nvram 2>/dev/null || \
        virsh undefine "$N" --remove-all-storage 2>/dev/null || true
      echo "  已刪除 $N"
    done
    exit 0 ;;
esac

[[ $EUID -eq 0 ]] || die "請以 sudo 執行：sudo bash $0"

# ---------------------------------------------------------------------------
# 1. 安裝虛擬化套件
# ---------------------------------------------------------------------------
echo "=== 1. 安裝 KVM / libvirt ==="
# 註：Ubuntu 24.04 已無 qemu-kvm 這個套件，改為 qemu-system-x86
PKGS=(qemu-system-x86 libvirt-daemon-system libvirt-clients virtinst
      cloud-image-utils genisoimage)
MISSING=()
for p in "${PKGS[@]}"; do
  dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  # 先修復既有的 dpkg 中斷狀態。這是本機在我們之前就留下的，
  # 不修的話任何 apt-get install 都會被擋。dpkg --configure -a 是 apt
  # 自己建議的標準修法，會把先前未完成的套件設定收尾。
  if ! dpkg --audit 2>/dev/null | grep -q '^$' && [[ -n "$(dpkg --audit 2>/dev/null)" ]]; then
    warn "偵測到既有的 dpkg 中斷狀態，先修復"
    dpkg --audit 2>/dev/null | head -20
    DEBIAN_FRONTEND=noninteractive dpkg --configure -a || \
      die "dpkg --configure -a 失敗，請人工檢查後再執行本腳本"
    ok "dpkg 狀態已修復"
  fi
  info "安裝：${MISSING[*]}"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}"; then
    warn "安裝失敗，嘗試 apt-get -f install 後重試"
    DEBIAN_FRONTEND=noninteractive dpkg --configure -a || true
    DEBIAN_FRONTEND=noninteractive apt-get -f install -y -qq || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}" \
      || die "套件安裝仍失敗，請人工檢查 apt 狀態"
  fi
  ok "套件安裝完成"
else
  ok "套件已齊全"
fi
systemctl enable --now libvirtd >/dev/null 2>&1 || true
systemctl is-active --quiet libvirtd && ok "libvirtd 執行中" || die "libvirtd 未啟動"
for g in libvirt kvm; do
  getent group "$g" >/dev/null && usermod -aG "$g" "$CALLER" || true
done
ok "已將 ${CALLER} 加入 libvirt / kvm 群組（需重新登入生效）"

# ---------------------------------------------------------------------------
# 2. 建立兩個虛擬網路（對齊教材網段）
# ---------------------------------------------------------------------------
echo
echo "=== 2. 虛擬網路 ==="
define_net() {
  local name="$1" xml="$2"
  if virsh net-info "$name" >/dev/null 2>&1; then
    ok "$name 已存在"
  else
    echo "$xml" > "/tmp/${name}.xml"
    virsh net-define "/tmp/${name}.xml" >/dev/null
    rm -f "/tmp/${name}.xml"
    ok "$name 已定義"
  fi
  virsh net-start "$name" >/dev/null 2>&1 || true
  virsh net-autostart "$name" >/dev/null 2>&1 || true
}

# NAT：對外連線用（教材 p.30 的 vmnet8 等價物），閘道 .2 與教材一致
define_net "$NAT_NET" "<network>
  <name>${NAT_NET}</name>
  <forward mode='nat'/>
  <bridge name='virbr-v2xnat' stp='on' delay='0'/>
  <ip address='${NAT_CIDR}.2' netmask='255.255.255.0'>
    <dhcp><range start='${NAT_CIDR}.128' end='${NAT_CIDR}.200'/></dhcp>
  </ip>
</network>"

# Host-only：VM 之間與 host 互通，不對外（教材 p.30 的 vmnet1 等價物）
# 不開 DHCP，位址由 cloud-init 寫死，確保每次開機都是 .131 / .132
define_net "$HO_NET" "<network>
  <name>${HO_NET}</name>
  <bridge name='virbr-v2xho' stp='on' delay='0'/>
  <ip address='${HO_CIDR}.1' netmask='255.255.255.0'/>
</network>"

# ---------------------------------------------------------------------------
# 3. 取得 Ubuntu 22.04 cloud image
# ---------------------------------------------------------------------------
echo
echo "=== 3. Ubuntu 22.04 cloud image ==="
if [[ -f "$BASE" ]]; then
  ok "已存在 $BASE（$(du -h "$BASE" | cut -f1)）"
else
  info "下載中（約 700 MB）…"
  curl -fL --progress-bar -o "${BASE}.part" "$IMG_URL"
  mv "${BASE}.part" "$BASE"
  ok "下載完成"
fi

# ---------------------------------------------------------------------------
# 4. 收集 SSH 公鑰
# ---------------------------------------------------------------------------
echo
echo "=== 4. SSH 金鑰 ==="
KEYS=""
if [[ -f "${CALLER_HOME}/.ssh/authorized_keys" ]]; then
  KEYS=$(grep -E '^(ssh|ecdsa)' "${CALLER_HOME}/.ssh/authorized_keys" || true)
fi
# 讓實體主機自己也能免密進 VM
if [[ ! -f "${CALLER_HOME}/.ssh/id_ed25519" ]]; then
  sudo -u "$CALLER" ssh-keygen -t ed25519 -N "" -q \
       -f "${CALLER_HOME}/.ssh/id_ed25519" -C "${CALLER}@$(hostname)"
  ok "已為 ${CALLER} 產生 ed25519 金鑰"
fi
KEYS="${KEYS}
$(cat "${CALLER_HOME}/.ssh/id_ed25519.pub")"
KEY_COUNT=$(echo "$KEYS" | grep -cE '^(ssh|ecdsa)' || echo 0)
[[ "$KEY_COUNT" -ge 1 ]] || die "找不到任何公鑰，VM 建起來會進不去"
ok "將寫入 ${KEY_COUNT} 把公鑰"

# ---------------------------------------------------------------------------
# 5. 建立 VM
# ---------------------------------------------------------------------------
echo
echo "=== 5. 建立 VM ==="
for entry in "${VMS[@]}"; do
  IFS='|' read -r NAME HO_IP VCPU RAM DISK MAC_NAT MAC_HO <<< "$entry"
  echo "--- ${NAME}"
  if virsh dominfo "$NAME" >/dev/null 2>&1; then
    ok "已存在，略過（要重建請先跑 --destroy）"
    continue
  fi

  DISK_IMG="${POOL}/${NAME}.qcow2"
  SEED_IMG="${POOL}/${NAME}-seed.iso"
  qemu-img create -q -f qcow2 -F qcow2 -b "$BASE" "$DISK_IMG" "${DISK}G"
  ok "磁碟 ${DISK}G（以 base image 為後端，省空間）"

  WORK=$(mktemp -d)
  # cloud-init user-data
  cat > "${WORK}/user-data" <<EOF
#cloud-config
hostname: ${NAME}
fqdn: ${NAME}
manage_etc_hosts: true
users:
  - name: ${VM_USER}
    groups: [sudo, adm]
    shell: /bin/bash
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    lock_passwd: true
    ssh_authorized_keys:
$(echo "$KEYS" | grep -E '^(ssh|ecdsa)' | sed 's/^/      - /')
ssh_pwauth: false
package_update: true
packages:
  - build-essential
  - git
  - curl
  - python3-venv
  - python3-pip
write_files:
  - path: /etc/profile.d/v2x-env.sh
    content: |
      # SPEC §3.1 的固定位址，供教材指令直接引用
      export N6_APP=172.16.6.10
      export N6_GW=172.16.6.100
runcmd:
  - [ systemctl, enable, --now, qemu-guest-agent ]
EOF

  # cloud-init network-config：把介面名強制設成教材用的 ens33 / ens36，
  # 這樣教材的指令可以逐字照抄，不必逐條改介面名。
  cat > "${WORK}/network-config" <<EOF
version: 2
ethernets:
  ens33:
    match: {macaddress: "${MAC_NAT}"}
    set-name: ens33
    dhcp4: true
  ens36:
    match: {macaddress: "${MAC_HO}"}
    set-name: ens36
    dhcp4: false
    addresses: [${HO_IP}/24]
EOF

  cloud-localds -N "${WORK}/network-config" "$SEED_IMG" "${WORK}/user-data"
  rm -rf "$WORK"
  ok "cloud-init seed 已產生"

  virt-install \
    --name "$NAME" \
    --memory "$RAM" --vcpus "$VCPU" \
    --cpu host-passthrough \
    --disk "path=${DISK_IMG},format=qcow2,bus=virtio" \
    --disk "path=${SEED_IMG},device=cdrom" \
    --network "network=${NAT_NET},mac=${MAC_NAT},model=virtio" \
    --network "network=${HO_NET},mac=${MAC_HO},model=virtio" \
    --os-variant ubuntu22.04 \
    --graphics none --console pty,target_type=serial \
    --import --noautoconsole >/dev/null
  virsh autostart "$NAME" >/dev/null 2>&1 || true
  ok "已建立並啟動（host-only ${HO_IP}）"
done

# ---------------------------------------------------------------------------
# 6. 等待開機
# ---------------------------------------------------------------------------
echo
echo "=== 6. 等待 cloud-init 完成 ==="
for entry in "${VMS[@]}"; do
  IFS='|' read -r NAME HO_IP _ <<< "$entry"
  printf "  %-14s " "$NAME"
  for i in $(seq 1 90); do
    if ssh -o BatchMode=yes -o StrictHostKeyChecking=no \
           -o UserKnownHostsFile=/dev/null -o ConnectTimeout=3 \
           -i "${CALLER_HOME}/.ssh/id_ed25519" \
           "${VM_USER}@${HO_IP}" 'cloud-init status --wait >/dev/null 2>&1; true' \
           >/dev/null 2>&1; then
      echo "就緒"
      break
    fi
    [[ $i -eq 90 ]] && echo "逾時（VM 可能仍在開機，稍後用 --status 再看）"
    sleep 5
  done
done

echo
echo "============================================================"
virsh list --all
echo
echo "從實體主機連進 VM："
for entry in "${VMS[@]}"; do
  IFS='|' read -r NAME HO_IP _ <<< "$entry"
  echo "  ssh ${VM_USER}@${HO_IP}      # ${NAME}"
done
echo
echo "下一步："
echo "  1. 確認兩台互通：ssh ${VM_USER}@${HO_CIDR}.131 ping -c2 ${HO_CIDR}.132"
echo "  2. 把實際的介面名與 IP 填進 docs/platform-notes.md §2"
echo "  3. 取得 free5GC / gtp5g / UERANSIM / OAI-MEP（教材假設映像檔已內含，"
echo "     我們沒有映像檔，需向平台團隊索取或照官方文件安裝）"
