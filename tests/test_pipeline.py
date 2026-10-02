"""訓練 -> 蒸餾 -> 評估的整條管線驗證（合成資料）。

=== 這支測試用的是合成資料，不是實驗結果 ===
目的是在真實 SUMO 資料到位前，先確認管線的**機制**是對的：
產物有沒有存、log 欄位對不對、輪數有沒有跑滿、熱替換會不會觸發、
proxy_hash 不符會不會擋下。

它**不**驗證「蒸餾有沒有帶來增益」——那取決於兩個路口的真實異質性
（SPEC §4.3），只有真資料能回答。任何寫進企劃書的數字都必須來自
SUMO 實機資料，合成資料的分數一律不得引用（見 docs/ai-disclosure.md 的紅線）。

測試結束會清掉自己產生的所有 data/ 與 models/ 檔案，避免假資料被誤認成成果。
"""
from __future__ import annotations

import io
import json
import contextlib
import pathlib
import shutil
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mec_app import config, model as M                         # noqa: E402
from training import dataset as D, distill, evaluate as E, metrics  # noqa: E402
from training import train_local as TL                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


cfg = config.load()
STEPS, DIM = cfg["window"]["steps"], cfg["features"]["dim"]

# --- 縮小訓練規模，讓測試幾秒跑完（只改記憶體中的設定，不動 config.yaml）---
cfg["train"]["epochs_pretrain"] = 3
cfg["train"]["epochs_per_round"] = 1
cfg["train"]["epochs_distill"] = 1
cfg["train"]["batch_size"] = 64
cfg["fd"]["rounds"] = 2
cfg["fd"]["proxy"]["n_samples"] = 300

# --- 備份既有檔案，測試結束還原 ---
# 本測試寫的是真正的 data/、models/、logs/ 路徑。在 VM1 上，那裡是正式資料集與訓練好的模型，
# 所以：備份檔名用完整相對路徑（models/a/round_0.pt 與 models/b/round_0.pt 不會互蓋）、
# 收尾時逐一還原，只刪「測試開始時不存在」的檔案。任何一項還原失敗就保留備份目錄並大聲報出。
created: list[pathlib.Path] = []
backups: dict[pathlib.Path, pathlib.Path] = {}
bak_dir = pathlib.Path(config.path("data")) / ".pipeline_test_backup"
models_before = {f for f in config.path("models").rglob("*") if f.is_file()}


def guard(p: pathlib.Path):
    p = pathlib.Path(p)
    if p.exists():
        bak_dir.mkdir(parents=True, exist_ok=True)
        b = bak_dir / str(p.relative_to(config.ROOT)).replace("/", "__")
        assert not b.exists() or p in backups, f"備份撞名：{b}"
        shutil.copy2(p, b)
        backups[p] = b
    else:
        created.append(p)
    return p


def synth(n: int, node: str, seed: int):
    """造出兩個路口型態明顯不同的合成資料。

    A 的風險由 rel_dist / rel_speed 決定（追撞型），
    B 的風險由 heading_diff / same_lane 決定（側向匯入型）——
    刻意對應 SPEC §4.3 的異質性設計，讓 A 訓練的模型在 B 上本來就會差。
    """
    rng = np.random.default_rng(seed)
    X = rng.random((n, STEPS, DIM)).astype(np.float32)
    last = X[:, -1, :]
    if node == "a":
        score = (1 - last[:, 2]) * 0.7 + last[:, 3] * 0.3      # 近且接近快 -> 危險
    else:
        score = (1 - last[:, 4]) * 0.6 + last[:, 5] * 0.4      # 對向且同道 -> 危險
    y = np.where(score > 0.72, 2, np.where(score > 0.55, 1, 0)).astype(np.int64)
    t = np.sort(rng.random(n).astype(np.float32) * 3600)
    vid = np.array([f"veh_{i % 40:04d}" for i in range(n)], dtype="<U16")
    return X, y, t, vid


print("\n[setup] 造合成資料")
for node in ("a", "b"):
    scen = config.node(node)["scenario"]
    p = guard(config.path(cfg["dataset"]["out"].format(scenario=scen)))
    D.save(p, *synth(2400, node, seed=11 if node == "a" else 22))
    s = D.load(p)
    check(f"節點 {node.upper()} 資料集 {len(s)} 筆，三類齊全",
          len(s) == 2400 and len(np.unique(s.y)) == 3, str(np.bincount(s.y)))

proxy_p = guard(config.path(cfg["fd"]["proxy"]["path"]))
rng = np.random.default_rng(99)
np.save(proxy_p, rng.random((300, STEPS, DIM)).astype(np.float32))
check("proxy_set.npy 已備妥（兩端共用同一份）", proxy_p.exists())

for node in ("a", "b"):
    for r in range(0, 3):
        guard(config.path(f"models/{node}/round_{r}.pt"))
    guard(config.path(f"models/{node}/current.pt"))
    guard(config.path(f"models/{node}/round_0.json"))
    guard(config.path(f"models/{node}/distill_summary.json"))
    guard(config.path(f"models/{node}/thresholds.json"))
guard(config.path("models/evaluation.json"))
fd_log_p = guard(config.path("logs/fd_rounds.jsonl"))
fd_log_p.unlink(missing_ok=True)          # 已備份；從空檔開始才數得準行數

try:
    print("\n[train_local] 第 0 輪單獨訓練基準（SPEC §7.4）")
    for node in ("a", "b"):
        with contextlib.redirect_stdout(io.StringIO()):
            TL.main(["--node", node])
        r0 = config.path(f"models/{node}/round_0.pt")
        cur = config.path(f"models/{node}/current.pt")
        check(f"{node}: round_0.pt 已存（基準，永不覆蓋）", r0.exists())
        check(f"{node}: current.pt 已存（推論端載入）", cur.exists())
        meta = json.loads(config.path(f"models/{node}/round_0.json").read_text())
        check(f"{node}: 評估結果一併存檔", "val" in meta and "test" in meta)
        check(f"{node}: 有記錄 macro-F1", 0.0 <= meta["test"]["macro_f1"] <= 1.0,
              str(meta["test"]["macro_f1"]))

    print("\n[evaluate] 跨路口矩陣（SPEC §8.1 主指標）")
    m0 = E.evaluate_matrix(0, cfg)
    check("2x2 四格都算出來", len(m0["cells"]) == 4, str(list(m0["cells"])))
    for k in ("a->a", "a->b", "b->a", "b->b"):
        check(f"含 {k}", k in m0["cells"])
    local = (m0["cells"]["a->a"]["macro_f1"] + m0["cells"]["b->b"]["macro_f1"]) / 2
    cross = (m0["cells"]["a->b"]["macro_f1"] + m0["cells"]["b->a"]["macro_f1"]) / 2
    check("單獨訓練下本地分數高於跨路口分數（驗證 SPEC §2 Q3 的前提）",
          local > cross, f"本地 {local:.3f} vs 跨路口 {cross:.3f}")

    print("\n[distill] 蒸餾回合（SPEC §7.4 七步驟）")
    mtime_before = config.path("models/a/current.pt").stat().st_mtime
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        res = distill.run("a", local_peer=True, cfg=cfg)
        distill.run("b", local_peer=True, cfg=cfg)   # 兩端都跑，矩陣才填得滿
    out = buf.getvalue()
    check("跑滿設定的輪數", len(res) == 2, str(len(res)))
    check("每輪都存下 round_N.pt",
          all(config.path(f"models/a/round_{r}.pt").exists() for r in (1, 2)))
    check("current.pt 被更新（觸發 MEC App 熱替換）",
          config.path("models/a/current.pt").stat().st_mtime > mtime_before)
    check("round_0.pt 沒有被覆蓋（基準保住）",
          json.loads(config.path("models/a/round_0.json").read_text())["round"] == 0)
    check("每輪傳輸量 = 300x3x4 = 3,600 B（本測試縮小過的 proxy）",
          all(r["raw_bytes"] == 300 * 3 * 4 for r in res), str(res[0]["raw_bytes"]))
    check("蒸餾損失有拆出 CE 與 KL 兩項", "CE" in out and "KL" in out)

    print("\n[distill] fd_rounds.jsonl（SPEC §8.4）")
    rows = [json.loads(x) for x in fd_log_p.read_text().strip().split("\n")]
    # 每個節點：train_local 寫第 0 輪基準 + distill 寫第 1–2 輪 = 3 行
    check("兩個節點各三輪（含第 0 輪基準），共六行", len(rows) == 6, str(len(rows)))
    check("欄位與 config.yaml 完全一致",
          list(rows[0]) == cfg["logs"]["fd_rounds"]["fields"], str(list(rows[0])))
    check("local_f1 與 cross_f1 都有值",
          all(r["local_f1"] is not None and r["cross_f1"] is not None for r in rows))
    check("每個節點的 round 都由 0 遞增（收斂曲線 x 軸從 0 開始）",
          [r["round"] for r in rows if r["node"] == "a"] == [0, 1, 2]
          and [r["round"] for r in rows if r["node"] == "b"] == [0, 1, 2],
          str([(r["node"], r["round"]) for r in rows]))
    check("node 欄位區分得出是哪一端送的",
          {r["node"] for r in rows} == {"a", "b"})
    check("第 0 輪 bytes_sent = 0（沒有任何交換，如實記錄）",
          all(r["bytes_sent"] == 0 for r in rows if r["round"] == 0))
    check("第 1 輪起 bytes_sent 有記錄（SPEC §8.3 傳輸量圖的來源）",
          all(r["bytes_sent"] == 3600 for r in rows if r["round"] > 0))
    check("第 0 輪的 cross_f1 即單獨訓練基準（收斂曲線的水平虛線）",
          all(isinstance(r["cross_f1"], float) for r in rows if r["round"] == 0))

    print("\n[distill] proxy_hash 不符時必須拒絕該輪（SPEC §7.2）")
    orig = distill.local_peer_logits
    distill.local_peer_logits = lambda n, r, c, px: (
        np.zeros((300, 3), np.float32), 3600, "sha256:deadbeef")
    try:
        distill.run("a", rounds=1, local_peer=True, cfg=cfg)
        ok, msg = False, ""
    except RuntimeError as e:
        ok, msg = True, str(e)
    finally:
        distill.local_peer_logits = orig
    check("hash 不符直接中止並說明原因", ok and "proxy_hash" in msg, msg[:80])
    check("錯誤訊息指向 SPEC §7.2", "SPEC §7.2" in msg)

    print("\n[distill] 非對稱協同：α = 0 的節點只提供、不吸收")
    asym = json.loads(json.dumps(cfg)); asym["fd"]["alpha"] = {"a": 0.0, "b": 0.5}
    check("alpha_for 讀得到各節點的 α",
          distill.alpha_for("a", asym) == 0.0 and distill.alpha_for("b", asym) == 0.5
          and distill.alpha_for("a", {"fd": {"alpha": 0.3}}) == 0.3)

    def _must_not_fetch(*_a, **_k):
        raise AssertionError("α = 0 不應該去取對方軟標籤")
    orig = distill.local_peer_logits
    distill.local_peer_logits = _must_not_fetch
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            res = distill.run("a", rounds=1, local_peer=True, cfg=asym)
        ok = True
    except AssertionError:
        ok, res = False, []
    finally:
        distill.local_peer_logits = orig
    check("α = 0 時不取對方軟標籤，但本地訓練照常完成", ok and len(res) == 1)
    check("α = 0 時本輪接收的線上 bytes 記為 0", ok and res[0]["wire_bytes"] == 0)
    check("仍照常發布自己的軟標籤給對方",
          config.path(cfg["fd"]["published"].format(node="a", round=1)).exists())

    print("\n[distill] 輪次對齊：對方還沒發布本輪就等待，拿到的必須是同一輪")
    import base64 as _b64
    import types as _types
    calls = []
    good = np.arange(300 * 3, dtype=np.float32).reshape(300, 3)

    def _fake_get(url, params=None, timeout=None):
        calls.append(params["round"])
        if len(calls) < 3:                                   # 前兩次：對方還在訓練
            return _types.SimpleNamespace(status_code=409, json=lambda: {})
        body = {"round": params["round"], "n_samples": 300, "n_classes": 3,
                "proxy_hash": "sha256:x", "logits": _b64.b64encode(good.tobytes()).decode()}
        return _types.SimpleNamespace(status_code=200, json=lambda: body,
                                      raise_for_status=lambda: None)

    fast = json.loads(json.dumps(cfg)); fast["fd"]["poll_s"] = 0.01
    real_httpx = sys.modules.get("httpx")
    sys.modules["httpx"] = _types.SimpleNamespace(get=_fake_get)
    try:
        arr, nbytes, _ = distill.fetch_peer_logits("a", 2, fast)
    finally:
        if real_httpx is not None:
            sys.modules["httpx"] = real_httpx
    check("409 時等待重試，第三次才取到", calls == [2, 2, 2], str(calls))
    check("取到的是對方該輪發布的內容", np.array_equal(arr, good))
    check("線上大小以 base64 計（約原始的 4/3）", nbytes == len(_b64.b64encode(good.tobytes())), str(nbytes))
    p_pub = distill.publish_logits("a", 7, good, cfg)
    check("發布的軟標籤可原樣讀回", np.array_equal(np.load(p_pub), good))
    distill.clear_published("a", cfg)
    check("重新開跑前清掉上次發布的檔案（對方不會拿到舊的）", not p_pub.exists())

    print("\n[distill] 代理資料不得改寫 BatchNorm 統計（否則本地表現崩掉）")
    import torch as _torch
    mb = M.build(); mb.train()
    bn = [x for x in mb.modules() if isinstance(x, _torch.nn.BatchNorm1d)][0]
    before = bn.running_mean.clone()
    with distill.frozen_bn_stats(mb):
        mb(_torch.randn(16, STEPS, DIM) * 5 + 3).sum().backward()
    check("代理資料前向後 BN running_mean 不變", _torch.equal(before, bn.running_mean))
    check("梯度照常回傳", mb.net[0].weight.grad is not None)
    check("離開後 momentum 還原", bn.momentum == 0.1, str(bn.momentum))

    print("\n[evaluate] 輪次比較與 SPEC §12 增益警示")
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        E.main(["--rounds", "0", "2"])
    txt = buf.getvalue()
    check("印出 round 0 的矩陣", "--- round 0 ---" in txt)
    check("印出 round 2 的矩陣", "--- round 2 ---" in txt)
    check("兩個跨路口方向都有列出", "a->b:" in txt and "b->a:" in txt, txt[-300:])
    check("印出跨路口增益且帶正負號", "跨路口增益" in txt and ("+" in txt or "-" in txt))
    check("有報告跨路口漏報率（危險類 recall）", "漏報率" in txt)
    check("evaluation.json 已寫出", config.path("models/evaluation.json").exists())

    # SPEC §12：增益 < 2% 時必須提出行動建議。直接驗證該分支。
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        flat = [E.evaluate_matrix(0, cfg), E.evaluate_matrix(0, cfg)]
        for r in flat:
            E.print_matrix(r, cfg)
        base, last = flat
        worst = min(last["cells"][k]["macro_f1"] - base["cells"][k]["macro_f1"]
                    for k in ("a->b", "b->a"))
    check("同一輪自比增益為 0（構造出 SPEC §12 的徵兆）", worst == 0.0, str(worst))
    src = pathlib.Path(ROOT / "training" / "evaluate.py").read_text(encoding="utf-8")
    check("增益 < 2% 的分支會指向 SPEC §12 與 §4.3",
          "SPEC §12" in src and "SPEC §4.3" in src)
    check("並明講負結果誠實呈現優於造假", "負結果誠實呈現優於造假" in src)

    print("\n[metrics] macro-F1 的行為")
    y = np.array([0]*90 + [1]*6 + [2]*4)
    allzero = np.zeros_like(y)
    check("全猜多數類時 accuracy 高但 macro-F1 低",
          metrics.report(y, allzero)["accuracy"] == 0.9
          and metrics.macro_f1(y, allzero) < 0.4,
          f"macro-F1 {metrics.macro_f1(y, allzero):.3f}")
    check("全猜多數類時危險類 recall = 0（最不能接受的失敗模式）",
          metrics.report(y, allzero)["danger_recall"] == 0.0)
    check("完美預測 macro-F1 = 1", metrics.macro_f1(y, y) == 1.0)

finally:
    print("\n[teardown] 清除合成資料")
    for p in created:
        pathlib.Path(p).unlink(missing_ok=True)
    for f in config.path("models").rglob("*"):          # 測試途中新產生的其他檔案
        if f.is_file() and f not in models_before:
            f.unlink(missing_ok=True)
    failed = []
    for p, b in backups.items():
        try:
            shutil.move(b, p)
        except OSError as e:
            failed.append(f"{p}: {e}")
    if failed:
        print(f"  !! 還原失敗，備份保留在 {bak_dir}：")
        for f in failed:
            print(f"     {f}")
        FAIL.append("teardown 還原")
    else:
        shutil.rmtree(bak_dir, ignore_errors=True)
    leftover = [str(p.relative_to(config.ROOT)) for p in
                list(config.path("models").rglob("*.pt"))
                + list(config.path("data").glob("*.npz"))
                if p not in models_before and p not in backups]
    print(f"  殘留檔案：{leftover or '無'}")

print(f"\n{'='*58}\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1)
print("全部通過")
