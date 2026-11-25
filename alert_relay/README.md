# 告警轉發與 Pico 告警器

遠端 MEC 判出的警示，經現場筆電轉發到 Pico 告警器（燈號＋蜂鳴器）。

```
實驗室 VM1：MEC App → logs/inference_{a,b}.jsonl
        │  （SSH，用 ~/.ssh/config 的 v2x-vm1）
現場筆電：relay.py ──UDP 5005──▶ 旅行路由器 WiFi ──▶ Pico W
        ◀────────────── ack ────────────────────
```

| 檔案 | 用途 | 誰用 |
|---|---|---|
| `protocol.py` | 訊息格式（唯一的定義，有疑問以這裡為準） | 全部 |
| `relay.py` | 轉發程式：讀遠端 MEC 的警示，送給 Pico | 主力學生（現場） |
| `fake_alerts.py` | 不連遠端，直接送測試訊息給 Pico，並量 ack 往返時間 | 硬體組 |
| `fake_pico.py` | 在電腦上模擬 Pico，行為就是 Pico 該有的樣子 | 全部 |
| `pico_reference.py` | Pico W 的 MicroPython 參考程式（**未在實機測試**，起點用） | 硬體組 |

只用 Python 標準函式庫，Windows／macOS／Linux 都能直接跑，不需要另外安裝套件。

## 訊息格式

詳見 `protocol.py` 檔頭。重點：

- 轉發端 → Pico：UDP port 5005，一則一行 JSON，`level` 為 `caution`／`danger`／`heartbeat`
- Pico → 轉發端：收到就**立刻**回 `{"ack": seq}`，不要等燈號做完
- Pico 行為：注意黃燈 1 秒；危險紅燈＋蜂鳴 0.5 秒；心跳綠燈閃一下；3 秒沒收到訊息綠燈熄滅

## 硬體組：開發與驗收

1. 在自己電腦先跑一次「正確答案」，看 Pico 應該有什麼反應：
   ```
   python alert_relay/fake_pico.py --port 15005
   python alert_relay/fake_alerts.py --pico 127.0.0.1 --port 15005 --rounds 1
   ```
2. Pico 接上同一個 WiFi 後，改送給真的 Pico：
   ```
   python alert_relay/fake_alerts.py --pico <Pico 的 IP>
   ```
   每則都要收到 ack，最後印出的「ack 往返中位數」就是測試文件要報的「轉發 → Pico」延遲。

## 主力學生：現場轉發

先確認 `ssh v2x-vm1` 連得上，再：
```
python alert_relay/relay.py --pico <Pico 的 IP>            # 兩個路口都轉
python alert_relay/relay.py --pico <Pico 的 IP> --node B   # 只轉路口 B（展示協同開關時）
```
沒有 Pico 時用 `fake_pico.py` 代替（`--pico 127.0.0.1`）。結束按 Ctrl+C，會印出送出與 ack 統計；
每則警示的 ack 往返時間記在 `logs/relay_alerts.jsonl`。

同一台車的連續警示，間隔小於 1 秒算同一次，只送一則；等級升高（注意 → 危險）會立刻再送。
與事件評估的合併規則相同，Pico 才不會狂響。實測：路口 A 8,000 筆中模型判出 127 次危險 → 送出 9 則。

## 延遲要分開報告

- 「UE → 5G → MEC → 產生警示」：5G 系統的延遲，中位數約 1.5 ms（主打）
- 「轉發送出 → Pico 收到」：ack 往返時間，現場 WiFi 的開銷，**另列並註明**

兩段混在一起，WiFi 的數十毫秒會蓋過 5G 的低延遲（SPEC §13.3）。
