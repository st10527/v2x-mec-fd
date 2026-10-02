# 學生操作手冊

**多路口 V2X 協同危險預警系統：基於 MEC 邊緣軟標籤交換的跨路口風險預測**

2026 行動通訊實務競賽｜智慧數位應用組｜主軸一「智慧交通安全新生活」（5G-V2X 平台）

> 這份手冊是寫給你的。照著做，一步一步來，每一步都有「怎麼確認自己做對了」。
> 卡住了不要硬撐超過 30 分鐘——看第 12 章，還是不行就照第 0 章的方式問老師。

---

## 目錄

- [第 0 章　先看這一頁](#第-0-章先看這一頁)
- [第 1 章　這個專案在做什麼](#第-1-章這個專案在做什麼)
- [第 2 章　準備環境](#第-2-章準備環境)
- [第 3 章　SUMO 30 分鐘入門](#第-3-章sumo-30-分鐘入門)
- [第 4 章　建立三個場景](#第-4-章建立三個場景)
- [第 5 章　收集資料與調參](#第-5-章收集資料與調參)
- [第 6 章　建資料集](#第-6-章建資料集)
- [第 7 章　練習：重跑、改一個參數、講給別人聽](#第-7-章練習重跑改一個參數講給別人聽)
- [第 8 章　交付](#第-8-章交付)
- [第 9 章　初賽文件：你負責的部分](#第-9-章初賽文件你負責的部分)
- [第 10 章　時程](#第-10-章時程)
- [第 11 章　紅線](#第-11-章紅線)
- [第 12 章　錯誤排除](#第-12-章錯誤排除)
- [附錄 A　指令速查卡](#附錄-a指令速查卡)
- [附錄 B　SSM 衝突型態碼](#附錄-bssm-衝突型態碼)
- [附錄 C　名詞表](#附錄-c名詞表)

---

## 第 0 章　先看這一頁

### 你負責什麼

| 你負責 | 你**不用**碰 |
|---|---|
| 用 SUMO 建三個交通場景 | 5G 核心網路（free5GC）、基地台模擬（UERANSIM） |
| 調整駕駛參數，讓兩個路口的危險型態不一樣 | MEC 平台（OAI-MEP / Kong）、iptables |
| 跑模擬、收集資料、建訓練資料集 | 實驗室的兩台虛擬機 |
| 產生公共代理資料集 | 模型訓練、聯邦蒸餾 |
| 跑圖表、錄影片、寫企劃書的指定章節 | `config.yaml`、`mec_app/`、`training/` 裡的程式碼 |

右欄的東西老師會處理。你只需要**會解釋**它們在做什麼（第 1 章、第 7 章），因為決賽評審會當面問你。

### 你要交的東西

| # | 交付物 | 期限 | 為什麼急 |
|---|---|---|---|
| 1 | 三個場景 + 三個資料集 + 代理資料集（第 8 章的 zip） | **9/27（日）** | 老師 9/28 要開始訓練模型，你的資料是**關鍵路徑** |
| 2 | 調參紀錄 `docs/tuning_log.md` | 隨 #1 一起 | 企劃書要寫「怎麼調出來的」 |
| 3 | 三張圖表 | 10/1 | 企劃書與簡報要用 |
| 4 | 3 分鐘 demo 影片 | 10/3 | 初賽繳交項目 |
| 5 | 企劃書你負責的章節 | 10/4 | 初賽繳交項目 |
| 6 | 簡報 | 10/5 | 初賽繳交項目 |

**初賽截止：115/10/06（二）中午 12:00。** 老師當天上午送件，你的東西要在前一天全部到位。

### 卡住了怎麼問

問問題時一次給齊這三樣，老師才能馬上回你：

1. **你在第幾章第幾步**（例如「4.1 建路網」）
2. **你打了什麼指令**（整行複製貼上）
3. **完整的錯誤訊息**（截圖或整段複製，不要只貼最後一行）

---

## 第 1 章　這個專案在做什麼

### 1.1 一句話

讓每個路口旁邊的小型伺服器（MEC）**預測**未來 3 秒會不會出事，而且兩個路口可以**互相學習**、但不用把車輛的行車軌跡傳給對方。

### 1.2 現有做法的問題

競賽平台給所有隊伍的範例程式，用的是 **TTC 規則**：

> TTC（Time To Collision，碰撞前時間）＝ 與同車道前車的間距 ÷ 接近速度。
> 例如與前車保險桿相距 30 公尺、後車比前車快 10 m/s → TTC = 3 秒。
> 規則：TTC < 1.5 秒就警告「危險」。

這個做法有兩個根本問題：

1. **只能偵測，不能預測。** TTC 小於 1.5 秒時，危險已經在眼前了，留給駕駛反應的時間很少。
2. **路口的側向衝突算不準。** TTC 是為「同一條線上一前一後」設計的。路口的車是從側面來的、轉彎的，「距離 ÷ 速度」不再好用。

### 1.3 我們的做法

三個關鍵字：

| 關鍵字 | 白話 | 在系統裡的位置 |
|---|---|---|
| **預測模型** | 看過去 2 秒的車輛動態，預測**未來 3 秒**的危險程度（安全／注意／危險） | MEC App 裡的小型神經網路 |
| **MEC 邊緣運算** | 運算放在路口旁邊，不送到遠端的雲端，所以反應快 | 5G 核心網路旁的 MEC 平台 |
| **聯邦蒸餾** | 兩個路口不交換原始資料，只交換「對同一批題目的答案」，互相學習 | 兩個 MEC App 之間 |

「聯邦蒸餾」用比喻說明：

> 兩個學生各自在不同學校讀書（各自的路口資料），不能交換課本（隱私）。
> 但老師發給他們**同一份練習卷**（公共代理資料集），兩人各自作答後交換答案，
> 看到對方的答案時，就知道「喔，原來這題還可以這樣想」。
> 他們交換的只是答案，不是課本。

你要做的「公共代理資料集」，就是那份**同一份練習卷**。

### 1.4 系統架構與你的位置

```
            ┌──────────────── 你負責 ────────────────┐
            │                                        │
            │   SUMO 交通模擬（路口 A、路口 B）       │
            │         │                              │
            │         │ 每 0.1 秒：每台車的位置、速度、方向…
            │         ▼                              │
            │   資料集（訓練用）+ 公共代理資料集       │
            └─────────┬──────────────────────────────┘
                      │
      ┌───────────────┴──── 老師負責 ─────────────────────────┐
      │                                                        │
      │  UE（車上的 5G 裝置）                                   │
      │    │ 5G 無線                                            │
      │    ▼                                                    │
      │  基地台 ─► 5G 核心網路 ─► MEP Gateway ─┬─► MEC App A ◄─┐│
      │                                       │    （路口 A）  ││
      │                                       │               ││ 交換答案
      │                                       └─► MEC App B ◄─┘│ （logits）
      │                                            （路口 B）   │
      │                                               │         │
      │                                        回傳危險警告     │
      └────────────────────────────────────────────────────────┘
```

### 1.5 為什麼你的場景是整個專案的成敗關鍵

聯邦蒸餾要有效，**兩個路口的危險型態必須不一樣**。

如果兩個路口都是「追撞」為主，路口 A 的模型本來就懂路口 B 的狀況，互相學習學不到新東西 —— 實驗會顯示「協同訓練沒有幫助」，整個題目就不成立了。

所以規格書（SPEC §4.3）刻意把兩個路口設計成：

| | 路口 A | 路口 B |
|---|---|---|
| 樣子 | 號誌化十字路口，雙向各 2 車道 | 無號誌 T 字路口，單車道 |
| 號誌 | 有，一個週期 60 秒 | 沒有，支道要停車再開 |
| 車種 | 小客車 90%、大車 10% | 小客車 60%、機車 40% |
| 流量 | 每個方向每小時 1200 輛 | 每個方向每小時 500 輛 |
| **主要危險型態** | **追撞**（前後車撞上） | **側向**（支道車匯入、機車鑽行） |

**第 5 章的調參，目的就是讓這兩欄的最後一列成立。** 驗收工具會幫你檢查。

### 1.6 六個「為什麼」（決賽評審一定會問）

先有個印象就好，第 7 章會再練習。

<details>
<summary><b>Q1　為什麼需要預警？</b></summary>

路口事故佔比高。3GPP（制定 5G 標準的組織）的技術報告對安全相關 V2X 服務的延遲要求很嚴格，把資料送到遠端雲端處理來不及。
</details>

<details>
<summary><b>Q2　規則（TTC）不是就夠了嗎？為什麼要學習模型？</b></summary>

TTC 只能在危險**已經發生**時觸發，是「偵測」；我們的模型看過去 2 秒、預測未來 3 秒，是「預測」，能比規則更早示警。而且 TTC 對路口的側向衝突定義不良。
這可以量化：比較模型與規則「第一次示警的時間差」（平均提前預警時間）。
</details>

<details>
<summary><b>Q3　為什麼要兩個路口一起學？</b></summary>

每個路口的幾何、號誌、車種不同，危險型態不同。只用路口 A 資料訓練的模型，拿去路口 B 會失準。
這可以量化：拿 A 的模型去測 B 的資料，看分數（跨路口 macro-F1）。
</details>

<details>
<summary><b>Q4　那為什麼不把兩個路口的資料集中起來一起訓練？</b></summary>

三個理由：（a）每台車每 0.1 秒一筆，持續上傳頻寬成本高；（b）集中訓練再下發模型，更新週期長；（c）台灣個資法對車輛軌跡很敏感。
</details>

<details>
<summary><b>Q5　為什麼交換「答案」（logits），不交換整個模型（FedAvg）？</b></summary>

（a）省傳輸量：我們的模型有 48,419 個參數（約 189 KB），交換答案只要 2000 題 × 3 類 × 4 bytes ≈ 24 KB，**省約 8 倍**；（b）兩個路口可以用不同結構的模型，FedAvg 要求結構一樣。
</details>

<details>
<summary><b>Q6　為什麼一定要用 MEC？</b></summary>

（a）運算就在路口旁邊，延遲遠低於雲端；（b）兩個路口的 App 透過 MEC 平台的「服務註冊與發現」找到彼此，這是國際標準（ETSI MEC）定義的平台能力；（c）App 要記住每台車過去 2 秒的狀態，這種「有記憶」的服務放在邊緣最適合。
</details>

---

## 第 2 章　準備環境

### 2.1 你需要的東西

| 項目 | 需求 |
|---|---|
| 作業系統 | Windows 10 / 11、macOS 14 以上（**Apple Silicon，即 M1/M2/M3/M4**）、或 Ubuntu 22.04 / 24.04 |
| 記憶體 | 至少 8 GB。**路口 A 的正式收集需要約 4.5 GB 可用記憶體**（見 5.4 節） |
| 硬碟 | 至少 5 GB 可用空間 |
| 網路 | 第一次安裝時要下載約 300 MB |
| 文字編輯器 | 建議 [VS Code](https://code.visualstudio.com/)（XML 有顏色標示、可以預覽這份手冊） |

> **Intel 晶片的 Mac 無法照本章安裝**（SUMO 1.27.0 沒有提供 Intel Mac 的安裝包）。
> 請直接跟老師說，改用實驗室的電腦。
>
> **Mac 使用者另外要裝 XQuartz 才能開 SUMO 的畫面**（`sumo-gui`）。SUMO 的畫面程式
> 是 X11 程式，macOS 本身不帶 X11。沒裝的話 `sumo-gui` 會報 `unable to open display`。
> 只影響「看畫面」—— 收集資料、建資料集、驗收都**不需要**畫面，沒裝也能做完。
> 安裝：到 <https://www.xquartz.org/> 下載安裝，**裝完一定要登出再登入**（或重開機）。

### 2.2 安裝 Python

需要 Python **3.10 到 3.13** 之間的版本（建議 3.12）。

**Windows**

1. 到 <https://www.python.org/downloads/> 下載 Python 3.12
2. 執行安裝程式，**第一個畫面最下面一定要勾「Add python.exe to PATH」**，再按 Install Now
3. 開啟「PowerShell」（開始選單搜尋 PowerShell），輸入：
   ```powershell
   python --version
   ```
   看到 `Python 3.12.x` 就成功了

**macOS**

打開「終端機」（Terminal），輸入 `python3 --version`。沒有的話到 <https://www.python.org/downloads/> 下載安裝。

**Ubuntu**

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
```

### 2.3 取得專案資料夾

老師會給你專案資料夾 `v2x-mec-fd`（壓縮檔或共用雲端連結）。

**放的位置很重要：路徑裡不要有中文、不要有空格。** SUMO 是用 C++ 寫的，遇到中文路徑常常開不了檔案，而且錯誤訊息完全看不出是這個原因。

| 作業系統 | 建議位置 |
|---|---|
| Windows | `C:\v2x\v2x-mec-fd` |
| macOS | `/Users/你的帳號/v2x/v2x-mec-fd` |
| Ubuntu | `/home/你的帳號/v2x/v2x-mec-fd` |

> Windows 的使用者名稱若是中文（例如 `C:\Users\王小明`），**千萬不要**放在桌面或文件資料夾裡。

之後本手冊所有指令，**都要在專案根目錄（`v2x-mec-fd` 這一層）執行**。

### 2.4 建立虛擬環境並安裝 SUMO

「虛擬環境」是一個獨立的 Python 空間，裝在裡面的套件不會影響電腦上其他程式。

**Windows（PowerShell）**

```powershell
cd C:\v2x\v2x-mec-fd
python -m venv .venv
.venv\Scripts\Activate.ps1
```

如果出現「**因為這個系統上已停用指令碼執行**」，先執行下面這行（只需要做一次），再重新執行上面那行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

成功後，提示字元最前面會出現 `(.venv)`。接著安裝：

```powershell
python -m pip install --upgrade pip
pip install -r requirements-student.txt
```

**macOS / Ubuntu（終端機）**

```bash
cd ~/v2x/v2x-mec-fd
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-student.txt
```

> **每次重新打開 PowerShell / 終端機，都要重新「啟用」虛擬環境**：
> Windows 執行 `.venv\Scripts\Activate.ps1`，macOS / Ubuntu 執行 `source .venv/bin/activate`。
> 看到提示字元前面有 `(.venv)` 才對。這是最常見的錯誤來源。

### 2.5 確認安裝成功

依序執行下面三行，每一行都要看到對應的結果：

```bash
sumo --version
```
→ 第一行是 `Eclipse SUMO sumo 1.27.0`

```bash
netconvert --version
```
→ 第一行是 `Eclipse SUMO netconvert 1.27.0`

```bash
python -c "import traci, sumolib, numpy, yaml; print('OK')"
```
→ 印出 `OK`

**三行都對了，第 2 章就完成了。** 版本一定要是 1.27.0（規格書 SPEC §4.2 指定，與競賽教材環境一致）。

### 2.6 安裝常見問題

| 狀況 | 原因 | 解法 |
|---|---|---|
| `sumo : 無法辨識…` / `command not found: sumo` | 虛擬環境沒啟用 | 重新執行 2.4 的啟用指令，確認有 `(.venv)` |
| macOS：`Library not loaded: ... libgdal...dylib` | `sumo-data` 版本不對 | `pip install sumo-data==1.27.0`（`requirements-student.txt` 已鎖定，照裝不會遇到） |
| Ubuntu：`libXrender.so.1: cannot open shared object file` | 沒有桌面環境的伺服器缺圖形函式庫 | `sudo apt install -y libxrender1 libgl1` |
| macOS 執行 `sumo-gui`：`unable to open display` | 沒有裝 XQuartz | 2.1 節：裝 XQuartz，**登出再登入** |
| `pip install` 很慢或逾時 | 網路 | 換個網路（例如手機熱點）再試 |
| 版本不是 1.27.0 | 裝到別的版本 | `pip install -r requirements-student.txt --force-reinstall` |

---

## 第 3 章　SUMO 30 分鐘入門

SUMO（Simulation of Urban MObility）是德國航太中心開發的開源交通模擬器，可以模擬每一台車怎麼開。

### 3.1 五個檔案與它們的關係

每個場景資料夾裡有五個你要寫的檔案：

```
  nodes.nod.xml  ─┐                                  ┌─ traffic.rou.xml
  （路口在哪）    │                                  │  （有哪些車、怎麼開）
                  ├─► netconvert ─► xxx.net.xml ─┐   │
  edges.edg.xml  ─┤    （組裝路網）   （完整路網）  ├───┴─► sumo ─► states.jsonl.gz（每台車的軌跡）
  （路怎麼連）    │                                │       （模擬）  ssm.xml.gz（危險事件，也就是標籤）
                  │                                │
  xxx.netccfg  ───┘                  xxx.sumocfg ──┘
  （組裝設定）                       （模擬設定）
```

| 檔案 | 內容 | 你會常改嗎 |
|---|---|---|
| `nodes.nod.xml` | 路口與道路端點的**位置** | 不太改 |
| `edges.edg.xml` | 哪個點連到哪個點、**幾條車道、速限** | 不太改 |
| `xxx.netccfg` | 告訴 netconvert 讀哪些檔、號誌週期多長 | 不太改 |
| `traffic.rou.xml` | **車種**（駕駛個性）與**車流**（每小時幾輛、往哪開） | **最常改——調參就是改這個** |
| `xxx.sumocfg` | 要跑多久、要量哪些安全指標 | 不太改 |

`xxx.net.xml` 是 netconvert **自動產生**的，不要手動改它。

### 3.2 幾個名詞

| 名詞 | 意思 |
|---|---|
| node（節點） | 一個點。路口是一個 node，道路的盡頭也是一個 node |
| edge（道路） | 兩個 node 之間的一段路，**有方向**。雙向道路 = 兩條 edge |
| lane（車道） | edge 裡的一條車道。`N2C_0` 是 edge `N2C` 的第 0 條車道（最右邊） |
| junction type | 路口怎麼管：`traffic_light` 有號誌、`priority_stop` 支道停讓、`right_before_left` 右方車優先 |
| vType（車種） | 一種駕駛個性：多快、多會急煞、跟車多近 |
| flow（車流） | 「每小時 N 輛，從 X 開到 Y」 |

### 3.3 座標與方向

SUMO 的座標跟數學課一樣：x 往右（東）、y 往上（北），單位是公尺。

```
                N (0, 200)
                    │
                    │  N2C ↓   ↑ C2N
                    │
  W (-200, 0) ──────C (0, 0)────── E (200, 0)
                    │
                    │
                S (0, -200)
```

命名規則：`N2C` = 從 N 開往 C（**進入**路口），`C2N` = 從 C 開往 N（**離開**路口）。

---

## 第 4 章　建立三個場景

每個場景都是：**寫五個檔 → 建路網 → 用畫面看一眼 → 跑驗收**。

> 建議路口 A 的檔案**自己一行一行打**（或至少一行一行讀過再貼上），邊打邊看註解。
> 決賽評審問「這個參數是什麼意思」時，你要答得出來。

### 4.1 路口 A：號誌化十字路口

所有檔案放在 `sumo/intersection_a/` 資料夾。

#### 步驟 1　寫 nodes.nod.xml（路口與端點位置）

**檔案：`sumo/intersection_a/nodes.nod.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 路口 A 的節點：中央一個號誌路口 C，四個方向各一個端點，距路口 200 公尺 -->
<nodes>
    <node id="C" x="0"    y="0"    type="traffic_light"/>
    <node id="N" x="0"    y="200"  type="priority"/>
    <node id="S" x="0"    y="-200" type="priority"/>
    <node id="E" x="200"  y="0"    type="priority"/>
    <node id="W" x="-200" y="0"    type="priority"/>
</nodes>
```

#### 步驟 2　寫 edges.edg.xml（道路）

**檔案：`sumo/intersection_a/edges.edg.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 路口 A 的道路：每個方向一進一出，各 2 車道，速限 50 km/h（13.89 m/s）
     命名規則：N2C = 從 N 開往 C（進入路口）、C2N = 從 C 開往 N（離開路口） -->
<edges>
    <edge id="N2C" from="N" to="C" numLanes="2" speed="13.89"/>
    <edge id="C2N" from="C" to="N" numLanes="2" speed="13.89"/>
    <edge id="S2C" from="S" to="C" numLanes="2" speed="13.89"/>
    <edge id="C2S" from="C" to="S" numLanes="2" speed="13.89"/>
    <edge id="E2C" from="E" to="C" numLanes="2" speed="13.89"/>
    <edge id="C2E" from="C" to="E" numLanes="2" speed="13.89"/>
    <edge id="W2C" from="W" to="C" numLanes="2" speed="13.89"/>
    <edge id="C2W" from="C" to="W" numLanes="2" speed="13.89"/>
</edges>
```

`speed` 的單位是 m/s。50 km/h ÷ 3.6 = 13.89 m/s。

#### 步驟 3　寫 intersection_a.netccfg（路網組裝設定）

**檔案：`sumo/intersection_a/intersection_a.netccfg`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 路口 A的路網建置設定：告訴 netconvert 要讀哪些檔、輸出到哪、號誌怎麼排。
     建置指令（在專案根目錄）：python sumo/build_networks.py intersection_a -->
<netconvertConfiguration>
    <input>
        <node-files value="nodes.nod.xml"/>
        <edge-files value="edges.edg.xml"/>
    </input>
    <output>
        <output-file value="intersection_a.net.xml"/>
    </output>
    <tls_building>
        <!-- 號誌週期 60 秒（SPEC §4.3）。netconvert 會自動排出綠 / 黃 / 紅的時相 -->
        <tls.cycle.time value="60"/>
    </tls_building>
    <junctions>
        <!-- 不允許迴轉：本專案不研究迴轉車，關掉可以少一種干擾 -->
        <no-turnarounds value="true"/>
    </junctions>
</netconvertConfiguration>
```

#### 步驟 4　寫 traffic.rou.xml（車種與車流）

這是你之後調參最常改的檔案。

**檔案：`sumo/intersection_a/traffic.rou.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 路口 A 的車種與車流（SPEC §4.3：小客車 90%、大車 10%；每方向 1200 輛/小時）

     駕駛參數刻意調得比 SUMO 預設「粗魯」，否則 SUMO 的預設駕駛幾乎不會出事，
     我們就收集不到危險樣本（SPEC §4.2）。要調危險程度，改這幾個值：
       sigma       駕駛不完美程度 0~1，越大越會亂踩油門煞車      （預設 0.5）
       tau         與前車的反應時間（秒），越小跟得越緊           （預設 1.0）
       minGap      停車時與前車保持的最小距離（公尺），越小越緊   （預設 2.5）
       speedFactor 相對速限的倍數，1.15 = 平均超速 15%           （預設 1.0） -->
<routes>
    <vTypeDistribution id="mixA">
        <vType id="carA"   vClass="passenger" probability="0.9"
               accel="2.6" decel="4.5" sigma="0.7" tau="0.6" minGap="1.5"
               speedFactor="normc(1.15,0.10,0.80,1.50)"/>
        <vType id="truckA" vClass="truck"     probability="0.1"
               accel="1.3" decel="4.0" sigma="0.6" tau="0.8" minGap="2.0"
               speedFactor="normc(1.05,0.10,0.80,1.30)"/>
    </vTypeDistribution>


    <!-- 每個入口 1200 輛/小時 = 直行 960 + 左轉 120 + 右轉 120
         左轉車會與對向直行車交叉，這就是 SPEC §4.3 說的「對向左轉穿越」。

         【已知的規格疑點，老師決定中】SPEC §4.3 同時要求路口 A 的危險樣本中
         側向 < 5%。實測（600 秒試跑）這組設定側向約 14%。把左轉降到 40 也只降到
         12%——路口已飽和，左轉車實際能通過的量受限於對向車流間隙，不受需求量控制。
         這不影響 SPEC §4.3 真正的驗收條件（A、B 兩路口危險型態「明顯偏斜」），
         check_scenario.py 會以 [注意] 而非 [失敗] 標示。 -->
    <!-- 從北方進來（往南開） -->
    <flow id="N_straight" type="mixA" from="N2C" to="C2S" begin="0" end="3700" vehsPerHour="960" departLane="best" departSpeed="max"/>
    <flow id="N_left"     type="mixA" from="N2C" to="C2E" begin="0" end="3700" vehsPerHour="120" departLane="best" departSpeed="max"/>
    <flow id="N_right"    type="mixA" from="N2C" to="C2W" begin="0" end="3700" vehsPerHour="120" departLane="best" departSpeed="max"/>
    <!-- 從南方進來（往北開） -->
    <flow id="S_straight" type="mixA" from="S2C" to="C2N" begin="0" end="3700" vehsPerHour="960" departLane="best" departSpeed="max"/>
    <flow id="S_left"     type="mixA" from="S2C" to="C2W" begin="0" end="3700" vehsPerHour="120" departLane="best" departSpeed="max"/>
    <flow id="S_right"    type="mixA" from="S2C" to="C2E" begin="0" end="3700" vehsPerHour="120" departLane="best" departSpeed="max"/>
    <!-- 從東方進來（往西開） -->
    <flow id="E_straight" type="mixA" from="E2C" to="C2W" begin="0" end="3700" vehsPerHour="960" departLane="best" departSpeed="max"/>
    <flow id="E_left"     type="mixA" from="E2C" to="C2S" begin="0" end="3700" vehsPerHour="120" departLane="best" departSpeed="max"/>
    <flow id="E_right"    type="mixA" from="E2C" to="C2N" begin="0" end="3700" vehsPerHour="120" departLane="best" departSpeed="max"/>
    <!-- 從西方進來（往東開） -->
    <flow id="W_straight" type="mixA" from="W2C" to="C2E" begin="0" end="3700" vehsPerHour="960" departLane="best" departSpeed="max"/>
    <flow id="W_left"     type="mixA" from="W2C" to="C2N" begin="0" end="3700" vehsPerHour="120" departLane="best" departSpeed="max"/>
    <flow id="W_right"    type="mixA" from="W2C" to="C2S" begin="0" end="3700" vehsPerHour="120" departLane="best" departSpeed="max"/>
</routes>
```

幾個要看懂的地方：

- `vTypeDistribution` 裡放了兩種車，`probability` 是出現的機率，所以每 10 台車大約有 9 台小客車、1 台大車
- `normc(1.15,0.10,0.80,1.50)` = 常態分布，平均 1.15、標準差 0.10、最小 0.80、最大 1.50。每台車出生時抽一個值，所以不是每台車都一樣快
- 每個方向有三條 flow（直行、左轉、右轉），`vehsPerHour` 加起來 = 1200
- 左轉的方向：從北邊來（往南開）的車，左轉是往**東**（`C2E`）。想像自己坐在車上就不會錯

#### 步驟 5　寫 intersection_a.sumocfg（模擬設定）

**檔案：`sumo/intersection_a/intersection_a.sumocfg`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 路口 A的模擬設定：用哪個路網、哪些車流、跑多久、要量什麼。
     看畫面：sumo-gui -c sumo/intersection_a/intersection_a.sumocfg
     收資料：python -m training.build_dataset collect 加上場景代號（a、b 或 proxy）-->
<sumoConfiguration>
    <input>
        <net-file value="intersection_a.net.xml"/>
        <route-files value="traffic.rou.xml"/>
    </input>

    <time>
        <begin value="0"/>
        <!-- 跑到 3700 秒：收資料只取前 3600 秒（SPEC §4.2），多 100 秒讓最後的衝突記錄完整 -->
        <end value="3700"/>
        <!-- 每 0.1 秒算一步（SPEC §4.2）。不可改，模型輸入的 20 步 = 2 秒就是這樣來的 -->
        <step-length value="0.1"/>
    </time>

    <processing>
        <!-- 車輛相撞時只警告、不中止模擬 -->
        <collision.action value="warn"/>
        <!-- 卡住超過 300 秒的車會被移走，避免整個路口塞死 -->
        <time-to-teleport value="300"/>
    </processing>

    <report>
        <duration-log.statistics value="true"/>
        <no-step-log value="true"/>
    </report>

    <!-- ======== SSM（替代安全指標）：本專案「標籤」的來源，絕對不能漏 ========
         SUMO 會在每台車上裝一個 SSM 裝置，量它跟附近每台車的：
           TTC   碰撞前時間：照目前速度開下去，幾秒後會撞上
           PET   後侵入時間：前一台車離開衝突點後，幾秒後下一台車到達
           DRAC  避免碰撞需要的減速度
         訓練標籤就是「未來 3 秒內 min(TTC, PET)」（SPEC §5.4）。 -->
    <ssm_device>
        <device.ssm.probability value="1"/>              <!-- 每一台車都裝 -->
        <device.ssm.measures value="TTC PET DRAC"/>
        <device.ssm.thresholds value="3.0 2.0 3.0"/>     <!-- 低於這些值才算衝突，記下來 -->
        <device.ssm.trajectories value="true"/>          <!-- 記錄每 0.1 秒的 TTC，標籤更準 -->
        <device.ssm.range value="50"/>                   <!-- 偵測 50 公尺內的車 -->
        <device.ssm.extratime value="5"/>
        <device.ssm.mdrac.prt value="1"/>
        <!-- 輸出成壓縮檔：路口 A 不壓縮會接近 900 MB，壓縮後約 120 MB -->
        <device.ssm.file value="ssm.xml.gz"/>
        <device.ssm.geo value="false"/>
        <device.ssm.write-positions value="false"/>
        <device.ssm.write-lane-positions value="false"/>
        <device.ssm.exclude-conflict-types value="none"/>
    </ssm_device>
</sumoConfiguration>
```

> **XML 註解的地雷：註解裡不能出現連續兩個減號 `--`。**
> 例如在註解裡寫 `--scenario` 會讓整個檔案變成不合法的 XML，SUMO 直接拒絕執行。

#### 步驟 6　建路網

在專案根目錄執行：

```bash
python sumo/build_networks.py intersection_a
```

**應該看到：**

```
=== intersection_a ===
  [OK]   路網：intersection_a/intersection_a.net.xml
[OK]   停止線：8 條車道、路口 ['C'] -> intersection_a/stoplines.json
```

「8 條車道」= 4 個方向 × 每個方向 2 條進入車道。數字不對就是 edges 寫錯了。

`stoplines.json` 是自動產生的，記錄每條進入車道的停止線位置。MEC App 算「這台車離停止線多遠」這個特徵要用它。

#### 步驟 7　用畫面看一眼

```bash
sumo-gui -c sumo/intersection_a/intersection_a.sumocfg
```

會跳出 SUMO 的視窗：

1. 按上方的綠色三角形 ▶ 開始跑
2. 上方「Delay」數值調到 100 左右，車子會慢下來比較好看
3. 用滑鼠滾輪放大路口

**你應該看到：** 四個方向都有車開進來、號誌在紅綠燈之間切換、大車（比較長）偶爾出現、路口會塞車排隊。

看完直接關掉視窗即可。**這一步只是看，不會產生資料。**

> Mac 出現 `unable to open display`？要先裝 XQuartz（2.1 節）。
> 真的裝不起來也沒關係，跳過這一步，後面的步驟都不需要畫面。

#### 步驟 8　驗收第 1 到第 4 關

```bash
python sumo/check_scenario.py intersection_a
```

現在第 1～4 關應該全部是 `[通過]`。第 5 關會說「還沒有模擬輸出」—— 這是對的，那是第 5 章的事。

**第 1～4 關有 `[失敗]`？** 看它下面那行 `→ 怎麼修：`，照做之後再跑一次。

---

### 4.2 路口 B：無號誌 T 字路口

所有檔案放在 `sumo/intersection_b/`。跟路口 A 的差別：

- 只有三個方向（T 字），而且**沒有號誌**
- 東西向是主線，南邊是支道，支道要停車讓主線（`priority_stop`）
- 單車道，但車道比較寬，讓機車能鑽
- 有 40% 機車
- **主線車和支道車用不同的駕駛個性**（原因寫在 traffic.rou.xml 的註解裡，第 5 章也會解釋）

**檔案：`sumo/intersection_b/nodes.nod.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 路口 B 的節點：T 字路口。東西向是主線，南方是次要道路從下方匯入。
     中央節點 type="priority_stop"：次要道路必須停車再讓（對應 SPEC §4.3 的「停讓標線」） -->
<nodes>
    <node id="C" x="0"    y="0"    type="priority_stop"/>
    <node id="W" x="-200" y="0"    type="priority"/>
    <node id="E" x="200"  y="0"    type="priority"/>
    <node id="S" x="0"    y="-200" type="priority"/>
</nodes>
```

**檔案：`sumo/intersection_b/edges.edg.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 路口 B 的道路：全部單車道。priority 數字大的是主線，小的是次要道路（要讓主線）。
     車道寬 3.5 公尺：比預設的 3.2 寬一點，讓機車有空間在車道內側向鑽行 -->
<edges>
    <edge id="W2C" from="W" to="C" numLanes="1" speed="13.89" priority="2" width="3.5"/>
    <edge id="C2W" from="C" to="W" numLanes="1" speed="13.89" priority="2" width="3.5"/>
    <edge id="E2C" from="E" to="C" numLanes="1" speed="13.89" priority="2" width="3.5"/>
    <edge id="C2E" from="C" to="E" numLanes="1" speed="13.89" priority="2" width="3.5"/>
    <edge id="S2C" from="S" to="C" numLanes="1" speed="11.11" priority="1" width="3.5"/>
    <edge id="C2S" from="C" to="S" numLanes="1" speed="11.11" priority="1" width="3.5"/>
</edges>
```

**檔案：`sumo/intersection_b/intersection_b.netccfg`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 路口 B的路網建置設定：告訴 netconvert 要讀哪些檔、輸出到哪、號誌怎麼排。
     建置指令（在專案根目錄）：python sumo/build_networks.py intersection_b -->
<netconvertConfiguration>
    <input>
        <node-files value="nodes.nod.xml"/>
        <edge-files value="edges.edg.xml"/>
    </input>
    <output>
        <output-file value="intersection_b.net.xml"/>
    </output>
    <junctions>
        <!-- 不允許迴轉：本專案不研究迴轉車，關掉可以少一種干擾 -->
        <no-turnarounds value="true"/>
    </junctions>
</netconvertConfiguration>
```

**檔案：`sumo/intersection_b/traffic.rou.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 路口 B 的車種與車流（SPEC §4.3：小客車 60%、機車 40%；每方向 500 輛/小時）

     路口 B 要讓「側向衝突」（匯入、交叉）成為主要危險型態，所以：
       impatience        等太久會不耐煩、硬擠進去，0~1，越大越冒險  （預設 0）
       jmTimegapMinor    次要道路車願意接受的最小間隙（秒），越小越敢切（預設 1）
       lcSublane         機車在車道內側向移動的積極度                 （預設 1）
     主線車的 tau 設大一點、minGap 拉開，避免追撞變成主要型態（SPEC 要求追撞 < 10%） -->
<routes>
    <!-- 主線車（東西向直行與轉入支道）：跟車保守，讓「追撞」不要變成主要型態。
         SPEC §4.3 要求路口 B 的危險樣本中追撞 < 10%。 -->
    <vTypeDistribution id="mixB_main">
        <vType id="carB_main"  vClass="passenger"  probability="0.6"
               accel="2.6" decel="4.5" sigma="0.4" tau="1.5" minGap="4.0"
               speedFactor="normc(1.00,0.10,0.80,1.20)"/>
        <vType id="motoB_main" vClass="motorcycle" probability="0.4"
               accel="3.5" decel="6.0" sigma="0.5" tau="1.3" minGap="2.5"
               latAlignment="arbitrary" lcSublane="2.0" minGapLat="0.4"
               speedFactor="normc(1.05,0.10,0.80,1.30)"/>
    </vTypeDistribution>

    <!-- 支道車（從南方匯入主線）：在「路口」冒進，但「跟車」保守。
         這兩件事在 SUMO 是分開的兩組參數：
           路口行為（jm 開頭、impatience）→ 設得冒進，製造匯入 / 交叉衝突
           跟車行為（tau、minGap、sigma） → 設得保守，避免支道排隊時互撞
         實測（600 秒）：兩組都設冒進時，危險追撞有 30%，全是支道上排隊的車
         一前一後撞上（S_left × S_right），不合格；拆開調之後才壓下來。
           impatience        等太久會硬擠進去，0~1，越大越冒險
           jmTimegapMinor    願意接受的主線車間隙（秒），越小越敢切
           jmIgnoreFoeProb   有多少機率「沒看到」主線來車                 （模擬分心）
           jmIgnoreFoeSpeed  主線車速低於此值（m/s）時才可能被忽略
           lcSublane         機車在車道內側向鑽行的積極度                  -->
    <vTypeDistribution id="mixB_minor">
        <vType id="carB_minor"  vClass="passenger"  probability="0.6"
               accel="2.6" decel="4.5" sigma="0.5" tau="1.4" minGap="3.0"
               impatience="0.9" jmTimegapMinor="0.3"
               jmIgnoreFoeProb="0.3" jmIgnoreFoeSpeed="10"
               speedFactor="normc(1.05,0.10,0.80,1.30)"/>
        <vType id="motoB_minor" vClass="motorcycle" probability="0.4"
               accel="3.5" decel="6.0" sigma="0.6" tau="1.2" minGap="2.0"
               impatience="1.0" jmTimegapMinor="0.2"
               jmIgnoreFoeProb="0.4" jmIgnoreFoeSpeed="12"
               latAlignment="arbitrary" lcSublane="3.0" minGapLat="0.3"
               speedFactor="normc(1.15,0.15,0.80,1.60)"/>
    </vTypeDistribution>

    <!-- 每個入口 500 輛/小時（SPEC §4.3） -->
    <!-- 從西方進來（主線，往東開）：直行 400、右轉進支道 100 -->
    <flow id="W_straight" type="mixB_main"  from="W2C" to="C2E" begin="0" end="3700" vehsPerHour="400" departSpeed="max"/>
    <flow id="W_right"    type="mixB_main"  from="W2C" to="C2S" begin="0" end="3700" vehsPerHour="100" departSpeed="max"/>
    <!-- 從東方進來（主線，往西開）：直行 400、左轉進支道 100 -->
    <flow id="E_straight" type="mixB_main"  from="E2C" to="C2W" begin="0" end="3700" vehsPerHour="400" departSpeed="max"/>
    <flow id="E_left"     type="mixB_main"  from="E2C" to="C2S" begin="0" end="3700" vehsPerHour="100" departSpeed="max"/>
    <!-- 從南方進來（支道，匯入主線）：左轉 250、右轉 250 -->
    <flow id="S_left"     type="mixB_minor" from="S2C" to="C2W" begin="0" end="3700" vehsPerHour="250" departSpeed="max"/>
    <flow id="S_right"    type="mixB_minor" from="S2C" to="C2E" begin="0" end="3700" vehsPerHour="250" departSpeed="max"/>
</routes>
```

**檔案：`sumo/intersection_b/intersection_b.sumocfg`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 路口 B的模擬設定：用哪個路網、哪些車流、跑多久、要量什麼。
     看畫面：sumo-gui -c sumo/intersection_b/intersection_b.sumocfg
     收資料：python -m training.build_dataset collect 加上場景代號（a、b 或 proxy）-->
<sumoConfiguration>
    <input>
        <net-file value="intersection_b.net.xml"/>
        <route-files value="traffic.rou.xml"/>
    </input>

    <time>
        <begin value="0"/>
        <!-- 跑到 3700 秒：收資料只取前 3600 秒（SPEC §4.2），多 100 秒讓最後的衝突記錄完整 -->
        <end value="3700"/>
        <!-- 每 0.1 秒算一步（SPEC §4.2）。不可改，模型輸入的 20 步 = 2 秒就是這樣來的 -->
        <step-length value="0.1"/>
    </time>

    <processing>
        <!-- 開啟「子車道模型」：車道被切成 0.8 公尺寬的小格，機車可以在車道內
             左右移動、從汽車旁邊鑽過去（SPEC §4.3「機車鑽行」）。只有路口 B 需要 -->
        <lateral-resolution value="0.8"/>
        <!-- 車輛相撞時只警告、不中止模擬 -->
        <collision.action value="warn"/>
        <!-- 卡住超過 300 秒的車會被移走，避免整個路口塞死 -->
        <time-to-teleport value="300"/>
    </processing>

    <report>
        <duration-log.statistics value="true"/>
        <no-step-log value="true"/>
    </report>

    <!-- ======== SSM（替代安全指標）：本專案「標籤」的來源，絕對不能漏 ========
         SUMO 會在每台車上裝一個 SSM 裝置，量它跟附近每台車的：
           TTC   碰撞前時間：照目前速度開下去，幾秒後會撞上
           PET   後侵入時間：前一台車離開衝突點後，幾秒後下一台車到達
           DRAC  避免碰撞需要的減速度
         訓練標籤就是「未來 3 秒內 min(TTC, PET)」（SPEC §5.4）。 -->
    <ssm_device>
        <device.ssm.probability value="1"/>              <!-- 每一台車都裝 -->
        <device.ssm.measures value="TTC PET DRAC"/>
        <device.ssm.thresholds value="3.0 2.0 3.0"/>     <!-- 低於這些值才算衝突，記下來 -->
        <device.ssm.trajectories value="true"/>          <!-- 記錄每 0.1 秒的 TTC，標籤更準 -->
        <device.ssm.range value="50"/>                   <!-- 偵測 50 公尺內的車 -->
        <device.ssm.extratime value="5"/>
        <device.ssm.mdrac.prt value="1"/>
        <!-- 輸出成壓縮檔：路口 A 不壓縮會接近 900 MB，壓縮後約 120 MB -->
        <device.ssm.file value="ssm.xml.gz"/>
        <device.ssm.geo value="false"/>
        <device.ssm.write-positions value="false"/>
        <device.ssm.write-lane-positions value="false"/>
        <device.ssm.exclude-conflict-types value="none"/>
    </ssm_device>
</sumoConfiguration>
```

建路網與驗收：

```bash
python sumo/build_networks.py intersection_b
python sumo/check_scenario.py intersection_b
```

應該看到「停止線：**3** 條車道」（三個方向各一條進入車道），第 1～4 關全部通過。

用畫面看的時候，注意看**機車會在車道內偏左或偏右**、從汽車旁邊鑽過去：

```bash
sumo-gui -c sumo/intersection_b/intersection_b.sumocfg
```

---

### 4.3 代理場景：公共練習卷

所有檔案放在 `sumo/proxy_public/`。

這個場景**不模仿任何真實路口**。它的唯一目的是產生第 1.3 節說的「同一份練習卷」—— 所以只要三種風險等級（安全、注意、危險）都有足夠的樣本就好。

**檔案：`sumo/proxy_public/nodes.nod.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 代理場景的節點：無號誌、優先權路口，刻意與 A、B 都不同（中性幾何） -->
<nodes>
    <node id="C" x="0"    y="0"    type="right_before_left"/>
    <node id="N" x="0"    y="150"  type="priority"/>
    <node id="S" x="0"    y="-150" type="priority"/>
    <node id="E" x="150"  y="0"    type="priority"/>
    <node id="W" x="-150" y="0"    type="priority"/>
</nodes>
```

**檔案：`sumo/proxy_public/edges.edg.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 代理場景的道路：四向單車道，速限 40 km/h -->
<edges>
    <edge id="N2C" from="N" to="C" numLanes="1" speed="11.11"/>
    <edge id="C2N" from="C" to="N" numLanes="1" speed="11.11"/>
    <edge id="S2C" from="S" to="C" numLanes="1" speed="11.11"/>
    <edge id="C2S" from="C" to="S" numLanes="1" speed="11.11"/>
    <edge id="E2C" from="E" to="C" numLanes="1" speed="11.11"/>
    <edge id="C2E" from="C" to="E" numLanes="1" speed="11.11"/>
    <edge id="W2C" from="W" to="C" numLanes="1" speed="11.11"/>
    <edge id="C2W" from="C" to="W" numLanes="1" speed="11.11"/>
</edges>
```

**檔案：`sumo/proxy_public/proxy_public.netccfg`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 代理場景的路網建置設定：告訴 netconvert 要讀哪些檔、輸出到哪、號誌怎麼排。
     建置指令（在專案根目錄）：python sumo/build_networks.py proxy_public -->
<netconvertConfiguration>
    <input>
        <node-files value="nodes.nod.xml"/>
        <edge-files value="edges.edg.xml"/>
    </input>
    <output>
        <output-file value="proxy_public.net.xml"/>
    </output>
    <junctions>
        <!-- 不允許迴轉：本專案不研究迴轉車，關掉可以少一種干擾 -->
        <no-turnarounds value="true"/>
    </junctions>
</netconvertConfiguration>
```

**檔案：`sumo/proxy_public/traffic.rou.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 代理場景的車流：三種車混合、各種轉向都有，目的是讓「安全 / 注意 / 危險」
     三種樣本都出現，而不是模仿任何一個真實路口（SPEC §7.1） -->
<routes>
    <vTypeDistribution id="mixP">
        <vType id="carP"   vClass="passenger"  probability="0.7" sigma="0.7" tau="0.7" minGap="1.8" impatience="0.4" speedFactor="normc(1.10,0.10,0.80,1.40)"/>
        <vType id="truckP" vClass="truck"      probability="0.1" sigma="0.6" tau="0.9" minGap="2.2" speedFactor="normc(1.00,0.10,0.80,1.20)"/>
        <vType id="motoP"  vClass="motorcycle" probability="0.2" sigma="0.8" tau="0.7" minGap="1.2" impatience="0.6" speedFactor="normc(1.10,0.15,0.80,1.50)"/>
    </vTypeDistribution>
    <flow id="N_straight" type="mixP" from="N2C" to="C2S" begin="0" end="3700" vehsPerHour="250" departSpeed="max"/>
    <flow id="N_turn"     type="mixP" from="N2C" to="C2E" begin="0" end="3700" vehsPerHour="100" departSpeed="max"/>
    <flow id="S_straight" type="mixP" from="S2C" to="C2N" begin="0" end="3700" vehsPerHour="250" departSpeed="max"/>
    <flow id="S_turn"     type="mixP" from="S2C" to="C2W" begin="0" end="3700" vehsPerHour="100" departSpeed="max"/>
    <flow id="E_straight" type="mixP" from="E2C" to="C2W" begin="0" end="3700" vehsPerHour="250" departSpeed="max"/>
    <flow id="E_turn"     type="mixP" from="E2C" to="C2N" begin="0" end="3700" vehsPerHour="100" departSpeed="max"/>
    <flow id="W_straight" type="mixP" from="W2C" to="C2E" begin="0" end="3700" vehsPerHour="250" departSpeed="max"/>
    <flow id="W_turn"     type="mixP" from="W2C" to="C2S" begin="0" end="3700" vehsPerHour="100" departSpeed="max"/>
</routes>
```

**檔案：`sumo/proxy_public/proxy_public.sumocfg`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- 代理場景的模擬設定：用哪個路網、哪些車流、跑多久、要量什麼。
     看畫面：sumo-gui -c sumo/proxy_public/proxy_public.sumocfg
     收資料：python -m training.build_dataset collect 加上場景代號（a、b 或 proxy）-->
<sumoConfiguration>
    <input>
        <net-file value="proxy_public.net.xml"/>
        <route-files value="traffic.rou.xml"/>
    </input>

    <time>
        <begin value="0"/>
        <!-- 跑到 3700 秒：收資料只取前 3600 秒（SPEC §4.2），多 100 秒讓最後的衝突記錄完整 -->
        <end value="3700"/>
        <!-- 每 0.1 秒算一步（SPEC §4.2）。不可改，模型輸入的 20 步 = 2 秒就是這樣來的 -->
        <step-length value="0.1"/>
    </time>

    <processing>
        <!-- 車輛相撞時只警告、不中止模擬 -->
        <collision.action value="warn"/>
        <!-- 卡住超過 300 秒的車會被移走，避免整個路口塞死 -->
        <time-to-teleport value="300"/>
    </processing>

    <report>
        <duration-log.statistics value="true"/>
        <no-step-log value="true"/>
    </report>

    <!-- ======== SSM（替代安全指標）：本專案「標籤」的來源，絕對不能漏 ========
         SUMO 會在每台車上裝一個 SSM 裝置，量它跟附近每台車的：
           TTC   碰撞前時間：照目前速度開下去，幾秒後會撞上
           PET   後侵入時間：前一台車離開衝突點後，幾秒後下一台車到達
           DRAC  避免碰撞需要的減速度
         訓練標籤就是「未來 3 秒內 min(TTC, PET)」（SPEC §5.4）。 -->
    <ssm_device>
        <device.ssm.probability value="1"/>              <!-- 每一台車都裝 -->
        <device.ssm.measures value="TTC PET DRAC"/>
        <device.ssm.thresholds value="3.0 2.0 3.0"/>     <!-- 低於這些值才算衝突，記下來 -->
        <device.ssm.trajectories value="true"/>          <!-- 記錄每 0.1 秒的 TTC，標籤更準 -->
        <device.ssm.range value="50"/>                   <!-- 偵測 50 公尺內的車 -->
        <device.ssm.extratime value="5"/>
        <device.ssm.mdrac.prt value="1"/>
        <!-- 輸出成壓縮檔：路口 A 不壓縮會接近 900 MB，壓縮後約 120 MB -->
        <device.ssm.file value="ssm.xml.gz"/>
        <device.ssm.geo value="false"/>
        <device.ssm.write-positions value="false"/>
        <device.ssm.write-lane-positions value="false"/>
        <device.ssm.exclude-conflict-types value="none"/>
    </ssm_device>
</sumoConfiguration>
```

```bash
python sumo/build_networks.py proxy_public
python sumo/check_scenario.py proxy_public
```

應該看到「停止線：**4** 條車道」，第 1～4 關全部通過。

---

## 第 5 章　收集資料與調參

### 5.1 先試跑 600 秒

正式資料要跑 3600 秒（1 小時的模擬時間），但調參時先跑 600 秒看趨勢，比較快。

```bash
python -m training.build_dataset collect --scenario a --max-seconds 600
python -m training.build_dataset collect --scenario b --max-seconds 600
python -m training.build_dataset collect --scenario proxy --max-seconds 600
```

`a`、`b`、`proxy` 是場景代號，分別對應 `intersection_a`、`intersection_b`、`proxy_public`。

跑的時候會每 300 秒回報一次進度：

```
  模擬時間    300/600 s，已錄 40,518 筆，實際耗時     3 s
```

**實測耗時（600 秒）：** 路口 A 約 30 秒、路口 B 約 4 秒、代理場景約 4 秒。

跑完每個場景資料夾裡會多兩個檔案：

- `states.jsonl.gz`：每台車每 0.1 秒的狀態（位置、速度、方向…）
- `ssm.xml.gz`：SUMO 偵測到的所有危險事件

### 5.2 看懂驗收結果

```bash
python sumo/check_scenario.py all
```

重點看**第 6 關**與最後的 **A、B 比較**。以老師實測的結果為例（600 秒）：

```
場景 intersection_a
  第 6 關｜這個路口的危險型態對不對
           危險事件型態：追撞 1057（86%）、交叉 170（14%）
    [通過] 追撞為主（86%）
    [注意] 側向衝突 14%，SPEC §4.3 要求 < 5%

場景 intersection_b
  第 6 關｜這個路口的危險型態對不對
           危險事件型態：交叉 42（75%）、碰撞 14（25%）
    [通過] 側向衝突為主（75%）
    [通過] 追撞 0%（SPEC 要求 < 10%）

A、B 兩個路口夠不夠不一樣？
    [通過] A 以追撞為主、B 以側向為主，兩個路口明顯偏斜
```

判讀：

| 看到 | 意思 | 要做什麼 |
|---|---|---|
| `[通過]` | 這一項合格 | 繼續 |
| `[注意]` | 不擋你往下走，但老師要知道 | **截圖貼給老師**，不用自己修 |
| `[失敗]` | 這一項不合格 | 照 `→ 怎麼修：` 做，修完再跑一次 |

**試跑階段一定會有一項 `[注意]`：「模擬時間只有 600 秒」**，這是對的，正式跑才會滿 3600 秒。

### 5.3 調參：每個旋鈕在做什麼

照手冊的檔案做，應該已經合格（除了 5.5 節的已知問題）。這一節是給你**理解**，也是第 7 章練習的基礎。

調參只改 `traffic.rou.xml` 裡 `vType` 的屬性。**一次只改一個，改完跑 600 秒看結果，記進調參紀錄。**

#### 跟車行為（影響「追撞」）

| 參數 | 意思 | 調大 → | 調小 → | SUMO 預設 |
|---|---|---|---|---|
| `tau` | 與前車的反應時間（秒） | 跟得遠，追撞**少** | 跟得緊，追撞**多** | 1.0 |
| `minGap` | 停車時與前車的最小距離（公尺） | 追撞**少** | 追撞**多** | 2.5 |
| `sigma` | 駕駛不完美程度（0～1），越大越會亂踩油門煞車 | 各種危險都**多** | 各種危險都**少** | 0.5 |
| `speedFactor` | 相對速限的倍數，1.15 = 平均超速 15% | 危險**多** | 危險**少** | 1.0 |
| `decel` | 一般煞車的減速度（m/s²） | 煞得快，危險少 | 煞得慢，危險多 | 4.5 |

#### 路口行為（影響「側向：匯入、交叉」）

| 參數 | 意思 | 調大 → | 調小 → | SUMO 預設 |
|---|---|---|---|---|
| `impatience` | 等太久會不耐煩、硬擠進去（0～1） | 側向**多** | 側向**少** | 0 |
| `jmTimegapMinor` | 支道車願意接受的主線車間隙（秒） | 側向**少**（比較保守） | 側向**多**（敢切） | 1 |
| `jmIgnoreFoeProb` | 「沒看到」來車的機率（模擬分心） | 側向**多** | 側向**少** | 0 |
| `jmIgnoreFoeSpeed` | 來車速度低於這個值（m/s）才可能沒看到 | 側向**多** | 側向**少** | 0 |
| `lcSublane` | 機車在車道內側向鑽行的積極度 | 鑽行**多** | 鑽行**少** | 1 |

#### 老師調參時踩過的坑（值得讀）

這兩段經驗說明了「為什麼檔案要寫成現在這樣」，決賽被問到時可以講：

**坑 1：路口 B 的追撞一開始有 30%（規格要求 < 10%）。**
查了是哪些車撞在一起，發現**全部是支道上排隊的車一前一後撞上**。原因是一開始把支道車的所有參數都設得很冒進。但 SUMO 裡「在路口多敢切」（`jm` 開頭、`impatience`）和「跟車多近」（`tau`、`minGap`）是**兩組獨立的參數**。拆開之後——路口冒進、跟車保守——追撞降到 0%，側向升到 75%。

**坑 2：路口 A 把左轉車流從每小時 120 輛降到 40 輛，側向衝突只從 14% 降到 12%。**
流量降了 3 倍，衝突卻幾乎沒少。原因是路口 A 已經**飽和**（車多到消化不完），左轉車實際能通過的量，受限於對向車流的間隙，而不是有多少車想左轉。調流量這條路在這種密度下走不通。（見 5.5 節）

> 這兩個坑的共同教訓：**不要猜，去查是誰撞誰。** 驗收工具報不合格時，把輸出貼給老師，老師可以幫你查衝突的車輛配對。

### 5.4 正式跑 3600 秒

調好之後，三個場景都跑滿 3600 秒（不加 `--max-seconds` 就是跑滿）：

```bash
python -m training.build_dataset collect --scenario b
python -m training.build_dataset collect --scenario proxy
python -m training.build_dataset collect --scenario a
```

**實測耗時與記憶體（3600 秒）：**

| 場景 | 耗時 | 峰值記憶體 | 產生的檔案大小 |
|---|---|---|---|
| 路口 B | 約 20 秒 | 約 50 MB | 約 11 MB |
| 代理場景 | 約 30 秒 | 約 220 MB | 約 21 MB |
| **路口 A** | **約 6 分鐘** | **約 4.2 GB** | 約 170 MB |

**路口 A 要特別注意：**

- 跑之前**關掉瀏覽器和其他大程式**，讓電腦至少有 4.5 GB 可用記憶體
- 跑的過程中畫面會停在同一行好幾十秒，**這不是當機**，看進度回報的秒數有在增加就好
- 記憶體都用在 SUMO 追蹤「車與車的相遇」上：路口 A 是飽和的，排隊的車彼此都在 50 公尺內，要追蹤的組合很多
- 你的電腦只有 8 GB 而跑到一半當掉？跟老師說，改用實驗室的電腦跑這一步

跑完再驗收一次：

```bash
python sumo/check_scenario.py all
```

第 5 關的「模擬時間」這次應該是 `[通過]`。

### 5.5 已知的規格疑點

**路口 A 的側向衝突約 14～16%，規格書要求 < 5%。** 驗收工具會標 `[注意]`，不是 `[失敗]`。

這不是你調錯，是規格書本身有張力：

- SPEC §4.3 說路口 A 的主要衝突型態包含「**對向左轉穿越**」（左轉車與對向直行車交叉）
- 同一段又要求路口 A 的危險樣本中**側向 < 5%**

實測發現，在「雙向 2 車道、每方向 1200 輛、60 秒週期」這個條件下，允許左轉的路口**天生**會產生 12～16% 的危險交叉，而且降左轉流量也壓不下來（5.3 節坑 2）。

**這件事老師在決定中，你不用處理。** 重要的是：規格書真正的驗收條件「A、B 兩路口危險型態**明顯偏斜**」是通過的（A 追撞 84% 對上 B 側向 72%）。

---

## 第 6 章　建資料集

### 6.1 建三個資料集

把模擬輸出轉成模型可以用的訓練資料：

```bash
python -m training.build_dataset build --scenario b
python -m training.build_dataset build --scenario proxy
python -m training.build_dataset build --scenario a
```

**這一步在做什麼：** 對每一台車，每隔一段時間取一個樣本 ——

- **輸入**：這台車**過去 2 秒**（20 步）的 8 個特徵（自己的速度、加速度、與最近衝突車的距離、接近速度…）
- **標籤**：這台車**未來 3 秒內**遇到的最小 TTC 或 PET → 安全 / 注意 / 危險

「輸入取自過去、標籤取自未來」—— 這就是為什麼我們是**預測**，而 TTC 規則只是偵測。

**實測耗時（3600 秒的資料）：** 路口 B 約 12 秒、代理場景約 20 秒、**路口 A 約 7 分鐘**（峰值記憶體約 1 GB）。

跑完會印出摘要（以下是老師實測的路口 B，你的數字可能略有不同）：

```
場景 b：100,931 筆，1500 台車，模擬時間 0.1–3599.8 s
類別分布：安全 86,569 (85.77%)  注意 11,363 (11.258%)  危險 2,999 (2.971%)
  特徵值域驗收通過：所有特徵截斷比例 < 1%
```

「特徵值域驗收」是在確認每個特徵的數值有沒有超出設定的範圍。如果你看到的是：

```
  !! 下列特徵的截斷比例 >= 1%，須回頭調整 config.yaml 的 features.norm 值域
     rel_speed: 3.5% 被截斷，實際範圍 [...]，設定 [...]
```

**不要緊張，也不要自己去改 `config.yaml`。** 這是老師設計好要抓的檢查點（SPEC §16），
通常代表你的場景跑出了設定範圍沒預料到的車況。把整段輸出貼給老師就好。

### 6.2 產生公共代理資料集（那份「同一份練習卷」）

```bash
python -m training.make_proxy
```

**應該看到：**

```
來源 proxy.npz：130,415 筆，安全 99,442 / 注意 21,084 / 危險 9,889
已輸出 .../data/proxy_set.npy：shape (2000, 20, 8)
分層後：安全 668 / 注意 666 / 危險 666（標籤已丟棄）
SHA256 1653ac7b0b6a2ca8...
```

（以上是老師實測的數字，你的結果可能略有不同。）

- `shape (2000, 20, 8)` = 2000 題，每題是 20 步 × 8 個特徵
- **三類各約 666 題**：刻意平均抽，讓對方的模型一定看得到「危險」長什麼樣子
- **標籤已丟棄**：練習卷只有題目，沒有答案 —— 答案要各路口的模型自己作答
- **SHA256** 是這份檔案的「指紋」：兩個路口必須拿到位元完全相同的練習卷，交換答案時會比對這個指紋，不一樣就拒絕

### 6.3 最後一次完整驗收

```bash
python sumo/check_scenario.py all
```

**這次的目標：沒有任何 `[失敗]`。** `[注意]` 截圖給老師即可。

---

## 第 7 章　練習：重跑、改一個參數、講給別人聽

規格書 SPEC §14 要求：**每個模組完成後，你要自己重跑一次、修改一個參數、口述一次原理。** 決賽評審會當面問問題，這一章的練習程度直接決定那時候答不答得出來。

### 7.1 練習一：改一個參數，預測結果，再驗證

每個練習都照這個格式：**先寫下你的預測，再跑，再比對。** 預測錯了沒關係，想清楚為什麼錯才是重點。

| # | 改什麼 | 在哪裡 | 你的預測 | 跑完結果 | 為什麼 |
|---|---|---|---|---|---|
| 1 | 路口 B 支道車的 `jmIgnoreFoeProb` 從 0.3 → 0 | `intersection_b/traffic.rou.xml` 的 `carB_minor` 與 `motoB_minor` | 側向衝突會變多還是少？ | | |
| 2 | 路口 A 的號誌週期 60 → 90 秒 | `intersection_a/intersection_a.netccfg`，改完要**重建路網** | 追撞會變多還是少？ | | |
| 3 | 路口 B 的 `lateral-resolution` 整行刪掉（關掉機車鑽行） | `intersection_b/intersection_b.sumocfg` | 危險事件總數會怎樣？ | | |

每個練習：

```bash
# 1. 改檔案（先把原本的值記下來！）
# 2. 練習 2 要先重建路網：python sumo/build_networks.py intersection_a
# 3. 跑 600 秒
python -m training.build_dataset collect --scenario b --max-seconds 600
# 4. 看結果
python sumo/check_scenario.py intersection_b
# 5. 把檔案改回原本的值！
```

> **做完一定要改回來**，然後重跑正式資料（5.4 節）。
> 練習用的資料不能交出去。

### 7.2 練習二：講給別人聽

找一個不懂這個專案的人（同學、家人），**不看稿**講一次。講完問對方：「你聽得懂我在做什麼嗎？」

**題目 1：這個專案在做什麼？（1 分鐘）**

提示：預測 vs 偵測、兩個路口互相學習、不交換原始資料。

**題目 2：你的場景是怎麼設計的？為什麼兩個路口要不一樣？（2 分鐘）**

提示：A 追撞為主、B 側向為主；如果一樣，互相學習就學不到新東西。

**題目 3：一筆訓練資料是怎麼來的？（2 分鐘）**

提示：SUMO 每 0.1 秒記錄一次 → 取過去 20 步（2 秒）的 8 個特徵當輸入 → 看未來 30 步（3 秒）內的最小 TTC/PET 當標籤。

**題目 4：資料從車子到 MEC 的完整路徑（3 分鐘）**

這題是 SPEC §13.5 指定的決賽必考題。照順序講出每一站在做什麼：

```
SUMO（模擬車輛）
 → UE（車上的 5G 裝置，把車輛狀態包成 V2X 訊息）
 → gNB（5G 基地台）
 → UPF（5G 核心網路的資料轉發功能）
 → DNAT（把封包導向 MEC 平台的入口）
 → Kong（MEP Gateway，依網址路徑決定送給哪個 App）
 → MEC App（算特徵、跑模型、判斷危險等級）
 → 回傳警告給 UE
```

**題目 5：聯邦蒸餾的一輪在做什麼？（2 分鐘）**

提示：每個路口先用自己的資料訓練 → 對練習卷作答 → 透過 MEC 平台找到對方、拿到對方的答案 → 用「自己的正確答案 + 對方的答案」再訓練一次 → 評估。

### 7.3 練習三：六個「為什麼」

回到 1.6 節，**先把答案蓋起來**，自己講一遍，再打開對照。六題都能講出來再往下。

---

## 第 8 章　交付

### 8.1 調參紀錄

在 `docs/tuning_log.md` 記錄你每一次改參數的經過。格式：

```markdown
# 調參紀錄

| 日期 | 場景 | 改了什麼 | 結果（危險事件型態） | 結論 |
|---|---|---|---|---|
| 9/26 | B | 照手冊建立，未改 | 交叉 75%、碰撞 25%、追撞 0% | 合格 |
| 9/28 | B | jmIgnoreFoeProb 0.3 → 0（練習一） | 交叉 ??%… | 側向變少，因為… |
```

**只記真的跑過的結果，數字從驗收工具的輸出複製，不要憑印象寫。**

### 8.2 打包

```bash
python sumo/pack_delivery.py
```

它會：

1. 檢查該交的檔案都在，缺了會列出來
2. 自動跑一次完整驗收，結果一起打包
3. 產生 `delivery_日期_時間.zip`

**應該看到：**

```
[完成] 已打包 delivery_20260927_2130.zip（xx.x MB）
```

打包內容：

| 內容 | 路徑 |
|---|---|
| 三個場景的原始檔 | `sumo/*/nodes.nod.xml` 等五個檔 + `stoplines.json` |
| 三個資料集 | `data/a.npz`、`data/b.npz`、`data/proxy.npz` 與各自的 `.summary.json` |
| 公共代理資料集 | `data/proxy_set.npy`、`.sha256`、`.json` |
| 驗收結果 | `docs/acceptance/check_*.txt` |
| 調參紀錄 | `docs/tuning_log.md` |

**不打包** `states.jsonl.gz` 和 `ssm.xml.gz`（太大，而且只要場景檔在，老師就能重跑出一樣的結果）。但**初賽結束前不要刪**。

### 8.3 交給老師

把 zip 上傳到老師指定的共用雲端資料夾（或用 USB），然後傳訊息給老師，內容包含：

1. zip 的檔名
2. `check_scenario.py all` 最後的結果截圖
3. 所有 `[注意]` 項目的截圖

---

## 第 9 章　初賽文件：你負責的部分

### 9.1 企劃書

競賽平台團隊建議的敘事順序：**情境說明 → 系統架構 → Demo 執行 → 呈現結果 → 效能數據 → 作品特色**。

建議分工（最後以老師的決定為準）：

| 章節 | 誰寫 | 素材在哪 |
|---|---|---|
| 情境說明：三個 SUMO 場景的設計 | **你** | 第 1.5 節的表格、`traffic.rou.xml` 的註解、`docs/tuning_log.md` |
| 情境說明：兩路口異質性的驗收結果 | **你** | `docs/acceptance/check_*.txt` 第 6 關與 A、B 比較 |
| 資料集建構：特徵、標籤、類別分布 | **你** | 第 6 章、`data/*.summary.json` |
| 系統架構、MEC App、API | 老師 | — |
| 實驗結果與效能分析圖表 | 你跑圖、老師寫分析 | 9.3 節 |
| AI 使用揭露 | 一起 | 9.4 節 |

### 9.2 3 分鐘 demo 影片

建議分鏡（照平台團隊建議的順序）：

| 時間 | 內容 | 畫面 |
|---|---|---|
| 0:00–0:20 | 問題：路口事故、規則只能偵測 | 簡報一頁 |
| 0:20–0:50 | 兩個路口長什麼樣、為什麼要不一樣 | `sumo-gui` 路口 A、B 各 10 秒 |
| 0:50–1:20 | 系統架構 | 1.4 節的架構圖 |
| 1:20–2:10 | Demo：車輛資料送進 MEC、回傳警告 | 老師提供的畫面錄影 |
| 2:10–2:40 | 結果：協同訓練前後的比較、提前預警時間 | 9.3 節的圖 |
| 2:40–3:00 | 特色總結 | 簡報一頁 |

用 `sumo-gui` 錄路口畫面時，把 Delay 調到 100、放大到看得清楚路口，用作業系統內建的錄影功能（Windows：`Win + G`；macOS：`Shift + Cmd + 5`）。

### 9.3 圖表

三張核心圖由 `analysis/` 裡的程式產生。**它們只讀 `logs/` 裡的實驗紀錄**，所以要等老師把實驗跑完、把 `logs/` 資料夾給你之後才能跑：

```bash
python -m analysis.plot_convergence     # 圖 1：協同訓練的收斂曲線
python -m analysis.plot_bytes           # 圖 2：傳輸量比較
python -m analysis.plot_latency         # 圖 3：延遲拆解（MEC vs Cloud）
```

圖會輸出到 `figures/`。

### 9.4 AI 使用揭露（必寫，不寫會被扣分或不計分）

競賽須知第八章第三點：使用 AI（例如 ChatGPT、Claude）當輔助工具，**必須在初賽文件中揭露使用過程**，沒寫的話主辦單位有權重新評分或不予計分。

**你自己用 AI 的每一次，都要當天記進 `docs/ai-disclosure.md` 的「使用紀錄」表格**，不要留到最後回想。格式：

```markdown
| 9/30 | ChatGPT | 請它解釋 SUMO 的 tau 參數是什麼意思 | 對照 SUMO 官方文件確認說法正確 |
| 10/4 | ChatGPT | 潤飾企劃書第 2 節的文字 | 逐句讀過，改了 3 處不準確的地方 |
```

**誠實揭露的風險，遠低於被發現沒揭露的風險。**

---

## 第 10 章　時程

今天是 9/24（四）。**粗體是硬期限。**

| 日期 | 你要做的事 | 對應章節 | 完成的判斷 |
|---|---|---|---|
| 9/24（四） | 讀第 0、1 章；裝好環境 | 0–2 | 2.5 節三行指令都對 |
| 9/25（五） | SUMO 入門；建好路口 A 並用畫面看過 | 3、4.1 | 路口 A 驗收第 1～4 關通過 |
| 9/26（六） | 建路口 B、代理場景；三個都試跑 600 秒 | 4.2、4.3、5.1 | `check_scenario.py all` 的 A、B 比較通過 |
| **9/27（日）** | **三個場景正式跑 3600 秒、建資料集、產生代理資料集、打包交付** | 5.4、6、8 | **zip 交給老師** |
| 9/28（一） | 練習一：改參數 | 7.1 | 調參紀錄有三筆練習 |
| 9/29（二） | 練習二、三：口述 | 7.2、7.3 | 能不看稿講完五個題目 |
| 9/30（三） | 企劃書：你負責的章節初稿 | 9.1 | 初稿給老師 |
| 10/1（四） | 跑三張圖表（老師給 logs 後） | 9.3 | `figures/` 有三張圖 |
| 10/2（五） | 影片腳本、錄製準備 | 9.2 | 分鏡確定 |
| **10/3（六）** | **錄製 3 分鐘影片** | 9.2 | **影片檔給老師** |
| **10/4（日）** | **企劃書定稿（含 AI 揭露）** | 9.1、9.4 | **給老師** |
| **10/5（一）** | **簡報、全文校對** | 9 | **給老師** |
| 10/6（二）上午 | 老師送件 | — | 中午 12:00 截止 |

---

## 第 11 章　紅線

這幾條沒有例外。違反任何一條，比做不完嚴重得多。

1. **不編造、不修改任何數字。** 企劃書、簡報、影片裡的每一個數字，都必須能指到某個檔案的某一行（驗收輸出、`summary.json`、`logs/`）。
2. **測試用、練習用的資料不能交出去。** 第 7 章練習跑出來的資料只是練習。
3. **不手動修改 `states.jsonl.gz`、`ssm.xml.gz`、`*.npz`。** 資料有問題就重跑，不要去改檔案。
4. **不改 `config.yaml`、`mec_app/`、`training/`、`deploy/`。** 覺得有問題就回報老師。這些是兩個路口共用的設定，改了會讓兩邊對不上。
5. **結果不好就照實說。** 如果協同訓練沒有幫助，那就是實驗結果，要分析原因，不是去調到「看起來有幫助」為止。評審通常能接受誠實的負面結果，但一定不接受造假。
6. **用了 AI 就要記錄**（9.4 節）。

---

## 第 12 章　錯誤排除

### 12.1 先做這三件事

遇到錯誤，先檢查：

1. **提示字元前面有沒有 `(.venv)`？** 沒有就重新啟用虛擬環境（2.4 節）
2. **你在不在專案根目錄？** 執行 `ls`（Windows 用 `dir`），應該看得到 `sumo`、`training`、`config.yaml`
3. **錯誤訊息從最上面開始讀。** Python 的錯誤訊息最重要的通常是**最後一行**，但原因常常在**中間**

### 12.2 常見錯誤

| 錯誤訊息（關鍵字） | 原因 | 解法 |
|---|---|---|
| `No module named 'mec_app'` 或 `'training'` | 不在專案根目錄 | `cd` 到 `v2x-mec-fd` 那一層再執行 |
| `No module named 'traci'` | 虛擬環境沒啟用 | 2.4 節 |
| `找不到路網 xxx.net.xml` | 還沒建路網 | `python sumo/build_networks.py` |
| `不是合法的 XML：... line X, column Y` | XML 寫錯，第 X 行第 Y 個字附近 | 最常見：少了 `/>`、引號沒關、**註解裡有 `--`** |
| `netconvert 沒有產生 xxx.net.xml` | nodes / edges 有錯 | 看上面的錯誤訊息；常見是 edge 的 `from`/`to` 指到不存在的 node id |
| `Error: File '...' is not accessible` | 路徑有中文或空格 | 把專案搬到 2.3 節建議的位置 |
| `Connection closed by SUMO` | SUMO 啟動就失敗了 | 手動執行 `sumo -c sumo/intersection_a/intersection_a.sumocfg --end 10` 看真正的錯誤 |
| 跑路口 A 時整台電腦變很慢、程式被強制關閉 | 記憶體不足 | 關掉其他程式；還是不行就改用實驗室電腦（5.4 節） |
| `時間倒退` | `states.jsonl.gz` 被動過 | 刪掉它，重新 collect |
| 驗收第 6 關 `[失敗]` | 危險型態不對 | 把完整輸出貼給老師，老師可以查是哪些車互撞 |

### 12.3 看不懂的錯誤

照第 0 章「卡住了怎麼問」的格式問老師。不要花超過 30 分鐘卡在同一個錯誤上。

---

## 附錄 A　指令速查卡

所有指令都在**專案根目錄**、**虛擬環境啟用**的狀態下執行。

```bash
# ---------- 環境 ----------
# 啟用虛擬環境（每次開新視窗都要）
.venv\Scripts\Activate.ps1          # Windows
source .venv/bin/activate           # macOS / Ubuntu

# 確認版本
sumo --version

# ---------- 場景 ----------
python sumo/build_networks.py                   # 三個場景全部建路網
python sumo/build_networks.py intersection_a    # 只建一個
sumo-gui -c sumo/intersection_a/intersection_a.sumocfg   # 看畫面

# ---------- 收集資料 ----------
python -m training.build_dataset collect --scenario a --max-seconds 600   # 試跑
python -m training.build_dataset collect --scenario a                     # 正式（3600 秒）
# 場景代號：a = intersection_a、b = intersection_b、proxy = proxy_public

# ---------- 建資料集 ----------
python -m training.build_dataset build --scenario a
python -m training.make_proxy                   # 公共代理資料集

# ---------- 驗收 ----------
python sumo/check_scenario.py intersection_a    # 單一場景
python sumo/check_scenario.py all               # 全部 + A、B 比較

# ---------- 交付 ----------
python sumo/pack_delivery.py
```

---

## 附錄 B　SSM 衝突型態碼

驗收工具把 SUMO 的衝突型態碼歸成幾類（依 SUMO 1.27.0 官方文件）：

| 類別 | 型態碼 | 意思 |
|---|---|---|
| **追撞** | 2、3、18 | 同一條路上一前一後（FOLLOWING） |
| **匯入** | 6、7、8、19 | 兩條路合成一條時（MERGING） |
| **交叉** | 10、11、14、15、17 | 兩條路交叉時（CROSSING） |
| 對向 | 20 | 在同一車道上迎面而來 |
| 碰撞 | 111 | 真的撞上了 |

「側向」= 匯入 + 交叉。

---

## 附錄 C　名詞表

| 名詞 | 意思 |
|---|---|
| V2X | Vehicle-to-Everything，車輛與周遭一切（其他車、路側設備、行人、網路）通訊 |
| BSM | Basic Safety Message，車輛定期廣播的基本安全訊息（位置、速度、方向…） |
| UE | User Equipment，使用者裝置。在這裡指車上的 5G 通訊模組 |
| gNB | 5G 基地台 |
| UPF | User Plane Function，5G 核心網路中負責轉發資料的功能 |
| MEC | Multi-access Edge Computing，多接取邊緣運算：把運算放在靠近使用者的地方 |
| MEP | MEC Platform，MEC 平台，提供 App 共用的服務（例如服務註冊與發現） |
| TTC | Time To Collision，碰撞前時間：照目前速度，幾秒後會撞上 |
| PET | Post-Encroachment Time，後侵入時間：前車離開衝突點後，幾秒後下一台車到達 |
| SSM | Surrogate Safety Measures，替代安全指標（TTC、PET 等的統稱） |
| 聯邦蒸餾 | Federated Distillation，多方不交換原始資料、只交換模型對共同資料的預測結果來互相學習 |
| logits | 模型在轉成機率之前的原始輸出分數，就是「對練習卷的答案」 |
| 公共代理資料集 | Proxy dataset，雙方共用的「練習卷」，只有題目沒有答案 |
| macro-F1 | 分類分數，每一類的分數平均。危險樣本很少時，比準確率更能看出模型好不好 |
| 異質性 | 兩個路口的資料分布不一樣。本專案刻意設計的 |
