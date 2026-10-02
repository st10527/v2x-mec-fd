#!/usr/bin/env python3
"""用 netconvert 把 nodes / edges 組成路網（SPEC §9）。Windows / macOS / Linux 通用。

每個場景資料夾裡放一個 <場景名>.netccfg，寫明要讀哪些檔、輸出到哪、號誌週期多長。
本程式只負責「對每個場景跑一次 netconvert -c」，所以要改路網設定請改 .netccfg。

建好路網後會順便產生 stoplines.json（每條進入路口車道的停止線座標），
那是 MEC App 算「距停止線距離」這個特徵要用的（SPEC §5.2 第 7 項）。

用法（在專案根目錄）：
    python sumo/build_networks.py                   # 三個場景全部重建
    python sumo/build_networks.py intersection_a    # 只建一個
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ALL = ["intersection_a", "intersection_b", "proxy_public"]


def find_tool(name: str) -> str | None:
    """先找 PATH，找不到就從 pip 裝的 eclipse-sumo 套件裡找。"""
    hit = shutil.which(name)
    if hit:
        return hit
    try:
        import sumo as sumo_pkg
    except ImportError:
        return None
    base = pathlib.Path(sumo_pkg.SUMO_HOME) / "bin" / name
    for cand in (base, base.with_suffix(".exe")):
        if cand.exists():
            return str(cand)
    return None


def main(argv: list[str]) -> int:
    netconvert = find_tool("netconvert")
    if not netconvert:
        print("[失敗] 找不到 netconvert。請先照 docs/STUDENT_GUIDE.md 第 2 章安裝 SUMO，"
              "並確認已啟用虛擬環境。")
        return 1

    sys.path.insert(0, str(HERE))
    from extract_stoplines import main as extract

    scenarios = argv[1:] or ALL
    bad = 0
    for s in scenarios:
        print(f"=== {s} ===")
        d = HERE / s
        cfg = d / f"{s}.netccfg"
        if not cfg.exists():
            print(f"  [略過] 找不到 {s}/{s}.netccfg（這個場景還沒建）")
            continue
        r = subprocess.run([netconvert, "-c", cfg.name], cwd=d,
                           capture_output=True, text=True)
        msgs = [ln for ln in (r.stdout + r.stderr).splitlines()
                if ln.strip() and not ln.startswith("Success")]
        for ln in msgs:
            print(f"  {ln}")
        net = d / f"{s}.net.xml"
        if r.returncode == 0 and net.exists():
            print(f"  [OK]   路網：{s}/{s}.net.xml")
            if extract(["extract_stoplines.py", s]) != 0:
                bad += 1
        else:
            print(f"  [失敗] netconvert 沒有產生 {s}.net.xml，請看上方錯誤訊息"
                  "（最常見：nodes / edges 裡的 id 拼錯，或 edge 的 from/to 指到不存在的 node）")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
