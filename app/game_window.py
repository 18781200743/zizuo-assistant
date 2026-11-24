"""独立可调大小的 H5 游戏窗口（WebView2）。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
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

# 游戏默认用手机 UA，登录接口不会返回 wxPcLoginLink，登录页就把“微信扫码”藏了。
# 这里在游戏窗进程里直接补上官方扫码链接并显示入口，不依赖助手 CDP 是否连上。
WECHAT_LOGIN_JS = r"""
(function () {
  if (window.__zzWechatLoginInstalled) return;
  window.__zzWechatLoginInstalled = true;

  var QR_APPID = 'wx565e856587e12457';
  var QR_REDIRECT = 'https%3A%2F%2Fsprogram.xnxykj.cn%2Fbox%2Fh5%2Fwxpc_login_redirect';

  function wechatLink() {
    var appid = '';
    try {
      var cfg = window.AladinConfig || {};
      appid = (cfg.appIdConfig && cfg.appIdConfig.h5) || '';
    } catch (e) {}
    return 'https://open.weixin.qq.com/connect/qrconnect?appid=' + QR_APPID
      + '&redirect_uri=' + QR_REDIRECT
      + '&response_type=code&scope=snsapi_login&state='
      + encodeURIComponent(appid || 'wx77200645d1c7f35f')
      + '#wechat_redirect';
  }

  function showWechat() {
    try {
      var link = localStorage.getItem('wxPcLoginLink');
      if (!link) {
        link = wechatLink();
        localStorage.setItem('wxPcLoginLink', link);
      }
      var frames = document.querySelectorAll('iframe');
      var phone = null;
      for (var i = 0; i < frames.length; i++) {
        if (String(frames[i].src || '').indexOf('phone.html') >= 0) {
          phone = frames[i];
          break;
        }
      }
      if (!phone) return;
      var doc = phone.contentDocument;
      if (!doc) return;
      var section = doc.getElementById('other-login-section');
      if (!section) return;
      if (section.style.display !== 'flex') section.style.display = 'flex';
    } catch (e) {}
  }

  showWechat();
  setInterval(showWechat, 1000);
})();
"""



WINDOWS_CHROMIUM_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def windows_chromium_candidates() -> List[str]:
    """Edge / Chrome 的常见安装路径（含当前用户安装）。"""
    out = list(WINDOWS_CHROMIUM_PATHS)
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        out.extend([
            str(Path(local) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            str(Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ])
    return out


def windows_chromium_path() -> Optional[str]:
    for item in windows_chromium_candidates():
        try:
            if Path(item).exists():
                return item
        except OSError:
            continue
    return None


def _cleanup_profile_processes(image_name: str, profile: str) -> None:
    """清掉上次崩溃遗留、仍占用同一用户目录的浏览器进程（Win10 常见）。"""
    if sys.platform != "win32" or not profile:
        return
    marker = profile.lower()
    try:
        raw = subprocess.check_output(
            ["wmic", "process", "where", "name='%s'" % image_name,
             "get", "ProcessId,CommandLine", "/format:csv"],
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
        text = raw.decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return
    for line in text.splitlines():
        if marker not in line.lower():
            continue
        parts = [part.strip() for part in line.split(",")]
        pid = parts[-1] if parts else ""
        if not pid.isdigit():
            continue
        try:
            subprocess.run(
                ["taskkill", "/PID", pid, "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=6,
            )
        except Exception:  # noqa: BLE001
            pass


def _keep_window_title(pid: int, title: str) -> None:
    """Edge/Chrome 的 --app 窗口标题默认是网页标题，持续改回固定标题。"""
    if sys.platform != "win32":
        return

    def worker() -> None:
        try:
            import win32gui
            import win32process
        except Exception:  # noqa: BLE001
            return
        deadline = time.time() + 20
        while time.time() < deadline:
            matched = []

            def callback(hwnd, _):
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        return
                    cls = win32gui.GetClassName(hwnd)
                    if "Chrome_WidgetWin" not in cls:
                        return
                    _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
                    if int(win_pid) != int(pid):
                        return
                    if win32gui.GetWindowText(hwnd):
                        rect = win32gui.GetWindowRect(hwnd)
                        area = max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])
                        matched.append((area, hwnd))
                except Exception:  # noqa: BLE001
                    return

            try:
                win32gui.EnumWindows(callback, None)
                for _, hwnd in sorted(matched, reverse=True)[:1]:
                    win32gui.SetWindowText(hwnd, title)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)

    threading.Thread(target=worker, daemon=True).start()


def _launch_windows_chromium(
    url: str,
    width: int,
    height: int,
    user_agent: str,
    browser: Optional[str] = None,
    port: int = 9229,
) -> subprocess.Popen:
    """Win10 兼容模式：用 Edge/Chrome 的独立 app 窗口 + 9229 调试端口。"""
    exe = (browser or "").strip() or windows_chromium_path()
    if not exe or not Path(exe).exists():
        tried = "\n".join("- " + item for item in windows_chromium_candidates()[:6])
        raise RuntimeError(
            "没有找到 Microsoft Edge 或 Google Chrome。\n"
            "请先安装 Edge / Chrome，再点「打开游戏窗口」。\n"
            "如果已经装了，可以在 config.json 里把 browser_path 填成浏览器的 exe 路径。\n"
            "已检查：\n" + tried
        )
    from .config_store import project_root

    name = "edge-profile" if "msedge" in Path(exe).name.lower() else "chrome-profile"
    profile = project_root() / "saves" / name
    profile.mkdir(parents=True, exist_ok=True)
    _cleanup_profile_processes(Path(exe).name, str(profile))
    cmd = [
        exe,
        f"--remote-debugging-port={int(port)}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}",
        f"--app={url}",
        f"--window-size={int(width)},{int(height)}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=IntensiveWakeUpThrottling,CalculateNativeWinOcclusion",
        f"--user-agent={user_agent}",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _keep_window_title(proc.pid, GAME_WINDOW_TITLE)
    return proc


def terminate_game_window(proc: Optional[subprocess.Popen]) -> None:
    """结束游戏窗口及其子进程（WebView2/Edge 子进程）。"""
    if proc is None:
        return
    if proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
    except Exception:  # noqa: BLE001
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass


def webview_storage_dir() -> Path:
    """持久化 WebView2 用户数据（Cookie/登录态），类似参考脚本的 config/web。"""
    from .config_store import project_root

    path = project_root() / "saves" / "webview"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_window(
    url: str,
    width: int,
    height: int,
    user_agent: str,
    backend: str = "webview2",
    port: int = 9229,
) -> None:
    if IS_MAC:
        _launch_mac_chromium(url, width, height, user_agent)
        return

    backend = (backend or "webview2").strip().lower()
    if backend in ("edge", "chrome", "browser"):
        _launch_windows_chromium(url, width, height, user_agent)
        return

    import os

    # 供助手通过 CDP 真实点击（解决系统模拟点击在 WebView2 上无效）
    # 下面几个参数专门关掉后台节流：
    #   --disable-background-timer-throttling   后台 setInterval 不再压到 1 秒
    #   --disable-backgrounding-occluded-windows 窗口被遮住不算「后台」
    #   --disable-renderer-backgrounding        渲染进程不降优先级
    #   --disable-features=IntensiveWakeUpThrottling  藏了 5 分钟后不再压到 1 分钟一次
    #   --disable-features=CalculateNativeWinOcclusion 不让 Windows 把窗口判成不可见
    # 注意：Chrome 只认最后一个 --disable-features，所以两个特性写在同一个参数里。
    browser_args = (
        f"--remote-debugging-port={int(port)} --remote-allow-origins=*"
        " --disable-background-timer-throttling"
        " --disable-backgrounding-occluded-windows"
        " --disable-renderer-backgrounding"
        " --disable-features=IntensiveWakeUpThrottling,CalculateNativeWinOcclusion"
    )
    existing = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "").strip()
    if "--remote-debugging-port" in existing:
        # 已经有人设过（比如用户自己加了参数）：保留，但把节流参数补上
        for flag in browser_args.split():
            if flag not in existing:
                existing += " " + flag
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = existing
    else:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            (existing + " " + browser_args).strip() if existing else browser_args
        )

    try:
        import webview
    except ImportError:
        print("缺少 pywebview，请先安装: pip install pywebview")
        sys.exit(1)

    width = max(280, int(width))
    height = max(400, int(height))
    storage = str(webview_storage_dir())
    _cleanup_profile_processes("msedgewebview2.exe", storage)

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
            window.evaluate_js(WECHAT_LOGIN_JS)
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
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=IntensiveWakeUpThrottling,CalculateNativeWinOcclusion",
        f"--user-agent={user_agent}",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_game_window(
    url: str,
    width: int = 390,
    height: int = 844,
    user_agent: Optional[str] = None,
    browser: Optional[str] = None,
    backend: str = "webview2",
    port: int = 9229,
) -> subprocess.Popen:
    """用独立进程打开游戏窗，不阻塞主界面。"""
    ua = user_agent or MOBILE_UA
    backend = (backend or "webview2").strip().lower()
    if backend not in ("auto", "webview2", "edge", "chrome", "browser"):
        backend = "webview2"
    if IS_MAC:
        return _launch_mac_chromium(url, width, height, ua, browser)
    if backend in ("edge", "chrome", "browser"):
        return _launch_windows_chromium(url, width, height, ua, browser, port)
    from .config_store import project_root

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
            "--backend",
            backend,
            "--port",
            str(int(port)),
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
            "--backend",
            backend,
            "--port",
            str(int(port)),
        ]
    return subprocess.Popen(cmd, cwd=str(root))


def main() -> None:
    parser = argparse.ArgumentParser(description="打开可调大小的 H5 游戏窗口")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--width", type=int, default=390)
    parser.add_argument("--height", type=int, default=844)
    parser.add_argument("--ua", default=MOBILE_UA)
    parser.add_argument("--backend", default="webview2")
    parser.add_argument("--port", type=int, default=9229)
    args = parser.parse_args()
    _run_window(args.url, args.width, args.height, args.ua, args.backend, args.port)


if __name__ == "__main__":
    main()
