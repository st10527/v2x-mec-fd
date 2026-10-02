# 平台教材重點摘要與環境差異記錄

**教材來源**：《20260804_行動通訊競賽_智慧數位應用組-5G-V2X 平台_上課教材》（71 頁）
講者：國立中興大學電機系 曾柏諺 助教（bytseng@smail.nchu.edu.tw）

SPEC §15.7：教材中的指令已驗證可用，直接沿用，不要自行改寫成其他寫法。
本檔記錄兩件事：**教材指令的抄錄**，以及**實際環境與教材不同之處**。
決賽繳交的設計與測試文件會直接引用這裡。

---

## 0. 教材與本專案的對應

| 教材章節 | 頁次 | 本專案對應 |
|---|---|---|
| 2c Linux 環境清理（iptables） | p.34 | `deploy/setup_vm1.sh` §6 的冪等刪除 |
| 3a OAI-MEP 啟動與驗證 | p.35–37 | `deploy/setup_vm1.sh` §4–5 |
| 3b 建立 MEC Data Network 網段 | p.39–40 | `deploy/setup_vm1.sh` §3 |
| 3c MEP Gateway (Kong) | p.45–48 | `deploy/kong_routes.sh` |
| 4a N6 流量轉發（DNAT） | p.49–52 | `deploy/setup_vm1.sh` §6 |
| 4b free5GC 部署 | p.53–54 | `deploy/setup_vm1.sh` §2、§8 |
| 4c UERANSIM 啟動 | p.55–57 | `deploy/setup_vm2.sh` §2–3 |
| 5a 端到端服務鏈驗證 | p.58–60 | `deploy/setup_vm2.sh --verify` |
| 5b V2X 碰撞預警部署 | p.61–62 | `mec_app/`、`deploy/kong_routes.sh` |
| 5c SUMO 整合與實測 | p.63–66 | `ue/sumo_ue_sender.py` |
| 5d 外網 (Cloud) 測試與比較 | p.68–70 | `deploy/setup_vm1.sh --cloud-mode` |

---

## 1. 教材的關鍵固定值

這些值 SPEC §3.1 / §3.3 已採用，兩邊一致。

| 項目 | 值 | 出處 |
|---|---|---|
| VM1 host-only | 192.168.113.131 | p.32 |
| VM2 host-only | 192.168.113.132 | p.33 |
| NAT 網段 | 172.16.211.0/24 | p.29–30 |
| Host-only 網段 | 192.168.113.0/24 | p.29–30 |
| MEC App 綁定（N6） | 172.16.6.10 | p.39 |
| MEP Gateway 入口（N6） | 172.16.6.100 | p.39 |
| **UE 網段** | **10.60.0.0/16**（uesimtun0 = 10.60.0.1） | p.51、p.57 |
| **oai-mep docker bridge** | **172.29.248.0/24** | p.51 |
| NAT 網卡 / Host-only 網卡 | ens33 / ens36 | p.31 |
| free5GC 版本 | v4.1.0 | p.54 |
| UERANSIM 版本 | v3.2.7 | p.55 |
| SUMO 版本 | 1.27.0 | p.63 |
| Kong 版本 | 3.0.2 | p.47 |
| **平台 Cloud server** | **http://140.120.108.48:18080** | p.69 |

> **注意**：p.31 寫 host-only 是 `ens36`，但 p.39 的 `ip a` 實際輸出顯示的是 `ens34`。
> 教材本身不一致，以**實機為準**，確認後填入下方 §2 的表格。

---

## 2. 前置確認（SPEC §3.1，D1 第一件事）

```bash
grep -cw vmx /proc/cpuinfo
ls -l /dev/kvm
lsmod | grep gtp
```

### 實體主機（<實驗主機>，<主機名稱>）— 已於 115/09/22 實測

| 檢查項 | 期望 | 實際 | 判定 |
|---|---|---|---|
| `grep -cw vmx /proc/cpuinfo` | > 0 | **32** | 通過 |
| `/dev/kvm` 存在 | 是 | **存在**（crw-rw----+ root:kvm） | 通過 |
| `lsmod \| grep gtp` | 有 gtp5g | 空 | 待安裝 |
| CPU / RAM / 空間 | 8 核 / 32 GB | **16 核 / 31 GB / 315 GB 可用** | 優於規格 |
| OS | Ubuntu 22.04.5（教材） | **Ubuntu 24.04，kernel 6.17.0-35** | **差異，見 §6** |

**結論：巢狀虛擬化可用，SPEC §3.1 的雙 VM 主方案成立，不需走單機降級。**

> 實體主機上原有一組 CUPS 套件卡在升級到一半（`libcups2t64` 停在 7.9、其餘元件在 7.13），
> dpkg 因此處於中斷狀態，任何 `apt-get install` 都會被擋。這是我們接手前就存在的狀況，
> 已用 `dpkg --configure -a` + `apt-get -f install` 修復（全部對齊 7.14）。
> `deploy/provision_vms.sh` 已內建此前置修復。

### VM 規格與網路實況 — 已於 115/09/23 建置完成並實測

以 `deploy/provision_vms.sh`（KVM/libvirt + cloud image + cloud-init）建立。

| 項目 | SPEC / 教材 | 實際 | 判定 |
|---|---|---|---|
| VM1 名稱 / OS | Ubuntu 22.04.5 | `vm1-free5gc`，**Ubuntu 22.04.5 LTS** | 對齊 |
| VM2 名稱 / OS | Ubuntu 22.04.5 | `vm2-ueransim`，**Ubuntu 22.04.5 LTS** | 對齊 |
| Guest kernel | 教材為 6.8.0-100（HWE） | **5.15.0-191（GA）** | 差異，但在 gtp5g 支援範圍（5.4–7.0.x）內 |
| 每台 vCPU / RAM / 磁碟 | 4 核 / 8 GB / 40 GB | 4 核 / 8 GB / 40 GB（39 G 可用 37 G） | 對齊 |
| VM1 NAT (ens33) | 172.16.211.0/24 | **172.16.211.193**，閘道 172.16.211.2 | 對齊 |
| VM1 Host-only (ens36) | 192.168.113.131 | **192.168.113.131** | 對齊 |
| VM2 NAT (ens33) | 172.16.211.0/24 | **172.16.211.165**，閘道 172.16.211.2 | 對齊 |
| VM2 Host-only (ens36) | 192.168.113.132 | **192.168.113.132** | 對齊 |
| 介面命名 | ens33 / ens36 | **ens33 / ens36** | 以 netplan `set-name` 強制對齊，教材指令可逐字照抄 |
| UE tun | uesimtun0 → 10.60.0.1 | **uesimtun0 → 10.60.0.x**（每次 PDU session 遞增） | 對齊 |
| UPF GTP 介面名（DNAT `-i`） | upfgtp | **upfgtp** | 對齊，教材的 DNAT 指令可直接沿用 |

連通性實測：VM1 ↔ VM2 host-only 互通；兩台經 NAT 可達外網與 DNS。

> §1 提到教材 p.31 與 p.39 對 host-only 介面名（ens36 / ens34）自相矛盾。
> 我們以 netplan 的 `match: macaddress` + `set-name` 把兩台都強制命名為
> **ens33（NAT）/ ens36（host-only）**，與教材 p.31 一致，此爭議就此消除。
>
> 另註：DNAT 規則用的 `-i upfgtp` 是 gtp5g 建立的 GTP 隧道介面，**不是實體網卡**，
> 不受虛擬化平台的網卡命名影響，教材該條指令可直接沿用。

### 存取方式

| 來源 | 指令 |
|---|---|
| 開發機 → 實體主機 | `ssh <實驗主機別名>` |
| 開發機 → VM1 / VM2 | `ssh v2x-vm1` / `ssh v2x-vm2`（經實體主機 ProxyJump） |
| 實體主機 → VM | `ssh v2x@192.168.113.131` / `.132` |

VM 內的 `v2x` 使用者具免密 sudo；實體主機的 `<主機帳號>` 已加入 `libvirt` / `kvm` 群組，
管理 VM 不需 sudo（須重新登入後生效，已確認）。

### 快照（回滾點）

| 快照名 | VM | 內容 | 建立日期 |
|---|---|---|---|
| `clean-22045` | 兩台 | 乾淨的 Ubuntu 22.04.5，cloud-init 完成，尚未裝任何 5G 元件 | 115/09/23 |
| `after-ueransim` | VM2 | UERANSIM v3.2.7 已建置 | 115/09/23 |
| `after-free5gc` | VM1 | gtp5g + Go + MongoDB + free5GC v4.1.0 已建置，尚未設定與啟動 | 115/09/23 |
| `after-oai-mep` | VM1 | 完整堆疊已建置：再加 Docker 29.8.1 + OAI-MEP（Kong 3.0.2 執行中），尚未設定 | 115/09/23 |
| `ue-registered` | 兩台 | UE 註冊成功、PDU session 建立、`uesimtun0` 上線 | 115/09/23 |
| `e2e-working` | 兩台 | **端到端打通**：UE→gNB→GTP-U→UPF→DNAT→Kong→MEC App，BSM 推論回應正常 | 115/09/23 |

```bash
bash deploy/provision_vms.sh --snapshot after-gtp5g   # 兩台一起做快照
bash deploy/provision_vms.sh --revert  clean-22045    # 兩台一起還原
bash deploy/provision_vms.sh --status                 # 看 VM / 網路 / 快照
```

**建議每個里程碑前先做一次快照**：裝完 gtp5g、跑通 free5GC、跑通 UERANSIM 各一次。
設定弄壞時幾秒還原，不必重裝——離初賽只剩 13 天，這個保險值得。

---

## 3. OAI-MEP 與 Kong

### 啟動（教材 p.35）

```bash
cd ~/oai-mep
docker compose -f ci-scripts/docker-compose.yaml up -d
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
sudo ufw disable
```

啟動後應有 5 個 container：`oai-mep`、`oai-mep-gateway`、`oai-mep-gateway-db`、
`oai-mep-registry`、`oai-registry-db`。

### 取得 Kong Admin port 與 container IP（教材 p.36–37，port 每次不同）

```bash
ADMIN_PORT=$(docker port oai-mep-gateway 8001/tcp | head -n1 | sed 's/.*://')
KONG_IP=$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' oai-mep-gateway)
```

| 項目 | 教材示範值 | 本次實測（115/09/23） |
|---|---|---|
| Kong Admin host port | 32772 / 32774 | **32774** |
| Kong Proxy host port | 32771 | **32773** |
| Kong container IP | 172.29.248.3 | **172.29.248.3** |
| Kong 版本 | 3.0.2 | **3.0.2** |
| Service Registry container IP | — | **172.29.248.6**（`oai-mep-registry`） |

> host port 由 docker 動態分配，**每次重建 container 都會變**，不可寫死。
> 一律用教材 p.36 的方式即時取得：
> `ADMIN_PORT=$(docker port oai-mep-gateway 8001/tcp | head -n1 | sed 's/.*://')`
>
> 實際起來的 5 個 container 與教材 p.35 一致：
> `oai-mep-gateway`、`oai-mep-gateway-db`、`oai-mep-registry`、`oai-registry-db`、
> `migeration-tmp`（Kong 的一次性 migration，跑完即停）。

### 建立 Service / Route（教材 p.46、p.62）

教材的寫法，`deploy/kong_routes.sh` 逐條照抄：

```bash
curl -i -X POST http://127.0.0.1:${ADMIN_PORT}/services \
     --data name=mec-health --data url='http://172.16.6.10:5000'
curl -i -X POST http://127.0.0.1:${ADMIN_PORT}/services/mec-health/routes \
     --data 'paths[]=/mec/health' --data strip_path=true
```

Service 已存在會回 HTTP 409（可改用 PATCH）；建立成功回 201。
`deploy/kong_routes.sh` 改以「先 DELETE 再 POST」達成冪等。

本專案要建的六條見 SPEC §3.3：

| Kong Route | 上游 | 建立狀態 | 驗證 |
|---|---|---|---|
| `/mec/health` | 172.16.6.10:5000 | **已建立** | |
| `/mec/v2x/a` | 172.16.6.10:5002 | **已建立** | UE 實測回 **200** |
| `/mec/v2x/b` | 172.16.6.10:5003 | **已建立** | UE 實測回 **200** |
| `/mec/fd/a` | 172.16.6.10:5002 | **已建立** | |
| `/mec/fd/b` | 172.16.6.10:5003 | **已建立** | |
| `/mec/dash` | 172.16.6.10:5010 | **已建立** | |

Kong 上另有一條 OAI-MEP 自帶的 `/service_registry` 路由（未命名），
那是 ETSI MEC 011 的 Discovery and Service Registry，SPEC §7.2 的 FD 服務發現要用它。

---

## 4. N6 網段與 DNAT

### N6 位址（教材 p.39–40）

```bash
sudo ip addr add 172.16.6.10/24  dev lo 2>/dev/null || true
sudo ip addr add 172.16.6.100/24 dev lo 2>/dev/null || true
ip a show dev lo | egrep '172\.16\.6\.10|172\.16\.6\.100'
```

### DNAT（教材 p.50）

```bash
sudo sysctl -w net.ipv4.ip_forward=1 >/dev/null
sudo iptables -t nat -I PREROUTING 1 -i upfgtp -d 172.16.6.100/32 \
     -p tcp --dport 80 -j DNAT --to-destination ${KONG_IP}:80
```

### DOCKER-USER 放行（教材 p.51）

UE 網段 `10.60.0.0/16` 與 oai-mep bridge `172.29.248.0/24` 互通：

```bash
sudo iptables -C DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  sudo iptables -I DOCKER-USER 1 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
sudo iptables -C DOCKER-USER -s 10.60.0.0/16 -d 172.29.248.0/24 -j ACCEPT 2>/dev/null || \
  sudo iptables -I DOCKER-USER 2 -s 10.60.0.0/16 -d 172.29.248.0/24 -j ACCEPT
sudo iptables -C DOCKER-USER -s 172.29.248.0/24 -d 10.60.0.0/16 -j ACCEPT 2>/dev/null || \
  sudo iptables -I DOCKER-USER 3 -s 172.29.248.0/24 -d 10.60.0.0/16 -j ACCEPT
sudo iptables -C DOCKER-USER -j RETURN 2>/dev/null || sudo iptables -A DOCKER-USER -j RETURN
```

### 規則清理（教材 p.34）

```bash
sudo iptables -t nat -L PREROUTING -n --line-numbers
sudo iptables -t nat -D PREROUTING <行號>
```

> 教材警告：**不要直接 `iptables -t nat -F`**，會把 docker 內部的規則一併刪掉。

---

## 5. free5GC / UERANSIM / 端到端驗證

### gtp5g（教材 p.53）

```bash
lsmod | grep gtp          # 有輸出即可；空白則執行下面三行
cd ~/gtp5g && make clean && make && sudo make install
```

### free5GC（教材 p.54，VM1 前景執行）

```bash
cd ~/free5gc && sudo ./run.sh
```

### UERANSIM（教材 p.55，VM2，各佔一個終端機）

```bash
cd ~/UERANSIM && sudo ./build/nr-gnb -c config/free5gc-gnb.yaml
cd ~/UERANSIM && sudo ./build/nr-ue  -c config/free5gc-ue.yaml
ip a | grep -A2 uesimtun0        # 應看到 10.60.0.1/24
```

成功訊號：gNB 側 `NG Setup procedure is successful`；
UE 側 `PDU Session establishment is successful, TUN interface[uesimtun0, 10.60.0.1] is up`。

### 端到端驗證（教材 p.58）

```bash
# 驗證 1：UE 直打 MEC App
curl -m 3 --interface uesimtun0 http://172.16.6.10:5000/healthz
# 驗證 2：UE 經 MEP Gateway
curl -m 3 --interface uesimtun0 http://172.16.6.100/mec/health/healthz
```

成功路徑（教材 p.60）：
`UE → 172.16.6.100:80/mec/… → iptables DNAT → Kong container:80 → MEC App (172.16.6.10:5000)`

| 項目 | 日期 | 結果 |
|---|---|---|
| UE 註冊 + PDU session | 115/09/23 | **成功**，`uesimtun0` 取得 10.60.0.x |
| UE → MEP Gateway → MEC App A / B | 115/09/23 | **HTTP 200** |
| UE → Gateway → `POST /bsm`（SPEC §5.1 真實訊息） | 115/09/23 | **成功**，回傳 risk_pred / risk_rule 與 §8.2 的四個時間戳 |

實測回應範例（`/mec/v2x/a/bsm`）：

```json
{"msg_id":"test-001","node":"A","vehicle_id":"veh_0001",
 "risk_pred":0,"risk_rule":0,"rel_dist":100.0,"model_ready":false,
 "ts_recv":...,"ts_window_ready":...,"ts_infer_done":...,"ts_resp":...}
```

`model_ready:false` 是正確的：權重尚未訓練（D10 才做），此時只有規則基準線可信。

---

## 6. 本專案環境與教材的差異（重要）

| 項目 | 教材 | 我們的實體主機 | 影響 |
|---|---|---|---|
| 虛擬化軟體 | VMware Workstation | 無（KVM 可用，但未裝 libvirt） | 需自行決定 VM 方案 |
| VM 映像檔 | 培訓營提供，內含 `~/oai-mep`、`~/free5gc`、`~/gtp5g`、`~/UERANSIM`、SUMO | **全部不存在** | 見下方「未解項目」 |
| Host OS | Ubuntu 22.04.5，kernel 6.8.0 | Ubuntu 24.04，kernel 6.17.0-35 | 見下方「kernel 相容性」 |

### 未解項目

1. ~~缺培訓營 VM 映像檔~~ → **已處置：改為自行建置（SPEC §15.3 / v1.3 決策）**

   教材 p.35 起的指令都假設 `~/oai-mep`、`~/free5gc`、`~/gtp5g`、`~/UERANSIM`
   已存在，那是培訓營映像檔的內容，我們沒有。團隊決定**不向平台團隊索取映像檔，
   改為從上游原始碼自行建置**，版本鎖定於 `deploy/versions.env`。

   理由（SPEC §15.7 v1.3）：決賽須繳交完整程式碼供評審辨識原創性，
   「能從零重現」本身就是證據；環境壞掉可一行指令重建；本專案是雙 MEC 節點 +
   聯邦蒸餾，教材的單一 App 示範不涵蓋，設定本來就得自己寫。

   **界線**：元件本身仍是平台指定的上游專案（free5GC / UERANSIM / OAI-MEP），
   不自行實作。平台關聯性佔初賽 40%，且 §2 Q6 依賴 OAI-MEP 的
   Service Registry 與 MEP Gateway。

2. **kernel 相容性：查證後確認不是問題。**
   gtp5g 官方說明（github.com/free5gc/gtp5g，115/09/22 查閱）指出支援
   **kernel 5.4（Ubuntu 20.04）到 7.0.x**，並內含 6.18 與 7.0 的相容性 patch；
   7.1 以上尚未測試。實體主機的 6.17 落在支援範圍內。

   > **更正備查**：本檔先前版本曾寫「6.17 很可能編不過」，那是未查證的推測，
   > 與官方說明不符，已更正。Linux 對 userspace 保證向下相容、對 kernel module
   > 不保證，這個結構性事實仍然成立，但不足以推論 gtp5g 在 6.17 上會失敗。

### 建 VM 而非裸機直裝的理由（決策紀錄）

核心版本的疑慮排除後，選擇 VM 的理由是下列四點，與 kernel 無關：

1. **SPEC §3.1 的拓樸需要兩台機器**（VM1 核網+MEC、VM2 UE+SUMO），裸機只有一台；
2. **實體主機是共用實驗機**，上面有 RLNC_Edge、AutoResearchClaw、EDGE_2026、
   TMC_2026 等其他專案。教材要求 `sudo ufw disable`、改 `net.ipv4.ip_forward`、
   插 iptables DNAT、載入 kernel module，都是全機層級改動，不應落在別人的專案上；
3. **快照可回滾**，核網設定弄壞時幾秒還原，對 14 天的時程是實質保險；
4. **對齊教材環境**（Ubuntu 22.04.5 + free5GC v4.1.0 驗證過的 Go / 函式庫組合），
   SPEC §15.7「教材指令原樣沿用」才站得住。

實測巢狀虛擬化可用（§2），故採 SPEC §3.1 的雙 VM 主方案，不走 §12 的單機降級。
佈建腳本：`deploy/provision_vms.sh`（KVM/libvirt + cloud image + cloud-init）。

### VM 佈建的設計選擇

| 選擇 | 理由 |
|---|---|
| cloud image + cloud-init，不用 ISO 安裝精靈 | 全程無互動、可重複執行，符合 SPEC §9「一行指令還原」 |
| netplan 以 MAC 比對並 `set-name: ens33/ens36` | 強制介面名與教材一致，教材指令可逐字照抄 |
| host-only 網路不開 DHCP，IP 由 cloud-init 寫死 | 每次開機固定 .131 / .132，不會因租約變動 |
| qcow2 以 base image 為後端 | 兩台 VM 共用 base，省空間也加快建立 |

> 註：DNAT 規則用的 `-i upfgtp` 是 gtp5g 建立的 GTP 隧道介面，**不是實體網卡**，
> 因此不受虛擬化平台的網卡命名差異影響，教材該條指令可直接沿用。

---

## 6b. 自建的元件與版本（115/09/23 起）

版本鎖定於 `deploy/versions.env`，每支 `install_*.sh` 都讀它。
改版本只需改一處，再重跑對應腳本。

| 元件 | 版本 | 腳本 | VM | 狀態 |
|---|---|---|---|---|
| gtp5g（GTP-U kernel module） | `v0.9.16` | `install_gtp5g.sh` | VM1 | **完成**，commit `8d723c2` |
| Go | `1.24.5` | `install_go.sh` | VM1 | **完成** |
| MongoDB | `7.0`（實裝 7.0.43） | `install_free5gc.sh` §2 | VM1 | **完成** |
| free5GC | `v4.1.0` | `install_free5gc.sh` | VM1 | **完成**，commit `de6bdb7`，13 個 NF |
| UERANSIM | `v3.2.7` | `install_ueransim.sh` | VM2 | **完成**，commit `1d1e154` |
| Docker | 29.8.1（上游 repo） | `install_oai_mep.sh` §1 | VM1 | **完成** |
| OAI-MEP | `master` commit `2881cad` | `install_oai_mep.sh` | VM1 | **完成**，5 container 執行中 |

free5GC v4.1.0 建出的 13 個 NF：
`amf ausf chf n3iwf nef nrf nssf pcf smf tngf udm udr upf`

### 版本選擇的依據

| 元件 | 為什麼是這個版本 |
|---|---|
| free5GC v4.1.0 | 與教材環境一致（教材 p.54 截圖：v4.1.0 / go1.24.5），平台團隊驗證過的組合 |
| Go 1.24.5 | free5GC v4.1.0 的 release note 載明升級至 Go 1.24；Ubuntu 22.04 的 apt 只有 1.18，必須從上游裝 |
| gtp5g v0.9.16 | 0.9.x 線最後一版，與 free5GC v4.1.0 同期，netlink API 相容性最穩。**已實測在 kernel 5.15.0-191 上編譯並載入成功**。若 UPF 啟動時抱怨版本，改試 v0.10.2 並記錄於 §7 |
| UERANSIM v3.2.7 | 與教材一致（教材 p.55 截圖） |
| MongoDB 7.0 | Ubuntu 22.04 官方庫已不含 mongodb，須加上游 repo；7.0 為 jammy 上的穩定線 |

### gtp5g 實測結果（VM1，115/09/23）

```
kernel      5.15.0-191-generic
gtp5g.ko    8.8 MB
模組版本     0.9.16
開機自動載入 /etc/modules-load.d/gtp5g.conf
```

編譯過程只有一則無害警告（`Skipping BTF generation ... due to unavailability of vmlinux`），
那是 guest 沒裝 debug 符號所致，不影響模組運作。

### OAI-MEP 的 compose 內容（實際確認）

| 服務 | 映像 |
|---|---|
| `oai-mep-gateway` | `kong:3.0-alpine` |
| `kong-migration` | `kong:3.0-alpine` |
| `oai-mep-gateway-db` | `postgres:9.6` |
| `oai-mep-registry` | `oaisoftwarealliance/oai-mep:latest` |
| `mongodb` | `mongo:latest` |

docker 網段 **`172.29.248.0/24`**（bridge 名 `oai-mep`），與教材 p.51 的
DOCKER-USER 規則所用網段一致 —— 那條規則可直接沿用。

repo 路徑：`https://gitlab.eurecom.fr/oai/orchestration/oai-mec/oai-mep.git`
（注意比直覺多一層 `oai-mec`）。

---

## 6c. SUMO（VM2，115/09/24）

以 pip 安裝 `eclipse-sumo==1.27.0`（與學生端相同，見 `requirements-student.txt`），
不走 apt / PPA——PPA 只給最新版，無法鎖定教材指定的 1.27.0。

| 項目 | 值 |
|---|---|
| sumo / netconvert | 1.27.0 |
| sumo-data | **1.27.0**（必須鎖定，見 §7） |
| 位置 | `~/v2x-mec-fd/.venv`（`SUMO_HOME` 由套件提供） |
| 系統相依 | `libxrender1`、`libgl1`（無桌面的 cloud image 缺，連非 GUI 的 `sumo` 都需要） |

**VM 經 NAT 下載 PyPI 只有約 50 KB/s**，138 MB 的 wheel 要等 45 分鐘以上。
做法：在開發機以 `pip download --platform manylinux2014_x86_64 --python-version 3.10` 取得
所有 wheel，rsync 到 VM2 的 `~/wheels/`，再 `pip install --no-index --find-links ~/wheels`。

### 實測效能（3600 秒，VM2：4 vCPU / 8 GB）

| 場景 | collect 耗時 | collect 峰值記憶體 | build 耗時 | build 峰值記憶體 |
|---|---|---|---|---|
| B | 21 s | 53 MB | 12 s | 274 MB |
| 代理 | 29 s | 223 MB | 20 s | 351 MB |
| A | 372 s | **4.2 GB** | 426 s | 1.0 GB |

路口 A 的 4.2 GB 全部來自 SSM 追蹤「車與車的相遇」（關掉 SSM 只剩 53 MB / 9 秒）。
A 是飽和路口，排隊車彼此都在 50 m 偵測範圍內，相遇數隨車數平方成長。
偵測範圍降到 30 m 可降到 2.7 GB，但交叉衝突在 14 m/s、3 秒內可能發生在 40 m 外，
故維持 50 m，改在學生手冊註明記憶體需求。

### 跨平台可重現性

路口 B 600 秒在 macOS（Apple Silicon）與 VM2（Linux x86_64）上產出**完全相同**的結果
（82,160 筆狀態、危險交叉 42 筆 / 75%）。學生在自己電腦重跑，應與參考資料一致。

---

## 7. 踩雷與解法

| 日期 | 現象 | 原因 | 解法 |
|---|---|---|---|
| 115/09/22 | `ssh-copy-id` 在輸入密碼後被 `Connection closed` | 它會先試 publickey 再試密碼，把伺服器的 `MaxAuthTries` 吃掉 | 加 `-o PubkeyAuthentication=no -o PreferredAuthentications=password` |
| 115/09/23 | 實體主機上任何 `apt-get install` 都被擋，報 `dpkg was interrupted` | 既有的 CUPS 套件升級到一半（`libcups2t64` 停在 7.9、其餘在 7.13），與我們無關 | `dpkg --configure -a` + `apt-get -f install`，全部對齊 7.14。已內建於 `provision_vms.sh` |
| 115/09/23 | OAI-MEP 的 git clone 404 | repo 路徑比直覺多一層：`oai/orchestration/**oai-mec**/oai-mep` | 修正 `versions.env` 的 `OAI_MEP_REPO` |
| 115/09/23 | UE 認證失敗 `Network failing the authentication check` → 實為 `SQN out of range` | free5GC 預設 SQN `16f3b3f70fc2` 遠大於全新 UE 的 SQN-MS=0，超出 3GPP 可接受視窗；free5GC 在此組合下**不處理** UE 送回的重新同步，AMF 一路 T3560 逾時 | 起始 SQN 改小。見下一列 |
| 115/09/23 | SQN 改成 0 仍失敗 | 3GPP 的 SQN = SEQ‖IND，UERANSIM 的 `indBitLen=5`（`src/ue/nas/usim/usim.cpp`），`checkSqn()` 要求 **SEQ 嚴格遞增**。free5GC 取用後 +1，首次送 SQN=1，其 SEQ=1>>5=**0**，不大於已記錄的 0 → 仍被拒 | 起始 SQN 設為 **`000000000020`**（0x20=32），首次送出時 SEQ=1 即可通過。已寫入 `configure_free5gc.sh` 並附完整推導 |
| 115/09/23 | 核網起來幾秒就整組消失，UE 只看到 `Registration Reject [CONGESTION]` | free5GC 的 `run.sh` 結尾是 `wait ${PID_LIST}`，陣列少了 `[@]`，只等第一個 PID；該行程一結束 run.sh 就 `exit 0`，systemd 把其餘 NF 全 SIGKILL | 改用自有啟動器 `deploy/free5gc_start.sh`（`wait -n "${PIDS[@]}"` + 訊號處理） |
| 115/09/23 | UPF 報 `open Gtp5g: open link: create: file exists` | `upfgtp` 介面由 UPF 建立，UPF 非正常結束時不會自己清掉，重啟時撞到殘留 | 啟動器在啟動 UPF 前先 `ip link del upfgtp` |
| 115/09/23 | **UE 註冊成功、PDU session 也成功，但資料完全不通**（VM1 的 ens36 上連一個 GTP-U 封包都沒有） | gNB 要把 GTP-U 送到哪，是 **SMF** 依 `smfcfg.yaml` 的 `userplaneInformation` 告知的，不是讀 `upfcfg.yaml`。只改 upfcfg 的話 gNB 仍被指向 `127.0.0.8` | `configure_free5gc.sh` 增加 §3b，一併改 smfcfg 的 N3 endpoint。**這是最難查的一個**：所有控制面訊號都成功，只有資料面靜默 |
| 115/09/24 | macOS 上 `netconvert` / `sumo-gui` 報 `Library not loaded: ... libgdal.39.3.13.0.dylib` | `eclipse-sumo==1.27.0` 宣告 `sumo-data>=1.27.0`，pip 自動裝了 1.27.1，但 1.27.0 的執行檔需要 1.27.0 附帶的函式庫版本。Linux wheel 自帶函式庫所以沒事，macOS 才中招；Windows 也有專屬 sumo-data wheel，視同有風險 | `requirements-student.txt` 鎖定 `sumo-data==1.27.0` |
| 115/09/24 | macOS 上 `sumo-gui` 報 `unable to open display :0.0` | SUMO 的 GUI 是 X11 程式（連結 libX11），macOS 不帶 X server | 裝 XQuartz 並登出再登入。**只影響看畫面**，收集與驗收都不需要 GUI |
| 115/09/24 | SUMO 拒絕讀 sumocfg：`not well-formed (invalid token)` | 在 XML 註解裡寫了 `--scenario`。**XML 規格禁止註解內出現 `--`** | 改寫註解；`tests/test_student_guide.py` 會掃描手冊中所有場景檔防止再犯 |
| 115/09/24 | `pkill -f "pip install"` 把自己的 shell 也殺掉 | pattern 出現在執行 pkill 的那個 ssh 指令字串裡，自己也被比對到 | 用 `pkill -f "[p]ip install"` 的字元類技巧避開自我比對 |
| _待填_ | | | |

卡關且自行無解時，平台團隊聯絡窗口：bytseng@smail.nchu.edu.tw（SPEC §12）。
