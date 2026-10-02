"""事件層級的警示評估與兩段式門檻校準（SPEC §6.4）。

=== 為什麼以「事件」為單位 ===
駕駛在乎的是「這次危險有沒有被提醒到」，不是「每 0.1 秒有沒有被標對」。
一件危險事件 = 一對車 min(TTC, PET) 首次 < 1.5 s（與 training/lead_time.py 相同定義）；
事件前 3 秒到事件後 0.5 秒內，雙方任一台**有被提醒**就算抓到。
不要求警示「指向對方那台車」：模型學的標籤就是「這台車未來 3 秒會不會遇到危險（不論對象）」，
駕駛收到「前方有危險」就會減速注意。「指向對方」的嚴格比法留給 lead_time.py 的「比規則早幾秒」。

=== 兩段式警示 ===
模型輸出「未來 3 秒內發生危險的機率」p。同一個分數、兩條線：
  注意（溫和提示，例如儀表板圖示）：p >= τ_注意。負責「幾乎全抓到」——事件召回率 >= 95%
  危險（強烈警報，例如聲音）      ：p >= τ_危險。負責「喊了要準」——強警報至少一半是真的
門檻只用**本路口的驗證資料**校準（部署時本來就只有自己的資料）；測試資料只用來報告。

=== 誤報怎麼算 ===
同一台車連續的警示（間隔 < 1 秒）算一次「警示」——駕駛感受到的是一次提醒，不是 10 筆紀錄。
一次警示開始後 3.5 秒內，這台車真的遇到對應等級的狀況就算有用，否則是誤報：
  注意（溫和提示）：之後真的出現 TTC < 3 秒（本來就是「注意」等級的狀況）
  危險（強烈警報）：之後真的出現 TTC < 1.5 秒
誤報以「每次通過路口平均被誤報幾次」表示：每台車只在路口附近一兩分鐘，換算成每小時會失真。

=== 重播快取 ===
特徵計算與模型無關，所以驗證 / 測試時段只重播一次，把每台車每 0.1 秒的特徵視窗存在
data/replay_{site}_{split}.npz。之後任何一版權重都能在幾十秒內評估完。
車流或 SSM 檔更新時會自動重建。

用法：
    python -m training.events build --site a --split val
    python -m training.events curve --node a --weights round_0 --site a --split val
    python -m training.events calibrate --node a --weights round_0 --also-current
"""
from __future__ import annotations

import argparse
from collections import deque

import numpy as np
import torch

from mec_app import config, features as F, model as M, rule_baseline as R
from training import dataset as D
from training.build_dataset import _geometry_for, iter_frames
from training.lead_time import AFTER_TOL_S, danger_events, parse_conflict_pairs

EPISODE_GAP_S = 1.0          # 同一台車間隔小於 1 秒的警示算同一次
TAU_GRID = np.round(np.arange(0.01, 0.991, 0.01), 2)
TSCALE = 10                  # 時間以 0.1 秒為單位編碼成整數


# ---------------------------------------------------------------------------
# 重播與快取
# ---------------------------------------------------------------------------
EXTRA_WARMUP_S = 300.0       # 額外測試車流（不同亂數種子）略過前 5 分鐘，等車流穩定


def _window(site: str, split: str, cfg: dict) -> tuple[float, float]:
    if split.startswith("seed"):                 # 額外測試資料：整段（略過暖機）都是測試
        return EXTRA_WARMUP_S, float(cfg["sumo"]["min_sim_seconds"])
    scen_key = config.node(site)["scenario"]
    ds = D.load(config.path(cfg["dataset"]["out"].format(scenario=scen_key)))
    part = D.temporal_split(ds, cfg)[{"val": 1, "test": 2}[split]]
    return float(part.t.min()), float(part.t.max())


def extra_dir(site: str, split: str, cfg: dict):
    """額外測試車流的位置：sumo/intersection_x/extra/seedN/。"""
    scen = cfg["sumo"]["scenarios"][config.node(site)["scenario"]]
    return config.path(scen["dir"], "extra", split)


def _sources(site: str, cfg: dict, split: str = "val"):
    scen = cfg["sumo"]["scenarios"][config.node(site)["scenario"]]
    if split.startswith("seed"):
        d = extra_dir(site, split, cfg)
        return d / "states.jsonl.gz", d / "ssm.xml.gz"
    states = config.path(scen["dir"], "states.jsonl.gz")
    ssm = config.path(scen["dir"], "ssm.xml.gz")
    if not ssm.exists():
        ssm = config.path(scen["dir"], "ssm.xml")
    return states, ssm


def build_replay(site: str, split: str, cfg: dict) -> dict:
    """重播某路口的驗證 / 測試時段，逐步做 MEC App 線上會做的事（特徵 → 2 秒視窗 → 規則）。"""
    scen_key = config.node(site)["scenario"]
    signalized = bool(cfg["sumo"]["scenarios"][scen_key]["signalized"])
    steps = int(cfg["window"]["steps"])
    step_len = float(cfg["sumo"]["step_length"])
    t0, t1 = _window(site, split, cfg)
    states, ssm = _sources(site, cfg, split)
    geom = _geometry_for(scen_key, cfg)

    ids: dict[str, int] = {}
    def iid(v):
        if v is None:
            return -1
        if v not in ids:
            ids[v] = len(ids)
        return ids[v]

    windows: dict[str, deque] = {}
    last_seen: dict[str, float] = {}
    X, T, V, P, RW, RT = [], [], [], [], [], []
    warm = t0 - steps * step_len
    for t, frame in iter_frames(states):
        if t < warm:
            continue
        if t > t1:
            break
        xs = []
        for ego in frame:
            norm, _, conflict = F.compute(ego, frame, geom, signalized, cfg)
            q = windows.get(ego.vehicle_id)
            if q is None:
                q = windows[ego.vehicle_id] = deque(maxlen=steps)
            q.append(norm)
            last_seen[ego.vehicle_id] = t
            if t < t0:
                continue
            lvl, _, target = R.evaluate_platform(ego, frame, cfg)
            w = list(q)
            if len(w) < steps:                                   # edge padding，與線上一致
                w = [w[0]] * (steps - len(w)) + w
            xs.append(np.stack(w))
            T.append(t); V.append(iid(ego.vehicle_id)); P.append(iid(conflict.partner_id))
            RW.append(lvl == 2); RT.append(iid(target))
        if xs:
            X.append(np.stack(xs).astype(np.float16))
        if len(windows) > 500:
            for v in [v for v, s in last_seen.items() if t - s > 1.0]:
                windows.pop(v, None); last_seen.pop(v, None)

    pairs = parse_conflict_pairs(ssm, float(cfg["label"]["thresholds"]["danger_lt"]))
    evs = danger_events(pairs, t0, t1)
    caution_pairs = parse_conflict_pairs(ssm, float(cfg["label"]["thresholds"]["caution_lt"]))
    ev_a = np.array([iid(k[0]) for k, _ in evs], np.int32)
    ev_b = np.array([iid(k[-1]) for k, _ in evs], np.int32)
    ev_t = np.array([t for _, t in evs], np.float32)
    # 每台車的危險時間（判斷一次警示有沒有用）
    def per_vehicle(pp):
        vv, tt = [], []
        for k, times in pp.items():
            for t in times:
                if t0 - 5 <= t <= t1 + 5:
                    for v in k:
                        vv.append(iid(v)); tt.append(t)
        return np.asarray(vv, np.int32), np.asarray(tt, np.float32)
    vd_v, vd_t = per_vehicle(pairs)
    vc_v, vc_t = per_vehicle(caution_pairs)
    return {
        "X": np.concatenate(X) if X else np.zeros((0, steps, 8), np.float16),
        "t": np.asarray(T, np.float32), "vid": np.asarray(V, np.int32),
        "partner": np.asarray(P, np.int32), "rule_warn": np.asarray(RW, bool),
        "rule_target": np.asarray(RT, np.int32),
        "ev_a": ev_a, "ev_b": ev_b, "ev_t": ev_t,
        "vd_vid": vd_v, "vd_t": vd_t, "vc_vid": vc_v, "vc_t": vc_t,
        "window": np.array([t0, t1], np.float32), "n_ids": np.array(len(ids)),
        "step_len": np.array(step_len, np.float32),
    }


def replay_path(site: str, split: str):
    return config.path("data", f"replay_{site}_{split}.npz")


def load_replay(site: str, split: str, cfg: dict, rebuild: bool = False) -> dict:
    p = replay_path(site, split)
    states, ssm = _sources(site, cfg, split)
    stamp = np.array([states.stat().st_mtime, ssm.stat().st_mtime])
    if p.exists() and not rebuild:
        d = dict(np.load(p))
        if np.allclose(d.get("stamp", -1), stamp):
            return d
    print(f"重播 {site.upper()} 路口 {split} 時段（只需一次，之後讀快取）…", flush=True)
    d = build_replay(site, split, cfg)
    d["stamp"] = stamp
    np.savez(p, **d)
    return d


# ---------------------------------------------------------------------------
# 評估
# ---------------------------------------------------------------------------
@torch.no_grad()
def danger_scores(model, rep: dict, batch: int = 8192) -> np.ndarray:
    """每一筆（車 × 0.1 秒）的危險機率。"""
    model.eval()
    X = rep["X"]
    out = np.empty(len(X), np.float32)
    for i in range(0, len(X), batch):
        xb = torch.from_numpy(X[i:i + batch].astype(np.float32))
        out[i:i + batch] = torch.softmax(model(xb), -1)[:, 2].numpy()
    return out


def _enc(key: np.ndarray, t: np.ndarray) -> np.ndarray:
    return key.astype(np.int64) * 100_000 + np.rint(t * TSCALE).astype(np.int64)


def alert_metrics(rep: dict, warn: np.ndarray, target: np.ndarray,
                  lookback: float, level: str = "danger", pair: bool = False) -> dict:
    """某組警示的事件召回率、提早秒數、誤報。

    level：誤報的判準——"caution" 之後出現 TTC < 3 s 就算有用；"danger" 要 TTC < 1.5 s
    pair ：True 時警示必須指向事件的另一台車才算抓到（lead_time.py 的嚴格比法）
    """
    n_ids = int(rep["n_ids"]) + 1
    idx = np.nonzero(warn)[0]
    # (1) 事件召回：雙方任一台有被提醒（pair=True 時須指向對方）
    tgt = (target[idx] + 1) if pair else np.zeros(len(idx), np.int64)
    key = rep["vid"][idx].astype(np.int64) * n_ids + tgt
    code = np.sort(_enc(key, rep["t"][idx]))
    ev_t = rep["ev_t"]
    first = np.full(len(ev_t), np.nan)
    lo_t, hi_t = ev_t - lookback, ev_t + AFTER_TOL_S
    for a, b in ((rep["ev_a"], rep["ev_b"]), (rep["ev_b"], rep["ev_a"])) if len(code) else ():
        k = a.astype(np.int64) * n_ids + ((b + 1) if pair else 0)
        lo, hi = _enc(k, lo_t), _enc(k, hi_t)
        pos = np.searchsorted(code, lo)
        ok = (pos < len(code)) & (code[np.minimum(pos, len(code) - 1)] <= hi)
        tt = (code[np.minimum(pos, len(code) - 1)] % 100_000) / TSCALE
        first = np.where(ok & ~(tt >= first), tt, first)
    caught = ~np.isnan(first)
    lead = (ev_t - first)[caught]
    # (2) 誤報：同車連續警示合成一次，開始後 lookback + 0.5 秒內沒遇到危險就是誤報
    order = np.lexsort((rep["t"][idx], rep["vid"][idx]))
    v, t = rep["vid"][idx][order], rep["t"][idx][order]
    start = np.ones(len(v), bool)
    if len(v):
        start[1:] = (v[1:] != v[:-1]) | (t[1:] - t[:-1] > EPISODE_GAP_S)
    ev_v, ev_s = v[start], t[start]
    vk, tk = ("vc_vid", "vc_t") if level == "caution" else ("vd_vid", "vd_t")
    dcode = np.sort(_enc(rep[vk], rep[tk]))
    lo, hi = _enc(ev_v, ev_s), _enc(ev_v, ev_s + lookback + AFTER_TOL_S)
    pos = np.searchsorted(dcode, lo)
    useful = (pos < len(dcode)) & (dcode[np.minimum(pos, len(dcode) - 1)] <= hi) if len(dcode) else np.zeros(len(ev_v), bool)
    veh_hours = len(rep["t"]) * float(rep["step_len"]) / 3600.0
    n_veh = int(len(np.unique(rep["vid"])))
    n_ep = int(start.sum())
    return {
        "events": int(len(ev_t)), "caught": int(caught.sum()),
        "recall": float(caught.mean()) if len(ev_t) else float("nan"),
        "lead_median_s": float(np.median(lead)) if len(lead) else float("nan"),
        "alerts": n_ep, "false_alerts": int(n_ep - useful.sum()),
        "alert_precision": float(useful.mean()) if n_ep else float("nan"),
        "false_per_vehicle_hour": float((n_ep - useful.sum()) / veh_hours) if veh_hours else float("nan"),
        "false_per_passage": float((n_ep - useful.sum()) / n_veh) if n_veh else float("nan"),
        "vehicles": n_veh,
        "vehicle_hours": veh_hours,
    }


def model_metrics(rep, s, tau, lookback, level="danger"):
    return alert_metrics(rep, s >= tau, rep["partner"], lookback, level)


def rule_metrics(rep, lookback):
    return alert_metrics(rep, rep["rule_warn"], rep["rule_target"], lookback, "danger")


def calibrate_two_tier(rep: dict, s: np.ndarray, cfg: dict) -> dict:
    """注意：事件召回 >= 目標的最高門檻；危險：強警報精準度 >= 目標的最低門檻（不低於注意）。"""
    dc = cfg["decision"]
    lookback = float(cfg["label"]["horizon_seconds"])
    target_recall = float(dc["caution_event_recall"])
    target_prec = float(dc["danger_alert_precision"])
    soft = {float(t): model_metrics(rep, s, t, lookback, "caution") for t in TAU_GRID}
    strong = {float(t): model_metrics(rep, s, t, lookback, "danger") for t in TAU_GRID}
    ok = [t for t, m in soft.items() if m["recall"] >= target_recall]
    tau_c = max(ok) if ok else float(TAU_GRID[0])
    cand = [t for t, m in strong.items() if t >= tau_c and m["alert_precision"] >= target_prec]
    tau_d = min(cand) if cand else max(strong, key=lambda t: strong[t]["alert_precision"])
    return {"caution": tau_c, "danger": tau_d, "soft": soft, "strong": strong,
            "recall_reached": bool(ok), "precision_reached": bool(cand)}


def calibrate_model(model, node: str, cfg: dict) -> dict:
    """新一版權重上線前的本地校準：回傳 {"caution": τ, "danger": τ}（只用本路口驗證資料）。

    沒有 SUMO 車流可重播時（例如測試用的合成資料），退回以逐筆樣本校準並明確警告——
    正式流程一定有車流，這條路只為了讓不依賴 SUMO 的測試能跑。
    """
    try:
        rep = load_replay(node, "val", cfg)
    except FileNotFoundError:
        print("  [警告] 找不到車流紀錄，改以逐筆樣本校準警示門檻（僅供測試）")
        return _calibrate_samples(model, node, cfg)
    th = calibrate_two_tier(rep, danger_scores(model, rep), cfg)
    return {"caution": th["caution"], "danger": th["danger"]}


def _calibrate_samples(model, node: str, cfg: dict) -> dict:
    from training.decision import probs
    from training.train_local import load_node_data
    _, va, _ = load_node_data(node, cfg)
    p = probs(model, va)[:, 2]
    danger = va.y == 2
    rec = {float(t): (danger & (p >= t)).sum() / max(danger.sum(), 1) for t in TAU_GRID}
    prec = {float(t): (danger & (p >= t)).sum() / max((p >= t).sum(), 1) for t in TAU_GRID}
    ok = [t for t, r in rec.items() if r >= float(cfg["decision"]["caution_event_recall"])]
    tau_c = max(ok) if ok else float(TAU_GRID[0])
    cand = [t for t, q in prec.items() if t >= tau_c and q >= float(cfg["decision"]["danger_alert_precision"])]
    return {"caution": tau_c, "danger": min(cand) if cand else max(prec, key=prec.get)}


def load_weights(node: str, weights: str):
    m = M.build()
    p = config.path(f"models/{node}/{weights}.pt") if "/" not in weights else config.path(weights)
    m.load_state_dict(torch.load(p, map_location="cpu", weights_only=True))
    return m


def fmt(m: dict) -> str:
    return (f"事件召回 {m['recall']:6.1%}（{m['caught']}/{m['events']}）"
            f"  每次通過誤報 {m['false_per_passage']:5.2f} 次"
            f"  警示精準度 {m['alert_precision']:6.1%}  提早中位數 {m['lead_median_s']:.1f} s")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--site", required=True); b.add_argument("--split", default="val")
    b.add_argument("--rebuild", action="store_true")
    c = sub.add_parser("curve")
    c.add_argument("--node", required=True); c.add_argument("--weights", default="current")
    c.add_argument("--site", default=None); c.add_argument("--split", default="val")
    k = sub.add_parser("calibrate")
    k.add_argument("--node", required=True); k.add_argument("--weights", default="current")
    k.add_argument("--also-current", action="store_true")
    a = ap.parse_args(argv)
    cfg = config.load()
    lookback = float(cfg["label"]["horizon_seconds"])
    if a.cmd == "build":
        rep = load_replay(a.site, a.split, cfg, rebuild=a.rebuild)
        print(f"{a.site.upper()} {a.split}：{len(rep['t']):,} 筆、危險事件 {len(rep['ev_t'])} 件"
              f"、{float(rep['window'][0]):.0f}–{float(rep['window'][1]):.0f} s")
        print(f"  規則基準線：{fmt(rule_metrics(rep, lookback))}")
    elif a.cmd == "curve":
        rep = load_replay(a.site or a.node, a.split, cfg)
        s = danger_scores(load_weights(a.node, a.weights), rep)
        print(f"模型 {a.node.upper()}/{a.weights} 在 {(a.site or a.node).upper()} 路口 {a.split} 時段")
        print(f"  規則基準線：{fmt(rule_metrics(rep, lookback))}")
        print("  （注意等級：之後出現 TTC < 3 s 算有用｜危險等級：TTC < 1.5 s 才算）")
        for t in (0.02, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
            print(f"  τ={t:<4} 注意 {fmt(model_metrics(rep, s, t, lookback, 'caution'))}")
            print(f"  {'':6} 危險 {fmt(model_metrics(rep, s, t, lookback, 'danger'))}")
    else:
        rep = load_replay(a.node, "val", cfg)
        s = danger_scores(load_weights(a.node, a.weights), rep)
        th = calibrate_two_tier(rep, s, cfg)
        stems = [a.weights] + (["current"] if a.also_current else [])
        M.save_threshold(a.node, stems, {"caution": th["caution"], "danger": th["danger"]})
        print(f"節點 {a.node.upper()} {a.weights}：注意 τ = {th['caution']}、危險 τ = {th['danger']}")
        print(f"  注意：{fmt(th['soft'][th['caution']])}" + ("" if th["recall_reached"] else "  ← 未達召回目標"))
        print(f"  危險：{fmt(th['strong'][th['danger']])}" + ("" if th["precision_reached"] else "  ← 未達精準度目標"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
