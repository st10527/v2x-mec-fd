"""產生公共代理資料集 proxy_set.npy（SPEC §7.1）。

聯邦蒸餾時，兩個路口不交換原始軌跡，而是各自對「同一批公共樣本」做預測，
再交換預測結果（logits）。這批公共樣本就是 proxy_set.npy。

它必須滿足三件事：
  1. 固定 2000 筆、shape (2000, 20, 8)          —— 兩端的 logits 才能逐筆對齊
  2. 三種風險等級都要有                        —— 否則對方的模型學不到「危險」長怎樣
     兩種抽法（config 的 fd.proxy.sampling）：
       natural    ：隨機抽時段，保留場景的自然比例——像一份公開的路口錄影，抽樣不需要標籤
       stratified ：依標籤三類各 1/3（舊做法），危險比例遠高於真實路口
  3. 兩端持有「位元完全相同」的副本             —— 以 SHA256 驗證，不符就拒絕該輪（SPEC §7.2）

來源：proxy_public 場景跑出來的 data/proxy.npz（中性幾何，與 A、B 兩個路口無關）。

用法：
    python -m training.make_proxy                                    # 依 config 產生正式的 proxy_set.npy
    python -m training.make_proxy --sampling natural --out data/proxy_set_natural.npy   # 試驗用，不覆蓋正式檔
"""
from __future__ import annotations

import argparse
import hashlib
import json

import numpy as np

from mec_app import config
from training import dataset as D


def pick_stratified(y: np.ndarray, n: int, rng) -> np.ndarray:
    """三類盡量平均，某類不夠就用其他類補滿。"""
    per = n // 3
    picked = []
    short = 0
    for c in (2, 1, 0):                         # 先抽稀少的類別
        idx = np.where(y == c)[0]
        want = per + (n - 3 * per if c == 0 else 0) + short
        take = min(want, len(idx))
        short = want - take
        picked.append(rng.choice(idx, size=take, replace=False))
    picked = np.concatenate(picked)
    if len(picked) < n:                         # 仍不足時從剩下的樣本補
        rest = np.setdiff1d(np.arange(len(y)), picked)
        picked = np.concatenate([picked, rng.choice(rest, size=n - len(picked), replace=False)])
    return np.sort(picked)


def pick_natural(n_total: int, n: int, rng) -> np.ndarray:
    """隨機抽時段、不看標籤——保留場景的自然比例。"""
    return np.sort(rng.choice(n_total, size=n, replace=False))


def main(argv=None) -> int:
    cfg = config.load()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sampling", choices=["natural", "stratified"],
                    default=cfg["fd"]["proxy"].get("sampling", "stratified"))
    ap.add_argument("--out", default=cfg["fd"]["proxy"]["path"])
    ap.add_argument("--sources", nargs="+", default=["proxy"],
                    help="公共場景（可多個，平均分配筆數），例如 proxy proxy_signal")
    a = ap.parse_args(argv)
    n = int(cfg["fd"]["proxy"]["n_samples"])
    out = config.path(a.out)
    rng = np.random.default_rng(int(cfg["train"]["seed"]))

    # 多個公共場景時平均分配筆數（例如無號誌與號誌中性路口各一半）
    Xs, ys, names = [], [], []
    for k, scen in enumerate(a.sources):
        src = config.path(cfg["dataset"]["out"].format(scenario=scen))
        s = D.load(src)
        want = n // len(a.sources) + (n % len(a.sources) if k == 0 else 0)
        counts = np.bincount(s.y, minlength=3)
        print(f"來源 {src.name}：{len(s):,} 筆，安全 {counts[0]:,} / 注意 {counts[1]:,} / 危險 {counts[2]:,}，抽 {want} 筆")
        if len(s) < want:
            raise SystemExit(f"來源只有 {len(s)} 筆，不足 {want} 筆。請把 {scen} 場景跑久一點。")
        idx = (pick_natural(len(s), want, rng) if a.sampling == "natural"
               else pick_stratified(s.y, want, rng))
        Xs.append(s.X[idx]); ys.append(s.y[idx]); names.append(src.name)
    Xall, yall = np.concatenate(Xs), np.concatenate(ys)

    got = np.bincount(yall, minlength=3)
    X = np.ascontiguousarray(Xall.astype(np.float32))
    # ↓ 抽完就丟掉標籤：proxy_set 只有特徵（SPEC §7.1）
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, X)

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.with_suffix(".sha256")).write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    (out.with_suffix(".json")).write_text(json.dumps({
        "shape": list(X.shape), "sha256": digest, "source": " + ".join(names),
        "sampling": a.sampling,
        "label_counts_before_drop": {"safe": int(got[0]), "caution": int(got[1]), "danger": int(got[2])},
        "note": "只含特徵，不含標籤（SPEC §7.1）。兩端必須持有位元相同的副本。",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已輸出 {out}：shape {X.shape}")
    print(f"抽樣方式 {a.sampling}：安全 {got[0]} / 注意 {got[1]} / 危險 {got[2]}（標籤已丟棄）")
    print(f"SHA256 {digest}")
    if got[2] < 100:
        print("[注意] 危險樣本少於 100 筆，蒸餾時對方模型可能學不到危險型態。"
              "建議把 proxy 場景的駕駛參數調得更激進一點再重跑。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
