"""MEC App 端到端驗證：真的送 BSM 進去、真的取回 logits。

以 FastAPI TestClient 直接打端點，不需要 Kong / free5GC，
因此可在開發機完成。VM 上的差異只在網路路徑，應用層行為相同。
"""
from __future__ import annotations

import atexit
import base64
import os
import pathlib
import shutil
import sys
import tempfile
import uuid
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEC_NODE", "a")
os.environ.setdefault("MEC_LOG_LEVEL", "WARNING")

from fastapi.testclient import TestClient          # noqa: E402
from mec_app import config, model as M             # noqa: E402
import mec_app.app as A                            # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


def bsm(vid, x, y, speed, heading, t, lane="e1_0", accel=0.0, phase="G", inter="A"):
    return {
        "msg_id": str(uuid.uuid4()), "ts_ue": time.time(), "intersection": inter,
        "vehicle_id": vid, "position": {"x": x, "y": y}, "speed": speed,
        "accel": accel, "heading": heading, "lane_id": lane,
        "signal_phase": phase, "sim_time": t,
    }


cfg = config.load()
STEP = cfg["sumo"]["step_length"]
STEPS = cfg["window"]["steps"]

# log 與模型寫到暫存目錄，不污染專案的 logs/ 與 models/
tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / "logs").mkdir()
orig_logdir = cfg["logs"]["dir"]
cfg["logs"]["dir"] = str(tmp / "logs")

# 本測試會寫入真正的 proxy_set 與 models/a 路徑（app 以固定路徑讀取）。
# 在 VM1 上跑時，那裡放的是正式的公開代理資料集與訓練好的模型——
# 先整個搬到旁邊，測試結束（含中途出錯）時原樣放回，測試產生的檔案則刪掉。
# 暫存區放在專案內而非 /tmp：萬一行程被強制終止，檔案仍在 data/.app_test_aside/ 找得回來。
aside_dir = config.path("data", ".app_test_aside")
moved: dict[pathlib.Path, pathlib.Path] = {}
touched = [config.path(cfg["fd"]["proxy"]["path"]),
           config.path(cfg["fd"]["model_swap"]["current"].format(node="a")),
           config.path(cfg["fd"]["model_swap"]["round0"].format(node="a")),
           config.path(cfg["fd"]["published"].format(node="a", round=3)),
           M.thresholds_path("a")]
for p_ in touched:
    if p_.exists():
        aside_dir.mkdir(parents=True, exist_ok=True)
        dst = aside_dir / f"{len(moved)}_{p_.name}"
        shutil.move(p_, dst)
        moved[p_] = dst


def restore_aside() -> None:
    for p_ in touched:
        p_.unlink(missing_ok=True)                   # 測試產物
    for p_, dst in moved.items():
        shutil.move(dst, p_)
    if aside_dir.exists() and not any(aside_dir.iterdir()):
        aside_dir.rmdir()


atexit.register(restore_aside)

print("\n[app] 啟動與健康檢查")
with TestClient(A.app) as client:
    r = client.get("/healthz")
    check("GET /healthz 回 200", r.status_code == 200, str(r.status_code))
    check("healthz 標明節點為 A", r.json()["node"] == "A", str(r.json()))
    check("無 current.pt 時仍能啟動（SPEC §15.2）", A.STATE.model is not None)
    check("model_path 為 None 並如實回報", A.STATE.model_path is None)

    print("\n[app] POST /bsm 追撞情境")
    # 兩車同車道一前一後，後車高速接近 —— 規則基準線應逐步升級
    last = None
    for i in range(STEPS + 5):
        t = round(i * STEP, 2)
        gap = 40.0 - i * 1.0                      # 間距逐步縮小
        client.post("/bsm", json=bsm("lead", 0, gap, 10.0, 0.0, t))
        last = client.post("/bsm", json=bsm("ego", 0, 0, 20.0, 0.0, t))
    check("POST /bsm 回 200", last.status_code == 200, str(last.status_code))
    b = last.json()
    check("回應含 risk_pred 與 risk_rule 兩路結果", "risk_pred" in b and "risk_rule" in b)
    check("有認出衝突對手 lead", b["conflict_with"] == "lead", str(b["conflict_with"]))
    check("回應標出規則警告針對的車（同車道前車）", b.get("rule_target") == "lead", str(b.get("rule_target")))
    check("rel_dist 隨時間縮小到 ~15 m", 10 < b["rel_dist"] < 25, str(b["rel_dist"]))
    check("ttc_now 已算出且為正", b["ttc_now"] is not None and b["ttc_now"] > 0, str(b["ttc_now"]))
    check("視窗滿 20 步後模型有輸出", b["risk_pred"] in (0, 1, 2), str(b["risk_pred"]))
    check("probs 為 3 類且總和 ~1",
          b["probs"] is not None and abs(sum(b["probs"]) - 1) < 0.01, str(b["probs"]))
    check("model_ready=false（權重尚未訓練，如實回報）", b["model_ready"] is False)
    check("延遲時間戳單調遞增（SPEC §8.2）",
          b["ts_recv"] <= b["ts_window_ready"] <= b["ts_infer_done"] <= b["ts_resp"])

    print("\n[app] 規則基準線確實隨危險升高而升級")
    levels = []
    # closing speed 固定為 20-10 = 10 m/s，故 TTC = gap/10：
    #   60m -> 6.0s 安全 / 25m -> 2.5s 注意 / 12m -> 1.2s 危險
    # 注意 30m 會剛好是 TTC=3.0，落在「嚴格小於」門檻外而判安全，不可拿來當注意的例子。
    for gap in (60.0, 25.0, 12.0):
        client.post("/bsm", json=bsm("lead", 0, gap, 10.0, 0.0, 99.0))
        rr = client.post("/bsm", json=bsm("ego", 0, 0, 20.0, 0.0, 99.0))
        levels.append(rr.json()["risk_rule"])
    check("60m 安全 / 25m 注意 / 12m 危險", levels == [0, 1, 2], str(levels))

    print("\n[app] 路由與輸入防呆")
    r = client.post("/bsm", json=bsm("x", 0, 0, 10, 0, 1.0, inter="B"))
    check("送錯路口的訊息被擋下（400）", r.status_code == 400, str(r.status_code))
    r = client.post("/bsm", json={"vehicle_id": "x"})
    check("欄位不合 SPEC §5.1 回 422", r.status_code == 422, str(r.status_code))

    print("\n[app] log 寫入（SPEC §8.4）")
    logf = pathlib.Path(A.STATE.inference_log.path)
    check("inference_a.jsonl 已產生", logf.exists(), str(logf))
    if logf.exists():
        import json
        lines = [json.loads(x) for x in logf.read_text().strip().split("\n")]
        check("每筆 BSM 都留下一行", len(lines) >= STEPS * 2, str(len(lines)))
        check("欄位與 config.yaml 完全一致",
              list(lines[0]) == cfg["logs"]["inference"]["fields"], str(list(lines[0])))
        check("ts_ue 有記錄（T1 可算）", lines[-1]["ts_ue"] is not None)

    print("\n[app] FD 端點（SPEC §7.2）")
    r = client.get("/logits")
    check("proxy_set.npy 不存在時回 503 並說明原因", r.status_code == 503, str(r.status_code))
    check("503 訊息指向 SPEC §7.1", "SPEC §7.1" in r.json()["detail"])

    # 造一份合規的 proxy_set 再試一次
    n = cfg["fd"]["proxy"]["n_samples"]
    proxy_path = config.path(cfg["fd"]["proxy"]["path"])
    proxy_path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    np.save(proxy_path, rng.random((n, STEPS, cfg["features"]["dim"])).astype(np.float32))
    A.STATE._proxy = A.STATE._proxy_hash = None

    r = client.get("/logits?round=3")
    check("第 3 輪尚未發布時回 409（取用方等待重試，輪次才對得齊）",
          r.status_code == 409, str(r.status_code))
    from training import distill
    published = rng.standard_normal((n, 3)).astype(np.float32)
    distill.publish_logits("a", 3, published, cfg)

    r = client.get("/logits?round=3")
    check("GET /logits 回 200", r.status_code == 200, str(r.status_code))
    j = r.json()
    check("回傳的正是該輪發布的軟標籤（不是當下模型的輸出）",
          np.array_equal(np.frombuffer(base64.b64decode(j["logits"]), dtype=np.float32)
                         .reshape(n, 3), published))
    r0 = client.get("/logits?round=0")
    check("round=0 以目前模型即時計算", r0.status_code == 200
          and not np.array_equal(np.frombuffer(base64.b64decode(r0.json()["logits"]),
                                               dtype=np.float32).reshape(n, 3), published))
    for k in ["node_id", "round", "proxy_hash", "temperature", "n_samples",
              "n_classes", "encoding", "logits", "bytes", "ts"]:
        check(f"回應含 SPEC §7.2 欄位 {k}", k in j)
    check("bytes = 24,000（SPEC §2 Q5 的論證數字）",
          j["bytes"] == cfg["fd"]["logits"]["raw_bytes"], str(j["bytes"]))
    check("round 參數有被帶回", j["round"] == 3)
    check("temperature = 3.0", j["temperature"] == cfg["fd"]["temperature"])
    check("proxy_hash 為 sha256 格式", str(j["proxy_hash"]).startswith("sha256:"))
    dec = np.frombuffer(base64.b64decode(j["logits"]), dtype=np.float32)
    check("base64 解回 2000x3 的 float32", dec.size == n * 3, str(dec.size))
    check("base64 上線大小 ~32 KB（非 24 KB）",
          30000 < len(j["logits"]) < 34000, str(len(j["logits"])))

    r = client.get("/status")
    st = r.json()
    check("GET /status 回 200 且 ready=true", r.status_code == 200 and st["ready"] is True)
    check("status 誠實回報降級模式（SPEC §12）",
          st["registry"]["mode"] == "kong_static", str(st["registry"]["mode"]))
    check("status 帶出 self_fd_url（走 MEP Gateway）",
          st["registry"]["self_fd_url"] == "http://172.16.6.100/mec/fd/a",
          st["registry"]["self_fd_url"])

    print("\n[app] 模型熱替換（SPEC §7.5）")
    mdir = config.path("models/a"); mdir.mkdir(parents=True, exist_ok=True)
    r0 = mdir / "round_0.pt"
    import torch
    torch.save(M.build().state_dict(), r0)
    r = client.post("/model/select?round=0")
    check("POST /model/select?round=0 成功", r.status_code == 200, str(r.status_code))
    check("已切到 round_0 權重", "round_0.pt" in (A.STATE.model_path or ""), str(A.STATE.model_path))
    check("/status 的 round 跟著更新", client.get("/status").json()["round"] == 0)
    check("未校準過的權重：門檻為 None（取機率最高的類別）",
          client.get("/status").json()["danger_threshold"] is None)
    M.save_threshold("a", ["round_0"], 0.05)
    client.post("/model/select?round=0")
    check("切換權重時一併載入它的危險門檻", client.get("/status").json()["danger_threshold"] == 0.05)
    r = client.post("/model/select?round=9")
    check("不存在的輪數回 404", r.status_code == 404, str(r.status_code))
    r0.unlink()

# 清理測試產物（proxy_set 與模型由 restore_aside 在結束時放回）
shutil.rmtree(tmp, ignore_errors=True)
cfg["logs"]["dir"] = orig_logdir

print(f"\n{'='*58}\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1)
print("全部通過")
