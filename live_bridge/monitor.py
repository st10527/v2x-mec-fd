import argparse
import time
from pathlib import Path

from bridge import read_new_records
from export_live import build_live_data, write_live_json

BASE_DIR = Path(__file__).resolve().parents[1]
POLL_INTERVAL = 1.0


def main():
    parser = argparse.ArgumentParser(
        description="V2X UE JSONL Monitor"
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=BASE_DIR / "logs",
        help="Directory containing UE JSONL logs"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BASE_DIR / "dashboard" / "live.json",
        help="Output path for the latest UE JSON snapshot（預設即面板讀取的 dashboard/live.json）"
    )
    args = parser.parse_args()

    log_dir = args.log_dir.resolve()

    log_files = {
        "A": log_dir / "ue_a.jsonl",
        "B": log_dir / "ue_b.jsonl",
    }

    # Skip existing records when monitoring starts.
    offsets = {
        node: path.stat().st_size if path.exists() else 0
        for node, path in log_files.items()
    }

    latest_records = {
        "A": None,
        "B": None,
    }

    print("===== V2X Live Bridge Monitor =====")
    print(f"Log directory: {log_dir}")
    print("Monitoring new UE records...")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            has_new_records = False

            for node, path in log_files.items():
                records, offsets[node] = read_new_records(
                    path, offsets[node]
                )

                for record in records:
                    has_new_records = True
                    latest_records[node] = record
                    print(
                        f"[{node}] "
                        f"Vehicle={record.get('vehicle_id')} "
                        f"Risk={record.get('risk_pred')} "
                        f"Latency={record.get('t_total_ms')} ms",
                        flush=True
                    )

            if has_new_records:
                snapshot = build_live_data(latest_records)
                write_live_json(args.output, snapshot)

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nMonitor stopped.")


if __name__ == "__main__":
    main()






