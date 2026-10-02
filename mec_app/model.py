"""1D-CNN 風險預測模型（SPEC §6.1）。

輸入 (20, 8) —— 過去 2 秒、每 0.1 秒一筆、每筆 8 維特徵。
輸出 3 類 logits —— {0 安全, 1 注意, 2 危險}。

=== 不得改為兩層 MLP（SPEC §15.3、§2 Q5 的設計警示）===
Q5 的論證是「logits 交換比傳模型參數省」。模型若縮小到兩層 MLP
（約 1.4k 參數 ≈ 5.8 KB），FedAvg 的傳輸量反而低於 logits 的 24 KB，
整條論證反轉，題目不再成立。本結構 48,419 參數 / 189 KB，
相對 logits 省 8.1 倍，論證安全。
"""
from __future__ import annotations

import json

import numpy as np
import torch
import torch.nn as nn

from . import config


class RiskCNN(nn.Module):
    """SPEC §6.1 的結構，超參一律取自 config.yaml。"""

    def __init__(self, cfg: dict | None = None) -> None:
        super().__init__()
        c = (cfg or config.load())["model"]
        steps, dim = c["input_shape"]
        conv = c["conv"]
        pool = int(c["pool"])

        self.net = nn.Sequential(
            nn.Conv1d(dim, conv[0]["out"], conv[0]["kernel"],
                      padding=conv[0]["padding"]),
            nn.ReLU(),
            nn.BatchNorm1d(conv[0]["out"]),
            nn.Conv1d(conv[0]["out"], conv[1]["out"], conv[1]["kernel"],
                      padding=conv[1]["padding"]),
            nn.ReLU(),
            nn.BatchNorm1d(conv[1]["out"]),
            nn.MaxPool1d(pool),
            nn.Flatten(),
            nn.Linear(conv[1]["out"] * (steps // pool), c["fc_hidden"]),
            nn.ReLU(),
            nn.Dropout(c["dropout"]),
            nn.Linear(c["fc_hidden"], c["num_classes"]),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B, steps, dim) —— 與資料集的存放順序一致，轉置在模型內完成，
        避免呼叫端各自轉置而搞錯維度。"""
        if x.dim() != 3:
            raise ValueError(f"輸入應為 (B, steps, dim)，收到 {tuple(x.shape)}")
        return self.net(x.transpose(1, 2))


def build(cfg: dict | None = None, *, verify: bool = True) -> RiskCNN:
    """建立模型，並核對參數量與 config / SPEC 記載一致。

    參數量是 SPEC §2 Q5 論證的直接依據，也會被寫進企劃書，
    因此在建模時就守住，避免有人改了結構卻忘了改文件。
    """
    c = cfg or config.load()
    m = RiskCNN(c)
    if verify:
        n = param_count(m)
        expect = int(c["model"]["param_count"])
        if n != expect:
            raise RuntimeError(
                f"參數量 {n} 與 config.yaml 的 {expect} 不符。"
                "若確為刻意變更結構，請一併更新 config.yaml 的 "
                "model.param_count / fp32_bytes、docs/SPEC.md §2 Q5 與 §6.1 "
                "的傳輸量倍數，並在 SPEC §16 留下修訂紀錄。")
    return m


# ---------------------------------------------------------------------------
# 警告決策：危險機率 >= 門檻才喊危險（門檻跟著權重走，見 training/decision.py）
# ---------------------------------------------------------------------------
def decide(probs, tau) -> np.ndarray:
    """probs: (N, 3) 機率。回傳 0 安全 / 1 注意 / 2 危險。

    tau 的三種形式：
      None                          取機率最高的類別（未校準的權重）
      {"caution": x, "danger": y}   兩段式警示（SPEC §6.4）：危險機率 >= y → 危險（強烈警報）；
                                    >= x → 注意（溫和提示）；其餘安全。門檻由 training/events.py 校準
      float                         舊版單一門檻（危險機率 >= tau → 危險）
    """
    p = np.asarray(probs, dtype=np.float32)
    if tau is None:
        return p.argmax(-1)
    if isinstance(tau, dict):
        out = np.zeros(len(p), dtype=np.int64)
        out[p[:, 2] >= float(tau["caution"])] = 1
        out[p[:, 2] >= float(tau["danger"])] = 2
        return out
    out = p[:, :2].argmax(-1)
    out[p[:, 2] >= tau] = 2
    return out


def thresholds_path(node: str):
    return config.path(f"models/{node}/thresholds.json")


def load_threshold(node: str, weights_stem: str):
    """查某一版權重（例如 round_0、round_5、current）校準過的門檻；沒校準過回 None。

    兩段式為 {"caution": x, "danger": y}；舊版單一門檻為 float。
    """
    p = thresholds_path(node)
    if not p.exists():
        return None
    v = json.loads(p.read_text(encoding="utf-8")).get(weights_stem)
    if v is None or isinstance(v, dict):
        return v
    return float(v)


def save_threshold(node: str, weights_stems: list[str], tau) -> None:
    p = thresholds_path(node)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    val = ({k: round(float(v), 4) for k, v in tau.items()} if isinstance(tau, dict)
           else round(float(tau), 4))
    for s in weights_stems:
        d[s] = val
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def param_count(m: nn.Module) -> int:
    return sum(p.numel() for p in m.parameters())


def fp32_bytes(m: nn.Module) -> int:
    """模型以 fp32 傳輸的位元組數，用於 SPEC §8.3 的傳輸量對比圖。"""
    return param_count(m) * 4


def soft_targets(logits: torch.Tensor, temperature: float) -> torch.Tensor:
    """溫度 T 下的軟標籤（SPEC §7.3）。"""
    return torch.softmax(logits / float(temperature), dim=-1)
