"""Discovery and Service Registry 註冊與查詢（SPEC §3.2、§7.2）。

MEC App A / B 各自把自己的 fd-service 註冊到 MEP 的 Service Registry，
再透過 MEP Gateway 取得對方位址並呼叫。SPEC §3.2 明訂：
「FD 交換走 Kong 與 Service Registry，不走共享 volume」——
這是「MEC 使用程度」子項目的主要得分點。

=== 降級順序（SPEC §12，必須誠實記錄實際落在哪一級）===
  1. registry      Service Registry 註冊 + 查詢（滿分作法）
  2. kong_static   查不到對方時，退回 Kong 固定路由互相呼叫；仍走 MEP Gateway
  3. shared_volume 最後才退到共享 volume / Redis，並在企劃書標註為模擬多節點

本模組每次實際採用的層級都寫進 self.mode，由 /status 端點回報，
避免決賽詢答時答不出「你們到底有沒有用到 Registry」。

注意：OAI-MEP 的 Registry API 路徑與 payload 格式依版本而異，
D13 在 VM 上確認後填入 config.yaml 的 registry 區，並記錄於
docs/platform-notes.md §4。在確認之前 registry.enabled 保持 false，
系統以 kong_static 模式運作，功能完整、只是不加分。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config

log = logging.getLogger("registry")

MODE_REGISTRY = "registry"
MODE_KONG_STATIC = "kong_static"
MODE_SHARED_VOLUME = "shared_volume"


class RegistryClient:
    def __init__(self, node: str, cfg: dict | None = None,
                 timeout: float = 3.0) -> None:
        self.cfg = cfg or config.load()
        self.node = str(node).lower()
        self.timeout = timeout
        self.mode = MODE_KONG_STATIC       # 未成功註冊前一律視為降級狀態
        self.service_id: str | None = None
        self.last_error: str | None = None

        reg = self.cfg.get("registry", {})
        self.enabled = bool(reg.get("enabled", False))
        self.base_url = str(reg.get("base_url", "")).rstrip("/")
        self.api = reg.get("api", {})
        self.service_name = str(reg["service_name_pattern"]).format(node=self.node)

    # -- 自身位址 ---------------------------------------------------------
    def self_fd_url(self) -> str:
        """本節點 FD service 經 MEP Gateway 的對外位址。"""
        gw = self.cfg["network"]["mep_gateway"]
        route = self.cfg["nodes"][self.node]["kong"]["fd_route"]
        return f"http://{gw}{route}"

    def peer_fd_url(self) -> str:
        """對側 FD service 位址：先問 Registry，失敗則用 Kong 固定路由。"""
        peer = config.peer_of(self.node)
        if self.enabled:
            url = self._discover(peer)
            if url:
                self.mode = MODE_REGISTRY
                return url.rstrip("/")
            self.mode = MODE_KONG_STATIC
        gw = self.cfg["network"]["mep_gateway"]
        route = self.cfg["nodes"][peer]["kong"]["fd_route"]
        return f"http://{gw}{route}"

    # -- Registry 操作 ----------------------------------------------------
    def register(self) -> bool:
        """向 Service Registry 註冊本節點的 fd-service。"""
        if not self.enabled:
            log.info("registry.enabled=false，直接以 %s 模式運作", MODE_KONG_STATIC)
            self.mode = MODE_KONG_STATIC
            return False
        try:
            r = httpx.post(
                f"{self.base_url}{self.api['register']}",
                json=self._payload(), timeout=self.timeout)
            r.raise_for_status()
            body: dict[str, Any] = r.json() if r.content else {}
            self.service_id = body.get("serInstanceId") or body.get("id")
            self.mode = MODE_REGISTRY
            log.info("已註冊 %s -> %s", self.service_name, self.service_id)
            return True
        except Exception as e:                      # 註冊失敗不可讓服務起不來
            self.last_error = f"{type(e).__name__}: {e}"
            self.mode = MODE_KONG_STATIC
            log.warning("Service Registry 註冊失敗（降級為 %s）：%s",
                        MODE_KONG_STATIC, self.last_error)
            return False

    def _discover(self, peer: str) -> str | None:
        name = str(self.cfg["registry"]["service_name_pattern"]).format(node=peer)
        try:
            r = httpx.get(f"{self.base_url}{self.api['discover']}",
                          params={"ser_name": name}, timeout=self.timeout)
            r.raise_for_status()
            items = r.json()
            if isinstance(items, dict):
                items = items.get("serviceInfo") or items.get("items") or []
            for it in items:
                tp = it.get("transportInfo", {}).get("endpoint", {})
                uris = tp.get("uris") or []
                if uris:
                    return str(uris[0])
            self.last_error = f"Registry 查得到回應但 {name} 無可用 uri"
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
        log.warning("查詢對側 %s 失敗：%s", peer, self.last_error)
        return None

    def _payload(self) -> dict[str, Any]:
        """ETSI GS MEC 011 ServiceInfo 的最小欄位集。

        實際欄位需依 OAI-MEP 版本調整（D13 在 VM 上確認後更新本函式，
        並把確認結果寫進 docs/platform-notes.md §4）。
        """
        return {
            "serName": self.service_name,
            "version": "1.0",
            "state": "ACTIVE",
            "serializer": "JSON",
            "transportInfo": {
                "id": f"fd-{self.node}",
                "name": "REST",
                "type": "REST_HTTP",
                "protocol": "HTTP",
                "version": "1.1",
                "endpoint": {"uris": [self.self_fd_url()]},
            },
        }

    def status(self) -> dict[str, Any]:
        """供 /status 端點回報實際落在哪一級降級（SPEC §12 的誠實紀錄）。"""
        return {
            "mode": self.mode,
            "registry_enabled": self.enabled,
            "service_name": self.service_name,
            "service_id": self.service_id,
            "self_fd_url": self.self_fd_url(),
            "last_error": self.last_error,
        }
