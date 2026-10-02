"""警告門檻的校準：漏報與誤報的實務取捨。

真實的預警系統要在兩種錯誤之間取捨：
  漏報：危險來了沒喊——可能就是一場事故
  誤報：沒事卻喊危險——駕駛被打擾，喊多了就不再理會警告
成本 = R × 漏報數 + 誤報數，R = 漏報一次抵幾次誤報（config 的 decision.miss_cost_ratio）。

每產生一版模型（round 0、每一輪蒸餾後），就用**本路口的驗證資料**找出成本最低的門檻 τ：
模型認為危險的機率 >= τ 才喊危險。這對應實際部署時「新模型上線前先做本地校準」，
只用自己路口的資料，測試資料不碰。

用法（替既有權重補校準）：
    python -m training.decision --node a --weights round_0 --also-current
"""
from __future__ import annotations

import argparse

import numpy as np
import torch

from mec_app import config, model as M

TAU_GRID = np.round(np.arange(0.05, 0.951, 0.05), 2)     # 下限要夠低：漏報很貴時門檻可能低於 0.3


def counts(y: np.ndarray, p: np.ndarray, tau: float) -> tuple[int, int, int]:
    """(漏報, 誤報, 真危險數)。喊危險 = 危險機率 >= tau。"""
    warn = p[:, 2] >= tau
    danger = y == 2
    return int((danger & ~warn).sum()), int((~danger & warn).sum()), int(danger.sum())


def cost_per_1000(y, p, tau, R) -> float:
    fn, fp, _ = counts(y, p, tau)
    return 1000.0 * (R * fn + fp) / len(y)


def best_tau(y, p, R) -> float:
    """成本最低的門檻；同分時取較高者（少喊一點）。"""
    return float(min(TAU_GRID, key=lambda t: (cost_per_1000(y, p, t, R), -t)))


@torch.no_grad()
def probs(model, split, batch_size: int = 2048) -> np.ndarray:
    model.eval()
    X = torch.from_numpy(np.ascontiguousarray(split.X))
    out = [torch.softmax(model(X[i:i + batch_size]), -1).numpy()
           for i in range(0, len(X), batch_size)]
    return np.concatenate(out).astype(np.float32)


def calibrate(model, val_split, cfg: dict) -> float:
    R = float(cfg["decision"]["miss_cost_ratio"])
    return best_tau(val_split.y, probs(model, val_split), R)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", required=True, choices=["a", "b"])
    ap.add_argument("--weights", required=True, help="models/{node}/ 下的權重檔名（不含 .pt）")
    ap.add_argument("--also-current", action="store_true",
                    help="current.pt 就是這一版時一併登記")
    a = ap.parse_args(argv)
    cfg = config.load()
    from training.train_local import load_node_data
    _, va, _ = load_node_data(a.node, cfg)
    m = M.build()
    m.load_state_dict(torch.load(config.path(f"models/{a.node}/{a.weights}.pt"),
                                 map_location="cpu", weights_only=True))
    tau = calibrate(m, va, cfg)
    stems = [a.weights] + (["current"] if a.also_current else [])
    M.save_threshold(a.node, stems, tau)
    print(f"節點 {a.node.upper()} {a.weights}：危險門檻 τ = {tau}（R = {cfg['decision']['miss_cost_ratio']}）"
          f" → {M.thresholds_path(a.node)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
