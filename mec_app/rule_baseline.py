"""規則基準線（SPEC §6.3）—— 對照組，必須實作，不是被丟棄的東西。

沿用平台範例 KevinTseng-0430/SUMO-V2X-TTC（app.py 的 compute_rear_end_ttc）的邏輯：
  * 只看**同車道的前車**（平台範例是追撞預警）
  * 距離用**保險桿到保險桿**：SUMO 的位置是車頭，所以
        間距 = 兩車頭距離 − 前車車長
  * TTC = 間距 ÷（自車速度 − 前車速度），相對速度 <= 0.1 m/s 視為沒有在接近
  * 間距 <= 0 視為已碰撞，TTC = 0
門檻則用 SPEC §6.3 的 1.5 / 3.0 秒（與模型標籤同一把尺，比較才公平；
平台範例預設是 4.0 / 5.0 秒）。

與模型的差別：本模組只看「此刻」，模型看「未來 3 秒」（SPEC §5.4）。
兩者第一次喊危險的時間差，就是 SPEC §8.1 的「平均提前預警時間」。

（v1.4 更正）先前版本用車頭位置直接相減當距離、而且對任何方向的車都算 TTC，
每一對都多算了一台車長，TTC 被系統性高估，路口 A 的規則幾乎不會觸發——
那會讓「模型比規則好」的比較失去公信力。現在改為與平台範例逐條一致。
"""
from __future__ import annotations

import math

from . import config

_EPS = 1e-6


def ttc(rel_dist: float, closing_speed: float) -> float:
    """當下 TTC。closing_speed 為正代表接近中；未接近則回傳 inf。"""
    if closing_speed <= _EPS:
        return math.inf
    return float(rel_dist) / float(closing_speed)


def classify(ttc_now: float, cfg: dict | None = None) -> int:
    """TTC -> 風險等級 {0 安全, 1 注意, 2 危險}。"""
    c = (cfg or config.load())["rule_baseline"]
    if ttc_now < float(c["ttc_danger_lt"]):
        return 2
    if ttc_now < float(c["ttc_caution_lt"]):
        return 1
    return 0


def evaluate(rel_dist: float, closing_speed: float,
             cfg: dict | None = None) -> tuple[int, float]:
    """一次回傳 (風險等級, 當下 TTC)，供 inference log 的 risk_rule / ttc_now。"""
    t = ttc(rel_dist, closing_speed)
    return classify(t, cfg), t


def same_lane_leader(ego, neighbors):
    """同車道、在自車前方、最近的那台車（平台範例 select_vehicle_pair 的等價物）。

    「前方」以自車航向判斷：對方位置減自車位置，投影到自車行進方向上為正。
    """
    import math as _m
    hx, hy = _m.sin(_m.radians(ego.heading)), _m.cos(_m.radians(ego.heading))
    best, best_d = None, _m.inf
    for nb in neighbors:
        if nb.vehicle_id == ego.vehicle_id or nb.lane_id != ego.lane_id:
            continue
        dx, dy = nb.x - ego.x, nb.y - ego.y
        if dx * hx + dy * hy <= 0:          # 在後方
            continue
        d = _m.hypot(dx, dy)
        if d < best_d:
            best, best_d = nb, d
    return best, best_d


def evaluate_platform(ego, neighbors, cfg: dict | None = None) -> tuple[int, float, str | None]:
    """平台範例的追撞 TTC 規則。回傳 (風險等級, 當下 TTC, 前車 id)。

    前車 id 就是這個警告「在提醒哪一台車」，與模型回應的 conflict_with 對應。
    """
    leader, d = same_lane_leader(ego, neighbors)
    if leader is None:
        return 0, math.inf, None
    gap = d - float(leader.length)                  # 保險桿到保險桿
    rel = float(ego.speed) - float(leader.speed)
    if gap <= 0:
        return classify(0.0, cfg), 0.0, leader.vehicle_id      # 已經碰到
    if rel <= 0.1:
        return 0, math.inf, leader.vehicle_id                  # 沒有在接近
    t = gap / rel
    return classify(t, cfg), t, leader.vehicle_id

