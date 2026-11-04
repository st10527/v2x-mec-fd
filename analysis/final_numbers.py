"""企劃書／簡報引用的主要數字，一次算出並寫入 models/final_numbers.json。

初賽時這份 json 是逐項手動彙整的；決賽改成一支程式，換環境、換權重後一行指令重算，
文件裡的每個數字都能指回這裡。

數字與口徑（皆用部署中模型 current.pt，門檻為各路口以自己驗證資料校準的 current 門檻）：
  A_*   號誌路口 A 的測試時段（依時間切分的最後 15%）
  B_*   無號誌路口 B 的 6 段獨立車流（sumo/intersection_b/extra/seed101–106，訓練從未看過）
  AB_*  A 的模型直接搬到 B：沿用 A 的門檻（home）／以 B 的驗證資料重新校準（local）
  rule  平台範例規則（只看同車道前車）在同一批事件上的召回

前置（只需一次，約 3 分鐘）：
    python -m analysis.final_numbers --collect     # 錄 B 路口 6 段獨立車流
用法：
    python -m analysis.final_numbers
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from mec_app import config, model as M
from training import events as EV
from training.adapt import EXTRA, evaluate_on


def collect_extra(cfg: dict) -> None:
    for sp in EXTRA:
        d = EV.extra_dir("b", sp, cfg)
        if (d / "states.jsonl.gz").exists() and (d / "ssm.xml.gz").exists():
            print(f"  {sp} 已存在，略過")
            continue
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "training.build_dataset", "collect", "--scenario", "b",
                        "--seed", sp.removeprefix("seed"),
                        "--out", str(d / "states.jsonl.gz"), "--ssm-out", str(d / "ssm.xml.gz")],
                       check=True)


def rule_pooled(site: str, splits: list[str], cfg: dict) -> dict:
    lb = float(cfg["label"]["horizon_seconds"])
    caught = events = 0
    for sp in splits:
        m = EV.rule_metrics(EV.load_replay(site, sp, cfg), lb)
        caught += m["caught"]; events += m["events"]
    return {"recall": caught / max(events, 1), "events": events}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collect", action="store_true", help="先錄 B 路口的 6 段獨立車流")
    a = ap.parse_args(argv)
    cfg = config.load()
    if a.collect:
        collect_extra(cfg)
        return 0

    lb = float(cfg["label"]["horizon_seconds"])
    ma, mb = EV.load_weights("a", "current"), EV.load_weights("b", "current")
    tha, thb = M.load_threshold("a", "current"), M.load_threshold("b", "current")

    rep = EV.load_replay("a", "test", cfg)
    s = EV.danger_scores(ma, rep)
    soft = EV.model_metrics(rep, s, tha["caution"], lb, "caution")
    strong = EV.model_metrics(rep, s, tha["danger"], lb, "danger")
    A = {"events": soft["events"], "soft_recall": soft["recall"],
         "soft_false_per_passage": soft["false_per_passage"],
         "strong_recall": strong["recall"], "strong_precision": strong["alert_precision"],
         "rule_recall": EV.rule_metrics(rep, lb)["recall"], "tau": tha}

    B = {**evaluate_on(mb, "b", EXTRA, thb, cfg), "rule_recall": rule_pooled("b", EXTRA, cfg)["recall"], "tau": thb}

    val_b = EV.load_replay("b", "val", cfg)
    th_local = EV.calibrate_two_tier(val_b, EV.danger_scores(ma, val_b), cfg)
    th_local = {"caution": th_local["caution"], "danger": th_local["danger"]}
    AB = {"home": {**evaluate_on(ma, "b", EXTRA, tha, cfg), "tau": tha},
          "local": {**evaluate_on(ma, "b", EXTRA, th_local, cfg), "tau": th_local}}

    out = {"A_test": A, "B_extra": B, "A_model_on_B": AB,
           "model_params": M.param_count(ma), "model_fp32_bytes": M.fp32_bytes(ma)}
    p = config.path("models/final_numbers.json")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    pct = lambda x: f"{100 * x:.1f}%"
    print(f"號誌路口 A（測試時段 {A['events']} 件）：注意召回 {pct(A['soft_recall'])}、"
          f"每次通過誤報 {A['soft_false_per_passage']:.2f}、強警報精準度 {pct(A['strong_precision'])}"
          f"｜平台規則 {pct(A['rule_recall'])}")
    print(f"無號誌路口 B（6 段獨立車流 {B['events']} 件）：注意召回 {pct(B['soft_recall'])}、"
          f"每次通過誤報 {B['soft_false_per_passage']:.2f}、強警報召回 {pct(B['strong_recall'])}、"
          f"精準度 {pct(B['strong_precision'])}｜平台規則 {pct(B['rule_recall'])}")
    print(f"A 模型搬到 B：沿用 A 門檻 注意召回 {pct(AB['home']['soft_recall'])}；"
          f"在地校準 {pct(AB['local']['soft_recall'])}（每次通過誤報 {AB['local']['soft_false_per_passage']:.2f}）")
    print(f"模型 {out['model_params']:,} 參數、{out['model_fp32_bytes'] / 1024:.0f} KB")
    print(f"已寫入 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
