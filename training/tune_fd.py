"""協同訓練參數的小範圍調整——有依據地選，而不是想怎麼設就怎麼設。

=== 選擇依據：實務成本，只用驗證資料 ===
真實的預警系統要在兩種錯誤之間取捨：
  漏報：危險來了沒喊——可能就是一場事故
  誤報：沒事卻喊危險——駕駛被打擾，喊多了就不再理會警告
成本 = R × 漏報數 + 誤報數（每 1000 筆樣本），R = 漏報一次抵幾次誤報（config 的 decision.miss_cost_ratio）。
R 沒有標準答案，所以同時看 R = 3、10、30：選出來的設定若都一樣，就不必爭論 R 該是多少。

每個節點另有一個「危險警告門檻」τ：模型認為危險的機率 ≥ τ 才喊危險。
τ 只用**自己路口的驗證資料**選（部署時本來就只有自己的資料）。

參數組合的比較標準：兩個模型各自在「自己路口」與「對方路口」驗證資料上的平均成本。
這對應系統設計階段——兩個路口的試營運資料都在手上，選一次參數；
**測試資料全程不碰**，只在最後報告時用。

=== 搜尋範圍（刻意很小）===
  α（協同權重）：0、0.1、0.3、0.5——α = 0 就是「不協同、只多訓練同樣次數」的對照組，
                  回答「是不是多訓練就好」
  每輪學習率：1e-3（與預訓練相同）、1e-4（微調常用，實測較穩定）
其他（溫度 T = 3、輪數 5、每輪 5+5 epochs）照 SPEC §7.3–7.4 不動。

模擬方式：兩個節點在同一個行程內按輪同步跑，算法與 training.distill.run 完全相同
（本地訓練 → 產生軟標籤 → 交換 → 蒸餾），只是交換不走網路。選定後的正式結果仍以
training.distill 經 MEP Gateway 跑一次。

用法：
    python -m training.tune_fd run --alpha 0.3 --lr 1e-4       # 跑一組，結果存 models/tune/
    python -m training.tune_fd select                          # 比較所有已跑的組合
"""
from __future__ import annotations

import argparse
import json
import re
import time

import numpy as np
import torch

from mec_app import config, model as M
from training import distill
from training.decision import best_tau, cost_per_1000, counts, probs  # noqa: F401
from training.train_local import (load_node_data, make_criterion, make_loader,
                                  seed_everything, train_epochs)

NODES = ("a", "b")
R_CHECK = (3, 10, 30)


def tag_of(alpha: float, lr: float, proxy_tag: str = "") -> str:
    return f"alpha{alpha:g}_lr{lr:g}" + (f"_{proxy_tag}" if proxy_tag else "")


def simulate(alpha, lr: float, cfg: dict) -> dict:
    """從 round_0 出發，兩節點同步跑 fd.rounds 輪。回傳 {node: model}。

    alpha 可為單一數值（兩節點相同）或 {node: α}（非對稱：各節點自己決定吸收多少對方的知識）。
    """
    c = json.loads(json.dumps(cfg))
    alphas = alpha if isinstance(alpha, dict) else {n: alpha for n in NODES}
    seed_everything(int(c["train"]["seed"]))
    data = {n: load_node_data(n, c) for n in NODES}
    proxy, _ = distill.load_proxy(c)
    models, opts = {}, {}
    for n in NODES:
        m = M.build()
        m.load_state_dict(torch.load(config.path(f"models/{n}/round_0.pt"),
                                     map_location="cpu", weights_only=True))
        models[n], opts[n] = m, torch.optim.Adam(m.parameters(), lr=lr)
    bs = int(c["train"]["batch_size"])
    for rnd in range(1, int(c["fd"]["rounds"]) + 1):
        t0 = time.time()
        for n in NODES:                                         # 1) 本地訓練
            tr = data[n][0]
            train_epochs(models[n], make_loader(tr, bs, True),
                         int(c["train"]["epochs_per_round"]), make_criterion(tr.y, c), opts[n])
        soft = {n: distill.proxy_logits(models[n], proxy) for n in NODES}   # 2) 軟標籤
        for n in NODES:                                         # 3–5) 交換並蒸餾
            peer = config.peer_of(n)
            c["fd"]["alpha"] = alphas[n]
            distill.distill_epochs(models[n], data[n][0], proxy, soft[peer],
                                   int(c["train"]["epochs_distill"]), c, opts[n])
        print(f"  第 {rnd} 輪完成（{time.time() - t0:.0f}s）", flush=True)
    return models


def dump_probs(models: dict, cfg: dict) -> dict[str, np.ndarray]:
    """每個模型在兩個路口的驗證與測試資料上的機率輸出。"""
    out = {}
    for site in NODES:
        _, va, te = load_node_data(site, cfg)
        out[f"y_val_{site}"], out[f"y_test_{site}"] = va.y, te.y
        for n, m in models.items():
            out[f"p_val_{n}_on_{site}"] = probs(m, va)
            out[f"p_test_{n}_on_{site}"] = probs(m, te)
    return out


def score(d: dict, R: float) -> dict:
    """某組參數在驗證資料上的成本（τ 由各節點自己的驗證資料選）。"""
    taus = {n: best_tau(d[f"y_val_{n}"], d[f"p_val_{n}_on_{n}"], R) for n in NODES}
    cells = {}
    for n in NODES:
        for site in NODES:
            cells[f"{n}->{site}"] = round(cost_per_1000(
                d[f"y_val_{site}"], d[f"p_val_{n}_on_{site}"], taus[n], R), 2)
    return {"tau": taus, "cells": cells,
            "mean_cost": round(float(np.mean(list(cells.values()))), 2)}


def load_round0(cfg: dict) -> dict:
    models = {}
    for n in NODES:
        m = M.build()
        m.load_state_dict(torch.load(config.path(f"models/{n}/round_0.pt"),
                                     map_location="cpu", weights_only=True))
        models[n] = m
    return models


def cmd_run(a, cfg) -> None:
    if a.threads:
        torch.set_num_threads(a.threads)
    out_dir = config.path("models", "tune"); out_dir.mkdir(parents=True, exist_ok=True)
    if a.round0:
        tag, models = "round0", load_round0(cfg)
    else:
        if a.proxy:                                  # 試驗其他公共資料（不動正式的 proxy_set）
            cfg["fd"]["proxy"]["path"] = a.proxy
        if a.alpha_a is not None or a.alpha_b is not None:
            alpha = {"a": a.alpha if a.alpha_a is None else a.alpha_a,
                     "b": a.alpha if a.alpha_b is None else a.alpha_b}
            tag = f"alphaA{alpha['a']:g}_B{alpha['b']:g}_lr{a.lr:g}" + (f"_{a.proxy_tag}" if a.proxy_tag else "")
        else:
            alpha, tag = a.alpha, tag_of(a.alpha, a.lr, a.proxy_tag)
        if a.seed is not None:                        # 換亂數種子，確認差距不是運氣
            cfg["train"]["seed"] = a.seed
            tag += f"_seed{a.seed}"
        print(f"=== {tag} ===", flush=True)
        models = simulate(alpha, a.lr, cfg)
    for n, m in models.items():                       # 權重也存下：事件層級評估需要重新推論
        torch.save(m.state_dict(), out_dir / f"{tag}_{n}.pt")
    np.savez_compressed(out_dir / f"{tag}.npz", **dump_probs(models, cfg))
    print(f"已存 models/tune/{tag}.npz")


def cmd_select(cfg) -> dict:
    out_dir = config.path("models", "tune")
    runs = {f.stem: dict(np.load(f)) for f in sorted(out_dir.glob("*.npz"))}
    if not runs:
        raise SystemExit("models/tune/ 沒有結果，請先執行 run")
    R0 = float(cfg["decision"]["miss_cost_ratio"])
    table = {tag: {R: score(d, R) for R in sorted(set(R_CHECK) | {R0})} for tag, d in runs.items()}
    print(f"\n驗證資料上的平均成本（每 1000 筆；越低越好）  R = 漏報一次抵幾次誤報")
    print(f"{'參數組合':<22}" + "".join(f"{'R=' + format(R, 'g'):>10}" for R in sorted(table[next(iter(table))])))
    for tag, byR in table.items():
        print(f"{tag:<22}" + "".join(f"{s['mean_cost']:>10.2f}" for s in byR.values()))
    pick = {}
    for R in sorted(table[next(iter(table))]):
        cand = {t: v[R]["mean_cost"] for t, v in table.items() if t != "round0"}
        pick[R] = min(cand, key=cand.get)
    print("\n各 R 下成本最低的組合：" + "、".join(f"R={R:g} → {t}" for R, t in pick.items()))
    chosen = pick[R0]
    s = table[chosen][R0]
    print(f"採用 R = {R0:g}：{chosen}；危險警告門檻 τ = {s['tau']}")
    print("\n驗證資料上的實務數字（採用組合 vs round 0，各自以本地驗證資料校準門檻）：")
    for tag in ("round0", chosen):
        if tag not in runs:
            continue
        d, taus = runs[tag], table[tag][R0]["tau"]
        cells = []
        for n in NODES:
            for site in NODES:
                fn, fp, nd = counts(d[f"y_val_{site}"], d[f"p_val_{n}_on_{site}"], taus[n])
                tp = nd - fn
                prec = tp / (tp + fp) if tp + fp else float("nan")
                cells.append(f"{n.upper()}→{site.upper()} 漏報 {100 * fn / max(nd, 1):4.1f}% 準確 {100 * prec:4.1f}%")
        print(f"  {tag:<20} " + "｜".join(cells))
    summary = {"miss_cost_ratio": R0, "chosen": chosen, "tau": s["tau"], "pick_by_R": pick,
               "table": {t: {str(R): v for R, v in byR.items()} for t, byR in table.items()}}
    (out_dir / "selection.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    return summary


# ---------------------------------------------------------------------------
# 新準則（SPEC §6.4）：事件召回 >= 95% 前提下的誤報
# ---------------------------------------------------------------------------
def _models_of(tag: str) -> dict:
    out_dir = config.path("models", "tune")
    models = {}
    for n in NODES:
        official = re.fullmatch(r"round(\d+)", tag)           # round0 / round5 = 正式流程產出的權重
        p = (config.path(f"models/{n}/round_{official.group(1)}.pt") if official
             else out_dir / f"{tag}_{n}.pt")
        if not p.exists():
            return {}
        m = M.build()
        m.load_state_dict(torch.load(p, map_location="cpu", weights_only=True))
        models[n] = m
    return models


def evaluate_alerts(tag: str, cfg: dict, split: str = "val") -> dict | None:
    """各節點以本路口驗證資料校準兩段門檻，再看在兩個路口 split 時段的表現。"""
    from training import events as EV
    models = _models_of(tag)
    if not models:
        return None
    lookback = float(cfg["label"]["horizon_seconds"])
    reps = {site: EV.load_replay(site, split, cfg) for site in NODES}
    val_reps = reps if split == "val" else {site: EV.load_replay(site, "val", cfg) for site in NODES}
    res = {"tag": tag, "split": split, "tau": {}, "cells": {}}
    for n, m in models.items():
        th = EV.calibrate_two_tier(val_reps[n], EV.danger_scores(m, val_reps[n]), cfg)
        res["tau"][n] = {"caution": th["caution"], "danger": th["danger"]}
        for site in NODES:
            sc = EV.danger_scores(m, reps[site])
            res["cells"][f"{n}->{site}"] = {
                "soft": EV.model_metrics(reps[site], sc, th["caution"], lookback, "caution"),
                "strong": EV.model_metrics(reps[site], sc, th["danger"], lookback, "danger"),
            }
    return res


def cmd_select_events(cfg, tags: list[str], split: str) -> dict:
    out_dir = config.path("models", "tune")
    if not tags:
        tags = ["round0"] + sorted({f.stem.rsplit("_", 1)[0] for f in out_dir.glob("*_a.pt")})
    rows = [r for t in tags if (r := evaluate_alerts(t, cfg, split))]
    print(f"\n{split} 時段｜門檻各自以本路口驗證資料校準（注意：召回 >= "
          f"{cfg['decision']['caution_event_recall']:.0%}；危險：精準度 >= {cfg['decision']['danger_alert_precision']:.0%}）")
    print("每格：注意 召回 / 每次通過誤報｜危險 召回 / 精準度")
    head = "".join(f"{k.upper():>34}" for k in ("a->a", "a->b", "b->a", "b->b"))
    print(f"{'組合':<24}{head}")
    for r in rows:
        line = f"{r['tag']:<24}"
        for k in ("a->a", "a->b", "b->a", "b->b"):
            c = r["cells"][k]
            line += (f"   {c['soft']['recall']:5.1%} / {c['soft']['false_per_passage']:4.2f}"
                     f"｜{c['strong']['recall']:5.1%} / {c['strong']['alert_precision']:5.1%}")
        print(line)
    out = out_dir / f"selection_events_{split}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完整結果：{out}")
    return {"rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--alpha", type=float, default=0.5)
    r.add_argument("--lr", type=float, default=1e-3)
    r.add_argument("--seed", type=int, default=None, help="換亂數種子（檢查差距是否只是運氣）")
    r.add_argument("--alpha-a", type=float, default=None, help="節點 A 的 α（非對稱時用）")
    r.add_argument("--alpha-b", type=float, default=None, help="節點 B 的 α（非對稱時用）")
    r.add_argument("--threads", type=int, default=0)
    r.add_argument("--round0", action="store_true", help="只輸出 round_0 模型的機率（對照）")
    r.add_argument("--proxy", default="", help="改用這份公共資料（例如 data/proxy_set_natural.npy）")
    r.add_argument("--proxy-tag", default="", help="結果檔名加上的標記")
    sub.add_parser("select")
    se = sub.add_parser("select-events", help="新準則：事件召回 >= 95% 前提下比誤報")
    se.add_argument("--split", default="val", choices=["val", "test"])
    se.add_argument("--tags", nargs="*", default=[])
    a = ap.parse_args(argv)
    cfg = config.load()
    if a.cmd == "run":
        cmd_run(a, cfg)
    elif a.cmd == "select-events":
        cmd_select_events(cfg, a.tags, a.split)
    else:
        cmd_select(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
