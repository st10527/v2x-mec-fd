"""SUMO 輸出 -> 特徵 + 標籤（SPEC §5.2、§5.4、§6.2）。

分成兩個子命令，中間以 SPEC §5.1 的 BSM 格式落地：

    collect   TraCI 驅動 SUMO，逐步錄下每車狀態 -> states.jsonl.gz   [需要 SUMO]
    build     states.jsonl.gz + ssm.xml -> data/{scenario}.npz       [純 Python]

拆兩段的理由：
  1. collect 產出的每一筆就是 UE 端會送出的 BSM，離線資料集與線上推論
     因此吃到同一種輸入，不會出現「訓練時有某欄位、上線時沒有」的分岔；
  2. 模擬跑一次很貴（3600 秒），特徵定義卻可能要調好幾次。分開之後
     調特徵不必重跑 SUMO；
  3. build 不依賴 SUMO，可在任何機器上重現，符合 SPEC §8.4 的可重現要求。

標籤（SPEC §5.4）取自 SUMO SSM device 的輸出，只用於訓練與評估，
即時推論時不可取得——這正是本專案是「預測」而非「偵測」的根據。

用法：
    python -m training.build_dataset collect --scenario a
    python -m training.build_dataset build   --scenario a
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import pathlib
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

import numpy as np

from mec_app import config, features as F

ROOT = config.ROOT


# ---------------------------------------------------------------------------
# collect：TraCI -> states.jsonl.gz
# ---------------------------------------------------------------------------
def collect(scenario: str, out: pathlib.Path | None = None,
            max_seconds: float | None = None, seed: int | None = None,
            ssm_out: pathlib.Path | None = None) -> pathlib.Path:
    """以 TraCI 驅動 SUMO 並錄下每步每車狀態。需要 SUMO 1.27.0。

    每一筆的欄位與 SPEC §5.1 的 BSM 完全一致。狀態擷取由 iter_states() 負責，
    ue/sumo_ue_sender.py 的即時模式也用同一個函式——訓練資料與上線資料
    必須出自同一段程式碼，不得分岔。
    """
    try:
        import traci                                     # noqa: F401
    except ImportError:
        sys.exit("找不到 traci。請照 docs/STUDENT_GUIDE.md 第 2 章安裝 SUMO 1.27.0。")
    import traci

    cfg = config.load()
    scen = cfg["sumo"]["scenarios"][scenario]
    sumocfg = config.path(scen["dir"], scen["config"])
    if not sumocfg.exists():
        sys.exit(f"{sumocfg} 不存在。請先照 docs/STUDENT_GUIDE.md 第 4 章建立場景。")

    net = config.path(scen["dir"], scen["config"].replace(".sumocfg", ".net.xml"))
    if not net.exists():
        sys.exit(f"找不到路網 {net.name}。請先執行：python sumo/build_networks.py")

    out = out or config.path(scen["dir"], "states.jsonl.gz")
    limit = max_seconds or float(cfg["sumo"]["min_sim_seconds"])
    inter = _intersection_of(scenario, cfg)

    import time as _time
    t0 = _time.time()
    cmd = [_sumo_binary(), "-c", str(sumocfg), "--step-length", str(cfg["sumo"]["step_length"])]
    if seed is not None:                 # 同一場景、不同亂數種子 → 獨立的另一段車流（額外測試資料用）
        cmd += ["--seed", str(seed)]
    if ssm_out is not None:
        # SUMO 把相對路徑解讀成「相對於 sumocfg 所在目錄」，一律給絕對路徑
        cmd += ["--device.ssm.file", str(pathlib.Path(ssm_out).resolve())]
    traci.start(cmd)
    n = 0
    last_report = 0.0
    try:
        with gzip.open(out, "wt", encoding="utf-8") as fh:
            for sim_t, batch in iter_states(traci, inter, limit):
                for rec in batch:
                    fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
                n += len(batch)
                if sim_t - last_report >= 300:            # 每模擬 5 分鐘回報一次進度
                    last_report = sim_t
                    print(f"  模擬時間 {sim_t:6.0f}/{limit:.0f} s，已錄 {n:,} 筆，"
                          f"實際耗時 {_time.time() - t0:5.0f} s", flush=True)
    finally:
        traci.close()          # SUMO 在 close 時才把剩下的 SSM 衝突寫進 ssm.xml.gz
    print(f"已錄下 {n:,} 筆狀態 -> {out}（實際耗時 {_time.time() - t0:.0f} s）")
    return out


def _sumo_binary() -> str:
    """優先用 PATH 上的 sumo；pip 裝的 eclipse-sumo 則從套件內取得。"""
    import shutil
    if shutil.which("sumo"):
        return "sumo"
    try:
        import sumo as _sumo_pkg                           # eclipse-sumo 套件
        cand = pathlib.Path(_sumo_pkg.SUMO_HOME) / "bin" / "sumo"
        for c in (cand, cand.with_suffix(".exe")):
            if c.exists():
                return str(c)
    except ImportError:
        pass
    return "sumo"


def iter_states(traci_mod, inter: str, limit: float | None = None):
    """逐步推進模擬，每步產出 (sim_time, [BSM, ...])。

    用 TraCI subscription 一次取回所有車的狀態。原本逐車逐項呼叫（每車每步 8 次
    TraCI 往返），路口 A 這種每方向 1200 輛的場景跑 3600 秒會慢到不可行；
    subscription 讓每步只剩一次往返。

    號誌狀態也不逐車查：每步對每個號誌查一次整串燈號，再依「哪條進入車道對應
    哪個燈號」查表。沒有號誌的車道一律視為綠燈（與 SPEC §5.2 第 8 項一致）。
    """
    import traci.constants as tc

    vars_ = [tc.VAR_POSITION, tc.VAR_SPEED, tc.VAR_ACCELERATION,
             tc.VAR_ANGLE, tc.VAR_LANE_ID, tc.VAR_LENGTH]

    # 號誌：進入車道 -> (號誌 id, 燈號字串中的索引)；路網不變，只需建一次
    lane_tls: dict[str, tuple[str, int]] = {}
    for tls in traci_mod.trafficlight.getIDList():
        for idx, links in enumerate(traci_mod.trafficlight.getControlledLinks(tls)):
            for link in links:
                if link and link[0] not in lane_tls:
                    lane_tls[link[0]] = (tls, idx)

    while traci_mod.simulation.getMinExpectedNumber() > 0:
        traci_mod.simulationStep()
        # 時間要在步進「之後」讀：此時取回的車輛狀態就是這個時間點的狀態。
        # （舊版在步進前讀時間，每筆紀錄都晚標了一步，會與 SSM 事件時間錯開 0.1 s）
        sim_t = round(traci_mod.simulation.getTime(), 2)
        if limit is not None and sim_t > limit:
            break
        for vid in traci_mod.simulation.getDepartedIDList():
            traci_mod.vehicle.subscribe(vid, vars_)

        tls_state = {tls: traci_mod.trafficlight.getRedYellowGreenState(tls)
                     for tls in {v[0] for v in lane_tls.values()}}

        batch = []
        for vid, r in traci_mod.vehicle.getAllSubscriptionResults().items():
            if not r:
                continue
            lane = r[tc.VAR_LANE_ID]
            sig = "G"
            if lane in lane_tls:
                tls, idx = lane_tls[lane]
                sig = tls_state[tls][idx]
            x, y = r[tc.VAR_POSITION]
            batch.append(bsm_record(
                vehicle_id=vid, inter=inter, sim_time=sim_t, x=x, y=y,
                speed=r[tc.VAR_SPEED], accel=r[tc.VAR_ACCELERATION],
                heading=r[tc.VAR_ANGLE], lane_id=lane, signal_phase=sig,
                length=r.get(tc.VAR_LENGTH, 5.0)))
        yield sim_t, batch


def bsm_record(*, vehicle_id: str, inter: str, sim_time: float, x: float,
               y: float, speed: float, accel: float, heading: float,
               lane_id: str, signal_phase: str, length: float = 5.0) -> dict:
    """SPEC §5.1 的 BSM 欄位。ue/sumo_ue_sender.py 亦呼叫本函式。

    msg_id 與 ts_ue 由送出端在送出當下補上，離線收集不需要。
    """
    return {
        "intersection": inter, "vehicle_id": vehicle_id,
        "position": {"x": round(float(x), 3), "y": round(float(y), 3)},
        "speed": round(float(speed), 3), "accel": round(float(accel), 3),
        "heading": round(float(heading), 2), "lane_id": lane_id,
        "signal_phase": signal_phase, "sim_time": round(float(sim_time), 2),
        "length": round(float(length), 2),
    }


def _rel(p) -> str:
    p = pathlib.Path(p).resolve()
    try:
        return p.relative_to(config.ROOT.resolve()).as_posix()
    except ValueError:
        return p.name


def _intersection_of(scenario: str, cfg: dict) -> str:
    for key, nc in cfg["nodes"].items():
        if nc["scenario"] == scenario:
            return nc["node_id"]
    return scenario.upper()      # proxy_public 沒有對應節點


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def read_states(path) -> dict[float, list[F.VehicleState]]:
    """讀 states.jsonl(.gz)，回傳 {sim_time: [VehicleState, ...]}。"""
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} 不存在。請先執行 `build_dataset.py collect --scenario ...`")
    opener = gzip.open if p.suffix == ".gz" else open
    by_t: dict[float, list[F.VehicleState]] = defaultdict(list)
    with opener(p, "rt", encoding="utf-8") as fh:
        for ln, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                by_t[round(float(json.loads(raw)["sim_time"]), 2)].append(
                    F.VehicleState.from_bsm(json.loads(raw)))
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                raise ValueError(f"{p}:{ln} 不是合法的 BSM：{e}") from e
    return dict(by_t)


def parse_ssm(path, keep_below: float | None = None) -> dict[str, list[tuple[float, float]]]:
    """解析 SUMO SSM device 輸出，回傳 {vehicle_id: [(事件時間, 數值), ...]}（依時間排序）。

    取三種資料，全部登錄給 ego 與 foe 兩台車（危險是雙方共同面對的，
    只標給 ego 會讓另一台車的樣本被錯標成安全）：

      * TTCSpan 逐步 TTC（sumocfg 開了 device.ssm.trajectories 時才有）——
        標籤要的是「未來 3 秒內的最小值」，逐步值比單一 minTTC 精確：
        某衝突的最小值若發生在 t 之前、但 t 之後仍有偏低的 TTC，只看 minTTC 會漏標
      * minTTC 單點（沒開 trajectories 時的後備）
      * PET 單點（PET 本質上每個衝突只有一個值）

    檔案可能很大（路口 A 跑 3600 秒、開 trajectories 約 900 MB 未壓縮），
    所以用 iterparse 逐筆讀、讀完即丟，記憶體不隨檔案大小成長。支援 .gz。

    keep_below：只保留數值低於此門檻的逐步 TTC（預設取 config 的注意門檻 3.0 s），
    高於門檻的值不會影響標籤，留著只是浪費記憶體。
    """
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"找不到 SSM 輸出（{p.name} 或 {p.name}.gz）。請確認場景的 sumocfg 已依 "
            "SPEC §4.2 開啟 SSM device，並先執行 build_dataset collect。")
    if keep_below is None:
        keep_below = float(config.load()["label"]["thresholds"]["caution_lt"])

    events: dict[str, list[tuple[float, float]]] = defaultdict(list)
    opener = gzip.open if p.suffix == ".gz" else open
    with opener(p, "rb") as fh:
        for _, conflict in ET.iterparse(fh, events=("end",)):
            if conflict.tag != "conflict":
                continue
            vehicles = [v for v in (conflict.get("ego"), conflict.get("foe")) if v]
            found: list[tuple[float, float]] = []

            ts, ttc = conflict.find("timeSpan"), conflict.find("TTCSpan")
            if ts is not None and ttc is not None:
                for t_str, v_str in zip(ts.get("values", "").split(),
                                        ttc.get("values", "").split()):
                    if v_str == "NA":
                        continue
                    try:
                        fv, ft = float(v_str), float(t_str)
                    except ValueError:
                        continue
                    if 0 <= fv < keep_below:
                        found.append((ft, fv))

            for tag in ("minTTC", "PET", "minPET"):
                for node in conflict.iter(tag):
                    val, tm = node.get("value"), node.get("time")
                    if val in (None, "NA") or tm in (None, "NA"):
                        continue
                    try:
                        fv, ft = float(val), float(tm)
                    except ValueError:
                        continue
                    if fv >= 0:                          # SSM 以負值表示未發生
                        found.append((ft, fv))

            for v in vehicles:
                events[v].extend(found)
            conflict.clear()                             # 讀完即丟，記憶體不累積
    for v in events:
        events[v].sort()
    return dict(events)


def label_for(events: list[tuple[float, float]], t: float,
              horizon: float, cfg: dict) -> tuple[int, float]:
    """SPEC §5.4：以未來 horizon 秒內的 min(TTC, PET) 決定標籤。

    區間取 (t, t+horizon]——嚴格大於 t，因為 t 當下的值屬於「現在」，
    是規則基準線看得到的東西。把它算進標籤會讓預測任務退化成偵測任務。

    events 須依時間排序（parse_ssm 保證）。用二分搜尋直接跳到 t 之後，
    開了逐步 TTC 之後每台車的事件數可達數百筆，從頭掃描會太慢。
    """
    th = cfg["label"]["thresholds"]
    best = float("inf")
    i = bisect.bisect_right(events, (t, float("inf")))
    while i < len(events):
        ft, fv = events[i]
        if ft > t + horizon:
            break
        if fv < best:
            best = fv
        i += 1
    if best < th["danger_lt"]:
        return 2, best
    if best < th["caution_lt"]:
        return 1, best
    return 0, best


# ---------------------------------------------------------------------------
# build：states + ssm -> npz
# ---------------------------------------------------------------------------
def _open_states(path):
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} 不存在。請先執行 `build_dataset collect --scenario ...`")
    return (gzip.open if p.suffix == ".gz" else open)(p, "rt", encoding="utf-8")


def _count_states(path) -> int:
    """先數一遍總筆數（不解析 JSON，只數行），用來決定取樣間隔。"""
    with _open_states(path) as fh:
        return sum(1 for line in fh if line.strip())


def iter_frames(path):
    """逐時間步讀 states，每次產出 (sim_time, [VehicleState, ...])。

    collect 是一步一步依時間順序寫入的，所以同一時間步的紀錄必然相鄰。
    逐步讀、用完即丟，記憶體只跟「同一時刻在路上的車數」有關，與檔案大小無關。
    """
    cur_t, frame = None, []
    with _open_states(path) as fh:
        for ln, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
                t = round(float(rec["sim_time"]), 2)
                st = F.VehicleState.from_bsm(rec)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                raise ValueError(f"{path}:{ln} 不是合法的 BSM：{e}") from e
            if cur_t is not None and t != cur_t:
                if t < cur_t:
                    raise ValueError(
                        f"{path}:{ln} 時間倒退（{cur_t} → {t}）。states 檔必須依時間排序，"
                        "請勿手動合併或編輯，重新執行 collect 即可。")
                yield cur_t, frame
                frame = []
            cur_t = t
            frame.append(st)
    if frame:
        yield cur_t, frame


def build(scenario: str, states_path=None, ssm_path=None,
          out=None, cfg: dict | None = None) -> dict:
    """組出 (N, 20, 8) 特徵視窗與標籤。

    特徵計算完全走 mec_app.features.compute——與 MEC App 即時推論同一份
    程式碼（SPEC §9「兩端共用，勿分岔」）。

    === 串流處理 ===
    舊版先把整份 states 載入記憶體再處理，路口 A（523 萬筆）峰值達 6.8 GB，
    學生筆電跑不動。現在逐時間步處理：每台車只保留最近 20 步的視窗，
    到取樣點就輸出一筆，車輛離開路網即釋放。

    取樣間隔：預設每 stride_steps 步取一筆（0.5 s）。若總量會超過 max_samples，
    就把間隔放大到剛好不超過（路口 A 約每 1.3 s 一筆），而不是全部產生後再抽樣——
    後者得先把所有樣本放進記憶體，正是要避免的事。
    """
    import math
    from collections import deque

    c = cfg or config.load()
    scen = c["sumo"]["scenarios"][scenario]
    d = c["dataset"]
    steps = int(c["window"]["steps"])
    base_stride = int(d["stride_steps"])
    horizon = float(c["label"]["horizon_seconds"])
    step_len = float(c["sumo"]["step_length"])
    min_steps = int(d["min_vehicle_steps"])
    cap = int(d["max_samples"])

    states_path = states_path or config.path(scen["dir"], "states.jsonl.gz")
    if ssm_path is None:
        gz = config.path(scen["dir"], "ssm.xml.gz")
        ssm_path = gz if gz.exists() else config.path(scen["dir"], "ssm.xml")
    out = pathlib.Path(out or config.path(d["out"].format(scenario=scenario)))

    events = parse_ssm(ssm_path)
    geom = _geometry_for(scenario, c)
    signalized = bool(scen["signalized"])

    total = _count_states(states_path)
    stride = max(base_stride, math.ceil(total / cap)) if total else base_stride

    X, y, ts, vids, raws = [], [], [], [], []
    # vid -> [視窗 deque, 已走步數, 尚未確認的樣本（車輛未滿 min_steps 前先暫存）]
    active: dict[str, list] = {}
    skipped_short = 0

    def emit(samples):
        for win, lbl, t_i, vid, raw in samples:
            X.append(win); y.append(lbl); ts.append(t_i); vids.append(vid); raws.append(raw)

    def finalize(vid):
        nonlocal skipped_short
        _, n, pending = active.pop(vid)
        if n < min_steps:
            skipped_short += 1           # 生命週期短於一個視窗，整台車捨棄
        # n >= min_steps 的車，樣本早已輸出，pending 必為空

    for t, frame in iter_frames(states_path):
        present = set()
        for ego in frame:
            vid = ego.vehicle_id
            present.add(vid)
            norm, raw, _ = F.compute(ego, frame, geom, signalized, c)
            slot = active.get(vid)
            if slot is None:
                slot = active[vid] = [deque(maxlen=steps), 0, []]
            win_q, n, pending = slot
            win_q.append(norm)
            if n % stride == 0:
                # edge padding：不足一個視窗時以最早一筆向前填充，
                # 與 mec_app.window.VehicleWindow 的行為一致。
                win = list(win_q)
                if len(win) < steps:
                    win = [win[0]] * (steps - len(win)) + win
                lbl, _ = label_for(events.get(vid, []), t, horizon, c)
                sample = (np.stack(win).astype(np.float32), lbl, t, vid, raw)
                if n + 1 >= min_steps:
                    emit([sample])
                else:
                    pending.append(sample)
            slot[1] = n + 1
            if slot[1] == min_steps and pending:
                emit(pending)            # 剛滿足最短生命週期，補發先前暫存的樣本
                pending.clear()
        for vid in [v for v in active if v not in present]:
            finalize(vid)
    for vid in list(active):
        finalize(vid)

    if not X:
        raise RuntimeError(
            f"{scenario} 沒有產生任何樣本。請確認 states 與 ssm 輸出非空，"
            "且車輛生命週期長於 min_vehicle_steps。")

    X = np.stack(X).astype(np.float32)
    y = np.asarray(y, dtype=np.int64)
    ts = np.asarray(ts, dtype=np.float32)
    vids = np.asarray(vids, dtype="<U16")
    raws = np.stack(raws).astype(np.float32)

    from training.dataset import feature_ranges, save
    save(out, X, y, ts, vids)

    counts = np.bincount(y, minlength=3)
    summary = {
        # 記相對於專案根目錄的路徑：摘要會進版控，不應帶出本機目錄結構
        "scenario": scenario, "out": _rel(out), "n_samples": int(len(y)),
        "class_counts": counts.tolist(),
        "class_pct": [round(100.0 * v / len(y), 3) for v in counts],
        "sim_time_range": [float(ts.min()), float(ts.max())],
        "n_vehicles": int(len(set(vids.tolist()))),
        "skipped_short_lived": skipped_short,
        "total_states": total,
        "stride_steps": stride, "subsampled": bool(stride > base_stride),
        "seconds_per_sample": round(stride * step_len, 3),
        "feature_ranges": feature_ranges(raws, c),
    }
    (out.with_suffix(".summary.json")).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _geometry_for(scenario: str, cfg: dict) -> F.Geometry:
    p = config.path(cfg["sumo"]["scenarios"][scenario]["dir"], "stoplines.json")
    if not p.exists():
        return F.Geometry(None, source=f"{p}（不存在）")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return F.Geometry({k: (float(v[0]), float(v[1])) for k, v in raw.items()},
                      source=str(p))


def print_summary(s: dict) -> None:
    names = ["安全", "注意", "危險"]
    print(f"\n場景 {s['scenario']}：{s['n_samples']:,} 筆，"
          f"{s['n_vehicles']} 台車，"
          f"模擬時間 {s['sim_time_range'][0]:.1f}–{s['sim_time_range'][1]:.1f} s")
    print("類別分布：" + "  ".join(
        f"{names[i]} {s['class_counts'][i]:,} ({s['class_pct'][i]}%)"
        for i in range(3)))
    if s["class_counts"][2] == 0:
        print("  !! 危險樣本為 0。依 SPEC §4.2 調大 sigma / tau / speedFactor "
              "再重跑，SUMO 預設的 Krauss 模型近乎無碰撞。")
    bad = {k: v["clipped_pct"] for k, v in s["feature_ranges"].items()
           if v["clipped_pct"] >= 1.0}
    if bad:
        print("  !! 下列特徵的截斷比例 >= 1%，須回頭調整 config.yaml 的 "
              "features.norm 值域（見該區的 [待驗收] 註記）：")
        for k, v in sorted(bad.items(), key=lambda kv: -kv[1]):
            r = s["feature_ranges"][k]
            print(f"     {k}: {v}% 被截斷，實際範圍 "
                  f"[{r['actual_min']:.2f}, {r['actual_max']:.2f}]，"
                  f"設定 [{r['config_min']}, {r['config_max']}]")
    else:
        print("  特徵值域驗收通過：所有特徵截斷比例 < 1%")
    print(f"摘要已寫入 {s['out'].replace('.npz', '.summary.json')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("collect", help="TraCI 驅動 SUMO 錄製狀態（需要 SUMO）")
    pc.add_argument("--scenario", required=True, choices=["a", "b", "proxy", "proxy_signal"])
    pc.add_argument("--out"); pc.add_argument("--max-seconds", type=float)
    pc.add_argument("--seed", type=int, help="SUMO 亂數種子（錄額外測試資料用）")
    pc.add_argument("--ssm-out", help="SSM 輸出位置（與 --seed 併用，避免蓋掉訓練用的 ssm.xml.gz）")

    pb = sub.add_parser("build", help="states + ssm -> npz（純 Python）")
    pb.add_argument("--scenario", required=True, choices=["a", "b", "proxy", "proxy_signal"])
    pb.add_argument("--states"); pb.add_argument("--ssm"); pb.add_argument("--out")

    a = ap.parse_args(argv)
    if a.cmd == "collect":
        collect(a.scenario, pathlib.Path(a.out) if a.out else None, a.max_seconds,
                seed=a.seed, ssm_out=pathlib.Path(a.ssm_out) if a.ssm_out else None)
    else:
        print_summary(build(a.scenario, a.states, a.ssm, a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
