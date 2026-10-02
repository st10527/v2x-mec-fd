# 多路口 V2X 協同危險預警系統

**基於 MEC 邊緣軟標籤交換的跨路口風險預測**

2026 行動通訊實務競賽｜智慧數位應用組｜主軸一「智慧交通安全新生活」（5G-V2X 平台）

路口的碰撞常來自側面：支道車切出、左轉車穿越。競賽平台的範例規則只看同車道前車，在無號誌路口只抓到 30.8% 的危險。
本作品在 5G MEC 上部署路口危險預警服務：車輛每 0.1 秒經 5G 回報位置與速度，路口旁的邊緣節點預測每台車未來 3 秒的碰撞風險，
以「注意／危險」兩段式提醒；路口之間只交換每輪 24 KB 的預測結果共享經驗，不傳送原始車流。

---

## 主要結果

| 指標 | 結果 | 對照 |
|---|---|---|
| 無號誌路口危險事件召回（注意等級，1,017 件獨立事件） | **99.2%** | 平台範例規則 30.8% |
| 號誌路口危險事件召回（注意等級，336 件） | 98.2%，每次通過路口平均誤報 0.02 次 | — |
| 提前預警（號誌路口，規則與本作品都有警示的 257 件） | 平均早 1.9 秒（中位數 2.2 秒） | 規則在危險發生後約 0.1 秒才警示 |
| 警示往返延遲（經 free5GC 核心網，含推論） | 中位數 1.5 ms，99% 在 4 ms 內 | 公有雲網路往返：台北 6 ms（最慢 5% 達 36 ms）、東京 40 ms |
| 新路口只有 5 分鐘當地資料（強烈警報召回，4 次重複） | 協同訓練起點 72% | 從零開始 52%（±15%） |
| 路口間協作通訊量 | 每輪 24 KB | 傳送整個模型 189 KB |

> 所有車流與危險標籤皆來自 SUMO 1.27.0 模擬，尚未以真實路口資料驗證。

## 資料來源與重現

- **資料**：全部由 SUMO 模擬產生（場景檔在 `sumo/`），以 SSM 逐步量測 TTC／PET；未使用任何真實路口影像或個人資料。
- **可重現**：場景檔、亂數種子與程式固定；已實測 Windows 與 Linux 產生的資料集逐筆相同；macOS 上僅 1 筆特徵值有 0.00006 的浮點捨入差異（37.6 萬筆中），標籤完全相同。
- **切分**：每個路口依時間切成 70%（訓練）／15%（校準警示門檻）／15%（測試）；無號誌路口另以 6 段不同亂數種子、從未用於訓練的車流評估。

從頭重現（不需 5G 環境）：

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-student.txt
source .venv/bin/activate
python sumo/build_networks.py                                  # 建路網與停止線
for s in a b proxy; do python -m training.build_dataset collect --scenario $s; \
                       python -m training.build_dataset build   --scenario $s; done
python -m training.make_proxy                                  # 公開參考資料（SHA-256 6e9a298e…）
python -m training.train_local --node a && python -m training.train_local --node b   # 單獨訓練（round 0）
python -m training.tune_fd run --alpha 0.5 --lr 1e-3           # 協同訓練 5 輪（兩節點在同一程式內按輪同步）
python -m training.tune_fd select-events --split test --tags round0 alpha0.5_lr0.001   # 事件層級評估
bash deploy/run_tests.sh                                       # 自動化測試
```

正式結果是在 5G 平台上以 `training.distill` 經 MEP Gateway（Kong）交換軟標籤跑出；單機以 `tune_fd` 模擬同一算法，
因 CPU 與套件版本不同，數字會非常接近但不保證逐位元相同。

完整 5G 環境（free5GC v4.1.0、UERANSIM v3.2.7、gtp5g v0.9.16、OAI-MEP）由 `deploy/` 腳本從原始碼建置於兩台 Ubuntu 22.04 VM，
步驟見 [docs/platform-notes.md](docs/platform-notes.md)。

## 初賽必交項目對照

| 競賽要求 | 位置 |
|---|---|
| 1. 交通情境模擬器設定檔與說明 | `sumo/intersection_a`、`sumo/intersection_b`、`sumo/proxy_public`；說明見企劃書第四章（二）、[docs/STUDENT_GUIDE.md](docs/STUDENT_GUIDE.md) 第 4–5 章 |
| 2. UE 端資料傳送程式簡介 | [`ue/sumo_ue_sender.py`](ue/sumo_ue_sender.py)（檔頭說明；支援 SUMO 即時與重播兩種模式，綁定 `uesimtun0` 確保經過 5G 核心網） |
| 3. MEC V2X App 程式簡介 | [`mec_app/app.py`](mec_app/app.py)（推論與經驗交換端點）、[`mec_app/features.py`](mec_app/features.py)（特徵）、[`mec_app/model.py`](mec_app/model.py)（模型與兩段式判定） |
| 4. API 與資料格式說明 | 企劃書第四章（三）；[docs/SPEC.md](docs/SPEC.md) §5.1（上行訊息）、§7.2（交換介面）、§8.4（紀錄格式） |
| 5. Demo 影片或執行成果 | 企劃書第四章（四）實測成果與圖 1–4 |
| 6. 實驗 log 與效能分析圖表 | `results/`（實驗紀錄樣本）；評估與作圖程式：`training/events.py`、`training/lead_time.py`、`training/evaluate.py`、`analysis/` |
| 7. 簡短技術報告 | 提案企劃書；技術細節見 [docs/SPEC.md](docs/SPEC.md) |

## 目錄

| 路徑 | 內容 |
|---|---|
| `sumo/` | 三個場景的設定檔、路網建置與驗收工具 |
| `ue/` | UE 端傳送程式 |
| `mec_app/` | MEC App：特徵、預測模型、兩段式警示、規則基準線、經驗交換端點 |
| `training/` | 資料集建構、單獨訓練、協同訓練（軟標籤交換）、事件層級評估、新路口實驗 |
| `analysis/` | 作圖腳本 |
| `deploy/` | 5G 平台與 MEC 部署腳本（VM 建置、free5GC、UERANSIM、OAI-MEP、Kong 路由） |
| `tests/` | 自動化測試（`bash deploy/run_tests.sh`） |
| `docs/` | 規格書（SPEC.md）、平台建置紀錄、AI 使用揭露、學生操作手冊 |

## 揭露

開發過程使用 AI 輔助工具，使用範圍與方式記錄於 [docs/ai-disclosure.md](docs/ai-disclosure.md)；所有實驗數據皆為團隊實機量測。
`deploy/` 內的 SIM 金鑰與 webconsole 帳密為 free5GC／UERANSIM 官方文件的預設測試值，僅供模擬環境使用。

## 授權

MIT License，見 [LICENSE](LICENSE)。
