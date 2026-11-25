"""告警器（Pico）的 UDP 訊息格式——轉發程式、假告警程式、假 Pico 共用。

每則訊息是一個 UDP 封包，內容為一行 JSON（UTF-8）：

  轉發端 → Pico（port 5005）
    {"v": 1, "seq": 1234, "node": "B", "level": "danger",
     "vid": "E_left.85", "target": "W_straight.40", "ts": 1765432100.123}

    level：caution（注意）、danger（危險）、heartbeat（心跳，每秒一次）
    vid／target：被警示的車、警示針對的車（MEC 給不出對象時為 null）
    ts：轉發端送出的時間（轉發端自己的時鐘，秒）
    heartbeat 只有 v、seq、level、ts 四個欄位

  Pico → 轉發端（回到送出訊息的那個位址與 port）
    {"ack": 1234}          帶回同一個 seq，收到就立刻回，不要等燈號做完

Pico 端行為（交接文件 6.3）：
  caution   黃燈亮 1 秒
  danger    紅燈亮＋蜂鳴器響 0.5 秒
  heartbeat 綠燈閃一下；超過 3 秒沒收到任何訊息 → 綠燈熄滅（代表連線斷了）

延遲要分開報告（SPEC §13.3）：「UE → MEC → 產生警示」是 5G 系統的延遲（主打）；
「轉發送出 → Pico 收到」由 ack 的往返時間估計，是現場 WiFi 的開銷，另列並註明。
"""
from __future__ import annotations

import json
import time

VERSION = 1
PICO_PORT = 5005
HEARTBEAT_S = 1.0
LEVELS = {1: "caution", 2: "danger"}


def alert(seq: int, node: str, level: str, vid: str, target: str | None) -> bytes:
    return json.dumps({"v": VERSION, "seq": seq, "node": node, "level": level,
                       "vid": vid, "target": target, "ts": round(time.time(), 3)},
                      ensure_ascii=False).encode("utf-8")


def heartbeat(seq: int) -> bytes:
    return json.dumps({"v": VERSION, "seq": seq, "level": "heartbeat",
                       "ts": round(time.time(), 3)}).encode("utf-8")


def parse_ack(data: bytes) -> int | None:
    try:
        msg = json.loads(data.decode("utf-8"))
        return int(msg["ack"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None
