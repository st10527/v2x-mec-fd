"""假告警程式：不連遠端，直接送測試訊息給 Pico，給硬體組開發與驗收用。

依序送出 心跳 → 注意 → 危險 → 停止心跳 4 秒（Pico 應熄綠燈）→ 恢復，重複執行，
並統計 Pico 的 ack 往返時間——這就是測試文件要報的「轉發 → Pico」延遲。

用法：
    python alert_relay/fake_alerts.py --pico 192.168.8.50          # 真的 Pico
    python alert_relay/fake_alerts.py --pico 127.0.0.1 --rounds 1  # 搭配 fake_pico.py
"""
from __future__ import annotations

import argparse
import sys
import socket
import statistics
import time

from protocol import PICO_PORT, alert, heartbeat, parse_ack


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pico", required=True, help="Pico 的 IP")
    ap.add_argument("--port", type=int, default=PICO_PORT)
    ap.add_argument("--rounds", type=int, default=3, help="重複幾輪（0 = 一直送）")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)   # 背景執行或導向檔案時也即時印出
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    dest = (a.pico, a.port)
    seq, rtts, lost = 0, [], 0

    def send(payload_fn, label: str) -> None:
        nonlocal seq, lost
        seq += 1
        t0 = time.time()
        sock.sendto(payload_fn(seq), dest)
        try:
            while True:
                data, _ = sock.recvfrom(1024)
                if parse_ack(data) == seq:
                    rtt = (time.time() - t0) * 1000
                    rtts.append(rtt)
                    print(f"  {label:<10} seq {seq:<4} ack 往返 {rtt:6.1f} ms")
                    return
        except socket.timeout:
            lost += 1
            print(f"  {label:<10} seq {seq:<4} 沒收到 ack（Pico 沒開？IP 或 port 錯？）")

    r = 0
    print(f"送到 {dest[0]}:{dest[1]}（Ctrl+C 結束）")
    try:
        while a.rounds == 0 or r < a.rounds:
            r += 1
            print(f"第 {r} 輪")
            for _ in range(2):
                send(heartbeat, "心跳"); time.sleep(1)
            send(lambda s: alert(s, "B", "caution", "E_left.85", "W_straight.40"), "注意"); time.sleep(1.5)
            send(lambda s: alert(s, "B", "danger", "E_left.85", "W_straight.40"), "危險"); time.sleep(1.5)
            send(lambda s: alert(s, "A", "danger", "N_straight.12", None), "危險無對象"); time.sleep(1.5)
            print("  停止心跳 4 秒 → Pico 應熄綠燈"); time.sleep(4)
    except KeyboardInterrupt:
        pass
    if rtts:
        print(f"\nack 往返：中位數 {statistics.median(rtts):.1f} ms、最大 {max(rtts):.1f} ms（{len(rtts)} 則），遺失 {lost} 則")
    else:
        print(f"\n全部 {lost} 則都沒收到 ack")
    return 0 if rtts and not lost else 1


if __name__ == "__main__":
    raise SystemExit(main())
