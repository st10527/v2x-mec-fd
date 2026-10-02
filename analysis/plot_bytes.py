"""圖 2：傳輸量對比（SPEC §8.3）。

長條圖，logits vs 模型參數，對數軸，標註倍數。驗證 SPEC §2 Q5。

SPEC §8.3 特別要求標明採用的是「原始張量大小」還是「HTTP 實際傳輸量」，
兩者不可混用——base64 會把 24,000 B 膨脹成約 32,000 B。本圖三根都畫出來，
並在圖上註明，被評審追問時站得住。

資料來源：logs/fd_rounds.jsonl 的 bytes_sent（實測）+ config.yaml 的模型大小
"""
from __future__ import annotations

import base64
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import matplotlib.pyplot as plt                       # noqa: E402

from mec_app import config, jsonlog                   # noqa: E402
from analysis import _style                           # noqa: E402


def main() -> int:
    cjk = _style.setup()
    cfg = config.load()
    rows = [r for r in jsonlog.read("fd_rounds") if r.get("bytes_sent")]

    # 優先採用實測值；沒有 log 時退回 config 的理論值並在圖上註明
    if rows:
        raw = int(round(sum(r["bytes_sent"] for r in rows) / len(rows)))
        src = _style.label(f"實測平均（{len(rows)} 輪）",
                           f"measured mean of {len(rows)} rounds", cjk)
    else:
        raw = int(cfg["fd"]["logits"]["raw_bytes"])
        src = _style.label("理論值（尚無實測 log）", "theoretical (no logs yet)", cjk)

    wire = len(base64.b64encode(b"\0" * raw))
    model = int(cfg["model"]["fp32_bytes"])

    bars = [
        (_style.label("logits\n原始張量", "logits\nraw tensor", cjk), raw, "#457b9d"),
        (_style.label("logits\nbase64 上線", "logits\nbase64 on wire", cjk), wire, "#a8dadc"),
        (_style.label(f"模型參數\n{cfg['model']['param_count']:,} 個",
                      f"model params\n{cfg['model']['param_count']:,}", cjk),
         model, "#c1121f"),
    ]

    fig, ax = plt.subplots(figsize=(6.8, 4.8))
    xs = range(len(bars))
    ax.bar(xs, [b[1] for b in bars], color=[b[2] for b in bars], width=0.6)
    ax.set_yscale("log")
    ax.set_xticks(list(xs)); ax.set_xticklabels([b[0] for b in bars])
    ax.set_ylabel(_style.label("每輪每節點傳輸量（bytes，對數軸）",
                               "Bytes per round per node (log scale)", cjk))
    ax.set_title(_style.label("聯邦蒸餾 vs FedAvg 的每輪傳輸量",
                              "Per-round payload: distillation vs FedAvg", cjk))

    for i, (_, v, _c) in enumerate(bars):
        ax.text(i, v * 1.12, f"{v:,} B", ha="center", fontsize=10)
    ax.text(0.5, model * 0.42,
            _style.label(f"省 {model / raw:.1f} 倍\n（base64 計 {model / wire:.1f} 倍）",
                         f"{model / raw:.1f}x smaller\n({model / wire:.1f}x on wire)", cjk),
            ha="center", fontsize=11, fontweight="bold", color="#c1121f")
    ax.set_ylim(top=model * 3)
    ax.text(0.01, 0.02, src, transform=ax.transAxes, fontsize=8.5, color="#555")

    _style.save(fig, "fig2_bytes.png")
    print(f"raw {raw:,} B / wire {wire:,} B / model {model:,} B"
          f"  -> {model / raw:.2f}x（原始）、{model / wire:.2f}x（上線）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
