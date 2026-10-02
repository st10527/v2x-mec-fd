"""守住 docs/STUDENT_GUIDE.md：手冊裡給學生照抄的場景檔，必須真的能用。

學生會一字不改地照抄手冊裡的 XML。只要手冊裡有一個錯字，學生就會卡在一個
他看不懂的錯誤上。這支測試把手冊裡每一個「**檔案：`sumo/.../...`**」區塊
抽出來，放進暫存的場景資料夾，然後：

  1. 每個檔案都是合法 XML
  2. XML 註解裡沒有 "--"（XML 規格禁止；SUMO 會直接拒絕整個檔案——實際踩過）
  3. 三個場景的檔案都齊全（每場景 5 個）
  4. 用 check_scenario.py 的第 1、3、4 關驗收：檔案齊、車流比例與流量合規、SSM 設定完整
  5. 若本機有 netconvert（裝了 SUMO），連路網與停止線都實際建一次
"""
from __future__ import annotations

import pathlib
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sumo"))
import check_scenario as CS            # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


guide = (ROOT / "docs" / "STUDENT_GUIDE.md").read_text(encoding="utf-8")
blocks = re.findall(r"\*\*檔案：`(sumo/[^`]+)`\*\*\s*\n\s*```xml\n(.*?)\n```", guide, flags=re.S)

print("\n[手冊] 抽出的場景檔")
check("抽到 15 個檔案區塊（3 場景 × 5 檔）", len(blocks) == 15, str(len(blocks)))
paths = [p for p, _ in blocks]
check("沒有重複的檔案", len(set(paths)) == len(paths))

tmp = pathlib.Path(tempfile.mkdtemp())
sumo_dir = tmp / "sumo"
for rel, body in blocks:
    dst = tmp / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(body + "\n", encoding="utf-8")

print("\n[手冊] 每個檔案的 XML")
for rel, body in blocks:
    try:
        ET.fromstring(body.encode("utf-8"))
        ok, err = True, ""
    except ET.ParseError as e:
        ok, err = False, str(e)
    check(f"{rel} 是合法 XML", ok, err)
    bad = [c for c in re.findall(r"<!--(.*?)-->", body, flags=re.S) if "--" in c]
    check(f"{rel} 註解裡沒有 '--'", not bad, bad[0][:40] if bad else "")

print("\n[手冊] 用驗收工具檢查（第 1、3、4 關）")
for scen in ("intersection_a", "intersection_b", "proxy_public"):
    d = sumo_dir / scen
    r = CS.Report(scen)
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        files_ok = CS.check_files(r, d, scen)
        CS.check_traffic(r, d, scen)
        CS.check_ssm_config(r, d, scen)
    check(f"{scen}：檔案齊全", files_ok)
    check(f"{scen}：第 1、3、4 關無失敗、無注意", r.fails == 0 and r.warns == 0,
          f"失敗 {r.fails}、注意 {r.warns}")

print("\n[手冊] 實際建路網（需要本機裝了 SUMO）")
sys.path.insert(0, str(ROOT / "sumo"))
from build_networks import find_tool   # noqa: E402
nc = find_tool("netconvert")
if nc is None:
    print("  SKIP  本機沒有 netconvert（開發機未裝 SUMO）；VM2 與學生環境會跑到這段")
else:
    import subprocess
    expect = {"intersection_a": 8, "intersection_b": 3, "proxy_public": 4}
    for scen, n_lanes in expect.items():
        d = sumo_dir / scen
        r = subprocess.run([nc, "-c", f"{scen}.netccfg"], cwd=d, capture_output=True, text=True)
        check(f"{scen}：netconvert 建網成功", r.returncode == 0 and (d / f"{scen}.net.xml").exists(),
              (r.stderr or r.stdout)[-200:])
        shutil.copy2(ROOT / "sumo" / "extract_stoplines.py", sumo_dir / "extract_stoplines.py")
        r = subprocess.run([sys.executable, str(sumo_dir / "extract_stoplines.py"), scen],
                           capture_output=True, text=True)
        import json
        sl = d / "stoplines.json"
        got = len(json.loads(sl.read_text())) if sl.exists() else -1
        check(f"{scen}：停止線 {n_lanes} 條（與手冊寫的一致）", got == n_lanes, str(got))

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{'='*58}\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1)
print("全部通過")
