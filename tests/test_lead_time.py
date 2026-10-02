"""提前預警時間的事件配對邏輯（training/lead_time.py 的純函式部分）。"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from training import lead_time as L      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


print("\n[parse_conflict_pairs] 一對車算一件")
xml = pathlib.Path(tempfile.mkdtemp()) / "ssm.xml"
xml.write_text("""<?xml version="1.0"?>
<SSMLog>
  <conflict begin="99.0" end="104.0" ego="follow" foe="lead">
    <timeSpan values="100.0 101.0 101.1 103.0"/>
    <TTCSpan values="2.0 1.2 1.0 NA"/>
    <minTTC time="101.1" position="0,0" type="2" value="1.00" speed="5"/>
    <PET time="NA" position="NA" type="NA" value="NA" speed="NA"/>
  </conflict>
  <conflict begin="105.0" end="106.0" ego="lead" foe="follow">
    <timeSpan values="105.0"/><TTCSpan values="1.3"/>
  </conflict>
  <conflict begin="105.0" end="106.0" ego="x" foe="y">
    <timeSpan values="105.0"/><TTCSpan values="2.5"/>
    <minTTC time="105.0" position="0,0" type="2" value="2.50" speed="5"/>
  </conflict>
</SSMLog>
""", encoding="utf-8")
pairs = L.parse_conflict_pairs(xml, danger_lt=1.5)
check("ego/foe 對調仍是同一對", list(pairs) == [("follow", "lead")], str(pairs))
check("只留低於危險門檻的時間（NA 略過）", pairs[("follow", "lead")] == [101.0, 101.1, 101.1, 105.0],
      str(pairs))

print("\n[danger_events] 危險事件的界定")
pairs = {("a", "b"): [101.0, 101.1, 103.0,      # 同一件（5 秒內連續）
                      110.0,                    # 隔 7 秒 → 新的一件
                      200.0]}                   # 在測試時段外
ev = L.danger_events(pairs, t0=90.0, t1=150.0)
check("5 秒內的連續危險算同一件、事件時間取第一次", ev[0] == (("a", "b"), 101.0), str(ev))
check("間隔超過 5 秒算新的一件", (("a", "b"), 110.0) in ev, str(ev))
check("只看測試時段", all(90 <= t <= 150 for _, t in ev), str(ev))
check("共 2 件", len(ev) == 2, str(ev))

print("\n[match] 模型與規則第一次警告")
events = [(("v1", "v2"), 101.0)]
model = {("v1", "v2"): [97.0, 98.5, 99.0, 100.0]}  # 97.0 早於 3 秒前，不算；98.5 才是第一次
rule = {("v1", "v2"): [100.8, 101.0]}
rows = L.match(events, model, rule, lookback=3.0)
r = rows[0]
check("事件前 3 秒以外的警告不算（模型只預測未來 3 秒）", r["first_warn_model_t"] == 98.5, str(r))
check("規則第一次警告 100.8", r["first_warn_rule_t"] == 100.8, str(r))
check("提早時間 = 規則時間 − 模型時間 = 2.3 秒", r["lead_time"] == 2.3, str(r))
check("沒從視窗起點就在喊，不算觸頂", r["model_at_cap"] is False, str(r))
cap = L.match(events, {("v1", "v2"): [97.0, 98.0]}, {}, lookback=3.0)[0]
check("視窗一開始就在喊 → 標記為觸頂（提早 3 秒是上限）", cap["model_at_cap"] is True, str(cap))
rear = L.match(events, {}, {("v2", "v1"): [100.9]}, lookback=3.0)[0]
check("雙方任一台收到警告就算（追撞時規則只提醒後車）", rear["first_warn_rule_t"] == 100.9, str(rear))
both = L.match(events, {("v1", "v2"): [99.5], ("v2", "v1"): [98.8]}, {}, lookback=3.0)[0]
check("雙方都有警告時取較早的", both["first_warn_model_t"] == 98.8, str(both))
late = L.match(events, {}, {("v1", "v2"): [101.4]}, lookback=3.0)[0]
check("事件後 0.5 秒內的規則警告仍算（規則只能當下觸發）", late["first_warn_rule_t"] == 101.4, str(late))
too_late = L.match(events, {}, {("v1", "v2"): [101.8]}, lookback=3.0)[0]
check("事件後超過 0.5 秒的警告不算", too_late["first_warn_rule_t"] is None, str(too_late))
other = L.match(events, {("v1", "v9"): [98.0]}, {("v2", "v9"): [99.0]}, lookback=3.0)[0]
check("指向別台車的警告不算（車隊裡的其他危險）",
      other["first_warn_model_t"] is None and other["first_warn_rule_t"] is None, str(other))

print("\n[_drop_previous] 重跑時取代自己上次的紀錄")
ev_log = pathlib.Path(tempfile.mkdtemp()) / "events.jsonl"
ev_log.write_text("".join(json.dumps({"event_id": e}) + "\n" for e in
                          ["a-round_0-0", "a-round_0-1", "b-round_0-0", "a-round_5-0"]))
L._drop_previous(ev_log, "a-round_0-")
left = [json.loads(x)["event_id"] for x in ev_log.read_text().splitlines()]
check("只移除同節點同權重的舊紀錄", left == ["b-round_0-0", "a-round_5-0"], str(left))

print("\n[summarize] 報告三個數字")
rows = [
    {"lead_time": 2.0, "first_warn_model_t": 1, "first_warn_rule_t": 3},
    {"lead_time": -0.5, "first_warn_model_t": 3, "first_warn_rule_t": 2.5},
    {"lead_time": None, "first_warn_model_t": 5, "first_warn_rule_t": None},   # 規則漏、模型抓
    {"lead_time": None, "first_warn_model_t": None, "first_warn_rule_t": 7},   # 模型漏、規則抓
    {"lead_time": None, "first_warn_model_t": None, "first_warn_rule_t": None},
]
s = L.summarize(rows)
check("平均提早只算兩者都有警告的事件", s["lead_time_mean_s"] == 0.75, str(s))
check("模型較早的比例 50%", s["model_earlier_pct"] == 50.0, str(s))
check("規則漏掉、模型抓到 1 件", s["rule_missed_model_caught"] == 1)
check("模型漏掉、規則抓到 1 件", s["model_missed_rule_caught"] == 1)
check("兩者都沒抓到 1 件", s["both_missed"] == 1)
check("沒有任何兩者皆警告的事件時不報平均（不會除以零）",
      L.summarize([{"lead_time": None, "first_warn_model_t": None, "first_warn_rule_t": None}])
      ["lead_time_mean_s"] is None)

print(f"\n{'='*58}\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1)
print("全部通過")
