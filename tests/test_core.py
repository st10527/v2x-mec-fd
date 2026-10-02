"""共用核心模組的驗證（config / features / window / model / rule / log）。

這些測試守住的是 SPEC 中「不得簡化」與「兩端共用」的幾條紅線，
跑一次只要幾秒，每次改 mec_app/ 之後都該跑。
"""
from __future__ import annotations

import math
import sys
import pathlib

import numpy as np
import torch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mec_app import config, features as F, jsonlog, model as M, rule_baseline as R
from mec_app.window import WindowStore

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


def st(vid, x, y, speed, heading, lane="e1_0", accel=0.0, phase="G"):
    return F.VehicleState(vid, x, y, speed, accel, heading, lane, phase, 0.0)


print("\n[config] SPEC §15.5 正規化參數兩端共用")
cfg = config.load()
lo, hi = config.norm_bounds()
check("norm_bounds 長度等於特徵維度", len(lo) == len(hi) == cfg["features"]["dim"])
check("norm_bounds 有快取（兩次呼叫同一物件）", config.norm_bounds()[0] is lo)
check("peer_of('a') == 'b'", config.peer_of("a") == "b")
check("peer_of('B') == 'a'", config.peer_of("B") == "a")

print("\n[features] 速度向量與 SUMO 航向慣例（0 = 正北，順時針）")
vx, vy = st("v", 0, 0, 10, 0).velocity()
check("heading=0 -> 朝北 (0, 10)", abs(vx) < 1e-6 and abs(vy - 10) < 1e-6)
vx, vy = st("v", 0, 0, 10, 90).velocity()
check("heading=90 -> 朝東 (10, 0)", abs(vx - 10) < 1e-6 and abs(vy) < 1e-6)

print("\n[features] 衝突對手選擇（檔頭註 (1)）")
ego = st("ego", 0, 0, 20, 0)                       # 朝北 20 m/s
lead = st("lead", 0, 30, 10, 0)                    # 正前方 30 m，同向 10 m/s
check("追撞情境選到前車", F.pick_conflict(ego, [lead], 100).partner_id == "lead")
c = F.pick_conflict(ego, [lead], 100)
check("closing_speed = 20-10 = 10 m/s", abs(c.closing_speed - 10) < 1e-5, f"{c.closing_speed}")
check("同向 heading_cos = 1", abs(c.heading_cos - 1) < 1e-6)
check("同 lane -> same_lane = 1", c.same_lane == 1.0)

far_closing = st("far", 0, 60, 30, 180)            # 遠、對向高速接近
near_static = st("near", 0, 10, 0, 0)              # 近、靜止（ego 接近它）
c = F.pick_conflict(ego, [far_closing, near_static], 100)
check("接近中者裡取最近（10 m 的靜止車）", c.partner_id == "near", c.partner_id)

behind = st("behind", 0, -20, 5, 0)                # 後方同向慢車，未接近
c = F.pick_conflict(ego, [behind], 100)
check("無人接近時退回最近者", c.partner_id == "behind")
check("未接近 -> closing_speed <= 0", c.closing_speed <= 1e-6, f"{c.closing_speed}")

c = F.pick_conflict(ego, [], 100)
check("視野內無車 -> rel_dist 填上限", c.rel_dist == 100 and c.partner_id is None)

# 真的交叉衝突：兩車都在 2 秒後抵達原點
ego_x = st("ego", 0, -30, 15, 0)                   # 從南邊往北開
cross = st("cross", 30, 0, 15, 270, lane="e2_0")    # 從東邊往西開
c = F.pick_conflict(ego_x, [cross], 100)
check("路口交叉（2 秒後同時到達交叉點）會被選為對手", c.partner_id == "cross", str(c.partner_id))
check("交叉情境 heading_cos ≈ 0（正交）", abs(c.heading_cos) < 1e-6, f"{c.heading_cos}")
check("不同 lane -> same_lane = 0", c.same_lane == 0.0)
check("非共線衝突仍算得出 closing_speed", c.closing_speed > 0, f"{c.closing_speed}")

print("\n[features] 會撞得到才算威脅（v1.4，SPEC §16 待確認事項 #5）")
passed = st("late", 30, 0, 15, 270, lane="e2_0")    # 自車已開過交叉點後才到
check("自車已通過交叉點後才到的橫向車，不算威脅",
      F.pick_conflict(ego, [passed], 100).partner_id is None)
oncoming = st("oncoming", -3.5, 40, 14, 180, lane="e1r_0")   # 對向車道迎面而來
c = F.pick_conflict(ego, [oncoming], 100)
check("對向車道錯車（接近速度 34 m/s）不算威脅", c.partner_id is None, str(c.partner_id))
check("沒有威脅時 rel_speed = 0（不會再出現 ±34 m/s 的極端值）", c.closing_speed == 0.0)
beside = st("beside", 3.2, 5, 18, 0, lane="e1_1")   # 隔壁車道、同方向並行
check("隔壁車道同向並行不算威脅", F.pick_conflict(ego, [beside], 100).partner_id is None)
follow = st("lead2", 0, 12, 20, 0)                  # 同車道、等速、車距 12 m
check("同車道等速跟車仍算威脅（前車急煞就會追撞）",
      F.pick_conflict(ego, [follow], 100).partner_id == "lead2")
cutin = st("cutin", 3.2, 15, 12, 345, lane="e1_1")   # 隔壁車道，斜切進自車車道
check("隔壁車道斜切進來的車算威脅", F.pick_conflict(ego, [cutin], 100).partner_id == "cutin")
c = F.pick_conflict(ego, [oncoming, follow], 100)
check("對向車與同車道前車並存時，選同車道前車", c.partner_id == "lead2", str(c.partner_id))
check("最近距離計算：正面相向 4 m 側距 → 4 m",
      abs(F.closest_approach(-4.0, 50.0, 0.0, -30.0, 3.0) - 4.0) < 1e-6)
check("最近距離計算：遠離中的車取現在距離",
      abs(F.closest_approach(0.0, 10.0, 0.0, 5.0, 3.0) - 10.0) < 1e-6)

print("\n[features] 號誌狀態對應（SPEC §5.2 第 8 項）")
check("綠 G -> 1.0", F.signal_state(st("v",0,0,0,0,phase="G"), True) == 1.0)
check("黃 y -> 0.5", F.signal_state(st("v",0,0,0,0,phase="y"), True) == 0.5)
check("紅 r -> 0.0", F.signal_state(st("v",0,0,0,0,phase="r"), True) == 0.0)
check("無號誌路口固定 1.0", F.signal_state(st("v",0,0,0,0,phase="r"), False) == 1.0)

print("\n[features] 正規化")
geom = F.Geometry({"e1_0": (0.0, 50.0)}, source="<test>")
n, raw, c = F.compute(ego, [lead], geom, signalized=True)
check("正規化後落在 [0,1]", float(n.min()) >= 0.0 and float(n.max()) <= 1.0)
check("輸出維度為 8", n.shape == (8,) and raw.shape == (8,))
check("dist_to_stopline 由幾何算出（50 m）", abs(raw[6] - 50.0) < 1e-5, f"{raw[6]}")
over = F.normalize(np.array([999, 999, 999, 999, 9, 9, 999, 9], dtype=np.float32))
check("超出值域截斷至 1.0 而非丟棄", bool(np.all(over == 1.0)))
under = F.normalize(np.array([-999]*8, dtype=np.float32))
check("低於值域截斷至 0.0", bool(np.all(under == 0.0)))
empty_geom = F.Geometry(None, source="<missing>")
import warnings
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    _, raw2, _ = F.compute(ego, [lead], empty_geom, signalized=True)
    check("缺 stoplines.json 時警告一次且不中斷", len(w) == 1 and raw2[6] == 100.0)

print("\n[window] SPEC §5.3 滑動視窗")
store = WindowStore(ttl_s=1.0)
w = store.push("veh_0042", n, now=100.0)
check("首筆即以 edge padding 填滿視窗", w.ready and len(w.buf) == cfg["window"]["steps"])
check("視窗張量 shape = (20, 8)", w.tensor().shape == (20, 8))
first = w.tensor()[0].copy()
store.push("veh_0042", np.ones(8, dtype=np.float32), now=100.1)
t = w.tensor()
check("推入後最新一筆在尾端", bool(np.all(t[-1] == 1.0)))
check("最舊一筆被擠出（長度不變）", len(w.buf) == 20)
check("視窗時間序為由舊到新", bool(np.all(t[0] == first)))
store.push("veh_other", n, now=100.0)
check("兩車各自獨立視窗", len(store) == 2)
check("逾時淘汰生效", store.evict_stale(now=102.0) == 2 and len(store) == 0)

print("\n[rule_baseline] SPEC §6.3 對照組")
check("接近中 30m / 10(m/s) -> TTC 3.0", abs(R.ttc(30, 10) - 3.0) < 1e-9)
check("未接近 -> TTC = inf", math.isinf(R.ttc(30, -5)))
check("TTC 1.4 -> 危險 2", R.classify(1.4) == 2)
check("TTC 1.5 -> 注意 1（門檻為嚴格小於）", R.classify(1.5) == 1)
check("TTC 2.9 -> 注意 1", R.classify(2.9) == 1)
check("TTC 3.0 -> 安全 0", R.classify(3.0) == 0)
check("TTC inf -> 安全 0", R.classify(math.inf) == 0)
lvl, t_now = R.evaluate(14.0, 10.0)
check("evaluate 回傳 (等級, TTC)", lvl == 2 and abs(t_now - 1.4) < 1e-9)

# 平台範例的追撞規則：同車道前車、保險桿間距（車頭距離 − 前車車長）
ego = st("ego", 0, 0, 15.0, 90)                        # 往東 15 m/s
lead = st("lead", 20, 0, 5.0, 90); lead.length = 5.0   # 車頭距離 20 m → 間距 15 m
lvl, t_now, _tgt = R.evaluate_platform(ego, [ego, lead])
check("平台規則：間距扣掉前車車長（15/10 = 1.5 s -> 注意）",
      lvl == 1 and abs(t_now - 1.5) < 1e-9, f"{lvl} {t_now}")
lead.length = 12.0                                      # 大車：間距只剩 8 m
lvl, t_now, _tgt = R.evaluate_platform(ego, [ego, lead])
check("平台規則：前車是大車時間距更小（0.8 s -> 危險）", lvl == 2 and abs(t_now - 0.8) < 1e-9)
behind = st("behind", -10, 0, 25.0, 90)
lvl, _, _tgt = R.evaluate_platform(ego, [ego, behind])
check("平台規則：後方的車不是前車", lvl == 0)
other_lane = st("x", 10, 0, 0.0, 90, lane="e1_1")
lvl, _, _tgt = R.evaluate_platform(ego, [ego, other_lane])
check("平台規則：只看同車道", lvl == 0)
slow = st("slow", 20, 0, 15.05, 90)
lvl, t_now, _tgt = R.evaluate_platform(ego, [ego, slow])
check("平台規則：沒有在接近（相對速度 <= 0.1）-> 安全", lvl == 0 and math.isinf(t_now))
touch = st("touch", 4, 0, 15.0, 90)
lvl, t_now, _tgt = R.evaluate_platform(ego, [ego, touch])
check("平台規則：間距 <= 0 視為碰撞（TTC 0）", lvl == 2 and t_now == 0.0)
near, far = st("n", 30, 0, 0.0, 90), st("f", 60, 0, 0.0, 90)
leader, _ = R.same_lane_leader(ego, [far, ego, near])
check("平台規則：取最近的前車", leader is near)
_, _, tgt = R.evaluate_platform(ego, [far, ego, near])
check("平台規則：回傳警告針對的車（前車 id）", tgt == "n", str(tgt))
check("BSM 沒帶車長時預設 5.0 m（與平台範例相同）",
      F.VehicleState.from_bsm({"vehicle_id": "v", "position": {"x": 0, "y": 0}, "speed": 0,
                               "accel": 0, "heading": 0, "lane_id": "l"}).length == 5.0)

print("\n[model] SPEC §6.1 / §2 Q5 紅線")
m = M.build()
check("參數量 48,419（config 與 SPEC 一致）", M.param_count(m) == 48419, str(M.param_count(m)))
check("fp32 = 193,676 bytes", M.fp32_bytes(m) == cfg["model"]["fp32_bytes"])
out = m(torch.randn(4, 20, 8))
check("forward: (B,20,8) -> (B,3)", tuple(out.shape) == (4, 3))
try:
    m(torch.randn(4, 8, 20)); ok = False
except Exception:
    ok = True
check("維度顛倒會報錯而非默默算錯", ok)
bad = dict(cfg); bad["model"] = dict(cfg["model"], fc_hidden=96)
try:
    M.build(bad); ok = False
except RuntimeError:
    ok = True
check("改結構卻沒改 config -> build 直接擋下", ok)
ratio = M.fp32_bytes(m) / cfg["fd"]["logits"]["raw_bytes"]
check("logits 省下 >5 倍（Q5 論證不反轉）", ratio > 5.0, f"{ratio:.2f}x")
sm = M.soft_targets(torch.tensor([[1.0, 2.0, 3.0]]), 3.0)
check("溫度軟標籤總和為 1", abs(float(sm.sum()) - 1.0) < 1e-6)
hard = M.soft_targets(torch.tensor([[1.0, 2.0, 3.0]]), 1.0)
check("T 越大分布越平緩", float(sm.max()) < float(hard.max()))

print("\n[jsonlog] SPEC §8.4 欄位固定")
import tempfile, json as _json
tmp = tempfile.mkdtemp()
tcfg = _json.loads(_json.dumps(cfg))
tcfg["logs"]["dir"] = tmp
wtr = jsonlog.JsonlWriter("fd_rounds", cfg=tcfg)
wtr.write(round=1, node="a", bytes_sent=24000, local_f1=0.8, cross_f1=0.6, duration_s=3.2)
check("寫入成功且檔案存在", wtr.path.exists())
for name, kw in [("缺欄位", dict(round=1)),
                 ("多欄位", dict(round=1, node="a", bytes_sent=1, local_f1=1,
                                 cross_f1=1, duration_s=1, oops=1))]:
    try:
        wtr.write(**kw); ok = False
    except ValueError:
        ok = True
    check(f"{name}直接報錯", ok)
rows = jsonlog.read("fd_rounds", cfg=tcfg)
check("讀回一筆且欄位順序固定", len(rows) == 1 and list(rows[0]) == wtr.fields)
check("不存在的 log 讀回空 list", jsonlog.read("events", cfg=tcfg) == [])

print(f"\n{'='*58}\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1)
print("全部通過")
