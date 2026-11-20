
import json
from pathlib import Path

# 專案根目錄
BASE_DIR = Path(__file__).resolve().parents[1]

# UE 紀錄檔
LOG_FILES = {
    "A": BASE_DIR / "logs" / "ue_a.jsonl",
    "B": BASE_DIR / "logs" / "ue_b.jsonl",
}


def read_latest(path):
    """讀取最後一筆有效的 UE JSONL 紀錄。"""

    if not path.exists():
        return None

    latest = None

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            # 跳過傳送失敗的紀錄
            if "error" in record:
                continue

            latest = record

    return latest


def read_new_records(path, offset):
    """從指定位置讀取新增的完整 JSONL 紀錄。"""

    if not path.exists():
        return [], 0

    if path.stat().st_size < offset:
        offset = 0

    records = []

    with path.open("rb") as f:
        f.seek(offset)

        while True:
            start = f.tell()
            line = f.readline()

            if not line:
                break

            if not line.endswith(b"\n"):
                f.seek(start)
                break

            try:
                data = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

            if not isinstance(data, dict):
                continue

            if "error" in data:
                continue

            records.append(data)

        new_offset = f.tell()

    return records, new_offset

def main():
    print("===== V2X Live Bridge Test =====")

    for node, path in LOG_FILES.items():
        data = read_latest(path)

        print(f"\nIntersection {node}")

        if data is None:
            print("No valid data")
            continue

        print("Vehicle:", data.get("vehicle_id"))
        print("AI Risk:", data.get("risk_pred"))
        print("Total Latency:", data.get("t_total_ms"), "ms")
        print("Inference Latency:", data.get("t3_inference_ms"), "ms")


if __name__ == "__main__":
    main()
