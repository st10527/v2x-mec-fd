# AI 使用揭露紀錄

依「2026 行動通訊實務競賽」須知第八章第三點：使用 AI 或生成式 AI 作為**輔助工具**
（而非產生最終設計成果）須在初賽文件或決賽簡報中揭露使用過程與步驟；
未標明者主辦有權重新評分或不予計分。

**本檔為逐次流水帳，供 D16 撰寫企劃書時彙整。每次使用 AI 輔助後即時補一行，不要事後回想。**
含糊帶過的風險高於誠實揭露（SPEC §11.2）。

---

## 企劃書用段落（定稿前依實際情形調整）

> 本作品開發過程中使用生成式 AI 工具輔助程式碼撰寫、環境除錯與文件文字潤飾。具體包括：
> 系統架構與演算法設計由團隊自行決定；MEC App 與訓練腳本之初始骨架由 AI 輔助產生後，
> 由團隊逐行審閱、修改與測試；SUMO 場景參數、特徵定義、蒸餾協定與所有實驗設計
> 均由團隊自行擬定。所有程式碼均經團隊實際執行驗證，所有實驗數據皆由本團隊實機量測產出，
> 團隊對最終內容負責。

此段建議放在企劃書結尾獨立小節，並在決賽簡報保留一頁。

---

## 使用紀錄

> **本表的「驗證狀態」欄必須寫實。** 自動化測試通過 ≠ 團隊審閱過。
> 競賽須知要求 AI 只能作為輔助工具，且決賽須繳交完整程式碼供評審辨識原創性；
> 團隊對每一行程式碼都要答得出「這在做什麼、為什麼這樣寫」。
> 標記「**待團隊逐行審閱**」的項目，在 D16 撰寫企劃書前必須全部消化完畢，
> 屆時把該欄改成實際的審閱與修改紀錄（例如「逐行審閱，修改 3 處：…」）。

| 日期 | 工具 | 用途 | 驗證狀態 |
|---|---|---|---|
| 115/09/19 | — | 系統架構、演算法設計、蒸餾協定、SUMO 場景異質性設計、評估指標：**團隊自行擬定**，未使用 AI 產生 | — |
| 115/09/20 | Claude Code | 依 SPEC §9 建立專案目錄骨架、README、`config.yaml`（SPEC 數值參數的機器可讀形式） | `config.yaml` 數值已由程式交叉檢查對上 SPEC 條號（視窗步數、預測步數、傳輸 bytes、參數量）。**待團隊逐行審閱** |
| 115/09/20 | Claude Code | 檢出 SPEC §6.1 模型參數量記載錯誤（原記 7.3 萬 / 291 KB，實為 48,419 / 189 KB） | 以 PyTorch 實際建構 §6.1 結構並計數驗證，修正後記於 SPEC §16 |
| 115/09/21 | Claude Code | 產生 `mec_app/` 全部模組初始骨架（FastAPI 推論端點與 FD 端點、滑動視窗、特徵計算、規則基準線、Service Registry client、log 寫入） | 自動化測試通過：`tests/test_core.py` 55 項、`tests/test_app.py` 46 項。**待團隊逐行審閱** |
| 115/09/21 | Claude Code | 產生 `training/` 骨架（資料集建構、指標、本地預訓練、蒸餾回合、跨路口評估） | 自動化測試通過：`tests/test_build_dataset.py` 40 項、`tests/test_pipeline.py` 45 項（含「標籤取自未來」的逐邊界測試）。**待團隊逐行審閱** |
| 115/09/21 | Claude Code | 產生 `ue/sumo_ue_sender.py`、`analysis/` 三張圖的繪製腳本、`deploy/` 部署腳本 | 自動化測試通過：`tests/test_integration.py` 21 項（實際起 server、送 BSM、產圖）。`deploy/setup_vm*.sh` 中屬於培訓營教材的指令**刻意留白**，由團隊對照教材填入。**待團隊逐行審閱** |
| 115/09/21 | Claude Code | 規格書 §5.2 未定義的三個細節（「最近衝突車輛」的判準、航向夾角的映射方式、停止線距離的幾何來源），**由 AI 提出實作定義並標記為待團隊確認** | 三項定義與理由寫在 `mec_app/features.py` 檔頭 (1)(2)(3)，並列入 SPEC §16「待確認事項」。**團隊尚未逐項確認；確認後須更新 SPEC §5.2 本文。** |
| 115/09/22 | Claude Code | 建立開發機到競賽主機（<實驗主機>）的 SSH 金鑰登入；掃描實體主機環境 | 實測結果記於 `docs/platform-notes.md` §2：巢狀虛擬化可用、16 核 / 31 GB。密碼由團隊自行輸入，AI 未接觸 |
| 115/09/22 | Claude Code | 讀取培訓營教材（71 頁），把 OAI-MEP / Kong / DNAT / free5GC / UERANSIM 指令抄錄進 `deploy/setup_vm*.sh` 與 `docs/platform-notes.md` | 指令逐條標註教材頁次，依 SPEC §15.7 原樣沿用未改寫；三支腳本已在實體主機上以 `--check-only` / `--dry-run` 實際執行。**待團隊對照教材逐條複核** |
| 115/09/22 | Claude Code | 指出教材環境與本專案實體主機的差異（缺培訓營 VM 映像檔、kernel 6.8 vs 6.17 的 gtp5g 相容性風險） | 記於 `docs/platform-notes.md` §6。**尚待團隊決定處置方式** |
| 115/09/23 | Claude Code | 撰寫 `deploy/provision_vms.sh`，以 KVM/libvirt + cloud image + cloud-init 建出 SPEC §3.1 的兩台 VM（Ubuntu 22.04.5） | 實際執行完成，兩台 VM 已建立並通過網路拓樸驗證（介面名、IP、互通性、外網），實測值記於 `platform-notes.md` §2。sudo 密碼由團隊自行輸入 |
| 115/09/23 | Claude Code | 查證 gtp5g 的 kernel 支援範圍（5.4–7.0.x），推翻自己前一日「6.17 可能編不過」的未查證推測 | 依官方 README 更正，並在 `platform-notes.md` §6 留下更正備查。**建 VM 的決策理由已改以拓樸需求、共用機隔離、快照回滾、對齊教材四點陳述** |
| 115/09/23 | 團隊 | **決策：不依賴培訓營 VM 映像檔，自行從上游原始碼建置整套環境**，但保留 free5GC / UERANSIM / OAI-MEP 三個上游元件 | 決策與理由寫入 SPEC §15.7（v1.3）與 §16 修訂紀錄 |
| 115/09/23 | Claude Code | 依上述決策撰寫 `deploy/versions.env` 與五支安裝腳本（gtp5g、Go、free5GC、UERANSIM、OAI-MEP） | gtp5g v0.9.16 與 UERANSIM v3.2.7 已在 VM 上實際建置成功；Go 1.24.5 經官方 checksum 驗證後安裝。**待團隊逐行審閱** |
| 115/09/23 | Claude Code | 撰寫 `configure_free5gc.sh` / `configure_ueransim.sh` / `install_services.sh` / `free5gc_start.sh` / `gen_topology_env.py`，完成雙 VM 拓樸設定與 UE 用戶資料建立 | **端到端實測成功**：UE 註冊、PDU session、`UE→Gateway→MEC App` 回 200、`POST /bsm` 回傳推論結果。五個關鍵問題的根因與解法記於 `platform-notes.md` §7。**待團隊逐行審閱** |
| 115/09/24 | Claude Code | 撰寫學生操作手冊 `docs/STUDENT_GUIDE.md` 與學生端工具（`sumo/build_networks.py`、`extract_stoplines.py`、`check_scenario.py`、`pack_delivery.py`、`training/make_proxy.py`） | 手冊中每一步都在 VM2（Linux）與全新 macOS 虛擬環境上實際執行驗證；`tests/test_student_guide.py` 自動檢查手冊中的場景檔。**待團隊逐行審閱，並由學生實際照做一次回饋** |
| 115/09/24 | Claude Code | **三個 SUMO 場景的具體參數值（駕駛參數、車流分配、主線 / 支道拆分）由 AI 依 SPEC §4.3 的設計目標提出並實測調整**；設計目標本身（幾何、車種比例、流量、異質性方向）仍為團隊擬定 | 3600 秒實測通過 SPEC §4.3「A、B 明顯偏斜」驗收。調參過程（含失敗的嘗試）記於手冊 5.3 節。**企劃書描述場景時須如實說明參數值的來源** |
| 115/09/24 | Claude Code | 資料管線修正：collect 改用 TraCI subscription、修正 sim_time 晚標一步、build 改串流、標籤改用逐步 TTC、SSM 輸出改 gzip | 串流 build 與舊版輸出逐筆位元相同（回歸驗證）；新增 12 項測試。**待團隊逐行審閱** |

<!-- 續填格式：
| 115/09/xx | Claude Code | 產生 xxx.py 初始骨架 | 逐行審閱並修改 xx 處；以 xxx 測試通過 |
-->

---

## 揭露邊界（自我檢查用）

團隊須能對下列各項明確回答「這是誰決定的」：

| 項目 | 歸屬 |
|---|---|
| 題目與差異化定位（SPEC §1） | 團隊 |
| 核心論證鏈 Q1–Q6（SPEC §2） | 團隊 |
| 系統架構與資料流（SPEC §3） | 團隊 |
| SUMO 場景幾何、車流、異質性設計（SPEC §4） | 團隊 |
| 特徵定義與標籤定義（SPEC §5） | 團隊 |
| 模型結構選型與蒸餾協定（SPEC §6、§7） | 團隊 |
| 評估指標與實驗設計（SPEC §8） | 團隊 |
| 程式碼初始骨架 | AI 輔助產生，**團隊逐行審閱修改（進行中，見上表）** |
| 環境除錯 | AI 輔助，團隊實機執行驗證 |
| 文字潤飾 | AI 輔助 |
| 規格未定義處的實作決定 | **AI 提出，待團隊確認**（清單見 SPEC §16 待確認事項） |
| **所有實驗數據** | **團隊實機量測，無任何 AI 生成數據** |

最後一列是紅線：任何圖表數字都必須能追溯到 `logs/` 下的 jsonl 原始紀錄。
