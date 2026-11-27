"""主界面：首页、抽卡、辅助工具、运行命令。"""

from __future__ import annotations

import csv
import os
import json
import queue
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Dict, List

try:  # Windows 全局热键；macOS 上没有这个库，界面按钮照样能用
    import keyboard
except Exception:  # noqa: BLE001
    keyboard = None

from .config_store import (
    load_config,
    project_root,
    normalize_assist,
    save_config,
)
from .gacha_store import (
    CARD_CATALOG,
    list_available_card_templates,
    list_ui_template_status,
    normalize_gacha,
    refresh_card_catalog,
    ui_dir,
)
from .game_window import (
    GAME_WINDOW_TITLE,
    SIZE_PRESETS,
    open_game_window,
    terminate_game_window,
)
from .cdp_click import find_page_ws
from .game_assist import GameAssist, load_mapping
from .instruction_library import (
    CATEGORY_ALL,
    format_content,
    library_instructions,
    load_library,
    load_sync_presets,
    script_title,
)
from .license import is_super_key, is_valid_key, make_super_key
from .instruction_runner import InstructionRunner
from .chest_4758a import run_chest_4758a
from .commands_store import SLOT_COUNT, load_slots, save_slots, set_slot, slot_label
from .command_recorder import CommandRecorder
from .build_info import APP_VERSION, dev_mode
from .gacha_cdp import GachaCdp

# 是否允许「用系统浏览器打开游戏」。
# 先关闭：浏览器里没有 9229 调试端口，辅助/抽卡注入不了。以后要恢复改成 True。
ENABLE_BROWSER_OPEN = False

# 抽卡固定目标名单（要保留的英雄）；界面可勾选，保存在 config 的 gacha_cdp.checked
# 参考工具「最大倍数招募」页面上的 19 个英雄（编号已按游戏内数据核对）
GACHA_POOL = [
    (84007, "敖丙"), (84008, "雅丽娜"), (84013, "乌瑟尔"), (84016, "卡卡西"),
    (84017, "达斯坦"), (84018, "杜康"), (85001, "李白"), (85002, "玛法里奥"),
    (82005, "莱特宁"), (83003, "王昭君"), (83027, "史矛革"), (83030, "卡尔"),
    (83031, "吉尔"), (84003, "亚瑟"), (84005, "缘壹"), (84009, "拉斐尔"),
    (84011, "谢必安"), (84014, "莉娜"), (84019, "甘道夫"),
]

# 品级（游戏 yx_mulu 里读出来的）：6 = 红，5 = 橙。红卡第一屏那 8 个
GACHA_QUALITY = {
    84007: 6, 84008: 6, 84013: 6, 84016: 6, 84017: 6, 84018: 6,
    85001: 6, 85002: 6,
    82005: 5, 83003: 5, 83027: 5, 83030: 5, 83031: 5,
    84003: 5, 84005: 5, 84009: 5, 84011: 5, 84014: 5, 84019: 5,
}

DEFAULT_FIXED_HEROES = [
    (84007, "敖丙"),
    (84008, "雅丽娜"),
    (84013, "乌瑟尔"),
    (84016, "卡卡西"),
    (84017, "达斯坦"),
    (84018, "杜康"),
    (85001, "李白"),
    (85002, "玛法里奥"),
    (84003, "亚瑟"),
    (84004, "阿狸"),
    (84005, "缘壹"),
    (84009, "拉斐尔"),
    (84011, "谢必安"),
    (84019, "甘道夫"),
    (83031, "吉尔"),
]
from .window_lock import lock_game_window, unlock_game_window

DEFAULT_GAME_URL = (
    "https://mprogram.boomegg.cn/box/game/wx77200645d1c7f35f/h5"
    "?appid=wx77200645d1c7f35f&game_platform=h5"
)


def _seconds_text(value: Any) -> str:
    """2.0 → '2'，0.5 → '0.5'。"""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "1"


# 视野（初始索敌）三档：参考工具里 0 = 关闭初始索敌，1 = 把半径放大到 1400
VISION_OFF = "关闭"
VISION_ZERO = "0 · 不让怪发现"
VISION_WIDE = "1 · 扩大索敌"
VISION_CHOICES = [VISION_OFF, VISION_ZERO, VISION_WIDE]


def _fmt_hp(value: Any) -> str:
    """血量显示：1.2亿 / 15.8万 / 3780。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number >= 1e8:
        return "%.1f亿" % (number / 1e8)
    if number >= 1e4:
        return "%.1f万" % (number / 1e4)
    return "%d" % number


def _vision_text(mode: Any) -> str:
    try:
        number = int(mode)
    except (TypeError, ValueError):
        return VISION_OFF
    return {0: VISION_ZERO, 1: VISION_WIDE}.get(number, VISION_OFF)


def _vision_mode(text: Any) -> int:
    """界面文字 → -1 关闭 / 0 不让怪发现 / 1 扩大索敌。"""
    value = str(text or "")
    if value.startswith("0"):
        return 0
    if value.startswith("1"):
        return 1
    return -1


# 快速复活方式
REVIVE_INSTANT = "立即复活（原地秒起，20 绿币/次）"
REVIVE_TP = "快速复活（免费，0.1 秒，原地，怪不回血，推荐）"
REVIVE_FREE = "免费回营地复活（不花道具/广告）"
REVIVE_ITEM = "道具复活（消耗复活道具）"
REVIVE_AD = "广告复活（看广告，不花道具）"
REVIVE_CAMP = "回营地复活（放弃战斗，回营地满血）"
REVIVE_CAMPFLY = "回营地复活（队伍留在营地，不飞回）"
REVIVE_CHOICES = [REVIVE_TP, REVIVE_INSTANT, REVIVE_FREE, REVIVE_ITEM, REVIVE_AD, REVIVE_CAMP, REVIVE_CAMPFLY]


def _revive_text(mode: Any) -> str:
    return {
        "instant": REVIVE_INSTANT,
        "tp": REVIVE_TP,
        "free": REVIVE_FREE,
        "ad": REVIVE_AD,
        "camp": REVIVE_CAMP,
        "campfly": REVIVE_CAMPFLY,
    }.get(str(mode or "").strip().lower(), REVIVE_ITEM)


def _revive_mode(text: Any) -> str:
    value = str(text or "")
    if value == REVIVE_INSTANT:
        return "instant"
    if value == REVIVE_TP:
        return "tp"
    if value == REVIVE_FREE:
        return "free"
    if value == REVIVE_AD:
        return "ad"
    if value == REVIVE_CAMP:
        return "camp"
    if value == REVIVE_CAMPFLY:
        return "campfly"
    return "item"


# 单英雄复活的方式
HERO_REVIVE_TIMER = "本地计时复活（倒计时改 N 秒）"
HERO_REVIVE_ITEM = "服务器复活（道具）"
HERO_REVIVE_AD = "服务器复活（广告）"
HERO_REVIVE_CAMPFLY = "回营地复活（队伍留在营地）"
HERO_REVIVE_CHOICES = [
    HERO_REVIVE_ITEM,
    HERO_REVIVE_AD,
    HERO_REVIVE_CAMPFLY,
]


def _hero_revive_text(mode: Any) -> str:
    return {
        "timer": HERO_REVIVE_TIMER,
        "ad": HERO_REVIVE_AD,
        "campfly": HERO_REVIVE_CAMPFLY,
    }.get(str(mode or "").strip().lower(), HERO_REVIVE_ITEM)


def _hero_revive_mode(text: Any) -> str:
    value = str(text or "")
    if value == HERO_REVIVE_TIMER:
        return "timer"
    if value == HERO_REVIVE_AD:
        return "ad"
    if value == HERO_REVIVE_CAMPFLY:
        return "campfly"
    return "item"


class _AutoChestAdapter:
    """给 run_chest_4758a 用的最小 runner 适配器。"""

    def __init__(self, bridge_provider, log, stop):
        self._bridge_provider = bridge_provider
        self._log = log
        self._stop = stop

    def _eval(self, js: str) -> Any:
        bridge = self._bridge_provider()
        if bridge is None:
            raise RuntimeError("还没连上游戏窗口")
        return bridge.eval_js(js)

    def _log_line(self, text: str) -> None:
        self._log(str(text))

    def _sleep(self, seconds: float) -> None:
        time.sleep(float(seconds))


class AppUI:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("小太阳")
        _w, _h = 480, 760
        try:
            _sw = self.root.winfo_screenwidth()
            _sh = self.root.winfo_screenheight()
            _x = max(0, (_sw - _w) // 2)
            _y = max(0, (_sh - _h) // 3)
            self.root.geometry("%dx%d+%d+%d" % (_w, _h, _x, _y))
        except Exception:  # noqa: BLE001
            self.root.geometry("480x760")
        # 固定窗口大小：拉大后缩回来会错位，干脆不让拉伸
        self.root.resizable(False, False)
        self._apply_window_icon()

        self.config: Dict[str, Any] = load_config()
        # 后台线程只往队列里丢日志，由 UI 线程统一写进日志框（跨线程操作 Tk 会卡死/丢日志）
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        assist_cfg = normalize_assist(self.config.get("assist"))
        self.assist = GameAssist(
            self.append_log,
            port=int(assist_cfg.get("cdp_port") or 9229),
        )
        self._auto_chest_stop = threading.Event()
        self._auto_chest_stop.set()
        self._auto_chest_thread = None
        self._auto_chest_q: "queue.Queue[str]" = queue.Queue()
        self.assist.names = {
            "bossKeywords": assist_cfg.get("boss_keywords") or [],
            "bossIds": assist_cfg.get("boss_ids") or [],
        }
        for key, value in (assist_cfg.get("names") or {}).items():
            self.assist.names[key] = value
        self._hotkeys_bound = False
        self._game_proc = None
        self._assist_auto_started = False
        self.instruction_runner = InstructionRunner(
            lambda: getattr(self.assist, "bridge", None),
            log=self._instruction_log,
            on_step=self._on_instruction_step,
            on_finished=self._on_instruction_finished,
            on_slot=self._on_instruction_slot,
        )
        self._cmd_events: "queue.Queue[tuple]" = queue.Queue()
        # 开发模式：源码运行显示开发功能；打包成 exe 默认隐藏（--dev / ZIZUO_DEV=1 可强制打开）
        self.dev_mode = dev_mode()
        self.card_vars: Dict[str, tk.BooleanVar] = {}
        self._frag_base: Dict[str, int] = {}
        self._gold_base = None

        self._init_dark_styles()
        self._build()
        try:
            self.root.after(150, self._pump_cmd_events)
            self.root.after(300, self._pump_auto_chest_log)
        except tk.TclError:
            pass
        self.refresh_gacha_templates()
        self.bind_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # 开界面就自动接上游戏窗（参考工具那样：不用点「启动辅助」）
        self.root.after(1200, self._auto_connect_assist)

    def _setup_style(self) -> None:
        """统一配色和控件风格（白底 + 浅灰分隔 + 蓝色主按钮）。"""
        self.colors = {
            "page": "#ffffff",
            "card": "#ffffff",
            "line": "#e5e7eb",
            "text": "#111827",
            "sub": "#6b7280",
            "accent": "#2563eb",
            "soft": "#f3f4f6",
            "green": "#059669",
            "red": "#dc2626",
        }
        c = self.colors
        self.root.configure(bg=c["page"])
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Microsoft YaHei UI", 9), background=c["page"], foreground=c["text"])
        style.configure("TFrame", background=c["page"])
        style.configure("TLabelframe", background=c["card"], bordercolor=c["line"], relief="solid", borderwidth=1)
        style.configure(
            "TLabelframe.Label",
            background=c["card"],
            foreground="#4b5563",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure("TLabel", background=c["card"], foreground=c["text"])
        style.configure("TButton", padding=(10, 4))
        style.configure("Accent.TButton", background=c["accent"], foreground="#ffffff", padding=(14, 6))
        style.map(
            "Accent.TButton",
            background=[("active", "#1d4ed8"), ("pressed", "#1e40af"), ("disabled", "#93b4f5")],
            foreground=[("disabled", "#f3f4f6")],
        )
        style.configure("TEntry", padding=4)
        style.configure("TCheckbutton", background=c["card"])
        style.configure("TCombobox", padding=2)
        style.configure("TSeparator", background=c["line"])
        style.configure("Treeview", background=c["card"], fieldbackground=c["card"], rowheight=24)
        style.configure(
            "Home.TButton",
            font=("Microsoft YaHei UI", 14),
            padding=(0, 14),
        )

    DARK_PAGE = "#231e3d"
    DARK_CARD = "#16233c"
    DARK_LINE = "#2a3b5c"
    DARK_TXT = "#e6ebf5"
    DARK_SUB = "#9fb0c9"

    def _init_dark_styles(self) -> None:
        """深色控件样式（只给要用深色的页面套）。"""
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        dark, card, txt, sub, line = (
            self.DARK_PAGE, self.DARK_CARD, self.DARK_TXT, self.DARK_SUB, self.DARK_LINE,
        )
        style.configure("Dark.TFrame", background=dark)
        style.configure("Dark.TLabel", background=dark, foreground=txt)
        style.configure(
            "Dark.TLabelframe", background=dark, bordercolor=line, relief="solid", borderwidth=1
        )
        style.configure("Dark.TLabelframe.Label", background=dark, foreground="#8ecbff")
        style.configure(
            "Dark.TButton", background=card, foreground=txt, bordercolor=line,
            focusthickness=0, relief="flat", padding=(6, 3),
        )
        style.map("Dark.TButton", background=[("active", "#24365a")])
        style.configure(
            "Dark.Accent.TButton", background="#2f7fe8", foreground="#ffffff",
            bordercolor="#2f7fe8",
        )
        style.map("Dark.Accent.TButton", background=[("active", "#2568c7")])
        style.configure("Dark.TCheckbutton", background=dark, foreground=txt)
        style.map("Dark.TCheckbutton", background=[("active", dark)])
        style.configure(
            "Dark.TEntry", fieldbackground="#1b2a44", background="#1b2a44",
            foreground=txt, insertcolor=txt, bordercolor=line,
            lightcolor=line, darkcolor=line,
        )
        style.map(
            "Dark.TEntry",
            fieldbackground=[("disabled", "#16233c"), ("readonly", "#16233c")],
            foreground=[("disabled", sub)],
        )
        style.configure(
            "Dark.TCombobox", fieldbackground="#1b2a44", background="#1b2a44",
            foreground=txt, arrowcolor=txt, bordercolor=line,
            lightcolor=line, darkcolor=line,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", "#1b2a44")],
            background=[("readonly", "#1b2a44"), ("active", "#24365a")],
            foreground=[("readonly", txt)],
            selectbackground=[("readonly", "#1b2a44")],
            selectforeground=[("readonly", txt)],
            arrowcolor=[("readonly", txt)],
        )
        style.configure(
            "Dark.Vertical.TScrollbar", background="#24365a", troughcolor=dark,
            bordercolor=dark, arrowcolor=sub,
        )
        style.configure(
            "Dark.Horizontal.TScrollbar", background="#24365a", troughcolor=dark,
            bordercolor=dark, arrowcolor=sub,
        )
        style.configure("Dark.TPanedwindow", background=dark)
        style.configure("AssistBody.TFrame", background="#231e3d")

        # 白天（浅色）
        style.configure("Light.TFrame", background="#ffffff")
        style.configure("Light.TLabel", background="#ffffff", foreground="#1f2937")
        style.configure(
            "Light.TLabelframe", background="#ffffff", bordercolor="#dce3ee",
            relief="solid", borderwidth=1,
        )
        style.configure("Light.TLabelframe.Label", background="#ffffff", foreground="#1c4f9c")
        style.configure(
            "Light.TButton", background="#eef2f8", foreground="#1f2937",
            bordercolor="#c9d3e4", focusthickness=0, relief="flat", padding=(6, 3),
        )
        style.map("Light.TButton", background=[("active", "#e2e8f4")])
        style.configure(
            "Light.Accent.TButton", background="#2f7fe8", foreground="#ffffff",
            bordercolor="#2f7fe8",
        )
        style.map("Light.Accent.TButton", background=[("active", "#2568c7")])
        style.configure("Light.TCheckbutton", background="#ffffff", foreground="#1f2937")
        style.map("Light.TCheckbutton", background=[("active", "#ffffff")])
        style.configure(
            "Light.TEntry", fieldbackground="#f7f9fc", background="#f7f9fc",
            foreground="#1f2937", insertcolor="#1f2937", bordercolor="#c9d3e4",
            lightcolor="#c9d3e4", darkcolor="#c9d3e4",
        )
        style.configure(
            "Light.TCombobox", fieldbackground="#f7f9fc", background="#eef2f8",
            foreground="#1f2937", arrowcolor="#1f2937", bordercolor="#c9d3e4",
            lightcolor="#c9d3e4", darkcolor="#c9d3e4",
        )
        style.map(
            "Light.TCombobox",
            fieldbackground=[("readonly", "#f7f9fc")],
            foreground=[("readonly", "#1f2937")],
        )
        style.configure(
            "Light.Vertical.TScrollbar", background="#dfe6f2", troughcolor="#ffffff",
            bordercolor="#ffffff", arrowcolor="#5b6478",
        )
        style.configure(
            "Light.Horizontal.TScrollbar", background="#dfe6f2", troughcolor="#ffffff",
            bordercolor="#ffffff", arrowcolor="#5b6478",
        )
        style.configure("Light.TPanedwindow", background="#ffffff")

    _FG_TO_LIGHT = {
        "#9fb0c9": "#6b7280", "#4ade80": "#15803d", "#f87171": "#b91c1c",
        "#fbbf24": "#b45309", "#7cc4ff": "#1d4ed8", "#c7d4e8": "#374151",
        "#e6ebf5": "#1f2937", "#ffffff": "#1f2937", "#dbe4f5": "#374151",
    }

    def _retint(self, widget, light: bool) -> None:
        """把区块里写死的前景色换成当前主题的深浅色。"""
        for child in widget.winfo_children():
            cls = child.winfo_class()
            try:
                if cls in ("TLabel", "Label"):
                    key = "foreground" if cls == "TLabel" else "fg"
                    cur = str(child.cget(key) or "").lower()
                    if cur:
                        orig = getattr(child, "_orig_fg", None) or cur
                        child._orig_fg = orig
                        if light:
                            new = self._FG_TO_LIGHT.get(orig, orig)
                        else:
                            new = orig
                        child.configure(**{key: new})
                elif cls == "Listbox":
                    if light:
                        child.configure(
                            bg="#f7f9fc", fg="#1f2937",
                            selectbackground="#2f7fe8", selectforeground="#ffffff",
                        )
                    else:
                        child.configure(
                            bg="#1b2a44", fg="#e6ebf5",
                            selectbackground="#2f7fe8", selectforeground="#ffffff",
                        )
            except tk.TclError:
                pass
            self._retint(child, light)

    def _apply_page_theme(self, widget, light: bool) -> None:
        prefix = "Light." if light else "Dark."
        for child in widget.winfo_children():
            try:
                cls = child.winfo_class()
                if cls == "TScrollbar":
                    orient = str(child.cget("orient"))
                    child.configure(
                        style=prefix + ("Vertical.TScrollbar" if orient == "vertical" else "Horizontal.TScrollbar")
                    )
                elif cls == "TButton":
                    current = str(child.cget("style") or "")
                    child.configure(
                        style=prefix + ("Accent.TButton" if "Accent" in current else "TButton")
                    )
                elif cls in ("TFrame", "TLabel", "TLabelframe", "TCheckbutton",
                             "TEntry", "TCombobox", "TPanedwindow"):
                    child.configure(style=prefix + cls)
            except tk.TclError:
                pass
            self._apply_page_theme(child, light)

    def _apply_dark(self, widget) -> None:
        """把某个页面里所有 ttk 控件换成深色样式。"""
        for child in widget.winfo_children():
            try:
                cls = child.winfo_class()
                if cls == "TScrollbar":
                    orient = str(child.cget("orient"))
                    child.configure(
                        style="Dark.Vertical.TScrollbar"
                        if orient == "vertical"
                        else "Dark.Horizontal.TScrollbar"
                    )
                elif cls == "TButton":
                    current = str(child.cget("style") or "")
                    child.configure(
                        style="Dark.Accent.TButton" if "Accent" in current else "Dark.TButton"
                    )
                elif cls in ("TFrame", "TLabel", "TLabelframe", "TCheckbutton",
                             "TEntry", "TCombobox", "TPanedwindow"):
                    child.configure(style="Dark." + cls)
                elif cls == "Listbox":
                    child.configure(
                        bg="#1b2a44", fg=self.DARK_TXT,
                        selectbackground="#2f7fe8", selectforeground="#ffffff",
                    )
            except tk.TclError:
                pass
            self._apply_dark(child)

    def _build(self) -> None:
        self._setup_style()
        c = self.colors
        # 顶部标题
        head = tk.Frame(self.root, bg=c["page"])
        # head 不再显示（首页自己画标题）
        tk.Label(
            head,
            text="小太阳脚本",
            bg=c["page"],
            fg=c["text"],
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(side=tk.LEFT)

        # 模式切换（胶囊样式）
        mode_bar = tk.Frame(self.root, bg=c["page"])
        # mode_bar 不再显示（导航在页面内）
        self.mode_var = tk.StringVar(value="home")
        self._mode_btn_home = tk.Button(
            mode_bar,
            text="首页",
            relief=tk.FLAT,
            bd=0,
            padx=9,
            pady=5,
            font=("Microsoft YaHei UI", 9, "bold"),
            command=lambda: self._switch_mode("home"),
        )
        self._mode_btn_home.pack(side=tk.LEFT)

        self._mode_btn_gacha = tk.Button(
            mode_bar,
            text="抽卡",
            relief=tk.FLAT,
            bd=0,
            padx=9,
            pady=5,
            font=("Microsoft YaHei UI", 9, "bold"),
            command=lambda: self._switch_mode("gacha"),
        )
        self._mode_btn_gacha.pack(side=tk.LEFT)
        self._mode_btn_assist = tk.Button(
            mode_bar,
            text="功能",
            relief=tk.FLAT,
            bd=0,
            padx=9,
            pady=5,
            font=("Microsoft YaHei UI", 9, "bold"),
            command=lambda: self._switch_mode("assist"),
        )
        self._mode_btn_assist.pack(side=tk.LEFT, padx=(6, 0))
        self._mode_btn_commands = tk.Button(
            mode_bar,
            text="运行命令",
            relief=tk.FLAT,
            bd=0,
            padx=9,
            pady=5,
            font=("Microsoft YaHei UI", 9, "bold"),
            command=lambda: self._switch_mode("commands"),
        )
        self._mode_btn_commands.pack(side=tk.LEFT, padx=(6, 0))
        # 分隔线也不显示

        # 窗口参数：变量始终创建（保存/读取要用），控件只在开发模式显示
        self.window_var = tk.StringVar(
            value=self.config.get("target_window_title") or GAME_WINDOW_TITLE
        )
        self.width_var = tk.StringVar(value=str(self.config.get("game_width", 390)))
        self.height_var = tk.StringVar(value=str(self.config.get("game_height", 844)))
        self.preset_var = tk.StringVar(value="常见 390x844")
        self.threshold_var = tk.StringVar(value=str(self.config.get("match_threshold", 0.85)))
        self.interval_var = tk.StringVar(value=str(self.config.get("loop_interval_ms", 800)))

        # 开发用的窗口标题/尺寸行不再显示（首页已有「打开游戏窗口」）
        if False:
            pass

        self._content = ttk.Frame(self.root)
        self._content.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self._page_gacha = ttk.Frame(self._content, padding=4)
        self._page_assist = ttk.Frame(self._content, padding=4)
        self._page_commands = ttk.Frame(self._content, padding=4)
        self._page_play = ttk.Frame(self._content, padding=4)
        self._page_home = tk.Frame(self._content, bg="#f3f5f9")
        self._page_license = tk.Frame(self._content, bg="#f5f6fa")
        self._scroll_canvases: Dict[str, tk.Canvas] = {}
        # 存档脚本功能暂时隐藏（后续重写），页面仍构建但默认不显示
        self._build_gacha_tab(self._make_scroll(self._page_gacha, "gacha"))
        self._page_assist.configure(style="Dark.TFrame")
        self._build_assist_tab(
            self._make_scroll(self._page_assist, "assist", bg=self.DARK_PAGE)
        )
        # 构建完后按当前主题（默认白天）套用配色
        self.root.after(60, self._apply_assist_theme)
        self._build_commands_tab(self._page_commands)
        self._build_play_tab(self._page_play)
        self._build_home_tab(self._page_home)
        self._build_license_tab(self._page_license)
        self.root.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self._switch_mode("home")

        # 日志框已去掉（用户要求）
        self.log = None

        self.root.after(200, self._tick_cursor)

    def _apply_window_icon(self) -> None:
        """窗口/任务栏图标：Windows 用 ico，其它平台退回 png。"""
        try:
            ico = os.path.join(self.ASSET_DIR, "sunny.ico")
            png = os.path.join(self.ASSET_DIR, "sunny.png")
            if os.name == "nt" and os.path.exists(ico):
                self.root.iconbitmap(default=ico)
                return
            if os.path.exists(png):
                self._window_icon_photo = tk.PhotoImage(file=png)
                self.root.iconphoto(True, self._window_icon_photo)
        except Exception:  # noqa: BLE001
            pass

    def _switch_mode(self, mode: str) -> None:
        self.mode_var.set(mode)
        pages = {
            "home": self._page_home,
            "gacha": self._page_gacha,
            "assist": self._page_assist,
            "commands": self._page_commands,
            "play": self._page_play,
            "license": self._page_license,
        }
        buttons = {
            "home": self._mode_btn_home,
            "gacha": self._mode_btn_gacha,
            "assist": self._mode_btn_assist,
            "commands": self._mode_btn_commands,
        }
        for name, page in pages.items():
            if name == mode:
                page.pack(fill=tk.BOTH, expand=True)
            else:
                page.pack_forget()
        c = getattr(self, "colors", {}) or {}
        accent = c.get("accent", "#2563eb")
        soft = c.get("soft", "#f3f4f6")
        text = c.get("text", "#111827")
        for name, button in buttons.items():
            if name == mode:
                button.configure(bg=accent, fg="#ffffff", activebackground=accent, activeforeground="#ffffff")
            else:
                button.configure(bg=soft, fg=text, activebackground="#e5e7eb", activeforeground=text)
        if mode == "gacha":
            pass
        elif mode == "assist":
            self._update_assist_labels()
        if mode == "home":
            try:
                self.root.after_idle(self._draw_home)
            except Exception:  # noqa: BLE001
                pass

    def _make_scroll(
        self, parent: ttk.Frame, key: str, bg: str = "#ffffff", frame_style: str = ""
    ) -> ttk.Frame:
        """给页面套一个可滚动容器，返回里面真正放内容的 frame。"""
        canvas = tk.Canvas(parent, highlightthickness=0, bd=0, bg=bg)
        sb = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        hb = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=canvas.xview)
        inner = ttk.Frame(canvas, style=frame_style) if frame_style else ttk.Frame(canvas)

        shown = {"v": True, "h": False}

        def _sync(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            width = max(canvas.winfo_width(), inner.winfo_reqwidth())
            canvas.itemconfigure(win, width=width)
            # 内容比窗口矮时铺满，避免底部留一大片空白
            height = max(inner.winfo_reqheight(), canvas.winfo_height())
            canvas.itemconfigure(win, height=height)
            try:
                # 滚动条只在放不下时才出现，能放下就收起来
                need_v = inner.winfo_reqheight() > canvas.winfo_height() + 1
                if need_v != shown["v"]:
                    shown["v"] = need_v
                    if need_v:
                        sb.pack(side=tk.RIGHT, fill=tk.Y)
                    else:
                        sb.pack_forget()
                need_h = inner.winfo_reqwidth() > canvas.winfo_width() + 1
                if need_h != shown["h"]:
                    shown["h"] = need_h
                    if need_h:
                        hb.pack(side=tk.BOTTOM, fill=tk.X)
                    else:
                        hb.pack_forget()
            except Exception:  # noqa: BLE001
                pass

        inner.bind("<Configure>", _sync)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", _sync)
        canvas.configure(yscrollcommand=sb.set, xscrollcommand=hb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._scroll_canvases[key] = canvas
        return inner

    def _on_mousewheel(self, event) -> None:
        """滚轮滚动当前页面（抽卡页的英雄列表自己滚动，不拦）。"""
        w = getattr(event, "widget", None)
        if w is getattr(self, "_gacha_hero_canvas", None):
            return
        canvases = getattr(self, "_scroll_canvases", {}) or {}
        if not canvases:
            return
        canvas = canvases.get(self.mode_var.get()) if hasattr(self, "mode_var") else None
        if canvas is None:
            canvas = next(iter(canvases.values()))
        try:
            canvas.yview_scroll(-1 if getattr(event, "delta", 0) > 0 else 1, "units")
        except Exception:  # noqa: BLE001
            pass

    def _card(self, parent, title: str, pady=(0, 8)):
        """统一的卡片容器。"""
        card = ttk.LabelFrame(parent, text=title, padding=8)
        card.pack(fill=tk.X, pady=pady)
        card.columnconfigure(4, weight=1)
        return card

    def _build_gacha_tab(self, parent: ttk.Frame) -> None:
        nav = ttk.Frame(parent)
        nav.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(nav, text="‹ 返回首页", command=lambda: self._switch_mode("home")).pack(side=tk.LEFT)
        ttk.Button(nav, text="辅助工具", command=lambda: self._switch_mode("assist")).pack(side=tk.RIGHT)

        gacha = normalize_gacha(self.config.get("gacha"))
        enabled = set(gacha.get("enabled_cards") or [])

        # ---------- 变量 ----------
        self.gacha_cdp_targets_var = tk.StringVar(value=str(gacha.get("cdp_targets", "")))
        self.gacha_cdp_red_var = tk.BooleanVar(value=False)
        self.gacha_cdp_max_var = tk.StringVar(value=str(gacha.get("cdp_max_draws", 0)))
        self.gacha_cdp_delay_var = tk.StringVar(value=str(gacha.get("cdp_delay_ms", 1200)))
        # 不刷新候选池、红不算目标（界面上不再提供开关）
        self.gacha_cdp_refresh_var = tk.BooleanVar(value=False)
        self.gacha_cdp_green_var = tk.StringVar(value=str(gacha.get("cdp_max_green", 0)))
        # 固定「有目标就用最大倍率抽」「抽中也放弃，一直抽」
        self.gacha_cdp_maxbet_var = tk.BooleanVar(value=True)
        self.gacha_cdp_abandon_var = tk.BooleanVar(value=True)
        self.gacha_turbo_var = tk.BooleanVar(value=bool(gacha.get("cdp_turbo")))
        self.gacha_pool_var = tk.StringVar(value="当前候选：未读取（打开招募面板后开始抽卡会自动读取）")
        self.gacha_records_var = tk.StringVar(value="抽中：尚未出货")
        self.gacha_cdp_status = tk.StringVar(value="打开酒馆/招募界面，再点「开始招募」")

        page_bg = "#ffffff"
        card_bg = "#f6f8fa"
        line = "#dde3ea"
        sub = "#6b7280"

        # ---------- 标题 ----------
        head = tk.Frame(parent, bg=page_bg)
        head.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            head, text="英雄招募", bg=page_bg, fg="#111827",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            head, text="开始招募时自动打开招募界面", bg=page_bg, fg=sub,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT, padx=(8, 0))

        # ---------- 设置卡 ----------
        cfg_card = tk.Frame(parent, bg=card_bg, highlightbackground=line, highlightthickness=1)
        cfg_card.pack(fill=tk.X, pady=(0, 8))

        row_a = tk.Frame(cfg_card, bg=card_bg)
        row_a.pack(fill=tk.X, padx=10, pady=(10, 6))
        tk.Label(row_a, text="抽奖次数", bg=card_bg, fg="#374151").pack(side=tk.LEFT)
        tk.Entry(
            row_a, textvariable=self.gacha_cdp_max_var, width=5, justify="center",
            relief=tk.FLAT, bg="#ffffff", fg="#111827", highlightbackground=line,
            highlightthickness=1, insertbackground="#111827",
        ).pack(side=tk.LEFT, padx=(6, 4), ipady=3)
        tk.Label(row_a, text="0 为无限", bg=card_bg, fg=sub).pack(side=tk.LEFT)
        tk.Label(row_a, text="绿钻消耗", bg=card_bg, fg="#374151").pack(side=tk.LEFT, padx=(16, 0))
        tk.Entry(
            row_a, textvariable=self.gacha_cdp_green_var, width=5, justify="center",
            relief=tk.FLAT, bg="#ffffff", fg="#111827", highlightbackground=line,
            highlightthickness=1, insertbackground="#111827",
        ).pack(side=tk.LEFT, padx=(6, 4), ipady=3)
        tk.Label(row_a, text="0 为不花", bg=card_bg, fg=sub).pack(side=tk.LEFT)
        row_a.pack_configure(pady=(10, 10))

        row_b = tk.Frame(cfg_card, bg=card_bg)
        row_b.pack(fill=tk.X, padx=10, pady=(0, 10))
        tk.Label(row_b, text="每次间隔", bg=card_bg, fg="#374151").pack(side=tk.LEFT)
        tk.Entry(
            row_b, textvariable=self.gacha_cdp_delay_var, width=6, justify="center",
            relief=tk.FLAT, bg="#ffffff", fg="#111827", highlightbackground=line,
            highlightthickness=1, insertbackground="#111827",
        ).pack(side=tk.LEFT, padx=(6, 4), ipady=3)
        tk.Label(
            row_b, text="毫秒（普通最低 300，极速最低 20）", bg=card_bg, fg=sub,
        ).pack(side=tk.LEFT)


        # ---------- 统计卡 ----------
        stat_card = tk.Frame(parent, bg=card_bg, highlightbackground=line, highlightthickness=1)
        stat_card.pack(fill=tk.X, pady=(0, 8))

        stat_left = tk.Frame(stat_card, bg=card_bg)
        stat_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=8)
        self.gacha_stat_draws_var = tk.StringVar(value="0")
        self.gacha_stat_red_var = tk.StringVar(value="0")
        self.gacha_stat_cost_var = tk.StringVar(value="0")
        self.gacha_stat_green_var = tk.StringVar(value="0")
        self.gacha_stat_time_var = tk.StringVar(value="0:00")
        stat_rows = [
            ("总次数", self.gacha_stat_draws_var, "红出现", self.gacha_stat_red_var),
            ("总消耗", self.gacha_stat_cost_var, "绿钻消耗", self.gacha_stat_green_var),
        ]
        for r, (l1, v1, l2, v2) in enumerate(stat_rows):
            tk.Label(stat_left, text=l1, bg=card_bg, fg=sub).grid(row=r, column=0, sticky="w", pady=1)
            tk.Label(
                stat_left, textvariable=v1, bg=card_bg, fg="#111827",
                font=("Microsoft YaHei UI", 10, "bold"),
            ).grid(row=r, column=1, sticky="w", padx=(4, 18))
            tk.Label(stat_left, text=l2, bg=card_bg, fg=sub).grid(row=r, column=2, sticky="w")
            tk.Label(
                stat_left, textvariable=v2, bg=card_bg, fg="#111827",
                font=("Microsoft YaHei UI", 10, "bold"),
            ).grid(row=r, column=3, sticky="w", padx=(4, 0))
        tk.Label(stat_left, text="总时间", bg=card_bg, fg=sub).grid(row=2, column=0, sticky="w", pady=1)
        tk.Label(
            stat_left, textvariable=self.gacha_stat_time_var, bg=card_bg, fg="#111827",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).grid(row=2, column=1, sticky="w", padx=(4, 0))

        btns = tk.Frame(stat_card, bg=card_bg)
        btns.pack(side=tk.RIGHT, padx=10, pady=8)
        tk.Button(
            btns, text="清空", command=self.clear_gacha_stats, relief=tk.FLAT, bd=0,
            bg="#e9edf2", fg="#374151", activebackground="#dfe4ea",
            font=("Microsoft YaHei UI", 10), padx=14, pady=9, cursor="hand2",
        ).pack(side=tk.LEFT)
        self.gacha_turbo_btn = tk.Button(
            btns, text="极速模式", command=self.toggle_gacha_turbo, relief=tk.FLAT, bd=0,
            bg="#fff3e0", fg="#b45309", activebackground="#ffe8c7",
            font=("Microsoft YaHei UI", 10, "bold"), padx=12, pady=9, cursor="hand2",
        )
        self.gacha_turbo_btn.pack(side=tk.LEFT, padx=(8, 0))
        self._sync_gacha_turbo_btn()
        self.gacha_cdp_btn = tk.Button(
            btns, text="开始招募", command=self.toggle_gacha_cdp, relief=tk.FLAT, bd=0,
            bg="#2f9e5f", fg="#ffffff", activebackground="#26864f", activeforeground="#ffffff",
            font=("Microsoft YaHei UI", 10, "bold"), padx=16, pady=9, cursor="hand2",
        )
        self.gacha_cdp_btn.pack(side=tk.LEFT, padx=(8, 0))

        # 状态 + 名单按钮同一行，省高度
        line2 = tk.Frame(parent, bg=page_bg)
        line2.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            line2, textvariable=self.gacha_cdp_status, bg=page_bg, fg="#555",
            anchor="w", justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        for text, cmd in (
            ("重载", self.reload_gacha_heroes),
            ("全不选", lambda: self._set_all_heroes(False)),
            ("全选", lambda: self._set_all_heroes(True)),
        ):
            tk.Button(
                line2, text=text, command=cmd, relief=tk.FLAT, bd=0,
                bg="#eef1f6", fg="#374151", activebackground="#e2e8f0",
                font=("Microsoft YaHei UI", 8), padx=8, pady=2, cursor="hand2",
            ).pack(side=tk.RIGHT, padx=(4, 0))

        # ---------- 英雄列表 ----------
        head2 = tk.Frame(parent, bg=page_bg)
        head2.pack(fill=tk.X)
        self.gacha_hero_count = tk.StringVar(value="最大倍数招募（已选 0）")
        tk.Label(
            head2, textvariable=self.gacha_hero_count, bg=page_bg, fg="#111827",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            head2, text="出现 / 中 / 碎片（红=品级6+）", bg=page_bg, fg=sub
        ).pack(side=tk.RIGHT)

        holder = tk.Frame(parent, bg=page_bg)
        holder.pack(fill=tk.BOTH, expand=True)
        self._gacha_hero_canvas = tk.Canvas(
            holder, height=88, width=300, highlightthickness=0, bd=0, bg=page_bg
        )
        hero_sb = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=self._gacha_hero_canvas.yview)
        self._gacha_hero_inner = tk.Frame(self._gacha_hero_canvas, bg=page_bg)
        self._gacha_hero_inner.columnconfigure(0, weight=1)
        self._gacha_hero_inner.columnconfigure(1, weight=1)
        win_id = self._gacha_hero_canvas.create_window(
            (0, 0), window=self._gacha_hero_inner, anchor="nw"
        )
        self._gacha_hero_canvas.configure(yscrollcommand=hero_sb.set)

        hero_shown = {"v": True}

        def _sync_hero(_e=None):
            # 两列铺满宽度；名单能一屏放完就不要滚动条
            try:
                self._gacha_hero_canvas.configure(
                    scrollregion=self._gacha_hero_canvas.bbox("all")
                )
                self._gacha_hero_canvas.itemconfigure(
                    win_id, width=self._gacha_hero_canvas.winfo_width()
                )
                need = (
                    self._gacha_hero_inner.winfo_reqheight()
                    > self._gacha_hero_canvas.winfo_height() + 1
                )
                if need != hero_shown["v"]:
                    hero_shown["v"] = need
                    if need:
                        hero_sb.pack(side=tk.RIGHT, fill=tk.Y)
                    else:
                        hero_sb.pack_forget()
            except tk.TclError:
                pass

        self._gacha_hero_inner.bind("<Configure>", _sync_hero, add="+")
        self._gacha_hero_canvas.bind("<Configure>", _sync_hero)
        self._gacha_hero_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hero_sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._gacha_hero_vars: Dict[int, tk.BooleanVar] = {}
        self._gacha_hero_counter_vars: Dict[int, tk.StringVar] = {}
        self._hero_name_cache: Dict[int, str] = {}
        self._gacha_running = False
        self._gacha_start_ts = None

        # 旧版「截图识别抽卡」参数只保留变量
        self.card_template_labels: Dict[str, tk.Label] = {}
        self.card_vars: Dict[str, tk.BooleanVar] = {
            name: tk.BooleanVar(value=name in enabled) for name in CARD_CATALOG
        }
        self.ui_status_var = tk.StringVar(value="")
        self.gacha_max_var = tk.StringVar(value=str(gacha.get("max_draws", 0)))
        self.gacha_settle_var = tk.StringVar(value=str(gacha.get("settle_ms", 2800)))
        self.gacha_threshold_var = tk.StringVar(value=str(gacha.get("match_threshold", 0.82)))
        self.gacha_nohit_var = tk.StringVar(value="正常抽卡")
        self.gacha_stop_on_hit_var = tk.BooleanVar(value=bool(gacha.get("stop_on_hit", False)))
        self.gacha_use_gem_var = tk.BooleanVar(value=bool(gacha.get("use_green_gem", False)))
        self.gacha_use_max_var = tk.BooleanVar(value=bool(gacha.get("use_max_button", True)))
        self.gacha_slot_var = tk.StringVar(value=f"存档{int(gacha.get('slot', 1))}")

        self.reload_gacha_heroes()

    # ---------------- 游戏辅助 ----------------
    # ------------------------------------------------------------ 首页（深色）

    BG_TOP = "#0b1220"
    BG_BOTTOM = "#182540"
    BANNER_TOP = "#0a2f66"
    BANNER_BOTTOM = "#2b8ae6"
    CARD_BG = "#16233c"
    CARD_LINE = "#2a3b5c"
    TXT = "#e6ebf5"
    SUB = "#9fb0c9"

    @staticmethod
    def _round_points(x0: float, y0: float, x1: float, y1: float, r: float):
        """圆角矩形的顶点（用折线近似）。"""
        import math

        r = max(1.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
        pts = []
        for cx, cy, start_deg in (
            (x1 - r, y0 + r, -90),
            (x1 - r, y1 - r, 0),
            (x0 + r, y1 - r, 90),
            (x0 + r, y0 + r, 180),
        ):
            for i in range(7):
                a = math.radians(start_deg + 90.0 * i / 6)
                pts.extend((cx + r * math.cos(a), cy + r * math.sin(a)))
        return pts

    @staticmethod
    def _vgrad(canvas: tk.Canvas, x0, y0, x1, y1, top: str, bottom: str) -> None:
        steps = max(2, int(y1 - y0))
        c1 = canvas.winfo_rgb(top)
        c2 = canvas.winfo_rgb(bottom)
        for i in range(steps):
            t = i / float(steps - 1)
            r = int(c1[0] + (c2[0] - c1[0]) * t) >> 8
            g = int(c1[1] + (c2[1] - c1[1]) * t) >> 8
            b = int(c1[2] + (c2[2] - c1[2]) * t) >> 8
            canvas.create_line(x0, y0 + i, x1, y0 + i, fill="#%02x%02x%02x" % (r, g, b))

    ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

    # 首页按参考图（1152x2048）的比例排版
    HOME_W, HOME_H = 1152.0, 2048.0

    def _home_bg_custom_path(self):
        try:
            ui = self.config.get("ui") or {}
            path = str(ui.get("bg_image") or "").strip()
        except Exception:  # noqa: BLE001
            return ""
        return path if path and os.path.exists(path) else ""

    def _home_bg(self, w: int, h: int):
        """整页背景：自定义图片优先，否则内置原画；按尺寸缓存。"""
        custom = self._home_bg_custom_path()
        key = (int(w), int(h), custom)
        if getattr(self, "_home_bg_key", None) == key:
            return getattr(self, "_home_bg_photo", None)
        photo = None
        try:
            from PIL import Image, ImageEnhance, ImageTk

            if custom:
                im = Image.open(custom).convert("RGB")
                tw, th = max(1, int(w)), max(1, int(h))
                src_w, src_h = im.size
                scale = max(tw / src_w, th / src_h)
                nw, nh = max(1, int(src_w * scale + 0.5)), max(1, int(src_h * scale + 0.5))
                im = im.resize((nw, nh), Image.LANCZOS)
                left = max(0, (nw - tw) // 2)
                top = max(0, (nh - th) // 2)
                im = im.crop((left, top, left + tw, top + th))
                im = ImageEnhance.Brightness(im).enhance(0.62)
                self._home_bg_pil = im
                photo = ImageTk.PhotoImage(im)
            else:
                for name in ("home_bg.jpg", "home_bg.png"):
                    path = os.path.join(self.ASSET_DIR, name)
                    if not os.path.exists(path):
                        continue
                    im = Image.open(path).convert("RGB")
                    im = im.resize((max(1, int(w)), max(1, int(h))), Image.LANCZOS)
                    self._home_bg_pil = im
                    photo = ImageTk.PhotoImage(im)
                    break
        except Exception:  # noqa: BLE001
            photo = None
        self._home_bg_key = key
        self._home_bg_photo = photo
        return photo

    def _pick_home_bg(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择主页背景图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            from PIL import Image

            im = Image.open(path)
            im.load()
            if im.width < 200 or im.height < 200:
                raise ValueError("图片太小（至少 200×200）")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("更换背景", "图片打不开：%s" % exc)
            return
        try:
            folder = os.path.join(project_root(), "saves", "backgrounds")
            os.makedirs(folder, exist_ok=True)
            ext = os.path.splitext(path)[1].lower() or ".png"
            target = os.path.join(folder, "home_bg" + ext)
            import shutil

            shutil.copyfile(path, target)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("更换背景", "保存失败：%s" % exc)
            return
        try:
            ui = self.config.get("ui")
            if not isinstance(ui, dict):
                ui = {}
            ui["bg_image"] = target
            self.config["ui"] = ui
            save_config(self.config)
        except Exception:  # noqa: BLE001
            pass
        self._home_bg_key = None
        self._draw_home()
        self.append_log("主页背景已更换：%s" % target)

    def _home_bg_options(self):
        folder = os.path.join(project_root(), "saves", "backgrounds")
        items = [""]
        try:
            for name in sorted(os.listdir(folder)):
                if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
                    items.append(os.path.join(folder, name))
        except OSError:
            pass
        return items

    def _cycle_home_bg(self) -> None:
        options = self._home_bg_options()
        if len(options) <= 1:
            self._pick_home_bg()
            return
        current = self._home_bg_custom_path()
        try:
            index = options.index(current)
        except ValueError:
            index = -1
        target = options[(index + 1) % len(options)]
        try:
            ui = self.config.get("ui")
            if not isinstance(ui, dict):
                ui = {}
            if target:
                ui["bg_image"] = target
            else:
                ui.pop("bg_image", None)
            self.config["ui"] = ui
            save_config(self.config)
        except Exception:  # noqa: BLE001
            pass
        self._home_bg_key = None
        self._draw_home()
        try:
            self.append_log("已切换主页背景：%s" % (os.path.basename(target) if target else "默认"))
        except Exception:  # noqa: BLE001
            pass

    def _reset_home_bg(self) -> None:
        try:
            ui = self.config.get("ui")
            if isinstance(ui, dict):
                ui.pop("bg_image", None)
                self.config["ui"] = ui
                save_config(self.config)
        except Exception:  # noqa: BLE001
            pass
        self._home_bg_key = None
        self._draw_home()
        self.append_log("主页背景已恢复默认")

    def _frosted(self, w: int, h: int, box, tint, alpha: float, radius: float, key: str):
        """从背景裁一块做磨砂玻璃卡片（圆角），按尺寸缓存。"""
        cache = getattr(self, "_frosted_cache", {})
        ck = (key, int(w), int(h), tuple(int(v) for v in box))
        if ck in cache:
            return cache[ck]
        photo = None
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageTk

            bg = getattr(self, "_home_bg_pil", None)
            if bg is None:
                return None
            x0, y0, x1, y1 = [int(round(v)) for v in box]
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(bg.width, x1), min(bg.height, y1)
            if x1 - x0 < 4 or y1 - y0 < 4:
                return None
            card = bg.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(8))
            veil = Image.new("RGB", card.size, tint)
            card = Image.blend(card, veil, alpha).convert("RGBA")
            mask = Image.new("L", card.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, card.width - 1, card.height - 1], radius=int(radius), fill=255
            )
            card.putalpha(mask)
            photo = ImageTk.PhotoImage(card)
        except Exception:  # noqa: BLE001
            photo = None
        cache[ck] = photo
        self._frosted_cache = cache
        return photo

    @staticmethod
    def _offset_polygon(pts, d):
        """把凸多边形向内收缩 d（用于双描边的内框）。"""
        import math

        n = len(pts)
        cx = sum(p[0] for p in pts) / n
        cy = sum(p[1] for p in pts) / n
        lines = []
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy) or 1
            nx, ny = -dy / length, dx / length
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            if (mx + nx - cx) ** 2 + (my + ny - cy) ** 2 > (mx - cx) ** 2 + (my - cy) ** 2:
                nx, ny = -nx, -ny
            lines.append(((x1 + nx * d, y1 + ny * d), (x2 + nx * d, y2 + ny * d)))
        out = []
        for i in range(n):
            (ax, ay), (bx, by) = lines[i - 1]
            (cx1, cy1), (dx1, dy1) = lines[i]
            den = (ax - bx) * (cy1 - dy1) - (ay - by) * (cx1 - dx1)
            if abs(den) < 1e-6:
                out.append((bx, by))
                continue
            t = ((ax - cx1) * (cy1 - dy1) - (ay - cy1) * (cx1 - dx1)) / den
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
        return out

    def _hex_photo(self, hexw: int, hexh: int):
        """辅助工具按钮：直接使用设计好的图标（app/assets/hex_button.png）。"""
        cache = getattr(self, "_hex_cache", {})
        key = (int(hexw), int(hexh))
        if key in cache:
            return cache[key]
        result = None
        try:
            from PIL import Image, ImageTk

            base = getattr(self, "_hex_button_src", None)
            if base is None:
                base = Image.open(os.path.join(self.ASSET_DIR, "hex_button.png")).convert("RGBA")
                self._hex_button_src = base
            img = base.resize((max(1, int(hexw)), max(1, int(hexh))), Image.LANCZOS)
            result = (ImageTk.PhotoImage(img), 0, 0)
        except Exception:  # noqa: BLE001
            result = None
        cache[key] = result
        self._hex_cache = cache
        return result
    def _build_home_tab(self, parent: tk.Frame) -> None:
        """首页：原画背景 + 卡片 + 发光按钮。"""
        parent.configure(bg=self.BG_TOP)
        canvas = tk.Canvas(parent, highlightthickness=0, bd=0, bg=self.BG_TOP)
        canvas.pack(fill=tk.BOTH, expand=True)
        self._home_canvas = canvas
        self.home_status_var = tk.StringVar(value="未打开（点下面「打开游戏窗口」）")
        self.home_status_var.trace_add("write", lambda *_a: self._sync_home_status())
        canvas.bind("<Configure>", lambda _e: self._draw_home())
        parent.after(30, self._draw_home)

    def _sync_home_status(self) -> None:
        item = getattr(self, "_home_status_item", None)
        canvas = getattr(self, "_home_canvas", None)
        if not item or canvas is None:
            return
        try:
            canvas.itemconfigure(item, text=self.home_status_var.get())
        except tk.TclError:
            pass

    def _draw_home(self) -> None:
        c = getattr(self, "_home_canvas", None)
        if c is None:
            return
        try:
            w = c.winfo_width()
            h = c.winfo_height()
        except tk.TclError:
            return
        if w <= 2 or h <= 2:
            return

        c.delete("all")
        ui_font = "Microsoft YaHei UI"

        def sx(v: float) -> float:
            return v * w / self.HOME_W

        def sy(v: float) -> float:
            return v * h / self.HOME_H

        # ---------- 背景原画 ----------
        photo = self._home_bg(w, h)
        if photo is not None:
            c.create_image(0, 0, image=photo, anchor="nw")
        else:
            self._vgrad(c, 0, 0, w, h, "#241b47", "#182742")

        card_bg = "#f3f5fb"
        card_line = "#e2e8f4"
        dark_txt = "#1f2937"
        sub_txt = "#4b5768"

        # ---------- 标题 ----------
        c.create_text(
            sx(52), sy(243), anchor="w", text="小太阳脚本",
            fill="#ffffff", font=(ui_font, 19, "bold"),
        )
        c.create_text(
            sx(52), sy(332), anchor="w", text="小太阳脚本 · 电脑版",
            fill="#dbe3f5", font=(ui_font, 9),
        )

        # ---------- 本机游戏卡片 ----------
        x0, y0, x1, y1 = sx(50), sy(510), sx(1105), sy(1085)
        card_img = self._frosted(
            w, h, (x0, y0, x1, y1), "#ffffff", 0.85, sx(30), "card"
        )
        if card_img is not None:
            c.create_image(x0, y0, image=card_img, anchor="nw")
        else:
            c.create_polygon(
                self._round_points(x0, y0, x1, y1, sx(30)),
                fill=card_bg, outline=card_line,
            )
        c.create_polygon(
            self._round_points(x0, y0, x1, y1, sx(30)),
            fill="", outline=card_line,
        )
        c.create_text(
            sx(84), sy(577), anchor="w", text="本机游戏",
            fill=dark_txt, font=(ui_font, 13, "bold"),
        )
        self._home_status_item = c.create_text(
            sx(84), sy(648), anchor="nw", text=self.home_status_var.get(),
            fill="#26303f", width=x1 - sx(130), font=(ui_font, 12), justify="left",
        )

        # 两个按钮
        by0, by1 = sy(925), sy(1000)
        bx0, bx1 = sx(95), sx(570)
        cx0, cx1 = sx(610), sx(1050)
        c.create_polygon(
            self._round_points(bx0, by0, bx1, by1, (by1 - by0) / 2),
            fill="#2f7fe8", outline="#8fc7ff", width=1, tags=("home_open",),
        )
        c.create_text(
            (bx0 + bx1) / 2, (by0 + by1) / 2, text="打开游戏窗口", fill="#ffffff",
            font=(ui_font, 11, "bold"), tags=("home_open",),
        )
        c.create_polygon(
            self._round_points(cx0, by0, cx1, by1, (by1 - by0) / 2),
            fill="#eef2f9", outline="#c8d3e5", tags=("home_reconnect",),
        )
        c.create_text(
            (cx0 + cx1) / 2, (by0 + by1) / 2, text="重新连接", fill="#374151",
            font=(ui_font, 11), tags=("home_reconnect",),
        )
        for tag in ("home_open", "home_reconnect"):
            c.tag_bind(tag, "<Enter>", lambda _e: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda _e: c.configure(cursor=""))
        c.tag_bind("home_open", "<Button-1>", lambda _e: self.ask_open_game())
        c.tag_bind("home_reconnect", "<Button-1>", lambda _e: self.reconnect_assist())

        # ---------- 关于 ----------
        ax0, ay0, ax1, ay1 = sx(959), sy(108), sx(1117), sy(195)
        c.create_polygon(
            self._round_points(ax0, ay0, ax1, ay1, sx(14)),
            fill="#ffffff", outline="#e6ecf7", tags=("home_about",),
        )
        c.create_text(
            (ax0 + ax1) / 2, (ay0 + ay1) / 2, text="关于", fill="#1c4f9c",
            font=(ui_font, 10, "bold"), tags=("home_about",),
        )
        c.tag_bind("home_about", "<Button-1>", lambda _e: self._show_about())
        c.tag_bind("home_about", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("home_about", "<Leave>", lambda _e: c.configure(cursor=""))

        # ---------- 赞助 ----------
        px0, py0, px1, py1 = sx(775), sy(108), sx(931), sy(195)
        c.create_polygon(
            self._round_points(px0, py0, px1, py1, sx(14)),
            fill="#ffffff", outline="#e6ecf7", tags=("home_sponsor",),
        )
        c.create_text(
            (px0 + px1) / 2, (py0 + py1) / 2, text="赞助", fill="#0f9d58",
            font=(ui_font, 10, "bold"), tags=("home_sponsor",),
        )
        c.tag_bind("home_sponsor", "<Button-1>", lambda _e: self._show_sponsor())
        c.tag_bind("home_sponsor", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("home_sponsor", "<Leave>", lambda _e: c.configure(cursor=""))

        # ---------- 密钥 ----------
        kx0, ky0, kx1, ky1 = sx(591), sy(108), sx(747), sy(195)
        unlocked = False
        try:
            unlocked = self._license_ok()
        except Exception:  # noqa: BLE001
            unlocked = False
        c.create_polygon(
            self._round_points(kx0, ky0, kx1, ky1, sx(14)),
            fill="#ffffff", outline="#e6ecf7", tags=("home_license",),
        )
        c.create_text(
            (kx0 + kx1) / 2, (ky0 + ky1) / 2,
            text=("已解锁" if unlocked else "输入密钥"),
            fill=("#0f9d58" if unlocked else "#1c4f9c"),
            font=(ui_font, 10, "bold"), tags=("home_license",),
        )
        c.tag_bind("home_license", "<Button-1>", lambda _e: self._license_click())
        c.tag_bind("home_license", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("home_license", "<Leave>", lambda _e: c.configure(cursor=""))

        # ---------- 辅助工具（发光六边形） ----------
        hx0, hx1 = sx(150), sx(1000)
        hy0, hy1 = sy(1145), sy(1345)
        mid = (hy0 + hy1) / 2
        hexw, hexh = int(hx1 - hx0), int(hy1 - hy0)
        art = self._hex_photo(hexw, hexh)
        if art is not None:
            photo, dx, dy = art
            self._hex_photo_ref = photo
            c.create_image(hx0 - dx, hy0 - dy, image=photo, anchor="nw", tags=("home_assist",))
        else:
            cut = hexw * 0.3
            hexa = [
                hx0 + cut, hy0, hx1 - cut, hy0, hx1, mid,
                hx1 - cut, hy1, hx0 + cut, hy1, hx0, mid,
            ]
            c.create_polygon(
                hexa, fill="#0e3b45", outline="#a5f3ff", width=2, tags=("home_assist",)
            )
        c.create_text(
            (hx0 + hx1) / 2, mid + 1, text="辅助工具", fill="#0b2f38",
            font=(ui_font, 20, "bold"), tags=("home_assist",),
        )
        c.create_text(
            (hx0 + hx1) / 2, mid, text="辅助工具", fill="#ffffff",
            font=(ui_font, 20, "bold"), tags=("home_assist",),
        )
        c.tag_bind("home_assist", "<Button-1>", lambda _e: self._switch_mode("assist"))
        c.tag_bind("home_assist", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("home_assist", "<Leave>", lambda _e: c.configure(cursor=""))

        # ---------- 使用说明 ----------
        ix0, iy0, ix1, iy1 = sx(50), sy(1430), sx(1105), sy(1790)
        info_img = self._frosted(
            w, h, (ix0, iy0, ix1, iy1), "#223154", 0.86, sx(30), "info"
        )
        if info_img is not None:
            c.create_image(ix0, iy0, image=info_img, anchor="nw")
        else:
            c.create_polygon(
                self._round_points(ix0, iy0, ix1, iy1, sx(30)),
                fill="#223154", outline="#33466c",
            )
        c.create_polygon(
            self._round_points(ix0, iy0, ix1, iy1, sx(30)),
            fill="", outline="#33466c",
        )
        c.create_text(
            sx(84), sy(1478), anchor="w", text="使用说明",
            fill="#ffffff", font=(ui_font, 11, "bold"),
        )
        lines = [
            "1. 打开游戏窗口，用验证码登录",
            "2. 进入主界面后会自动连接",
            "3. 点「辅助工具」开启红包 / 复活 / 刷新 / 闪现",
        ]
        for i, line in enumerate(lines):
            c.create_text(
                sx(84), sy(1560 + i * 78), anchor="w", text=line,
                fill="#dbe4f5", font=(ui_font, 10),
            )

        # ---------- 底部说明 ----------
        c.create_text(
            w / 2, sy(1912), text="说明：使用脚本，封号自己负责",
            fill="#f97316", font=(ui_font, 10),
        )
        qq_item = c.create_text(
            w / 2, sy(1952), text="QQ群：1090324996（点击复制）",
            fill="#9fd0ff", font=(ui_font, 10), tags=("home_qq",),
        )

        def copy_qq(_event=None) -> None:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append("1090324996")
                c.itemconfigure(qq_item, text="QQ群：1090324996（已复制）")
                self.root.after(
                    1500,
                    lambda: c.itemconfigure(qq_item, text="QQ群：1090324996（点击复制）"),
                )
            except tk.TclError:
                pass

        c.tag_bind("home_qq", "<Button-1>", copy_qq)
        c.tag_bind("home_qq", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("home_qq", "<Leave>", lambda _e: c.configure(cursor=""))
        c.create_text(
            w / 2, sy(2006), text="脚本版本 %s" % APP_VERSION,
            fill="#a9b8d4", font=(ui_font, 9),
        )
        c.create_text(
            w * 0.20, sy(2036), text="切换背景", fill="#9fd0ff", font=(ui_font, 9),
            tags=("home_cyclebg",),
        )
        c.create_text(
            w * 0.50, sy(2036), text="更换背景", fill="#9fd0ff", font=(ui_font, 9),
            tags=("home_pickbg",),
        )
        c.create_text(
            w * 0.80, sy(2036), text="恢复默认", fill="#9fd0ff", font=(ui_font, 9),
            tags=("home_resetbg",),
        )
        c.tag_bind("home_cyclebg", "<Button-1>", lambda _e: self._cycle_home_bg())
        c.tag_bind("home_cyclebg", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("home_cyclebg", "<Leave>", lambda _e: c.configure(cursor=""))
        c.tag_bind("home_pickbg", "<Button-1>", lambda _e: self._pick_home_bg())
        c.tag_bind("home_pickbg", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("home_pickbg", "<Leave>", lambda _e: c.configure(cursor=""))
        c.tag_bind("home_resetbg", "<Button-1>", lambda _e: self._reset_home_bg())
        c.tag_bind("home_resetbg", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("home_resetbg", "<Leave>", lambda _e: c.configure(cursor=""))

    def _show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            "小太阳脚本\n\n"
            "脚本版本：%s\n"
            "游戏辅助工具（安卓 / 电脑同一套运行时）\n"
            "功能：红包、复活、地图刷新、闪现、取坐标、运行命令\n\n"
            "说明：使用脚本，封号自己负责。" % APP_VERSION,
        )

    def _show_sponsor(self) -> None:
        """首页「赞助」：显示微信收款二维码。"""
        win = tk.Toplevel(self.root)
        win.title("赞助作者")
        win.configure(bg="#f7f8fb")
        win.transient(self.root)
        win.resizable(False, False)
        try:
            win.grab_set()
        except tk.TclError:
            pass
        tk.Label(
            win, text="感谢支持！", bg="#f7f8fb", fg="#1f2937",
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(pady=(16, 2))
        tk.Label(
            win, text="微信扫码赞助，金额随意", bg="#f7f8fb", fg="#4b5768",
            font=("Microsoft YaHei UI", 10),
        ).pack(pady=(0, 10))
        photo = None
        try:
            from PIL import Image, ImageTk

            path = os.path.join(self.ASSET_DIR, "sponsor_qr.png")
            if os.path.exists(path):
                im = Image.open(path).convert("RGB")
                im.thumbnail((340, 340), Image.LANCZOS)
                photo = ImageTk.PhotoImage(im)
        except Exception:  # noqa: BLE001
            photo = None
        if photo is not None:
            label = tk.Label(win, image=photo, bg="#ffffff", bd=0)
            label.image = photo  # type: ignore[attr-defined]  # 防止被回收
            label.pack(padx=18, pady=(0, 8))
        else:
            tk.Label(
                win, text="二维码图片还没放好：\napp/assets/sponsor_qr.png",
                bg="#f7f8fb", fg="#b45309", font=("Microsoft YaHei UI", 10),
                justify="center",
            ).pack(padx=24, pady=10)
        tk.Label(
            win, text="赞助完全自愿，不影响任何功能", bg="#f7f8fb", fg="#8a94a6",
            font=("Microsoft YaHei UI", 9),
        ).pack(pady=(0, 8))
        tk.Button(win, text="关闭", width=14, command=win.destroy).pack(pady=(0, 14))
        win.update_idletasks()
        try:
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - win.winfo_width()) // 2)
            y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - win.winfo_height()) // 2)
            win.geometry("+%d+%d" % (x, y))
        except tk.TclError:
            pass

    ASSIST_TOP_H = 352

    def _sync_assist_status(self) -> None:
        item = getattr(self, "_assist_status_item", None)
        c = getattr(self, "_assist_top", None)
        if item is None or c is None:
            return
        try:
            c.itemconfigure(item, text=self.assist_message_var.get())
            bbox = c.bbox(item)
            bottom = bbox[3] if bbox else 0
            if abs(bottom - getattr(self, "_assist_status_bottom", 0)) > 2:
                self._assist_status_bottom = bottom
                self._draw_assist_top()
        except tk.TclError:
            pass

    def _click_tool(self, key: str) -> None:
        if key in ("commands", "play"):
            self._switch_mode(key)
            return
        self._show_tool(key)
        self._draw_assist_top()

    def _click_reconnect(self) -> None:
        try:
            self.assist_message_var.set("正在重新连接游戏窗口…")
        except tk.TclError:
            pass
        self.reconnect_assist()

    def _click_stopall(self) -> None:
        try:
            self.assist_message_var.set("已全部关闭")
        except tk.TclError:
            pass
        self.stop_all_assist()

    def toggle_assist_theme(self) -> None:
        """夜间（魔法阵）/ 白天（深蓝）背景切换。"""
        self.assist_theme = "day" if self.assist_theme == "night" else "night"
        try:
            self.config["assist_theme"] = self.assist_theme
            save_config(self.config)
        except Exception:  # noqa: BLE001
            pass
        self._apply_assist_theme()

    def _apply_assist_theme(self) -> None:
        light = getattr(self, "assist_theme", "day") == "day"
        color = "#ffffff" if light else "#231e3d"
        try:
            style = ttk.Style(self.root)
            style.configure("AssistBody.TFrame", background=color)
            canvas = self._scroll_canvases.get("assist")
            if canvas is not None:
                canvas.configure(bg=color)
            self._page_assist.configure(style="Light.TFrame" if light else "Dark.TFrame")
            self._apply_page_theme(self._page_assist, light)
            self._retint(self._page_assist, light)
        except tk.TclError:
            pass
        self._draw_assist_top()

    def _draw_assist_top(self) -> None:
        """画辅助工具页顶部：背景、返回、状态、两个按钮、图例、分类按钮。"""
        c = getattr(self, "_assist_top", None)
        if c is None:
            return
        try:
            w = c.winfo_width()
            h = c.winfo_height()
        except tk.TclError:
            return
        if w <= 2 or h <= 2:
            return

        c.delete("all")
        ui_font = "Microsoft YaHei UI"

        light = getattr(self, "assist_theme", "day") == "day"
        if not light:
            photo = self._assist_bg_photo(w, h)
            if photo is not None:
                self._assist_bg_ref = photo
                c.create_image(0, 0, image=photo, anchor="nw")
            else:
                c.configure(bg="#241d40")
        else:
            c.configure(bg="#ffffff")

        title_fg = "#16213a" if light else "#ffffff"
        sub_fg = "#33405a" if light else "#e9e5ff"
        legend_fg = "#5b6478" if light else "#c9c2ea"
        chip_bg = "#eef2f8" if light else "#1a1630"
        chip_line = "#c9d3e4" if light else "#9a8fe0"
        back_fg = "#2b3550" if light else "#e8e3ff"
        btn_bg = "#ffffff" if light else "#0f2f3d"
        btn_line = "#2aa6c8" if light else "#3fc4e0"
        btn_inner = "#bfe8f3" if light else "#2a8ea8"
        btn_fg = "#0f5c74" if light else "#dff6ff"
        legend_colors = (
            ("绿", "#15803d"), ("黄", "#b45309"), ("红", "#b91c1c")
        ) if light else (
            ("绿", "#4ade80"), ("黄", "#facc15"), ("红", "#f87171")
        )

        # 返回 + 标题
        c.create_polygon(
            self._round_points(12, 14, 96, 48, 6),
            fill=chip_bg, outline=chip_line, tags=("a_back",),
        )
        c.create_text(
            54, 31, text="‹ 返回", fill=back_fg, font=(ui_font, 10), tags=("a_back",),
        )
        c.tag_bind("a_back", "<Button-1>", lambda _e: self._switch_mode("home"))
        c.create_text(
            110, 31, anchor="w", text="辅助工具", fill=title_fg,
            font=(ui_font, 15, "bold"),
        )

        # 状态文字
        self._assist_status_item = c.create_text(
            12, 56, anchor="nw", text=self.assist_message_var.get(),
            fill=sub_fg, width=w - 24, font=(ui_font, 11), justify="left",
        )
        bbox = c.bbox(self._assist_status_item)
        self._assist_status_bottom = bbox[3] if bbox else 0

        # 夜间 / 白天 切换
        tx0, tx1 = w - 100, w - 12
        c.create_polygon(
            self._round_points(tx0, 14, tx1, 48, 6),
            fill=chip_bg, outline=chip_line, tags=("a_theme",),
        )
        c.create_text(
            (tx0 + tx1) / 2, 31,
            text="☾ 夜间" if light else "☀ 白天",
            fill=back_fg, font=(ui_font, 9), tags=("a_theme",),
        )
        c.tag_bind("a_theme", "<Button-1>", lambda _e: self.toggle_assist_theme())

        # 重新连接 / 全部关闭
        for text, x0, x1, cmd, tag in (
            ("重新连接", w - 268, w - 172, self._click_reconnect, "a_reconnect"),
            ("全部关闭", w - 164, w - 12, self._click_stopall, "a_stopall"),
        ):
            c.create_polygon(
                self._round_points(x0, 116, x1, 158, 6),
                fill=btn_bg, outline=btn_line, tags=(tag,),
            )
            c.create_polygon(
                self._round_points(x0 + 3, 119, x1 - 3, 155, 5),
                fill="", outline=btn_inner, tags=(tag,),
            )
            c.create_text(
                (x0 + x1) / 2, 137, text=text, fill=btn_fg,
                font=(ui_font, 11, "bold"), tags=(tag,),
            )
            c.tag_bind(tag, "<Button-1>", lambda _e, cmd=cmd: cmd())

        # 安全等级图例
        c.create_text(12, 170, anchor="w", text="安全等级", fill=legend_fg, font=(ui_font, 9))
        x = 74
        for text, color in legend_colors:
            c.create_polygon(
                self._round_points(x - 4, 160, x + 22, 180, 4),
                fill=chip_bg, outline="",
            )
            c.create_text(x + 9, 170, text=text, fill=color, font=(ui_font, 10, "bold"))
            x += 28
            if text != "红":
                c.create_text(x, 170, anchor="w", text="→", fill="#8b84ad", font=(ui_font, 9))
                x += 20
        c.create_text(x + 8, 170, anchor="w", text="红色最危险", fill=legend_fg, font=(ui_font, 9))

        # 分类按钮
        items = [
            ("地图 / 红包", "map", "teal"),
            ("复活 / 闪现", "revive", "orange"),
            ("抽卡", "gacha", "teal"),
            ("运行命令", "commands", "teal"),
            ("功能玩法", "play", "teal"),
        ]
        gap = 8
        bw = (w - 24 - gap) // 2
        bh = 48
        for index, (text, key, kind) in enumerate(items):
            row, col = divmod(index, 2)
            x0 = 12 + col * (bw + gap)
            y0 = 186 + row * (bh + 7)
            btn = self._tool_button_photo(bw, bh, kind)
            tag = "a_tool_%s" % key
            if btn is not None:
                c.create_image(x0, y0, image=btn, anchor="nw", tags=(tag,))
                setattr(self, "_tool_btn_%s" % key, btn)
            else:
                c.create_rectangle(x0, y0, x0 + bw, y0 + bh, fill="#153a48", outline="#3fc4e0", tags=(tag,))
            if getattr(self, "_tool_current", None) == key:
                c.create_polygon(
                    self._round_points(x0 - 2, y0 - 2, x0 + bw + 2, y0 + bh + 2, 7),
                    fill="", outline="#a5f3ff", width=2, tags=(tag,),
                )
            c.create_text(
                x0 + bw / 2, y0 + bh / 2, text=text, fill="#ffffff",
                font=(ui_font, 11, "bold"), tags=(tag,),
            )
            c.tag_bind(tag, "<Button-1>", lambda _e, k=key: self._click_tool(k))

        c.tag_bind("a_back", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("a_reconnect", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("a_stopall", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("a_back", "<Leave>", lambda _e: c.configure(cursor=""))

    def _assist_bg_photo(self, w: int, h: int):
        """辅助工具页背景（魔法阵），按尺寸缓存。"""
        key = (int(w), int(h))
        if getattr(self, "_assist_bg_key", None) == key:
            return getattr(self, "_assist_bg_ref", None)
        photo = None
        try:
            from PIL import Image, ImageTk

            path = os.path.join(self.ASSET_DIR, "assist_bg.jpg")
            im = Image.open(path).convert("RGB")
            cw, ch = im.size
            scale = max(w / cw, h / ch)
            nw, nh = max(1, int(cw * scale)), max(1, int(ch * scale))
            im = im.resize((nw, nh), Image.LANCZOS)
            left = (nw - w) // 2
            top = (nh - h) // 2
            photo = ImageTk.PhotoImage(im.crop((left, top, left + w, top + h)))
        except Exception:  # noqa: BLE001
            photo = None
        self._assist_bg_key = key
        self._assist_bg_ref = photo
        return photo

    def _tool_button_photo(self, w: int, h: int, kind: str):
        """分类按钮（玻璃质感 + 描边 + 底部光），返回 PhotoImage。"""
        cache = getattr(self, "_tool_btn_cache", {})
        key = (int(w), int(h), kind)
        if key in cache:
            return cache[key]
        photo = None
        try:
            import numpy as np
            from PIL import Image, ImageDraw, ImageFilter, ImageTk

            styles = {
                "teal": ((26, 76, 92), (10, 40, 50), (70, 214, 240)),
                "orange": ((240, 150, 52), (198, 92, 22), (255, 198, 120)),
                "red": ((140, 44, 62), (92, 22, 38), (240, 120, 140)),
            }
            top_c, bot_c, line_c = styles.get(kind, styles["teal"])
            ss = 3
            W, H = max(2, int(w)) * ss, max(2, int(h)) * ss
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            # 渐变填充
            arr = np.zeros((H, W, 3), np.uint8)
            for y in range(H):
                t = y / max(1, H - 1)
                arr[y, :, :] = [
                    int(top_c[i] + (bot_c[i] - top_c[i]) * t) for i in range(3)
                ]
            body = Image.fromarray(arr, "RGB").convert("RGBA")
            mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, W - 1, H - 1], radius=int(6 * ss), fill=255
            )
            body.putalpha(mask)
            # 外发光
            glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(glow).rounded_rectangle(
                [ss, ss, W - 1 - ss, H - 1 - ss], radius=int(6 * ss),
                outline=line_c + (200,), width=2 * ss,
            )
            glow = glow.filter(ImageFilter.GaussianBlur(3 * ss))
            img = Image.alpha_composite(img, glow)
            img = Image.alpha_composite(img, body)
            d = ImageDraw.Draw(img)
            d.rounded_rectangle(
                [ss, ss, W - 1 - ss, H - 1 - ss], radius=int(6 * ss),
                outline=line_c + (255,), width=max(1, ss),
            )
            # 底边高光
            d.line(
                [(int(W * 0.12), H - 2 * ss), (int(W * 0.88), H - 2 * ss)],
                fill=line_c + (190,), width=max(1, ss),
            )
            photo = ImageTk.PhotoImage(img.resize((int(w), int(h)), Image.LANCZOS))
        except Exception:  # noqa: BLE001
            photo = None
        cache[key] = photo
        self._tool_btn_cache = cache
        return photo

    def _build_assist_tab(self, parent: ttk.Frame) -> None:
        assist = normalize_assist(self.config.get("assist"))
        self.assist_theme = str(self.config.get("assist_theme") or "day")

        # ---------- 顶部：背景 + 状态 + 分类按钮 ----------
        top = tk.Canvas(parent, height=self.ASSIST_TOP_H, highlightthickness=0, bd=0, bg="#0c1220")
        top.pack(fill=tk.X)
        self._assist_top = top
        self.assist_message_var = tk.StringVar(
            value="辅助已自动连接，等待游戏窗口（点上方「打开游戏窗口」即可）"
        )
        self.assist_message_var.trace_add("write", lambda *_a: self._sync_assist_status())
        top.bind("<Configure>", lambda _e: self._draw_assist_top())
        parent.after(30, self._draw_assist_top)

        # ---------- 下面：各功能区块（可滚动） ----------
        body = self._make_scroll(parent, "assist", bg="#231e3d", frame_style="AssistBody.TFrame")
        parent = body

        self._tool_sections = {}
        self._tool_current = None

        # ---------- 地图刷新 ----------
        refresh = ttk.LabelFrame(parent, text="地图刷新", padding=6)
        refresh.columnconfigure(4, weight=1)

        ttk.Label(refresh, text="小怪死亡后等待").grid(row=0, column=0, sticky=tk.W)
        self.assist_mob_var = tk.StringVar(value=_seconds_text(assist.get("mob_seconds", 1)))
        ttk.Entry(refresh, textvariable=self.assist_mob_var, width=6, justify="center").grid(row=0, column=1, padx=(8, 2))
        ttk.Label(refresh, text="秒").grid(row=0, column=2, sticky=tk.W)
        self.assist_mob_btn = ttk.Button(refresh, text="开启", width=10, command=lambda: self.toggle_assist("mob"))
        self.assist_mob_btn.grid(row=0, column=3, padx=(6, 4))
        self.assist_mob_status = tk.StringVar(value="待接入")
        ttk.Label(
            refresh, textvariable=self.assist_mob_status, foreground="#9fb0c9",
            wraplength=420, justify=tk.LEFT,
        ).grid(row=1, column=0, columnspan=5, sticky=tk.W, pady=(2, 0))

        ttk.Label(refresh, text="BOSS 死亡后等待").grid(row=2, column=0, sticky=tk.W, pady=(6, 0))
        self.assist_boss_var = tk.StringVar(value=_seconds_text(assist.get("boss_seconds", 3)))
        ttk.Entry(refresh, textvariable=self.assist_boss_var, width=6, justify="center").grid(row=2, column=1, padx=(8, 2), pady=(6, 0))
        ttk.Label(refresh, text="秒").grid(row=2, column=2, sticky=tk.W, pady=(6, 0))
        self.assist_boss_btn = ttk.Button(refresh, text="开启", width=10, command=lambda: self.toggle_assist("boss"))
        self.assist_boss_btn.grid(row=2, column=3, padx=(6, 4), pady=(6, 0))
        self.assist_boss_status = tk.StringVar(value="待接入")
        ttk.Label(
            refresh, textvariable=self.assist_boss_status, foreground="#9fb0c9",
            wraplength=420, justify=tk.LEFT,
        ).grid(row=3, column=0, columnspan=5, sticky=tk.W, pady=(2, 0))
        self.assist_gold_var = tk.StringVar(value="BOSS 金币 0")
        ttk.Label(refresh, textvariable=self.assist_gold_var, foreground="#fbbf24").grid(
            row=5, column=0, columnspan=2, sticky=tk.W, pady=(6, 0)
        )
        ttk.Button(refresh, text="清除卡图", width=10, command=self.clear_stuck).grid(
            row=4, column=0, sticky=tk.W, pady=(8, 0)
        )
        ttk.Label(
            refresh,
            text="传送/回城没反应时点一下",
            foreground="#9fb0c9",
            wraplength=200,
        ).grid(row=4, column=1, columnspan=4, sticky=tk.W, padx=(8, 0), pady=(8, 0))

        self.assist_auto_chest_var = tk.BooleanVar(value=False)
        self.assist_auto_chest_btn = ttk.Checkbutton(
            refresh, text="自动宝箱", variable=self.assist_auto_chest_var,
            style="Toolbutton", command=self.toggle_auto_chest,
        )
        self.assist_auto_chest_btn.grid(row=6, column=0, sticky=tk.W, pady=(8, 0))
        self.assist_auto_status = tk.StringVar(value="自动宝箱：定时开 BOSS 掉落宝箱")
        ttk.Label(
            refresh, textvariable=self.assist_auto_status, foreground="#9fb0c9",
            wraplength=420, justify=tk.LEFT,
        ).grid(row=7, column=0, columnspan=5, sticky=tk.W, pady=(2, 0))

        # ---------- 红包 / 祈愿 ----------
        free = ttk.LabelFrame(parent, text="红包 / 祈愿", padding=6)
        free.columnconfigure(4, weight=1)

        ttk.Label(free, text="快速抢红包").grid(row=0, column=0, sticky=tk.W)
        self.assist_green_var = tk.StringVar(value="已领 0 个红包 · 0 绿钻")
        ttk.Label(free, textvariable=self.assist_green_var, foreground="#4ade80", wraplength=160).grid(row=0, column=1, columnspan=2, sticky=tk.W, padx=6)
        self.assist_red_btn = ttk.Button(free, text="开启", width=10, command=lambda: self.toggle_assist("redPacket"))
        self.assist_red_btn.grid(row=0, column=3, padx=(6, 4))
        self.assist_red_status = tk.StringVar(value="尚未开启")
        ttk.Label(free, textvariable=self.assist_red_status, foreground="#9fb0c9").grid(row=0, column=4, sticky=tk.W)

        ttk.Label(free, text="红包间隔").grid(row=2, column=0, sticky=tk.W, pady=(6, 0))
        self.assist_red_ms_var = tk.StringVar(value=str(assist.get("red_interval_ms", 20)))
        ttk.Entry(
            free, textvariable=self.assist_red_ms_var, width=6, justify="center",
        ).grid(row=2, column=1, sticky=tk.W, padx=6, pady=(6, 0))
        ttk.Label(
            free, text="毫秒（10-1000，越小越快）", foreground="#9fb0c9",
        ).grid(row=2, column=2, columnspan=3, sticky=tk.W, pady=(6, 0))

        ttk.Label(free, text="祈愿领取").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.assist_wish_btn = ttk.Button(free, text="开启", width=10, command=lambda: self.toggle_assist("wish"))
        self.assist_wish_btn.grid(row=1, column=3, padx=(6, 4), pady=(6, 0))
        self.assist_wish_status = tk.StringVar(value="未检查")
        ttk.Label(free, textvariable=self.assist_wish_status, foreground="#9fb0c9").grid(row=1, column=4, sticky=tk.W, pady=(6, 0))

        # ---------- 快速复活 ----------
        revive = ttk.LabelFrame(parent, text="快速复活", padding=6)
        revive.columnconfigure(3, weight=1)
        self._revive_flash_job = None
        self._revive_btn_pending = 0.0
        self.assist_revive_btn = ttk.Button(
            revive, text="快速复活", width=11, command=self.toggle_revive_auto,
            style="Accent.TButton",
        )
        self.assist_revive_btn.grid(row=0, column=0, sticky=tk.W)
        self.assist_revive_now_btn = ttk.Button(
            revive, text="手动复活", width=11, command=self.revive_now
        )
        self.assist_revive_now_btn.grid(row=0, column=1, padx=(6, 0))
        self.assist_clear_stuck_btn = ttk.Button(
            revive, text="清除卡图", width=11, command=self.clear_stuck
        )
        self.assist_clear_stuck_btn.grid(row=0, column=2, padx=(6, 0))
        self.assist_revive_status = tk.StringVar(value="")
        ttk.Label(
            revive, textvariable=self.assist_revive_status, foreground="#4ade80", wraplength=330
        ).grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(4, 0))

        # ---------- 闪现寻怪 ----------
        blink = ttk.LabelFrame(parent, text="闪现寻怪", padding=6)
        ttk.Label(blink, text="方式").grid(row=0, column=0, sticky=tk.W)
        self.assist_blink_mode_var = tk.StringVar(value="瞬移")
        ttk.Combobox(
            blink, textvariable=self.assist_blink_mode_var, values=["瞬移", "飞过去"],
            width=6, state="readonly",
        ).grid(row=0, column=1, sticky=tk.W, padx=4)
        ttk.Label(blink, text="筛选").grid(row=0, column=2, sticky=tk.W, padx=(8, 0))
        self.assist_monster_filter_var = tk.StringVar(value="全部")
        filter_box = ttk.Combobox(
            blink, textvariable=self.assist_monster_filter_var, values=["全部", "BOSS", "小怪"],
            width=6, state="readonly",
        )
        filter_box.grid(row=0, column=3, sticky=tk.W, padx=4)
        filter_box.bind("<<ComboboxSelected>>", lambda _e: self.render_monster_list())
        ttk.Button(blink, text="刷新怪物", width=8, command=self.refresh_blink_list).grid(
            row=0, column=4, sticky=tk.W, padx=(8, 0)
        )
        self.assist_blink_btn = ttk.Button(blink, text="过去", width=6, command=self.blink_assist)
        self.assist_blink_btn.grid(row=0, column=5, padx=(6, 4))
        self.assist_click_tp_var = tk.BooleanVar(value=False)
        self.assist_click_tp_check = ttk.Checkbutton(
            blink, text="鼠标瞬移", variable=self.assist_click_tp_var,
            style="Toolbutton", command=self.toggle_click_teleport,
        )
        self.assist_click_tp_check.grid(row=0, column=6, padx=(6, 0))
        self.assist_blink_status = tk.StringVar(value="点「刷新怪物」，选中一只再点「过去」")
        ttk.Label(
            blink, textvariable=self.assist_blink_status, foreground="#9fb0c9", wraplength=380,
            justify=tk.LEFT,
        ).grid(row=1, column=0, columnspan=7, sticky=tk.W, pady=(4, 0))

        list_frame = ttk.Frame(blink)
        list_frame.grid(row=2, column=0, columnspan=7, sticky=tk.EW, pady=(4, 0))
        self.assist_monster_list = tk.Listbox(list_frame, height=7, activestyle="none")
        list_sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.assist_monster_list.yview)
        self.assist_monster_list.configure(yscrollcommand=list_sb.set)
        self.assist_monster_list.pack(side=tk.LEFT, fill=tk.X, expand=True)
        list_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.assist_monster_list.bind("<Double-Button-1>", lambda _e: self.blink_assist())
        self.assist_monster_list.bind("<<ListboxSelect>>", lambda _e: self._update_monster_stats())
        self.assist_monster_stats = tk.StringVar(value="")
        ttk.Label(
            blink, textvariable=self.assist_monster_stats, foreground="#c7d2fe",
            wraplength=460, justify=tk.LEFT,
        ).grid(row=3, column=0, columnspan=7, sticky=tk.W, pady=(3, 0))
        self._monster_rows: List[Dict[str, Any]] = []
        self._picked_world = None
        self.assist_blink_var = tk.StringVar(value=str(assist.get("blink_target", "")))

        # ---------- 取坐标 ----------
        coord = ttk.LabelFrame(parent, text="取坐标", padding=6)
        ttk.Button(coord, text="取坐标", width=8, command=self.pick_coord).pack(side=tk.LEFT)
        ttk.Button(coord, text="飞过去", width=8, command=self.fly_to_picked).pack(side=tk.LEFT, padx=(6, 0))
        self.assist_coord_status = tk.StringVar(
            value="点「取坐标」，再到游戏画面上点一下要记录的位置"
        )
        ttk.Label(
            coord, textvariable=self.assist_coord_status, foreground="#9fb0c9", wraplength=280
        ).pack(side=tk.LEFT, padx=6)

        # ---------- 日常任务 ----------
        # 入口暂时移除（后续重写）。状态变量保留：取坐标也复用它显示进度。
        self.assist_daily_status = tk.StringVar(value="")

        # ---------- 战斗参数（保留，默认关；实测会被服务端校验）----------
        combat = ttk.LabelFrame(parent, text="战斗参数（改战斗数值，会被服务端校验，默认关）", padding=6)
        combat.columnconfigure(4, weight=1)

        ttk.Label(combat, text="英雄倍伤").grid(row=0, column=0, sticky=tk.W)
        self.assist_damage_var = tk.StringVar(value=_seconds_text(assist.get("damage_value", 2)))
        ttk.Entry(combat, textvariable=self.assist_damage_var, width=6, justify="center").grid(row=0, column=1, padx=(8, 2))
        ttk.Label(combat, text="倍").grid(row=0, column=2, sticky=tk.W)
        self.assist_damage_btn = ttk.Button(combat, text="开启", width=6, command=lambda: self.toggle_combat("damage"))
        self.assist_damage_btn.grid(row=0, column=3, padx=(6, 4))
        self.assist_damage_status = tk.StringVar(value="尚未开启")
        ttk.Label(combat, textvariable=self.assist_damage_status, foreground="#9fb0c9", wraplength=320).grid(row=0, column=4, sticky=tk.W)

        ttk.Label(combat, text="英雄免伤").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.assist_shield_var = tk.StringVar(value=_seconds_text(assist.get("shield_percent", 100)))
        ttk.Entry(combat, textvariable=self.assist_shield_var, width=6, justify="center").grid(row=1, column=1, padx=(8, 2), pady=(6, 0))
        ttk.Label(combat, text="%").grid(row=1, column=2, sticky=tk.W, pady=(6, 0))
        self.assist_shield_btn = ttk.Button(combat, text="开启", width=6, command=lambda: self.toggle_combat("shield"))
        self.assist_shield_btn.grid(row=1, column=3, padx=(6, 4), pady=(6, 0))
        self.assist_shield_status = tk.StringVar(value="尚未开启")
        ttk.Label(combat, textvariable=self.assist_shield_status, foreground="#9fb0c9", wraplength=320).grid(row=1, column=4, sticky=tk.W, pady=(6, 0))

        ttk.Label(combat, text="技能冷却").grid(row=2, column=0, sticky=tk.W, pady=(6, 0))
        self.assist_cooldown_var = tk.StringVar(value=_seconds_text(assist.get("cooldown_seconds", 3)))
        ttk.Entry(combat, textvariable=self.assist_cooldown_var, width=6, justify="center").grid(row=2, column=1, padx=(8, 2), pady=(6, 0))
        ttk.Label(combat, text="秒").grid(row=2, column=2, sticky=tk.W, pady=(6, 0))
        self.assist_cooldown_btn = ttk.Button(combat, text="开启", width=6, command=lambda: self.toggle_combat("cooldown"))
        self.assist_cooldown_btn.grid(row=2, column=3, padx=(6, 4), pady=(6, 0))
        self.assist_cooldown_status = tk.StringVar(value="尚未开启")
        ttk.Label(combat, textvariable=self.assist_cooldown_status, foreground="#9fb0c9", wraplength=320).grid(row=2, column=4, sticky=tk.W, pady=(6, 0))

        ttk.Label(
            combat,
            text=(
                "⚠ 实测：倍伤 / 免伤 / 技能冷却 / 视野 都会被服务端校验，"
                "用了会弹「战斗数据与服务器不一致」。功能默认关闭，要不要开你自己决定；"
                "开着时不能开地图刷新。"
            ),
            foreground="#f87171",
            wraplength=432,
        ).grid(row=4, column=0, columnspan=5, sticky=tk.W, pady=(6, 0))

        if self.dev_mode:
            row = ttk.Frame(combat)
            row.grid(row=5, column=0, columnspan=5, sticky=tk.W, pady=(4, 0))
            ttk.Button(row, text="诊断（查字段）", command=self.diagnose_assist).pack(side=tk.LEFT, padx=2)
            ttk.Button(row, text="自测重进", command=self.self_test_assist).pack(side=tk.LEFT, padx=2)
            ttk.Button(row, text="保存辅助设置", command=self.save_all).pack(side=tk.LEFT, padx=2)

        self.root.after(800, self._tick_assist)

    # ---------------------------------------------------------------- 分类区块

        self._tool_sections = {
            "map": refresh,
            "red": free,
            "revive": revive,
            "blink": blink,
        }

    # ---------------- 密钥 ----------------
    PROTECTED_TOOLS = {"map": "地图 / 红包", "red": "地图 / 红包",
                       "revive": "复活 / 闪现", "blink": "复活 / 闪现"}
    PROTECTED_PLAY = {"plank": "跳一跳", "guild": "公会情报"}
    # 测试完成前先从「功能玩法」隐藏，代码保留；清空这个集合即可恢复
    HIDDEN_PLAY = {"talisman", "rogue"}

    def _game_role_id(self) -> str:
        """从游戏里读当前角色 ID（模型 GZZR）。"""
        try:
            raw = self._plank_eval(
                "(function(){try{"
                "for (const p of window.System.entries()) {"
                "var e=p[1]; if(!e||typeof e!=='object') continue;"
                "var m=e.HBJS||e; if(m&&typeof m==='object'&&m.GZZR) return String(m.GZZR);"
                "}"
                "for (const p of window.System.entries()) {"
                "var e2=p[1]; if(!e2||typeof e2!=='object') continue;"
                "var m2=e2.HBJS||e2; if(m2&&typeof m2==='object'&&m2.XRFZ) return String(m2.XRFZ);"
                "}"
                "var rid = window.localStorage.getItem('RoleID') || window.localStorage.getItem('roleId');"
                "if (rid) return String(rid);"
                "return String(window.localStorage.getItem('OpenID')||'');"
                "}catch(e){return ''}})()"
            )
            return str(raw or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def _license_ok(self) -> bool:
        try:
            lic = self.config.get("license") or {}
            if not isinstance(lic, dict) or not lic.get("unlocked"):
                return False
            if is_super_key(str(lic.get("super_key") or "")) or is_super_key(
                str(lic.get("key") or "")
            ):
                return True
            keys = lic.get("keys")
            if not isinstance(keys, dict):
                keys = {}
            try:
                current = str(self._game_role_id() or "").strip()
            except Exception:  # noqa: BLE001
                current = ""
            game_id = current or str(lic.get("game_id") or "").strip()
            if not game_id:
                return False
            key = str(keys.get(game_id) or "").strip()
            if not key and str(lic.get("game_id") or "").strip() == game_id:
                key = str(lic.get("key") or "").strip()
            if not key:
                return False
            return is_valid_key(key, game_id)
        except Exception:  # noqa: BLE001
            return False

    def _build_license_tab(self, parent: tk.Frame) -> None:
        """密钥页：直接替换首页显示，不再弹新窗口。"""
        parent.configure(bg="#f5f6fa")

        top = tk.Frame(parent, bg="#f5f6fa")
        top.pack(fill=tk.X, padx=16, pady=(12, 0))
        tk.Button(
            top, text="← 返回", relief=tk.FLAT, bd=0, padx=12, pady=4,
            bg="#e8eef9", fg="#1c4f9c", activebackground="#dbe6f7",
            font=("Microsoft YaHei UI", 10, "bold"), command=self._license_back,
        ).pack(side=tk.LEFT)
        tk.Label(
            top, text="密钥", bg="#f5f6fa", fg="#1f2937",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side=tk.LEFT, padx=14)

        card = tk.Frame(
            parent, bg="#ffffff", highlightthickness=1, highlightbackground="#e2e8f4"
        )
        card.pack(pady=18)

        # 购买二维码
        photo = None
        try:
            from PIL import Image, ImageTk

            qr_path = os.path.join(self.ASSET_DIR, "pay_qr.png")
            if os.path.exists(qr_path):
                im = Image.open(qr_path).convert("RGB")
                im.thumbnail((190, 210), Image.LANCZOS)
                photo = ImageTk.PhotoImage(im)
        except Exception:  # noqa: BLE001
            photo = None
        if photo is not None:
            qr = tk.Label(card, image=photo, bg="#ffffff", bd=0)
            qr.image = photo  # type: ignore[attr-defined]  # 防止被回收
            qr.pack(padx=22, pady=(18, 10))
        else:
            tk.Frame(card, bg="#ffffff", height=12).pack()

        tk.Label(
            card, text="先进入游戏再获取ID\n一个ID一个账户",
            bg="#ffffff", fg="#8a94a6", justify="center",
            font=("Microsoft YaHei UI", 9),
        ).pack(pady=(0, 10))

        self._license_id_var = tk.StringVar()
        self._license_key_var = tk.StringVar()
        self._license_status_var = tk.StringVar()

        row1 = tk.Frame(card, bg="#ffffff")
        row1.pack(fill=tk.X, padx=22)
        tk.Label(
            row1, text="游戏ID：", bg="#ffffff", fg="#333333",
            font=("Microsoft YaHei UI", 10),
        ).pack(side=tk.LEFT)
        tk.Entry(
            row1, textvariable=self._license_id_var, state="readonly", width=22,
            readonlybackground="#f7f9fd", fg="#1a4d8f", font=("Consolas", 10),
            relief=tk.SOLID, bd=1,
        ).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(
            row1, text="复制", width=6, relief=tk.FLAT, bd=0,
            bg="#eef2ff", fg="#1e3a8a", activebackground="#dbeafe",
            font=("Microsoft YaHei UI", 9), command=self._license_copy_id,
        ).pack(side=tk.LEFT)

        row2 = tk.Frame(card, bg="#ffffff")
        row2.pack(fill=tk.X, padx=22, pady=(10, 0))
        tk.Label(
            row2, text="密钥：", bg="#ffffff", fg="#333333",
            font=("Microsoft YaHei UI", 10),
        ).pack(side=tk.LEFT)
        self._license_key_entry = tk.Entry(
            row2, textvariable=self._license_key_var, width=24,
            font=("Consolas", 10), relief=tk.SOLID, bd=1,
        )
        self._license_key_entry.pack(side=tk.LEFT)
        self._license_key_entry.bind("<Return>", lambda _e: self._license_submit())

        tk.Label(
            card, textvariable=self._license_status_var, bg="#ffffff",
            fg="#0f9d58", font=("Microsoft YaHei UI", 9),
        ).pack(pady=(8, 0))

        btns = tk.Frame(card, bg="#ffffff")
        btns.pack(pady=(10, 16))
        ttk.Button(
            btns, text="确认", width=12, style="Accent.TButton",
            command=self._license_submit,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            btns, text="取消", width=12, command=self._license_back,
        ).pack(side=tk.LEFT)

    def _open_license_page(self) -> None:
        game_id = ""
        try:
            game_id = str(self._game_role_id() or "").strip()
        except Exception:  # noqa: BLE001
            game_id = ""
        self._license_id_var.set(game_id)
        self._license_key_var.set("")
        status = ""
        try:
            lic = self.config.get("license") or {}
            if is_super_key(str(lic.get("super_key") or "")) or is_super_key(
                str(lic.get("key") or "")
            ):
                status = "已解锁（超级密钥）"
            elif self._license_ok():
                status = "此账号已绑定"
            elif game_id:
                status = "此账号未绑定"
            else:
                status = "请先进入游戏"
        except Exception:  # noqa: BLE001
            status = ""
        self._license_status_var.set(status)
        self._switch_mode("license")
        self.root.after(80, self._license_focus)

    def _license_focus(self) -> None:
        try:
            self._license_key_entry.focus_set()
        except tk.TclError:
            pass

    def _license_copy_id(self) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._license_id_var.get())
            self._license_status_var.set("游戏ID已复制")
        except tk.TclError:
            pass

    def _license_back(self) -> None:
        self._license_pending = None
        self._switch_mode(getattr(self, "_license_return_mode", "home") or "home")

    def _license_submit(self) -> None:
        key = self._license_key_var.get().strip()
        if not key:
            self._license_status_var.set("请输入密钥")
            return
        try:
            game_id = str(self._game_role_id() or "")
        except Exception:  # noqa: BLE001
            game_id = ""
        if not is_valid_key(key, game_id):
            self._license_status_var.set("密钥无效")
            return
        try:
            lic = self.config.get("license")
            if not isinstance(lic, dict):
                lic = {}
            keys = lic.get("keys")
            if not isinstance(keys, dict):
                keys = {}
            # 旧版单密钥迁移到多密钥表
            old_id = str(lic.get("game_id") or "").strip()
            old_key = str(lic.get("key") or "").strip()
            if old_id and old_key and not is_super_key(old_key):
                keys.setdefault(old_id, old_key)
            super_key = str(lic.get("super_key") or "").strip()
            if is_super_key(key):
                super_key = key
            else:
                keys[game_id] = key
            self.config["license"] = {
                "unlocked": True,
                "key": key,
                "game_id": "" if is_super_key(key) else game_id,
                "super_key": super_key,
                "keys": keys,
            }
            save_config(self.config)
        except Exception:  # noqa: BLE001
            pass
        self._license_status_var.set(
            "已解锁（超级密钥）" if is_super_key(key) else "已绑定此账号"
        )
        pending = getattr(self, "_license_pending", None)
        self._license_pending = None
        retry = pending[1] if pending else None
        self.root.after(350, lambda: self._after_license(retry))

    def _after_license(self, retry=None) -> None:
        self._switch_mode(getattr(self, "_license_return_mode", "home") or "home")
        if callable(retry):
            try:
                retry()
            except Exception:  # noqa: BLE001
                pass

    def _license_click(self) -> None:
        """首页「密钥」按钮：进入密钥页（替换首页）。"""
        self._license_pending = None
        self._license_return_mode = "home"
        self._open_license_page()

    def _ask_license(self, name: str, retry=None) -> bool:
        if self._license_ok():
            return True
        mode = ""
        try:
            mode = str(self.mode_var.get())
        except Exception:  # noqa: BLE001
            mode = ""
        self._license_return_mode = (
            mode if mode in ("home", "play", "assist", "gacha", "commands") else "home"
        )
        self._license_pending = (name, retry)
        self._open_license_page()
        return False

    def _show_tool(self, key: str) -> None:
        """点分类：只显示这一个功能；再点一次收起。"""
        if key == "gacha":
            self._switch_mode(key)
            return
        if key in self.PROTECTED_TOOLS:
            if not self._ask_license(
                self.PROTECTED_TOOLS[key], retry=lambda: self._show_tool(key)
            ):
                return
        sections = getattr(self, "_tool_sections", {}) or {}
        for widget in sections.values():
            try:
                widget.pack_forget()
            except Exception:  # noqa: BLE001
                pass
        if key == "all":
            locked = not self._license_ok()
            for name, widget in sections.items():
                if locked and name in ("map", "red", "revive", "blink"):
                    continue
                widget.pack(fill=tk.X, pady=(0, 8))
            self._tool_current = "all"
            return
        if key == self._tool_current:
            self._tool_current = None
            return
        group = {
            "map": ("map", "red"),
            "revive": ("revive", "blink"),
        }.get(key, (key,))
        shown = False
        for name in group:
            widget = sections.get(name)
            if widget is not None:
                widget.pack(fill=tk.X, pady=(0, 8))
                shown = True
        if shown:
            self._tool_current = key

    COMMAND_TEMPLATE = (
        "# 运行命令：一行一条，# 开头是注释；Ctrl+Enter 运行\n"
        "# 示例：\n"
        "# 点击(50.00,50.00)\n"
        "# 等待(1)\n"
        "# 移动(上,2)\n"
        "# 不回城也能直接传送：传送(章节,地图)，例如 传送(1,2)\n"
        "# 传送(1,2)\n"
        "# 回城()\n"
    )

    def _build_commands_tab(self, parent: ttk.Frame) -> None:
        nav = ttk.Frame(parent)
        nav.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(nav, text="‹ 返回首页", command=lambda: self._switch_mode("home")).pack(side=tk.LEFT)
        ttk.Button(nav, text="辅助工具", command=lambda: self._switch_mode("assist")).pack(side=tk.RIGHT)

        c = self.colors
        bar = tk.Frame(parent, bg=c["page"])
        bar.pack(fill=tk.X, pady=(0, 2))
        tk.Label(
            bar, text="运行命令", bg=c["page"], fg=c["text"], font=("Microsoft YaHei UI", 11, "bold")
        ).pack(side=tk.LEFT)
        self.cmd_run_btn = ttk.Button(
            bar, text="运行", width=10, style="Accent.TButton", command=self.toggle_instruction
        )
        self.cmd_run_btn.pack(side=tk.RIGHT)
        self.cmd_status = tk.StringVar(value="编辑脚本，点「运行」")
        tk.Label(
            parent,
            textvariable=self.cmd_status,
            bg=c["page"],
            fg="#1a4d8f",
            anchor="w",
            justify=tk.LEFT,
            wraplength=440,
        ).pack(fill=tk.X, pady=(0, 4))

        # 槽位 + 录制
        slot_data = load_slots(default_content=str(self.config.get("commands_text") or self.COMMAND_TEMPLATE))
        self.cmd_slots = slot_data["slots"]
        self._cmd_slot_index = max(0, min(SLOT_COUNT - 1, int(slot_data.get("active") or 1) - 1))
        self._cmd_running_slot = self._cmd_slot_index + 1
        self._cmd_syncing_slot = False
        self._cmd_recorder = CommandRecorder(
            lambda: getattr(self.assist, "bridge", None), log=self.append_log
        )

        self._cmd_preset_loaded = False
        self._cmd_preset_title = ""
        self._cmd_text_guard_until = 0.0

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 6))

        # 指令列表（按分类）
        presets = []
        categories = [{"id": 0, "name": "全部"}]
        library = {}
        try:
            library = load_library()
            for cat in library.get("categories") or []:
                cid = int(cat.get("id") or 0)
                name = str(cat.get("name") or "").strip()
                if cid and name and all(row["id"] != cid for row in categories):
                    categories.append({"id": cid, "name": name})
        except Exception:  # noqa: BLE001
            library = {}
        try:
            for item in load_sync_presets():
                content = str(item.get("content") or "").strip()
                if not content:
                    continue
                presets.append(
                    {
                        "title": str(item.get("title") or script_title(content) or "未命名指令"),
                        "content": content,
                        "category_id": int(item.get("category_id") or 3),
                    }
                )
        except Exception:  # noqa: BLE001
            pass
        try:
            for item in library_instructions(library, CATEGORY_ALL):
                content = str(item.get("content") or "").strip()
                if not content:
                    continue
                presets.append(
                    {
                        "title": str(item.get("title") or script_title(content) or "未命名指令"),
                        "content": content,
                        "category_id": int(item.get("category_id") or 3),
                    }
                )
        except Exception:  # noqa: BLE001
            pass
        for item in presets:
            cid = int(item.get("category_id") or 0)
            if cid and all(row["id"] != cid for row in categories):
                categories.append({"id": cid, "name": "分类%d" % cid})
        categories.append({"id": -1, "name": "我的"})
        self._cmd_presets = presets
        self._cmd_category_ids = [row["id"] for row in categories]
        self._cmd_category_names = [row["name"] for row in categories]
        self._cmd_category_id = 0
        self._cmd_visible_presets: list = []

        cat_row = ttk.Frame(parent)
        cat_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(cat_row, text="分类").pack(side=tk.LEFT)
        self.cmd_category_var = tk.StringVar(value="全部")
        self.cmd_category_box = ttk.Combobox(
            cat_row,
            textvariable=self.cmd_category_var,
            values=self._cmd_category_names,
            state="readonly",
            width=12,
        )
        self.cmd_category_box.pack(side=tk.LEFT, padx=6)
        self.cmd_category_box.bind("<<ComboboxSelected>>", lambda _e: self._on_category_selected())

        pick = ttk.Frame(parent)
        pick.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(pick, text="指令列表").pack(side=tk.LEFT)
        self.cmd_preset_var = tk.StringVar()
        self.cmd_preset_box = ttk.Combobox(
            pick,
            textvariable=self.cmd_preset_var,
            values=[],
            state="readonly",
            height=20,
        )
        self.cmd_preset_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.cmd_preset_box.bind("<<ComboboxSelected>>", lambda _e: self._load_selected_preset())

        action_row = ttk.Frame(parent)
        action_row.pack(fill=tk.X, pady=(0, 6))
        self.cmd_save_btn = ttk.Button(
            action_row, text="保存", width=7, command=self._save_slot_clicked
        )
        self.cmd_save_btn.pack(side=tk.LEFT, padx=(0, 3))
        self.cmd_new_btn = ttk.Button(
            action_row, text="新建", width=7, command=self._new_slot_clicked
        )
        self.cmd_new_btn.pack(side=tk.LEFT, padx=(0, 3))
        self.cmd_rename_btn = ttk.Button(
            action_row, text="重命名", width=8, command=self._rename_slot_clicked
        )
        self.cmd_rename_btn.pack(side=tk.LEFT, padx=(0, 3))
        self.cmd_clear_btn = ttk.Button(
            action_row, text="清空", width=7, command=self._clear_slot_clicked
        )
        self.cmd_clear_btn.pack(side=tk.LEFT, padx=(0, 3))
        self.cmd_record_btn = ttk.Button(
            action_row, text="录制", width=7, command=self.toggle_recording
        )
        self.cmd_record_btn.pack(side=tk.LEFT)

        self._refresh_preset_box()

        ttk.Label(
            parent,
            text=(
                "一行一条指令，# 开头是注释。支持：移动(方向或角度,秒) / 等待(秒) / "
                "点击(X%,Y%) / 滚轮(上|下|左|右,量[,X%,Y%]) / 传送(章节,地图[,大陆]，不用先回城) / "
                "回城() / 采集(目标) / 开宝箱([个数][,普通|双倍]) / 跳转(槽位) / 停止()"
            ),
            foreground="#6b7280",
            wraplength=430,
            justify=tk.LEFT,
        ).pack(side=tk.BOTTOM, anchor=tk.W, pady=(6, 0))

        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        self.cmd_text = tk.Text(frame, wrap=tk.NONE, undo=True, height=18, font=("Consolas", 10))
        ysb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.cmd_text.yview)
        xsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.cmd_text.xview)
        self.cmd_text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.cmd_text.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.cmd_text.tag_configure("run", background="#fff3cd")
        self.cmd_text.bind("<<Modified>>", self._on_cmd_text_modified)
        self.cmd_text.bind("<Control-Return>", self._run_from_shortcut)

        self._load_slot_into_editor()

    # ------------------------------------------------------------ 功能玩法

    def _build_play_tab(self, parent: ttk.Frame) -> None:
        nav = ttk.Frame(parent)
        nav.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(nav, text="‹ 返回首页", command=lambda: self._switch_mode("home")).pack(side=tk.LEFT)
        ttk.Button(nav, text="辅助工具", command=lambda: self._switch_mode("assist")).pack(side=tk.RIGHT)

        ttk.Label(
            parent,
            text="功能玩法",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        # 功能选择：默认什么都不选，点哪个才在下面显示哪个
        self._play_buttons: Dict[str, tk.Button] = {}
        self._play_panels: Dict[str, ttk.LabelFrame] = {}
        self._play_current = None
        self._guild_rows: List[Dict[str, Any]] = []
        self._guild_polling = False
        self._guild_teams_cache: Dict[tuple, Dict[str, Any]] = {}
        self._guild_teams_loading = False
        self._guild_teams_key = None
        self._guild_exporting = False
        self._guild_export_pending = False
        self._guild_export_rows: List[Dict[str, Any]] = []
        self._guild_export_index = 0
        self._guild_export_errors: List[str] = []
        self._guild_export_retry = 0
        self._maze_auto = False
        self._maze_last_stamp = ""

        chooser = ttk.Frame(parent)
        chooser.pack(fill=tk.X, pady=(0, 8))
        for col in range(2):
            chooser.columnconfigure(col, weight=1)
        play_items = [
            ("daily", "日常任务"),
            ("arena", "荣耀竞技场"),
            ("rogue", "杀戮尖塔"),
            ("plank", "跳一跳"),
            ("farm", "农场"),
            ("talisman", "护符升级"),
            ("guild", "公会情报"),
            ("maze", "公会迷宫"),
        ]
        play_items = [item for item in play_items if item[0] not in self.HIDDEN_PLAY]
        for index, (key, text) in enumerate(play_items):
            row, col = divmod(index, 2)
            span = 2 if index == len(play_items) - 1 and len(play_items) % 2 else 1
            button = tk.Button(
                chooser, text=text, command=lambda k=key: self._play_select(k),
                relief=tk.FLAT, bd=0, padx=10, pady=8, cursor="hand2",
                bg="#eef2ff", fg="#1e3a8a",
                activebackground="#dbeafe", activeforeground="#1e3a8a",
                font=("Microsoft YaHei UI", 10, "bold"),
            )
            button.grid(
                row=row, column=col, columnspan=span, sticky="ew",
                padx=(0 if col == 0 else 6, 0), pady=(0, 6),
            )
            self._play_buttons[key] = button

        content = ttk.Frame(parent)
        content.pack(fill=tk.BOTH, expand=True)
        self._play_content = content

        # ---- 日常任务 ----
        daily = ttk.LabelFrame(content, text="日常任务（领取类）", padding=10)
        tk.Label(
            daily,
            text="一键领取：任务奖励 / 福利战令 / 商店免费礼包 / 邮件。\n"
                 "需要游戏已登录；弹窗会自动关闭，找不到的项会跳过并在下面提示。",
            foreground="#7a5b00", background="#fff7e6", justify=tk.LEFT,
            anchor="w", wraplength=440, padx=8, pady=6,
            highlightthickness=1, highlightbackground="#f0d9a8",
        ).pack(fill=tk.X, pady=(0, 6))
        daily_checks = ttk.Frame(daily)
        daily_checks.pack(fill=tk.X, pady=(0, 6))
        self.daily_task_var = tk.BooleanVar(value=True)
        self.daily_welfare_var = tk.BooleanVar(value=True)
        self.daily_shop_var = tk.BooleanVar(value=True)
        self.daily_mail_var = tk.BooleanVar(value=True)
        self.daily_cards_var = tk.BooleanVar(value=False)
        self.daily_market_var = tk.BooleanVar(value=True)
        self.daily_fund_var = tk.BooleanVar(value=False)
        self.daily_activity_var = tk.BooleanVar(value=True)
        self.daily_ad_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(daily_checks, text="任务奖励", variable=self.daily_task_var).pack(side=tk.LEFT)
        ttk.Checkbutton(daily_checks, text="福利战令", variable=self.daily_welfare_var).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(daily_checks, text="超值基金", variable=self.daily_fund_var).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(daily_checks, text="活动", variable=self.daily_activity_var).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(daily_checks, text="商店免费", variable=self.daily_shop_var).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(daily_checks, text="黑市免费", variable=self.daily_market_var).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(daily_checks, text="邮件", variable=self.daily_mail_var).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(daily_checks, text="看广告", variable=self.daily_ad_var).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(daily_checks, text="用任务卡", variable=self.daily_cards_var).pack(side=tk.LEFT, padx=(8, 0))
        daily_btns = ttk.Frame(daily)
        daily_btns.pack(fill=tk.X, pady=(0, 6))
        self.daily_start_btn = ttk.Button(
            daily_btns, text="开始领取", width=12, style="Accent.TButton",
            command=self._daily_start,
        )
        self.daily_start_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.daily_stop_btn = ttk.Button(
            daily_btns, text="停止", width=8, command=self._daily_stop,
        )
        self.daily_stop_btn.pack(side=tk.LEFT)
        self.daily_status = tk.StringVar(value="未开始")
        ttk.Label(
            daily, textvariable=self.daily_status, foreground="#1a4d8f",
            wraplength=440, justify=tk.LEFT,
        ).pack(anchor=tk.W)
        self._play_panels["daily"] = daily


        # ---- 荣耀竞技场 ----
        arena = ttk.LabelFrame(content, text="荣耀竞技场", padding=8)
        tk.Label(
            arena,
            text="先打开游戏里的荣耀竞技场，再点开始。\n"
                 "次数填 0 = 一直打到没次数；打第几个填 0 = 自动优先免费，"
                 "填 1/2/3 就固定打列表对应位置的对手。",
            foreground="#7a5b00", background="#fff7e6", justify=tk.LEFT,
            anchor="w", wraplength=440, padx=8, pady=6,
            highlightthickness=1, highlightbackground="#f0d9a8",
        ).pack(fill=tk.X, pady=(0, 6))
        arena_row = ttk.Frame(arena)
        arena_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(arena_row, text="挑战次数").pack(side=tk.LEFT)
        self.arena_count_var = tk.StringVar(value="0")
        ttk.Spinbox(
            arena_row, from_=0, to=99, width=5, textvariable=self.arena_count_var,
        ).pack(side=tk.LEFT, padx=(6, 10))
        ttk.Label(arena_row, text="打第几个").pack(side=tk.LEFT)
        arena_cfg = normalize_assist(self.config.get("assist"))
        self.arena_target_var = tk.StringVar(value=str(arena_cfg.get("arena_target", 3)))
        ttk.Spinbox(
            arena_row, from_=0, to=6, width=4, textvariable=self.arena_target_var,
        ).pack(side=tk.LEFT, padx=(6, 10))
        self.arena_first_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(arena_row, text="抢第一", variable=self.arena_first_var).pack(side=tk.LEFT)
        arena_btns = ttk.Frame(arena)
        arena_btns.pack(fill=tk.X, pady=(0, 6))
        self.arena_start_btn = ttk.Button(
            arena_btns, text="开始挑战", width=12, style="Accent.TButton", command=self._arena_start,
        )
        self.arena_start_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.arena_stop_btn = ttk.Button(
            arena_btns, text="停止", width=8, command=self._arena_stop,
        )
        self.arena_stop_btn.pack(side=tk.LEFT)
        self.arena_status = tk.StringVar(value="未开始")
        ttk.Label(
            arena, textvariable=self.arena_status, foreground="#1a4d8f",
            wraplength=440, justify=tk.LEFT,
        ).pack(anchor=tk.W)
        self._play_panels["arena"] = arena

        # ---- 杀戮尖塔 ----
        rogue = ttk.LabelFrame(content, text="杀戮尖塔挑战", padding=8)
        tk.Label(
            rogue,
            text="扫荡层数填 0 = 用当前已通关的最高难度；每日最多扫荡 2 次（和游戏一致）。\n"
                 "活动没开或不在可挑战状态时会直接提示，不会乱点。",
            foreground="#7a5b00", background="#fff7e6", justify=tk.LEFT,
            anchor="w", wraplength=440, padx=8, pady=6,
            highlightthickness=1, highlightbackground="#f0d9a8",
        ).pack(fill=tk.X, pady=(0, 6))
        rogue_row = ttk.Frame(rogue)
        rogue_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(rogue_row, text="扫荡层数").pack(side=tk.LEFT)
        self.rogue_floor_var = tk.StringVar(value="0")
        ttk.Spinbox(
            rogue_row, from_=0, to=9999, width=7, textvariable=self.rogue_floor_var,
        ).pack(side=tk.LEFT, padx=(6, 12))
        self.rogue_start_btn = ttk.Button(
            rogue_row, text="开始扫荡", width=12, style="Accent.TButton", command=self._rogue_start,
        )
        self.rogue_start_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.rogue_stop_btn = ttk.Button(
            rogue_row, text="停止", width=8, command=self._rogue_stop,
        )
        self.rogue_stop_btn.pack(side=tk.LEFT)
        self.rogue_status = tk.StringVar(value="未开始")
        ttk.Label(
            rogue, textvariable=self.rogue_status, foreground="#1a4d8f",
            wraplength=440, justify=tk.LEFT,
        ).pack(anchor=tk.W)
        self._play_panels["rogue"] = rogue

        # ---- 跳一跳（秘境探宝） ----
        plank = ttk.LabelFrame(content, text="跳一跳（秘境探宝自动跳）", padding=8)
        self.plank_status = tk.StringVar(value="进入秘境探宝后点「开始自动跳」")
        ttk.Label(
            plank, textvariable=self.plank_status, foreground="#1a4d8f",
            wraplength=440, justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 6))
        ttk.Label(
            plank,
            text="先进入游戏「秘境探宝」，再点开始；会自动选力度连续跳。",
            foreground="#7a5b00",
            wraplength=440,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 6))
        plank_btns = ttk.Frame(plank)
        plank_btns.pack(fill=tk.X, pady=(0, 6))
        self.plank_run_btn = ttk.Button(
            plank_btns, text="开始自动跳", width=12, style="Accent.TButton",
            command=self.toggle_plank,
        )
        self.plank_run_btn.pack(side=tk.LEFT)
        plank_log_frame = ttk.Frame(plank)
        plank_log_frame.pack(fill=tk.BOTH, expand=True)
        self.plank_log_text = tk.Text(
            plank_log_frame, height=6, wrap=tk.WORD, state=tk.DISABLED,
            font=("Microsoft YaHei UI", 9), background="#f8fafc",
        )
        plank_sb = ttk.Scrollbar(
            plank_log_frame, orient=tk.VERTICAL, command=self.plank_log_text.yview
        )
        self.plank_log_text.configure(yscrollcommand=plank_sb.set)
        self.plank_log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        plank_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._plank_running = False
        self._plank_last = ""
        self._append_plank_log("跳一跳：进入秘境探宝后点「开始自动跳」。")
        self._play_panels["plank"] = plank

        # ---- 农场 ----
        farm = ttk.LabelFrame(content, text="农场（自动循环 / 收菜 / 偷菜 / 施肥）", padding=8)
        tk.Label(
            farm,
            text="使用说明：\n"
                 "1. 先打开游戏里的农场（个人农场或「拜访好友」列表）。\n"
                 "2. 自动循环：自己的菜熟了就回去收+补种；没熟去好友农场偷菜/施肥；次数用完只等自己的菜。\n"
                 "3. 个人农场按成熟时间检查，最多 5 分钟一次；好友农场每 2~2.5 秒一步，拜访完一轮自动停。",
            foreground="#7a5b00", background="#fff7e6", justify=tk.LEFT,
            anchor="w", wraplength=440, padx=8, pady=6,
            highlightthickness=1, highlightbackground="#f0d9a8",
        ).pack(fill=tk.X, pady=(0, 6))
        self.farm_status = tk.StringVar(
            value="推荐「自动循环」：自己的菜熟了就回去收+种，没熟就去偷菜施肥"
        )
        ttk.Label(
            farm, textvariable=self.farm_status, foreground="#1a4d8f",
            wraplength=440, justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 6))
        farm_btns = ttk.Frame(farm)
        farm_btns.pack(fill=tk.X, pady=(0, 6))
        self.farm_auto_btn = ttk.Button(
            farm_btns, text="自动循环（收菜+偷菜）", width=19, style="Accent.TButton",
            command=lambda: self._farm_start("auto"),
        )
        self.farm_auto_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.farm_own_btn = ttk.Button(
            farm_btns, text="只收自己的菜", width=12,
            command=lambda: self._farm_start("own"),
        )
        self.farm_own_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.farm_visit_btn = ttk.Button(
            farm_btns, text="只偷菜+施肥", width=12,
            command=lambda: self._farm_start("visit"),
        )
        self.farm_visit_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.farm_stop_btn = ttk.Button(
            farm_btns, text="停止", width=7, command=self._farm_stop,
        )
        self.farm_stop_btn.pack(side=tk.LEFT)
        self.farm_stats = tk.StringVar(value="本次：偷菜 0 · 施肥 0 · 拜访 0")
        ttk.Label(
            farm, textvariable=self.farm_stats, foreground="#4b5563",
        ).pack(anchor=tk.W, pady=(0, 2))
        self._farm_running = False
        self._farm_last = ""
        self._play_panels["farm"] = farm

        # ---- 护符升级 ----
        talisman = ttk.LabelFrame(content, text="护符升级", padding=10)
        ttk.Label(talisman, text="护符升级：下一步实现。").pack(anchor=tk.W)
        self._play_panels["talisman"] = talisman

        # ---- 公会情报（内嵌）----
        guild = ttk.LabelFrame(content, text="公会情报", padding=8)
        self._build_guild_panel(guild)
        self._play_panels["guild"] = guild

        # ---- 公会迷宫 ----
        maze = ttk.LabelFrame(content, text="公会迷宫", padding=8)
        self._build_maze_panel(maze)
        self._play_panels["maze"] = maze

    def _play_select(self, key: str) -> None:
        """点功能按钮：只显示选中功能的下面内容，并高亮按钮。"""
        if key in self.PROTECTED_PLAY and not self._ask_license(
            self.PROTECTED_PLAY[key], retry=lambda: self._play_select(key)
        ):
            return
        panel = self._play_panels.get(key)
        if panel is None:
            return
        for widget in self._play_panels.values():
            try:
                widget.pack_forget()
            except Exception:  # noqa: BLE001
                pass
        panel.pack(fill=tk.BOTH, expand=True)
        self._play_current = key
        for name, button in self._play_buttons.items():
            if name == key:
                button.configure(
                    bg="#2563eb", fg="#ffffff",
                    activebackground="#1d4ed8", activeforeground="#ffffff",
                )
            else:
                button.configure(
                    bg="#eef2ff", fg="#1e3a8a",
                    activebackground="#dbeafe", activeforeground="#1e3a8a",
                )
        if key in ("guild", "maze", "plank", "farm") and not self.assist.running:
            try:
                self.start_assist()
            except Exception:  # noqa: BLE001
                pass
        if key == "maze":
            if not getattr(self, "_maze_auto", False):
                self._maze_auto = True
                self.root.after(800, self._maze_auto_poll)
        else:
            self._maze_auto = False

    # ------------------------------------------------------------ 跳一跳

    def _append_plank_log(self, text: str) -> None:
        widget = getattr(self, "plank_log_text", None)
        if widget is None:
            return
        try:
            state = str(widget.cget("state"))
            if state == tk.DISABLED:
                widget.configure(state=tk.NORMAL)
            widget.insert(tk.END, str(text) + "\n")
            widget.see(tk.END)
            if state == tk.DISABLED:
                widget.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _plank_reset_button(self) -> None:
        try:
            self.plank_run_btn.configure(text="开始自动跳")
        except tk.TclError:
            pass

    def _daily_start(self) -> None:
        if getattr(self, "_daily_running", False):
            return
        items = {
            "task": bool(self.daily_task_var.get()),
            "welfare": bool(self.daily_welfare_var.get()),
            "shop": bool(self.daily_shop_var.get()),
            "mail": bool(self.daily_mail_var.get()),
            "cards": bool(self.daily_cards_var.get()),
            "market": bool(self.daily_market_var.get()),
            "fund": bool(self.daily_fund_var.get()),
            "activity": bool(self.daily_activity_var.get()),
            "ad": bool(self.daily_ad_var.get()),
        }
        if not any(items.values()):
            try:
                self.daily_status.set("先勾选要领取的项目")
            except tk.TclError:
                pass
            return
        if not self.assist.running:
            try:
                self.start_assist()
            except Exception:  # noqa: BLE001
                pass
        payload = json.dumps(items)
        try:
            raw = self._plank_eval(
                "(function(){try{var a=window.__zzAssist;"
                "if(!a||!a.dailyClaimsStart)return JSON.stringify({ok:false,message:'运行时还没注入'});"
                "return JSON.stringify(a.dailyClaimsStart(" + payload + "));}"
                "catch(e){return JSON.stringify({ok:false,message:String((e&&e.message)||e)})}})()"
            )
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception as exc:  # noqa: BLE001
            try:
                self.daily_status.set("启动失败：%s" % exc)
            except tk.TclError:
                pass
            return
        if not isinstance(data, dict) or not data.get("ok"):
            message = str((data or {}).get("message") or "启动失败")
            try:
                self.daily_status.set(message)
            except tk.TclError:
                pass
            return
        self._daily_running = True
        try:
            self.daily_start_btn.configure(state=tk.DISABLED)
            self.daily_status.set("开始领取…")
        except tk.TclError:
            pass
        self._daily_poll()

    def _daily_stop(self) -> None:
        try:
            self._plank_eval(
                "(window.__zzAssist&&window.__zzAssist.dailyClaimsStop"
                "&&window.__zzAssist.dailyClaimsStop(),'ok')"
            )
        except Exception:  # noqa: BLE001
            pass
        self._daily_running = False
        try:
            self.daily_start_btn.configure(state=tk.NORMAL)
            self.daily_status.set("已停止")
        except tk.TclError:
            pass

    def _daily_poll(self) -> None:
        if not getattr(self, "_daily_running", False):
            return
        try:
            raw = self._plank_eval(
                "(function(){try{var a=window.__zzAssist;"
                "if(!a||!a.dailyClaimsState)return 'null';"
                "return JSON.stringify(a.dailyClaimsState());}catch(e){return 'null'}})()"
            )
            st = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:  # noqa: BLE001
            st = None
        if isinstance(st, dict):
            counts = st.get("counts") or {}
            text = "领取中：%s" % (st.get("step") or "")
            text += " · 任务 %d · 福利 %d · 基金 %d · 活动 %d · 广告 %d · 商店 %d · 黑市 %d · 邮件 %d · 任务卡 %d" % (
                int(counts.get("task") or 0),
                int(counts.get("welfare") or 0),
                int(counts.get("fund") or 0),
                int(counts.get("activity") or 0),
                int(counts.get("ad") or 0),
                int(counts.get("shop") or 0),
                int(counts.get("market") or 0),
                int(counts.get("mail") or 0),
                int(counts.get("cards") or 0),
            )
            if st.get("last"):
                text += " · " + str(st.get("last"))
            if st.get("error"):
                text += " · 出错：" + str(st.get("error"))
            try:
                if st.get("running"):
                    self.daily_status.set(text)
                else:
                    self._daily_running = False
                    self.daily_start_btn.configure(state=tk.NORMAL)
                    done = "完成" in str(st.get("last") or "")
                    self.daily_status.set(("完成：" if done else "已结束：") + text.replace("领取中：", ""))
                    return
            except tk.TclError:
                pass
        self.root.after(800, self._daily_poll)

    def _arena_start(self) -> None:
        if getattr(self, "_arena_running", False):
            return
        try:
            count = int(float(self.arena_count_var.get() or 0))
        except (TypeError, ValueError):
            count = 0
        try:
            target = int(float(self.arena_target_var.get() or 0))
        except (TypeError, ValueError):
            target = 0
        target = max(0, min(6, target))
        try:
            assist_cfg = normalize_assist(self.config.get("assist"))
            assist_cfg["arena_target"] = target
            self.config["assist"] = assist_cfg
            save_config(self.config)
        except Exception:  # noqa: BLE001
            pass
        payload = json.dumps({
            "count": max(0, min(99, count)),
            "takeFirst": bool(self.arena_first_var.get()),
            "targetIndex": target,
        })
        try:
            raw = self._plank_eval(
                "(function(){try{var a=window.__zzAssist;"
                "if(!a||!a.arenaStart)return JSON.stringify({ok:false,message:'运行时还没注入'});"
                "return JSON.stringify(a.arenaStart(" + payload + "));}"
                "catch(e){return JSON.stringify({ok:false,message:String((e&&e.message)||e)})}})()"
            )
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception as exc:  # noqa: BLE001
            try:
                self.arena_status.set("启动失败：%s" % exc)
            except tk.TclError:
                pass
            return
        if not isinstance(data, dict) or not data.get("ok"):
            try:
                self.arena_status.set(str((data or {}).get("message") or "启动失败"))
            except tk.TclError:
                pass
            return
        self._arena_running = True
        try:
            self.arena_start_btn.configure(state=tk.DISABLED)
            self.arena_status.set("开始挑战…")
        except tk.TclError:
            pass
        self._arena_poll()

    def _arena_stop(self) -> None:
        try:
            self._plank_eval(
                "(window.__zzAssist&&window.__zzAssist.arenaStop"
                "&&window.__zzAssist.arenaStop(),'ok')"
            )
        except Exception:  # noqa: BLE001
            pass
        self._arena_running = False
        try:
            self.arena_start_btn.configure(state=tk.NORMAL)
            self.arena_status.set("已停止")
        except tk.TclError:
            pass

    def _arena_poll(self) -> None:
        if not getattr(self, "_arena_running", False):
            return
        try:
            raw = self._plank_eval(
                "(function(){try{var a=window.__zzAssist;"
                "if(!a||!a.arenaState)return 'null';"
                "return JSON.stringify(a.arenaState());}catch(e){return 'null'}})()"
            )
            st = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:  # noqa: BLE001
            st = None
        if isinstance(st, dict):
            point = st.get("resultAt") or {}
            try:
                px = float(point.get("x"))
                py = float(point.get("y"))
            except (TypeError, ValueError):
                px = py = 0.0
            if px > 0 and py > 0 and (time.monotonic() - getattr(self, "_arena_click_at", 0.0)) > 1.5:
                self._arena_click_at = time.monotonic()
                try:
                    call = getattr(self.assist.bridge, "_call", None)
                    if call is not None:
                        call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": px, "y": py, "button": "none", "buttons": 0})
                        time.sleep(0.03)
                        call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": px, "y": py, "button": "left", "buttons": 1, "clickCount": 1})
                        time.sleep(0.03)
                        call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": px, "y": py, "button": "left", "buttons": 0, "clickCount": 1})
                except Exception:  # noqa: BLE001
                    pass
            text = "竞技场：%s · 已打 %d 场" % (st.get("step") or "", int(st.get("done") or 0))
            if st.get("rank"):
                text += " · " + str(st.get("rank"))
            if st.get("last"):
                text += " · " + str(st.get("last"))
            if st.get("error"):
                text += " · " + str(st.get("error"))
            try:
                if st.get("running"):
                    self.arena_status.set(text)
                else:
                    self._arena_running = False
                    self.arena_start_btn.configure(state=tk.NORMAL)
                    self.arena_status.set("已结束：" + text.replace("竞技场：", ""))
                    return
            except tk.TclError:
                pass
        self.root.after(900, self._arena_poll)

    def _rogue_start(self) -> None:
        if getattr(self, "_rogue_running", False):
            return
        try:
            floor = int(float(self.rogue_floor_var.get() or 0))
        except (TypeError, ValueError):
            floor = 0
        payload = json.dumps({"floor": max(0, min(9999, floor))})
        try:
            raw = self._plank_eval(
                "(function(){try{var a=window.__zzAssist;"
                "if(!a||!a.rogueStart)return JSON.stringify({ok:false,message:'运行时还没注入'});"
                "return JSON.stringify(a.rogueStart(" + payload + "));}"
                "catch(e){return JSON.stringify({ok:false,message:String((e&&e.message)||e)})}})()"
            )
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception as exc:  # noqa: BLE001
            try:
                self.rogue_status.set("启动失败：%s" % exc)
            except tk.TclError:
                pass
            return
        if not isinstance(data, dict) or not data.get("ok"):
            try:
                self.rogue_status.set(str((data or {}).get("message") or "启动失败"))
            except tk.TclError:
                pass
            return
        self._rogue_running = True
        try:
            self.rogue_start_btn.configure(state=tk.DISABLED)
            self.rogue_status.set("开始扫荡…")
        except tk.TclError:
            pass
        self._rogue_poll()

    def _rogue_stop(self) -> None:
        try:
            self._plank_eval(
                "(window.__zzAssist&&window.__zzAssist.rogueStop"
                "&&window.__zzAssist.rogueStop(),'ok')"
            )
        except Exception:  # noqa: BLE001
            pass
        self._rogue_running = False
        try:
            self.rogue_start_btn.configure(state=tk.NORMAL)
            self.rogue_status.set("已停止")
        except tk.TclError:
            pass

    def _rogue_poll(self) -> None:
        if not getattr(self, "_rogue_running", False):
            return
        try:
            raw = self._plank_eval(
                "(function(){try{var a=window.__zzAssist;"
                "if(!a||!a.rogueState)return 'null';"
                "return JSON.stringify(a.rogueState());}catch(e){return 'null'}})()"
            )
            st = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:  # noqa: BLE001
            st = None
        if isinstance(st, dict):
            info = st.get("info") or {}
            text = "杀戮尖塔：%s" % (st.get("step") or "")
            if info.get("error"):
                text += " · " + str(info.get("error"))
            else:
                text += " · 已通关 %s · 剩余 %s" % (info.get("cleared"), info.get("remaining"))
            text += " · 已扫 %d" % int(st.get("done") or 0)
            if st.get("last"):
                text += " · " + str(st.get("last"))
            if st.get("error"):
                text += " · " + str(st.get("error"))
            try:
                if st.get("running"):
                    self.rogue_status.set(text)
                else:
                    self._rogue_running = False
                    self.rogue_start_btn.configure(state=tk.NORMAL)
                    self.rogue_status.set("已结束：" + text.replace("杀戮尖塔：", ""))
                    return
            except tk.TclError:
                pass
        self.root.after(900, self._rogue_poll)

    def _plank_eval(self, expression: str) -> Any:
        bridge = getattr(self.assist, "bridge", None)
        if bridge is None:
            raise RuntimeError("还没连上游戏窗口")
        return bridge.eval_js(expression)

    def _stop_plank(self, quiet: bool = False) -> None:
        try:
            self._plank_eval(
                "(window.__zzAssist&&window.__zzAssist.autoJumpStop"
                "&&window.__zzAssist.autoJumpStop(),'ok')"
            )
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                self._append_plank_log("停止失败：%s" % exc)
        self._plank_running = False
        self._plank_reset_button()
        try:
            self.plank_status.set("已停止" if not quiet else "已关闭")
        except tk.TclError:
            pass
        if not quiet:
            self._append_plank_log("已停止自动跳")

    def toggle_plank(self) -> None:
        if getattr(self, "_plank_running", False):
            self._stop_plank()
            return
        if not self.assist.running:
            try:
                self.start_assist()
            except Exception:  # noqa: BLE001
                pass
        try:
            raw = self._plank_eval(
                "(function(){try{var a=window.__zzAssist;"
                "if(!a||!a.autoJumpStart)return JSON.stringify({ok:false,message:'运行时还没注入'});"
                "return JSON.stringify(a.autoJumpStart({}));}"
                "catch(e){return JSON.stringify({ok:false,message:String((e&&e.message)||e)})}})()"
            )
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception as exc:  # noqa: BLE001
            try:
                self.plank_status.set("启动失败：%s" % exc)
            except tk.TclError:
                pass
            return
        if not isinstance(data, dict) or not data.get("ok"):
            message = ""
            if isinstance(data, dict):
                message = str(data.get("message") or "")
            try:
                self.plank_status.set(message or "启动失败")
            except tk.TclError:
                pass
            self._append_plank_log(message or "启动失败")
            return
        self._plank_running = True
        self._plank_last = ""
        try:
            self.plank_run_btn.configure(text="停止")
            self.plank_status.set("自动跳中…")
        except tk.TclError:
            pass
        self._append_plank_log("开始自动跳（秘境探宝）")
        self.root.after(300, self._plank_poll)

    def _plank_poll(self) -> None:
        if not getattr(self, "_plank_running", False):
            return
        try:
            raw = self._plank_eval(
                "(function(){try{var a=window.__zzAssist;"
                "return a&&a.autoJumpState?JSON.stringify(a.autoJumpState()):'{}';}"
                "catch(e){return '{}'}})()"
            )
            state = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if not isinstance(state, dict):
                state = {}
        except Exception as exc:  # noqa: BLE001
            self._plank_running = False
            self._plank_reset_button()
            try:
                self.plank_status.set("读取状态失败：%s" % exc)
            except tk.TclError:
                pass
            return
        last = str(state.get("last") or "")
        if last and last != self._plank_last:
            self._plank_last = last
            self._append_plank_log(last)
        error = str(state.get("error") or "")
        if error:
            self._plank_running = False
            self._plank_reset_button()
            try:
                self.plank_status.set("失败：" + error)
            except tk.TclError:
                pass
            self._append_plank_log("失败：" + error)
            return
        if not state.get("running"):
            self._plank_running = False
            self._plank_reset_button()
            try:
                self.plank_status.set("已结束：" + (last or "完成"))
            except tk.TclError:
                pass
            self._append_plank_log("自动跳结束：" + (last or "完成"))
            return
        try:
            jumps = int(state.get("jumps") or 0)
        except (TypeError, ValueError):
            jumps = 0
        text = "自动跳中：已跳 %d 次" % jumps
        if last:
            text += " · " + last
        try:
            self.plank_status.set(text)
        except tk.TclError:
            pass
        try:
            self.root.after(500, self._plank_poll)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 农场

    def _append_farm_log(self, text: str) -> None:
        widget = getattr(self, "farm_log_text", None)
        if widget is None:
            return
        try:
            state = str(widget.cget("state"))
            if state == tk.DISABLED:
                widget.configure(state=tk.NORMAL)
            widget.insert(tk.END, str(text) + "\n")
            widget.see(tk.END)
            if state == tk.DISABLED:
                widget.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _farm_reset_buttons(self) -> None:
        try:
            self.farm_auto_btn.configure(state=tk.NORMAL)
            self.farm_own_btn.configure(state=tk.NORMAL)
            self.farm_visit_btn.configure(state=tk.NORMAL)
        except tk.TclError:
            pass

    def _farm_eval(self, expression: str) -> Any:
        bridge = getattr(self.assist, "bridge", None)
        if bridge is None:
            raise RuntimeError("还没连上游戏窗口")
        return bridge.eval_js(expression)

    def _farm_start(self, mode: str) -> None:
        if getattr(self, "_farm_running", False):
            self._farm_stop()
        if not self.assist.running:
            try:
                self.start_assist()
            except Exception:  # noqa: BLE001
                pass
        mode_value = mode if mode in ("own", "visit", "auto") else "visit"
        try:
            raw = self._farm_eval(
                "(function(){try{var a=window.__zzAssist;"
                "if(!a||!a.farmAutoStart)return JSON.stringify({ok:false,message:'运行时还没注入'});"
                "return JSON.stringify(a.farmAutoStart({mode:'" + mode_value + "'}));}"
                "catch(e){return JSON.stringify({ok:false,message:String((e&&e.message)||e)})}})()"
            )
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception as exc:  # noqa: BLE001
            try:
                self.farm_status.set("启动失败：%s" % exc)
            except tk.TclError:
                pass
            return
        if not isinstance(data, dict) or not data.get("ok"):
            message = str(data.get("message") or "") if isinstance(data, dict) else ""
            try:
                self.farm_status.set(message or "启动失败")
            except tk.TclError:
                pass
            self._append_farm_log(message or "启动失败")
            return
        self._farm_running = True
        self._farm_last = ""
        try:
            self.farm_stats.set("本次：偷菜 0 · 施肥 0 · 拜访 0")
        except tk.TclError:
            pass
        try:
            self.farm_auto_btn.configure(state=tk.DISABLED)
            self.farm_own_btn.configure(state=tk.DISABLED)
            self.farm_visit_btn.configure(state=tk.DISABLED)
            message = str(data.get("message") or "").strip()
            if message:
                self.farm_status.set(message)
            else:
                self.farm_status.set({
                    "auto": "已开始：自动循环",
                    "own": "已开始：收自己的菜",
                    "visit": "已开始：偷菜/施肥",
                }.get(mode_value, "已开始"))
        except tk.TclError:
            pass
        self.root.after(300, self._farm_poll)

    def _farm_stop(self) -> None:
        try:
            self._farm_eval(
                "(window.__zzAssist&&window.__zzAssist.farmAutoStop"
                "&&window.__zzAssist.farmAutoStop(),'ok')"
            )
        except Exception:  # noqa: BLE001
            pass
        self._farm_running = False
        self._farm_reset_buttons()
        try:
            self.farm_status.set("已停止")
        except tk.TclError:
            pass

    def _farm_show_stats(self, state: Dict[str, Any]) -> None:
        try:
            if not isinstance(state, dict):
                return
            steal = int(state.get("stealCount") or 0)
            help_count = int(state.get("helpCount") or 0)
            visits = int(state.get("nextCount") or 0)
            text = "本次：偷菜 %d · 施肥 %d · 拜访 %d" % (steal, help_count, visits)
            counters = state.get("counters") or {}
            mode = str(state.get("mode") or "")
            if mode != "own" and isinstance(counters, dict) and counters.get("known"):
                text += " · 剩 偷%d/肥%d" % (
                    int(counters.get("steal") or 0),
                    int(counters.get("fertilize") or 0),
                )
            empty = int(state.get("emptyPlots") or 0)
            if empty:
                text += " · 空地 %d 块" % empty
            if mode != "visit":
                mature = int(state.get("ownMature") or 0)
                if mature > 0:
                    text += " · 成熟 %d 块" % mature
                next_at = int(state.get("nextOwnAt") or 0)
                left = next_at - int(time.time()) if next_at else -1
                if left > 0:
                    text += " · 下次成熟 %02d:%02d:%02d" % (
                        left // 3600, (left % 3600) // 60, left % 60)
            if state.get("seedMissing"):
                text += " · 无种子"
            self.farm_stats.set(text)
        except (tk.TclError, TypeError, ValueError):
            pass

    def _farm_poll(self) -> None:
        if not getattr(self, "_farm_running", False):
            return
        try:
            raw = self._farm_eval(
                "(function(){try{var a=window.__zzAssist;"
                "return a&&a.farmAutoState?JSON.stringify(a.farmAutoState()):'{}';}"
                "catch(e){return '{}'}})()"
            )
            state = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if not isinstance(state, dict):
                state = {}
            self._farm_show_stats(state)
        except Exception:  # noqa: BLE001
            self._farm_running = False
            self._farm_reset_buttons()
            try:
                self.farm_status.set("已停止：读取状态失败")
            except tk.TclError:
                pass
            return
        error = str(state.get("error") or "")
        if error:
            self._farm_running = False
            self._farm_reset_buttons()
            try:
                self.farm_status.set("已停止：" + error)
            except tk.TclError:
                pass
            return
        if not state.get("running"):
            self._farm_running = False
            self._farm_reset_buttons()
            try:
                self.farm_status.set("已停止")
            except tk.TclError:
                pass
            return
        try:
            self.root.after(700, self._farm_poll)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 槽位

    def _slot_label(self, index: int) -> str:
        if 0 <= index < len(self.cmd_slots):
            return slot_label(self.cmd_slots[index], index)
        return "槽位 %d · 空" % (index + 1)

    def _on_cmd_text_modified(self, _event=None) -> None:
        """用户手动改内容后，取消“从指令列表载入”的未保存状态。"""
        try:
            if not self.cmd_text.edit_modified():
                return
            if time.monotonic() < getattr(self, "_cmd_text_guard_until", 0.0):
                self.cmd_text.edit_modified(False)
                return
            if not getattr(self, "_cmd_syncing_slot", False):
                self._cmd_preset_loaded = False
            self.cmd_text.edit_modified(False)
        except tk.TclError:
            pass

    def _save_current_slot_into_memory(self) -> None:
        if not hasattr(self, "cmd_text"):
            return
        content = self.cmd_text.get("1.0", "end-1c")
        set_slot(self.cmd_slots, self._cmd_slot_index, content)
        self._cmd_preset_loaded = False
        self._refresh_preset_box()

    def _set_editor_content(self, content: str) -> None:
        self._cmd_syncing_slot = True
        old_state = str(self.cmd_text.cget("state"))
        try:
            if old_state == tk.DISABLED:
                self.cmd_text.configure(state=tk.NORMAL)
            self.cmd_text.delete("1.0", tk.END)
            self.cmd_text.insert("1.0", content)
            self.cmd_text.tag_remove("run", "1.0", tk.END)
            self.cmd_text.see("1.0")
            self.cmd_text.edit_modified(False)
        finally:
            if old_state == tk.DISABLED:
                try:
                    self.cmd_text.configure(state=tk.DISABLED)
                except tk.TclError:
                    pass
            self._cmd_syncing_slot = False
            self._cmd_text_guard_until = time.monotonic() + 0.25
        self._cmd_step_line = 1

    def _load_slot_into_editor(self) -> None:
        content = str(self.cmd_slots[self._cmd_slot_index].get("content") or "")
        self._set_editor_content(content)
        self._cmd_preset_loaded = False
        self._cmd_preset_title = ""
        self._refresh_preset_box()

    def _persist_slots(self) -> None:
        try:
            save_slots({"active": self._cmd_slot_index + 1, "slots": self.cmd_slots})
        except Exception:  # noqa: BLE001
            pass

    def _save_slot_clicked(self) -> None:
        self._save_current_slot_into_memory()
        self._persist_slots()
        self.cmd_status.set("已保存：%s" % self._slot_label(self._cmd_slot_index))

    def _new_slot_clicked(self) -> None:
        if self.instruction_runner.running or self._cmd_recorder.recording:
            return
        if not self._cmd_preset_loaded:
            self._save_current_slot_into_memory()
        empty = None
        for i, slot in enumerate(self.cmd_slots):
            if not str(slot.get("content") or "").strip():
                empty = i
                break
        if empty is None:
            messagebox.showinfo("新建脚本", "10 个槽位都满了，先清空一个再新建")
            return
        name = simpledialog.askstring(
            "新建脚本", "脚本名称：", initialvalue="未命名", parent=self.root
        )
        if name is None:
            return
        name = name.strip() or "未命名"
        self._cmd_slot_index = empty
        set_slot(self.cmd_slots, empty, "", title=name)
        self._load_slot_into_editor()
        self._persist_slots()
        self._cmd_category_id = -1
        try:
            index = self._cmd_category_ids.index(-1)
            self.cmd_category_box.current(index)
            self.cmd_category_var.set(self._cmd_category_names[index])
            self._refresh_preset_box()
        except (ValueError, tk.TclError):
            pass
        self.cmd_status.set("已新建到 %s，写完后点「保存」" % self._slot_label(empty))

    def _rename_slot_clicked(self) -> None:
        if self.instruction_runner.running or self._cmd_recorder.recording:
            return
        current = str(self.cmd_slots[self._cmd_slot_index].get("title") or "")
        name = simpledialog.askstring(
            "重命名",
            "给 %s 起个名字：" % self._slot_label(self._cmd_slot_index),
            initialvalue=current,
            parent=self.root,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            return
        self.cmd_slots[self._cmd_slot_index]["title"] = name
        self._persist_slots()
        self._refresh_preset_box()
        self.cmd_status.set("已重命名：%s" % name)

    def _clear_slot_clicked(self) -> None:
        if self.instruction_runner.running or self._cmd_recorder.recording:
            return
        if not messagebox.askyesno("清空槽位", "清空 %s？" % self._slot_label(self._cmd_slot_index)):
            return
        set_slot(self.cmd_slots, self._cmd_slot_index, "")
        self._load_slot_into_editor()
        self._persist_slots()
        self.cmd_status.set("已清空：%s" % self._slot_label(self._cmd_slot_index))

    def _cmd_slot_map(self) -> dict:
        slots = {}
        for i, slot in enumerate(self.cmd_slots):
            content = str(slot.get("content") or "")
            if i == self._cmd_slot_index:
                content = self.cmd_text.get("1.0", "end-1c")
            if content.strip():
                slots[i + 1] = content
        return slots

    def _set_commands_editable(self, editable: bool) -> None:
        state = tk.NORMAL if editable else tk.DISABLED
        try:
            self.cmd_text.configure(state=state)
        except tk.TclError:
            pass
        for widget in (
            getattr(self, "cmd_category_box", None),
            getattr(self, "cmd_preset_box", None),
        ):
            if widget is None:
                continue
            try:
                widget.configure(state="readonly" if editable else "disabled")
            except tk.TclError:
                pass
        for widget in (
            getattr(self, "cmd_save_btn", None),
            getattr(self, "cmd_new_btn", None),
            getattr(self, "cmd_rename_btn", None),
            getattr(self, "cmd_clear_btn", None),
            getattr(self, "cmd_record_btn", None),
        ):
            if widget is None:
                continue
            try:
                widget.configure(state="normal" if editable else "disabled")
            except tk.TclError:
                pass

    def _append_recorded_lines(self, lines: List[str]) -> None:
        if not lines:
            return
        try:
            state = str(self.cmd_text.cget("state"))
            if state == tk.DISABLED:
                self.cmd_text.configure(state=tk.NORMAL)
            text = self.cmd_text.get("1.0", "end-1c")
            if text and not text.endswith("\n"):
                self.cmd_text.insert(tk.END, "\n")
            self.cmd_text.insert(tk.END, "\n".join(lines) + "\n")
            self.cmd_text.see(tk.END)
            if state == tk.DISABLED:
                self.cmd_text.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def toggle_recording(self) -> None:
        if self._cmd_recorder.recording:
            lines = self._cmd_recorder.stop()
            self._append_recorded_lines(lines)
            self._save_current_slot_into_memory()
            self._persist_slots()
            try:
                self.cmd_record_btn.configure(text="录制")
            except tk.TclError:
                pass
            self._set_commands_editable(True)
            try:
                self.cmd_run_btn.configure(state=tk.NORMAL)
            except tk.TclError:
                pass
            self.cmd_status.set("录制结束，已写入 %s" % self._slot_label(self._cmd_slot_index))
            return

        if self.instruction_runner.running:
            messagebox.showinfo("录制", "先停止当前指令再录制")
            return
        if getattr(self.assist, "bridge", None) is None:
            messagebox.showinfo(
                "录制",
                "还没连上游戏窗口。\n\n请用首页「打开游戏窗口」打开游戏，"
                "等顶部显示「辅助已连接（版本号）」后再录制。",
            )
            return
        if not self._cmd_recorder.start():
            messagebox.showerror("录制", "录制启动失败，先重新连接游戏窗口再试")
            return

        self._save_current_slot_into_memory()
        stamp = time.strftime("%H:%M:%S")
        self._append_recorded_lines(["# 录制 %s" % stamp])
        try:
            self.cmd_record_btn.configure(text="停止录制")
        except tk.TclError:
            pass
        self._set_commands_editable(False)
        try:
            self.cmd_run_btn.configure(state=tk.DISABLED)
            self.cmd_record_btn.configure(state=tk.NORMAL)
        except tk.TclError:
            pass
        self.cmd_status.set("录制中：在游戏窗口里点击/拖动摇杆/滚轮，回来点「停止录制」")
        self._poll_recording()

    def _poll_recording(self) -> None:
        if not self._cmd_recorder.recording:
            return
        self._append_recorded_lines(self._cmd_recorder.poll())
        try:
            self.root.after(250, self._poll_recording)
        except tk.TclError:
            pass

    def _on_instruction_slot(self, slot: int) -> None:
        self._cmd_events.put(("slot", int(slot)))

    def _on_category_selected(self) -> None:
        index = self.cmd_category_box.current()
        if 0 <= index < len(self._cmd_category_ids):
            self._cmd_category_id = int(self._cmd_category_ids[index])
            self._refresh_preset_box()

    def _refresh_preset_box(self) -> None:
        if self._cmd_category_id == -1:
            rows = []
            for i, slot in enumerate(self.cmd_slots):
                rows.append(
                    {
                        "slot_index": i,
                        "title": slot_label(slot, i),
                        "content": str(slot.get("content") or ""),
                        "category_id": -1,
                    }
                )
            labels = [str(item.get("title") or "") for item in rows]
        else:
            if self._cmd_category_id == 0:
                rows = list(self._cmd_presets)
            else:
                rows = [
                    item
                    for item in self._cmd_presets
                    if int(item.get("category_id") or 0) == self._cmd_category_id
                ]
            labels = [str(item.get("title") or "") for item in rows]
        self._cmd_visible_presets = rows
        try:
            self.cmd_preset_box.configure(values=labels)
            self.cmd_preset_var.set("")
            self.cmd_preset_box.set("")
            if self._cmd_category_id == -1:
                for index, item in enumerate(rows):
                    if int(item.get("slot_index")) == self._cmd_slot_index:
                        self.cmd_preset_box.current(index)
                        break
        except tk.TclError:
            pass

    def _load_selected_preset(self) -> None:
        if self.instruction_runner.running or self._cmd_recorder.recording:
            messagebox.showinfo("运行命令", "先停止当前指令/录制，再切换指令列表")
            return
        index = self.cmd_preset_box.current()
        presets = getattr(self, "_cmd_visible_presets", [])
        if index < 0 or index >= len(presets):
            return
        item = presets[index]
        if item.get("slot_index") is not None:
            target = int(item["slot_index"])
            if target == self._cmd_slot_index:
                self.cmd_status.set("当前：%s" % self._slot_label(self._cmd_slot_index))
                return
            if not self._cmd_preset_loaded:
                self._save_current_slot_into_memory()
            self._cmd_slot_index = target
            self._load_slot_into_editor()
            self._persist_slots()
            self.cmd_status.set("当前：%s" % self._slot_label(self._cmd_slot_index))
            return
        if not self._cmd_preset_loaded:
            self._save_current_slot_into_memory()
        content = format_content(str(item.get("content") or ""))
        if not content:
            return
        self._set_editor_content(content)
        self._cmd_preset_loaded = True
        self._cmd_preset_title = str(item.get("title") or "未命名指令")
        self.cmd_status.set(
            "已载入：%s（点「保存」写入 %s，或点「运行」直接执行）"
            % (self._cmd_preset_title, self._slot_label(self._cmd_slot_index))
        )

    def toggle_instruction(self) -> None:
        """一个按钮：没跑就运行，跑着就停止。"""
        if self.instruction_runner.running:
            self.stop_instruction()
        else:
            self.run_instruction()

    def _set_run_button(self, running: bool) -> None:
        try:
            self.cmd_run_btn.configure(text="停止" if running else "运行")
        except tk.TclError:
            pass

    def _highlight_step(self, raw: str) -> None:
        """把当前执行的那一行标黄（重复的行接着上次往下找）。"""
        def norm(value: str) -> str:
            return (
                str(value).replace("（", "(").replace("）", ")").replace("，", ",").strip()
            )

        try:
            text = self.cmd_text
            target = norm(raw)
            last = int(str(text.index("end-1c")).split(".")[0])
            start = getattr(self, "_cmd_step_line", 1)
            found = 0
            for lineno in range(start, last + 1):
                if norm(text.get("%d.0" % lineno, "%d.end" % lineno)) == target:
                    found = lineno
                    break
            if not found:
                for lineno in range(1, start):
                    if norm(text.get("%d.0" % lineno, "%d.end" % lineno)) == target:
                        found = lineno
                        break
            text.tag_remove("run", "1.0", tk.END)
            if found:
                text.tag_add("run", "%d.0" % found, "%d.end" % found)
                text.see("%d.0" % found)
                self._cmd_step_line = found + 1
        except tk.TclError:
            pass

    def run_instruction(self) -> None:
        if self._cmd_recorder.recording:
            messagebox.showinfo("运行命令", "先停止录制")
            return
        if self.instruction_runner.running:
            self.cmd_status.set("上一条还在跑，先点「停止」")
            return
        self._save_current_slot_into_memory()
        self._persist_slots()
        content = format_content(self.cmd_text.get("1.0", "end-1c"))
        if not content:
            messagebox.showinfo("运行命令", "先写点指令再运行")
            return
        if not self.assist.running:
            try:
                self.start_assist()
            except Exception:  # noqa: BLE001
                pass
        try:
            build = self.assist.current_build()
        except Exception:  # noqa: BLE001
            build = ""
        if not build:
            try:
                status = self.assist_message_var.get()
            except Exception:  # noqa: BLE001
                status = ""
            # 状态栏里的「辅助已连接（4758a）」也来自运行时，snapshot 读不到时兜底用它。
            if "已连接" in status and "（" in status and "）" in status:
                candidate = status.split("（", 1)[1].split("）", 1)[0].strip()
                if candidate and candidate != "未知版本":
                    build = candidate
            if not build:
                messagebox.showinfo(
                    "运行命令",
                    "还没识别到游戏版本。\n\n"
                    "当前辅助状态：%s\n\n"
                    "请用首页的「打开游戏窗口」打开游戏（不要用普通浏览器），"
                    "登录并进入主界面，等顶部显示「辅助已连接（版本号）」后再运行。"
                    % (status or "辅助未连接"),
                )
                return
        self._cmd_step_line = 1
        self._cmd_running_slot = self._cmd_slot_index + 1
        slots = self._cmd_slot_map()
        try:
            self.instruction_runner.start(
                script_title(content) or "槽位%d" % (self._cmd_slot_index + 1),
                content,
                load_mapping(),
                build=build,
                slots=slots,
                start_slot=self._cmd_slot_index + 1,
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("运行命令", str(exc))
            return
        self.config["commands_text"] = content
        try:
            save_config(self.config)
        except Exception:  # noqa: BLE001
            pass
        self.cmd_text.tag_remove("run", "1.0", tk.END)
        self._set_run_button(True)
        self._set_commands_editable(False)
        self.cmd_status.set("运行中：%s" % self._slot_label(self._cmd_slot_index))

    def _run_from_shortcut(self, _event=None):
        self.toggle_instruction()
        return "break"

    def stop_instruction(self) -> None:
        if self.instruction_runner.running:
            self.instruction_runner.stop()
            self.cmd_status.set("正在停止…")

    def _on_instruction_step(self, index: int, total: int, text: str) -> None:
        """运行器线程回调：只丢队列，界面由主线程 _pump_cmd_events 更新。"""
        self._cmd_events.put(("step", index, total, text))

    def _instruction_log(self, text: str) -> None:
        """运行器日志：同时写队列（状态条显示传送/采集等细节）。"""
        self.append_log(text)
        self._cmd_events.put(("log", str(text)))

    def _on_instruction_finished(self, ok: bool, message: str) -> None:
        self._cmd_events.put(("done", ok, message))

    def _pump_cmd_events(self) -> None:
        """主线程定时取指令事件，更新状态条、当前行高亮、按钮文字。"""
        while True:
            try:
                event = self._cmd_events.get_nowait()
            except queue.Empty:
                break
            if not event:
                continue
            kind = event[0]
            try:
                if kind == "slot":
                    _, slot_no = event
                    self._cmd_running_slot = int(slot_no)
                    self._cmd_slot_index = max(0, min(SLOT_COUNT - 1, int(slot_no) - 1))
                    self._load_slot_into_editor()
                    self.cmd_status.set("运行中：%s" % self._slot_label(self._cmd_slot_index))
                elif kind == "step":
                    _, index, total, raw = event
                    self.cmd_status.set(
                        "运行中 槽位%d %d/%d：%s"
                        % (self._cmd_running_slot, index, total, raw)
                    )
                    self._highlight_step(str(raw))
                elif kind == "log":
                    _, text = event
                    text = str(text).strip()
                    if text:
                        self.cmd_status.set(text)
                elif kind == "done":
                    _, ok, message = event
                    self._set_run_button(False)
                    self._set_commands_editable(True)
                    self.cmd_status.set(("✔ " if ok else "■ ") + str(message or ""))
                    self.cmd_text.tag_remove("run", "1.0", tk.END)
            except tk.TclError:
                pass
        try:
            self.root.after(150, self._pump_cmd_events)
        except tk.TclError:
            pass

    def _restore_feature(self, feature: str, seconds: float, btn, status, attempt: int = 0) -> None:
        """恢复上次开着的刷新；刚启动时运行时可能还没装好，失败就过几秒重试。"""
        try:
            state = self.assist.state or {}
            enabled = bool((state.get(feature) or {}).get("enabled"))
            if enabled:
                btn.configure(text="关闭")
                if status is not None:
                    status.set("已恢复上次的开启状态")
                return
            self.assist.set_feature(feature, seconds)
            btn.configure(text="关闭")
            if status is not None:
                status.set("正在恢复上次的开启状态…")
            if attempt == 0:
                self.append_log(
                    "已恢复%s地图刷新：等 %g 秒" % ("小怪" if feature == "mob" else "BOSS", seconds)
                )
        except Exception:  # noqa: BLE001
            pass
        if attempt < 5:
            try:
                self.root.after(
                    4000,
                    lambda: self._restore_feature(feature, seconds, btn, status, attempt + 1),
                )
            except tk.TclError:
                pass

    def _assist_names(self) -> Dict[str, Any]:
        assist = normalize_assist(self.config.get("assist"))
        names: Dict[str, Any] = {
            "bossKeywords": assist.get("boss_keywords") or [],
            "bossIds": assist.get("boss_ids") or [],
        }
        names.update(assist.get("names") or {})
        return names

    def _read_assist_into_config(self) -> None:
        assist = normalize_assist(self.config.get("assist"))
        for key, var in (("mob_seconds", self.assist_mob_var), ("boss_seconds", self.assist_boss_var)):
            try:
                assist[key] = max(0.0, min(600.0, float(var.get().strip())))
            except (ValueError, AttributeError):
                continue
        for key, var, low, high in (
            ("damage_value", getattr(self, "assist_damage_var", None), 1.0, 70000.0),
            ("shield_percent", getattr(self, "assist_shield_var", None), 1.0, 100.0),
            ("cooldown_seconds", getattr(self, "assist_cooldown_var", None), 0.1, 3600.0),
        ):
            if var is None:
                continue
            try:
                assist[key] = max(low, min(high, float(var.get().strip())))
            except (ValueError, AttributeError, TypeError):
                continue
        blink_var = getattr(self, "assist_blink_var", None)
        if blink_var is not None:
            assist["blink_target"] = blink_var.get().strip()
        vision_var = getattr(self, "assist_vision_var", None)
        if vision_var is not None:
            assist["vision_mode"] = _vision_mode(vision_var.get())
        revive_delay_var = getattr(self, "assist_revive_delay_var", None)
        if revive_delay_var is not None:
            try:
                assist["revive_delay"] = max(0.0, min(10.0, float(revive_delay_var.get().strip())))
            except (ValueError, AttributeError, TypeError):
                pass
        arena_target_var = getattr(self, "arena_target_var", None)
        if arena_target_var is not None:
            try:
                assist["arena_target"] = max(
                    0, min(6, int(float(arena_target_var.get().strip() or "0")))
                )
            except (ValueError, AttributeError):
                pass
        red_ms_var = getattr(self, "assist_red_ms_var", None)
        if red_ms_var is not None:
            try:
                assist["red_interval_ms"] = max(10, min(1000, int(red_ms_var.get().strip() or "20")))
            except (ValueError, AttributeError):
                pass
        assist["revive_mode"] = "tp"
        assist["revive_delay"] = 0
        self.config["assist"] = assist

    def start_assist(self) -> None:
        self._read_assist_into_config()
        self.assist.set_names(self._assist_names())
        if not self.assist.running:
            self.assist.start()
            self.append_log("游戏辅助已自动连接：打开游戏窗口后会自己注入运行时")
        self._restore_assist_features()

    def _restore_assist_features(self) -> None:
        """重启工具后恢复上次开着的「小怪 / BOSS 地图刷新 / 自动复活」。"""
        cfg = normalize_assist(self.config.get("assist"))
        if cfg.get("revive_enabled"):
            try:
                self.assist.set_feature("revive", {
                    "mode": cfg.get("revive_mode") or "tp",
                    "delay": cfg.get("revive_delay") or 0,
                })
            except Exception:  # noqa: BLE001
                pass
        rows = (
            ("mob", "mob_enabled", getattr(self, "assist_mob_var", None),
             getattr(self, "assist_mob_btn", None), getattr(self, "assist_mob_status", None)),
            ("boss", "boss_enabled", getattr(self, "assist_boss_var", None),
             getattr(self, "assist_boss_btn", None), getattr(self, "assist_boss_status", None)),
        )
        for feature, key, var, btn, status in rows:
            if not cfg.get(key) or var is None or btn is None:
                continue
            try:
                seconds = max(1.0, float(str(var.get()).strip() or "1"))
            except (tk.TclError, ValueError):
                seconds = 1.0
            self._restore_feature(feature, seconds, btn, status, attempt=0)

    def _auto_connect_assist(self) -> None:
        """界面一打开就连上（和参考工具一样，不需要「启动辅助」这一步）。"""
        if self._assist_auto_started:
            return
        self._assist_auto_started = True
        try:
            self.start_assist()
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"游戏辅助自动连接失败：{exc}")
        # 不自动打开游戏窗口（用户要求）；需要时点首页「打开游戏窗口」
        # self.root.after(600, self._auto_open_game_window)
        self.root.after(4000, self.reload_gacha_heroes)

    def _game_open_mode(self) -> str:
        mode = str(self.config.get("game_open_mode") or "ask").strip().lower()
        if not ENABLE_BROWSER_OPEN and mode == "browser":
            return "ask"
        return mode if mode in ("ask", "builtin", "browser") else "ask"

    def _auto_open_game_window(self) -> None:
        """启动时打开游戏：默认直接开内置窗口（系统浏览器已关闭）。"""
        if not normalize_assist(self.config.get("assist")).get("auto_open_window", False):
            return
        if self._game_proc is not None and self._game_proc.poll() is None:
            return
        try:
            from .capture import find_window_hwnd

            if find_window_hwnd(GAME_WINDOW_TITLE):
                return
        except Exception:  # noqa: BLE001
            pass
        if not ENABLE_BROWSER_OPEN:
            self.append_log("没有检测到游戏窗口，正在打开内置游戏窗口…")
            self.open_game_window()
            return
        mode = self._game_open_mode()
        if mode == "builtin":
            self.append_log("按设置打开内置游戏窗口…")
            self.open_game_window()
        elif mode == "browser":
            self.append_log("按设置用系统浏览器打开游戏…")
            self.open_game_browser()
        else:
            self.ask_open_game(startup=True)

    def ask_open_game(self, startup: bool = False) -> None:
        """打开游戏：系统浏览器关闭时直接开内置窗口，否则弹窗让用户选。"""
        if not ENABLE_BROWSER_OPEN:
            self.open_game_window()
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("打开游戏")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="用哪种方式打开游戏？", font=("", 11, "bold")).pack(anchor=tk.W)
        ttk.Label(
            frame,
            text=(
                "内置窗口：带调试端口，游戏辅助 / 抽卡可以直接接管。\n"
                "系统浏览器：你平时用的浏览器，辅助无法注入，只能自己手动玩。"
            ),
            foreground="#555",
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(6, 10))
        remember = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="记住我的选择（以后不再询问）", variable=remember).pack(
            anchor=tk.W
        )

        btns = ttk.Frame(frame)
        btns.pack(fill=tk.X, pady=(12, 0))

        def choose(mode: str) -> None:
            if remember.get():
                self.config["game_open_mode"] = mode
                try:
                    save_config(self.config)
                except Exception:  # noqa: BLE001
                    pass
            dialog.destroy()
            if mode == "builtin":
                self.append_log("已选择：内置游戏窗口")
                self.open_game_window()
            else:
                self.append_log("已选择：系统浏览器")
                self.open_game_browser()

        ttk.Button(btns, text="内置游戏窗口", command=lambda: choose("builtin")).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btns, text="系统浏览器", command=lambda: choose("browser")).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btns, text="取消", command=dialog.destroy).pack(side=tk.RIGHT, padx=2)

        dialog.update_idletasks()
        try:
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - dialog.winfo_width()) // 2)
            y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - dialog.winfo_height()) // 3)
            dialog.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass
        if startup:
            dialog.lift()

    def reconnect_assist(self) -> None:
        self._read_assist_into_config()
        self.assist.set_names(self._assist_names())
        self.assist.reconnect()
        self.append_log("已重新连接游戏窗口：正在重新注入运行时…")

    def _pump_auto_chest_log(self) -> None:
        while True:
            try:
                text = self._auto_chest_q.get_nowait()
            except queue.Empty:
                break
            self.append_log(text)
        try:
            self.root.after(300, self._pump_auto_chest_log)
        except tk.TclError:
            pass

    def _auto_chest_loop(self) -> None:
        while not self._auto_chest_stop.is_set():
            if not self.assist.running:
                time.sleep(0.5)
                continue
            adapter = _AutoChestAdapter(
                lambda: getattr(self.assist, "bridge", None),
                self._auto_chest_q.put,
                self._auto_chest_stop,
            )
            try:
                run_chest_4758a(adapter, 0, "auto")
            except Exception as exc:  # noqa: BLE001
                msg = str(exc) or exc.__class__.__name__
                if "已停止" not in msg:
                    self._auto_chest_q.put("自动宝箱：" + msg)
            for _ in range(80):
                if self._auto_chest_stop.is_set():
                    break
                time.sleep(0.1)

    def toggle_auto_chest(self) -> None:
        on = bool(self.assist_auto_chest_var.get())
        if on:
            if not self.assist.running:
                try:
                    self.start_assist()
                except Exception:  # noqa: BLE001
                    pass
            self._auto_chest_stop.clear()
            if self._auto_chest_thread is None or not self._auto_chest_thread.is_alive():
                self._auto_chest_thread = threading.Thread(
                    target=self._auto_chest_loop, daemon=True
                )
                self._auto_chest_thread.start()
            self.append_log("自动宝箱：开启（每 8 秒检查一次）")
            self.assist_auto_status.set("自动宝箱：运行中")
        else:
            self._auto_chest_stop.set()
            self.append_log("自动宝箱：关闭")
            self.assist_auto_status.set("自动宝箱：已关闭")

    @staticmethod
    def _guild_power_text(value: Any) -> str:
        try:
            return format(int(value), ",")
        except (TypeError, ValueError):
            return "0"

    @staticmethod
    def _guild_power_placeholder(value: Any) -> bool:
        """100/200/300 是游戏给未配置队伍下发的占位战力，不是真实战力。"""
        try:
            power = int(value)
        except (TypeError, ValueError):
            return False
        return 0 <= power <= 300 and power % 100 == 0

    @classmethod
    def _guild_member_power_text(cls, value: Any) -> str:
        return "未配置" if cls._guild_power_placeholder(value) else cls._guild_power_text(value)

    @classmethod
    def _guild_team_text(cls, member: Dict[str, Any], teams=None, default: str = "未配置") -> str:
        if teams is None:
            teams = member.get("teamPowers") or []
        if not teams:
            return default
        parts = []
        for index, team in enumerate(teams):
            if not isinstance(team, dict):
                continue
            slot = team.get("slot") or (index + 1)
            parts.append("队%s %s" % (slot, cls._guild_member_power_text(team.get("power"))))
        return " / ".join(parts) or "未配置"

    @classmethod
    def _guild_group_power(cls, members: List[Dict[str, Any]]):
        real = [m for m in members if not cls._guild_power_placeholder(m.get("power"))]
        power = sum(int(m.get("power") or 0) for m in real)
        return power, len(members) - len(real), bool(real)

    def _build_guild_panel(self, parent: ttk.Frame) -> None:
        note = tk.Label(
            parent,
            text=(
                "说明：战力 100 / 200 / 300 是游戏给未配置队伍下发的占位值"
                "（每队默认 100），不是真实战力。\n"
                "工具会显示为「未配置」，并且不计入总战力；配置好队伍后才会显示真实战力。\n"
                "选中据点后，下方成员明细会自动读取并显示每个人三个队各自的战力（需要几秒）。"
            ),
            foreground="#8a5a00", background="#fff7e6", justify=tk.LEFT,
            anchor="w", wraplength=400, padx=10, pady=6,
            highlightthickness=1, highlightbackground="#f0d9a8",
        )
        note.pack(fill=tk.X, pady=(0, 6))

        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(bar, text="阵营").pack(side=tk.LEFT)
        self._guild_camp_var = tk.StringVar(value="全部")
        ttk.Combobox(
            bar, textvariable=self._guild_camp_var, values=["全部", "我方", "敌方"],
            width=6, state="readonly",
        ).pack(side=tk.LEFT, padx=(4, 10))
        self._guild_query_btn = ttk.Button(
            bar, text="查询", width=8, command=self._guild_query
        )
        self._guild_query_btn.pack(side=tk.LEFT)
        self._guild_copy_btn = ttk.Button(
            bar, text="复制结果", width=9, command=self._guild_copy
        )
        self._guild_copy_btn.pack(side=tk.LEFT, padx=(6, 0))
        self._guild_export_btn = ttk.Button(
            bar, text="全部导出", width=9, command=self._guild_export_all
        )
        self._guild_export_btn.pack(side=tk.LEFT, padx=(6, 0))

        self._guild_status = tk.StringVar(value="点「查询」读取据点成员与战力（每方 1~13 号据点）")
        ttk.Label(
            parent, textvariable=self._guild_status, foreground="#1a4d8f",
        ).pack(fill=tk.X, anchor=tk.W, pady=(0, 4))

        body = ttk.Frame(parent)
        body.pack(fill=tk.BOTH, expand=True)
        columns = ("camp", "stronghold", "count", "power", "members")
        tree = ttk.Treeview(body, columns=columns, show="headings", height=8)
        for col, text, width, anchor_side in (
            ("camp", "阵营", 60, tk.CENTER),
            ("stronghold", "据点", 70, tk.CENTER),
            ("count", "人数", 50, tk.CENTER),
            ("power", "总战力", 110, tk.E),
            ("members", "成员", 360, tk.W),
        ):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor=anchor_side, stretch=(col == "members"))
        vsb = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        detail = ttk.LabelFrame(parent, text="成员明细", padding=4)
        detail.pack(fill=tk.X, pady=(6, 0))
        text = tk.Text(detail, height=8, wrap=tk.WORD, font=("Microsoft YaHei UI", 9))
        tsb = ttk.Scrollbar(detail, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=tsb.set, state=tk.DISABLED)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tsb.pack(side=tk.RIGHT, fill=tk.Y)

        self._guild_tree = tree
        self._guild_text = text
        tree.bind("<<TreeviewSelect>>", self._guild_show_detail)

    def _guild_query(self, export: bool = False) -> None:
        tree = getattr(self, "_guild_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        if not self.assist.running:
            try:
                self.start_assist()
            except Exception:  # noqa: BLE001
                pass
        if not export:
            self._guild_export_pending = False
        camp = {"我方": 1, "敌方": 2}.get(self._guild_camp_var.get(), 0)
        result = self.assist.guild_intel_start(camp)
        if not result.get("ok"):
            self._guild_status.set("查询失败：%s" % (result.get("error") or "未知原因"))
            if export:
                self._guild_set_export_busy(False)
                self._guild_export_pending = False
            return
        self._guild_status.set("查询中 0/%s…" % (result.get("total") or 26))
        if not getattr(self, "_guild_polling", False):
            self._guild_polling = True
            self.root.after(300, self._guild_poll)

    def _guild_poll(self) -> None:
        tree = getattr(self, "_guild_tree", None)
        if tree is None or not tree.winfo_exists():
            self._guild_polling = False
            return
        state = self.assist.guild_intel_state()
        if not state:
            self.root.after(400, self._guild_poll)
            return
        done = int(state.get("done") or 0)
        total = int(state.get("total") or 0)
        if state.get("running"):
            self._guild_status.set("查询中 %d/%d…" % (done, total))
            self.root.after(350, self._guild_poll)
            return
        self._guild_polling = False
        self._guild_render(state.get("rows") or [])
        if getattr(self, "_guild_export_pending", False):
            self._guild_export_pending = False
            self.root.after(80, self._guild_start_export_teams)

    def _guild_render(self, rows: List[Dict[str, Any]]) -> None:
        tree = getattr(self, "_guild_tree", None)
        if tree is None:
            return
        rows = sorted(
            rows,
            key=lambda row: (int(row.get("camp") or 0), int(row.get("id") or 0)),
        )
        self._guild_rows = rows
        self._guild_teams_cache = {}
        self._guild_teams_loading = False
        self._guild_teams_key = None
        try:
            tree.delete(*tree.get_children())
        except tk.TclError:
            return
        own_count = own_power = enemy_count = enemy_power = 0
        own_unset = enemy_unset = 0
        for row in rows:
            camp = int(row.get("camp") or 0)
            label = "我方" if camp == 1 else "敌方"
            error = str(row.get("error") or "")
            members = row.get("members") or []
            if error:
                count = 0
                power_text = "0"
                summary = "查询失败：%s" % error
            else:
                count = int(row.get("count") or len(members))
                power, unset, has_real = self._guild_group_power(members)
                power_text = "未配置" if members and not has_real else self._guild_power_text(power)
                if members:
                    parts = []
                    for member in members[:3]:
                        name = str(member.get("name") or member.get("id") or "?")
                        parts.append("%s(%s)" % (name, self._guild_member_power_text(member.get("power"))))
                    summary = "、".join(parts)
                    if len(members) > 3:
                        summary += " 等 %d 人" % len(members)
                else:
                    summary = "（无人驻守）"
                if camp == 1:
                    own_count += count
                    own_power += power
                    own_unset += unset
                elif camp == 2:
                    enemy_count += count
                    enemy_power += power
                    enemy_unset += unset
            tree.insert("", "end", values=(
                label,
                "%d 号" % int(row.get("id") or 0),
                count,
                power_text,
                summary,
            ))
        unset_hint = ""
        if own_unset or enemy_unset:
            unset_hint = "（%d 人未配置队伍）" % (own_unset + enemy_unset)
        self._guild_status.set(
            "我方 %d 人 / 战力 %s；敌方 %d 人 / 战力 %s%s"
            % (own_count, self._guild_power_text(own_power), enemy_count,
               self._guild_power_text(enemy_power), unset_hint)
        )
        if rows:
            first = tree.get_children()
            if first:
                tree.selection_set(first[0])

    def _guild_show_detail(self, _event=None) -> None:
        tree = getattr(self, "_guild_tree", None)
        text = getattr(self, "_guild_text", None)
        if tree is None or text is None:
            return
        selection = tree.selection()
        if not selection:
            return
        index = tree.index(selection[0])
        rows = getattr(self, "_guild_rows", [])
        if index < 0 or index >= len(rows):
            return
        row = rows[index]
        camp_id = int(row.get("camp") or 0)
        spot_id = int(row.get("id") or 0)
        camp = "我方" if camp_id == 1 else "敌方"
        members = row.get("members") or []
        cache = getattr(self, "_guild_teams_cache", {}).get((camp_id, spot_id))
        loading = bool(getattr(self, "_guild_teams_loading", False)) and getattr(
            self, "_guild_teams_key", None
        ) == (camp_id, spot_id)
        if (
            members
            and cache is None
            and not getattr(self, "_guild_teams_loading", False)
            and not getattr(self, "_guild_exporting", False)
        ):
            self._guild_start_teams(camp_id, spot_id)
            loading = True
        power, unset, has_real = self._guild_group_power(members)
        power_text = "未配置" if members and not has_real else self._guild_power_text(power)
        lines = [
            "%s %d 号据点｜人数 %s｜总战力 %s"
            % (camp, int(row.get("id") or 0), row.get("count") or 0, power_text)
        ]
        error = str(row.get("error") or "")
        if error:
            lines.append("查询失败：%s" % error)
        if unset:
            lines.append("其中 %d 人未配置队伍，占位战力（100/200/300）不计入总战力。" % unset)
        for member_index, member in enumerate(members, 1):
            teams = None
            if cache is not None:
                teams = cache.get(str(member.get("id")))
            if teams is not None:
                team_default = "未配置"
            elif loading:
                team_default = "读取中…"
            elif cache is None:
                team_default = "未读取"
            else:
                team_default = "未配置"
            lines.append(
                "%2d. %s  总战力 %s  三队：%s  等级 %s  区服 %s  ID %s"
                % (
                    member_index,
                    member.get("name") or "?",
                    self._guild_member_power_text(member.get("power")),
                    self._guild_team_text(member, teams, team_default),
                    member.get("level") or 0,
                    member.get("server") or "-",
                    member.get("id") or "-",
                )
            )
        if members and cache is None and not loading:
            lines.append("分队战力未读取：点「查询」后重新选中据点。")
        text.configure(state=tk.NORMAL)
        text.delete("1.0", tk.END)
        text.insert("1.0", "\n".join(lines))
        text.configure(state=tk.DISABLED)

    def _guild_start_teams(self, camp: int, stronghold: int) -> None:
        key = (int(camp), int(stronghold))
        self._guild_teams_key = key
        self._guild_teams_loading = True
        result = self.assist.guild_teams_start(camp, stronghold)
        if not result.get("ok"):
            self._guild_teams_loading = False
            self._guild_teams_cache[key] = {}
            self._guild_status.set("分队战力：%s" % (result.get("error") or "读取失败"))
            self.root.after(50, self._guild_show_detail)
            return
        self._guild_status.set(
            "正在读取 %d 号据点分队战力 0/%s…"
            % (int(stronghold), result.get("total") or 0)
        )
        self.root.after(250, self._guild_poll_teams)

    def _guild_poll_teams(self) -> None:
        state = self.assist.guild_teams_state()
        if not state:
            self._guild_teams_loading = False
            return
        if state.get("running"):
            self._guild_status.set(
                "正在读取 %s 号据点分队战力 %d/%d…"
                % (state.get("id") or 0, state.get("done") or 0, state.get("total") or 0)
            )
            self.root.after(300, self._guild_poll_teams)
            return
        key = (int(state.get("camp") or 0), int(state.get("id") or 0))
        self._guild_teams_cache[key] = state.get("teams") or {}
        self._guild_teams_loading = False
        error = str(state.get("error") or "")
        if error:
            self._guild_status.set("分队战力读取失败：%s" % error)
        else:
            self._guild_status.set("分队战力已读取")
        self._guild_show_detail()

    def _guild_set_export_busy(self, busy: bool) -> None:
        self._guild_exporting = bool(busy)
        state = tk.DISABLED if busy else tk.NORMAL
        for name in ("_guild_query_btn", "_guild_copy_btn", "_guild_export_btn"):
            button = getattr(self, name, None)
            if button is None:
                continue
            try:
                button.configure(state=state)
            except tk.TclError:
                pass

    def _guild_export_all(self) -> None:
        """一键查询全部据点 + 每个成员的三个队战力，然后导出 TXT（可选 CSV）。"""
        if getattr(self, "_guild_exporting", False):
            return
        self._guild_set_export_busy(True)
        self._guild_export_pending = True
        self._guild_export_rows = []
        self._guild_export_index = 0
        self._guild_export_errors = []
        self._guild_export_retry = 0
        try:
            self._guild_camp_var.set("全部")
        except tk.TclError:
            pass
        self._guild_status.set("准备导出：先查询全部据点…")
        self._guild_query(export=True)

    def _guild_start_export_teams(self) -> None:
        rows = [
            row for row in getattr(self, "_guild_rows", [])
            if (row.get("members") or []) and not row.get("error")
        ]
        self._guild_export_rows = rows
        self._guild_export_index = 0
        self._guild_export_retry = 0
        if not rows:
            self._guild_finish_export()
            return
        self._guild_next_export_team()

    def _guild_next_export_team(self) -> None:
        rows = getattr(self, "_guild_export_rows", [])
        index = getattr(self, "_guild_export_index", 0)
        if index >= len(rows):
            self._guild_finish_export()
            return
        row = rows[index]
        camp = int(row.get("camp") or 0)
        spot = int(row.get("id") or 0)
        key = (camp, spot)
        cache = getattr(self, "_guild_teams_cache", {})
        if key in cache:
            self._guild_export_index = index + 1
            self._guild_export_retry = 0
            self.root.after(15, self._guild_next_export_team)
            return
        result = self.assist.guild_teams_start(camp, spot)
        if not result.get("ok"):
            error = str(result.get("error") or "读取失败")
            if "正在读取其他据点" in error and getattr(self, "_guild_export_retry", 0) < 40:
                self._guild_export_retry += 1
                self._guild_status.set("等待上一个据点读取完成…")
                self.root.after(300, self._guild_next_export_team)
                return
            self._guild_export_errors.append("%s%d号：%s" % ("我方" if camp == 1 else "敌方", spot, error))
            self._guild_export_index = index + 1
            self._guild_export_retry = 0
            self.root.after(30, self._guild_next_export_team)
            return
        self._guild_export_retry = 0
        self._guild_status.set(
            "正在读取分队 %d/%d 个据点（%s %d号）…"
            % (index + 1, len(rows), "我方" if camp == 1 else "敌方", spot)
        )
        self.root.after(250, self._guild_poll_export_team)

    def _guild_poll_export_team(self) -> None:
        state = self.assist.guild_teams_state()
        if not state:
            self._guild_export_index += 1
            self.root.after(50, self._guild_next_export_team)
            return
        if state.get("running"):
            self._guild_status.set(
                "正在读取分队 %d/%d 个据点，当前 %d/%d 人…"
                % (
                    getattr(self, "_guild_export_index", 0) + 1,
                    len(getattr(self, "_guild_export_rows", [])),
                    state.get("done") or 0,
                    state.get("total") or 0,
                )
            )
            self.root.after(300, self._guild_poll_export_team)
            return
        camp = int(state.get("camp") or 0)
        spot = int(state.get("id") or 0)
        if camp and spot:
            self._guild_teams_cache[(camp, spot)] = state.get("teams") or {}
        error = str(state.get("error") or "")
        if error:
            self._guild_export_errors.append(
                "%s%d号：%s" % ("我方" if camp == 1 else "敌方", spot, error)
            )
        self._guild_export_index += 1
        self.root.after(30, self._guild_next_export_team)

    def _guild_finish_export(self) -> None:
        self._guild_set_export_busy(False)
        try:
            self._guild_show_detail()
        except tk.TclError:
            pass
        if not getattr(self, "_guild_rows", []):
            self._guild_status.set("没有可导出的数据")
            return
        default_name = "公会战情报_%s.txt" % time.strftime("%Y%m%d_%H%M")
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出公会战情报",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[
                ("文本文件（微信/QQ 可直接打开）", "*.txt"),
                ("CSV 文件", "*.csv"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            self._guild_status.set("已取消导出（数据已缓存，可直接选中据点查看）")
            return
        try:
            members = self._guild_write_table(path)
        except OSError as exc:
            self._guild_status.set("导出失败：%s" % exc)
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        errors = len(getattr(self, "_guild_export_errors", []))
        text = "已导出 %d 个据点 / %d 人" % (
            len(getattr(self, "_guild_export_rows", [])), members
        )
        if errors:
            text += "；%d 个据点读取失败" % errors
        self._guild_status.set(text + "：" + path)

    def _guild_write_table(self, path: str) -> int:
        total_members = 0
        delimiter = "\t" if path.lower().endswith(".txt") else ","
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=delimiter)
            writer.writerow([
                "阵营", "据点", "序号", "成员", "总战力",
                "队1战力", "队2战力", "队3战力", "等级", "区服", "ID",
            ])
            for row in getattr(self, "_guild_rows", []):
                camp = int(row.get("camp") or 0)
                label = "我方" if camp == 1 else "敌方"
                spot = int(row.get("id") or 0)
                cache = getattr(self, "_guild_teams_cache", {}).get((camp, spot)) or {}
                for index, member in enumerate(row.get("members") or [], 1):
                    teams = cache.get(str(member.get("id"))) or []
                    by_slot: Dict[int, Any] = {}
                    for team in teams:
                        if not isinstance(team, dict):
                            continue
                        try:
                            slot = int(team.get("slot") or 0)
                            by_slot[slot] = int(team.get("power") or 0)
                        except (TypeError, ValueError):
                            continue
                    writer.writerow([
                        label,
                        spot,
                        index,
                        member.get("name") or "?",
                        int(member.get("power") or 0),
                        by_slot.get(1, ""),
                        by_slot.get(2, ""),
                        by_slot.get(3, ""),
                        member.get("level") or 0,
                        member.get("server") or "",
                        member.get("id") or "",
                    ])
                    total_members += 1
            writer.writerow([])
            writer.writerow(["汇总"])
            writer.writerow(["阵营", "人数", "真实总战力", "未配置人数"])
            for camp in (1, 2):
                members = [
                    member
                    for row in getattr(self, "_guild_rows", [])
                    if int(row.get("camp") or 0) == camp
                    for member in (row.get("members") or [])
                ]
                power, unset, _ = self._guild_group_power(members)
                writer.writerow([
                    "我方" if camp == 1 else "敌方",
                    len(members),
                    power,
                    unset,
                ])
        return total_members

    def _guild_copy(self) -> None:
        rows = getattr(self, "_guild_rows", [])
        if not rows:
            self._guild_status.set("还没有查询结果")
            return
        lines: List[str] = []
        for row in rows:
            camp = "我方" if int(row.get("camp") or 0) == 1 else "敌方"
            members = row.get("members") or []
            power, unset, has_real = self._guild_group_power(members)
            power_text = "未配置" if members and not has_real else self._guild_power_text(power)
            lines.append(
                "%s %d 号据点  人数 %s  总战力 %s"
                % (camp, int(row.get("id") or 0), row.get("count") or 0, power_text)
            )
            if unset:
                lines.append("  其中 %d 人未配置队伍（占位战力不计入总战力）" % unset)
            if row.get("error"):
                lines.append("  查询失败：%s" % row.get("error"))
                continue
            cache = getattr(self, "_guild_teams_cache", {}).get(
                (int(row.get("camp") or 0), int(row.get("id") or 0))
            ) or {}
            for member in members:
                teams = cache.get(str(member.get("id")))
                lines.append(
                    "  %s  总战力 %s  三队：%s  等级 %s  区服 %s  ID %s"
                    % (
                        member.get("name") or "?",
                        self._guild_member_power_text(member.get("power")),
                        self._guild_team_text(member, teams, "未读取"),
                        member.get("level") or 0,
                        member.get("server") or "-",
                        member.get("id") or "-",
                    )
                )
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self._guild_status.set("已复制 %d 条据点记录" % len(rows))

    def _build_maze_panel(self, parent: ttk.Frame) -> None:
        note = tk.Label(
            parent,
            text=(
                "说明：自动读取当前迷宫难度/层数；进入「公会迷宫首领战斗」后点「查询首领」，"
                "读取首领血量、抗暴击、抗暴伤、命中和全部属性。\n"
                "不会发起挑战或修改游戏数据；离开战斗后仍会保留本次结果，刷新页面后清空。"
            ),
            foreground="#8a5a00", background="#fff7e6", justify=tk.LEFT,
            anchor="w", wraplength=400, padx=10, pady=6,
            highlightthickness=1, highlightbackground="#f0d9a8",
        )
        note.pack(fill=tk.X, pady=(0, 6))

        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(bar, text="查询首领", width=10, command=self._maze_query).pack(side=tk.LEFT)
        ttk.Button(bar, text="复制结果", width=9, command=self._maze_copy).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        self._maze_status = tk.StringVar(value="进入公会迷宫首领战斗后点「查询首领」")
        ttk.Label(
            parent, textvariable=self._maze_status, foreground="#1a4d8f",
            wraplength=400, justify=tk.LEFT,
        ).pack(fill=tk.X, anchor=tk.W, pady=(0, 4))

        self._maze_context_var = tk.StringVar(value="当前迷宫：未进入公会迷宫")
        ttk.Label(
            parent, textvariable=self._maze_context_var, foreground="#0f766e",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(fill=tk.X, anchor=tk.W, pady=(0, 4))

        self._maze_summary = tk.StringVar(value="尚未查询")
        ttk.Label(
            parent, textvariable=self._maze_summary, foreground="#334155",
            wraplength=400, justify=tk.LEFT, font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(fill=tk.X, anchor=tk.W, pady=(0, 4))

        body = ttk.Frame(parent)
        body.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(body, columns=("name", "value"), show="headings", height=8)
        tree.heading("name", text="属性")
        tree.heading("value", text="数值")
        tree.column("name", width=120, anchor=tk.W, stretch=False)
        tree.column("value", width=250, anchor=tk.W, stretch=True)
        vsb = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self._maze_tree = tree

    def _maze_refresh_context(self) -> None:
        """读取当前迷宫难度/层数，不要求进首领战斗。"""
        try:
            ctx = self.assist.maze_context()
        except Exception:  # noqa: BLE001
            ctx = {}
        if ctx.get("ok"):
            floors = ctx.get("floors")
            self._maze_context_var.set(
                "当前迷宫：难度 %s · 第 %s/%s 层"
                % (
                    ctx.get("difficulty") or "-",
                    ctx.get("floor") or "-",
                    floors if floors not in (None, 0) else "-",
                )
            )
            return
        cached = ctx.get("cached") or {}
        if cached:
            floors = cached.get("floors")
            self._maze_context_var.set(
                "当前迷宫：难度 %s · 第 %s/%s 层（上次）"
                % (
                    cached.get("difficulty") or "-",
                    cached.get("floor") or "-",
                    floors if floors not in (None, 0) else "-",
                )
            )
        else:
            self._maze_context_var.set("当前迷宫：未进入公会迷宫")

    def _maze_auto_poll(self) -> None:
        """选中「公会迷宫」时自动观察，进入首领战斗后自动记录一次。"""
        if not getattr(self, "_maze_auto", False) or getattr(self, "_play_current", None) != "maze":
            self._maze_auto = False
            return
        self._maze_refresh_context()
        try:
            result = self.assist.maze_query()
            snapshot = result.get("snapshot")
            stamp = str((snapshot or {}).get("capturedAt") or "")
            if result.get("ok") and snapshot and stamp != getattr(self, "_maze_last_stamp", ""):
                self._maze_last_stamp = stamp
                self._maze_render(snapshot, cached=False)
            elif not self._maze_last_stamp:
                cached = result.get("cached")
                if cached:
                    self._maze_last_stamp = str(cached.get("capturedAt") or "cached")
                    self._maze_render(cached, cached=True)
                    self._maze_status.set("等待进入首领战斗…（下面是上次结果）")
        except Exception:  # noqa: BLE001
            pass
        if getattr(self, "_maze_auto", False) and getattr(self, "_play_current", None) == "maze":
            self.root.after(2000, self._maze_auto_poll)

    def _maze_query(self) -> None:
        self._maze_refresh_context()
        if not self.assist.running:
            try:
                self.start_assist()
            except Exception:  # noqa: BLE001
                pass
        result = self.assist.maze_query()
        snapshot = result.get("snapshot")
        if result.get("ok") and snapshot:
            self._maze_last_stamp = str(snapshot.get("capturedAt") or "")
            self._maze_render(snapshot, cached=False)
            return
        error = str(result.get("error") or "未知原因")
        cached = result.get("cached")
        if cached:
            self._maze_render(cached, cached=True)
            self._maze_status.set("当前不在首领战斗：%s（下面是上次查询结果）" % error)
        else:
            self._maze_status.set("查询失败：%s" % error)
            self._maze_summary.set("尚未查询")

    def _maze_render(self, snapshot: Dict[str, Any], cached: bool = False) -> None:
        def pct(value: Any) -> str:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return "-"
            return "%.2f%%" % number

        difficulty = snapshot.get("difficulty")
        floor = snapshot.get("floor")
        self._maze_summary.set(
            "难度 %s · 第 %s 层 · 首领 %s · 总血量 %s · 抗暴击 %s · 抗暴伤 %s · 命中 %s"
            % (
                difficulty if difficulty not in (None, 0) else "-",
                floor if floor not in (None, 0) else "-",
                snapshot.get("bossId") or "-",
                self._guild_power_text(snapshot.get("maxHp") or 0),
                pct(snapshot.get("antiCrit")),
                pct(snapshot.get("antiCritDamage")),
                pct(snapshot.get("hitRate")),
            )
        )
        tree = getattr(self, "_maze_tree", None)
        if tree is not None:
            try:
                tree.delete(*tree.get_children())
                for item in snapshot.get("attributes") or []:
                    tree.insert("", "end", values=(
                        item.get("name") or ("属性 #%s" % item.get("id")),
                        item.get("value") or item.get("raw") or "",
                    ))
            except tk.TclError:
                pass
        stamp = str(snapshot.get("capturedAt") or "")
        if cached:
            self._maze_status.set("显示上次查询结果（%s）" % (stamp or "时间未知"))
        else:
            self._maze_status.set("查询成功：%s" % (stamp or "已记录"))

    def _maze_copy(self) -> None:
        snapshot = None
        # 从面板当前显示内容复制，避免再发一次游戏请求
        summary = getattr(self, "_maze_summary", None)
        tree = getattr(self, "_maze_tree", None)
        if summary is None or tree is None:
            return
        lines = []
        context = getattr(self, "_maze_context_var", None)
        if context is not None and context.get():
            lines.append(context.get())
        lines.append(summary.get())
        for item in tree.get_children():
            values = tree.item(item, "values")
            if len(values) >= 2:
                lines.append("%s：%s" % (values[0], values[1]))
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        try:
            self._maze_status.set("已复制首领属性")
        except Exception:  # noqa: BLE001
            pass

    def stop_assist(self) -> None:
        self._disable_click_teleport()
        try:
            self.assist_auto_chest_var.set(False)
            self._auto_chest_stop.set()
        except Exception:  # noqa: BLE001
            pass
        self.assist.stop()
        self.append_log("游戏辅助已停止（页面里的开关状态会在下次连接时恢复）")

    def stop_all_assist(self) -> None:
        self.assist.stop_all()
        self.append_log("已发送：全部关闭")

    def toggle_assist(self, feature: str) -> None:
        if not self.assist.running:
            self.start_assist()
        state = self.assist.state or {}
        if feature in ("mob", "boss"):
            var = self.assist_mob_var if feature == "mob" else self.assist_boss_var
            btn = self.assist_mob_btn if feature == "mob" else self.assist_boss_btn
            status = self.assist_mob_status if feature == "mob" else self.assist_boss_status
            try:
                seconds = float(var.get().strip())
            except ValueError:
                messagebox.showerror("参数错误", "等待秒数需要是数字")
                return
            if not 0 <= seconds <= 600:
                messagebox.showerror("参数错误", "等待秒数需要在 0-600 之间")
                return
            enabled = bool((state.get(feature) or {}).get("enabled"))
            try:
                assist_cfg = normalize_assist(self.config.get("assist"))
                assist_cfg["mob_enabled" if feature == "mob" else "boss_enabled"] = not enabled and seconds > 0
                self.config["assist"] = assist_cfg
                save_config(self.config)
            except Exception:  # noqa: BLE001
                pass
            if enabled or seconds <= 0:
                self.assist.set_feature(feature, 0)
                # 先把按钮/状态改过来，别等 0.6 秒后的快照，看起来像没生效
                btn.configure(text="开启")
                status.set("正在关闭…")
                self.append_log(f"已发送：关闭{'小怪' if feature == 'mob' else 'BOSS'}地图刷新")
            else:
                self.assist.set_feature(feature, seconds)
                btn.configure(text="关闭")
                status.set(f"正在开启（{seconds:g} 秒）…")
                self.append_log(
                    f"已发送：{'小怪' if feature == 'mob' else 'BOSS'} 死亡后等待 {seconds:g} 秒重进地图"
                )
            return

        if feature == "redPacket":
            try:
                red_ms = max(10, min(1000, int(self.assist_red_ms_var.get().strip() or "20")))
            except (ValueError, AttributeError):
                messagebox.showerror("参数错误", "红包间隔需要是 10-1000 的整数毫秒")
                return
            self.assist.set_feature("redConfig", {"tickMs": red_ms, "gapMs": 0})
            try:
                assist_cfg = normalize_assist(self.config.get("assist"))
                assist_cfg["red_interval_ms"] = red_ms
                self.config["assist"] = assist_cfg
                save_config(self.config)
            except Exception:  # noqa: BLE001
                pass
        enabled = bool((state.get(feature) or {}).get("enabled"))
        self.assist.set_feature(feature, not enabled)
        label = {"redPacket": "快速抢红包", "wish": "祈愿领取"}.get(feature, feature)
        if feature == "redPacket":
            self.assist_red_btn.configure(text="关闭" if not enabled else "开启")
            self.assist_red_status.set(
                ("正在开启（%dms）…" % red_ms) if not enabled else "正在关闭…"
            )
        elif feature == "wish":
            self.assist_wish_btn.configure(text="关闭" if not enabled else "开启")
            self.assist_wish_status.set("正在开启…" if not enabled else "正在关闭…")
        self.append_log(f"已发送：{'关闭' if enabled else '开启'}{label}")

    def toggle_combat(self, name: str) -> None:
        """倍伤 / 视野 / 冷却：读界面值 → 发指令（0 或 -1 表示关闭）。"""
        if not self.assist.running:
            self.start_assist()
        combat = (self.assist.state or {}).get("combat") or {}
        item = combat.get(name) or {}
        enabled = bool(item.get("enabled"))

        if name == "damage":
            try:
                value = float(self.assist_damage_var.get().strip())
            except (ValueError, AttributeError):
                messagebox.showerror("参数错误", "倍率需要是数字")
                return
            if not 1 <= value <= 70000:
                messagebox.showerror("参数错误", "倍率需要在 1 - 70000 之间")
                return
            send: Any = 0 if enabled else value
            btn, status, label = (self.assist_damage_btn, self.assist_damage_status, "英雄倍伤")
        elif name == "shield":
            try:
                value = float(self.assist_shield_var.get().strip())
            except (ValueError, AttributeError):
                messagebox.showerror("参数错误", "免伤比例需要是数字")
                return
            if not 1 <= value <= 100:
                messagebox.showerror("参数错误", "免伤比例需要在 1 - 100 之间")
                return
            send = 0 if enabled else value
            btn, status, label = (self.assist_shield_btn, self.assist_shield_status, "英雄免伤")
        else:
            try:
                value = float(self.assist_cooldown_var.get().strip())
            except (ValueError, AttributeError):
                messagebox.showerror("参数错误", "冷却需要是数字")
                return
            if not 0.1 <= value <= 3600:
                messagebox.showerror("参数错误", "冷却需要在 0.1 - 3600 秒之间")
                return
            send = 0 if enabled else value
            btn, status, label = (self.assist_cooldown_btn, self.assist_cooldown_status, "技能冷却")

        self._read_assist_into_config()
        self.assist.set_feature(name, send)
        closing = (send < 0) if name == "vision" else (send <= 0)
        if name == "vision":
            btn.configure(text="应用")
        else:
            btn.configure(text="开启" if closing else "关闭")
        status.set("正在关闭…" if closing else "正在应用…")
        self.append_log(f"已发送：{'关闭' if closing else '开启'}{label}")

    def _revive_flash(self, text: str) -> None:
        """只显示按钮点击结果，1.6 秒后自动清掉。"""
        try:
            self.assist_revive_status.set(text)
            job = getattr(self, "_revive_flash_job", None)
            if job is not None:
                self.root.after_cancel(job)
            self._revive_flash_job = self.root.after(
                1600, lambda: self.assist_revive_status.set("")
            )
        except tk.TclError:
            pass

    def toggle_revive_auto(self) -> None:
        """开启/关闭自动复活。"""
        if not self.assist.running:
            self.start_assist()
        revive = (self.assist.state or {}).get("revive") or {}
        enabled = bool(revive.get("enabled"))
        try:
            assist_cfg = normalize_assist(self.config.get("assist"))
            assist_cfg["revive_enabled"] = not enabled
            assist_cfg["revive_mode"] = "tp"
            assist_cfg["revive_delay"] = 0
            self.config["assist"] = assist_cfg
            save_config(self.config)
        except Exception:  # noqa: BLE001
            pass
        self._revive_btn_pending = time.monotonic() + 1.5
        if enabled:
            self.assist.set_feature("revive", 0)
            self.assist_revive_btn.configure(text="快速复活")
            self._revive_flash("关闭成功")
            self.append_log("已发送：关闭自动复活")
            return
        self.assist.set_feature("revive", {"mode": "tp", "delay": 0})
        self.assist_revive_btn.configure(text="关闭")
        self._revive_flash("开启成功")
        self.append_log("已发送：开启自动复活")

    def clear_stuck(self) -> None:
        """清除卡住的切图标记：传送/回城点了没反应时用。"""
        if not self.assist.running:
            self.start_assist()
        result = self.assist.clear_stuck()
        self._revive_flash("清除成功")
        message = str((result or {}).get("message") or "已尝试清除")
        try:
            self.append_log("已发送：" + message)
        except Exception:  # noqa: BLE001
            pass

    def revive_now(self) -> None:
        """手动复活一次（免费：0.1 秒原地拉起倒地的英雄，不传送、不花货币）。"""
        if not self.assist.running:
            self.start_assist()
        self.assist.set_feature("reviveNow", 1)
        self._revive_flash("手动复活成功")
        self.append_log("已发送：立即复活（免费，瞬移营地复活后回到原地）")

    def refresh_blink_list(self) -> None:
        """读当前地图的怪（客户端已加载的全部），按筛选条件显示。"""
        if not self.assist.running:
            self.start_assist()
        rows = []
        try:
            rows = self.assist.list_monsters() or []
        except Exception:  # noqa: BLE001
            rows = []
        self._monster_rows = rows
        self.render_monster_list()

    def render_monster_list(self) -> None:
        """按「全部 / BOSS / 小怪」筛选后填到列表。"""
        rows = getattr(self, "_monster_rows", []) or []
        mode = str(self.assist_monster_filter_var.get() or "全部")
        if mode == "BOSS":
            view = [r for r in rows if r.get("boss")]
        elif mode == "小怪":
            view = [r for r in rows if not r.get("boss")]
        else:
            view = list(rows)
        self._monster_view = view

        lb = self.assist_monster_list
        lb.delete(0, tk.END)
        for item in view[:400]:
            name = str(item.get("name") or "怪")
            dist = int(item.get("d") or 0)
            hp = item.get("hp")
            max_hp = item.get("maxHp")
            if isinstance(hp, (int, float)) and isinstance(max_hp, (int, float)) and max_hp > 0:
                hp_text = "血量 %s/%s" % (_fmt_hp(hp), _fmt_hp(max_hp))
            elif isinstance(hp, (int, float)):
                hp_text = "血量 %s" % _fmt_hp(hp)
            else:
                hp_text = ""
            tail = ("    " + hp_text if hp_text else "") + "    距离 %d" % dist
            if item.get("boss"):
                lb.insert(tk.END, "★ %s%s" % (name, tail))
                try:
                    lb.itemconfig(tk.END, foreground="#c62828")
                except tk.TclError:
                    pass
            else:
                lb.insert(tk.END, "%s%s" % (name, tail))

        if not rows:
            self.assist_blink_status.set("没读到怪（先进地图、等辅助连上再刷新）")
            self.assist_monster_stats.set("")
            return
        bosses = sum(1 for r in rows if r.get("boss"))
        label = {"BOSS": "BOSS", "小怪": "小怪"}.get(mode, "全部")
        self.assist_blink_status.set(
            "当前地图 %d 只（BOSS %d / 小怪 %d）· %s：显示 %d 只"
            % (len(rows), bosses, len(rows) - bosses, label, len(view))
        )
        self._update_monster_stats()

    def _update_monster_stats(self) -> None:
        """列表下方显示当前选中（没选就第一只）的属性。"""
        var = getattr(self, "assist_monster_stats", None)
        if var is None:
            return
        rows = getattr(self, "_monster_view", []) or []
        if not rows:
            var.set("")
            return
        sel = self.assist_monster_list.curselection()
        idx = int(sel[0]) if sel else 0
        if idx < 0 or idx >= len(rows):
            idx = 0
        item = rows[idx]
        parts = []
        for stat in item.get("stats") or []:
            if not isinstance(stat, dict):
                continue
            name = str(stat.get("label") or stat.get("key") or "")
            text = stat.get("text")
            if text is None or text == "":
                text = stat.get("value")
            if name and text is not None:
                parts.append("%s %s" % (name, text))
        hp = item.get("hp")
        max_hp = item.get("maxHp")
        hp_text = ""
        if isinstance(hp, (int, float)) and isinstance(max_hp, (int, float)) and max_hp > 0:
            hp_text = "%s/%s" % (_fmt_hp(hp), _fmt_hp(max_hp))
        elif isinstance(hp, (int, float)):
            hp_text = _fmt_hp(hp)
        head = "第%d只" % (idx + 1) if not sel else "选中"
        detail = " · ".join(parts)
        if hp_text:
            detail = ("血量 " + hp_text + (" · " if detail else "")) + detail
        if not detail:
            detail = "该版本暂不支持读取属性"
        try:
            var.set(head + "：" + detail)
        except tk.TclError:
            pass

    def fly_to_picked(self) -> None:
        """飞到最近一次「取坐标」取到的位置。"""
        point = getattr(self, "_picked_world", None)
        if not point:
            self.assist_coord_status.set("先点「取坐标」，在游戏画面上点一下")
            return
        if not self.assist.running:
            self.start_assist()
        mode = "fly" if self.assist_blink_mode_var.get() == "飞过去" else "instant"
        self.assist.set_feature("blinkTo", {"x": point[0], "y": point[1], "mode": mode})
        self.assist_coord_status.set("正在飞过去…" if mode == "fly" else "正在瞬移…")
        self.append_log("已发送：飞到坐标 (%d, %d)" % (int(point[0]), int(point[1])))

    def _disable_click_teleport(self) -> None:
        try:
            if getattr(self, "assist_click_tp_var", None) is not None:
                self.assist_click_tp_var.set(False)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.assist.click_teleport(False)
        except Exception:  # noqa: BLE001
            pass

    def toggle_click_teleport(self) -> None:
        on = bool(self.assist_click_tp_var.get())
        if on and not self.assist.running:
            try:
                self.start_assist()
            except Exception:  # noqa: BLE001
                pass
        result = self.assist.click_teleport(on)
        if result.get("ok"):
            if on:
                self.append_log("鼠标瞬移已开启：点地图任意位置即闪现")
                self.assist_coord_status.set("鼠标瞬移：点地图任意位置")
            else:
                self.append_log("鼠标瞬移已关闭")
                self.assist_coord_status.set("鼠标瞬移已关闭")
        else:
            self.assist_click_tp_var.set(False)
            msg = result.get("error") or "未知原因"
            self.assist_coord_status.set("鼠标瞬移开启失败：%s" % msg)

    def pick_coord(self) -> None:
        if getattr(self, "assist_click_tp_var", None) is not None and self.assist_click_tp_var.get():
            self._disable_click_teleport()
        """取坐标：先点按钮，再在游戏画面上点一下要记录的位置。"""
        if not self.assist.running:
            self.start_assist()
        result = self.assist.pick_coord()
        if not result.get("ok"):
            self.assist_daily_status.set("取坐标失败：%s" % (result.get("error") or "未知原因"))
            return
        self.assist_daily_status.set("请到游戏画面上点一下要记录的位置…")
        self.root.after(700, self._poll_picked)

    def _poll_picked(self, attempt: int = 0) -> None:
        picked = self.assist.picked_coord()
        armed_at = 0
        try:
            armed_at = int(self.assist.bridge.eval_js("String(window.__zzPickArmedAt || 0)") or 0)
        except Exception:  # noqa: BLE001
            armed_at = 0
        fresh = bool(picked) and int(picked.get("at") or 0) >= armed_at
        if picked and (fresh or picked.get("error") or picked.get("canceled")):
            if picked.get("error"):
                self.assist_daily_status.set("取坐标出错：%s" % picked["error"])
                return
            if picked.get("canceled"):
                self.assist_coord_status.set("已取消")
                return
            try:
                self._picked_world = (float(picked.get("x")), float(picked.get("y")))
            except (TypeError, ValueError):
                self._picked_world = None
            text = "坐标 (%s, %s)" % (picked.get("x"), picked.get("y"))
            self.assist_coord_status.set(text + "  已复制（点「飞过去」过去）")
            self.assist_daily_status.set(text + "  已复制")
            copy_text = str(picked.get("text") or ("%s,%s" % (picked.get("x"), picked.get("y"))))
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(copy_text)
            except tk.TclError:
                pass
            self.append_log("取坐标：" + text + "（%s）" % copy_text)
            return
        if attempt < 60:
            self.root.after(500, lambda: self._poll_picked(attempt + 1))

    def blink_assist(self) -> None:
        """过去：瞬移或飞过去（选列表里那只怪）。"""
        if not self.assist.running:
            self.start_assist()
        mode = "fly" if self.assist_blink_mode_var.get() == "飞过去" else "instant"
        rows = getattr(self, "_monster_view", None)
        if rows is None:
            rows = getattr(self, "_monster_rows", []) or []
        sel = self.assist_monster_list.curselection()
        if sel and 0 <= int(sel[0]) < len(rows):
            item = rows[int(sel[0])]
            self.assist.set_feature(
                "blinkTo",
                {"x": item.get("x"), "y": item.get("y"), "mode": mode},
            )
            self.assist_blink_status.set("正在飞过去…" if mode == "fly" else "正在闪现…")
            self.append_log(
                ("已发送：飞过去 → " if mode == "fly" else "已发送：闪现 → ")
                + str(item.get("name") or "")
            )
            return
        target = self.assist_blink_var.get().strip()
        if not target:
            self.assist_blink_status.set("先在列表里选一只怪，或填名字")
            return
        self.assist.set_feature("blink", target)
        self.assist_blink_status.set(f"正在找「{target}」…")
        self.append_log(f"已发送：闪现到「{target}」")

    def _update_assist_labels(self) -> None:
        state = self.assist.state or {}
        if not state:
            self.assist_message_var.set("辅助已自动连接，等待游戏窗口（点上方「打开游戏窗口」即可）")
            return
        gap = bool(state.get("missingMapping")) and not state.get("ready")
        try:
            if state.get("ready"):
                red = (state.get("redPacket") or {})
                self.home_status_var.set(
                    "已连接 · %s · 今日红包 %s 个" % (state.get("build") or "?", red.get("today") or 0)
                )
            elif state.get("error"):
                self.home_status_var.set("未就绪：%s" % state.get("error"))
            else:
                self.home_status_var.set("等待游戏进入主界面…")
        except Exception:  # noqa: BLE001
            pass
        if gap:
            self.assist_message_var.set(
                "辅助未就绪：没有当前游戏版本的字段映射（游戏每次更新都会重新随机内部名）。"
                "把新版本补进 app/assist_mapping.json 后即可启用。"
            )
        elif not state.get("ok") or (state.get("error") and not state.get("ready")):
            self.assist_message_var.set(f"辅助未就绪：{state.get('error') or '未知原因'}")
        else:
            self.assist_message_var.set(str(state.get("message") or "辅助已连接"))

        mob = state.get("mob") or {}
        boss = state.get("boss") or {}
        red = state.get("redPacket") or {}
        wish = state.get("wish") or {}

        combat = state.get("combat") or {}
        damage = combat.get("damage") or {}
        vision = combat.get("vision") or {}
        cooldown = combat.get("cooldown") or {}

        revive = state.get("revive") or {}

        blink = state.get("blink") or {}
        self.assist_blink_btn.configure(
            state=tk.DISABLED if gap else tk.NORMAL,
            text="闪现中…" if blink.get("busy") else "闪现",
        )
        self.assist_blink_status.set(str(blink.get("reason") or "点「刷新怪物」，选中一只再点「过去」"))

        for button in (self.assist_mob_btn, self.assist_boss_btn,
                       self.assist_red_btn, self.assist_wish_btn,
                       self.assist_damage_btn, self.assist_shield_btn,
                       self.assist_cooldown_btn,
                       self.assist_revive_btn, self.assist_revive_now_btn,
                       self.assist_clear_stuck_btn):
            button.configure(state=tk.DISABLED if gap else tk.NORMAL)

        self.assist_mob_btn.configure(text="关闭" if mob.get("enabled") else "开启")
        self.assist_boss_btn.configure(text="关闭" if boss.get("enabled") else "开启")
        self.assist_damage_btn.configure(text="关闭" if damage.get("enabled") else "开启")
        self.assist_shield_btn.configure(text="关闭" if (combat.get("shield") or {}).get("enabled") else "开启")
        self.assist_cooldown_btn.configure(text="关闭" if cooldown.get("enabled") else "开启")
        self.assist_red_btn.configure(text="关闭" if red.get("enabled") else "开启")
        self.assist_wish_btn.configure(text="关闭" if wish.get("enabled") else "开启")

        self.assist_mob_status.set(str(mob.get("reason") or "待接入"))
        self.assist_damage_status.set(str(damage.get("reason") or ("需要版本映射" if gap else "尚未开启")))
        self.assist_shield_status.set(str((combat.get("shield") or {}).get("reason") or ("需要版本映射" if gap else "尚未开启")))
        self.assist_cooldown_status.set(str(cooldown.get("reason") or ("需要版本映射" if gap else "尚未开启")))
        self.assist_boss_status.set(str(boss.get("reason") or "待接入"))
        self.assist_red_status.set(str(red.get("reason") or ("需要版本映射" if gap else "尚未开启")))
        self.assist_wish_status.set(str(wish.get("reason") or "待接入"))
        if time.monotonic() >= getattr(self, "_revive_btn_pending", 0.0):
            self.assist_revive_btn.configure(
                text="关闭" if revive.get("enabled") else "快速复活"
            )

        green, green_gem, gold = self.assist.display_stats()
        self.assist_green_var.set(f"已领 {green} 个红包 · {green_gem} 绿钻")
        build = str(state.get("build") or "")
        self.assist_gold_var.set(f"BOSS 金币 {gold}" + (f"（版本 {build}）" if build else ""))

        # 未解锁祈愿时按钮置灰，和原工具一致
        if not gap:
            unlocked = wish.get("available") is not False or bool(wish.get("enabled"))
            self.assist_wish_btn.configure(state=tk.NORMAL if unlocked else tk.DISABLED)

    def _tick_assist(self) -> None:
        try:
            self._update_assist_labels()
        except Exception:  # noqa: BLE001
            pass
        # 看门狗：连接线程万一意外退出，这里自动接上，界面不会一直卡在旧状态
        if self._assist_auto_started and not self.assist.running:
            try:
                self.assist.start()
            except Exception:  # noqa: BLE001
                pass
        self.root.after(250, self._tick_assist)

    def diagnose_assist(self) -> None:
        self.append_log("正在诊断游戏辅助（检查模块表与各业务入口）…")

        def work() -> None:
            info = self.assist.diagnose()
            if not info:
                self.append_log("诊断失败：游戏页无返回，请确认已打开游戏窗口并进入游戏")
                return
            if info.get("error"):
                self.append_log(f"诊断失败：{info['error']}")
                return
            self.append_log(
                f"模块 {info.get('modules')} 个，System "
                f"{'有' if info.get('hasSystem') else '无'}，地址 {info.get('url')}"
            )
            for label, value in (info.get("services") or {}).items():
                self.append_log(f"  {label}: {value}")
            monsters = info.get("monsters") or []
            if monsters:
                parts = []
                for item in monsters[:12]:
                    text = str(item.get("id"))
                    if item.get("boss"):
                        text += "(BOSS)"
                    if item.get("dead"):
                        text += "(已死)"
                    if item.get("name"):
                        text += f"[{item['name']}]"
                    parts.append(text)
                self.append_log("  当前怪物：" + "，".join(parts))
            combat = info.get("combat")
            if isinstance(combat, dict):
                if combat.get("error"):
                    self.append_log(f"  战斗参数：{combat['error']}")
                else:
                    self.append_log(
                        "  战斗参数：视野可改 %s 项、冷却可改 %s 个技能、倍伤 %s"
                        % (
                            combat.get("visionRows", 0),
                            combat.get("cooldownRows", 0),
                            combat.get("damage", "-"),
                        )
                    )
            revive = info.get("revive")
            if isinstance(revive, dict):
                if revive.get("error"):
                    self.append_log(f"  快速复活：{revive['error']}")
                else:
                    self.append_log(
                        "  快速复活：复活接口 %s，全灭面板 %s"
                        % (
                            "可用" if revive.get("reviveMethod") else "缺失",
                            "正在显示" if revive.get("panelFound") else "未显示",
                        )
                    )
            for error in info.get("errors") or []:
                self.append_log(f"  问题：{error}")

        threading.Thread(target=work, daemon=True).start()

    def self_test_assist(self) -> None:
        """一键自测：假装 BOSS 死亡，看 N 秒后会不会自己重进地图。"""
        try:
            seconds = float(self.assist_boss_var.get().strip())
        except (ValueError, AttributeError):
            messagebox.showerror("参数错误", "等待秒数需要是数字")
            return
        if not 0 < seconds <= 600:
            messagebox.showerror("参数错误", "等待秒数需要在 0-600 之间")
            return
        self.append_log(f"自测：假装击杀 BOSS，{seconds:g} 秒后应自动重进地图…")

        def work() -> None:
            result = self.assist.self_test("boss", seconds)
            if result.get("error"):
                self.append_log(f"自测失败：{result['error']}")
            else:
                self.append_log(
                    "自测指令已生效，接下来看 BOSS 状态里的倒计时，倒数完应出现「重新进入地图」"
                )

        threading.Thread(target=work, daemon=True).start()

    def _rebuild_card_checks(self, enabled: set[str] | None = None) -> None:
        from . import gacha_store

        if not hasattr(self, "_cards_frame"):
            return
        catalog = refresh_card_catalog()
        gacha_store.CARD_CATALOG = catalog
        if enabled is None:
            enabled = {n for n, v in self.card_vars.items() if v.get()}
        for child in self._cards_frame.winfo_children():
            child.destroy()
        self.card_vars.clear()
        self.card_template_labels.clear()
        cols = 4
        for i, name in enumerate(catalog):
            cell = ttk.Frame(self._cards_frame)
            cell.grid(row=i // cols, column=i % cols, sticky=tk.W, padx=8, pady=4)
            var = tk.BooleanVar(value=name in enabled)
            self.card_vars[name] = var
            ttk.Checkbutton(cell, text=name, variable=var).pack(side=tk.LEFT)
            mark = tk.Label(cell, text="", font=("", 8))
            mark.pack(side=tk.LEFT, padx=2)
            self.card_template_labels[name] = mark

    def refresh_gacha_templates(self) -> None:
        enabled = {n for n, v in self.card_vars.items() if v.get()}
        self._rebuild_card_checks(enabled)
        available = list_available_card_templates()
        for name, label in self.card_template_labels.items():
            if name in available:
                label.configure(text="有图", foreground="#0a7a2f")
            else:
                label.configure(text="无模板", foreground="#a33")
        ui_status = list_ui_template_status()
        label_map = {
            "recruit_coin": "金币招募",
            "recruit_gem": "绿砖",
            "max": "最大",
            "refresh": "刷新",
            "abandon": "放弃",
        }
        parts = []
        for key, path in ui_status.items():
            label = label_map.get(key, key)
            parts.append(f"{label}:{'有' if path else '缺'}")
        self.ui_status_var.set(
            f"图片目录 {ui_dir()} → 英雄 "
            + "、".join(refresh_card_catalog())
            + "  |  按钮 "
            + "  ".join(parts)
        )
        self.append_log(
            f"英雄模板 {len(available)}/{len(refresh_card_catalog())}；UI "
            + "，".join(parts)
        )

    def _tick_cursor(self) -> None:
        self._drain_logs()
        self.root.after(200, self._tick_cursor)

    def append_log(self, msg: str) -> None:
        """任何线程都可以调用：只入队，由 UI 线程统一写日志框。"""
        self._log_queue.put(str(msg))

    def _drain_logs(self) -> None:
        try:
            while True:
                msg = self._log_queue.get_nowait()
                if self.log is not None:
                    self.log.insert(tk.END, msg + "\n")
                    self.log.see(tk.END)
        except queue.Empty:
            return

    def _on_preset(self, _event=None) -> None:
        name = self.preset_var.get()
        size = SIZE_PRESETS.get(name)
        if not size:
            return
        w, h = size
        self.width_var.set(str(w))
        self.height_var.set(str(h))

    def _read_game_size(self) -> tuple[int, int]:
        try:
            w = int(self.width_var.get().strip())
            h = int(self.height_var.get().strip())
        except ValueError as exc:
            raise ValueError("宽高必须是整数") from exc
        if w < 280 or h < 400:
            raise ValueError("建议宽≥280、高≥400")
        return w, h

    def open_game_window(self) -> None:
        try:
            width, height = self._read_game_size()
        except ValueError as exc:
            messagebox.showerror("尺寸错误", str(exc))
            return

        url = str(self.config.get("game_url") or DEFAULT_GAME_URL).strip() or DEFAULT_GAME_URL
        self.config["game_url"] = url
        self.config["game_width"] = width
        self.config["game_height"] = height
        self.window_var.set(GAME_WINDOW_TITLE)
        self.config["target_window_title"] = GAME_WINDOW_TITLE

        backend = str(self.config.get("game_window_backend") or "auto").strip().lower()
        if backend not in ("auto", "webview2", "edge", "chrome", "browser"):
            backend = "auto"
        launch_backend = "webview2" if backend == "auto" else backend

        if self._game_proc is not None and self._game_proc.poll() is None:
            self.append_log("已有游戏窗口进程，正在重新打开…")
            terminate_game_window(self._game_proc)

        try:
            self._game_proc = open_game_window(
                url=url, width=width, height=height,
                browser=str(self.config.get("browser_path") or "").strip() or None,
                backend=launch_backend,
                port=int(getattr(self.assist.bridge, "port", 9229)),
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打开失败", f"无法打开游戏窗口：{exc}")
            return

        save_config(self.config)
        # 顺手把辅助运行时接上：进游戏后点开关就能用，不用再点「启动辅助」
        try:
            self.assist.set_names(self._assist_names())
            self.assist.start()
        except Exception:  # noqa: BLE001
            pass
        self.append_log(
            f"已打开游戏窗 {width}x{height}，标题「{GAME_WINDOW_TITLE}」；"
            "平时可拖拽缩放；抽卡/存档运行时会恢复并锁定尺寸；"
            "已开启调试端口用于稳定点击"
        )
        if launch_backend == "webview2":
            self.append_log("正在检查调试端口；Win10 若 WebView2 不可用，会自动切换 Edge 兼容模式")
            self.root.after(1200, lambda: self._watch_game_port(self._game_proc, 0, "webview2"))
        else:
            self.root.after(1200, lambda: self._watch_game_port(self._game_proc, 0, launch_backend))

    def _watch_game_port(self, proc, tries: int = 0, backend: str = "webview2") -> None:
        """Win10 兼容：WebView2 没开 9229 时，自动切到 Edge/Chrome 窗口。"""
        if proc is None or proc is not getattr(self, "_game_proc", None):
            return
        if proc.poll() is not None:
            if backend == "webview2" and tries <= 3:
                self._open_edge_fallback(proc)
            return
        try:
            ready = bool(find_page_ws(int(getattr(self.assist.bridge, "port", 9229))))
        except Exception:  # noqa: BLE001
            ready = False
        if ready:
            if tries > 0:
                self.append_log("游戏窗口调试端口已就绪")
            return
        if tries < 15:
            self.root.after(1000, lambda: self._watch_game_port(proc, tries + 1, backend))
            return
        if backend != "webview2":
            self.append_log("兼容模式仍未连上调试端口：请检查防火墙，或关闭游戏窗重开")
            return
        self._open_edge_fallback(proc)

    def _open_edge_fallback(self, proc) -> None:
        """WebView2 不可用时，用 Edge/Chrome 窗口打开同一游戏。"""
        self.append_log("WebView2 没有开启调试端口（Win10 常见问题），自动切换 Edge 兼容模式…")
        try:
            width, height = self._read_game_size()
        except ValueError:
            width, height = 390, 844
        url = str(self.config.get("game_url") or DEFAULT_GAME_URL)
        terminate_game_window(proc)
        try:
            self._game_proc = open_game_window(
                url=url, width=width, height=height,
                browser=str(self.config.get("browser_path") or "").strip() or None,
                backend="edge",
                port=int(getattr(self.assist.bridge, "port", 9229)),
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Win10 兼容模式失败",
                "%s\n\n请安装 Microsoft Edge 或 Google Chrome 后再试。" % exc,
            )
            return
        self.append_log("已切换到 Edge 兼容模式，请在新窗口登录游戏")
        try:
            self.assist.start()
        except Exception:  # noqa: BLE001
            pass
        self.root.after(1200, lambda: self._watch_game_port(self._game_proc, 0, "edge"))

    def open_game_browser(self) -> None:
        url = str(self.config.get("game_url") or DEFAULT_GAME_URL).strip() or DEFAULT_GAME_URL
        webbrowser.open(url)
        self.append_log(f"已用系统浏览器打开: {url}")

    def _read_gacha_into_config(self) -> bool:
        try:
            slot_text = self.gacha_slot_var.get().strip().replace("存档", "")
            slot = int(slot_text or "1")
            max_draws = int(self.gacha_max_var.get().strip() or "0")
            settle_ms = int(self.gacha_settle_var.get().strip() or "2800")
            threshold = float(self.gacha_threshold_var.get().strip() or "0.82")
        except ValueError:
            messagebox.showerror("参数错误", "抽卡参数格式不对")
            return False
        enabled = [name for name, var in self.card_vars.items() if var.get()]
        gacha_cfg = normalize_gacha(
            {
                "enabled_cards": enabled,
                "slot": slot,
                "max_draws": max_draws,
                "settle_ms": settle_ms,
                "match_threshold": threshold,
                "no_hit_action": self.gacha_nohit_var.get().strip() or "正常抽卡",
                "use_green_gem": bool(self.gacha_use_gem_var.get()),
                "use_max_button": bool(self.gacha_use_max_var.get()),
                "stop_on_hit": bool(self.gacha_stop_on_hit_var.get()),
            }
        )
        try:
            gacha_cfg["cdp_turbo"] = bool(self.gacha_turbo_var.get())
            gacha_cfg["cdp_delay_ms"] = max(
                20, int(self.gacha_cdp_delay_var.get().strip() or "1200")
            )
        except (AttributeError, TypeError, ValueError):
            pass
        self.config["gacha"] = gacha_cfg
        return True

    def _read_form_into_config(self) -> bool:
        try:
            self.config["target_window_title"] = self.window_var.get().strip()
            self.config["match_threshold"] = float(self.threshold_var.get().strip())
            self.config["loop_interval_ms"] = int(self.interval_var.get().strip())
            self.config["game_width"] = int(self.width_var.get().strip())
            self.config["game_height"] = int(self.height_var.get().strip())
            try:
                self.config["gacha_cdp"] = {
                    "checked": [
                        str(hero_id)
                        for hero_id, var in self._gacha_hero_vars.items()
                        if var.get()
                    ]
                }
            except tk.TclError:
                pass
            if not self.config.get("game_url"):
                self.config["game_url"] = DEFAULT_GAME_URL
        except ValueError:
            messagebox.showerror("参数错误", "阈值、间隔或宽高格式不对")
            return False
        self._read_assist_into_config()
        return self._read_gacha_into_config()

    def save_all(self) -> None:
        if not self._read_form_into_config():
            return
        save_config(self.config)
        self.append_log("配置已保存")

    # ---- 直连抽卡（CDP，调游戏招募接口） ----

    def _gacha_cdp(self) -> GachaCdp:
        obj = getattr(self, "_gacha_cdp_obj", None)
        if obj is None:
            obj = GachaCdp()
            self._gacha_cdp_obj = obj
        return obj

    def _update_hero_count(self) -> None:
        try:
            checked = sum(1 for v in self._gacha_hero_vars.values() if v.get())
            self.gacha_hero_count.set(f"最大倍数招募（已选 {checked}）")
        except tk.TclError:
            pass

    def _set_all_heroes(self, value: bool) -> None:
        for var in getattr(self, "_gacha_hero_vars", {}).values():
            try:
                var.set(value)
            except tk.TclError:
                pass
        self._update_hero_count()

    def _fixed_checked_ids(self) -> set:
        """默认一个都不勾，由用户自己选。"""
        section = self.config.get("gacha_cdp") or {}
        checked = section.get("checked")
        ids: set = set()
        if isinstance(checked, list):
            for item in checked:
                text = str(item).strip()
                if text.isdigit():
                    ids.add(int(text))
        return ids

    def reload_gacha_heroes(self) -> None:
        rows = list(GACHA_POOL)
        for widget in self._gacha_hero_inner.winfo_children():
            widget.destroy()
        self._gacha_hero_vars.clear()
        self._gacha_hero_counter_vars.clear()
        self._hero_name_cache = {hid: name for hid, name in rows}
        checked = self._fixed_checked_ids()
        for index, (hero_id, name) in enumerate(rows):
            # 红卡（品级 6+）=红底，其余=黄底
            if GACHA_QUALITY.get(int(hero_id), 5) >= 6:
                card_bg, card_line, name_fg = "#fdecea", "#f0c4be", "#b3261e"
            else:
                card_bg, card_line, name_fg = "#fff8e1", "#efe0b4", "#8a6100"
            cell = tk.Frame(
                self._gacha_hero_inner, bg=card_bg,
                highlightbackground=card_line, highlightthickness=1,
            )
            cell.grid(row=index // 2, column=index % 2, sticky="ew", padx=3, pady=1)
            var = tk.BooleanVar(value=(hero_id in checked))
            tk.Checkbutton(
                cell, text=name, variable=var, command=self._update_hero_count,
                bg=card_bg, fg=name_fg, activebackground=card_bg,
                activeforeground=name_fg, selectcolor="#ffffff",
                bd=0, highlightthickness=0, anchor="w",
                font=("Microsoft YaHei UI", 10, "bold"),
            ).pack(side=tk.LEFT, padx=(6, 0), pady=2)
            counter = tk.StringVar(value="0/0/0")
            tk.Label(
                cell, textvariable=counter, bg=card_bg, fg=name_fg,
            ).pack(side=tk.RIGHT, padx=(0, 8))
            self._gacha_hero_vars[hero_id] = var
            self._gacha_hero_counter_vars[hero_id] = counter
        self._update_hero_count()

    def list_gacha_heroes(self) -> None:
        gacha_cdp = self._gacha_cdp()
        state = gacha_cdp.ensure()
        if str(state).startswith("fail"):
            self.append_log("读取英雄列表失败：" + str(state)[5:])
            return
        heroes = gacha_cdp.heroes()
        if not heroes:
            self.append_log("没读到英雄列表（游戏可能还没进主界面）")
            return
        rows = [f"{h.get('name')}={h.get('id')}（品级{h.get('quality')}）" for h in heroes]
        self.append_log("可选英雄（红/橙，名字=编号）：" + "，".join(rows))

    def start_gacha_cdp(self) -> None:
        self._frag_base = {}
        self._gold_base = None
        targets: List[str] = []
        raw = (self.gacha_cdp_targets_var.get() or "").replace("，", ",").replace("、", ",")
        for part in raw.split(","):
            part = part.strip()
            if part:
                targets.append(part)
        # 勾选的英雄也加进目标
        for hero_id, var in getattr(self, "_gacha_hero_vars", {}).items():
            try:
                if var.get() and hero_id > 0:
                    targets.append(str(hero_id))
            except tk.TclError:
                pass
        keep_quality = 6 if self.gacha_cdp_red_var.get() else 0
        turbo = bool(getattr(self, "gacha_turbo_var", None) and self.gacha_turbo_var.get())
        try:
            max_draws = int(self.gacha_cdp_max_var.get().strip() or "0")
            delay_ms = int(self.gacha_cdp_delay_var.get().strip() or ("50" if turbo else "1200"))
            delay_ms = max(20, delay_ms) if turbo else max(300, delay_ms)
            max_green = int(self.gacha_cdp_green_var.get().strip() or "0")
        except ValueError:
            messagebox.showerror("参数错误", "抽卡参数格式不对")
            return
        if not targets and not keep_quality:
            messagebox.showinfo("提示", "请先勾选要保留的目标英雄")
            return

        # 勾选状态落盘，下次打开还在
        try:
            self._read_form_into_config()
            try:
                self.config["gacha"]["cdp_always_abandon"] = bool(self.gacha_cdp_abandon_var.get())
                self.config["gacha"]["cdp_turbo"] = bool(turbo)
                self.config["gacha"]["cdp_delay_ms"] = int(delay_ms)
            except Exception:  # noqa: BLE001
                pass
            save_config(self.config)
        except Exception:  # noqa: BLE001
            pass

        gacha_cdp = self._gacha_cdp()
        state = gacha_cdp.ensure()
        if str(state).startswith("fail"):
            self.gacha_cdp_status.set("直连失败：" + str(state)[5:])
            self.append_log("直连抽卡失败：" + str(state)[5:])
            return
        result = gacha_cdp.start(
            targets=targets,
            keep_quality=keep_quality,
            max_draws=max_draws,
            delay_ms=delay_ms,
            refresh=bool(self.gacha_cdp_refresh_var.get()),
            max_green=max_green,
            max_bet=bool(self.gacha_cdp_maxbet_var.get()),
            always_abandon=bool(self.gacha_cdp_abandon_var.get()),
            turbo=turbo,
        )
        if not result.get("ok"):
            self.gacha_cdp_status.set("开始失败：" + str(result.get("message")))
            self.append_log("直连抽卡开始失败：" + str(result.get("message")))
            return
        self._gacha_running = True
        self._gacha_start_ts = time.monotonic()
        try:
            self.gacha_cdp_btn.configure(text="停止", bg="#c0392b", activebackground="#a93226")
        except tk.TclError:
            pass
        for var, value in (
            (self.gacha_stat_draws_var, "0"),
            (self.gacha_stat_red_var, "0"),
            (self.gacha_stat_cost_var, "0"),
            (self.gacha_stat_green_var, "0"),
            (self.gacha_stat_time_var, "0:00"),
        ):
            try:
                var.set(value)
            except tk.TclError:
                pass
        self.gacha_cdp_status.set(
            "已开始（极速 %dms/次），读取招募面板…" % delay_ms
            if turbo else "已开始（%dms/次），读取招募面板…" % delay_ms
        )
        self.append_log("已开始直连抽卡")
        self.root.after(600, self._tick_gacha_cdp)

    def stop_gacha_cdp(self) -> None:
        try:
            self._gacha_cdp().stop()
        except Exception:  # noqa: BLE001
            pass
        self._gacha_running = False
        try:
            self.gacha_cdp_btn.configure(text="开始招募", bg="#2f9e5f", activebackground="#26864f")
        except tk.TclError:
            pass
        try:
            self.gacha_cdp_status.set("已停止")
        except tk.TclError:
            pass

    def _sync_gacha_turbo_btn(self) -> None:
        try:
            on = bool(self.gacha_turbo_var.get())
            if on:
                self.gacha_turbo_btn.configure(
                    text="极速：开", bg="#e67e22", fg="#ffffff",
                    activebackground="#cf711f", activeforeground="#ffffff",
                )
            else:
                self.gacha_turbo_btn.configure(
                    text="极速模式", bg="#fff3e0", fg="#b45309",
                    activebackground="#ffe8c7", activeforeground="#b45309",
                )
        except (tk.TclError, AttributeError):
            pass

    def toggle_gacha_turbo(self) -> None:
        """开关极速模式：约 50ms 一步，下次「开始招募」生效。"""
        on = not bool(self.gacha_turbo_var.get())
        self.gacha_turbo_var.set(on)
        self._sync_gacha_turbo_btn()
        try:
            delay = int(self.gacha_cdp_delay_var.get().strip() or "0")
        except (TypeError, ValueError):
            delay = 0
        if on and (delay <= 0 or delay > 300):
            self.gacha_cdp_delay_var.set("50")
            delay = 50
        try:
            self.config.setdefault("gacha", {})["cdp_turbo"] = on
            self.config.setdefault("gacha", {})["cdp_delay_ms"] = max(20, delay or 50)
            save_config(self.config)
        except Exception:  # noqa: BLE001
            pass
        try:
            if on:
                self.gacha_cdp_status.set(
                    "极速模式已开：约 %dms/次，下次开始生效（注意风控）" % (delay or 50)
                )
            else:
                self.gacha_cdp_status.set("极速模式已关闭")
        except tk.TclError:
            pass

    def toggle_gacha_cdp(self) -> None:
        """一个按钮：没跑就开，跑着就停。"""
        if getattr(self, "_gacha_running", False):
            self.stop_gacha_cdp()
        else:
            self.start_gacha_cdp()

    def clear_gacha_stats(self) -> None:
        """清空统计数字（引擎自己的计数在下次开始时才重置）。"""
        if getattr(self, "_gacha_running", False):
            try:
                self.gacha_cdp_status.set("先点「停止」再清空")
            except tk.TclError:
                pass
            return
        self._frag_base = {}
        self._gold_base = None
        self._gacha_start_ts = None
        for var in (
            self.gacha_stat_draws_var, self.gacha_stat_red_var,
            self.gacha_stat_cost_var, self.gacha_stat_green_var,
        ):
            try:
                var.set("0")
            except tk.TclError:
                pass
        try:
            self.gacha_stat_time_var.set("0:00")
            self.gacha_cdp_status.set("统计已清空")
        except tk.TclError:
            pass
        for var in getattr(self, "_gacha_hero_counter_vars", {}).values():
            try:
                var.set("0/0/0")
            except tk.TclError:
                pass

    def _tick_gacha_cdp(self) -> None:
        try:
            snap = self._gacha_cdp().snapshot()
        except Exception:  # noqa: BLE001
            snap = None
        if snap:
            stats = snap.get("stats") or {}
            text = ""
            candidates = snap.get("candidates") or []
            if candidates:
                cache = getattr(self, "_hero_name_cache", {})
                names = []
                for item in candidates:
                    hero_id = int(item.get("heroId") or 0)
                    name = cache.get(hero_id) or str(hero_id)
                    names.append(name + ("(已抽)" if item.get("selected") else ""))
                try:
                    self.gacha_pool_var.set("当前候选：" + "、".join(names))
                except tk.TclError:
                    pass
            else:
                try:
                    self.gacha_pool_var.set("当前候选：未读取（打开招募面板后开始抽卡会自动读取）")
                except tk.TclError:
                    pass

            counters = snap.get("counters") or {}
            frag_map = snap.get("fragments") or {}
            # 第一次拿到碎片数据时记基准，之后算「本次抽中多少」
            if isinstance(frag_map, dict) and frag_map:
                if not self._frag_base:
                    self._frag_base = {str(k): int(v) for k, v in frag_map.items()}
            for hero_id, var in getattr(self, "_gacha_hero_counter_vars", {}).items():
                info = counters.get(str(hero_id)) or counters.get(hero_id) or {}
                appear = int(info.get("appear") or 0)
                hit = int(info.get("hit") or 0)
                gained = None
                if isinstance(frag_map, dict):
                    cur = frag_map.get(str(hero_id), frag_map.get(hero_id))
                    if cur is not None:
                        base = self._frag_base.get(str(hero_id))
                        gained = max(0, int(cur) - int(base or 0))
                frag_txt = "0" if gained is None else str(gained)
                try:
                    var.set(f"{appear}/{hit}/{frag_txt}")
                except tk.TclError:
                    pass

            records = snap.get("records") or []
            gold = snap.get("gold")
            if isinstance(gold, (int, float)) and self._gold_base is None:
                self._gold_base = int(gold)

            # ---- 统计卡 / 按钮状态 ----
            running = bool(snap.get("running"))
            self._gacha_running = running
            try:
                if running:
                    self.gacha_cdp_btn.configure(
                        text="停止", bg="#c0392b", activebackground="#a93226"
                    )
                else:
                    self.gacha_cdp_btn.configure(
                        text="开始招募", bg="#2f9e5f", activebackground="#26864f"
                    )
            except tk.TclError:
                pass
            try:
                all_records = snap.get("records") or []
                red_appear = stats.get("redAppear")
                if red_appear is None:
                    # 旧运行时没有这个字段，用抽到的红代替
                    reds = sum(
                        int(r.get("count") or 0)
                        for r in all_records
                        if int(r.get("quality") or 0) >= 6
                    )
                else:
                    reds = int(red_appear or 0)
                spent = 0
                if isinstance(gold, (int, float)) and self._gold_base is not None:
                    spent = max(0, self._gold_base - int(gold))
                self.gacha_stat_draws_var.set(str(int(stats.get("draws") or 0)))
                self.gacha_stat_red_var.set(str(reds))
                self.gacha_stat_cost_var.set(f"{spent:,}")
                self.gacha_stat_green_var.set(str(int(stats.get("greenSpent") or 0)))
                if running and self._gacha_start_ts:
                    secs = max(0, int(time.monotonic() - self._gacha_start_ts))
                    self.gacha_stat_time_var.set(f"{secs // 60}:{secs % 60:02d}")
            except tk.TclError:
                pass

            for part in (snap.get("action"), snap.get("stopReason")):
                part = str(part or "").strip()
                if not part:
                    continue
                if part in text:
                    continue
                text = (text + " | " + part) if text else part
            if not snap.get("ready") and snap.get("stateReason"):
                part = str(snap.get("stateReason")).strip()
                if part:
                    text = (text + " | " + part) if text else part
            if not text:
                text = "待开始"
            try:
                self.gacha_cdp_status.set(text)
            except tk.TclError:
                return
            if not snap.get("running"):
                self._gacha_running = False
                return
        try:
            self.root.after(600, self._tick_gacha_cdp)
        except tk.TclError:
            return

    def _lock_game_window(self, reason: str) -> None:
        title = (self.window_var.get() or GAME_WINDOW_TITLE).strip()
        try:
            width, height = self._read_game_size()
        except ValueError:
            width = int(self.config.get("game_width", 390) or 390)
            height = int(self.config.get("game_height", 844) or 844)
        self.config["game_width"] = width
        self.config["game_height"] = height
        if lock_game_window(title, width=width, height=height):
            self.append_log(
                f"已恢复并锁定游戏窗客户区 {width}x{height}（{reason}中），结束后可再拖拽缩放"
            )
        else:
            self.append_log(f"未找到游戏窗，无法恢复/锁定尺寸（{reason}）")
        try:
            from .cdp_click import cdp_available

            if cdp_available():
                self.append_log("后台点击: CDP已就绪（不抢鼠标/前台，可拖走窗口）")
            else:
                self.append_log(
                    "后台点击: CDP未就绪，将用消息点击兜底。"
                    "建议关闭游戏窗后重新「打开游戏窗口」以启用更稳的CDP"
                )
        except Exception:  # noqa: BLE001
            pass

    def _unlock_game_window_if_idle(self) -> None:
        if unlock_game_window():
            self.append_log("已解锁游戏窗，可拖拽移动/缩放")

    def stop_run(self) -> None:
        try:
            self.stop_gacha_cdp()
        except Exception:  # noqa: BLE001
            pass
        self._unlock_game_window_if_idle()

    def bind_hotkeys(self) -> None:
        if self._hotkeys_bound:
            return

        def on_stop():
            self.root.after(0, self.stop_run)

        if keyboard is None:
            self._hotkeys_bound = True
            self.append_log("当前系统不支持全局热键，用界面按钮操作即可")
            return
        keyboard.add_hotkey("f9", on_stop)
        self._hotkeys_bound = True
        self.append_log("热键: F9 停止（含抽卡）")
        self.append_log("抽卡结果: saves/gacha_result.json")

    def on_close(self) -> None:
        self._disable_click_teleport()
        try:
            self.assist_auto_chest_var.set(False)
            self._auto_chest_stop.set()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._cmd_recorder.recording:
                self._cmd_recorder.stop()
            self.instruction_runner.stop()
            if getattr(self, "_plank_running", False):
                self._stop_plank(quiet=True)
            if getattr(self, "_farm_running", False):
                try:
                    self._farm_eval(
                        "(window.__zzAssist&&window.__zzAssist.farmAutoStop"
                        "&&window.__zzAssist.farmAutoStop(),'ok')"
                    )
                except Exception:  # noqa: BLE001
                    pass
                self._farm_running = False
            if not getattr(self, "_cmd_preset_loaded", False):
                self._save_current_slot_into_memory()
            self._persist_slots()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.stop_gacha_cdp()
            self._gacha_cdp().close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.assist.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._game_proc is not None and self._game_proc.poll() is None:
            try:
                self._game_proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        try:
            if self._read_form_into_config():
                save_config(self.config)
        except Exception:  # noqa: BLE001
            pass
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:  # noqa: BLE001
            pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    AppUI().run()
