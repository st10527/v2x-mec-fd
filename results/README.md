# 實驗紀錄樣本

README「主要結果」各項數字所依據的紀錄（115/11/25 新環境；實驗主機重灌後以同一份資料與設定重新訓練）。
所有檔案都可由下列程式重新產生；車流與危險標籤全部來自 SUMO 1.27.0 模擬。

| 檔案 | 內容 | 產生方式 |
|---|---|---|
| `final_numbers.json` | 兩路口的事件召回、每次通過誤報、強烈警報召回與精準度、平台規則對照、A 模型搬到 B 的結果 | `python -m analysis.final_numbers` |
| `lead_time_events_a.jsonl` | 號誌路口測試時段 336 件危險事件逐件比對：模型與規則第一次警示的時間、提早秒數 | `python -m training.lead_time --node a` |
| `latency_cloud_20261125.csv` | UE 經 5G 到路口 MEC 與公有雲（AWS 台北、香港、東京）的 TCP 建立時間，各 20 次 | `python analysis/measure_cloud_latency.py`（VM2，需 VM1 開 `--cloud-mode`） |
| `newsite_summary.csv` | 新路口只有 5 分鐘資料時，不同起點的強烈警報召回（4 次重複的平均與標準差） | 由 `newsite_runs/` 彙整 |
| `newsite_runs/adapt_b_seed{1..4}.json` | 新路口實驗每次重複的原始結果 | `python -m training.adapt --site b --minutes 5 --seed N --start ...` |

警示往返延遲（UE 經 5G 到 MEC，逐筆）由 `ue/sumo_ue_sender.py` 寫在 `logs/ue_{a,b}.jsonl`；
協同訓練各輪紀錄在 `logs/fd_rounds.jsonl`（`python -m training.distill --node a|b`）。
