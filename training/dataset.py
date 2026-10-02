"""資料集的存取、切分與類別權重（SPEC §6.2）。

npz 內容：
    X      float32 (N, 20, 8)   已正規化的特徵視窗
    y      int64   (N,)         標籤 {0,1,2}
    t      float32 (N,)         樣本的模擬時間，供時間切分使用
    vid    <U16    (N,)         車輛 id，供事件層級分析（lead time）使用
"""
from __future__ import annotations

import pathlib

import numpy as np

from mec_app import config


class Split:
    __slots__ = ("X", "y", "t", "vid", "name")

    def __init__(self, X, y, t, vid, name=""):
        self.X, self.y, self.t, self.vid, self.name = X, y, t, vid, name

    def __len__(self) -> int:
        return len(self.y)

    def counts(self, n_classes: int = 3) -> np.ndarray:
        return np.bincount(self.y, minlength=n_classes)

    def __repr__(self) -> str:
        return f"<Split {self.name} n={len(self)} 類別分布={self.counts().tolist()}>"


def save(path, X, y, t, vid) -> pathlib.Path:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        p,
        X=np.asarray(X, dtype=np.float32), y=np.asarray(y, dtype=np.int64),
        t=np.asarray(t, dtype=np.float32), vid=np.asarray(vid, dtype="<U16"))
    return p


def load(path) -> Split:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} 不存在。資料集由 training/build_dataset.py 從 SUMO 輸出產生。")
    d = np.load(p, allow_pickle=False)
    return Split(d["X"], d["y"], d["t"], d["vid"], name=p.stem)


def temporal_split(s: Split, cfg: dict | None = None) -> tuple[Split, Split, Split]:
    """依**模擬時間**切 70/15/15（SPEC §6.2）。

    不可隨機切。0.1 秒的相鄰時間步幾乎是同一筆資料，隨機切會讓同一個危險
    事件的前後時刻分別落進訓練集與測試集，測試分數會虛高到沒有意義。
    """
    c = (cfg or config.load())["train"]
    if c["split_mode"] != "temporal":
        raise ValueError(
            f"split_mode 為 {c['split_mode']!r}。SPEC §6.2 要求依時間切分，"
            "不得隨機切——相鄰時間步會造成訓練/測試洩漏。")
    order = np.argsort(s.t, kind="stable")
    n = len(order)
    n_tr = int(n * c["split"]["train"])
    n_va = int(n * c["split"]["val"])
    cuts = {"train": order[:n_tr],
            "val": order[n_tr:n_tr + n_va],
            "test": order[n_tr + n_va:]}
    return tuple(
        Split(s.X[i], s.y[i], s.t[i], s.vid[i], name=f"{s.name}.{k}")
        for k, i in cuts.items())


def class_weights(y: np.ndarray, n_classes: int = 3) -> np.ndarray:
    """inverse frequency 類別權重（SPEC §6.2）。

    危險樣本佔比可能低於 1%，不補償的話模型全預測「安全」就有 99% 準確率，
    而 danger recall = 0——正是本專案最不能接受的失敗模式。
    """
    cnt = np.bincount(np.asarray(y, dtype=np.int64), minlength=n_classes)
    w = np.where(cnt > 0, len(y) / (n_classes * np.maximum(cnt, 1)), 0.0)
    return w.astype(np.float32)


def feature_ranges(X_raw: np.ndarray, cfg: dict | None = None) -> dict:
    """統計原始特徵的實際值域與截斷比例。

    config.yaml 的 features.norm 標了 [待驗收]：值域是依常識設的初值，
    D7 產出資料集後必須用本函式確認截斷比例 < 1%，否則危險樣本
    （本來就多落在值域邊緣）會被系統性壓扁到邊界值。
    """
    c = cfg or config.load()
    lo, hi = config.norm_bounds()
    order = c["features"]["order"]
    X_raw = np.asarray(X_raw, dtype=np.float32).reshape(-1, len(order))
    out = {}
    for i, name in enumerate(order):
        col = X_raw[:, i]
        clipped = int(np.sum((col < lo[i]) | (col > hi[i])))
        out[name] = {
            "config_min": float(lo[i]), "config_max": float(hi[i]),
            "actual_min": float(col.min()), "actual_max": float(col.max()),
            "p01": float(np.percentile(col, 1)),
            "p99": float(np.percentile(col, 99)),
            "clipped": clipped,
            "clipped_pct": round(100.0 * clipped / max(len(col), 1), 4),
        }
    return out
