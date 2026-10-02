"""JSON Lines 實驗紀錄（SPEC §8.4）。

SPEC §15.4：log 先於功能。任何新模組先把欄位寫好再寫邏輯——
實驗數據是初賽第 6 項繳交要求，補寫不回來。

三個檔的欄位定義在 config.yaml 的 logs 區，本模組對照該定義檢查每一筆，
欄位多寫少寫都會直接報錯。畫圖腳本只讀這三個檔（SPEC §8.4），
不得從程式內部直接產圖。
"""
from __future__ import annotations

import json
import pathlib
import threading
from typing import Any

from . import config


class JsonlWriter:
    """單一 jsonl 檔的寫入器，欄位受 config.yaml 約束。"""

    def __init__(self, kind: str, node: str | None = None,
                 cfg: dict | None = None) -> None:
        c = cfg or config.load()
        spec = c["logs"][kind]
        name = spec["file"]
        if "{node}" in name:
            if node is None:
                raise ValueError(f"log 種類 {kind!r} 需要 node 參數")
            name = name.format(node=str(node).lower())
        self.kind = kind
        self.fields: list[str] = list(spec["fields"])
        self.path = config.path(c["logs"]["dir"], name)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, **record: Any) -> None:
        """寫一筆。欄位必須與 config.yaml 的定義完全一致。"""
        missing = [f for f in self.fields if f not in record]
        extra = [k for k in record if k not in self.fields]
        if missing or extra:
            raise ValueError(
                f"{self.kind} log 欄位不符 config.yaml："
                f"缺少 {missing}、多出 {extra}。"
                "若確需增減欄位，請同時更新 config.yaml 與 docs/SPEC.md §8.4。")
        line = json.dumps({f: record[f] for f in self.fields},
                          ensure_ascii=False, separators=(",", ":"))
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def read(kind: str, node: str | None = None,
         cfg: dict | None = None) -> list[dict[str, Any]]:
    """讀回整份 jsonl，供 analysis/ 下的繪圖腳本使用。"""
    c = cfg or config.load()
    spec = c["logs"][kind]
    name = spec["file"]
    if "{node}" in name:
        name = name.format(node=str(node).lower())
    p: pathlib.Path = config.path(c["logs"]["dir"], name)
    if not p.exists():
        return []
    out = []
    with open(p, "r", encoding="utf-8") as fh:
        for ln, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError as e:
                raise ValueError(f"{p}:{ln} 不是合法 JSON：{e}") from e
    return out
