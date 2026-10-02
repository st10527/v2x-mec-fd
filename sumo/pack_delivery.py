#!/usr/bin/env python3
"""把學生要交的成果打包成一個 zip，並先檢查有沒有漏交。

打包內容（docs/STUDENT_GUIDE.md 第 8 章的交付清單）：
  * 三個場景的原始檔（nodes / edges / routes / netccfg / sumocfg）與 stoplines.json
  * 三個資料集 data/{a,b,proxy}.npz 與各自的 summary.json
  * 公共代理資料集 data/proxy_set.npy（含 .sha256、.json）
  * 驗收結果 docs/acceptance/ 與調參紀錄 docs/tuning_log.md

不打包：states.jsonl.gz 與 ssm.xml.gz（很大，而且只要場景檔在，老師就能重跑出一模一樣的結果；
請留在自己電腦上，初賽結束前不要刪）。

用法（在專案根目錄）：
    python sumo/pack_delivery.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCEN = ["intersection_a", "intersection_b", "proxy_public"]


def required() -> list[pathlib.Path]:
    files = []
    for s in SCEN:
        d = pathlib.Path("sumo") / s
        files += [d / "nodes.nod.xml", d / "edges.edg.xml", d / "traffic.rou.xml",
                  d / f"{s}.netccfg", d / f"{s}.sumocfg", d / "stoplines.json"]
    for k in ("a", "b", "proxy"):
        files += [pathlib.Path("data") / f"{k}.npz", pathlib.Path("data") / f"{k}.summary.json"]
    files += [pathlib.Path("data/proxy_set.npy"), pathlib.Path("data/proxy_set.sha256"),
              pathlib.Path("data/proxy_set.json"), pathlib.Path("docs/tuning_log.md")]
    return files


def main() -> int:
    missing = [f for f in required() if not (ROOT / f).exists()]
    if missing:
        print("[失敗] 下列檔案還沒有，先補齊再打包：")
        for f in missing:
            print(f"    {f}")
        print("\n→ 對照 docs/STUDENT_GUIDE.md 第 8 章的交付清單")
        return 1

    # 打包前自動跑一次完整驗收，結果一起交
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    acc_dir = ROOT / "docs" / "acceptance"
    acc_dir.mkdir(parents=True, exist_ok=True)
    acc = acc_dir / f"check_{stamp}.txt"
    print("執行完整驗收（python sumo/check_scenario.py all）……")
    r = subprocess.run([sys.executable, str(ROOT / "sumo" / "check_scenario.py"), "all"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    acc.write_text(r.stdout + r.stderr, encoding="utf-8")
    tail = [ln for ln in r.stdout.splitlines() if ln.strip()][-3:]
    print("  " + "\n  ".join(tail))
    if r.returncode != 0:
        print("\n[失敗] 驗收還有 [失敗] 項目，請先修好再打包（完整結果見 "
              f"{acc.relative_to(ROOT)}）")
        return 1

    out = ROOT / f"delivery_{stamp}.zip"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for f in required():
            z.write(ROOT / f, arcname=str(f).replace("\\", "/"))
        for f in sorted(acc_dir.glob("*.txt")):
            z.write(f, arcname=str(f.relative_to(ROOT)).replace("\\", "/"))
    mb = out.stat().st_size / 1024 / 1024
    print(f"\n[完成] 已打包 {out.name}（{mb:.1f} MB）")
    print("→ 把這個 zip 交給老師（上傳到共用雲端資料夾，或用 USB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
