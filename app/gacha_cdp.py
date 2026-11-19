"""抽卡直连控制器：把 gacha_script 注入游戏页，发开始/停止指令并读状态。

和截图识别版（app/gacha.py）互不影响，复用同一条 WebView2 调试端口，
但用独立连接，避免和游戏辅助的轮询抢同一个 websocket。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from .cdp_click import DEFAULT_CDP_PORT
from .game_assist import AssistBridge, load_mapping
from .gacha_script import GACHA_VERSION, build_runtime


class GachaCdp:
    def __init__(self, port: int = DEFAULT_CDP_PORT) -> None:
        self.bridge = AssistBridge(port=port)

    # ---- 注入 ----

    def _mapping_json(self) -> str:
        return json.dumps(load_mapping(), ensure_ascii=False, sort_keys=True)

    def ensure(self) -> str:
        """返回 'installed' / 'ok' / 'fail:<原因>'。"""
        mapping = self._mapping_json()
        stamp = hashlib.md5((str(GACHA_VERSION) + "|" + mapping).encode("utf-8")).hexdigest()
        try:
            fresh = self.bridge.eval_js(
                "(window.__zzGacha && window.__zzGacha.version === %d"
                " && window.__zzGachaStamp === %s) ? 'ok' : 'need'"
                % (GACHA_VERSION, json.dumps(stamp))
            )
            if fresh == "ok":
                return "ok"
        except RuntimeError:
            return "fail:" + self.bridge.last_error

        prefix = (
            "window.__zzGachaMapping = %s;\n"
            "window.__zzGachaStamp = %s;\n" % (mapping, json.dumps(stamp))
        )
        expression = (
            "(function(){try{"
            + prefix
            + build_runtime()
            + ";return window.__zzGacha?'installed':'fail:运行时未注册';"
            "}catch(e){return 'fail:'+((e&&e.message)||e)}})()"
        )
        try:
            value = self.bridge.eval_js(expression)
        except RuntimeError as exc:
            return f"fail:{exc}"
        return str(value or "fail:未知")

    # ---- 指令 ----

    def snapshot(self) -> Optional[Dict[str, Any]]:
        try:
            raw = self.bridge.eval_js("JSON.stringify(window.__zzGacha ? window.__zzGacha.snapshot() : null)")
        except RuntimeError:
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def heroes(self) -> List[Dict[str, Any]]:
        """完整英雄图鉴（红/橙）：[{id, name, quality}]。"""
        try:
            raw = self.bridge.eval_js("JSON.stringify(window.__zzGacha ? window.__zzGacha.heroes() : [])")
        except RuntimeError:
            return []
        try:
            return json.loads(raw) if raw else []
        except (TypeError, ValueError):
            return []

    def pool(self) -> Dict[str, Any]:
        """当前招募面板里的候选英雄（真正能抽到的）。"""
        try:
            raw = self.bridge.eval_js("JSON.stringify(window.__zzGacha ? window.__zzGacha.pool() : {ok:false,heroes:[]})")
        except RuntimeError as exc:
            return {"ok": False, "reason": str(exc), "heroes": []}
        try:
            return json.loads(raw) if raw else {"ok": False, "heroes": []}
        except (TypeError, ValueError):
            return {"ok": False, "heroes": []}

    def start(
        self,
        targets: Optional[List[Any]] = None,
        keep_quality: int = 6,
        max_draws: int = 0,
        delay_ms: int = 1200,
        refresh: bool = False,
        max_green: int = 0,
        max_bet: bool = True,
        always_abandon: bool = False,
    ) -> Dict[str, Any]:
        # targets 里既可以是英雄编号，也可以是英雄名（由页面运行时解析）
        names = [str(x).strip() for x in (targets or []) if str(x).strip()]
        opts = {
            "targets": names,
            "keepQuality": int(keep_quality or 0),
            "maxDraws": int(max_draws or 0),
            "delayMs": int(delay_ms or 1200),
            "refresh": bool(refresh),
            "maxGreen": int(max_green or 0),
            "maxBet": bool(max_bet),
            "alwaysAbandon": bool(always_abandon),
        }
        expression = (
            "(function(){try{window.__zzGacha.start(%s);return 'ok';}"
            "catch(e){return 'fail:'+((e&&e.message)||e)}})()" % json.dumps(opts)
        )
        try:
            result = str(self.bridge.eval_js(expression) or "")
        except RuntimeError as exc:
            return {"ok": False, "message": str(exc)}
        if result.startswith("fail:"):
            return {"ok": False, "message": result[5:]}
        return {"ok": True, "message": "已开始抽卡"}

    def stop(self) -> Dict[str, Any]:
        try:
            self.bridge.eval_js("window.__zzGacha && window.__zzGacha.stop('已手动停止')")
        except RuntimeError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "message": "已停止"}

    def close(self) -> None:
        self.bridge.close()
