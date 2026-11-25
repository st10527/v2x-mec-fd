"""把兩個路口的路網匯出成面板畫地圖用的 dashboard/geometry.json。

車輛位置（MEC 推論紀錄的 x、y）是 SUMO 座標，單位公尺；這裡的車道形狀用同一套座標，
面板只要把兩者用同一個比例換成畫面座標，車就會落在路上。

輸出格式：
  {"A": {"bounds": [xmin, ymin, xmax, ymax],
         "lanes": [{"id": "N2C_0", "internal": false, "width": 3.2, "shape": [[x, y], ...]}, ...]},
   "B": {...}}
  internal = true 是路口內部的轉彎車道（SUMO 的 :C_* 車道）。

路網（*.net.xml）不在 GitHub 上，執行前需先有路網檔（VM 上已有，或照 STUDENT_GUIDE 重建）。
路網改了才需要重跑：
    python dashboard/export_geometry.py
"""
from __future__ import annotations

import json
import pathlib
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
NETS = {"A": ROOT / "sumo" / "intersection_a" / "intersection_a.net.xml",
        "B": ROOT / "sumo" / "intersection_b" / "intersection_b.net.xml"}
OUT = ROOT / "dashboard" / "geometry.json"


def lanes_of(net: pathlib.Path) -> list[dict]:
    lanes = []
    for edge in ET.parse(net).getroot().iter("edge"):
        internal = edge.get("function") == "internal"
        for lane in edge.iter("lane"):
            shape = [[round(float(v), 2) for v in p.split(",")[:2]] for p in lane.get("shape", "").split()]
            if len(shape) >= 2:
                lanes.append({"id": lane.get("id"), "internal": internal,
                              "width": float(lane.get("width", 3.2)), "shape": shape})
    return lanes


def main() -> None:
    out = {}
    for name, net in NETS.items():
        if not net.exists():
            raise SystemExit(f"找不到 {net}：路網檔不在 GitHub 上，請在 VM 上執行，或先照 STUDENT_GUIDE 建路網")
        lanes = lanes_of(net)
        xs = [p[0] for ln in lanes for p in ln["shape"]]
        ys = [p[1] for ln in lanes for p in ln["shape"]]
        out[name] = {"bounds": [min(xs), min(ys), max(xs), max(ys)], "lanes": lanes}
        print(f"路口 {name}：{len(lanes)} 條車道，範圍 x {min(xs):.0f}–{max(xs):.0f}、y {min(ys):.0f}–{max(ys):.0f} 公尺")
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"已輸出 {OUT}（{OUT.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
