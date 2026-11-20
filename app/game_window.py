"""独立可调大小的 H5 游戏窗口（WebView2）。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .platform_compat import IS_MAC, mac_chromium_candidates, mac_chromium_path

GAME_WINDOW_TITLE = "Zizuo游戏窗"

DEFAULT_URL = (
    "https://mprogram.boomegg.cn/box/game/wx77200645d1c7f35f/h5"
    "?appid=wx77200645d1c7f35f&game_platform=h5"
)

SIZE_PRESETS = {
    "小屏 360x640": (360, 640),
    "常见 390x844": (390, 844),
    "中屏 414x896": (414, 896),
    "大屏 430x932": (430, 932),
}

# 用 Android 而不是 iPhone，避免弹出「添加到主屏幕」遮罩
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)

# 进页后去掉「添加到主屏幕」引导，露出登录框
DISMISS_OVERLAY_JS = r"""
(function () {
  function hideNode(el) {
    if (!el || !el.style) return;
    el.style.setProperty('display', 'none', 'important');
    el.style.setProperty('visibility', 'hidden', 'important');
    el.style.setProperty('pointer-events', 'none', 'important');
    try { el.remove(); } catch (e) {}
  }

  function looksLikeGuide(text) {
    if (!text) return false;
    return (
      text.indexOf('添加到主屏幕') >= 0 ||
      text.indexOf('从桌面进入') >= 0 ||
      text.indexOf('第1步') >= 0 ||
      text.indexOf('分享按钮') >= 0
    );
  }

  function sweep() {
    var all = document.querySelectorAll('div,section,aside,article');
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var t = (el.innerText || el.textContent || '').slice(0, 800);
      if (!looksLikeGuide(t)) continue;
      // 优先干掉整层遮罩，而不是只藏小标题
      var target = el;
      for (var d = 0; d < 4 && target.parentElement; d++) {
        var p = target.parentElement;
        var pt = (p.innerText || '').slice(0, 800);
        if (looksLikeGuide(pt) && p !== document.body && p !== document.documentElement) {
          target = p;
        } else {
          break;
        }
      }
      hideNode(target);
    }
    // 常见全屏 fixed/absolute 遮罩
    var candidates = document.querySelectorAll('[class*="guide"],[class*="mask"],[class*="overlay"],[id*="guide"],[id*="mask"]');
    for (var j = 0; j < candidates.length; j++) {
      var c = candidates[j];
      var ct = (c.innerText || c.textContent || '').slice(0, 800);
      if (looksLikeGuide(ct)) hideNode(c);
    }
  }

  sweep();
  setInterval(sweep, 800);
  try {
    Object.defineProperty(navigator, 'standalone', { get: function () { return true; } });
  } catch (e) {}
})();
"""


def webview_storage_dir() -> Path:
    """持久化 WebView2 用户数据（Cookie/登录态），类似参考脚本的 config/web。"""
    from .config_store import project_root

    path = project_root() / "saves" / "webview"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_window(url: str, width: int, height: int, user_agent: str) -> None:
    if IS_MAC:
        _launch_mac_chromium(url, width, height, user_agent)
        return

    import os

    # 供助手通过 CDP 真实点击（解决系统模拟点击在 WebView2 上无效）
    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--remote-debugging-port=9229 --remote-allow-origins=*",
    )

    try:
        import webview
    except ImportError:
        print("缺少 pywebview，请先安装: pip install pywebview")
        sys.exit(1)

    width = max(280, int(width))
    height = max(400, int(height))
    storage = str(webview_storage_dir())

    window = webview.create_window(
        f"{GAME_WINDOW_TITLE} [{width}x{height}]",
        url=url,
        width=width,
        height=height,
        resizable=True,
        confirm_close=False,
        text_select=True,
    )

    def _refresh_title():
        try:
            w, h = window.get_size()
            window.set_title(f"{GAME_WINDOW_TITLE} [{w}x{h}]")
        except Exception:  # noqa: BLE001
            pass

    def _on_loaded():
        _refresh_title()
        try:
            window.evaluate_js(DISMISS_OVERLAY_JS)
        except Exception:  # noqa: BLE001
            pass

    try:
        window.events.loaded += _on_loaded
    except Exception:  # noqa: BLE001
        pass
    try:
        window.events.resized += lambda *_a, **_k: _refresh_title()
    except Exception:  # noqa: BLE001
        pass

    # private_mode 默认 True 会清 Cookie，导致每次都要重新验证码登录
    attempts = [
        {
            "gui": "edgechromium",
            "debug": False,
            "private_mode": False,
            "storage_path": storage,
            "user_agent": user_agent,
        },
        {
            "gui": "edgechromium",
            "private_mode": False,
            "storage_path": storage,
            "user_agent": user_agent,
        },
        {"gui": "edgechromium", "private_mode": False, "storage_path": storage},
        {"gui": "edgechromium", "private_mode": False},
        {"gui": "edgechromium"},
    ]
    last_err: Exception | None = None
    for kwargs in attempts:
        try:
            webview.start(**{k: v for k, v in kwargs.items() if v is not None})
            return
        except TypeError as exc:
            last_err = exc
            continue
    if last_err:
        raise last_err


def _launch_mac_chromium(
    url: str, width: int, height: int, user_agent: str, browser: Optional[str] = None
) -> subprocess.Popen:
    """macOS：没有 WebView2，直接开 Chrome/Edge 的独立窗口 + 9229 调试端口。"""
    exe = (browser or "").strip() or mac_chromium_path()
    if not exe or not Path(exe).exists():
        tried = "\n".join("- " + item for item in mac_chromium_candidates()[:6])
        raise RuntimeError(
            "没有找到 Chrome 或 Edge。\n"
            "请先安装 Google Chrome / Microsoft Edge，再点「打开游戏窗口」。\n"
            "如果已经装了，可以在 config.json 里把 browser_path 填成浏览器的可执行文件路径。\n"
            "已检查：\n" + tried
        )
    from .config_store import project_root

    profile = project_root() / "saves" / "chrome-profile"
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        exe,
        "--remote-debugging-port=9229",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}",
        f"--app={url}",
        f"--window-size={int(width)},{int(height)}",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-agent={user_agent}",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_game_window(
    url: str,
    width: int = 390,
    height: int = 844,
    user_agent: Optional[str] = None,
    browser: Optional[str] = None,
) -> subprocess.Popen:
    """用独立进程打开游戏窗，不阻塞主界面。"""
    if IS_MAC:
        return _launch_mac_chromium(url, width, height, user_agent or MOBILE_UA, browser)
    from .config_store import project_root

    ua = user_agent or MOBILE_UA
    root = project_root()
    if getattr(sys, "frozen", False):
        # PyInstaller：复用同一 exe
        cmd: List[str] = [
            sys.executable,
            "--game-window",
            "--url",
            url,
            "--width",
            str(int(width)),
            "--height",
            str(int(height)),
            "--ua",
            ua,
        ]
    else:
        cmd = [
            sys.executable,
            "-m",
            "app.game_window",
            "--url",
            url,
            "--width",
            str(int(width)),
            "--height",
            str(int(height)),
            "--ua",
            ua,
        ]
    return subprocess.Popen(cmd, cwd=str(root))


def main() -> None:
    parser = argparse.ArgumentParser(description="打开可调大小的 H5 游戏窗口")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--width", type=int, default=390)
    parser.add_argument("--height", type=int, default=844)
    parser.add_argument("--ua", default=MOBILE_UA)
    args = parser.parse_args()
    _run_window(args.url, args.width, args.height, args.ua)


if __name__ == "__main__":
    main()
