"""端到端整合：真的起 server、真的送 BSM、真的產圖。

鏈路：UE sender -> uvicorn(MEC App) -> logs/ue_a.jsonl + logs/inference_a.jsonl
      -> analysis/plot_*.py -> figures/*.png

與 VM 上的正式部署相比，這裡少的只有 UERANSIM / free5GC / Kong 這段網路路徑，
應用層行為完全相同。因此本機能過，VM 上剩下的就是網路設定問題。

一樣會清掉自己產生的所有檔案。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx                                          # noqa: E402
from mec_app import config                            # noqa: E402
from training.build_dataset import bsm_record         # noqa: E402

PASS, FAIL = [], []
PY_BIN = str(ROOT / ".venv" / "bin" / "python")
PORT = 5102                                           # 避開正式的 5002


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


cfg = config.load()
STEP = cfg["sumo"]["step_length"]
tmp = pathlib.Path(ROOT / "data" / ".integration_tmp")
tmp.mkdir(parents=True, exist_ok=True)

created = [
    config.path("logs/ue_a.jsonl"),
    config.path("logs/inference_a.jsonl"),
    config.path("logs/fd_rounds.jsonl"),
    config.path("figures/fig1_convergence.png"),
    config.path("figures/fig2_bytes.png"),
    config.path("figures/fig3_latency.png"),
]
backups = {}
for p in created:
    if p.exists():
        b = tmp / p.name
        shutil.copy2(p, b)
        backups[p] = b
        p.unlink()

# --- 造重播用的 states.jsonl：三台車，其中兩台在同車道上逐漸逼近 ---
import gzip
states = tmp / "states.jsonl.gz"
with gzip.open(states, "wt", encoding="utf-8") as fh:
    for i in range(60):
        t = round(i * STEP, 2)
        for vid, y0, spd, lane in (("lead", 45.0, 8.0, "e1_0"),
                                   ("ego", 0.0, 20.0, "e1_0"),
                                   ("cross", 30.0, 12.0, "e2_0")):
            heading = 90.0 if vid == "cross" else 0.0
            x = (30.0 - spd * t) if vid == "cross" else 0.0
            y = y0 if vid == "cross" else y0 + spd * t
            fh.write(json.dumps(bsm_record(
                vehicle_id=vid, inter="A", sim_time=t, x=x, y=y,
                speed=spd, accel=0.0, heading=heading,
                lane_id=lane, signal_phase="G")) + "\n")

proc = None
try:
    print("\n[server] 啟動 uvicorn")
    env = dict(os.environ, MEC_NODE="a", MEC_LOG_LEVEL="WARNING",
               PYTHONPATH=str(ROOT))
    proc = subprocess.Popen(
        [PY_BIN, "-m", "uvicorn", "mec_app.app:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    base = f"http://127.0.0.1:{PORT}"
    up = False
    for _ in range(100):
        if proc.poll() is not None:
            break
        try:
            if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
                up = True
                break
        except Exception:
            time.sleep(0.2)
    if not up and proc.poll() is not None:
        print(proc.stdout.read()[-2000:])
    check("uvicorn 起得來且 /healthz 回 200", up)

    print("\n[ue] 重播模式送 BSM")
    r = subprocess.run(
        [PY_BIN, "ue/sumo_ue_sender.py", "--intersection", "A",
         "--replay", str(states), "--target", base, "--limit", "150"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(r.stdout[-1500:], r.stderr[-1500:])
    check("UE sender 正常結束（零失敗）", r.returncode == 0, r.stderr[-200:])
    check("印出端到端延遲統計", "端到端延遲" in r.stdout, r.stdout[-200:])

    ue_log = config.path("logs/ue_a.jsonl")
    check("logs/ue_a.jsonl 已產生", ue_log.exists())
    rows = [json.loads(x) for x in ue_log.read_text().strip().split("\n")]
    check("送出 150 筆", len(rows) == 150, str(len(rows)))
    check("沒有任何一筆失敗", all("error" not in r_ for r_ in rows))
    fields = set(cfg["logs"]["ue"]["fields"])
    check("欄位與 config.yaml 的 logs.ue 一致",
          fields <= set(rows[0]), str(sorted(fields - set(rows[0]))))
    check("T1–T4 四段都量到（SPEC §8.2）",
          all(r_[k] is not None for r_ in rows for k in
              ("t1_uplink_ms", "t2_features_ms", "t3_inference_ms", "t4_downlink_ms")))
    t3 = [r_["t3_inference_ms"] for r_ in rows]
    check("T3 模型推論 < 5 ms（SPEC §6.1 預期 < 1 ms，含 Python 開銷放寬）",
          float(np.median(t3)) < 5.0, f"中位數 {np.median(t3):.3f} ms")
    tot = [r_["t_total_ms"] for r_ in rows]
    check("T_total = T1+T2+T3+T4 誤差 < 1 ms",
          all(abs(r_["t_total_ms"] - sum(r_[k] for k in
              ("t1_uplink_ms", "t2_features_ms", "t3_inference_ms", "t4_downlink_ms")))
              < 1.0 for r_ in rows))
    print(f"    本機迴路端到端中位數 {np.median(tot):.2f} ms"
          f"（其中推論 {np.median(t3):.3f} ms）")

    print("\n[ue] 模型與規則兩路都有輸出")
    check("risk_rule 有判出非安全的情況",
          any(r_["risk_rule"] != 0 for r_ in rows),
          str({r_["risk_rule"] for r_ in rows}))
    check("risk_pred 在視窗就緒後有值",
          any(r_["risk_pred"] in (0, 1, 2) for r_ in rows))

    inf_log = config.path("logs/inference_a.jsonl")
    check("MEC 端 inference_a.jsonl 也有寫", inf_log.exists())
    ilines = [json.loads(x) for x in inf_log.read_text().strip().split("\n")]
    check("MEC 端筆數與 UE 端一致", len(ilines) == 150, str(len(ilines)))
    check("MEC 端欄位符合 SPEC §8.4",
          list(ilines[0]) == cfg["logs"]["inference"]["fields"])

    print("\n[analysis] 從 log 產出三張圖（SPEC §8.3）")
    # 造一份 fd_rounds.jsonl 供圖 1、圖 2 使用
    from mec_app import jsonlog
    w = jsonlog.JsonlWriter("fd_rounds")
    for node, base_f1 in (("a", 0.41), ("b", 0.38)):
        for rnd in range(6):
            w.write(round=rnd, node=node,
                    bytes_sent=0 if rnd == 0 else cfg["fd"]["logits"]["raw_bytes"],
                    local_f1=round(0.80 + 0.01 * rnd, 4),
                    cross_f1=round(base_f1 + 0.035 * rnd, 4),
                    duration_s=round(12.0 + rnd, 2))

    for script, fig in (("analysis.plot_convergence", "fig1_convergence.png"),
                        ("analysis.plot_bytes", "fig2_bytes.png"),
                        ("analysis.plot_latency", "fig3_latency.png")):
        rr = subprocess.run([PY_BIN, "-m", script], cwd=str(ROOT), env=env,
                            capture_output=True, text=True, timeout=180)
        if rr.returncode != 0:
            print(rr.stdout[-800:], rr.stderr[-800:])
        f = config.path("figures", fig)
        check(f"{script} 產出 {fig}",
              rr.returncode == 0 and f.exists() and f.stat().st_size > 8000,
              rr.stderr[-200:])

    r2 = subprocess.run([PY_BIN, "-m", "analysis.plot_bytes"], cwd=str(ROOT),
                        env=env, capture_output=True, text=True)
    check("圖 2 同時報告原始與上線兩個倍數（SPEC §8.3 要求不可混用）",
          "原始" in r2.stdout and "上線" in r2.stdout, r2.stdout[-200:])
    check("圖 2 的倍數為 8.x（Q5 論證數字）",
          "8.0" in r2.stdout or "8.1" in r2.stdout, r2.stdout[-120:])

    print("\n[analysis] 缺 log 時給的是可行動的錯誤訊息")
    saved = config.path("logs/fd_rounds.jsonl").read_text()
    config.path("logs/fd_rounds.jsonl").unlink()
    rr = subprocess.run([PY_BIN, "-m", "analysis.plot_convergence"], cwd=str(ROOT),
                        env=env, capture_output=True, text=True)
    check("缺 fd_rounds.jsonl 時明講下一步是跑哪支腳本",
          "train_local" in rr.stderr and "distill" in rr.stderr, rr.stderr[-200:])
    config.path("logs/fd_rounds.jsonl").write_text(saved)

finally:
    print("\n[teardown] 收拾")
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    for p in created:
        pathlib.Path(p).unlink(missing_ok=True)
    for p, b in backups.items():
        shutil.move(b, p)
    shutil.rmtree(tmp, ignore_errors=True)
    left = [str(p.relative_to(ROOT)) for p in
            list(config.path("figures").glob("*.png"))
            + list(config.path("logs").glob("*.jsonl"))]
    print(f"  殘留檔案：{left or '無'}")

print(f"\n{'='*58}\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1)
print("全部通過")
