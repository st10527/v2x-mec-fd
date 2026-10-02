#!/usr/bin/env python3
"""由 config.yaml 產生 deploy/topology.env，供 shell 腳本 source。

SPEC §15.5 的精神：同一個值只有一個來源。網路位址寫在 config.yaml（Python 端
與 MEC App 讀它），shell 腳本不另外手寫一份，而是從這裡產生——否則兩邊遲早漂移，
而且漂移時不會報錯，只會在 UE 連不上時浪費半天。

用法：python3 deploy/gen_topology_env.py [輸出路徑]
"""
from __future__ import annotations

import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    net = cfg["network"]
    nodes = cfg["nodes"]
    out = pathlib.Path(argv[1]) if len(argv) > 1 else ROOT / "deploy" / "topology.env"

    lines = [
        "# 由 deploy/gen_topology_env.py 從 config.yaml 產生，請勿手動編輯。",
        "# 要改位址請改 config.yaml 的 network / nodes 區，再重跑產生器。",
        "",
        f'VM1_HOSTONLY="{net["vm1"]["hostonly"]}"   # free5GC + OAI-MEP + MEC Apps',
        f'VM2_HOSTONLY="{net["vm2"]["hostonly"]}"   # UERANSIM + SUMO + UE',
        f'N6_APP="{net["mec_bind"]}"        # MEC App 綁定位址',
        f'N6_GW="{net["mep_gateway"]}"       # MEP Gateway 對外入口',
        f'UE_IF="{net["ue_tun"]}"          # UE tun 介面',
        "",
        "# 各 MEC 節點的 port（SPEC §3.3）",
    ]
    for key in sorted(nodes):
        lines.append(f'NODE_{key.upper()}_PORT="{nodes[key]["port"]}"')
    lines.append("")
    lines.append("# 健康檢查與展示面板")
    lines.append(f'HEALTH_PORT="{cfg["services"]["health"]["port"]}"')
    lines.append(f'DASH_PORT="{cfg["services"]["dashboard"]["port"]}"')
    lines.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已產生 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
