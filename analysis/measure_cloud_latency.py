"""MEC 與公有雲的延遲對照（SPEC §8.1、§2 Q6）：在 VM2 經 5G（uesimtun0）量 TCP 建立連線時間。

TCP 三向交握的建立時間 ≈ 一次網路往返，不含伺服器處理，最能直接比較「封包要跑多遠」。
目的地與初賽相同：路口 MEC（Kong 入口）與 AWS 台北、香港、東京區域的服務端點，另加平台教材的 Cloud server。

前置：VM1 要開 Cloud 對照路徑，UE 才出得了外網：
    bash deploy/setup_vm1.sh --cloud-mode            # 在 VM1
用法（在 VM2）：
    python analysis/measure_cloud_latency.py         # 每個目的地 20 次，結果存 results/latency_cloud_YYYYMMDD.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime
import pathlib
import statistics
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGETS = [
    ("MEC（路口邊緣節點）", "http://172.16.6.100/mec/v2x/a/healthz"),
    ("公有雲・台北 (AWS ap-east-2)", "https://ec2.ap-east-2.amazonaws.com"),
    ("公有雲・香港 (AWS ap-east-1)", "https://ec2.ap-east-1.amazonaws.com"),
    ("公有雲・東京 (AWS ap-northeast-1)", "https://ec2.ap-northeast-1.amazonaws.com"),
    ("平台 Cloud server（教材 p.69）", "http://140.120.108.48:18080"),
]


def connect_ms(url: str, iface: str) -> float | None:
    r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-m", "5", "--interface", iface,
                        "-w", "%{time_connect}", url], capture_output=True, text=True)
    try:
        v = float(r.stdout)
    except ValueError:
        return None
    return v * 1000 if v > 0 else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--interface", default="uesimtun0")
    a = ap.parse_args()
    today = datetime.date.today().isoformat()
    out = ROOT / "results" / f"latency_cloud_{today.replace('-', '')}.csv"
    rows = []
    for name, url in TARGETS:
        vals = [v for v in (connect_ms(url, a.interface) for _ in range(a.n)) if v is not None]
        if not vals:
            print(f"{name:<32} 連不到（Cloud 對照路徑開了嗎？）")
            continue
        vals.sort()
        p95 = vals[min(len(vals) - 1, int(len(vals) * 0.95))]
        med = statistics.median(vals)
        print(f"{name:<32} 中位數 {med:6.1f} ms、95 百分位 {p95:6.1f} ms（成功 {len(vals)}/{a.n}）")
        rows.append([name, f"{med:.1f}", f"{p95:.1f}", f"{len(vals)}/{a.n}",
                     f"UE 經 5G 核心網（{a.interface}）TCP 建立連線時間", today])
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["目的地", "中位數_ms", "95百分位_ms", "成功次數", "量測方式", "量測日期"])
        w.writerows(rows)
    print(f"已存 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
