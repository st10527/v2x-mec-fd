"""三張核心圖表的共用樣式（SPEC §8.3）。

SPEC §8.4：畫圖腳本只讀 logs/ 下的 jsonl，不得從程式內部直接產圖。
所有圖都由本目錄的腳本從落地的 log 重新產生，確保可重現性——
決賽要繳完整程式碼供評審辨識原創性，圖表能被重跑出來這件事本身就是證據。
"""
from __future__ import annotations

import pathlib

import matplotlib

matplotlib.use("Agg")                      # 無頭環境（VM / CI）也能出圖
import matplotlib.pyplot as plt            # noqa: E402

from mec_app import config                 # noqa: E402

# 中文標籤在多數 Linux 上會缺字型而變成豆腐格。優先找系統中文字型，
# 找不到就退回英文標籤——圖是要放進企劃書的，缺字比英文難看得多。
# 順序即優先序。macOS 的 PingFang / Heiti 與 Linux 的 Noto CJK 字數較全；
# Microsoft JhengHei 排在後面，因為在部分機器上缺字（例如「口」會變豆腐格）。
_CJK = ["PingFang TC", "Heiti TC", "Noto Sans CJK TC", "Noto Sans CJK SC",
        "Source Han Sans TW", "Arial Unicode MS", "Microsoft JhengHei"]


def setup() -> bool:
    """設定樣式，回傳是否有可用的中文字型。"""
    from matplotlib import font_manager
    have = {f.name for f in font_manager.fontManager.ttflist}
    cjk = [f for f in _CJK if f in have]
    plt.rcParams.update({
        "figure.dpi": 150, "savefig.dpi": 200, "savefig.bbox": "tight",
        "font.size": 11, "axes.grid": True, "grid.alpha": 0.3,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.unicode_minus": False,
    })
    if cjk:
        plt.rcParams["font.sans-serif"] = cjk + ["DejaVu Sans"]
    return bool(cjk)


def label(zh: str, en: str, cjk_ok: bool) -> str:
    return zh if cjk_ok else en


def save(fig, name: str) -> pathlib.Path:
    out = config.path("figures", name)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    print(f"已輸出 {out}")
    return out
