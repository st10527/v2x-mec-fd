"""UE 端 V2X 訊息傳送程式（SPEC §3.2、§5.1）。

改寫自平台範例 KevinTseng-0430/SUMO-V2X-TTC 的 UE sender，主要差異：
  * 支援 --intersection A|B，兩個路口各跑一份，打到不同的 Kong 路由
  * 訊息欄位改為 SPEC §5.1 的格式（參考 SAE J2735 BSM）
  * 量測 T1 與 T4（SPEC §8.2），把往返各段時間寫進 UE 端 log
  * 除了 TraCI 即時模式，另提供 replay 模式：直接重播
    build_dataset collect 錄下的 states.jsonl.gz，不需要跑 SUMO

封包路徑（SPEC §3.2）：
    UE(uesimtun0) -> UERANSIM -> free5GC(N3->UPF->N6) -> iptables DNAT
    -> Kong -> MEC App

--interface uesimtun0 會把 socket 綁到該介面的位址，確保流量真的走過
5G 核網而不是從主機直接抄捷徑到 MEC App。這件事會被評審問，
走錯路徑的話 SPEC §8.1 的 MEC vs Cloud 延遲對照就沒有意義。

用法：
    # 即時模式（VM2，需要 SUMO）
    python ue/sumo_ue_sender.py --intersection A --interface uesimtun0

    # 重播模式（任何機器）
    python ue/sumo_ue_sender.py --intersection A --replay sumo/intersection_a/states.jsonl.gz

    # Cloud 對照組（SPEC §8.1 的 MEC vs Cloud 延遲）
    python ue/sumo_ue_sender.py --intersection A --target http://<cloud>/v2x
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import re
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import httpx

from mec_app import config
from training.build_dataset import bsm_record


def interface_address(name: str) -> str:
    """取得指定網卡的 IPv4 位址。

    UE 的 tun 介面（uesimtun0）位址由 free5GC 動態配發（10.60.0.x），
    不能寫死，每次重啟 UERANSIM 都可能不同。
    """
    for cmd in (["ip", "-4", "addr", "show", "dev", name], ["ifconfig", name]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=5).stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    raise SystemExit(
        f"找不到介面 {name} 的 IPv4 位址。請確認 UERANSIM 已啟動且 "
        f"`ip addr show {name}` 看得到 10.60.0.x（SPEC §3.1）。")


def build_client(interface: str | None, timeout: float) -> httpx.Client:
    """建立 HTTP client，必要時綁定到 UE 的 tun 介面。"""
    if not interface:
        return httpx.Client(timeout=timeout)
    addr = interface_address(interface)
    print(f"綁定 {interface} ({addr}) —— 流量將經 UERANSIM / free5GC")
    return httpx.Client(timeout=timeout,
                        transport=httpx.HTTPTransport(local_address=addr))


def target_url(intersection: str, override: str | None, cfg: dict) -> str:
    """預設打到 MEP Gateway 上本路口的 Kong 路由（SPEC §3.3）。"""
    if override:
        return override.rstrip("/")
    node = intersection.lower()
    gw = cfg["network"]["mep_gateway"]
    route = cfg["nodes"][node]["kong"]["infer_route"]
    return f"http://{gw}{route}"


def send_one(client: httpx.Client, url: str, rec: dict) -> dict:
    """送出一筆 BSM 並量測往返時間（SPEC §8.2 的 T1 與 T4）。"""
    rec = dict(rec, msg_id=str(uuid.uuid4()), ts_ue=time.time())
    t0 = rec["ts_ue"]
    try:
        r = client.post(f"{url}/bsm", json=rec)
        t_back = time.time()
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        return {"msg_id": rec["msg_id"], "error": f"{type(e).__name__}: {e}",
                "ts_ue": t0}
    return {
        "msg_id": rec["msg_id"], "vehicle_id": rec["vehicle_id"],
        "ts_ue": t0, "ts_ue_recv": t_back,
        # MEC App 在回應中帶回自己的時間戳，UE 端據此拆解各段
        "t1_uplink_ms": round((body["ts_recv"] - t0) * 1000, 3),
        "t2_features_ms": round((body["ts_window_ready"] - body["ts_recv"]) * 1000, 3),
        "t3_inference_ms": round((body["ts_infer_done"] - body["ts_window_ready"]) * 1000, 3),
        "t4_downlink_ms": round((t_back - body["ts_resp"]) * 1000, 3),
        "t_total_ms": round((t_back - t0) * 1000, 3),
        "risk_pred": body.get("risk_pred"), "risk_rule": body.get("risk_rule"),
        "ttc_now": body.get("ttc_now"),
    }


def iter_replay(path, step_length: float, realtime: bool):
    """重播 states.jsonl(.gz)，依 sim_time 分組逐步送出。"""
    p = pathlib.Path(path)
    if not p.exists():
        raise SystemExit(f"{p} 不存在。請先執行 "
                         "`python -m training.build_dataset collect --scenario ...`")
    opener = gzip.open if p.suffix == ".gz" else open
    batch, cur = [], None
    with opener(p, "rt", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            rec = json.loads(raw)
            t = rec["sim_time"]
            if cur is not None and t != cur:
                yield cur, batch
                if realtime:
                    time.sleep(step_length)
                batch = []
            cur = t
            batch.append(rec)
    if batch:
        yield cur, batch


def iter_traci(scenario: str, cfg: dict, inter: str):
    """即時模式：TraCI 驅動 SUMO，每步產出該步所有車輛的 BSM。

    狀態擷取直接呼叫 training.build_dataset.iter_states——與建資料集用的是
    同一段程式碼，上線送出的 BSM 與訓練資料因此不會分岔。
    """
    try:
        import traci
    except ImportError:
        raise SystemExit("找不到 traci。請照 docs/STUDENT_GUIDE.md 第 2 章安裝 SUMO，"
                         "或改用 --replay 模式。")
    from training.build_dataset import _sumo_binary, iter_states
    scen = cfg["sumo"]["scenarios"][scenario]
    sumocfg = config.path(scen["dir"], scen["config"])
    binary = _sumo_binary()
    if sys.stdout.isatty() and binary == "sumo":
        binary = "sumo-gui"
    traci.start([binary, "-c", str(sumocfg),
                 "--step-length", str(cfg["sumo"]["step_length"])])
    try:
        for t, batch in iter_states(traci, inter):
            if batch:
                yield t, batch
    finally:
        traci.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--intersection", required=True, choices=["A", "B", "a", "b"])
    ap.add_argument("--interface", default=None,
                    help="綁定的網卡，VM2 上應為 uesimtun0")
    ap.add_argument("--target", default=None,
                    help="覆寫目標 URL；Cloud 對照組用（SPEC §8.1）")
    ap.add_argument("--replay", default=None,
                    help="重播 states.jsonl(.gz) 而非跑 SUMO")
    ap.add_argument("--realtime", action="store_true",
                    help="重播時依 step-length 放慢到真實時間")
    ap.add_argument("--limit", type=int, default=None, help="只送前 N 筆")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--out", default=None,
                    help="UE 端延遲 log 路徑，預設 logs/ue_{路口}.jsonl")
    a = ap.parse_args(argv)

    cfg = config.load()
    inter = a.intersection.upper()
    node = inter.lower()
    url = target_url(inter, a.target, cfg)
    out = pathlib.Path(a.out or config.path("logs", f"ue_{node}.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)

    source = (iter_replay(a.replay, float(cfg["sumo"]["step_length"]), a.realtime)
              if a.replay else
              iter_traci(cfg["nodes"][node]["scenario"], cfg, inter))

    print(f"路口 {inter} -> {url}\n延遲 log -> {out}")
    sent, errors, lat = 0, 0, []
    client = build_client(a.interface, a.timeout)
    t_start = time.time()
    try:
        with client, open(out, "a", encoding="utf-8") as fh:
            for sim_t, batch in source:
                for rec in batch:
                    rec.setdefault("intersection", inter)
                    res = send_one(client, url, rec)
                    fh.write(json.dumps(res, ensure_ascii=False) + "\n")
                    sent += 1
                    if "error" in res:
                        errors += 1
                        if errors <= 3:
                            print(f"  送出失敗：{res['error']}")
                    else:
                        lat.append(res["t_total_ms"])
                    if a.limit and sent >= a.limit:
                        raise StopIteration
    except (KeyboardInterrupt, StopIteration):
        pass

    dur = time.time() - t_start
    print(f"\n送出 {sent:,} 筆（失敗 {errors}）耗時 {dur:.1f}s")
    if lat:
        arr = sorted(lat)
        p50 = arr[len(arr) // 2]
        p95 = arr[min(len(arr) - 1, int(len(arr) * 0.95))]
        print(f"端到端延遲 T_total：中位數 {p50:.2f} ms、p95 {p95:.2f} ms、"
              f"最大 {arr[-1]:.2f} ms")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
