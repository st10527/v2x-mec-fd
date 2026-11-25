"""告警轉發程式：遠端 MEC 的警示 → 現場的 Pico 告警器。

遠端的 MEC App 不能直接把 UDP 送到現場的 Pico（中間隔著學校與會場的 NAT），所以由
現場的學生筆電轉發：

    VM1 的 logs/inference_{a,b}.jsonl ──（ssh tail）──▶ 本程式 ──（UDP 5005）──▶ Pico

讀的是 MEC 端的推論紀錄，用的是學生筆電已設好的 SSH 連線（~/.ssh/config 的 v2x-vm1），
不必在 VM 上另開服務或改 Kong。SSH 斷線會自動重連。

規則：
  * 只轉發模型的判定（risk_pred = 1 注意、2 危險），平台規則的判定不轉發
  * 同一台車的連續警示，間隔小於 1 秒算同一次，只送一則；等級升高（注意 → 危險）則立刻再送
    ——與事件評估同一個合併規則（training/events.py 的 EPISODE_GAP_S），Pico 才不會狂響
  * 每秒送一則心跳；Pico 3 秒沒收到東西就熄綠燈，現場一眼看得出連線斷了
  * Pico 每收到一則就回 ack，本程式記下往返時間，寫進 logs/relay_alerts.jsonl

用法（在現場筆電上）：
    python alert_relay/relay.py --pico 192.168.8.50            # Pico 的 IP
    python alert_relay/relay.py --pico 192.168.8.50 --node B   # 只轉發路口 B
    python alert_relay/relay.py --pico 127.0.0.1               # 搭配 fake_pico.py 在本機測試
"""
from __future__ import annotations

import argparse
import sys
import json
import pathlib
import queue
import select
import signal
import socket
import statistics
import subprocess
import threading
import time

from protocol import HEARTBEAT_S, LEVELS, PICO_PORT, alert, heartbeat, parse_ack

ROOT = pathlib.Path(__file__).resolve().parents[1]
EPISODE_GAP_S = 1.0          # 與 training/events.py 相同：同車間隔 < 1 秒算同一次警示
REMOTE_LOGS = "~/v2x-mec-fd/logs/inference_a.jsonl ~/v2x-mec-fd/logs/inference_b.jsonl"


def ssh_reader(host: str, out: "queue.Queue[tuple[str, dict]]", stop: threading.Event) -> None:
    """持續 tail 遠端兩份推論紀錄，把 (路口, 紀錄) 放進佇列；斷線 3 秒後重連。"""
    cmd = ["ssh", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2", host,
           f"tail -n0 -F {REMOTE_LOGS} 2>/dev/null"]
    while not stop.is_set():
        node = None
        print(f"[ssh] 連線 {host}，讀取 MEC 推論紀錄…", flush=True)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1)
        except FileNotFoundError:
            print("[ssh] 找不到 ssh 指令，請確認已安裝 OpenSSH 用戶端", flush=True)
            return
        for line in proc.stdout:
            if stop.is_set():
                break
            line = line.strip()
            if line.startswith("==> "):                    # tail 切換檔案時的標頭
                node = "A" if "inference_a" in line else "B" if "inference_b" in line else None
                continue
            if not line or node is None:
                continue
            try:
                out.put((node, json.loads(line)))
            except json.JSONDecodeError:
                continue
        proc.kill()
        if not stop.is_set():
            print("[ssh] 連線中斷，3 秒後重連", flush=True)
            time.sleep(3)


def _stop_on_signal(signum, frame) -> None:
    raise KeyboardInterrupt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pico", required=True, help="Pico 的 IP（本機測試用 127.0.0.1）")
    ap.add_argument("--port", type=int, default=PICO_PORT)
    ap.add_argument("--host", default="v2x-vm1", help="~/.ssh/config 裡 VM1 的名稱")
    ap.add_argument("--node", choices=["A", "B"], default=None, help="只轉發某個路口")
    ap.add_argument("--log", default=str(ROOT / "logs" / "relay_alerts.jsonl"))
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)   # 背景執行或導向檔案時也即時印出

    # Ctrl+C 之外，被一般方式終止（kill、關視窗）時也照樣收尾、印出統計
    signal.signal(signal.SIGTERM, _stop_on_signal)
    records: "queue.Queue[tuple[str, dict]]" = queue.Queue()
    stop = threading.Event()
    threading.Thread(target=ssh_reader, args=(a.host, records, stop), daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    dest = (a.pico, a.port)
    log_path = pathlib.Path(a.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logf = open(log_path, "a", encoding="utf-8")

    seq = 0
    pending: dict[int, tuple[float, dict]] = {}       # seq -> (送出時間, 內容)
    last_alert: dict[tuple[str, str], tuple[float, int]] = {}   # (路口, 車) -> (最後一次警示時間, 等級)
    rtts: list[float] = []
    sent = acked = 0
    next_hb = time.time()
    print(f"轉發到 Pico {dest[0]}:{dest[1]}（Ctrl+C 結束）", flush=True)

    try:
        while True:
            now = time.time()
            # 1) 心跳
            if now >= next_hb:
                seq += 1
                sock.sendto(heartbeat(seq), dest)
                next_hb = now + HEARTBEAT_S
            # 2) 新的推論紀錄 → 警示
            try:
                while True:
                    node, r = records.get_nowait()
                    lvl = r.get("risk_pred")
                    if lvl not in LEVELS or (a.node and node != a.node):
                        continue
                    key = (node, r.get("vehicle_id"))
                    prev = last_alert.get(key)
                    last_alert[key] = (now, lvl)
                    if prev and now - prev[0] < EPISODE_GAP_S and lvl <= prev[1]:
                        continue                              # 同一次警示的延續，不重送
                    seq += 1
                    msg = {"seq": seq, "node": node, "level": LEVELS[lvl],
                           "vid": r.get("vehicle_id"), "target": r.get("conflict_with")}
                    sock.sendto(alert(seq, node, msg["level"], msg["vid"], msg["target"]), dest)
                    pending[seq] = (time.time(), msg)
                    sent += 1
                    print(f"→ [{node}] {msg['level']:<7} {msg['vid']}"
                          + (f" ← {msg['target']}" if msg["target"] else ""), flush=True)
            except queue.Empty:
                pass
            # 3) Pico 的 ack
            try:
                while True:
                    data, _ = sock.recvfrom(1024)
                    k = parse_ack(data)
                    if k in pending:
                        t0, msg = pending.pop(k)
                        rtt = (time.time() - t0) * 1000
                        rtts.append(rtt)
                        acked += 1
                        logf.write(json.dumps({**msg, "sent_ts": round(t0, 3), "ack_rtt_ms": round(rtt, 2)},
                                              ensure_ascii=False) + "\n")
                        logf.flush()
            except (BlockingIOError, ConnectionResetError):
                pass
            # 超過 3 秒沒 ack 的視為遺失
            for k in [k for k, (t0, _) in pending.items() if time.time() - t0 > 3]:
                pending.pop(k)
            # 等到 Pico 有回應或 10 ms 到期就醒來。不可用固定 sleep：ack 會等到下一輪才被讀到，
            # 量到的往返時間被灌水最多 10 ms（實測本機假 Pico 從 0.4 ms 變成 11 ms）
            select.select([sock], [], [], 0.01)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        logf.close()
        print(f"\n送出警示 {sent} 則，收到 ack {acked} 則", end="")
        if rtts:
            print(f"；轉發 → Pico 往返中位數 {statistics.median(rtts):.1f} ms、最大 {max(rtts):.1f} ms", end="")
        print(f"\n紀錄：{log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
