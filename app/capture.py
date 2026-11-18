"""屏幕截图与鼠标坐标。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import mss
import numpy as np

try:
    from ctypes import windll
    import win32api
    import win32gui
    import win32ui
    WIN32_OK = True
except ImportError:  # macOS / Linux
    windll = None
    win32api = None
    win32gui = None
    win32ui = None
    WIN32_OK = False

from .platform_compat import require_windows


def _need_win32() -> None:
    if not WIN32_OK:
        require_windows()

# Chromium/WebView2 需要此标志才能 PrintWindow 出真实画面
PW_RENDERFULLCONTENT = 2


@dataclass
class Region:
    left: int
    top: int
    width: int
    height: int
    hwnd: int = 0

    def as_mss(self) -> dict:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


def get_cursor_pos() -> Tuple[int, int]:
    _need_win32()
    return win32api.GetCursorPos()


def _client_region_from_hwnd(hwnd: int) -> Optional[Region]:
    _need_win32()
    try:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        width = right - left
        height = bottom - top
        if width <= 50 or height <= 50:
            return None
        # 客户区 (0,0) -> 屏幕坐标（不含标题栏/边框，录制更准）
        sx, sy = win32gui.ClientToScreen(hwnd, (0, 0))
        return Region(sx, sy, width, height, hwnd=int(hwnd))
    except Exception:  # noqa: BLE001
        return None


def find_window_hwnd(title_keyword: str) -> Optional[int]:
    _need_win32()
    if not title_keyword:
        return None
    keyword = title_keyword.lower()
    hits: list[tuple[int, int]] = []

    def enum_handler(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title or keyword not in title.lower():
            return
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except Exception:  # noqa: BLE001
            return
        area = max(0, right - left) * max(0, bottom - top)
        if area > 50 * 50:
            hits.append((area, int(hwnd)))

    win32gui.EnumWindows(enum_handler, None)
    if not hits:
        return None
    hits.sort(reverse=True)
    return hits[0][1]


def find_window_rect(title_keyword: str) -> Optional[Region]:
    _need_win32()
    """返回窗口客户区（内容区）矩形，避免边框/阴影导致坐标偏移。"""
    hwnd = find_window_hwnd(title_keyword)
    if not hwnd:
        return None
    region = _client_region_from_hwnd(hwnd)
    if region:
        return region
    # 回退整窗
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return Region(left, top, right - left, bottom - top, hwnd=hwnd)
    except Exception:  # noqa: BLE001
        return None


def _grab_hwnd_printwindow(hwnd: int) -> Optional[np.ndarray]:
    _need_win32()
    """用 PrintWindow 抓 WebView/游戏窗客户区，避免 mss 抓到空白或错误层。"""
    import cv2

    try:
        _l, _t, right, bottom = win32gui.GetClientRect(hwnd)
        width = int(right)
        height = int(bottom)
        if width < 50 or height < 50:
            return None

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        # 按整窗尺寸打缓冲，再裁客户区
        wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
        win_w = max(width, wr - wl)
        win_h = max(height, wb - wt)
        bitmap.CreateCompatibleBitmap(mfc_dc, win_w, win_h)
        save_dc.SelectObject(bitmap)
        ok = windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if not ok:
            ok = windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)
        bmpinfo = bitmap.GetInfo()
        bmpstr = bitmap.GetBitmapBits(True)
        img = np.frombuffer(bmpstr, dtype=np.uint8)
        img = img.reshape((bmpinfo["bmHeight"], bmpinfo["bmWidth"], 4))
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

        win32gui.DeleteObject(bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)

        if not ok or img.size == 0:
            return None

        # 客户区相对整窗的偏移
        sx, sy = win32gui.ClientToScreen(hwnd, (0, 0))
        crop_x = max(0, sx - wl)
        crop_y = max(0, sy - wt)
        cropped = img[crop_y : crop_y + height, crop_x : crop_x + width]
        if cropped.shape[0] < 50 or cropped.shape[1] < 50:
            return None
        # 全黑/近白说明抓失败
        mean = float(cropped.mean())
        if mean < 3.0 or mean > 252.0:
            return None
        return np.ascontiguousarray(cropped)
    except Exception:  # noqa: BLE001
        return None


def _grab_mss(region: Optional[Region]) -> np.ndarray:
    import cv2

    with mss.mss() as sct:
        monitor = region.as_mss() if region else sct.monitors[1]
        shot = sct.grab(monitor)
        bgra = np.array(shot, dtype=np.uint8)
        return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)


def grab_screen(region: Optional[Region] = None) -> np.ndarray:
    """优先 PrintWindow（适配 Edge WebView），失败再 mss。

    注意：PrintWindow 只能抓整个客户区。若 region 是子区域（如下半区找按钮），
    会先抓整窗再按相对偏移裁剪，避免坐标错位。
    """
    if region is not None and region.hwnd:
        printed = _grab_hwnd_printwindow(int(region.hwnd))
        if printed is not None:
            full = _client_region_from_hwnd(int(region.hwnd))
            if full is None:
                return printed
            # 子区域：相对客户区裁剪
            if (
                region.left != full.left
                or region.top != full.top
                or region.width != full.width
                or region.height != full.height
            ):
                x0 = max(0, region.left - full.left)
                y0 = max(0, region.top - full.top)
                x1 = min(printed.shape[1], x0 + max(1, region.width))
                y1 = min(printed.shape[0], y0 + max(1, region.height))
                cropped = printed[y0:y1, x0:x1]
                if cropped.size > 0:
                    return np.ascontiguousarray(cropped)
            return printed
    return _grab_mss(region)


def grab_cursor_patch(half_w: int = 60, half_h: int = 30) -> tuple[np.ndarray, Tuple[int, int]]:
    """截取鼠标周围小块，用于保存按钮模板（如传送）。"""
    x, y = get_cursor_pos()
    left = max(0, x - half_w)
    top = max(0, y - half_h)
    width = half_w * 2
    height = half_h * 2
    img = _grab_mss(Region(left, top, width, height))
    return img, (x, y)
