"""后台点击：不抢前台、不占用用户鼠标（对齐参考脚本后台消息点击）。"""

from __future__ import annotations

import time
from typing import Optional, Tuple

try:
    import win32api
    import win32con
    import win32gui
    WIN32_OK = True
except ImportError:  # macOS / Linux
    win32api = None
    win32con = None
    win32gui = None
    WIN32_OK = False

from .platform_compat import require_windows


def _need_win32() -> None:
    if not WIN32_OK:
        require_windows()



def sleep_ms(ms: int) -> None:
    time.sleep(max(0, ms) / 1000.0)


def offset_point(point: Tuple[int, int], dx: int = 0, dy: int = 0) -> Tuple[int, int]:
    return point[0] + dx, point[1] + dy


def find_chrome_ctrl(hwnd: int) -> Optional[int]:
    _need_win32()
    """找 WebView2/Chromium 可收鼠标消息的子窗。"""
    if not hwnd:
        return None
    preferred = (
        "Chrome_RenderWidgetHostHWND",
        "Chrome_WidgetWin_1",
        "Chrome_WidgetWin_0",
        "Intermediate D3D Window",
    )
    found: dict[str, int] = {}

    def enum(child: int, _):
        try:
            cls = win32gui.GetClassName(child)
            if cls in preferred:
                found[cls] = int(child)
        except Exception:  # noqa: BLE001
            pass

    try:
        win32gui.EnumChildWindows(int(hwnd), enum, None)
    except Exception:  # noqa: BLE001
        return int(hwnd)
    for cls in preferred:
        if cls in found:
            return found[cls]
    return int(hwnd)


def _makelong(x: int, y: int) -> int:
    return (int(y) << 16) | (int(x) & 0xFFFF)


def message_click(
    hwnd: int,
    client_x: int,
    client_y: int,
    *,
    clicks: int = 1,
    button: str = "left",
) -> bool:
    _need_win32()
    """向 Chromium 子窗发鼠标消息（后台，不移动系统光标）。"""
    if not hwnd:
        return False
    ctrl = find_chrome_ctrl(int(hwnd))
    if not ctrl:
        return False

    # 客户区坐标：截图相对顶层客户区；子窗通常铺满客户区
    # 若子窗与顶层客户区原点不同，按屏幕差换算
    try:
        top_origin = win32gui.ClientToScreen(int(hwnd), (0, 0))
        ctrl_origin = win32gui.ClientToScreen(ctrl, (0, 0))
        lx = int(client_x) + (top_origin[0] - ctrl_origin[0])
        ly = int(client_y) + (top_origin[1] - ctrl_origin[1])
    except Exception:  # noqa: BLE001
        lx, ly = int(client_x), int(client_y)

    if button == "right":
        down, up = win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP
        mk = win32con.MK_RBUTTON
    else:
        down, up = win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP
        mk = win32con.MK_LBUTTON

    lp = _makelong(lx, ly)
    try:
        for _ in range(max(1, int(clicks))):
            win32gui.PostMessage(ctrl, win32con.WM_MOUSEMOVE, 0, lp)
            time.sleep(0.02)
            win32gui.PostMessage(ctrl, down, mk, lp)
            time.sleep(0.05)
            win32gui.PostMessage(ctrl, up, 0, lp)
            time.sleep(0.04)
        return True
    except Exception:  # noqa: BLE001
        return False


def click_in_client(
    hwnd: int,
    client_x: int,
    client_y: int,
    *,
    screen_left: int = 0,
    screen_top: int = 0,
    clicks: int = 1,
    button: str = "left",
    allow_steal_mouse: bool = False,
) -> str:
    _need_win32()
    """后台点击客户区坐标。

    优先 CDP → 消息点击 →（可选）系统鼠标。
    默认不抢前台、不移动用户鼠标。
    返回: 'cdp' | 'message' | 'sendinput' | 'fail'
    """
    # 1) CDP：不依赖前台/鼠标
    try:
        from .cdp_click import cdp_available, click_css

        if hwnd and cdp_available():
            if click_css(float(client_x), float(client_y)):
                time.sleep(0.06)
                return "cdp"
    except Exception:  # noqa: BLE001
        pass

    # 2) 参考脚本同款：子窗 PostMessage 后台点击
    if hwnd and message_click(
        int(hwnd), client_x, client_y, clicks=clicks, button=button
    ):
        time.sleep(0.06)
        return "message"

    # 3) 仅显式允许时才动用户鼠标
    if allow_steal_mouse:
        click_at(
            int(screen_left) + int(client_x),
            int(screen_top) + int(client_y),
            clicks=clicks,
            button=button,
        )
        return "sendinput"
    return "fail"


def click_at(
    x: int,
    y: int,
    clicks: int = 1,
    button: str = "left",
    restore_cursor: bool = True,
) -> None:
    _need_win32()
    """系统鼠标点击（会干扰用户，仅作兜底）。"""
    from ctypes import Structure, Union, byref, sizeof, windll
    from ctypes import wintypes

    user32 = windll.user32

    class MOUSEINPUT(Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", wintypes.LPARAM),
        )

    class _INPUT_UNION(Union):
        _fields_ = (("mi", MOUSEINPUT),)

    class INPUT(Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", _INPUT_UNION))

    def to_abs(px: int, py: int) -> Tuple[int, int]:
        vs_left = user32.GetSystemMetrics(76)
        vs_top = user32.GetSystemMetrics(77)
        vs_w = user32.GetSystemMetrics(78)
        vs_h = user32.GetSystemMetrics(79)
        ax = int(round((int(px) - vs_left) * 65535 / max(vs_w - 1, 1)))
        ay = int(round((int(py) - vs_top) * 65535 / max(vs_h - 1, 1)))
        return ax, ay

    old = win32api.GetCursorPos() if restore_cursor else None
    ix, iy = int(x), int(y)
    if button == "right":
        down, up = win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP
    else:
        down, up = win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP
    abs_move = win32con.MOUSEEVENTF_MOVE | win32con.MOUSEEVENTF_ABSOLUTE

    def send(flags: int) -> None:
        ax, ay = to_abs(ix, iy)
        inp = INPUT()
        inp.type = 0
        inp.union.mi = MOUSEINPUT(ax, ay, 0, flags, 0, 0)
        user32.SendInput(1, byref(inp), sizeof(INPUT))

    send(abs_move)
    time.sleep(0.04)
    for _ in range(max(1, int(clicks))):
        send(abs_move | down)
        time.sleep(0.06)
        send(abs_move | up)
        time.sleep(0.04)
    time.sleep(0.08)
    if old is not None:
        try:
            win32api.SetCursorPos(old)
        except Exception:  # noqa: BLE001
            pass


def move_to(x: int, y: int) -> None:
    _need_win32()
    """仅兜底用；后台模式请勿调用。"""
    click_at(x, y, clicks=0, restore_cursor=False)


def focus_window(hwnd: int) -> bool:
    _need_win32()
    """已废弃用于抽卡/脚本：后台模式不抢前台。保留给调试。"""
    if not hwnd:
        return False
    try:
        import win32process

        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        return True
    except Exception:  # noqa: BLE001
        return False
