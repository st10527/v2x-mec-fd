"""設定載入。

config.yaml 是 docs/SPEC.md 所有數值參數的機器可讀形式，也是特徵正規化參數的
唯一來源（SPEC §15.5：兩端共用，不得各自用自己的資料統計）。
"""
from __future__ import annotations

import functools
import pathlib
from typing import Any

import numpy as np
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"


@functools.lru_cache(maxsize=4)
def load(path: str | None = None) -> dict[str, Any]:
    """載入並快取 config.yaml。"""
    p = pathlib.Path(path) if path else CONFIG_PATH
    with open(p, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def node(name: str) -> dict[str, Any]:
    """取得節點設定；name 為 'a' / 'b'（大小寫不拘）。"""
    cfg = load()
    key = str(name).lower()
    if key not in cfg["nodes"]:
        raise KeyError(f"未知節點 {name!r}，可用：{sorted(cfg['nodes'])}")
    return cfg["nodes"][key]


def peer_of(name: str) -> str:
    """回傳對側節點代號。本專案固定為兩節點（SPEC §3.2）。"""
    keys = sorted(load()["nodes"])
    if len(keys) != 2:
        raise RuntimeError(f"peer_of 假設兩節點，實際 {len(keys)} 個")
    cur = str(name).lower()
    return keys[1] if cur == keys[0] else keys[0]


@functools.lru_cache(maxsize=1)
def norm_bounds() -> tuple[np.ndarray, np.ndarray]:
    """回傳特徵正規化的 (lo, hi) 向量，順序同 features.order。"""
    f = load()["features"]
    lo = np.array([f["norm"][k]["min"] for k in f["order"]], dtype=np.float32)
    hi = np.array([f["norm"][k]["max"] for k in f["order"]], dtype=np.float32)
    if np.any(hi <= lo):
        bad = [k for k, a, b in zip(f["order"], lo, hi) if b <= a]
        raise ValueError(f"config.yaml 正規化值域非法（max <= min）：{bad}")
    return lo, hi


def path(*parts: str) -> pathlib.Path:
    """組出以專案根目錄為基準的路徑。"""
    return ROOT.joinpath(*parts)
