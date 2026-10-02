"""每車滑動視窗（SPEC §5.3）。

每台車維護一個 deque(maxlen=20)，即過去 2.0 秒、每 0.1 秒一筆。
車輛首次出現且不足 20 步時，以第一筆狀態向前填充（edge padding）。

另含逾時淘汰：車輛離開路口後不會再送 BSM，若不淘汰，長時間執行會讓
MEC App 的記憶體隨累計車輛數單調成長。這是有狀態邊緣服務的必要管理
（SPEC §2 Q6(c) 正是以此論證 MEC 的必要性）。
"""
from __future__ import annotations

import time
from collections import deque
from typing import Iterator

import numpy as np

from . import config


class VehicleWindow:
    """單一車輛的特徵環形緩衝區。"""

    __slots__ = ("vehicle_id", "buf", "last_seen", "_dim", "_maxlen")

    def __init__(self, vehicle_id: str, maxlen: int, dim: int) -> None:
        self.vehicle_id = vehicle_id
        self.buf: deque[np.ndarray] = deque(maxlen=maxlen)
        self.last_seen = time.time()
        self._dim = dim
        self._maxlen = maxlen

    def push(self, feat: np.ndarray, now: float | None = None) -> None:
        """加入一筆已正規化的 8 維特徵。首筆進入時向前填滿整個視窗。"""
        feat = np.asarray(feat, dtype=np.float32).reshape(-1)
        if feat.size != self._dim:
            raise ValueError(f"特徵維度應為 {self._dim}，收到 {feat.size}")
        if not self.buf:
            # edge padding：首次出現即以第一筆狀態填滿，使視窗立刻可用。
            for _ in range(self._maxlen):
                self.buf.append(feat.copy())
        else:
            self.buf.append(feat)
        self.last_seen = now if now is not None else time.time()

    @property
    def ready(self) -> bool:
        return len(self.buf) == self._maxlen

    def tensor(self) -> np.ndarray:
        """回傳 shape (steps, dim) 的視窗，時間由舊到新。"""
        if not self.ready:
            raise RuntimeError(f"視窗尚未就緒：{len(self.buf)}/{self._maxlen}")
        return np.stack(self.buf, axis=0).astype(np.float32)


class WindowStore:
    """路口層級的每車視窗表。"""

    def __init__(self, ttl_s: float = 10.0, cfg: dict | None = None) -> None:
        c = cfg or config.load()
        self.maxlen = int(c["window"]["steps"])
        self.dim = int(c["features"]["dim"])
        self.ttl_s = float(ttl_s)
        self._w: dict[str, VehicleWindow] = {}

    def push(self, vehicle_id: str, feat: np.ndarray,
             now: float | None = None) -> VehicleWindow:
        w = self._w.get(vehicle_id)
        if w is None:
            w = VehicleWindow(vehicle_id, self.maxlen, self.dim)
            self._w[vehicle_id] = w
        w.push(feat, now=now)
        return w

    def get(self, vehicle_id: str) -> VehicleWindow | None:
        return self._w.get(vehicle_id)

    def evict_stale(self, now: float | None = None) -> int:
        """淘汰超過 ttl_s 未更新的車輛，回傳淘汰數量。"""
        t = now if now is not None else time.time()
        dead = [k for k, w in self._w.items() if t - w.last_seen > self.ttl_s]
        for k in dead:
            del self._w[k]
        return len(dead)

    def __len__(self) -> int:
        return len(self._w)

    def __iter__(self) -> Iterator[VehicleWindow]:
        return iter(self._w.values())


class NeighborTable:
    """路口層級的「各車最新狀態」表。

    特徵中的 rel_dist / rel_speed 需要同一時刻的他車狀態，但 BSM 是逐筆串流
    進來的（每車每 0.1 s 一筆，不同車不同步）。本表保留每台車最後一筆狀態，
    計算特徵時取 sim_time 落在容忍窗內的車作為鄰車集合。

    容忍窗預設為 1.5 個模擬步長：太小會在車輛送達順序抖動時漏掉鄰車，
    太大則會把上一個時間步的舊位置當成當下位置。
    """

    def __init__(self, tolerance_steps: float = 1.5, ttl_s: float = 10.0,
                 cfg: dict | None = None) -> None:
        c = cfg or config.load()
        self.tolerance = float(tolerance_steps) * float(c["sumo"]["step_length"])
        self.ttl_s = float(ttl_s)
        self._s: dict[str, tuple[object, float]] = {}   # vid -> (state, wall_t)

    def update(self, state, now: float | None = None) -> None:
        self._s[state.vehicle_id] = (state, now if now is not None else time.time())

    def neighbors_of(self, ego) -> list:
        """取出與 ego 同一時刻（容忍窗內）的其他車輛狀態。"""
        out = []
        for vid, (s, _) in self._s.items():
            if vid == ego.vehicle_id:
                continue
            if abs(s.sim_time - ego.sim_time) <= self.tolerance:
                out.append(s)
        return out

    def evict_stale(self, now: float | None = None) -> int:
        t = now if now is not None else time.time()
        dead = [k for k, (_, seen) in self._s.items() if t - seen > self.ttl_s]
        for k in dead:
            del self._s[k]
        return len(dead)

    def __len__(self) -> int:
        return len(self._s)
