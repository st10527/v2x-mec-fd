"""聯邦蒸餾回合驅動（SPEC §7.3、§7.4）。

每一輪照 SPEC §7.4 的七個步驟跑：

    1. 本地以 train set 訓練 5 epochs
    2. 對 proxy_set 前向，產生溫度 T 的軟標籤
    3. 向 Service Registry 查詢對方 fd-service 位址
    4. 經 MEP Gateway GET 對方 logits，驗證 proxy_hash
    5. 以 §7.3 的損失更新本地模型 5 epochs
    6. 在本地 val set 與對方 test set 上評估，寫入 log
    7. 記錄本輪傳輸 bytes

蒸餾損失（SPEC §7.3）：
    L = (1 - α)·CE(y_true, p_local) + α·T²·KL(p_peer_soft ‖ p_local_soft)
T² 係數用於平衡軟硬標籤的梯度量級（Hinton et al. 標準做法）：
軟標籤的梯度量級與 1/T² 成正比，不乘回來的話 T 一調大，KL 項就形同消失。

=== 兩點必須誠實說明（會被評審問到）===

(1) 交換的是 logits，不是原始軌跡。對方拿到的只有「我的模型對公共代理資料集
    的輸出」，而 proxy 場景是中性幾何、與兩個路口的真實車流無關。
    這是 SPEC §2 Q4 隱私論證的實際兌現。

(2) 步驟 6 的「對方 test set 評估」是**離線評估用途**，直接讀本機上對方的
    npz 檔。初賽版兩個節點跑在同一台 VM1 上，這在工程上是最省事的做法，
    但它不屬於隱私保護協定的一部分——交換協定只有步驟 2–5。
    企劃書與簡報必須把這件事講清楚，不可讓評審誤以為測試集也在網路上流動。
    決賽版雙實體節點（SPEC §13.2）時，跨節點評估改為各自回報分數。

用法：
    python -m training.distill --node a                # 經 Kong 向對方取 logits
    python -m training.distill --node a --local-peer   # 單機模擬，不走網路
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import pathlib
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as Fn

from mec_app import config, jsonlog, model as M
from mec_app.registry_client import RegistryClient
from training import dataset as D, metrics
from training.events import calibrate_model
from training.train_local import (evaluate_model, load_node_data, make_criterion,
                                  make_loader, save_atomic, seed_everything)


# ---------------------------------------------------------------------------
# 公共代理資料集
# ---------------------------------------------------------------------------
def load_proxy(cfg: dict) -> tuple[np.ndarray, str]:
    """載入 proxy_set.npy 並計算 SHA256（SPEC §7.1）。"""
    p = config.path(cfg["fd"]["proxy"]["path"])
    if not p.exists():
        raise FileNotFoundError(
            f"{p} 不存在。proxy_set.npy 由 proxy_public 場景產生（SPEC §7.1），"
            "兩端須持有位元完全相同的副本。")
    raw = p.read_bytes()
    arr = np.load(p).astype(np.float32)
    want = (cfg["fd"]["proxy"]["n_samples"], cfg["window"]["steps"],
            cfg["features"]["dim"])
    if arr.shape != want:
        raise ValueError(f"proxy_set 形狀應為 {want}，實際 {arr.shape}")
    return arr, "sha256:" + hashlib.sha256(raw).hexdigest()


@torch.no_grad()
def proxy_logits(model: nn.Module, proxy: np.ndarray,
                 batch_size: int = 512) -> np.ndarray:
    """對公共代理資料集前向，產生 logits（SPEC §7.4 步驟 2）。"""
    model.eval()
    out = []
    X = torch.from_numpy(np.ascontiguousarray(proxy))
    for i in range(0, len(X), batch_size):
        out.append(model(X[i:i + batch_size]).numpy())
    return np.concatenate(out).astype(np.float32)


# ---------------------------------------------------------------------------
# 取得對方 logits
# ---------------------------------------------------------------------------
def publish_logits(node: str, rnd: int, logits: np.ndarray, cfg: dict) -> pathlib.Path:
    """把本輪軟標籤寫到 MEC App 讀得到的位置（原子寫入，對方不會讀到寫一半的檔）。"""
    p = config.path(cfg["fd"]["published"].format(node=node, round=rnd))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.stem + ".tmp.npy")
    np.save(tmp, logits.astype(np.float32))
    os.replace(tmp, p)
    return p


def clear_published(node: str, cfg: dict) -> None:
    """移除上一次執行留下的已發布軟標籤，避免對方拿到舊的。"""
    pattern = cfg["fd"]["published"].format(node=node, round="*")
    for f in config.ROOT.glob(pattern):
        f.unlink(missing_ok=True)


def fetch_peer_logits(node: str, rnd: int, cfg: dict,
                      timeout: float = 30.0) -> tuple[np.ndarray, int, str]:
    """經 MEP Gateway 向對方取第 rnd 輪的 logits（SPEC §7.4 步驟 3–4）。

    對方還沒發布本輪（409）就等待重試，上限 fd.wait_peer_s 秒。
    回傳 (logits 陣列, 線上傳輸 bytes, 對方回報的 proxy_hash)。
    """
    import httpx
    reg = RegistryClient(node, cfg)
    base = reg.peer_fd_url()
    url = f"{base}/logits"
    print(f"  取對方第 {rnd} 輪 logits：{url}（registry 模式 = {reg.mode}）")
    wait_s, poll_s = float(cfg["fd"]["wait_peer_s"]), float(cfg["fd"]["poll_s"])
    t_start, last_note = time.time(), 0.0
    while True:
        r = httpx.get(url, params={"round": rnd}, timeout=timeout)
        if r.status_code != 409:
            break
        waited = time.time() - t_start
        if waited > wait_s:
            raise TimeoutError(f"等了 {waited:.0f} 秒，對方仍未發布第 {rnd} 輪軟標籤")
        if waited - last_note >= 30:
            print(f"  等待對方完成第 {rnd} 輪本地訓練…（已等 {waited:.0f} 秒）")
            last_note = waited
        time.sleep(poll_s)
    r.raise_for_status()
    j = r.json()
    if int(j.get("round", -1)) != rnd:
        raise RuntimeError(f"要的是第 {rnd} 輪，對方回的是第 {j.get('round')} 輪")
    raw = base64.b64decode(j["logits"])
    arr = np.frombuffer(raw, dtype=np.float32).reshape(
        j["n_samples"], j["n_classes"]).copy()
    return arr, len(j["logits"]), str(j["proxy_hash"])     # 線上大小 = base64 字串長度


def local_peer_logits(node: str, rnd: int, cfg: dict,
                      proxy: np.ndarray) -> tuple[np.ndarray, int, str]:
    """單機模擬：直接讀對方的權重檔算 logits，不走網路。

    僅供 D10–D12 之間 Kong 路由還沒通時的開發用途。
    **這不是 SPEC §3.2 要求的交換方式**，正式量測與展示必須走網路路徑，
    否則 SPEC §2 Q6(b) 的 MEC 論證與「MEC 使用程度」的得分都拿不到。
    """
    peer = config.peer_of(node)
    p = config.path(cfg["fd"]["model_swap"]["current"].format(node=peer))
    if not p.exists():
        raise FileNotFoundError(f"{p} 不存在，對方尚未完成第 0 輪預訓練。")
    m = M.build()
    m.load_state_dict(torch.load(p, map_location="cpu", weights_only=True))
    arr = proxy_logits(m, proxy)
    _, phash = load_proxy(cfg)
    return arr, arr.nbytes, phash


# ---------------------------------------------------------------------------
# 蒸餾損失（SPEC §7.3）
# ---------------------------------------------------------------------------
class DistillLoss(nn.Module):
    """L = (1-α)·CE(y_true, p_local) + α·T²·KL(p_peer_soft ‖ p_local_soft)。

    注意兩項的樣本來源不同：
      硬標籤項 CE   吃本地 train set 的 (x, y)
      軟標籤項 KL   吃公共代理資料集的 x_proxy，與對方在同一批 proxy 樣本
                    上的 logits 對齊
    所以 forward 收兩組 logits，不可把本地 batch 的輸出拿去跟 peer 對 proxy
    的輸出算 KL——那是在比兩組不同的樣本，蒸餾會變成雜訊。
    """

    def __init__(self, ce: nn.Module, alpha: float, temperature: float) -> None:
        super().__init__()
        self.ce = ce
        self.alpha = float(alpha)
        self.T = float(temperature)

    def forward(self, local_logits, y_true, local_proxy_logits, peer_proxy_logits):
        hard = self.ce(local_logits, y_true)
        # KL 的 input 需為 log 機率、target 為機率（PyTorch 慣例）
        log_p_local = Fn.log_softmax(local_proxy_logits / self.T, dim=-1)
        p_peer = Fn.softmax(peer_proxy_logits / self.T, dim=-1)
        soft = Fn.kl_div(log_p_local, p_peer, reduction="batchmean")
        # T² 平衡軟硬標籤的梯度量級：軟標籤梯度與 1/T² 成正比，
        # 不乘回來的話 T 一調大 KL 項就形同消失（Hinton et al. 標準做法）。
        loss = (1 - self.alpha) * hard + self.alpha * (self.T ** 2) * soft
        return loss, hard, soft


@contextlib.contextmanager
def frozen_bn_stats(model: nn.Module):
    """暫停 BatchNorm 的 running mean/var 更新（梯度照常回傳）。

    公共代理資料來自中性路口，分布和本地資料差很多。若讓它更新 BN 統計，
    評估與線上推論時用的是「本地 + 代理」混合後的統計，本地表現會整個崩掉
    （實測 A 本地 val macro-F1 0.75 → 0.46，且與 α、代理抽樣數無關）。
    BN 統計只應該反映本路口的車流。
    """
    bns = [m for m in model.modules() if isinstance(m, nn.modules.batchnorm._BatchNorm)]
    saved = [m.momentum for m in bns]
    for m in bns:
        m.momentum = 0.0          # running = (1-0)·running + 0·batch → 不變
    try:
        yield
    finally:
        for m, mom in zip(bns, saved):
            m.momentum = mom


def distill_epochs(model, train_split: D.Split, proxy: np.ndarray,
                   peer: np.ndarray, epochs: int, cfg: dict,
                   optimizer) -> list[dict]:
    """以蒸餾損失更新本地模型（SPEC §7.4 步驟 5）。

    每一步同時抽一個本地 batch 與一個 proxy batch，兩項損失相加後一起反傳。
    """
    crit = DistillLoss(make_criterion(train_split.y, cfg),
                       cfg["fd"]["alpha"], cfg["fd"]["temperature"])
    bs = int(cfg["train"]["batch_size"])
    loader = make_loader(train_split, bs, shuffle=True)

    Xp = torch.from_numpy(np.ascontiguousarray(proxy))
    Lp = torch.from_numpy(np.ascontiguousarray(peer))
    n_proxy = len(Xp)
    proxy_bs = min(int(cfg["fd"].get("proxy_batch", bs)), n_proxy)   # 每步抽幾筆公共資料
    g = torch.Generator().manual_seed(int(cfg["train"]["seed"]))

    history = []
    model.train()
    for ep in range(1, epochs + 1):
        tot = {"loss": 0.0, "hard": 0.0, "soft": 0.0}
        nb = 0
        for xb, yb in loader:
            idx = torch.randint(0, n_proxy, (proxy_bs,), generator=g)
            optimizer.zero_grad()
            local_out = model(xb)
            with frozen_bn_stats(model):
                proxy_out = model(Xp[idx])
            loss, hard, soft = crit(local_out, yb, proxy_out, Lp[idx])
            loss.backward()
            optimizer.step()
            tot["loss"] += float(loss.item())
            tot["hard"] += float(hard.item())
            tot["soft"] += float(soft.item())
            nb += 1
        rec = {k: v / max(nb, 1) for k, v in tot.items()}
        history.append(rec)
        print(f"    蒸餾 epoch {ep}/{epochs}  loss {rec['loss']:.4f}"
              f"  (CE {rec['hard']:.4f} / KL {rec['soft']:.4f})")
    return history


# ---------------------------------------------------------------------------
# 回合驅動
# ---------------------------------------------------------------------------
def alpha_for(node: str, cfg: dict) -> float:
    """fd.alpha 可為單一數值或 {node: α}。

    非對稱協同：每個路口自己決定吸收多少對方的知識（α = 0 表示不吸收，
    但仍照常發布自己的軟標籤給對方）。依據見 training/tune_fd.py。
    """
    a = cfg["fd"]["alpha"]
    return float(a[node] if isinstance(a, dict) else a)


def run(node: str, rounds: int | None = None, local_peer: bool = False,
        cfg: dict | None = None) -> list[dict]:
    c = json.loads(json.dumps(cfg or config.load()))
    alpha = alpha_for(node, c)
    c["fd"]["alpha"] = alpha                    # DistillLoss 讀的是本節點的 α
    seed_everything(int(c["train"]["seed"]))
    if c["train"].get("distill_threads"):
        torch.set_num_threads(int(c["train"]["distill_threads"]))
    rounds = rounds or int(c["fd"]["rounds"])
    peer = config.peer_of(node)

    tr, va, _ = load_node_data(node, c)
    _, _, peer_te = load_node_data(peer, c)      # 見檔頭 (2) 的誠實說明
    proxy, my_hash = load_proxy(c)

    model = M.build()
    cur = config.path(c["fd"]["model_swap"]["current"].format(node=node))
    if not cur.exists():
        raise FileNotFoundError(
            f"{cur} 不存在。請先執行 `python -m training.train_local --node {node}` "
            "完成第 0 輪單獨訓練基準（SPEC §7.4）。")
    model.load_state_dict(torch.load(cur, map_location="cpu", weights_only=True))

    opt = torch.optim.Adam(model.parameters(),
                           lr=float(c["train"].get("lr_round", c["train"]["lr"])))
    fd_log = jsonlog.JsonlWriter("fd_rounds", cfg=c)
    clear_published(node, c)
    results = []

    for rnd in range(1, rounds + 1):
        t0 = time.time()
        print(f"\n=== 節點 {node.upper()} 第 {rnd}/{rounds} 輪 ===")

        # 1) 本地訓練
        from training.train_local import train_epochs
        train_epochs(model, make_loader(tr, int(c["train"]["batch_size"]), True),
                     int(c["train"]["epochs_per_round"]),
                     make_criterion(tr.y, c), opt, log_prefix=f"  本地")

        # 2) 產生自己的軟標籤並發布，對方經 MEC App 的 /logits?round=rnd 取用
        mine = proxy_logits(model, proxy)
        publish_logits(node, rnd, mine, c)

        # 3-4) 取對方 logits 並驗證 proxy_hash
        #      α = 0 的節點不吸收對方知識，也就不必取（蒸餾項權重為 0，對方 logits 不影響結果）
        if alpha == 0:
            peer_logits, wire_bytes, peer_hash = np.zeros_like(mine), 0, my_hash
            print("  本節點 α = 0：只提供軟標籤給對方，不吸收對方知識")
        elif local_peer:
            peer_logits, wire_bytes, peer_hash = local_peer_logits(node, rnd, c, proxy)
        else:
            peer_logits, wire_bytes, peer_hash = fetch_peer_logits(node, rnd, c)
        if peer_hash != my_hash:
            raise RuntimeError(
                f"第 {rnd} 輪拒絕：對方 proxy_hash {peer_hash} 與本地 {my_hash} 不符。"
                "兩端必須持有位元完全相同的 proxy_set.npy（SPEC §7.2）。")

        # 5) 蒸餾更新
        distill_epochs(model, tr, proxy, peer_logits,
                       int(c["train"]["epochs_distill"]), c, opt)

        # 6) 以本路口驗證資料校準兩段式警示門檻（SPEC §6.4），再評估
        tau = calibrate_model(model, node, c)
        val_rep = evaluate_model(model, va, c, tau)
        cross_rep = evaluate_model(model, peer_te, c, tau)
        dur = time.time() - t0

        # 7) 紀錄（SPEC §8.4 的 fd_rounds.jsonl）
        fd_log.write(round=rnd, node=node, bytes_sent=int(mine.nbytes),
                     local_f1=round(val_rep["macro_f1"], 5),
                     cross_f1=round(cross_rep["macro_f1"], 5),
                     duration_s=round(dur, 3))
        M.save_threshold(node, [f"round_{rnd}", "current"], tau)   # 先寫門檻再換權重
        save_atomic(model, config.path(f"models/{node}/round_{rnd}.pt"))
        save_atomic(model, cur)          # 熱替換：推論端會偵測到 mtime 改變

        print(f"  本地 val macro-F1 {val_rep['macro_f1']:.4f}"
              f"   跨路口({node.upper()}→{peer.upper()}) macro-F1 {cross_rep['macro_f1']:.4f}"
              f"   危險 recall {cross_rep['danger_recall']:.4f}   門檻 τ {tau}")
        print(f"  本輪傳輸：原始 {mine.nbytes:,} B"
              f"（線上 {wire_bytes:,} B）  耗時 {dur:.1f}s")
        results.append({"round": rnd, "danger_threshold": tau, "val": val_rep, "cross": cross_rep,
                        "raw_bytes": int(mine.nbytes), "wire_bytes": wire_bytes,
                        "duration_s": dur})

    out = config.path(f"models/{node}/distill_summary.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n摘要已寫入 {out}")
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", required=True, choices=["a", "b"])
    ap.add_argument("--rounds", type=int, default=None)
    ap.add_argument("--local-peer", action="store_true",
                    help="單機模擬，直接讀對方權重而不走 Kong。開發用，"
                         "正式量測與展示必須走網路路徑（SPEC §3.2）。")
    a = ap.parse_args(argv)
    run(a.node, a.rounds, a.local_peer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
