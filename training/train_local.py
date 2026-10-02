"""第 0 輪本地預訓練（SPEC §6.2、§7.4）。

第 0 輪是「單獨訓練」基準：只用自己路口的資料，不做任何交換。
SPEC §7.4 明訂「必須完整保留其模型與評估結果」——它是整份成果的對照組，
收斂曲線上那條水平虛線就是它。**不可在後續輪次覆蓋 round_0.pt。**

本模組同時提供共用的訓練迴圈與評估函式，distill.py 直接匯入使用，
確保兩邊的訓練條件（優化器、類別權重、batch 組法）完全一致——
否則蒸餾帶來的增益會混進「訓練設定不同」這個干擾因素，論證就不乾淨了。

用法：
    python -m training.train_local --node a
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from mec_app import config, jsonlog, model as M
from training import dataset as D, metrics


def seed_everything(seed: int) -> None:
    """固定隨機種子。決賽要繳完整程式碼供評審辨識原創性，
    數字重現不出來會很難解釋。"""
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


def make_loader(split: D.Split, batch_size: int, shuffle: bool) -> DataLoader:
    """組 DataLoader。

    drop_last 在訓練時開啟：模型含 BatchNorm1d，batch 只剩 1 筆時
    會直接報錯。資料量小於一個 batch 時退回不丟棄並縮小 batch。
    """
    ds = TensorDataset(torch.from_numpy(np.ascontiguousarray(split.X)),
                       torch.from_numpy(np.ascontiguousarray(split.y)))
    bs = min(batch_size, max(len(split), 2))
    drop = shuffle and len(split) > bs
    return DataLoader(ds, batch_size=bs, shuffle=shuffle, drop_last=drop)


def make_criterion(y: np.ndarray, cfg: dict) -> nn.CrossEntropyLoss:
    """交叉熵 + inverse frequency 類別權重（SPEC §6.2）。"""
    w = torch.from_numpy(D.class_weights(y, cfg["model"]["num_classes"]))
    return nn.CrossEntropyLoss(weight=w)


def train_epochs(model: nn.Module, loader: DataLoader, epochs: int,
                 criterion: nn.Module, optimizer: torch.optim.Optimizer,
                 log_prefix: str = "") -> list[float]:
    """標準訓練迴圈，回傳每個 epoch 的平均損失。"""
    model.train()
    history = []
    for ep in range(1, epochs + 1):
        total, n = 0.0, 0
        for xb, yb in loader:
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()
            total += float(loss.item()) * len(yb)
            n += len(yb)
        avg = total / max(n, 1)
        history.append(avg)
        if log_prefix and (ep % 5 == 0 or ep == epochs):
            print(f"  {log_prefix} epoch {ep:>3}/{epochs}  loss {avg:.4f}")
    return history


@torch.no_grad()
def predict(model: nn.Module, split: D.Split, batch_size: int = 512,
            tau: float | None = None) -> np.ndarray:
    """tau = 危險警告門檻（training/decision.py）；None 時取機率最高的類別。"""
    model.eval()
    out = []
    X = torch.from_numpy(np.ascontiguousarray(split.X))
    for i in range(0, len(X), batch_size):
        p = torch.softmax(model(X[i:i + batch_size]), dim=-1).numpy()
        out.append(M.decide(p, tau))
    return np.concatenate(out) if out else np.empty(0, dtype=np.int64)


def evaluate_model(model: nn.Module, split: D.Split, cfg: dict,
                   tau: float | None = None) -> dict:
    """回傳 metrics.report 的完整結果（含 macro-F1 與危險類 recall）。"""
    return metrics.report(split.y, predict(model, split, tau=tau),
                          cfg["model"]["num_classes"],
                          {int(k): v for k, v in cfg["label"]["names"].items()})


def load_node_data(node: str, cfg: dict) -> tuple[D.Split, D.Split, D.Split]:
    scen = config.node(node)["scenario"]
    path = config.path(cfg["dataset"]["out"].format(scenario=scen))
    return D.temporal_split(D.load(path), cfg)


def save_atomic(model: nn.Module, path) -> None:
    """原子性存檔（SPEC §7.5）。

    先寫 .tmp 再 os.replace，避免推論端在寫到一半時載入半個檔案。
    MEC App 是持續運作的，熱替換與存檔必然會撞在一起。
    """
    import os
    import pathlib
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    torch.save(model.state_dict(), tmp)
    os.replace(tmp, p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", required=True, choices=["a", "b"])
    ap.add_argument("--epochs", type=int, default=None,
                    help="預設取 config 的 train.epochs_pretrain（30）")
    a = ap.parse_args(argv)

    cfg = config.load()
    seed_everything(int(cfg["train"]["seed"]))
    node = a.node
    epochs = a.epochs or int(cfg["train"]["epochs_pretrain"])

    tr, va, te = load_node_data(node, cfg)
    print(f"節點 {node.upper()}  train {len(tr)} / val {len(va)} / test {len(te)}")
    print(f"train 類別分布 {tr.counts().tolist()}"
          f"   類別權重 {np.round(D.class_weights(tr.y), 3).tolist()}")

    model = M.build()
    opt = torch.optim.Adam(model.parameters(), lr=float(cfg["train"]["lr"]))
    crit = make_criterion(tr.y, cfg)
    loader = make_loader(tr, int(cfg["train"]["batch_size"]), shuffle=True)

    t0 = time.time()
    train_epochs(model, loader, epochs, crit, opt, log_prefix=f"[{node}]")
    dur = time.time() - t0

    # 上線前以本路口驗證資料校準兩段式警示門檻（SPEC §6.4，training/events.py）
    from training.events import calibrate_model
    tau = calibrate_model(model, node, cfg)
    print(f"\n警示門檻：注意 {tau['caution']}、危險 {tau['danger']}")
    val_rep = evaluate_model(model, va, cfg, tau)
    test_rep = evaluate_model(model, te, cfg, tau)
    print(f"\n本地 val\n{metrics.format_report(val_rep)}")
    print(f"\n本地 test\n{metrics.format_report(test_rep)}")

    # round_0.pt 是單獨訓練基準，永不覆蓋；current.pt 是推論端載入的權重。
    r0 = config.path(cfg["fd"]["model_swap"]["round0"].format(node=node))
    cur = config.path(cfg["fd"]["model_swap"]["current"].format(node=node))
    M.save_threshold(node, ["round_0", "current"], tau)       # 先寫門檻，推論端換權重時才對得上
    save_atomic(model, r0)
    save_atomic(model, cur)
    print(f"\n已存 {r0}\n已存 {cur}")

    # 對方 test set 上的分數 —— 這是收斂曲線上那條「單獨訓練」水平虛線。
    # 對方資料尚未建好時記為 null，不讓整支腳本失敗。
    cross_rep = None
    try:
        peer = config.peer_of(node)
        _, _, peer_te = load_node_data(peer, cfg)
        cross_rep = evaluate_model(model, peer_te, cfg, tau)
        print(f"\n跨路口 {node.upper()}→{peer.upper()}\n"
              f"{metrics.format_report(cross_rep)}")
    except FileNotFoundError:
        print(f"\n（對方資料集尚未建立，跨路口分數暫記為 null）")

    # 第 0 輪也要進 fd_rounds.jsonl：SPEC §8.3 的收斂曲線 x 軸從 0 開始，
    # 而 SPEC §8.4 規定畫圖腳本只讀 logs/ 下的 jsonl，基準線不能只存在
    # models/*.json 裡。bytes_sent=0 如實反映第 0 輪沒有任何交換。
    jsonlog.JsonlWriter("fd_rounds", cfg=cfg).write(
        round=0, node=node, bytes_sent=0,
        local_f1=round(val_rep["macro_f1"], 5),
        cross_f1=round(cross_rep["macro_f1"], 5) if cross_rep else None,
        duration_s=round(dur, 3))

    (r0.with_suffix(".json")).write_text(json.dumps({
        "node": node, "round": 0, "epochs": epochs,
        "duration_s": round(dur, 2),
        "n_train": len(tr), "n_val": len(va), "n_test": len(te),
        "val": val_rep, "test": test_rep, "cross": cross_rep,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
