"""新路口上線：在地微調需要多少當地資料？（SPEC §7.6）

每個路口條件不同，一個模型打全部不實際。新路口上線的流程是：
  ① 取得起點模型  ② 收一段當地車流  ③ 在地微調權重  ④ 在地校準兩段式門檻  ⑤ 上線把關
這支程式回答 ③ 的實務問題——「新路口要收多久的資料才能上線？好的起點能省多少？」

做法：把 B 當成新路口，只給它訓練時段的前 N 分鐘資料，從不同起點出發、用相同的訓練預算微調，
門檻一律用 B 的驗證資料校準，最後在「不同亂數種子錄的獨立 B 車流」上評估
（sumo/intersection_b/extra/seedNNN，約 950 件危險事件；訓練從沒看過）。

起點：
  scratch        從零開始（沒有任何其他路口的知識）
  donor:<路徑>   從別的路口的模型出發（例如 A 的 round_0、或協同訓練後的模型）

用法：
    python -m training.adapt --site b --minutes 5 10 20 42 \
        --start scratch donor:models/a/round_0.pt donor:models/tune/alpha0.5_lr0.001_a.pt
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import torch

from mec_app import config, model as M
from training import dataset as D, events as EV
from training.train_local import (load_node_data, make_criterion, make_loader,
                                  seed_everything, train_epochs)

EXTRA = [f"seed{s}" for s in range(101, 107)]


def subset_minutes(split: D.Split, minutes: float) -> D.Split:
    t0 = float(split.t.min())
    keep = split.t < t0 + 60.0 * minutes
    return D.Split(split.X[keep], split.y[keep], split.t[keep],
                   split.vid[keep] if split.vid is not None else None, f"{split.name}[{minutes:g}min]")


def start_model(start: str):
    m = M.build()
    if start.startswith("donor:"):
        m.load_state_dict(torch.load(config.path(start.split(":", 1)[1]),
                                     map_location="cpu", weights_only=True))
    return m


def evaluate_on(model, site: str, splits: list[str], th: dict, cfg: dict) -> dict:
    """把多段獨立車流的事件合在一起算（事件數加總，誤報以總通過車次平均）。"""
    lb = float(cfg["label"]["horizon_seconds"])
    agg = {"soft": [0, 0, 0, 0], "strong": [0, 0, 0, 0]}      # caught, events, false, vehicles/alerts
    for sp in splits:
        rep = EV.load_replay(site, sp, cfg)
        s = EV.danger_scores(model, rep)
        soft = EV.model_metrics(rep, s, th["caution"], lb, "caution")
        strong = EV.model_metrics(rep, s, th["danger"], lb, "danger")
        for k, m in (("soft", soft), ("strong", strong)):
            agg[k][0] += m["caught"]; agg[k][1] += m["events"]; agg[k][2] += m["false_alerts"]
            agg[k][3] += m["vehicles"] if k == "soft" else m["alerts"]
    so, st = agg["soft"], agg["strong"]
    return {
        "events": so[1],
        "soft_recall": so[0] / max(so[1], 1),
        "soft_false_per_passage": so[2] / max(so[3], 1),
        "strong_recall": st[0] / max(st[1], 1),
        "strong_precision": (st[3] - st[2]) / max(st[3], 1),
    }


def run(site: str, minutes: list[float], starts: list[str], epochs: int, seed: int, cfg: dict) -> list[dict]:
    tr, va, _ = load_node_data(site, cfg)
    val_rep = EV.load_replay(site, "val", cfg)
    rows = []
    for start in starts:
        for mins in minutes:
            seed_everything(seed)
            sub = subset_minutes(tr, mins)
            m = start_model(start)
            opt = torch.optim.Adam(m.parameters(), lr=float(cfg["train"]["lr"]))
            t0 = time.time()
            if len(sub) and len(np.unique(sub.y)) == 3:
                train_epochs(m, make_loader(sub, int(cfg["train"]["batch_size"]), True),
                             epochs, make_criterion(sub.y, cfg), opt)
            th = EV.calibrate_two_tier(val_rep, EV.danger_scores(m, val_rep), cfg)
            th = {"caution": th["caution"], "danger": th["danger"]}
            res = evaluate_on(m, site, EXTRA, th, cfg)
            row = {"start": start, "minutes": mins, "n_train": int(len(sub)),
                   "danger_train": int((sub.y == 2).sum()), "tau": th,
                   "train_s": round(time.time() - t0, 1), **res}
            rows.append(row)
            print(f"{start:<44} {mins:>4g} 分鐘（{row['n_train']:>6,} 筆，危險 {row['danger_train']:>4}）  "
                  f"注意 召回 {res['soft_recall']:6.1%}  每次通過誤報 {res['soft_false_per_passage']:4.2f}"
                  f"｜危險 召回 {res['strong_recall']:6.1%}  精準度 {res['strong_precision']:6.1%}"
                  f"  τ=({th['caution']},{th['danger']})", flush=True)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="b", choices=["a", "b"])
    ap.add_argument("--minutes", type=float, nargs="+", default=[5, 10, 20, 42])
    ap.add_argument("--start", nargs="+", default=["scratch", "donor:models/a/round_0.pt"])
    ap.add_argument("--epochs", type=int, default=None, help="預設取 train.epochs_pretrain（與 round 0 相同預算）")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    cfg = config.load()
    if a.threads:
        torch.set_num_threads(a.threads)
    seed = a.seed if a.seed is not None else int(cfg["train"]["seed"])
    rows = run(a.site, a.minutes, a.start, a.epochs or int(cfg["train"]["epochs_pretrain"]), seed, cfg)
    out = config.path(a.out or f"models/tune/adapt_{a.site}_seed{seed}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"結果：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
