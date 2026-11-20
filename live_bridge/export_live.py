import json
import os
import tempfile
from pathlib import Path


def write_live_json(output_path, data):
    """Safely write a complete JSON file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=".live_",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(data, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")

        os.replace(temp_path, output_path)

    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def build_live_data(latest_records):
    """Build a snapshot of the latest UE records."""
    from datetime import datetime, timezone

    return {
        "source": "local_simulation",
        "live": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "intersections": {
            "A": latest_records.get("A"),
            "B": latest_records.get("B"),
        },
    }
