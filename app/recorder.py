"""游戏窗口内鼠标点击录制（客户区相对坐标）。"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple


from .capture import Region, find_window_rect

try:
    import win32api
    import win32gui
    WIN32_OK = True
except ImportError:  # macOS / Linux
    win32api = None
    win32gui = None
    WIN32_OK = False

from .platform_compat import require_windows


def _need_win32() -> None:
    if not WIN32_OK:
        require_windows()

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02


class ClickRecorder:
    def __init__(
        self,
        log: Optional[Callable[[str], None]] = None,
        ignore_window_title: str = "桌面找图助手",
    ):
        self._log = log or (lambda _msg: None)
        self._ignore_title = ignore_window_title
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.recording = False
        self.steps: List[Dict] = []
        self._last_ts = 0.0
        self._last_click_ts = 0.0
        self._left_down = False
        self._right_down = False
        self._ignore_until = 0.0
        self.target_window_title = ""
        self.window_region: Optional[Region] = None
        self.recorded_size: Optional[Tuple[int, int]] = None

    def start(self, target_window_title: str = "") -> None:
        _need_win32()
        if self.recording:
            self._log("已在录制中")
            return
        self.target_window_title = (target_window_title or "").strip()
        self.window_region = (
            find_window_rect(self.target_window_title) if self.target_window_title else None
        )
        if self.target_window_title and self.window_region is None:
            self._log(f"未找到窗口「{self.target_window_title}」，请先打开游戏浏览器窗口")
            return

        self.steps = []
        self.recorded_size = None
        self._stop.clear()
        self.recording = True
        self._last_ts = time.time()
        self._last_click_ts = 0.0
        self._left_down = bool(win32api.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        self._right_down = bool(win32api.GetAsyncKeyState(VK_RBUTTON) & 0x8000)
        # 忽略按下 F6 时的残留点击
        self._ignore_until = time.time() + 0.55
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        if self.window_region:
            r = self.window_region
            self.recorded_size = (r.width, r.height)
            self._log(
                f"开始游戏内录制：客户区 {r.width}x{r.height} @({r.left},{r.top})，"
                "只记录点在该窗口内的点击，F6 停止"
            )
        else:
            self._log("开始录制（全屏坐标）。建议填写窗口标题后再录，F6 停止")

    def stop(self) -> List[Dict]:
        if not self.recording:
            return list(self.steps)
        self._stop.set()
        self.recording = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self.window_region:
            self.recorded_size = (
                self.window_region.width,
                self.window_region.height,
            )
        self._log(f"录制结束，共 {len(self.steps)} 步")
        return list(self.steps)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.target_window_title:
                self.window_region = find_window_rect(self.target_window_title)
            self._check_button(VK_LBUTTON, "left", "_left_down")
            self._check_button(VK_RBUTTON, "right", "_right_down")
            time.sleep(0.008)

    def _should_ignore_helper_ui(self, x: int, y: int) -> bool:
        if time.time() < self._ignore_until:
            return True
        try:
            hwnd = win32gui.WindowFromPoint((x, y))
            while hwnd:
                title = win32gui.GetWindowText(hwnd)
                if title and self._ignore_title in title:
                    return True
                hwnd = win32gui.GetParent(hwnd)
        except Exception:  # noqa: BLE001
            return False
        return False

    def _to_relative(self, x: int, y: int) -> Optional[tuple[int, int]]:
        region = self.window_region
        if region is None:
            return x, y
        if not (
            region.left <= x < region.left + region.width
            and region.top <= y < region.top + region.height
        ):
            return None
        return x - region.left, y - region.top

    def _check_button(self, vk: int, button: str, flag_name: str) -> None:
        down = bool(win32api.GetAsyncKeyState(vk) & 0x8000)
        was_down = getattr(self, flag_name)
        if down and not was_down:
            x, y = win32api.GetCursorPos()
            if self._should_ignore_helper_ui(x, y):
                setattr(self, flag_name, down)
                return
            # 防抖：过密点击忽略（避免按下抖动录两下）
            now = time.time()
            if self._last_click_ts and (now - self._last_click_ts) < 0.08:
                setattr(self, flag_name, down)
                return
            rel = self._to_relative(x, y)
            if rel is None:
                self._log("点击不在游戏客户区内，已忽略")
                setattr(self, flag_name, down)
                return
            rx, ry = rel
            delay_ms = int(max(0, (now - self._last_ts) * 1000))
            if not self.steps:
                delay_ms = 0
            step = {
                "x": int(rx),
                "y": int(ry),
                "delay_ms": delay_ms,
                "button": button,
                "clicks": 1,
                "relative": bool(self.window_region is not None),
                "mode": "click",
            }
            self.steps.append(step)
            self._last_ts = now
            self._last_click_ts = now
            if self.window_region:
                self.recorded_size = (
                    self.window_region.width,
                    self.window_region.height,
                )
            tag = "相对" if step["relative"] else "屏幕"
            self._log(
                f"录到 #{len(self.steps)} {button} {tag}({rx},{ry}) 间隔{delay_ms}ms"
            )
        setattr(self, flag_name, down)
