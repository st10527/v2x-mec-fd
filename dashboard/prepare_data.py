
import json
from pathlib import Path
from collections import Counter
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
OUTPUT = ROOT / "dashboard" / "data.json"


def analyze(node):
    path = LOGS / f"ue_{node.lower()}.jsonl"
    rows = []

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))

    successful = [
        row for row in rows
        if "error" not in row and row.get("risk_pred") in (0, 1, 2)
    ]

    risks = Counter(row["risk_pred"] for row in successful)

    def latency(field):
        values = [
            row[field] for row in successful
            if isinstance(row.get(field), (int, float))
            and row[field] >= 0
        ]
        return round(median(values), 3) if values else None

    alerts = [
        {
            "vehicle_id": row.get("vehicle_id"),
            "risk": row["risk_pred"],
            "ttc_now": row.get("ttc_now"),
            "t_total_ms": row.get("t_total_ms")
        }
        for row in successful
        if row["risk_pred"] in (1, 2)
    ]

    return {
        "total": len(rows),
        "success": len(successful),
        "failed": sum("error" in row for row in rows),
        "risk": {
            "safe": risks[0],
            "caution": risks[1],
            "danger": risks[2]
        },
        "median_total_ms": latency("t_total_ms"),
        "median_inference_ms": latency("t3_inference_ms"),
        "alerts": alerts
    }


data = {
    "source": "historical_ue_logs",
    "live": False,
    "intersections": {
        "A": analyze("A"),
        "B": analyze("B")
    }
}

with OUTPUT.open("w", encoding="utf-8") as file:
    json.dump(data, file, ensure_ascii=False, indent=2)

print("資料整理完成：", OUTPUT)

for node, result in data["intersections"].items():
    print(
        f"路口 {node}:",
        f"成功 {result['success']} 筆,",
        f"注意 {result['risk']['caution']} 筆,",
        f"危險 {result['risk']['danger']} 筆"
    )
