# 實驗紀錄樣本

本資料夾將放入企劃書各項數字所依據的實驗紀錄樣本（JSON Lines）與彙整結果，預計於 115/10/05 補上：

- 事件層級評估結果（兩段式警示的召回、誤報、精準度；平台規則對照）
- 提前預警時間的逐件比對紀錄
- 警示往返延遲（UE 經 5G 核心網到 MEC，逐筆）
- 協同訓練各輪紀錄與新路口實驗結果

產生這些紀錄的程式皆已公開：`training/events.py`、`training/lead_time.py`、`training/tune_fd.py`、`training/adapt.py`、`ue/sumo_ue_sender.py`。
