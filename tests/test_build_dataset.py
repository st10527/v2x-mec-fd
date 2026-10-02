"""build_dataset 的驗證：用手工 fixture 模擬 SUMO 輸出。

本機沒有 SUMO，但 build 子命令是純 Python，可以完整驗證。
真正需要 SUMO 的只有 collect 子命令（TraCI），那在 VM2 上跑。

最關鍵的一項是「標籤取自未來」（SPEC §5.4）——這是本專案是預測而非偵測的
根據，若寫錯成取當下，整個題目就塌了，所以這裡測得特別細。
"""
from __future__ import annotations

import gzip
import json
import pathlib
import shutil
import sys
import tempfile

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mec_app import config                          # noqa: E402
from training import build_dataset as B             # noqa: E402
from training import dataset as D                   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


cfg = config.load()
STEP = cfg["sumo"]["step_length"]
STEPS = cfg["window"]["steps"]
tmp = pathlib.Path(tempfile.mkdtemp())

# --- 造 states.jsonl.gz：兩台車同車道，後車以 20 m/s 追上 10 m/s 的前車 ---
states = tmp / "states.jsonl.gz"
n_steps = 120                                        # 12 秒
with gzip.open(states, "wt", encoding="utf-8") as fh:
    for i in range(n_steps):
        t = round(i * STEP, 2)
        fh.write(json.dumps(B.bsm_record(
            vehicle_id="lead", inter="A", sim_time=t,
            x=0.0, y=60.0 + 10.0 * t, speed=10.0, accel=0.0,
            heading=0.0, lane_id="e1_0", signal_phase="G")) + "\n")
        fh.write(json.dumps(B.bsm_record(
            vehicle_id="ego", inter="A", sim_time=t,
            x=0.0, y=20.0 * t, speed=20.0, accel=0.0,
            heading=0.0, lane_id="e1_0", signal_phase="G")) + "\n")

# --- 造 ssm.xml：SUMO SSM device 的真實格式 ---
ssm = tmp / "ssm.xml"
ssm.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<SSMLog>
    <conflict begin="7.00" end="9.00" ego="ego" foe="lead">
        <minTTC time="8.00" position="0.00,160.00" type="10" value="1.20"/>
        <maxDRAC time="8.00" position="0.00,160.00" type="10" value="3.50"/>
    </conflict>
    <conflict begin="3.00" end="4.00" ego="ego" foe="lead">
        <minTTC time="3.50" position="0.00,70.00" type="10" value="2.40"/>
        <PET time="3.50" position="0.00,70.00" type="17" value="NA"/>
    </conflict>
    <globalMeasures ego="ego"/>
</SSMLog>
""", encoding="utf-8")

print("\n[parse_ssm] SUMO SSM device 輸出解析")
ev = B.parse_ssm(ssm)
check("ego 與 foe 都拿到事件（危險是雙方共同面對的）", set(ev) == {"ego", "lead"}, str(set(ev)))
check("ego 取到兩筆 minTTC", len(ev["ego"]) == 2, str(ev["ego"]))
check("事件依時間排序", ev["ego"] == sorted(ev["ego"]), str(ev["ego"]))
check("值為 NA 的量測被略過（不會變成 0）",
      all(v > 0 for _, v in ev["ego"]), str(ev["ego"]))
check("maxDRAC 不納入標籤（SPEC §5.4 只用 TTC / PET）",
      [v for _, v in ev["ego"]] == [2.4, 1.2], str(ev["ego"]))

print("\n[label_for] SPEC §5.4 標籤取自未來 3 秒")
e = ev["ego"]
lbl, val = B.label_for(e, 0.0, 3.0, cfg)
check("t=0：未來 3s 內只有 t=3.5 之外 -> 安全", lbl == 0, f"lbl={lbl} val={val}")
lbl, val = B.label_for(e, 1.0, 3.0, cfg)
check("t=1：涵蓋 t=3.5 的 TTC 2.4 -> 注意 1", lbl == 1 and val == 2.4, f"{lbl} {val}")
lbl, val = B.label_for(e, 5.5, 3.0, cfg)
check("t=5.5：涵蓋 t=8.0 的 TTC 1.2 -> 危險 2", lbl == 2 and val == 1.2, f"{lbl} {val}")
lbl, _ = B.label_for(e, 8.0, 3.0, cfg)
check("t=8.0：事件在當下不算未來 -> 安全（預測非偵測）", lbl == 0, str(lbl))
lbl, _ = B.label_for(e, 7.9, 3.0, cfg)
check("t=7.9：事件在 0.1 秒後 -> 危險 2", lbl == 2, str(lbl))
check("區間為 (t, t+3]，不含 t 本身",
      B.label_for([(5.0, 0.5)], 5.0, 3.0, cfg)[0] == 0
      and B.label_for([(5.0, 0.5)], 4.9, 3.0, cfg)[0] == 2)
check("剛好落在 t+3 邊界上算進去",
      B.label_for([(8.0, 0.5)], 5.0, 3.0, cfg)[0] == 2)
check("無事件 -> 安全", B.label_for([], 1.0, 3.0, cfg)[0] == 0)

print("\n[read_states]")
by_t = B.read_states(states)
check("時間步數正確", len(by_t) == n_steps, str(len(by_t)))
check("每步兩台車", all(len(v) == 2 for v in by_t.values()))
check("BSM 還原成 VehicleState", by_t[0.0][0].lane_id == "e1_0")

print("\n[build] 端到端")
out = tmp / "a.npz"
summary = B.build("a", states_path=states, ssm_path=ssm, out=out, cfg=cfg)
check("npz 已產生", out.exists())
check("summary.json 已產生", out.with_suffix(".summary.json").exists())
s = D.load(out)
check("X shape = (N, 20, 8)", s.X.shape[1:] == (STEPS, 8), str(s.X.shape))
check("特徵已正規化到 [0,1]", s.X.min() >= 0.0 and s.X.max() <= 1.0)
check("三種標籤都有出現", set(np.unique(s.y).tolist()) == {0, 1, 2}, str(np.unique(s.y)))
check("樣本數 = 車數 × 步數 / stride",
      len(s) == 2 * (n_steps // cfg["dataset"]["stride_steps"]), str(len(s)))
check("vid 有記錄（事件層級分析用）", set(s.vid.tolist()) == {"ego", "lead"})
check("t 為模擬時間且單調可排序", s.t.min() == 0.0 and s.t.max() < 12.0)
check("summary 帶出類別分布", sum(summary["class_counts"]) == len(s))
check("summary 帶出特徵值域驗收", "feature_ranges" in summary
      and "ego_speed" in summary["feature_ranges"])
fr = summary["feature_ranges"]["ego_speed"]
check("ego_speed 實際範圍 10–20 m/s 被記錄下來",
      fr["actual_min"] == 10.0 and fr["actual_max"] == 20.0, str(fr))
check("截斷比例有算出來", fr["clipped_pct"] == 0.0, str(fr["clipped_pct"]))

print("\n[build] 視窗的 edge padding 與 MEC App 行為一致")
ego_idx = np.where(s.vid == "ego")[0]
first = s.X[ego_idx[0]]
check("最早的樣本整個視窗都是同一筆（向前填充）",
      bool(np.allclose(first, first[0])), "首樣本視窗未被填滿")
later = s.X[ego_idx[-1]]
check("後期樣本視窗內有變化（非填充）", not bool(np.allclose(later, later[0])))

print("\n[dataset] 時間切分與類別權重（SPEC §6.2）")
tr, va, te = D.temporal_split(s, cfg)
check("70/15/15 比例", abs(len(tr)/len(s) - 0.7) < 0.02
      and abs(len(va)/len(s) - 0.15) < 0.02, f"{len(tr)}/{len(va)}/{len(te)}")
check("切分後三段無交集", len(tr) + len(va) + len(te) == len(s))
check("train 的時間全部早於 test（不得隨機切）",
      tr.t.max() <= te.t.min(), f"{tr.t.max()} vs {te.t.min()}")
bad_cfg = json.loads(json.dumps(cfg)); bad_cfg["train"]["split_mode"] = "random"
try:
    D.temporal_split(s, bad_cfg); ok = False
except ValueError:
    ok = True
check("split_mode 非 temporal 時直接報錯", ok)
w = D.class_weights(np.array([0]*98 + [1]*1 + [2]*1))
check("inverse frequency：稀少類權重遠高於多數類", w[2] > w[0] * 50, str(w))
check("類別完全缺席時權重為 0 而非 inf",
      D.class_weights(np.array([0, 0, 1]))[2] == 0.0)

print("\n[build] 危險樣本為 0 時會出聲（SPEC §4.2）")
empty_ssm = tmp / "empty.xml"
empty_ssm.write_text('<?xml version="1.0"?><SSMLog></SSMLog>', encoding="utf-8")
s2 = B.build("a", states_path=states, ssm_path=empty_ssm, out=tmp/"b.npz", cfg=cfg)
check("無衝突時全部標為安全", s2["class_counts"] == [len(D.load(tmp/"b.npz")), 0, 0],
      str(s2["class_counts"]))
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    B.print_summary(s2)
check("摘要會警告危險樣本為 0 並指向 SPEC §4.2", "SPEC §4.2" in buf.getvalue())

print("\n[build] 找不到輸入時的錯誤訊息可行動")
for fn, arg in [(B.read_states, tmp/"nope.jsonl.gz"), (B.parse_ssm, tmp/"nope.xml")]:
    try:
        fn(arg); ok, msg = False, ""
    except FileNotFoundError as e:
        ok, msg = True, str(e)
    check(f"{fn.__name__} 缺檔報錯且說明下一步", ok and ("collect" in msg or "SPEC" in msg), msg)

print("\n[build] 串流處理的邊界情況")
# 1) 生命週期短於 min_vehicle_steps 的車要整台捨棄，不可留下半截樣本
short = tmp / "short.jsonl.gz"
with gzip.open(short, "wt", encoding="utf-8") as fh:
    for i in range(60):
        t = round(i * STEP, 2)
        fh.write(json.dumps(B.bsm_record(vehicle_id="long", inter="A", sim_time=t,
            x=0.0, y=10.0 * t, speed=10.0, accel=0.0, heading=0.0,
            lane_id="e1_0", signal_phase="G")) + "\n")
        if i < 8:                                  # 只活 8 步的車
            fh.write(json.dumps(B.bsm_record(vehicle_id="blip", inter="A", sim_time=t,
                x=5.0, y=10.0 * t, speed=10.0, accel=0.0, heading=0.0,
                lane_id="e1_1", signal_phase="G")) + "\n")
sb = B.build("a", states_path=short, ssm_path=empty_ssm, out=tmp / "short.npz", cfg=cfg)
sd = D.load(tmp / "short.npz")
check("短命車輛（8 步 < 20 步）整台被捨棄", "blip" not in set(sd.vid.tolist()), str(set(sd.vid.tolist())))
check("summary 記下捨棄台數", sb["skipped_short_lived"] == 1, str(sb["skipped_short_lived"]))
check("長命車輛的樣本完整保留（60 步 / 每 5 步 = 12 筆）",
      int((sd.vid == "long").sum()) == 12, str(int((sd.vid == "long").sum())))
# 2) 時間倒退代表檔案被手動合併過，必須明確報錯而不是默默產出錯的資料
bad = tmp / "bad.jsonl.gz"
with gzip.open(bad, "wt", encoding="utf-8") as fh:
    for t in (0.1, 0.2, 0.1):
        fh.write(json.dumps(B.bsm_record(vehicle_id="x", inter="A", sim_time=t,
            x=0, y=0, speed=0, accel=0, heading=0, lane_id="e", signal_phase="G")) + "\n")
try:
    list(B.iter_frames(bad)); ok, msg = False, ""
except ValueError as e:
    ok, msg = True, str(e)
check("時間倒退時報錯並說明原因", ok and "時間倒退" in msg, msg[:60])
# 車長（SAE J2735 VehicleSize）隨 BSM 一起錄下，讀回來時保留；舊紀錄沒有此欄則預設 5 m
rec = B.bsm_record(vehicle_id="t", inter="A", sim_time=0.1, x=0, y=0, speed=0, accel=0,
                   heading=0, lane_id="e", signal_phase="G", length=12.0)
check("BSM 紀錄含車長欄位", rec.get("length") == 12.0, str(rec))
_, fr = next(B.iter_frames(states))
check("讀回舊紀錄（無車長）時預設 5.0 m", all(v.length == 5.0 for v in fr))
# 3) 超過 max_samples 時放大取樣間隔，而不是全部產生後再抽
tight = json.loads(json.dumps(cfg)); tight["dataset"]["max_samples"] = 10
st = B.build("a", states_path=states, ssm_path=ssm, out=tmp / "tight.npz", cfg=tight)
check("總量超過上限時自動放大取樣間隔", st["stride_steps"] > cfg["dataset"]["stride_steps"]
      and st["subsampled"], f"stride={st['stride_steps']}")
check("放大後樣本數不超過上限太多（每台車至多多 1 筆）",
      st["n_samples"] <= 10 + 2, str(st["n_samples"]))

print("\n[parse_ssm] gzip 與逐步 TTC（device.ssm.trajectories=true）")
# 構造一個「只看 minTTC 會漏標」的衝突：
#   t=10.0 時 TTC 最低 1.0（minTTC 記在這裡）
#   t=12.0 ~ 12.4 時 TTC 仍在 2.0 左右（注意等級）
# 對 t=11.0 的樣本而言，未來 3 秒內 (11, 14] 的最小值是 2.0 → 應標「注意」。
# 若只看 minTTC（發生在 t=10.0，已是過去）→ 會錯標成「安全」。
traj_xml = """<?xml version="1.0" encoding="UTF-8"?>
<SSMLog>
    <conflict begin="10.00" end="12.40" ego="ego" foe="lead">
        <timeSpan values="10.00 10.10 12.00 12.20 12.40"/>
        <typeSpan values="2 2 2 2 2"/>
        <TTCSpan values="1.00 1.20 2.10 2.00 NA"/>
        <minTTC time="10.00" position="0,0" type="2" value="1.00" speed="10"/>
        <PET time="NA" position="NA" type="NA" value="NA" speed="NA"/>
    </conflict>
</SSMLog>
"""
gz_path = tmp / "traj.xml.gz"
with gzip.open(gz_path, "wt", encoding="utf-8") as fh:
    fh.write(traj_xml)
ev_t = B.parse_ssm(gz_path)
check("讀得了 .gz", "ego" in ev_t, str(list(ev_t)))
times = [t for t, _ in ev_t["ego"]]
check("逐步 TTC 被讀進來（不只 minTTC 一點）", 12.0 in times and 12.2 in times, str(ev_t["ego"]))
check("NA 值被略過", all(v == v for _, v in ev_t["ego"]))
check("foe 也登錄同一組事件", ev_t["lead"] == ev_t["ego"])
lbl, val = B.label_for(ev_t["ego"], 11.0, 3.0, cfg)
check("t=11 的未來窗內有 TTC 2.0 → 注意（逐步 TTC 抓得到）", lbl == 1 and val == 2.0, f"{lbl} {val}")
only_min = [(10.0, 1.0)]
lbl2, _ = B.label_for(only_min, 11.0, 3.0, cfg)
check("對照組：只看 minTTC 會錯標成安全（證明逐步 TTC 有必要）", lbl2 == 0, str(lbl2))
over = B.parse_ssm(gz_path, keep_below=1.1)
check("keep_below 會丟掉高於門檻的逐步值（省記憶體）",
      all(v < 1.1 for t, v in over["ego"] if t not in (10.0,)), str(over["ego"]))

print("\n[iter_states] TraCI 狀態擷取（以假 traci 模組驗證，不需 SUMO）")
import types
tc = types.SimpleNamespace(VAR_POSITION=0x42, VAR_SPEED=0x40, VAR_ACCELERATION=0x72,
                           VAR_ANGLE=0x43, VAR_LANE_ID=0x51, VAR_LENGTH=0x44)
sys.modules["traci.constants"] = tc
sys.modules.setdefault("traci", types.ModuleType("traci")).constants = tc

class FakeTraci:
    def __init__(self):
        self.t = 0.0
        self.subscribed = set()
        sim = types.SimpleNamespace()
        sim.getMinExpectedNumber = lambda: 1 if self.t < 0.25 else 0
        sim.simulationStep = None
        sim.getTime = lambda: self.t
        sim.getDepartedIDList = lambda: ["v1"] if abs(self.t - 0.1) < 1e-9 else []
        self.simulation = sim
        veh = types.SimpleNamespace()
        veh.subscribe = lambda vid, vars_: self.subscribed.add(vid)
        veh.getAllSubscriptionResults = lambda: {
            vid: {tc.VAR_POSITION: (1.0 + self.t, 2.0), tc.VAR_SPEED: 10.0,
                  tc.VAR_ACCELERATION: 0.5, tc.VAR_ANGLE: 90.0, tc.VAR_LANE_ID: "N2C_0",
                  tc.VAR_LENGTH: 4.5}
            for vid in self.subscribed}
        self.vehicle = veh
        tl = types.SimpleNamespace()
        tl.getIDList = lambda: ["C"]
        tl.getControlledLinks = lambda tls: [[("N2C_0", "C2S_0", ":C_0")], [("S2C_0", "C2N_0", ":C_1")]]
        tl.getRedYellowGreenState = lambda tls: "rG" if self.t < 0.15 else "yG"
        self.trafficlight = tl
    def simulationStep(self):
        self.t = round(self.t + 0.1, 2)

ft = FakeTraci()
out = list(B.iter_states(ft, "A"))
# 假模組在 t < 0.25 時回報「還有車」，所以會步進 0.1、0.2、0.3 共三步
check("每步產出一批（共三步）", [t for t, _ in out] == [0.1, 0.2, 0.3],
      str([(t, len(b)) for t, b in out]))
t1, b1 = out[0]
check("sim_time 取自步進之後（第一步 = 0.1，不是 0.0）", t1 == 0.1, str(t1))
check("狀態與時間對齊（x = 1.0 + 0.1）", abs(b1[0]["position"]["x"] - 1.1) < 1e-6, str(b1[0]["position"]))
check("號誌以車道查表：N2C_0 對應燈號索引 0 → 紅", b1[0]["signal_phase"] == "r", b1[0]["signal_phase"])
check("燈號隨時間更新：下一步變黃", out[1][1][0]["signal_phase"] == "y", out[1][1][0]["signal_phase"])
check("輸出欄位符合 SPEC §5.1 BSM", set(b1[0]) >= {"vehicle_id", "position", "speed", "accel",
                                                    "heading", "lane_id", "signal_phase", "sim_time"})
check("新出發的車才訂閱（只訂一次）", ft.subscribed == {"v1"})
check("車長從 TraCI 訂閱取得（4.5 m）", b1[0]["length"] == 4.5, str(b1[0].get("length")))

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{'='*58}\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1)
print("全部通過")
