"""假 Pico：在電腦上模擬告警器，照 protocol.py 的格式收訊息、回 ack、用文字顯示燈號。

兩個用途：
  * 硬體還沒好之前，測轉發程式（relay.py）整條路是否正常
  * 給硬體組當「正確答案」：真的 Pico 收到同樣的訊息，行為應該跟這支一樣

用法：
    python alert_relay/fake_pico.py                 # 聽 0.0.0.0:5005
    python alert_relay/relay.py --pico 127.0.0.1    # 另開一個視窗跑轉發程式
"""
from __future__ import annotations

import argparse
import sys
import json
import socket
import time

from protocol import PICO_PORT

LIGHT = {"caution": "🟡 注意（黃燈 1 秒）", "danger": "🔴 危險（紅燈＋蜂鳴 0.5 秒）"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=PICO_PORT)
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)   # 背景執行或導向檔案時也即時印出
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", a.port))
    sock.settimeout(0.5)
    print(f"假 Pico 聽 UDP {a.port}（Ctrl+C 結束）")
    last_rx, link_up, counts = 0.0, False, {"caution": 0, "danger": 0, "heartbeat": 0}
    try:
        while True:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                if link_up and time.time() - last_rx > 3:
                    link_up = False
                    print("⚫ 3 秒沒收到訊息 → 綠燈熄滅（連線中斷）")
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                print(f"格式錯誤，略過：{data[:60]!r}")
                continue
            sock.sendto(json.dumps({"ack": msg.get("seq")}).encode(), addr)   # 先回 ack，再做燈號
            last_rx = time.time()
            if not link_up:
                link_up = True
                print("🟢 收到訊息 → 綠燈恢復")
            lvl = msg.get("level")
            counts[lvl] = counts.get(lvl, 0) + 1
            if lvl in LIGHT:
                tgt = f"，對象 {msg['target']}" if msg.get("target") else ""
                print(f"{LIGHT[lvl]}  路口 {msg.get('node')} 車 {msg.get('vid')}{tgt}")
    except KeyboardInterrupt:
        print(f"\n收到 注意 {counts['caution']}、危險 {counts['danger']}、心跳 {counts['heartbeat']} 則")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
