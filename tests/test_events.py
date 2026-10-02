"""事件層級警示評估與兩段式門檻（training/events.py）。"""
from __future__ import annotations

import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mec_app import config, model as M     # noqa: E402
from training import events as E           # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


# 造一段 100 秒、兩台車（0、1）互為衝突對手的重播；再加一台旁觀車 2
T = np.round(np.arange(0, 100, 0.1), 1).astype(np.float32)
rows_t = np.concatenate([T, T, T])
rows_v = np.concatenate([np.zeros_like(T, np.int32), np.ones_like(T, np.int32), np.full_like(T, 2, np.int32)])
rows_p = np.concatenate([np.ones_like(T, np.int32), np.zeros_like(T, np.int32), np.full_like(T, -1, np.int32)])
rep = {"t": rows_t, "vid": rows_v, "partner": rows_p,
       "ev_a": np.array([0, 0], np.int32), "ev_b": np.array([1, 1], np.int32),
       "ev_t": np.array([30.0, 70.0], np.float32),
       "vd_vid": np.array([0, 1, 0, 1], np.int32), "vd_t": np.array([30.0, 30.0, 70.0, 70.0], np.float32),
       "vc_vid": np.array([0, 1, 0, 1, 2], np.int32), "vc_t": np.array([30.0, 30.0, 70.0, 70.0, 52.0], np.float32),
       "n_ids": np.array(3), "step_len": np.array(0.1, np.float32)}


def warn_at(*spans, vid=0):
    w = np.zeros(len(rows_t), bool)
    for a, b in spans:
        w |= (rows_v == vid) & (rows_t >= a) & (rows_t <= b)
    return w


print("\n[alert_metrics] 事件召回")
m = E.alert_metrics(rep, warn_at((28.0, 30.0)), rep["partner"], 3.0)
check("事件前 2 秒開始喊 → 抓到第一件、提早 2 秒", m["caught"] == 1 and abs(m["lead_median_s"] - 2.0) < 1e-6, str(m))
m = E.alert_metrics(rep, warn_at((20.0, 21.0)), rep["partner"], 3.0)
check("事件前 10 秒的警示不算預警這件事", m["caught"] == 0)
m = E.alert_metrics(rep, warn_at((29.0, 30.0), vid=1), rep["partner"], 3.0)
check("雙方任一台收到指向對方的警示即算抓到", m["caught"] == 1)
wrong = rep["partner"].copy(); wrong[rows_v == 0] = 2
m = E.alert_metrics(rep, warn_at((28.0, 30.0)), wrong, 3.0)
check("預設以「這台車有被提醒」計，不要求指向對方", m["caught"] == 1)
m = E.alert_metrics(rep, warn_at((28.0, 30.0)), wrong, 3.0, pair=True)
check("pair=True 時指向別台車的警示不算", m["caught"] == 0)

print("\n[alert_metrics] 誤報以「次」計、換算每車每小時")
m = E.alert_metrics(rep, warn_at((50.0, 52.0)), rep["partner"], 3.0)
check("連續 2 秒的警示算一次", m["alerts"] == 1, str(m))
check("之後沒遇到危險 → 誤報 1 次", m["false_alerts"] == 1)
vh = len(rows_t) * 0.1 / 3600
check("每車每小時誤報 = 誤報次數 ÷ 車小時", abs(m["false_per_vehicle_hour"] - 1 / vh) < 1e-6)
check("每次通過誤報 = 誤報次數 ÷ 車輛數", abs(m["false_per_passage"] - 1 / 3) < 1e-6)
m2 = E.alert_metrics(rep, warn_at((50.0, 52.0), vid=2), rep["partner"], 3.0, level="caution")
check("注意等級：之後出現 TTC < 3 s 就不算誤報", m2["false_alerts"] == 0, str(m2))
m3 = E.alert_metrics(rep, warn_at((50.0, 52.0), vid=2), rep["partner"], 3.0, level="danger")
check("危險等級：同樣狀況沒到 TTC < 1.5 s 就是誤報", m3["false_alerts"] == 1)
m = E.alert_metrics(rep, warn_at((50.0, 50.5), (52.0, 52.5)), rep["partner"], 3.0)
check("間隔超過 1 秒算兩次", m["alerts"] == 2)
m = E.alert_metrics(rep, warn_at((28.0, 30.0)), rep["partner"], 3.0)
check("喊了之後真的遇到危險 → 不是誤報", m["false_alerts"] == 0 and m["alert_precision"] == 1.0)

print("\n[calibrate_two_tier] 注意負責召回、危險負責精準")
cfg = config.load()
s = np.zeros(len(rows_t), np.float32)
s[warn_at((27.5, 30.0))] = 0.9                 # 第一件：高分預警
s[warn_at((67.5, 70.0))] = 0.3                 # 第二件：低分預警
s[warn_at((10.0, 11.0), (14.0, 15.0), (18.0, 19.0))] = 0.3   # 三次低分誤報 → 0.3 時精準度 2/5
th = E.calibrate_two_tier(rep, s, cfg)
check("注意門檻 <= 0.3（兩件都要抓到才達 95%）", th["caution"] <= 0.3, str(th["caution"]))
check("危險門檻 > 0.3（避開低分誤報，精準度 >= 50%）", th["danger"] > 0.3, str(th["danger"]))
check("危險門檻不低於注意門檻", th["danger"] >= th["caution"])

print("\n[decide] 兩段式")
pp = np.array([[0.9, 0.05, 0.05], [0.5, 0.2, 0.3], [0.1, 0.1, 0.8]], np.float32)
check("危險機率 0.05 / 0.3 / 0.8 → 安全 / 注意 / 危險",
      M.decide(pp, {"caution": 0.2, "danger": 0.6}).tolist() == [0, 1, 2])

print(f"\n{'='*58}\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1)
print("全部通過")
