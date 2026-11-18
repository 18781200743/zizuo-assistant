"""通过 WebView2 远程调试端口发送真实鼠标点击（比系统模拟点击更稳）。"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple
from urllib.request import urlopen

DEFAULT_CDP_PORT = 9229


def _http_json(url: str, timeout: float = 1.5) -> Any:
    with urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_page_ws(port: int = DEFAULT_CDP_PORT) -> Optional[str]:
    try:
        targets = _http_json(f"http://127.0.0.1:{port}/json")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(targets, list):
        return None
    # 优先带游戏域名的 page
    pages = [t for t in targets if str(t.get("type") or "") == "page"]
    for t in pages:
        url = str(t.get("url") or "")
        if "boomegg" in url or "wx77200645" in url or "game" in url:
            ws = t.get("webSocketDebuggerUrl")
            if ws:
                return str(ws)
    for t in pages:
        ws = t.get("webSocketDebuggerUrl")
        if ws:
            return str(ws)
    return None


def cdp_available(port: int = DEFAULT_CDP_PORT) -> bool:
    try:
        _http_json(f"http://127.0.0.1:{port}/json/version", timeout=0.8)
        return True
    except Exception:  # noqa: BLE001
        return False


def _cdp_call(ws: Any, msg_id: list, method: str, params: Optional[Dict] = None) -> Dict:
    msg_id[0] += 1
    nid = msg_id[0]
    ws.send(json.dumps({"id": nid, "method": method, "params": params or {}}))
    for _ in range(40):
        raw = ws.recv()
        data = json.loads(raw)
        if data.get("id") == nid:
            return data
    return {}


def ensure_game_viewport(
    width: int = 390,
    height: int = 844,
    *,
    port: int = DEFAULT_CDP_PORT,
) -> bool:
    """强制页面按目标宽高、DPR=1 布局（同一 CDP 会话内有效）。

    Windows 125% 缩放时默认 DPR=1.25，PrintWindow 常裁掉底部「放弃」。
    """
    try:
        import websocket
    except ImportError:
        return False

    ws_url = find_page_ws(port)
    if not ws_url:
        return False

    try:
        ws = websocket.create_connection(ws_url, timeout=3)
    except Exception:  # noqa: BLE001
        return False

    try:
        mid = [0]
        w = max(280, int(width))
        h = max(400, int(height))
        _cdp_call(
            ws,
            mid,
            "Emulation.setDeviceMetricsOverride",
            {
                "width": w,
                "height": h,
                "deviceScaleFactor": 1,
                "mobile": True,
            },
        )
        time.sleep(0.2)
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


def capture_css_frame(
    *,
    port: int = DEFAULT_CDP_PORT,
    width: int = 390,
    height: int = 844,
    force_dpr1: bool = False,
) -> Optional[Any]:
    """CDP 截取一帧并缩放到 CSS 像素。返回 BGR ndarray。

    默认不改 DPR（改了会导致后续点击坐标错位）。
    PrintWindow 裁底时，用这张图找「放弃」更稳。
    """
    try:
        import base64

        import cv2
        import numpy as np
        import websocket
    except ImportError:
        return None

    ws_url = find_page_ws(port)
    if not ws_url:
        return None

    try:
        ws = websocket.create_connection(ws_url, timeout=4)
    except Exception:  # noqa: BLE001
        return None

    try:
        mid = [0]
        if force_dpr1:
            _cdp_call(
                ws,
                mid,
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": max(280, int(width)),
                    "height": max(400, int(height)),
                    "deviceScaleFactor": 1,
                    "mobile": True,
                },
            )
            time.sleep(0.15)

        ev = _cdp_call(
            ws,
            mid,
            "Runtime.evaluate",
            {
                "expression": (
                    "({dpr: window.devicePixelRatio||1,"
                    " w: window.innerWidth||0,"
                    " h: window.innerHeight||0})"
                ),
                "returnByValue": True,
            },
        )
        val = ((ev.get("result") or {}).get("result") or {}).get("value") or {}
        css_w = int(val.get("w") or width)
        css_h = int(val.get("h") or height)

        shot = _cdp_call(ws, mid, "Page.captureScreenshot", {"format": "png"})
        b64 = ((shot.get("result") or {}).get("data")) or ""
        if not b64:
            return None
        raw = base64.b64decode(b64)
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            return None
        if css_w > 0 and css_h > 0 and (img.shape[1] != css_w or img.shape[0] != css_h):
            img = cv2.resize(img, (css_w, css_h), interpolation=cv2.INTER_AREA)
        return np.ascontiguousarray(img)
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


def click_css(x: float, y: float, port: int = DEFAULT_CDP_PORT) -> bool:
    """在页面 CSS 坐标处点击（与客户区/CDP 缩放后坐标一致，不再强改 DPR）。"""
    try:
        import websocket
    except ImportError:
        return False

    ws_url = find_page_ws(port)
    if not ws_url:
        return False

    try:
        ws = websocket.create_connection(ws_url, timeout=3)
    except Exception:  # noqa: BLE001
        return False

    try:
        mid = [0]
        css_x = float(x)
        css_y = float(y)
        try:
            ev = _cdp_call(
                ws,
                mid,
                "Runtime.evaluate",
                {
                    "expression": (
                        "({dpr: window.devicePixelRatio||1,"
                        " w: window.innerWidth||0,"
                        " h: window.innerHeight||0})"
                    ),
                    "returnByValue": True,
                },
            )
            val = ((ev.get("result") or {}).get("result") or {}).get("value") or {}
            dpr = float(val.get("dpr") or 1)
            iw = float(val.get("w") or 0)
            ih = float(val.get("h") or 0)
            # 仅当传入的是设备像素（明显超出 CSS 视口）时才换算
            if dpr > 1.05 and iw > 0 and ih > 0:
                if css_x > iw * 1.15 or css_y > ih * 1.15:
                    css_x /= dpr
                    css_y /= dpr
        except Exception:  # noqa: BLE001
            pass

        for state in ("mouseMoved", "mousePressed", "mouseReleased"):
            params = {
                "type": state,
                "x": css_x,
                "y": css_y,
                "button": "left",
                "clickCount": 1 if state != "mouseMoved" else 0,
            }
            if state == "mouseMoved":
                params.pop("clickCount", None)
                params["button"] = "none"
            _cdp_call(ws, mid, "Input.dispatchMouseEvent", params)
            time.sleep(0.03)
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


def physical_to_css(
    x: int,
    y: int,
    *,
    dpi: int = 96,
    grab_size: Optional[Tuple[int, int]] = None,
    css_size: Optional[Tuple[int, int]] = None,
) -> Tuple[float, float]:
    """把截图/客户区物理像素坐标转成页面 CSS 坐标。"""
    scale = max(dpi, 96) / 96.0
    if grab_size and css_size and grab_size[0] > 0 and grab_size[1] > 0:
        # 若已知 CSS 布局大小，按比例更准
        return (
            float(x) * css_size[0] / grab_size[0],
            float(y) * css_size[1] / grab_size[1],
        )
    return float(x) / scale, float(y) / scale
