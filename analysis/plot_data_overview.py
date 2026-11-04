"""資料分布圖：讓評審看懂「資料長什麼樣子、兩個路口差在哪」。

四張圖（輸出到 figures/，每張圖背後的數字另存 figures/data/*.csv）：
  fig_data_1_hotspots.png       危險熱點：危險事件發生的位置疊在路網上（A、B 並排）
  fig_data_2_conflict_types.png 危險事件的衝突型態組成（追撞／交叉／匯入…）
  fig_data_3_labels.png         資料集的標籤比例（安全／注意／危險）
  fig_data_4_features.png       A、B 兩路口的特徵分布對照

資料來源（全部是 SUMO 模擬，一小時車流）：
  sumo/intersection_x/ssm.xml.gz     SUMO SSM 記錄的每一對車的衝突（TTC／PET、型態、位置）
  sumo/intersection_x/*.net.xml      路網（畫底圖用）
  data/{a,b,proxy}.npz               建好的資料集（特徵視窗與標籤）

定義沿用專案其他地方，數字才對得上：
  危險／注意門檻：config.yaml 的 label.thresholds（min(TTC, PET) < 1.5 s／< 3.0 s）
  衝突型態：sumo/check_scenario.py 的 conflict_class（依 SUMO 1.27.0 SSM 型態碼）

用法：
    python -m analysis.plot_data_overview
"""
from __future__ import annotations

import csv
import gzip
import pathlib
import sys
import xml.etree.ElementTree as ET
from collections import Counter

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import importlib.util                            # noqa: E402

from analysis._style import save, setup          # noqa: E402
from mec_app import config                       # noqa: E402

import matplotlib.pyplot as plt                  # noqa: E402

# 專案的 sumo/ 目錄與 pip 的 eclipse-sumo 套件（import 名也叫 sumo）撞名，直接從檔案路徑載入
_spec = importlib.util.spec_from_file_location("check_scenario", config.path("sumo", "check_scenario.py"))
_cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cs)
conflict_class = _cs.conflict_class

SITES = {"a": "路口 A（號誌十字）", "b": "路口 B（無號誌 T 字）"}
COLORS = {"a": "#2b5d9b", "b": "#d9822b"}
TYPE_ORDER = ["追撞", "交叉", "匯入", "對向", "碰撞", "其他"]
TYPE_COLORS = {"追撞": "#2b5d9b", "交叉": "#d9822b", "匯入": "#e8b04b",
               "對向": "#8c6bb1", "碰撞": "#c0392b", "其他": "#b8c2cc"}


def scen_dir(site: str) -> pathlib.Path:
    cfg = config.load()
    return config.path(cfg["sumo"]["scenarios"][config.node(site)["scenario"]]["dir"])


def ssm_conflicts(site: str) -> list[tuple[float, int, float, float, float]]:
    """每一對車的衝突取 min(TTC, PET) 最小的那一刻：(值, 型態碼, x, y, 當下車速)。"""
    d = scen_dir(site)
    path = d / "ssm.xml.gz" if (d / "ssm.xml.gz").exists() else d / "ssm.xml"
    out = []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as fh:
        for _, el in ET.iterparse(fh, events=("end",)):
            if el.tag != "conflict":
                continue
            best = None
            for tag in ("minTTC", "PET"):
                for m in el.iter(tag):
                    v, ty, pos = m.get("value"), m.get("type"), m.get("position")
                    if v in (None, "NA") or ty in (None, "NA") or pos in (None, "NA"):
                        continue
                    try:
                        fv = float(v)
                        x, y = (float(p) for p in pos.split(","))
                        sp = float(m.get("speed"))
                    except (TypeError, ValueError):
                        continue
                    if fv >= 0 and (best is None or fv < best[0]):
                        best = (fv, int(float(ty)), x, y, sp)
            if best:
                out.append(best)
            el.clear()
    return out


def lane_shapes(site: str) -> list[np.ndarray]:
    net = next(scen_dir(site).glob("*.net.xml"))
    shapes = []
    for _, el in ET.iterparse(net, events=("end",)):
        if el.tag == "lane" and el.get("shape"):
            shapes.append(np.array([[float(v) for v in p.split(",")[:2]]
                                    for p in el.get("shape").split()]))
        if el.tag == "edge":
            el.clear()
    return shapes


def write_csv(name: str, header: list[str], rows: list[list]) -> None:
    out = config.path("figures", "data", name)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:      # utf-8-sig：Excel 開中文不亂碼
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


# ---------------------------------------------------------------------------
def fig_hotspots(conf: dict, th: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.4))
    rows = []
    for ax, site in zip(axes, ("a", "b")):
        for s in lane_shapes(site):
            ax.plot(s[:, 0], s[:, 1], color="#d5dbe1", lw=2.2, solid_capstyle="round", zorder=1)
        c = np.array(conf[site])
        cau = c[(c[:, 0] >= th["danger_lt"]) & (c[:, 0] < th["caution_lt"])]
        dan = c[c[:, 0] < th["danger_lt"]]
        ax.scatter(cau[:, 2], cau[:, 3], s=4, color="#e8b04b", alpha=0.25, lw=0, label=f"注意（{len(cau):,} 件）", zorder=2)
        ax.scatter(dan[:, 2], dan[:, 3], s=7, color="#c0392b", alpha=0.45, lw=0, label=f"危險（{len(dan):,} 件）", zorder=3)
        cx, cy = np.median(dan[:, 2]), np.median(dan[:, 3])
        ax.set_xlim(cx - 90, cx + 90); ax.set_ylim(cy - 90, cy + 90)
        ax.set_aspect("equal"); ax.grid(False)
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_title(SITES[site])
        ax.legend(loc="lower left", fontsize=9, markerscale=3, frameon=True)
        rows += [[site.upper(), "危險", f"{x:.2f}", f"{y:.2f}", f"{v:.2f}", conflict_class(int(t))] for v, t, x, y, _ in dan]
    fig.suptitle("危險熱點：衝突發生的位置（SUMO 模擬一小時）", y=1.0)
    save(fig, "fig_data_1_hotspots.png")
    write_csv("hotspots_danger.csv", ["路口", "等級", "x_m", "y_m", "min_TTC_PET_s", "衝突型態"], rows)


def fig_conflict_types(conf: dict, th: dict) -> None:
    fig, ax = plt.subplots(figsize=(8.5, 2.8))
    rows = []
    for i, site in enumerate(("b", "a")):
        dan = [t for v, t, *_ in conf[site] if v < th["danger_lt"]]
        cnt = Counter(conflict_class(t) for t in dan)
        tot = sum(cnt.values())
        left = 0.0
        for k in TYPE_ORDER:
            n = cnt.get(k, 0)
            if not n:
                continue
            w = n / tot
            ax.barh(i, w, left=left, color=TYPE_COLORS[k], edgecolor="white", height=0.6)
            if w >= 0.06:
                ax.text(left + w / 2, i, f"{k} {w:.0%}", ha="center", va="center", color="white", fontsize=10)
            left += w
            rows.append([site.upper(), k, n, f"{w:.4f}"])
        ax.text(1.01, i, f"{tot:,} 件", va="center", fontsize=9, color="#6b7785")
    ax.set_yticks([0, 1]); ax.set_yticklabels([SITES["b"], SITES["a"]])
    ax.set_xlim(0, 1); ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(False)
    from matplotlib.patches import Patch
    seen = [k for k in TYPE_ORDER if any(r[1] == k for r in rows)]
    ax.legend(handles=[Patch(color=TYPE_COLORS[k], label=k) for k in seen], ncol=len(seen), fontsize=9,
              loc="upper center", bbox_to_anchor=(0.5, -0.18), frameon=False)
    ax.set_title("危險事件的衝突型態：A 以追撞為主，B 以側向交叉為主")
    save(fig, "fig_data_2_conflict_types.png")
    write_csv("conflict_types_danger.csv", ["路口", "衝突型態", "件數", "比例"], rows)


def fig_labels() -> None:
    names = ["安全", "注意", "危險"]
    colors = ["#b8c2cc", "#e8b04b", "#c0392b"]
    sets = [("a", SITES["a"]), ("b", SITES["b"]), ("proxy", "公共代理場景")]
    fig, ax = plt.subplots(figsize=(8.5, 2.9))
    rows = []
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=c, label=n) for c, n in zip(colors, names)], ncol=3, fontsize=9,
              loc="upper center", bbox_to_anchor=(0.5, -0.18), frameon=False)
    for i, (key, title) in enumerate(reversed(sets)):
        y = np.load(config.path("data", f"{key}.npz"))["y"]
        cnt = np.bincount(y, minlength=3)
        tot = cnt.sum()
        left = 0.0
        for k in range(3):
            w = cnt[k] / tot
            ax.barh(i, w, left=left, color=colors[k], edgecolor="white", height=0.6)
            if k == 0:
                ax.text(left + w / 2, i, f"{names[k]} {w:.1%}", ha="center", va="center",
                        color="#1f2d3d", fontsize=10)
            left += w
            rows.append([title, names[k], int(cnt[k]), f"{w:.4f}"])
        ax.text(1.01, i, f"注意 {cnt[1] / tot:.1%}｜危險 {cnt[2] / tot:.1%}｜{tot:,} 筆",
                va="center", fontsize=9, color="#1f2d3d")
    ax.set_yticks(range(3)); ax.set_yticklabels([t for _, t in reversed(sets)])
    ax.set_xlim(0, 1); ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(False)
    dpct = {r[0]: float(r[3]) for r in rows if r[1] == "危險"}
    ax.set_title(f"資料集的標籤比例：危險樣本很少（A {dpct[SITES['a']]:.1%}、B {dpct[SITES['b']]:.1%}）")
    save(fig, "fig_data_3_labels.png")
    write_csv("label_distribution.csv", ["資料集", "標籤", "筆數", "比例"], rows)


def fig_features(conf: dict, th: dict) -> None:
    cfg = config.load()
    order, norm = cfg["features"]["order"], cfg["features"]["norm"]

    def last_step(site: str, feat: str, danger_only: bool) -> np.ndarray:
        d = np.load(config.path("data", f"{site}.npz"))
        x = d["X"][:, -1, order.index(feat)].astype(np.float64)
        if danger_only:
            x = x[d["y"] == 2]
        lo, hi = float(norm[feat]["min"]), float(norm[feat]["max"])
        return lo + x * (hi - lo)                                   # 還原成原始單位

    def ssm_speed(site: str, *_):
        return np.array([sp for v, _, _, _, sp in conf[site] if v < th["danger_lt"]])

    # 第三格取自 SSM（與衝突型態同一來源）。不用模型特徵 heading_diff：特徵裡的「衝突對象」
    # 是以最近接近點挑的，與 SSM 記錄的那一對車定義不同，畫出來會與衝突型態圖看似矛盾。
    panels = [
        ("ego_speed", False, "車速（m/s）｜全部樣本", np.linspace(0, 20, 41), last_step),
        ("rel_speed", True, "與最近衝突車的接近速度（m/s）｜危險樣本", np.linspace(-10, 35, 46), last_step),
        ("ssm_speed", True, "危險發生當下的車速（m/s）｜SSM 危險事件", np.linspace(0, 20, 41), ssm_speed),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.6))
    rows = []
    for ax, (feat, dang, title, bins, src) in zip(axes, panels):
        for site in ("a", "b"):
            v = src(site, feat, dang)
            h, _ = np.histogram(v, bins=bins)
            p = h / max(h.sum(), 1)
            ax.stairs(p, bins, fill=True, alpha=0.35, color=COLORS[site])
            ax.stairs(p, bins, color=COLORS[site], lw=1.4, label=SITES[site])
            rows += [[site.upper(), feat, "危險" if dang else "全部", f"{bins[i]:.2f}", f"{bins[i+1]:.2f}", int(h[i]), f"{p[i]:.4f}"]
                     for i in range(len(h))]
        ax.set_title(title, fontsize=10)
        ax.set_yticks([])
        ax.grid(False)
    axes[0].legend(fontsize=9)
    fig.suptitle("兩個路口的車流特性差很多——各自的模型學到的東西不同，協同才有意義", y=1.04)
    save(fig, "fig_data_4_features.png")
    write_csv("feature_distribution.csv", ["路口", "特徵", "樣本", "區間下界", "區間上界", "筆數", "比例"], rows)


def main() -> int:
    setup()
    th = config.load()["label"]["thresholds"]
    th = {"danger_lt": float(th["danger_lt"]), "caution_lt": float(th["caution_lt"])}
    conf = {}
    for site in ("a", "b"):
        print(f"讀取路口 {site.upper()} 的 SSM 紀錄（路口 A 較大，約需 1–2 分鐘）…")
        conf[site] = ssm_conflicts(site)
    fig_hotspots(conf, th)
    fig_conflict_types(conf, th)
    fig_labels()
    fig_features(conf, th)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
