"""圖 3：延遲拆解堆疊圖（SPEC §8.2、§8.3）。

T1–T4 分段，MEC 路徑與 Cloud 路徑並列。驗證 SPEC §2 Q6。
SPEC §8.3 指出這張可直接沿用平台範例已有的 MEC / Cloud 對照做法，
成本最低、說服力高。

四個區段（SPEC §8.2）：
    T1 上行網路   UE 送出 ts_ue            -> MEC App 收到請求
    T2 特徵組裝   收到請求                 -> 滑動視窗就緒
    T3 模型推論   視窗就緒                 -> 輸出 risk level
    T4 下行回傳   產生回應                 -> UE 收到

資料來源：logs/ue_{node}.jsonl。T4 與 T2/T3 的分界只有 UE 端量得到
（MEC 端的 inference log 在原理上放不進 T4），故以 UE 端 log 為準。

所有時間戳都在同一台實體主機上取得，避免時鐘同步爭議——
SPEC §8.2 要求這一點必須在報告中主動說明，本圖直接標在圖上。

用法：
    python -m analysis.plot_latency                        # 只畫 MEC
    python -m analysis.plot_latency --cloud logs/ue_cloud.jsonl
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import matplotlib.pyplot as plt                       # noqa: E402
import numpy as np                                    # noqa: E402

from mec_app import config                            # noqa: E402
from analysis import _style                           # noqa: E402

SEGMENTS = [("t1_uplink_ms", "T1 上行網路", "T1 uplink", "#003049"),
            ("t2_features_ms", "T2 特徵組裝", "T2 features", "#457b9d"),
            ("t3_inference_ms", "T3 模型推論", "T3 inference", "#f77f00"),
            ("t4_downlink_ms", "T4 下行回傳", "T4 downlink", "#c1121f")]


def read_ue_log(path) -> list[dict]:
    p = pathlib.Path(path)
    if not p.exists():
        return []
    out = []
    for ln, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"{p}:{ln} 不是合法 JSON：{e}") from e
        if "error" not in rec and "t_total_ms" in rec:
            out.append(rec)
    return out


def summarize(rows: list[dict]) -> dict:
    """各段取中位數。用中位數而非平均，是因為網路延遲的長尾會把平均拉歪，
    而我們要呈現的是典型情況；p95 另外標出來，不混在同一根長條裡。"""
    s = {}
    for key, _zh, _en, _c in SEGMENTS:
        vals = [r[key] for r in rows if r.get(key) is not None]
        s[key] = float(np.median(vals)) if vals else 0.0
    tot = [r["t_total_ms"] for r in rows]
    s["n"] = len(rows)
    s["total_p50"] = float(np.median(tot)) if tot else 0.0
    s["total_p95"] = float(np.percentile(tot, 95)) if tot else 0.0
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cloud", default=None,
                    help="Cloud 對照組的 UE log（沿用平台範例的 Cloud 模式）")
    a = ap.parse_args(argv)

    cjk = _style.setup()
    cfg = config.load()

    paths = []
    for n in sorted(cfg["nodes"]):
        p = config.path(cfg["logs"]["dir"], cfg["logs"]["ue"]["file"].format(node=n))
        rows = read_ue_log(p)
        if rows:
            paths.append((_style.label(f"MEC 路口 {n.upper()}",
                                       f"MEC node {n.upper()}", cjk), rows))
    if a.cloud:
        rows = read_ue_log(a.cloud)
        if rows:
            paths.append((_style.label("Cloud 對照", "Cloud baseline", cjk), rows))

    if not paths:
        raise SystemExit(
            "找不到任何 UE 端延遲 log。請先以 ue/sumo_ue_sender.py 送資料，"
            "它會寫出 logs/ue_{node}.jsonl。")

    labels = [p[0] for p in paths]
    stats = [summarize(p[1]) for p in paths]

    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    bottoms = np.zeros(len(paths))
    for key, zh, en, color in SEGMENTS:
        vals = np.array([s[key] for s in stats])
        ax.bar(labels, vals, bottom=bottoms, color=color,
               label=_style.label(zh, en, cjk), width=0.5)
        for i, (v, b) in enumerate(zip(vals, bottoms)):
            if v > 0.02 * max(s["total_p50"] for s in stats):
                ax.text(i, b + v / 2, f"{v:.2f}", ha="center", va="center",
                        fontsize=8.5, color="white", fontweight="bold")
        bottoms += vals

    for i, s in enumerate(stats):
        ax.text(i, bottoms[i] * 1.03,
                _style.label(f"中位數 {s['total_p50']:.2f} ms\np95 {s['total_p95']:.2f} ms\n"
                             f"n={s['n']:,}",
                             f"p50 {s['total_p50']:.2f} ms\np95 {s['total_p95']:.2f} ms\n"
                             f"n={s['n']:,}", cjk),
                ha="center", fontsize=9)

    ax.set_ylabel(_style.label("端到端延遲（ms，各段中位數）",
                               "End-to-end latency (ms, per-segment median)", cjk))
    ax.set_title(_style.label("端到端延遲拆解：MEC vs Cloud",
                              "End-to-end latency breakdown: MEC vs Cloud", cjk))
    ax.set_ylim(top=max(bottoms) * 1.35)
    ax.legend(loc="upper left", frameon=False, ncols=2, fontsize=9)
    ax.text(0.01, -0.16,
            _style.label("所有時間戳取自同一台實體主機，無跨機時鐘同步誤差（SPEC §8.2）",
                         "All timestamps taken on one physical host; no cross-host clock skew",
                         cjk),
            transform=ax.transAxes, fontsize=8, color="#555")
    _style.save(fig, "fig3_latency.png")

    for lbl, s in zip(labels, stats):
        print(f"{lbl}: n={s['n']:,}  T1 {s['t1_uplink_ms']:.2f}  "
              f"T2 {s['t2_features_ms']:.2f}  T3 {s['t3_inference_ms']:.2f}  "
              f"T4 {s['t4_downlink_ms']:.2f}  總計中位數 {s['total_p50']:.2f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
