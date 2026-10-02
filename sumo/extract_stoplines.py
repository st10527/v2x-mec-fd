#!/usr/bin/env python3
"""從路網抽出「每條進入路口的車道」的停止線座標，寫成 stoplines.json。

為什麼需要這個檔：MEC App 收到的 BSM（SPEC §5.1）只有車輛座標與車道 id，
沒有「距停止線多遠」。第 7 個特徵 dist_to_stopline 必須由 MEC 端用路網幾何換算，
換算用的就是這個檔（見 mec_app/features.py 檔頭註 (3)）。

停止線 = 車道形狀（shape）的最後一個點，也就是車道進入路口的那一端。
只取「真正的路口」：相鄰節點數 >= 3 的 junction。場景邊緣的端點只連一條路，
不算路口，不需要停止線。

用法：python sumo/extract_stoplines.py intersection_a
"""
from __future__ import annotations

import json
import pathlib
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法：python sumo/extract_stoplines.py <場景資料夾名>")
        return 2
    try:
        import sumolib
    except ImportError:
        print("[FAIL] 找不到 sumolib。請先照 docs/STUDENT_GUIDE.md 第 2 章安裝。")
        return 1

    scen = argv[1]
    here = pathlib.Path(__file__).resolve().parent / scen
    net_path = here / f"{scen}.net.xml"
    if not net_path.exists():
        print(f"[FAIL] 找不到 {net_path}，請先跑 bash sumo/build_networks.sh {scen}")
        return 1

    net = sumolib.net.readNet(str(net_path), withInternal=False)
    stoplines: dict[str, list[float]] = {}
    junctions = []
    for node in net.getNodes():
        neighbours = {e.getFromNode().getID() for e in node.getIncoming()} | \
                     {e.getToNode().getID() for e in node.getOutgoing()}
        neighbours.discard(node.getID())
        if len(neighbours) < 3:          # 場景邊緣的端點，不是路口
            continue
        junctions.append(node.getID())
        for edge in node.getIncoming():
            for lane in edge.getLanes():
                x, y = lane.getShape()[-1]
                stoplines[lane.getID()] = [round(x, 2), round(y, 2)]

    if not stoplines:
        print("[FAIL] 路網裡找不到任何路口（相鄰節點數 >= 3 的 junction）")
        return 1

    out = here / "stoplines.json"
    out.write_text(json.dumps(stoplines, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK]   停止線：{len(stoplines)} 條車道、路口 {junctions} -> {scen}/stoplines.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
