"""評估指標（SPEC §8.1）。

主指標是**跨路口 macro-F1**：A 的模型在 B 的 test set 上，反之亦然。
用 macro 而非 micro 是關鍵——危險樣本本來就稀少（SPEC §4.3 刻意把兩個路口
的危險型態做成偏斜），micro-F1 會被「安全」這一類淹沒，看不出協同的增益。

以 numpy 自行實作而不依賴 scikit-learn：計算本身只有十幾行，而 VM1 上已經
有 free5GC 與 gtp5g 等相依要顧，少一個套件少一個部署變數。
"""
from __future__ import annotations

import numpy as np


def confusion(y_true: np.ndarray, y_pred: np.ndarray,
              n_classes: int = 3) -> np.ndarray:
    """列為真實類別、行為預測類別。"""
    y_true = np.asarray(y_true, dtype=np.int64).ravel()
    y_pred = np.asarray(y_pred, dtype=np.int64).ravel()
    if y_true.shape != y_pred.shape:
        raise ValueError(f"長度不一致：{y_true.shape} vs {y_pred.shape}")
    cm = np.zeros((n_classes, n_classes), dtype=np.int64)
    np.add.at(cm, (y_true, y_pred), 1)
    return cm


def per_class(y_true: np.ndarray, y_pred: np.ndarray,
              n_classes: int = 3) -> dict[str, np.ndarray]:
    """各類別的 precision / recall / f1 / support。

    分母為 0 時該項記為 0（而非 nan）：某一輪若模型完全不預測「危險」，
    我們要看到 recall=0 這個事實，不是一個 nan 把它藏起來。
    """
    cm = confusion(y_true, y_pred, n_classes)
    tp = np.diag(cm).astype(np.float64)
    pred_sum = cm.sum(axis=0).astype(np.float64)
    true_sum = cm.sum(axis=1).astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        prec = np.where(pred_sum > 0, tp / pred_sum, 0.0)
        rec = np.where(true_sum > 0, tp / true_sum, 0.0)
        f1 = np.where(prec + rec > 0, 2 * prec * rec / (prec + rec), 0.0)
    return {"precision": prec, "recall": rec, "f1": f1,
            "support": true_sum.astype(np.int64), "confusion": cm}


def macro_f1(y_true: np.ndarray, y_pred: np.ndarray, n_classes: int = 3) -> float:
    """SPEC §8.1 的主指標。各類別等權，不依樣本數加權。"""
    return float(per_class(y_true, y_pred, n_classes)["f1"].mean())


def report(y_true: np.ndarray, y_pred: np.ndarray, n_classes: int = 3,
           names: dict[int, str] | None = None) -> dict:
    """彙整成可直接寫進 log 的 dict。"""
    m = per_class(y_true, y_pred, n_classes)
    names = names or {0: "safe", 1: "caution", 2: "danger"}
    return {
        "macro_f1": float(m["f1"].mean()),
        "accuracy": float(np.mean(np.asarray(y_true).ravel()
                                  == np.asarray(y_pred).ravel())),
        "per_class": {
            names.get(i, str(i)): {
                "precision": round(float(m["precision"][i]), 4),
                "recall": round(float(m["recall"][i]), 4),
                "f1": round(float(m["f1"][i]), 4),
                "support": int(m["support"][i]),
            } for i in range(n_classes)
        },
        # 危險類 recall 是次要指標裡最重要的一項（SPEC §8.1）：
        # 漏報一次危險的代價遠高於誤報一次。
        "danger_recall": round(float(m["recall"][n_classes - 1]), 4),
        "confusion": m["confusion"].tolist(),
    }


def format_report(rep: dict) -> str:
    lines = [f"macro-F1 {rep['macro_f1']:.4f}   accuracy {rep['accuracy']:.4f}"
             f"   danger-recall {rep['danger_recall']:.4f}",
             f"{'class':<10}{'prec':>8}{'recall':>8}{'f1':>8}{'support':>9}"]
    for name, v in rep["per_class"].items():
        lines.append(f"{name:<10}{v['precision']:>8.4f}{v['recall']:>8.4f}"
                     f"{v['f1']:>8.4f}{v['support']:>9d}")
    lines.append("confusion (列=真實, 行=預測): " + str(rep["confusion"]))
    return "\n".join(lines)
