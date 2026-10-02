"""特徵計算與正規化（SPEC §5.2）。

**本模組同時被兩條路徑使用：**
  1. mec_app/app.py       — 即時推論時從收到的 BSM 計算特徵
  2. training/build_dataset.py — 離線從 SUMO 輸出重建同樣的特徵

兩邊必須得到位元等價的結果，否則模型訓練時看到的輸入分布與上線時不同。
SPEC §9 明訂本檔「兩端共用，勿分岔」——修改前先確認兩條路徑都還對。

特徵在 MEC 端計算而非 UE 端（SPEC §5.2），理由有二：降低上行負載，
以及讓 MEC 的運算角色在架構圖上明確。

---------------------------------------------------------------------------
規格未定死、由本實作補齊的三個細節（如需變更請同步更新 SPEC）：

(1) 「最近衝突車輛」的選法
    SPEC §5.2 只說「最近衝突車輛」，未定義衝突判準。本實作：
      候選 = 同路口、距離 <= rel_dist 上限（100 m）的其他車輛，
             且「會撞得到」：同一條車道，或照目前方向與速度開下去，
             未來 3 秒內兩車最近距離 <= 2.5 公尺（config: features.conflict_cpa_m）
      優先取「接近中」（closing_speed > 0）者裡距離最小的；
      若無任何車接近中，退而取距離最小者（此時 rel_speed <= 0）。

    「會撞得到」這一條是 v1.4 加的（SPEC §16 待確認事項 #5）。沒有它的時候，
    對向車道迎面而來的車（兩車接近速度 28 m/s 以上）會被選成衝突對手——
    但它們各走各的車道，錯身而過，根本不會相撞。實測路口 B 的 rel_speed
    因此有 47% 超出值域。對應真實情境的說法很簡單：
      對向車道錯車、隔壁車道並行 → 不是威脅
      同車道前車、路口橫向來車、支道匯入車 → 是威脅
    同車道一律保留，是因為等速跟車時兩車「最近距離」就是車距本身，
    但前車一急煞就會追撞，不能排除。

(2) heading_diff 的定義
    SPEC 只說「航向夾角，正規化至 [-1, 1]」。本實作取 cos(Δψ)：
      +1 = 同向、0 = 正交、-1 = 對向。
    取 cos 而非角度線性映射，是因為它在 ψ=0/360 交界處連續，
    不會讓模型在同一個物理狀態上看到兩個極端值。

(3) dist_to_stopline 的來源
    BSM（SPEC §5.1）不含停止線距離，須由 MEC 端以路網幾何換算。
    本實作讀 sumo/intersection_{x}/stoplines.json（lane_id -> [x, y]），
    該檔由 D5–D6 建場景時以 netconvert 輸出產生。檔案不存在時，
    本特徵退化為上限值並在啟動時警告一次，不中斷服務。
---------------------------------------------------------------------------
"""
from __future__ import annotations

import json
import math
import pathlib
import warnings
from dataclasses import dataclass

import numpy as np

from . import config

# SUMO 號誌相位字元 -> signal_state（SPEC §5.2 第 8 項）
# 'o'/'O'（off）與未知字元一律視為不受號誌限制，與無號誌路口一致。
_SIGNAL_MAP = {
    "G": 1.0, "g": 1.0,
    "y": 0.5, "Y": 0.5,
    "r": 0.0, "R": 0.0,
}
_EPS = 1e-6


@dataclass(slots=True)
class VehicleState:
    """單一時間步的車輛狀態，欄位對應 SPEC §5.1 的 BSM。"""

    vehicle_id: str
    x: float
    y: float
    speed: float           # m/s
    accel: float           # m/s^2
    heading: float         # deg，SUMO 慣例：0 = 正北，順時針遞增
    lane_id: str
    signal_phase: str = "G"
    sim_time: float = 0.0
    # 車長（公尺）。SAE J2735 BSM 的 VehicleSize 欄位；規則基準線算保險桿間距要用。
    # 預設 5.0 與平台範例相同，讓沒帶車長的舊紀錄仍可讀。
    length: float = 5.0

    @classmethod
    def from_bsm(cls, msg: dict) -> "VehicleState":
        pos = msg["position"]
        return cls(
            vehicle_id=msg["vehicle_id"],
            x=float(pos["x"]), y=float(pos["y"]),
            speed=float(msg["speed"]), accel=float(msg["accel"]),
            heading=float(msg["heading"]), lane_id=str(msg["lane_id"]),
            signal_phase=str(msg.get("signal_phase", "G")),
            sim_time=float(msg.get("sim_time", 0.0)),
            length=float(msg.get("length", 5.0)),
        )

    def velocity(self) -> tuple[float, float]:
        """速度向量。SUMO heading 以正北為 0、順時針為正。"""
        rad = math.radians(self.heading)
        return self.speed * math.sin(rad), self.speed * math.cos(rad)


@dataclass(slots=True)
class Conflict:
    """選定的衝突對手與其幾何關係，供規則基準線與 log 共用。"""

    partner_id: str | None
    rel_dist: float
    closing_speed: float       # 接近為正
    heading_cos: float         # cos(Δψ)
    same_lane: float           # 0.0 / 1.0


class Geometry:
    """路口幾何：lane_id -> 停止線座標。見本檔頭註 (3)。"""

    def __init__(self, stoplines: dict[str, tuple[float, float]] | None = None,
                 source: str = "<none>") -> None:
        self.stoplines = stoplines or {}
        self.source = source
        self._warned = False

    @classmethod
    def load(cls, intersection: str) -> "Geometry":
        """由 config 的場景目錄載入 stoplines.json；不存在則回傳空幾何。"""
        cfg = config.load()
        scen = cfg["nodes"][str(intersection).lower()]["scenario"]
        p = config.path(cfg["sumo"]["scenarios"][scen]["dir"], "stoplines.json")
        if not p.exists():
            return cls(None, source=f"{p}（不存在）")
        raw = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
        return cls({k: (float(v[0]), float(v[1])) for k, v in raw.items()},
                   source=str(p))

    def dist_to_stopline(self, st: VehicleState, fallback: float) -> float:
        pt = self.stoplines.get(st.lane_id)
        if pt is None:
            if not self._warned and not self.stoplines:
                warnings.warn(
                    f"停止線幾何未載入（{self.source}），"
                    f"dist_to_stopline 一律填上限值 {fallback}。"
                    "D5–D6 建好場景後須補上 stoplines.json。",
                    RuntimeWarning, stacklevel=2)
                self._warned = True
            return fallback
        return math.hypot(st.x - pt[0], st.y - pt[1])


def signal_state(st: VehicleState, signalized: bool) -> float:
    """號誌狀態。無號誌路口固定 1（SPEC §5.2 第 8 項）。"""
    if not signalized:
        return 1.0
    ph = st.signal_phase or ""
    return _SIGNAL_MAP.get(ph[:1], 1.0)


def closest_approach(dx: float, dy: float, rvx: float, rvy: float,
                     horizon: float) -> float:
    """照目前速度外推，未來 horizon 秒內兩車的最近距離（公尺）。

    (dx, dy)：對方相對於自己的位置；(rvx, rvy)：對方相對於自己的速度。
    """
    v2 = rvx * rvx + rvy * rvy
    t = 0.0 if v2 < _EPS else max(0.0, min(horizon, -(dx * rvx + dy * rvy) / v2))
    return math.hypot(dx + rvx * t, dy + rvy * t)


def pick_conflict(ego: VehicleState, neighbors: list[VehicleState],
                  max_dist: float, cpa_m: float = 2.5,
                  horizon: float = 3.0) -> Conflict:
    """選出最近的衝突對手。選法見本檔頭註 (1)。"""
    evx, evy = ego.velocity()
    best_closing: tuple[float, Conflict] | None = None   # 以距離排序
    best_any: tuple[float, Conflict] | None = None

    for nb in neighbors:
        if nb.vehicle_id == ego.vehicle_id:
            continue
        dx, dy = nb.x - ego.x, nb.y - ego.y
        dist = math.hypot(dx, dy)
        if dist > max_dist or dist < _EPS:
            continue
        nvx, nvy = nb.velocity()
        # 不在同車道、而且照目前方向開下去碰不到的車（對向錯車、隔壁車道並行），
        # 不是威脅，不列入候選。
        if nb.lane_id != ego.lane_id and \
                closest_approach(dx, dy, nvx - evx, nvy - evy, horizon) > cpa_m:
            continue
        ux, uy = dx / dist, dy / dist
        # 相對速度在視線方向上的投影，接近為正。
        closing = (evx - nvx) * ux + (evy - nvy) * uy
        c = Conflict(
            partner_id=nb.vehicle_id,
            rel_dist=dist,
            closing_speed=closing,
            heading_cos=math.cos(math.radians(nb.heading - ego.heading)),
            same_lane=1.0 if nb.lane_id == ego.lane_id else 0.0,
        )
        if best_any is None or dist < best_any[0]:
            best_any = (dist, c)
        if closing > _EPS and (best_closing is None or dist < best_closing[0]):
            best_closing = (dist, c)

    chosen = best_closing or best_any
    if chosen is None:
        # 視野內無他車：距離填上限（等效於無風險），其餘為中性值。
        return Conflict(None, max_dist, 0.0, 1.0, 0.0)
    return chosen[1]


def raw_vector(ego: VehicleState, conflict: Conflict, dist_stopline: float,
               sig: float) -> np.ndarray:
    """組出 8 維未正規化特徵，順序同 config.yaml 的 features.order。"""
    return np.array([
        ego.speed,              # 1 ego_speed
        ego.accel,              # 2 ego_accel
        conflict.rel_dist,      # 3 rel_dist
        conflict.closing_speed, # 4 rel_speed（接近為正）
        conflict.heading_cos,   # 5 heading_diff，cos(Δψ) ∈ [-1, 1]
        conflict.same_lane,     # 6 same_lane
        dist_stopline,          # 7 dist_to_stopline
        sig,                    # 8 signal_state
    ], dtype=np.float32)


def normalize(raw: np.ndarray) -> np.ndarray:
    """min-max 正規化至 [0, 1]，值域取自 config.yaml（SPEC §15.5）。

    超出值域一律截斷，不丟樣本——丟樣本會讓危險樣本（本來就稀少且多半
    落在值域邊緣）系統性消失。
    """
    lo, hi = config.norm_bounds()
    out = (np.asarray(raw, dtype=np.float32) - lo) / (hi - lo)
    if config.load()["features"].get("clip_out_of_range", True):
        np.clip(out, 0.0, 1.0, out=out)
    return out.astype(np.float32)


def compute(ego: VehicleState, neighbors: list[VehicleState],
            geometry: Geometry, signalized: bool,
            cfg: dict | None = None) -> tuple[np.ndarray, np.ndarray, Conflict]:
    """完整的一步特徵計算。

    回傳 (已正規化特徵, 原始特徵, 衝突資訊)。
    原始特徵供 D7 的值域驗收統計使用；衝突資訊供規則基準線與 log 使用。
    """
    c = cfg or config.load()
    norm = c["features"]["norm"]
    max_dist = float(norm["rel_dist"]["max"])
    max_stop = float(norm["dist_to_stopline"]["max"])

    conflict = pick_conflict(ego, neighbors, max_dist,
                             cpa_m=float(c["features"].get("conflict_cpa_m", 2.5)),
                             horizon=float(c["label"]["horizon_seconds"]))
    d_stop = min(geometry.dist_to_stopline(ego, max_stop), max_stop)
    raw = raw_vector(ego, conflict, d_stop, signal_state(ego, signalized))
    return normalize(raw), raw, conflict
