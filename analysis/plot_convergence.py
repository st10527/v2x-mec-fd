"""圖 1：收斂曲線（SPEC §8.3）。

x 軸為蒸餾輪數 0–5，y 軸為跨路口 macro-F1，兩條線（A→B、B→A），
對照水平虛線為單獨訓練（第 0 輪）。

這是驗證 SPEC §2 Q3 的主圖，也是整份成果最重要的一張：
若兩條線沒有爬升，SPEC §12 要求先回頭加大 §4.3 的路口異質性，
仍無效則如實報告負結果。

資料來源：logs/fd_rounds.jsonl（唯一來源，SPEC §8.4）
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import matplotlib.pyplot as plt                       # noqa: E402

from mec_app import config, jsonlog                   # noqa: E402
from analysis import _style                           # noqa: E402


def main() -> int:
    cjk = _style.setup()
    rows = jsonlog.read("fd_rounds")
    if not rows:
        raise SystemExit(
            "logs/fd_rounds.jsonl 是空的。請先執行 training.train_local "
            "（第 0 輪）與 training.distill（第 1–5 輪）。")

    cfg = config.load()
    nodes = sorted(cfg["nodes"])
    colors = {nodes[0]: "#c1121f", nodes[1]: "#003049"}
    fig, ax = plt.subplots(figsize=(7.2, 4.6))

    plotted = 0
    for n in nodes:
        peer = config.peer_of(n)
        pts = sorted((r["round"], r["cross_f1"]) for r in rows
                     if r["node"] == n and r["cross_f1"] is not None)
        if not pts:
            continue
        xs, ys = zip(*pts)
        ax.plot(xs, ys, marker="o", color=colors[n], linewidth=2,
                label=f"{n.upper()} → {peer.upper()}")
        base = dict(pts).get(0)
        if base is not None:
            ax.axhline(base, color=colors[n], linestyle="--", alpha=0.55,
                       linewidth=1.2)
            ax.annotate(
                _style.label(f"{n.upper()} 單獨訓練 {base:.3f}",
                             f"{n.upper()} solo {base:.3f}", cjk),
                xy=(min(xs), base), xytext=(6, 5),
                textcoords="offset points", fontsize=9, color=colors[n])
            if len(ys) > 1:
                delta = ys[-1] - base
                ax.annotate(f"{delta:+.3f}", xy=(xs[-1], ys[-1]),
                            xytext=(6, 4), textcoords="offset points",
                            fontsize=10, color=colors[n], fontweight="bold")
        plotted += 1

    if not plotted:
        raise SystemExit("fd_rounds.jsonl 裡沒有任何 cross_f1，無法作圖。")

    ax.set_xlabel(_style.label("蒸餾輪數", "Distillation round", cjk))
    ax.set_ylabel(_style.label("跨路口 macro-F1", "Cross-intersection macro-F1", cjk))
    ax.set_title(_style.label("協同訓練的跨路口泛化能力",
                              "Cross-intersection generalization", cjk))
    ax.set_xticks(sorted({r["round"] for r in rows}))
    ax.legend(loc="upper left", frameon=False)
    ax.text(0.99, 0.02,
            _style.label("虛線 = 單獨訓練基準（第 0 輪）",
                         "dashed = solo-training baseline (round 0)", cjk),
            transform=ax.transAxes, fontsize=8.5, color="#555", ha="right")
    ax.margins(x=0.08)
    _style.save(fig, "fig1_convergence.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
