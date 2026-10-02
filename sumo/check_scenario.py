#!/usr/bin/env python3
"""場景自我驗收工具——做完每一步就跑一次，看自己過了沒。

它會依序檢查：

  第 1 關  檔案齊不齊                  （nodes / edges / routes / sumocfg / netccfg）
  第 2 關  路網建好了沒                （net.xml、stoplines.json）
  第 3 關  車流組成對不對              （對照 SPEC §4.3 的車種比例與流量）
  第 4 關  SSM 設定開了沒              （沒開就沒有標籤，整個專案做不下去）
  第 5 關  跑出來的資料夠不夠          （模擬時間、危險事件數量）
  第 6 關  兩個路口夠不夠「不一樣」    （SPEC §4.3 的異質性驗收，本專案成敗關鍵）
  第 7 關  資料集能不能用              （npz 的類別分布、特徵值域截斷比例）

前面的關沒過，後面的關就不用看——先修前面的。

用法：
  python sumo/check_scenario.py intersection_a        # 檢查單一場景
  python sumo/check_scenario.py all                   # 三個場景全檢查 + 比較 A、B
"""
from __future__ import annotations

import gzip
import json
import pathlib
import sys
import xml.etree.ElementTree as ET
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
SUMO = ROOT / "sumo"

# ---------------------------------------------------------------------------
# SSM 衝突型態碼 -> 類別（依 SUMO 1.27.0 官方文件 docs/Simulation/Output/SSM_Device.md）
# ---------------------------------------------------------------------------
REAR_END = {1, 2, 3, 18}                 # FOLLOWING_*：前後車，追撞型
MERGING = {5, 6, 7, 8, 19}               # MERGING_*：匯入型
CROSSING = {9, 10, 11, 12, 13, 14, 15, 16, 17}   # CROSSING / 衝突區：交叉型
ONCOMING = {20}
COLLISION = {111}


def conflict_class(t: int) -> str:
    if t in REAR_END:
        return "追撞"
    if t in MERGING:
        return "匯入"
    if t in CROSSING:
        return "交叉"
    if t in ONCOMING:
        return "對向"
    if t in COLLISION:
        return "碰撞"
    return "其他"


# SPEC §4.3 的目標值
TARGETS = {
    "intersection_a": {
        "描述": "號誌化四叉路口，雙向各 2 車道",
        "車種": {"passenger": 0.90, "truck": 0.10},
        "每方向流量": 1200,
        "主要衝突": "追撞",
    },
    "intersection_b": {
        "描述": "無號誌 T 字匯入，單車道",
        "車種": {"passenger": 0.60, "motorcycle": 0.40},
        "每方向流量": 500,
        "主要衝突": "側向（匯入 + 交叉）",
    },
    "proxy_public": {
        "描述": "中性幾何，只用來產生公共代理資料集",
        "車種": None,
        "每方向流量": None,
        "主要衝突": None,
    },
}

DANGER = 1.5      # SPEC §5.4：min(TTC, PET) < 1.5 s 為危險
CAUTION = 3.0     # 1.5 <= x < 3.0 為注意
MIN_SIM_S = 3600  # SPEC §4.2：每個場景至少 3600 秒


# ---------------------------------------------------------------------------
# 輸出
# ---------------------------------------------------------------------------
class Report:
    def __init__(self, name: str) -> None:
        self.name = name
        self.fails = 0
        self.warns = 0

    def ok(self, msg: str) -> None:
        print(f"    [通過] {msg}")

    def warn(self, msg: str, how: str = "") -> None:
        self.warns += 1
        print(f"    [注意] {msg}")
        if how:
            print(f"           → {how}")

    def fail(self, msg: str, how: str = "") -> None:
        self.fails += 1
        print(f"    [失敗] {msg}")
        if how:
            print(f"           → 怎麼修：{how}")

    def stage(self, title: str) -> None:
        print(f"\n  {title}")


# ---------------------------------------------------------------------------
# 各關
# ---------------------------------------------------------------------------
def check_files(r: Report, d: pathlib.Path, scen: str) -> bool:
    r.stage("第 1 關｜檔案齊不齊")
    need = {
        f"{scen}.netccfg": "路網建置設定（告訴 netconvert 讀哪些檔）",
        "nodes.nod.xml": "節點：路口與端點的位置",
        "edges.edg.xml": "道路：誰連到誰、幾條車道、速限",
        "traffic.rou.xml": "車種與車流",
        f"{scen}.sumocfg": "模擬設定（含 SSM）",
    }
    all_ok = True
    for fn, what in need.items():
        if (d / fn).exists():
            r.ok(f"{fn}（{what}）")
        else:
            r.fail(f"缺少 {fn}（{what}）",
                   f"照 docs/STUDENT_GUIDE.md 第 4 章建立 {scen}/{fn}")
            all_ok = False
    for fn in need:
        p = d / fn
        if p.exists():
            try:
                ET.parse(p)
            except ET.ParseError as e:
                r.fail(f"{fn} 不是合法的 XML：{e}",
                       "通常是少了結尾的 /> 或 </...>，或引號沒關。錯誤訊息裡的行號就是出事的地方")
                all_ok = False
    return all_ok


# 中央路口的型態是場景的「身分」：A 有號誌、B 支道停讓、公共場景右方車優先。
# 公共場景尤其不能改——兩個路口必須持有位元相同的公共資料（SPEC §7.1），
# 改了型態，公共資料就整份不同，和老師那邊的副本對不上。
CENTER_TYPE = {"intersection_a": "traffic_light", "intersection_b": "priority_stop",
               "proxy_public": "right_before_left"}


def check_center_type(r: Report, d: pathlib.Path, scen: str) -> None:
    want = CENTER_TYPE.get(scen)
    nod = d / "nodes.nod.xml"
    if not want or not nod.exists():
        return
    center = next((n for n in ET.parse(nod).getroot().iter("node") if n.get("id") == "C"), None)
    got = center.get("type") if center is not None else None
    if got == want:
        r.ok(f"中央路口型態 {got}（與手冊一致）")
    else:
        r.fail(f"中央路口型態是 {got}，手冊規定 {want}",
               f"nodes.nod.xml 的 C 節點改回 type=\"{want}\" 後重建路網。"
               "想改路口型態請先問老師，並記在 docs/tuning_log.md")


def check_network(r: Report, d: pathlib.Path, scen: str) -> bool:
    r.stage("第 2 關｜路網建好了沒")
    check_center_type(r, d, scen)
    net = d / f"{scen}.net.xml"
    if not net.exists():
        r.fail(f"找不到 {scen}.net.xml", f"執行 bash sumo/build_networks.sh {scen}")
        return False
    r.ok(f"{scen}.net.xml 存在")
    root = ET.parse(net).getroot()
    tls = [j for j in root.iter("junction") if j.get("type") == "traffic_light"]
    if scen == "intersection_a":
        if tls:
            r.ok(f"有號誌路口（{len(tls)} 個）")
            for p in root.iter("tlLogic"):
                cycle = sum(float(ph.get("duration", 0)) for ph in p.iter("phase"))
                if abs(cycle - 60) <= 1:
                    r.ok(f"號誌週期 {cycle:.0f} 秒（SPEC 要求 60 秒）")
                else:
                    r.warn(f"號誌週期 {cycle:.0f} 秒，SPEC §4.3 要求 60 秒",
                           f"在 {scen}.netccfg 裡設定 <tls.cycle.time value=\"60\"/> 後重建路網")
        else:
            r.fail("路口A 應該有號誌，但路網裡沒有 traffic_light",
                   "nodes.nod.xml 中央節點要設 type=\"traffic_light\"")
    if scen == "intersection_b" and tls:
        r.fail("路口B 應該是無號誌，但路網裡有 traffic_light",
               "nodes.nod.xml 中央節點改成 type=\"priority_stop\"（次要道路停讓）")
    sl = d / "stoplines.json"
    if sl.exists():
        n = len(json.loads(sl.read_text(encoding="utf-8")))
        r.ok(f"stoplines.json 有 {n} 條車道")
    else:
        r.fail("找不到 stoplines.json", f"執行 bash sumo/build_networks.sh {scen}（會自動產生）")
        return False
    return True


def vtype_mix(d: pathlib.Path) -> tuple[dict[str, float], dict[str, float]]:
    """從 traffic.rou.xml 算出車種比例與每個入口的每小時流量。"""
    root = ET.parse(d / "traffic.rou.xml").getroot()
    vclass = {vt.get("id"): vt.get("vClass", "passenger") for vt in root.iter("vType")}
    dist: dict[str, dict[str, float]] = {}
    for vd in root.iter("vTypeDistribution"):
        members = {}
        for vt in vd.iter("vType"):
            members[vt.get("vClass", "passenger")] = members.get(vt.get("vClass", "passenger"), 0) + \
                float(vt.get("probability", 1))
        if not members and vd.get("vTypes"):
            ids = vd.get("vTypes").split()
            probs = [float(x) for x in (vd.get("probabilities") or "").split()] or [1.0] * len(ids)
            for i, p in zip(ids, probs):
                members[vclass.get(i, "passenger")] = members.get(vclass.get(i, "passenger"), 0) + p
        tot = sum(members.values()) or 1
        dist[vd.get("id")] = {k: v / tot for k, v in members.items()}

    by_class: Counter = Counter()
    by_entry: Counter = Counter()
    for f in root.iter("flow"):
        vph = float(f.get("vehsPerHour", 0) or 0)
        if not vph and f.get("period"):
            vph = 3600 / float(f.get("period"))
        t = f.get("type", "DEFAULT_VEHTYPE")
        mix = dist.get(t) or {vclass.get(t, "passenger"): 1.0}
        for c, p in mix.items():
            by_class[c] += vph * p
        entry = (f.get("from") or f.get("route") or "?")
        by_entry[entry] += vph
    tot = sum(by_class.values()) or 1
    return {k: v / tot for k, v in by_class.items()}, dict(by_entry)


def check_traffic(r: Report, d: pathlib.Path, scen: str) -> None:
    r.stage("第 3 關｜車流組成對不對（SPEC §4.3）")
    t = TARGETS[scen]
    try:
        mix, entries = vtype_mix(d)
    except (ET.ParseError, FileNotFoundError):
        r.fail("traffic.rou.xml 讀不了", "先過第 1 關")
        return
    if not entries:
        r.fail("traffic.rou.xml 裡沒有任何 <flow>", "照手冊第 4 章加上車流")
        return
    if t["車種"]:
        for c, want in t["車種"].items():
            got = mix.get(c, 0)
            msg = f"{c} 佔 {got:.0%}（目標 {want:.0%}）"
            if abs(got - want) <= 0.03:
                r.ok(msg)
            else:
                r.warn(msg, "調整 vTypeDistribution 裡各車種的 probability")
    if t["每方向流量"]:
        for e, vph in sorted(entries.items()):
            msg = f"入口 {e}：{vph:.0f} 輛/小時（目標 {t['每方向流量']}）"
            if abs(vph - t["每方向流量"]) / t["每方向流量"] <= 0.1:
                r.ok(msg)
            else:
                r.warn(msg, "調整該入口所有 <flow> 的 vehsPerHour 加總")
    else:
        r.ok(f"共 {len(entries)} 個入口、總流量 {sum(entries.values()):.0f} 輛/小時")


def check_ssm_config(r: Report, d: pathlib.Path, scen: str) -> None:
    r.stage("第 4 關｜SSM 設定開了沒（沒開就沒有標籤）")
    cfg = d / f"{scen}.sumocfg"
    if not cfg.exists():
        r.fail("找不到 sumocfg", "先過第 1 關")
        return
    txt = cfg.read_text(encoding="utf-8")
    checks = {
        'device.ssm.probability value="1"': "SSM 裝在每一台車上",
        "TTC": "量測 TTC",
        "PET": "量測 PET",
        'device.ssm.file value="ssm.xml': "輸出檔名是 ssm.xml 或 ssm.xml.gz",
        'step-length value="0.1"': "時間步長 0.1 秒（SPEC §4.2）",
    }
    for key, what in checks.items():
        if key in txt:
            r.ok(what)
        else:
            r.fail(f"sumocfg 裡找不到：{key}（{what}）",
                   "對照手冊第 4 章的 sumocfg 範例補上")


def parse_ssm_events(path: pathlib.Path):
    """逐筆讀 ssm.xml（檔案可能很大，用 iterparse 不整份載入）。"""
    events = []
    opener = gzip.open if path.suffix == ".gz" else open
    fh = opener(path, "rb")
    for _, el in ET.iterparse(fh, events=("end",)):
        if el.tag != "conflict":
            continue
        best = None
        for tag in ("minTTC", "PET"):
            for m in el.iter(tag):
                v, ty = m.get("value"), m.get("type")
                if v in (None, "NA") or ty in (None, "NA"):
                    continue
                try:
                    fv = float(v)
                except ValueError:
                    continue
                if fv < 0:
                    continue
                if best is None or fv < best[0]:
                    best = (fv, int(float(ty)))
        if best:
            events.append(best)
        el.clear()
    fh.close()
    return events


def check_run(r: Report, d: pathlib.Path, scen: str):
    r.stage("第 5 關｜跑出來的資料夠不夠")
    states = d / "states.jsonl.gz"
    ssm = d / "ssm.xml.gz"
    if not ssm.exists():
        ssm = d / "ssm.xml"
    if not states.exists() or not ssm.exists():
        missing = [p.name for p in (states, ssm) if not p.exists()]
        r.fail(f"還沒有模擬輸出：缺 {', '.join(missing)}",
               f"執行 python -m training.build_dataset collect --scenario "
               f"{ {'intersection_a':'a','intersection_b':'b','proxy_public':'proxy'}[scen] }")
        return None
    last_t = 0.0
    with gzip.open(states, "rt", encoding="utf-8") as fh:
        for line in fh:
            pass
        if line.strip():
            last_t = json.loads(line)["sim_time"]
    if last_t >= MIN_SIM_S - 1:
        r.ok(f"模擬時間 {last_t:.0f} 秒（至少 {MIN_SIM_S}）")
    else:
        r.warn(f"模擬時間只有 {last_t:.0f} 秒，SPEC §4.2 要求至少 {MIN_SIM_S} 秒",
               "正式資料要跑滿；試跑階段可以先用 --max-seconds 600 看趨勢")

    ev = parse_ssm_events(ssm)
    danger = [e for e in ev if e[0] < DANGER]
    caution = [e for e in ev if DANGER <= e[0] < CAUTION]
    print(f"           SSM 衝突總數 {len(ev)}，其中危險(<{DANGER}s) {len(danger)}、注意 {len(caution)}")
    if len(danger) >= 50:
        r.ok(f"危險事件 {len(danger)} 筆（至少 50 筆才訓練得動）")
    elif danger:
        r.warn(f"危險事件只有 {len(danger)} 筆，偏少",
               "提高 vType 的 sigma（0.5→0.8）、降低 tau（1.0→0.5）、"
               "把 speedFactor 平均值調到 1.1 以上，或提高流量")
    else:
        r.fail("一筆危險事件都沒有——SUMO 預設駕駛模型幾乎不會出事",
               "照手冊第 5 章調整駕駛參數（sigma / tau / speedFactor / minGap）")
    return danger, caution


def summarize(events, label: str):
    cls = Counter(conflict_class(t) for _, t in events)
    tot = sum(cls.values()) or 1
    order = ["追撞", "匯入", "交叉", "對向", "碰撞", "其他"]
    parts = [f"{k} {cls.get(k,0)}（{cls.get(k,0)/tot:.0%}）" for k in order if cls.get(k)]
    print(f"           {label}：" + "、".join(parts) if parts else f"           {label}：無")
    rear = cls.get("追撞", 0) / tot
    lateral = (cls.get("匯入", 0) + cls.get("交叉", 0)) / tot
    return rear, lateral


def check_heterogeneity(r: Report, scen: str, danger, caution) -> None:
    r.stage("第 6 關｜這個路口的危險型態對不對（SPEC §4.3，本專案成敗關鍵）")
    if scen == "proxy_public":
        summarize(danger + caution, "代理場景 危險+注意")
        r.ok("代理場景不要求特定型態，只要三種風險等級都有樣本即可")
        return
    rear, lateral = summarize(danger, "危險事件型態")
    summarize(danger + caution, "危險+注意 型態")
    if not danger:
        r.fail("沒有危險事件，無法判斷", "先過第 5 關")
        return
    if scen == "intersection_a":
        if rear > lateral:
            r.ok(f"追撞為主（{rear:.0%}）")
        else:
            r.fail(f"追撞只佔 {rear:.0%}，沒有成為主要型態",
                   "路口A 要追撞為主：縮小 minGap、降低 tau、提高直行車比例與流量")
        if lateral < 0.05:
            r.ok(f"側向衝突 {lateral:.0%}（SPEC 要求 < 5%）")
        else:
            r.warn(f"側向衝突 {lateral:.0%}，SPEC §4.3 要求 < 5%",
                   "降低左轉車流比例，或加入左轉保護時相（手冊第 5 章 5.3）。"
                   "另見手冊「已知的規格疑點」一節")
    if scen == "intersection_b":
        if lateral > rear:
            r.ok(f"側向衝突為主（{lateral:.0%}）")
        else:
            r.fail(f"側向只佔 {lateral:.0%}，沒有成為主要型態",
                   "路口B 要側向為主：提高次要道路匯入流量、提高 impatience、"
                   "確認 sumocfg 有開 lateral-resolution（機車鑽行）")
        if rear < 0.10:
            r.ok(f"追撞 {rear:.0%}（SPEC 要求 < 10%）")
        else:
            r.warn(f"追撞 {rear:.0%}，SPEC §4.3 要求 < 10%",
                   "拉大主線的 minGap、提高主線 tau，讓主線跟車不那麼緊")


def check_dataset(r: Report, scen: str) -> None:
    r.stage("第 7 關｜資料集能不能用")
    key = {"intersection_a": "a", "intersection_b": "b", "proxy_public": "proxy"}[scen]
    summ = ROOT / "data" / f"{key}.summary.json"
    if not summ.exists():
        r.fail(f"還沒建資料集（data/{key}.npz）",
               f"執行 python -m training.build_dataset build --scenario {key}")
        return
    s = json.loads(summ.read_text(encoding="utf-8"))
    c = s["class_counts"]
    print(f"           {s['n_samples']:,} 筆樣本：安全 {c[0]:,} / 注意 {c[1]:,} / 危險 {c[2]:,}")
    if c[2] >= 200:
        r.ok(f"危險樣本 {c[2]} 筆")
    elif c[2] > 0:
        r.warn(f"危險樣本只有 {c[2]} 筆，模型可能學不起來", "回第 5 關把危險事件拉多一點")
    else:
        r.fail("危險樣本 0 筆", "回第 5 關")
    bad = {k: v["clipped_pct"] for k, v in s["feature_ranges"].items() if v["clipped_pct"] >= 1.0}
    if bad:
        for k, v in bad.items():
            fr = s["feature_ranges"][k]
            r.warn(f"特徵 {k} 有 {v}% 被截斷（實際 {fr['actual_min']:.1f}~{fr['actual_max']:.1f}，"
                   f"設定 {fr['config_min']}~{fr['config_max']}）",
                   "把這行輸出貼給老師，由老師決定要不要改 config.yaml 的值域（學生不要自己改）")
    else:
        r.ok("所有特徵截斷比例 < 1%（值域驗收通過）")


# ---------------------------------------------------------------------------
def check_one(scen: str):
    d = SUMO / scen
    print("=" * 66)
    print(f"場景 {scen}：{TARGETS[scen]['描述']}")
    print("=" * 66)
    r = Report(scen)
    if not check_files(r, d, scen):
        return r, None
    if not check_network(r, d, scen):
        return r, None
    check_traffic(r, d, scen)
    check_ssm_config(r, d, scen)
    res = check_run(r, d, scen)
    if res is None:
        return r, None
    danger, caution = res
    check_heterogeneity(r, scen, danger, caution)
    check_dataset(r, scen)
    return r, danger


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in (*TARGETS, "all"):
        print(__doc__)
        return 2
    scens = list(TARGETS) if argv[1] == "all" else [argv[1]]
    results = {}
    for s in scens:
        r, danger = check_one(s)
        results[s] = (r, danger)

    if argv[1] == "all":
        da, db = results["intersection_a"][1], results["intersection_b"][1]
        print("\n" + "=" * 66)
        print("A、B 兩個路口夠不夠不一樣？（SPEC §4.3 驗收）")
        print("=" * 66)
        if da and db:
            ra, la = summarize(da, "A 危險事件")
            rb, lb = summarize(db, "B 危險事件")
            if ra > la and lb > rb:
                print("    [通過] A 以追撞為主、B 以側向為主，兩個路口明顯偏斜")
                print("           → 可以進入資料集與訓練階段（SPEC §10 D7）")
            else:
                print("    [失敗] 兩個路口的危險型態不夠不同")
                print("           → SPEC §4.3：分布相近時必須回頭調參，不可直接進入蒸餾階段")
        else:
            print("    [待定] 兩個路口都要先有模擬輸出才能比較")

    print("\n" + "-" * 66)
    total_f = sum(r.fails for r, _ in results.values())
    total_w = sum(r.warns for r, _ in results.values())
    if total_f == 0 and total_w == 0:
        print("全部通過。把這個畫面截圖貼到進度回報，然後做手冊第 8 章的「交付」。")
    elif total_f == 0:
        print(f"沒有失敗項目，有 {total_w} 項 [注意]。")
        print("  [注意] 不擋你往下走，但每一項都要截圖貼給老師確認——")
        print("  有些是老師要決定的事（例如 config.yaml 的值域），不是你要修的。")
    else:
        print(f"失敗 {total_f} 項、注意 {total_w} 項。")
        print("  從最上面第一個 [失敗] 開始修，修完再跑一次。一次只修一項。")
    return 1 if total_f else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
