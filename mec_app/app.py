"""MEC V2X App：推論端點 + 聯邦蒸餾端點（SPEC §3.2、§3.3、§7.2）。

一個行程同時服務兩組 Kong 路由（SPEC §3.3「FD Service 與推論 App 共用同一個
FastAPI 行程，僅路徑不同，避免多開行程」）：

    /mec/v2x/{node}  --Kong strip_path--> POST /bsm      即時推論
    /mec/fd/{node}   --Kong strip_path--> GET  /logits   軟標籤交換
                                          GET  /status   輪數與降級狀態

節點以環境變數 MEC_NODE=a|b 決定，其餘全部讀 config.yaml：
兩個節點跑的是同一份程式碼，不存在 app_a.py / app_b.py 這種分岔。

啟動：
    MEC_NODE=a .venv/bin/python -m uvicorn mec_app.app:app \
        --host 172.16.6.10 --port 5002
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
import torch
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import config, features as F, jsonlog, model as M, rule_baseline as R
from .registry_client import RegistryClient
from .window import NeighborTable, WindowStore

logging.basicConfig(
    level=os.environ.get("MEC_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("mec_app")

RISK_NAMES = {0: "safe", 1: "caution", 2: "danger"}


class AppState:
    """單一 MEC 節點的執行期狀態。

    有狀態是刻意的：每車滑動視窗與鄰車表必須跨請求保留，這正是 SPEC §2 Q6(c)
    「無法在無狀態雲端函式中低成本實現」的論證所指。
    """

    def __init__(self, node: str) -> None:
        self.cfg = config.load()
        self.node = str(node).lower()
        self.node_cfg = config.node(self.node)
        self.node_id = self.node_cfg["node_id"]
        scen = self.cfg["sumo"]["scenarios"][self.node_cfg["scenario"]]
        self.signalized = bool(scen["signalized"])

        self.windows = WindowStore()
        self.neighbors = NeighborTable()
        self.geometry = F.Geometry.load(self.node)
        self.registry = RegistryClient(self.node)
        self.inference_log = jsonlog.JsonlWriter("inference", node=self.node)

        self.model: M.RiskCNN | None = None
        self.model_path: str | None = None
        self.model_mtime: float = 0.0
        self.danger_tau: float | None = None     # 這一版權重校準過的危險警告門檻
        self.round: int = 0
        self.last_val_f1: float | None = None

        self._proxy: np.ndarray | None = None
        self._proxy_hash: str | None = None
        self.msg_count = 0
        self.started = time.time()

    # -- 模型 -------------------------------------------------------------
    def load_model(self, path: str | None = None) -> str:
        """載入權重。未指定時取 current.pt；不存在則以隨機初始化啟動。

        隨機初始化是刻意允許的：D8–D9 只有規則基準線、還沒有模型時，
        App 仍必須能收資料並回傳規則告警（SPEC §15.2：任何時間點都必須
        有一個可展示的狀態）。此時 /status 會標明 model_ready=false。
        """
        p = config.path(
            path or self.cfg["fd"]["model_swap"]["current"].format(node=self.node))
        m = M.build()
        if p.exists():
            state = torch.load(p, map_location="cpu", weights_only=True)
            m.load_state_dict(state)
            self.model_mtime = p.stat().st_mtime
            self.model_path = str(p)
            self.danger_tau = M.load_threshold(self.node, p.stem)
            log.info("已載入權重 %s（危險門檻 %s）", p, self.danger_tau)
        else:
            self.model_mtime = 0.0
            self.model_path = None
            self.danger_tau = None
            log.warning("權重 %s 不存在，以隨機初始化啟動（僅規則基準線可信）", p)
        m.eval()
        self.model = m
        return self.model_path or "<random-init>"

    def maybe_hot_swap(self) -> bool:
        """SPEC §7.5：蒸餾完成後熱替換推論權重。

        以 mtime 偵測 current.pt 是否被原子性 rename 換掉。每次請求只做一次
        stat，成本遠低於推論本身。
        """
        if self.model_path is None:
            return False
        p = config.path(self.model_path)
        try:
            mt = p.stat().st_mtime
        except OSError:
            return False
        if mt <= self.model_mtime:
            return False
        log.info("偵測到權重更新，熱替換中：%s", p)
        self.load_model(self.model_path)
        return True

    # -- 公共代理資料集 ---------------------------------------------------
    def proxy(self) -> tuple[np.ndarray, str]:
        """載入 proxy_set.npy 並計算 SHA256（SPEC §7.1）。

        hash 供對方驗證兩端副本位元相同；不一致必須拒絕該輪（SPEC §7.2）。
        """
        if self._proxy is not None and self._proxy_hash is not None:
            return self._proxy, self._proxy_hash
        p = config.path(self.cfg["fd"]["proxy"]["path"])
        if not p.exists():
            raise FileNotFoundError(
                f"{p} 不存在。proxy_set.npy 由 D12 的 proxy_public 場景產生，"
                "兩端須持有位元完全相同的副本（SPEC §7.1）。")
        raw = p.read_bytes()
        arr = np.load(p).astype(np.float32)
        steps = self.cfg["window"]["steps"]
        dim = self.cfg["features"]["dim"]
        n = self.cfg["fd"]["proxy"]["n_samples"]
        if arr.shape != (n, steps, dim):
            raise ValueError(
                f"proxy_set 形狀應為 {(n, steps, dim)}，實際 {arr.shape}")
        self._proxy = arr
        self._proxy_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
        return self._proxy, self._proxy_hash


STATE: AppState | None = None


def state() -> AppState:
    if STATE is None:
        raise HTTPException(503, "服務尚未初始化")
    return STATE


@asynccontextmanager
async def lifespan(app: FastAPI):
    global STATE
    node = os.environ.get("MEC_NODE", "a")
    STATE = AppState(node)
    STATE.load_model()
    STATE.registry.register()      # 失敗會自動降級，不阻擋啟動（SPEC §12）
    log.info("MEC App 節點 %s 啟動，registry 模式 = %s",
             STATE.node_id, STATE.registry.mode)
    yield
    log.info("MEC App 節點 %s 關閉，累計處理 %d 筆 BSM",
             STATE.node_id, STATE.msg_count)


app = FastAPI(title="MEC V2X 危險預警 App", version="1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# 推論路由（Kong: /mec/v2x/{node}）
# ---------------------------------------------------------------------------
@app.post("/bsm")
async def ingest_bsm(msg: dict[str, Any], request: Request) -> JSONResponse:
    """接收一筆 V2X 上行訊息，回傳風險告警（SPEC §5.1、§3.2）。

    延遲拆解（SPEC §8.2）：
      T1 上行網路 = ts_recv - ts_ue
      T2 特徵組裝 = ts_window_ready - ts_recv
      T3 模型推論 = ts_infer_done - ts_window_ready
      T4 下行回傳 = UE 端收到時間 - ts_resp（由 UE 端量測）
    """
    ts_recv = time.time()
    s = state()

    try:
        ego = F.VehicleState.from_bsm(msg)
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(422, f"BSM 欄位不合 SPEC §5.1：{e}") from e

    declared = str(msg.get("intersection", s.node_id)).upper()
    if declared != s.node_id:
        raise HTTPException(
            400, f"本節點為路口 {s.node_id}，收到標記為 {declared} 的訊息。"
                 "請確認 Kong 路由與 UE 的 --intersection 參數一致。")

    s.maybe_hot_swap()

    # 1) 更新鄰車表，取同一時刻的鄰車集合
    s.neighbors.update(ego, now=ts_recv)
    nbrs = s.neighbors.neighbors_of(ego)

    # 2) 特徵（在 MEC 端計算，非 UE 端 —— SPEC §5.2）
    feat, raw, conflict = F.compute(ego, nbrs, s.geometry, s.signalized, s.cfg)

    # 3) 滑動視窗
    win = s.windows.push(ego.vehicle_id, feat, now=ts_recv)
    ts_window_ready = time.time()

    # 4) 規則基準線（對照組，與模型同步計算 —— SPEC §6.3，邏輯與平台範例一致）
    risk_rule, ttc_now, rule_target = R.evaluate_platform(ego, nbrs, s.cfg)

    # 5) 模型推論
    risk_pred, probs = -1, None
    if win.ready and s.model is not None:
        with torch.no_grad():
            x = torch.from_numpy(win.tensor()).unsqueeze(0)
            logits = s.model(x)
            p = torch.softmax(logits, dim=-1)[0]
        risk_pred = int(M.decide(p.numpy()[None], s.danger_tau)[0])
        probs = [round(float(v), 4) for v in p]
    ts_infer_done = time.time()

    body = {
        "msg_id": msg.get("msg_id"),
        "node": s.node_id,
        "vehicle_id": ego.vehicle_id,
        "risk_pred": risk_pred,
        "risk_pred_name": RISK_NAMES.get(risk_pred, "unknown"),
        "risk_rule": risk_rule,
        "risk_rule_name": RISK_NAMES.get(risk_rule, "unknown"),
        "ttc_now": None if ttc_now == float("inf") else round(ttc_now, 3),
        "probs": probs,
        "conflict_with": conflict.partner_id,        # 模型警告針對的車
        "rule_target": rule_target,                  # 規則警告針對的車（同車道前車）
        "rel_dist": round(float(conflict.rel_dist), 2),
        "sim_time": ego.sim_time,
        "model_ready": bool(win.ready and s.model_path is not None),
        # T1/T2/T3 直接回給 UE，方便 UE 端把 T4 補上後湊出完整 T_total
        "ts_recv": ts_recv,
        "ts_window_ready": ts_window_ready,
        "ts_infer_done": ts_infer_done,
    }
    ts_resp = time.time()
    body["ts_resp"] = ts_resp

    # 6) 結構化紀錄（SPEC §8.4，欄位由 config.yaml 強制）
    s.inference_log.write(
        msg_id=msg.get("msg_id"),
        ts_ue=msg.get("ts_ue"),
        ts_recv=ts_recv,
        ts_infer_done=ts_infer_done,
        ts_resp=ts_resp,
        vehicle_id=ego.vehicle_id,
        risk_pred=risk_pred,
        risk_rule=risk_rule,
        ttc_now=None if ttc_now == float("inf") else ttc_now,
    )

    s.msg_count += 1
    if s.msg_count % 500 == 0:          # 定期淘汰離開路口的車輛
        s.windows.evict_stale(now=ts_resp)
        s.neighbors.evict_stale(now=ts_resp)

    return JSONResponse(body)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    """沿用教材的健康檢查慣例（SPEC §3.3 的 mec-health 路由）。"""
    s = state()
    return {
        "status": "ok",
        "node": s.node_id,
        "uptime_s": round(time.time() - s.started, 1),
        "messages": s.msg_count,
        "tracked_vehicles": len(s.windows),
    }


# ---------------------------------------------------------------------------
# 聯邦蒸餾路由（Kong: /mec/fd/{node}）—— SPEC §7.2
# ---------------------------------------------------------------------------
@app.get("/logits")
async def get_logits(round: int = Query(0, ge=0)) -> dict[str, Any]:
    """回傳本節點對公共代理資料集的軟標籤（SPEC §7.2）。

    round >= 1：回傳蒸餾程序在該輪「發布」的軟標籤（training/distill.py 步驟 2 寫出）。
                尚未發布回 409，取用方等待後重試——這樣兩節點每一輪交換的都是同一輪的結果。
    round = 0 ：以目前載入的模型即時計算（單獨訓練基準、展示用）。

    回應格式與 SPEC §7.2 完全一致。bytes 欄位回報的是 **原始 float32 張量**
    大小（2000×3×4 = 24,000），不是 base64 後的線上大小——
    SPEC §8.3 要求作圖時標明採用何者，兩者不可混用。
    """
    s = state()
    if s.model is None:
        raise HTTPException(503, "模型尚未載入")
    try:
        proxy, phash = s.proxy()
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(503, str(e)) from e

    T = float(s.cfg["fd"]["temperature"])
    if round >= 1:
        pub = config.path(s.cfg["fd"]["published"].format(node=s.node, round=round))
        if not pub.exists():
            raise HTTPException(409, f"節點 {s.node_id} 第 {round} 輪軟標籤尚未產生，請稍後重試")
        out = np.load(pub).astype(np.float32)
    else:
        with torch.no_grad():
            out = s.model(torch.from_numpy(proxy)).numpy().astype(np.float32)

    raw = out.tobytes(order="C")
    expect = s.cfg["fd"]["logits"]["raw_bytes"]
    if len(raw) != expect:
        raise HTTPException(
            500, f"logits 位元組數 {len(raw)} 與 config 的 {expect} 不符")

    return {
        "node_id": s.node_id,
        "round": round,
        "proxy_hash": phash,
        "temperature": T,
        "n_samples": int(out.shape[0]),
        "n_classes": int(out.shape[1]),
        "encoding": s.cfg["fd"]["logits"]["encoding"],
        "logits": base64.b64encode(raw).decode("ascii"),
        "bytes": len(raw),
        "ts": time.time(),
    }


@app.get("/status")
async def fd_status() -> dict[str, Any]:
    """回傳目前輪數、是否就緒、上一輪的本地驗證分數（SPEC §7.2）。"""
    s = state()
    try:
        _, phash = s.proxy()
        proxy_ok = True
    except Exception:
        phash, proxy_ok = None, False
    return {
        "node_id": s.node_id,
        "round": s.round,
        "ready": bool(s.model is not None and proxy_ok),
        "last_val_f1": s.last_val_f1,
        "proxy_hash": phash,
        "model_path": s.model_path,
        "model_mtime": s.model_mtime,
        "danger_threshold": s.danger_tau,
        "registry": s.registry.status(),
    }


@app.post("/model/select")
async def select_model(round: int = Query(..., ge=0)) -> dict[str, Any]:
    """切換到指定輪數的權重（SPEC §7.5 的現場 demo 互動）。

    round=0 為單獨訓練基準、round=5 為協同訓練結果。
    決賽展示時由面板按鈕呼叫本端點，讓評審當場看見漏報被補上。
    """
    s = state()
    p = config.path(f"models/{s.node}/round_{round}.pt")
    if not p.exists():
        raise HTTPException(404, f"{p} 不存在")
    s.load_model(str(p.relative_to(config.ROOT)))
    s.round = round
    return {"node_id": s.node_id, "round": round, "model_path": s.model_path}
