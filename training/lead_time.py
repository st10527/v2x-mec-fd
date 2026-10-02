"""提前預警時間：模型比 TTC 規則早幾秒喊危險（SPEC §8.1、§8.4 的 events.jsonl）。

這是展示時最直觀的一句話——「同一個危險狀況，我們的系統比平台範例的規則早 X 秒警告」。
對應 SPEC §2 Q2：規則只能偵測（危險已經發生），模型能預測（危險發生前）。

做法：把測試時段的車流「重播」一次，逐步做 MEC App 線上會做的事——
  每 0.1 秒算特徵 → 更新每車 2 秒視窗 → 模型判斷 → 規則判斷
兩者都記下「第一次喊危險」的時間，再對到 SUMO 記錄的真實危險事件。
特徵、視窗、規則、模型全部直接呼叫 mec_app 的程式碼，量到的就是線上系統的行為。

=== 報告內容（三個數字一起看，缺一不可）===
  1. 提早秒數：模型與規則都有喊的事件，模型平均早幾秒
  2. 漏報：規則漏掉、但模型抓到的事件有幾件（反之亦然）
  3. 準確率：模型喊危險的時刻裡，接下來 3 秒真的出事的比例
     ——只報前兩項會誤導：一個「永遠喊危險」的模型提早秒數最漂亮，但毫無用處

=== 危險事件的定義 ===
  一件危險事件 = 一個危險狀況 = SUMO SSM 記到的**一對車**（ego 與 foe），
  其 min(TTC, PET) < 1.5 s（SPEC §5.4 的危險門檻）。
  同一對車 5 秒內的連續危險算同一件事，事件時間取第一次低於門檻的那一刻。
  只看測試時段（依時間切分的最後 15%，與模型評估用的是同一段）。
  **雙方任一台收到「指向對方」的警告，就算這件事有被預警**——這是真實情境的算法：
    * 任一台：追撞時平台規則只會提醒後車，被撞的前車收不到；若把兩台車各算一件，
      規則天生就漏一半，同一個危險也被算了兩次，對規則不公平
    * 指向對方：V2X 警示會帶威脅來源（MEC App 回應裡模型的 conflict_with、規則的
      rule_target）。車隊裡每台車都可能正在被提醒「注意前車」，那是別的危險；
      不看對象的話，雙方都會拿到「還沒發生就已在喊」的假提早
  對每件事件，在「事件前 3 秒 ~ 事件後 0.5 秒」內找第一次警告：
    前 3 秒 = 模型的預測範圍（SPEC §5.4），更早的警告不算是在預警這件事，
             所以提早秒數**最多就是 3 秒**（模型在視窗一開始就已在警告）
    後 0.5 秒 = 讓規則有機會在危險當下觸發（規則本來就只能在當下觸發）

用法：
    python -m training.lead_time --node a                # 用 models/a/current.pt
    python -m training.lead_time --node a --round 0      # 單獨訓練的基準模型
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import pathlib
import time
import xml.etree.ElementTree as ET
from collections import deque

import numpy as np
import torch

from mec_app import config, features as F, jsonlog, model as M, rule_baseline as R
from training import dataset as D
from training.build_dataset import _geometry_for, iter_frames, parse_ssm

EVENT_GAP_S = 5.0        # 同一台車 5 秒內的連續危險算同一件事
AFTER_TOL_S = 0.5        # 事件後 0.5 秒內的警告仍算數（讓規則有機會當下觸發）


def parse_conflict_pairs(path, danger_lt: float) -> dict[tuple[str, ...], list[float]]:
    """讀 SSM 輸出，回傳 {(車A, 車B): [低於危險門檻的時間, ...]}（依時間排序）。

    取值方式與 build_dataset.parse_ssm 相同（逐步 TTC + minTTC + PET），
    差別只在這裡以「一對車」為單位，而不是分別登錄給兩台車。
    """
    p = pathlib.Path(path)
    pairs: dict[tuple[str, ...], list[float]] = {}
    opener = gzip.open if p.suffix == ".gz" else open
    with opener(p, "rb") as fh:
        for _, c in ET.iterparse(fh, events=("end",)):
            if c.tag != "conflict":
                continue
            key = tuple(sorted(v for v in (c.get("ego"), c.get("foe")) if v))
            found = []
            ts, ttc = c.find("timeSpan"), c.find("TTCSpan")
            if ts is not None and ttc is not None:
                for t_str, v_str in zip(ts.get("values", "").split(),
                                        ttc.get("values", "").split()):
                    try:
                        if 0 <= float(v_str) < danger_lt:
                            found.append(float(t_str))
                    except ValueError:                   # NA
                        continue
            for tag in ("minTTC", "PET", "minPET"):
                for n in c.iter(tag):
                    try:
                        if 0 <= float(n.get("value")) < danger_lt:
                            found.append(float(n.get("time")))
                    except (TypeError, ValueError):
                        continue
            if key and found:
                pairs.setdefault(key, []).extend(found)
            c.clear()
    for k in pairs:
        pairs[k].sort()
    return pairs


def danger_events(pairs: dict[tuple[str, ...], list[float]], t0: float,
                  t1: float) -> list[tuple[tuple[str, ...], float]]:
    """把每對車的危險時間整理成事件清單：[((車A, 車B), 事件時間), ...]。"""
    out = []
    for key, times in pairs.items():
        last = -math.inf
        for t in times:
            if t - last > EVENT_GAP_S and t0 <= t <= t1:
                out.append((key, t))
            last = t
    out.sort(key=lambda e: e[1])
    return out


def first_in_window(times: list[float], lo: float, hi: float) -> float | None:
    """times 已排序；回傳落在 [lo, hi] 內的第一個時間。"""
    import bisect
    i = bisect.bisect_left(times, lo)
    return times[i] if i < len(times) and times[i] <= hi else None


Warnings = dict[tuple[str, str], list[float]]     # (收到警告的車, 警告針對的車) -> 時間


def _first_any(warn: Warnings, vehicles, lo: float, hi: float) -> float | None:
    """事件雙方互相指向的警告中，落在 [lo, hi] 內的第一次。"""
    ts = [t for a in vehicles for b in vehicles if a != b
          if (t := first_in_window(warn.get((a, b), []), lo, hi)) is not None]
    return min(ts) if ts else None


def match(events, model_warn: Warnings, rule_warn: Warnings, lookback: float) -> list[dict]:
    """對每件危險事件，找出模型與規則第一次警告的時間（雙方任一台收到指向對方的警告即算）。"""
    rows = []
    for k, (vehicles, t_ev) in enumerate(events):
        lo, hi = t_ev - lookback, t_ev + AFTER_TOL_S
        tm = _first_any(model_warn, vehicles, lo, hi)
        tr = _first_any(rule_warn, vehicles, lo, hi)
        rows.append({
            "event_id": k, "vehicles": list(vehicles), "label_t": round(t_ev, 2),
            "model_at_cap": tm is not None and tm - lo < 0.05,
            "first_warn_model_t": None if tm is None else round(tm, 2),
            "first_warn_rule_t": None if tr is None else round(tr, 2),
            "lead_time": None if (tm is None or tr is None) else round(tr - tm, 2),
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    both = [r["lead_time"] for r in rows if r["lead_time"] is not None]
    m_only = sum(1 for r in rows if r["first_warn_model_t"] is not None and r["first_warn_rule_t"] is None)
    r_only = sum(1 for r in rows if r["first_warn_rule_t"] is not None and r["first_warn_model_t"] is None)
    none = sum(1 for r in rows if r["first_warn_model_t"] is None and r["first_warn_rule_t"] is None)
    return {
        "events": len(rows),
        "model_warned_at_3s_cap": sum(1 for r in rows if r.get("model_at_cap")),
        "both_warned": len(both),
        "lead_time_mean_s": round(float(np.mean(both)), 2) if both else None,
        "lead_time_median_s": round(float(np.median(both)), 2) if both else None,
        "model_earlier_pct": round(100 * sum(1 for x in both if x > 0) / len(both), 1) if both else None,
        "rule_missed_model_caught": m_only,
        "model_missed_rule_caught": r_only,
        "both_missed": none,
    }


def _drop_previous(path: pathlib.Path, prefix: str) -> None:
    """從 events.jsonl 移除 event_id 以 prefix 開頭的舊紀錄，其他節點/權重的紀錄保留。"""
    if not path.exists():
        return
    keep = [ln for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not json.loads(ln).get("event_id", "").startswith(prefix)]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(ln + "\n" for ln in keep), encoding="utf-8")
    tmp.replace(path)


def run(node: str, rnd: int | None, cfg: dict) -> dict:
    scen_key = config.node(node)["scenario"]
    scen = cfg["sumo"]["scenarios"][scen_key]
    node_id = config.node(node)["node_id"]
    signalized = bool(scen["signalized"])
    steps = int(cfg["window"]["steps"])
    horizon = float(cfg["label"]["horizon_seconds"])
    danger_lt = float(cfg["label"]["thresholds"]["danger_lt"])

    # 測試時段：與模型評估相同的時間切分
    ds = D.load(config.path(cfg["dataset"]["out"].format(scenario=scen_key)))
    _, _, te = D.temporal_split(ds, cfg)
    t0, t1 = float(te.t.min()), float(te.t.max())

    # 模型
    wpath = (config.path(f"models/{node}/round_{rnd}.pt") if rnd is not None
             else config.path(cfg["fd"]["model_swap"]["current"].format(node=node)))
    if not wpath.exists():
        raise SystemExit(f"找不到權重 {wpath}，請先執行 training.train_local / training.distill")
    net = M.build()
    net.load_state_dict(torch.load(wpath, map_location="cpu", weights_only=True))
    net.eval()
    tau = M.load_threshold(node, wpath.stem)          # 這一版權重校準過的危險門檻

    states = config.path(scen["dir"], "states.jsonl.gz")
    ssm = config.path(scen["dir"], "ssm.xml.gz")
    if not ssm.exists():
        ssm = config.path(scen["dir"], "ssm.xml")
    geom = _geometry_for(scen_key, cfg)

    print(f"節點 {node_id}｜權重 {wpath.name}｜測試時段 {t0:.1f}–{t1:.1f} s")
    start = time.time()

    windows: dict[str, deque] = {}
    last_seen: dict[str, float] = {}
    model_warn: Warnings = {}
    rule_warn: Warnings = {}
    model_warn_by_vid: dict[str, list[float]] = {}         # 算準確率用（不分對象）
    n_model_warn = 0
    warm = t0 - steps * float(cfg["sumo"]["step_length"])     # 先暖機 2 秒，視窗才是滿的

    for t, frame in iter_frames(states):
        if t < warm:
            continue
        if t > t1:
            break
        vids, partners, wins = [], [], []
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
            if lvl == 2:
                rule_warn.setdefault((ego.vehicle_id, target), []).append(t)
            w = list(q)
            if len(w) < steps:                                  # edge padding，與線上一致
                w = [w[0]] * (steps - len(w)) + w
            vids.append(ego.vehicle_id)
            partners.append(conflict.partner_id)
            wins.append(np.stack(w))
        if wins:
            with torch.no_grad():
                p = torch.softmax(net(torch.from_numpy(np.stack(wins).astype(np.float32))), -1).numpy()
            pred = M.decide(p, tau)
            for vid, partner, lvl_m in zip(vids, partners, pred):
                if lvl_m == 2:
                    model_warn.setdefault((vid, partner), []).append(t)
                    model_warn_by_vid.setdefault(vid, []).append(t)
                    n_model_warn += 1
        # 離開路網的車釋放視窗
        if len(windows) > 500:
            for v in [v for v, s in last_seen.items() if t - s > 1.0]:
                windows.pop(v, None); last_seen.pop(v, None)

    ssm_ev = parse_ssm(ssm)                      # 每台車的危險時間（算準確率用）
    events = danger_events(parse_conflict_pairs(ssm, danger_lt), t0, t1)
    rows = match(events, model_warn, rule_warn, lookback=horizon)
    summ = summarize(rows)

    # 準確率：模型喊危險的時刻中，接下來 3 秒內真的出事的比例
    hit = 0
    for vid, times in model_warn_by_vid.items():
        evs = [t for t, v in ssm_ev.get(vid, []) if v < danger_lt]
        for t in times:
            if first_in_window(evs, t + 1e-6, t + horizon) is not None:
                hit += 1
    summ["model_warn_steps"] = n_model_warn
    summ["model_warn_precision_pct"] = round(100 * hit / n_model_warn, 1) if n_model_warn else None
    summ["node"] = node
    summ["weights"] = wpath.name
    summ["danger_threshold"] = tau
    summ["test_window_s"] = [round(t0, 1), round(t1, 1)]
    summ["duration_s"] = round(time.time() - start, 1)

    w = jsonlog.JsonlWriter("events", cfg=cfg)
    prefix = f"{node}-{wpath.stem}-"
    _drop_previous(pathlib.Path(w.path), prefix)          # 同一節點、同一權重重跑時取代舊結果
    for r in rows:
        w.write(event_id=f"{prefix}{r['event_id']}", intersection=node_id,
                first_warn_model_t=r["first_warn_model_t"],
                first_warn_rule_t=r["first_warn_rule_t"],
                label_t=r["label_t"], lead_time=r["lead_time"])
    out = config.path(f"models/{node}/lead_time_{wpath.stem}.json")
    out.write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
    return summ


def print_summary(s: dict) -> None:
    print(f"\n危險事件 {s['events']} 件（一對車算一件；測試時段 {s['test_window_s'][0]}–{s['test_window_s'][1]} s）")
    if s["both_warned"]:
        print(f"  兩者都有警告的 {s['both_warned']} 件：模型平均提早 {s['lead_time_mean_s']} 秒"
              f"（中位數 {s['lead_time_median_s']} 秒），其中 {s['model_earlier_pct']}% 是模型較早")
    print(f"  模型在事件前 3 秒（預測範圍上限）就已在警告的：{s['model_warned_at_3s_cap']} 件"
          f"——提早秒數以 3 秒為上限")
    print(f"  規則漏掉、模型抓到：{s['rule_missed_model_caught']} 件")
    print(f"  模型漏掉、規則抓到：{s['model_missed_rule_caught']} 件")
    print(f"  兩者都沒抓到：{s['both_missed']} 件")
    if s["model_warn_precision_pct"] is not None:
        print(f"  模型喊「危險」的準確率：{s['model_warn_precision_pct']}%"
              f"（喊了之後 3 秒內真的出事的比例，共喊 {s['model_warn_steps']:,} 次）")
    print(f"  （逐件紀錄已寫入 logs/events.jsonl，耗時 {s['duration_s']} 秒）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", required=True, choices=["a", "b"])
    ap.add_argument("--round", type=int, default=None,
                    help="用 models/{node}/round_N.pt；不給則用 current.pt")
    a = ap.parse_args(argv)
    print_summary(run(a.node, a.round, config.load()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
