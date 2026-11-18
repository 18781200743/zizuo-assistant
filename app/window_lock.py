"""运行抽卡/存档时：先恢复固定窗口尺寸，再临时禁止缩放。"""

from __future__ import annotations

from typing import Optional, Tuple


from .capture import find_window_hwnd

try:
    from ctypes import byref, windll
    from ctypes.wintypes import RECT
    import win32con
    import win32gui
    WIN32_OK = True
except ImportError:  # macOS / Linux
    byref = windll = None
    RECT = None
    win32con = None
    win32gui = None
    WIN32_OK = False

from .platform_compat import require_windows


def _need_win32() -> None:
    if not WIN32_OK:
        require_windows()

_SIZE_STYLES = (win32con.WS_THICKFRAME | win32con.WS_MAXIMIZEBOX) if WIN32_OK else 0

_locked_hwnd: int = 0
_saved_style: Optional[int] = None
_locked_outer: Optional[Tuple[int, int]] = None  # 锁定的整窗宽高
_locked_client: Optional[Tuple[int, int]] = None  # 目标客户区宽高


def is_locked() -> bool:
    return bool(_locked_hwnd) and _saved_style is not None


def _outer_size(hwnd: int) -> Tuple[int, int]:
    _need_win32()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return int(right - left), int(bottom - top)


def _client_size(hwnd: int) -> Tuple[int, int]:
    _need_win32()
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    return int(right - left), int(bottom - top)


def _outer_for_client(hwnd: int, client_w: int, client_h: int) -> Tuple[int, int]:
    _need_win32()
    """按当前窗口样式，把目标客户区换算成整窗外框尺寸。"""
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    rect = RECT(0, 0, int(client_w), int(client_h))
    windll.user32.AdjustWindowRectEx(byref(rect), style, False, ex)
    return int(rect.right - rect.left), int(rect.bottom - rect.top)


def resize_outer(hwnd: int, width: int, height: int) -> bool:
    _need_win32()
    """整窗调到指定宽高，保持左上角不动。"""
    try:
        left, top, _r, _b = win32gui.GetWindowRect(hwnd)
        win32gui.SetWindowPos(
            hwnd,
            None,
            left,
            top,
            int(width),
            int(height),
            win32con.SWP_NOZORDER | win32con.SWP_NOMOVE,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def resize_to_client(hwnd: int, client_w: int, client_h: int) -> bool:
    _need_win32()
    """把客户区调到指定宽高（外框自动加边框/标题栏）。"""
    try:
        ow, oh = _outer_for_client(hwnd, client_w, client_h)
        if not resize_outer(hwnd, ow, oh):
            return False
        # 锁边框后 chrome 可能变，再校正一次
        cw, ch = _client_size(hwnd)
        if abs(cw - client_w) > 2 or abs(ch - client_h) > 2:
            ow, oh = _outer_for_client(hwnd, client_w, client_h)
            resize_outer(hwnd, ow, oh)
        return True
    except Exception:  # noqa: BLE001
        return False


def lock_game_window(
    title_keyword: str,
    width: int = 390,
    height: int = 844,
) -> bool:
    _need_win32()
    """先恢复到配置的固定抽卡客户区尺寸，再锁定禁止拖边框缩放。"""
    global _locked_hwnd, _saved_style, _locked_outer, _locked_client

    hwnd = find_window_hwnd(title_keyword)
    if not hwnd:
        return False

    tw = max(280, int(width))
    th = max(400, int(height))

    if _locked_hwnd and _locked_hwnd != hwnd:
        unlock_game_window()

    try:
        # 已锁定则先恢复可缩放样式，再改尺寸
        if _locked_hwnd == hwnd and _saved_style is not None:
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, _saved_style)
            win32gui.SetWindowPos(
                hwnd,
                None,
                0,
                0,
                0,
                0,
                win32con.SWP_NOZORDER
                | win32con.SWP_NOMOVE
                | win32con.SWP_NOSIZE
                | win32con.SWP_FRAMECHANGED,
            )

        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        if not (style & win32con.WS_THICKFRAME):
            style = style | _SIZE_STYLES
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)
            win32gui.SetWindowPos(
                hwnd,
                None,
                0,
                0,
                0,
                0,
                win32con.SWP_NOZORDER
                | win32con.SWP_NOMOVE
                | win32con.SWP_NOSIZE
                | win32con.SWP_FRAMECHANGED,
            )

        if not resize_to_client(hwnd, tw, th):
            return False

        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        _saved_style = int(style)
        _locked_client = (tw, th)
        _locked_hwnd = int(hwnd)

        new_style = style & ~_SIZE_STYLES
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, new_style)
        # 去掉可调边框后外框可能略变，再钉一次目标客户区
        resize_to_client(hwnd, tw, th)
        _locked_outer = _outer_size(hwnd)
        win32gui.SetWindowPos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            win32con.SWP_NOZORDER
            | win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_FRAMECHANGED,
        )
        return True
    except Exception:  # noqa: BLE001
        _locked_hwnd = 0
        _saved_style = None
        _locked_outer = None
        _locked_client = None
        return False


def unlock_game_window() -> bool:
    _need_win32()
    """恢复可缩放，并保持抽卡用的固定客户区尺寸。"""
    global _locked_hwnd, _saved_style, _locked_outer, _locked_client

    hwnd = _locked_hwnd
    style = _saved_style
    client = _locked_client
    outer = _locked_outer
    _locked_hwnd = 0
    _saved_style = None
    _locked_outer = None
    _locked_client = None

    if not hwnd or style is None:
        return False
    if not win32gui.IsWindow(hwnd):
        return False

    try:
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)
        win32gui.SetWindowPos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            win32con.SWP_NOZORDER
            | win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_FRAMECHANGED,
        )
        if client is not None:
            resize_to_client(hwnd, client[0], client[1])
        elif outer is not None:
            resize_outer(hwnd, outer[0], outer[1])
        return True
    except Exception:  # noqa: BLE001
        return False


def _enforce_size(hwnd: int) -> None:
    _need_win32()
    if _locked_client:
        try:
            tw, th = _locked_client
            cw, ch = _client_size(hwnd)
            if abs(cw - tw) <= 2 and abs(ch - th) <= 2:
                return
            resize_to_client(hwnd, tw, th)
            return
        except Exception:  # noqa: BLE001
            pass
    if not _locked_outer:
        return
    try:
        tw, th = _locked_outer
        ow, oh = _outer_size(hwnd)
        if abs(ow - tw) <= 2 and abs(oh - th) <= 2:
            return
        resize_outer(hwnd, tw, th)
    except Exception:  # noqa: BLE001
        pass


def enforce_locked_size(title_keyword: str = "") -> None:
    _need_win32()
    if not is_locked():
        return
    hwnd = _locked_hwnd
    if title_keyword:
        found = find_window_hwnd(title_keyword)
        if found:
            hwnd = found
    if hwnd:
        _enforce_size(int(hwnd))
