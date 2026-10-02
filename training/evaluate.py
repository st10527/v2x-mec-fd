"""跨路口評估（SPEC §8.1）。

主指標是**跨路口 macro-F1**：A 的模型在 B 的 test set 上，反之亦然。
它直接驗證 SPEC §2 Q3——「單一路口訓練出的模型遇到對方路口的型態會失效」。

輸出一個 2×2 的評估矩陣（模型 × 測試集），並對照 round_0（單獨訓練）與
指定輪次（協同訓練）。收斂曲線那張圖要的就是這些數字。

用法：
    python -m training.evaluate                  # 比較 round_0 與最後一輪
    python -m training.evaluate --rounds 0 3 5   # 指定要比的輪次
"""
from __future__ import annotations

import argparse
import json

import torch

from mec_app import config, model as M
from training import metrics
from training.train_local import evaluate_model, load_node_data


def load_round(node: str, rnd: int, cfg: dict) -> M.RiskCNN | None:
    p = config.path(f"models/{node}/round_{rnd}.pt")
    if not p.exists():
        return None
    m = M.build()
    m.load_state_dict(torch.load(p, map_location="cpu", weights_only=True))
    m.eval()
    return m


def available_rounds(node: str, cfg: dict) -> list[int]:
    d = config.path(f"models/{node}")
    if not d.exists():
        return []
    out = []
    for p in d.glob("round_*.pt"):
        try:
            out.append(int(p.stem.split("_")[1]))
        except (IndexError, ValueError):
            continue
    return sorted(out)


def evaluate_matrix(rnd: int, cfg: dict) -> dict:
    """對指定輪次，算出 2×2 的 (模型節點 × 測試集節點) macro-F1。"""
    nodes = sorted(cfg["nodes"])
    tests = {n: load_node_data(n, cfg)[2] for n in nodes}
    out = {"round": rnd, "cells": {}}
    for mn in nodes:
        m = load_round(mn, rnd, cfg)
        if m is None:
            continue
        tau = M.load_threshold(mn, f"round_{rnd}")
        out.setdefault("tau", {})[mn] = tau
        for tn in nodes:
            rep = evaluate_model(m, tests[tn], cfg, tau)
            out["cells"][f"{mn}->{tn}"] = rep
    return out


def print_matrix(res: dict, cfg: dict) -> None:
    nodes = sorted(cfg["nodes"])
    print(f"\n--- round {res['round']} ---")
    head = "模型 \\ 測試集"                       # 放在 f-string 外：Python 3.10 不允許其中含反斜線
    print(f"{head:<16}" + "".join(f"{n.upper():>12}" for n in nodes))
    for mn in nodes:
        row = f"{mn.upper():<16}"
        for tn in nodes:
            cell = res["cells"].get(f"{mn}->{tn}")
            row += f"{cell['macro_f1']:>12.4f}" if cell else f"{'—':>12}"
        print(row)
    for key, cell in res["cells"].items():
        mn, tn = key.split("->")
        if mn != tn:
            print(f"  {key} 危險類 recall {cell['danger_recall']:.4f}"
                  f"   （跨路口漏報率 {1 - cell['danger_recall']:.1%}）")
    R = float(cfg["decision"]["miss_cost_ratio"])
    tau = res.get("tau", {})
    print(f"  實務數字（危險門檻 τ = {tau}；成本 = {R:g}×漏報 + 誤報，每 1000 筆）：")
    for key, cell in res["cells"].items():
        fn, fp, tp, n = practical(cell)
        prec = tp / (tp + fp) if tp + fp else float("nan")
        print(f"    {key.upper():<6} 漏報 {fn / max(fn + tp, 1):6.1%}   喊危險的準確率 {prec:6.1%}"
              f"   成本 {1000 * (R * fn + fp) / n:7.1f}")


def practical(cell: dict) -> tuple[int, int, int, int]:
    """由混淆矩陣取出 (危險漏報, 危險誤報, 危險命中, 總筆數)。"""
    cm = cell["confusion"]
    tp = cm[2][2]
    fn = sum(cm[2]) - tp
    fp = sum(row[2] for row in cm) - tp
    return fn, fp, tp, sum(sum(r) for r in cm)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", type=int, nargs="*", default=None)
    ap.add_argument("--out", default="models/evaluation.json")
    a = ap.parse_args(argv)
    cfg = config.load()

    nodes = sorted(cfg["nodes"])
    rounds = a.rounds
    if rounds is None:
        have = sorted(set(available_rounds(nodes[0], cfg))
                      & set(available_rounds(nodes[1], cfg)))
        if not have:
            raise SystemExit(
                "找不到任何 round_*.pt。請先執行 training.train_local "
                "（第 0 輪）與 training.distill（第 1–5 輪）。")
        rounds = sorted({have[0], have[-1]})

    results = [evaluate_matrix(r, cfg) for r in rounds]
    for res in results:
        print_matrix(res, cfg)

    # 協同相對單獨訓練的增益——SPEC §12 要求增益 < 2% 時回頭加大異質性
    if len(results) >= 2:
        base, last = results[0], results[-1]
        print(f"\n--- round {base['round']} -> {last['round']} 跨路口增益 ---")
        worst = 1.0
        for mn in nodes:
            for tn in nodes:
                if mn == tn:
                    continue
                k = f"{mn}->{tn}"
                if k in base["cells"] and k in last["cells"]:
                    b = base["cells"][k]["macro_f1"]
                    l = last["cells"][k]["macro_f1"]
                    print(f"  {k}: {b:.4f} -> {l:.4f}  ({l - b:+.4f})")
                    worst = min(worst, l - b)
        if worst < 0.02:
            print("\n  !! 跨路口 macro-F1 提升 < 2%（SPEC §12 的徵兆）。"
                  "\n     先回頭加大 SPEC §4.3 的路口異質性再重跑；"
                  "\n     若調整後仍無效，如實報告負結果並分析原因——"
                  "\n     負結果誠實呈現優於造假，評審通常接受。")

    p = config.path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完整結果已寫入 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
