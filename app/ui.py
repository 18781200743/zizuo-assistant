"""主界面：1-10 存档 + 脚本代码 + 抽卡。"""

from __future__ import annotations

import os
import queue
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Dict, List, Optional

try:  # Windows 全局热键；macOS 上没有这个库，界面按钮照样能用
    import keyboard
except Exception:  # noqa: BLE001
    keyboard = None

from .capture import grab_cursor_patch
from .config_store import (
    load_config,
    new_task,
    normalize_assist,
    save_config,
    templates_dir,
)
from .gacha import GachaRunner
from .gacha_store import (
    CARD_CATALOG,
    cards_dir,
    list_available_card_templates,
    list_ui_template_status,
    normalize_gacha,
    refresh_card_catalog,
    ui_dir,
)
from .game_window import GAME_WINDOW_TITLE, SIZE_PRESETS, open_game_window
from .game_assist import GameAssist, load_mapping
from .instruction_library import (
    CATEGORY_ALL,
    clear_instruction_override,
    delete_user_instruction,
    format_content,
    library_categories,
    library_instructions,
    load_library,
    save_instruction_override,
    save_user_instruction,
    script_title,
)
from .instruction_runner import InstructionRunner
from .build_info import dev_mode
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
from .recorder import ClickRecorder
from .runner import TaskRunner
from .script_codec import script_to_lines, script_to_steps, steps_to_script, slot_title
from .slots_store import (
    SLOT_COUNT,
    load_slots,
    save_slots,
    set_slot,
    slot_label,
)
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


# 日常任务编号 -> 名字（游戏里的任务表编号，用于显示和判断能不能自动做）
DAILY_TASK_NAMES = {
    1043: "合成一次宝石",
    1007: "击杀BOSS熔岩领主",
    1015: "开启5个地图宝箱",
    1017: "分享游戏1次",
    1032: "击杀BOSS黑石矮人王",
    1033: "击杀BOSS炎魔大帝",
    1034: "挑战云顶试炼塔1次",
    1009: "招募英雄3次",
    1010: "升级英雄1次",
    1012: "挑战荣耀竞技场",
    1014: "采集资源50次",
    1018: "观看视频广告",
    1035: "击杀1000只怪物",
    1047: "登录游戏",
}

# 这些任务不做（要花钱/要 SDK/用户不要）
DAILY_TASK_SKIP = ("宝箱", "广告", "招募", "合成", "竞技", "试炼")


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


class AppUI:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("百炼英雄 · 助手")
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

        self.config: Dict[str, Any] = load_config()
        self.slots: List[List[str]] = load_slots()
        # 后台线程只往队列里丢日志，由 UI 线程统一写进日志框（跨线程操作 Tk 会卡死/丢日志）
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self.runner = TaskRunner(self.append_log, on_finished=self._on_run_finished)
        self.gacha_runner = GachaRunner(
            self.append_log,
            on_progress=self._on_gacha_progress,
            on_finished=self._on_run_finished,
        )
        self.recorder = ClickRecorder(self.append_log)
        assist_cfg = normalize_assist(self.config.get("assist"))
        self.assist = GameAssist(
            self.append_log,
            port=int(assist_cfg.get("cdp_port") or 9229),
        )
        self.assist.names = {
            "bossKeywords": assist_cfg.get("boss_keywords") or [],
            "bossIds": assist_cfg.get("boss_ids") or [],
        }
        for key, value in (assist_cfg.get("names") or {}).items():
            self.assist.names[key] = value
        self._hotkeys_bound = False
        self._game_proc = None
        self._loading_slot = False
        self._assist_auto_started = False
        self.instruction_runner = InstructionRunner(
            lambda: getattr(self.assist, "bridge", None),
            log=self.append_log,
            on_step=self._on_instruction_step,
            on_finished=self._on_instruction_finished,
        )
        self._cmd_events: "queue.Queue[tuple]" = queue.Queue()
        self.cmd_library: Dict[str, Any] = load_library()
        self.cmd_category: int = CATEGORY_ALL
        self.cmd_rows: List[Dict[str, Any]] = []
        # 开发模式：源码运行显示开发功能；打包成 exe 默认隐藏（--dev / ZIZUO_DEV=1 可强制打开）
        self.dev_mode = dev_mode()
        self.record_status = tk.StringVar(value="录制: 未开始（游戏窗口内相对坐标）")
        self.slot_hint = tk.StringVar(value="")
        self.gacha_status = tk.StringVar(value="抽卡: 未开始")
        self.card_vars: Dict[str, tk.BooleanVar] = {}
        self._frag_base: Dict[str, int] = {}
        self._gold_base = None

        self._init_dark_styles()
        self._build()
        try:
            self.root.after(150, self._pump_cmd_events)
        except tk.TclError:
            pass
        self.refresh_slot_list(select=int(self.config.get("selected_slot", 1)) - 1)
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
            text="百炼英雄 · 助手",
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
            text="运行指令",
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

        self._page_slots = ttk.Frame(self._content, padding=4)
        self._page_gacha = ttk.Frame(self._content, padding=4)
        self._page_assist = ttk.Frame(self._content, padding=4)
        self._page_commands = ttk.Frame(self._content, padding=4)
        self._page_home = tk.Frame(self._content, bg="#f3f5f9")
        self._scroll_canvases: Dict[str, tk.Canvas] = {}
        # 存档脚本功能暂时隐藏（后续重写），页面仍构建但默认不显示
        self._build_slots_tab(self._page_slots)
        self._build_gacha_tab(self._make_scroll(self._page_gacha, "gacha"))
        self._page_assist.configure(style="Dark.TFrame")
        self._build_assist_tab(
            self._make_scroll(self._page_assist, "assist", bg=self.DARK_PAGE)
        )
        # 构建完后按当前主题（默认白天）套用配色
        self.root.after(60, self._apply_assist_theme)
        self._build_commands_tab(self._page_commands)
        self._build_home_tab(self._page_home)
        self.root.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self._switch_mode("home")

        # 日志框已去掉（用户要求）
        self.log = None

        self.root.after(200, self._tick_cursor)

    def _switch_mode(self, mode: str) -> None:
        self.mode_var.set(mode)
        pages = {
            "home": self._page_home,
            "gacha": self._page_gacha,
            "assist": self._page_assist,
            "commands": self._page_commands,
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

    def _build_slots_tab(self, parent: ttk.Frame) -> None:
        mid = ttk.Panedwindow(parent, orient=tk.HORIZONTAL)
        mid.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(mid, padding=4)
        center = ttk.Frame(mid, padding=4)
        right = ttk.Frame(mid, padding=4)
        mid.add(left, weight=2)
        mid.add(center, weight=4)
        mid.add(right, weight=3)

        ttk.Label(left, text="存档 1-10（选中后运行）").pack(anchor=tk.W)
        self.slot_list = tk.Listbox(left, height=16, exportselection=False)
        self.slot_list.pack(fill=tk.BOTH, expand=True, pady=4)
        self.slot_list.bind("<<ListboxSelect>>", self._on_slot_select)

        slot_btns = ttk.Frame(left)
        slot_btns.pack(fill=tk.X)
        ttk.Button(slot_btns, text="保存到选中存档", command=self.save_editor_to_slot).pack(
            fill=tk.X, pady=2
        )
        ttk.Button(slot_btns, text="清空选中存档", command=self.clear_selected_slot).pack(
            fill=tk.X, pady=2
        )
        ttk.Label(left, textvariable=self.slot_hint, wraplength=220).pack(
            anchor=tk.W, pady=6
        )

        ttk.Label(center, text="脚本代码（录制后复制到任意存档）").pack(anchor=tk.W)
        editor_wrap = ttk.Frame(center)
        editor_wrap.pack(fill=tk.BOTH, expand=True, pady=4)
        self.script_text = tk.Text(editor_wrap, wrap=tk.NONE, undo=True)
        yscroll = ttk.Scrollbar(editor_wrap, orient=tk.VERTICAL, command=self.script_text.yview)
        self.script_text.configure(yscrollcommand=yscroll.set)
        self.script_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        code_btns = ttk.Frame(center)
        code_btns.pack(fill=tk.X)
        ttk.Button(code_btns, text="复制脚本", command=self.copy_script).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(code_btns, text="粘贴到编辑器", command=self.paste_script).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(code_btns, text="写入存档…", command=self.write_to_slot_dialog).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(code_btns, text="插入找图(传送)", command=self.insert_find_teleport).pack(
            side=tk.LEFT, padx=2
        )

        form = ttk.LabelFrame(right, text="操作", padding=8)
        form.pack(fill=tk.X)
        self.pos_var = tk.StringVar(value="鼠标: -")
        ttk.Label(form, textvariable=self.pos_var).pack(anchor=tk.W)
        ttk.Label(form, textvariable=self.record_status).pack(anchor=tk.W, pady=2)
        ttk.Label(form, text="1. 打开游戏窗口").pack(anchor=tk.W, pady=1)
        ttk.Label(form, text="2. 鼠标移到「传送」上 → 截取模板").pack(anchor=tk.W, pady=1)
        ttk.Label(form, text="3. F6 录制点击；可用 找图点击(传送)").pack(anchor=tk.W, pady=1)
        ttk.Label(form, text="4. 写入存档 → F8 单次运行（默认不循环）").pack(
            anchor=tk.W, pady=1
        )

        self.loop_var = tk.BooleanVar(value=bool(self.config.get("loop_playback", False)))
        ttk.Checkbutton(form, text="循环运行（会持续占鼠标，慎用）", variable=self.loop_var).pack(
            anchor=tk.W, pady=4
        )

        run_box = ttk.Frame(right)
        run_box.pack(fill=tk.X, pady=4)
        ttk.Button(run_box, text="录制(F6)", command=self.toggle_record).pack(side=tk.LEFT, padx=2)
        ttk.Button(run_box, text="运行选中(F8)", command=self.start_run).pack(side=tk.LEFT, padx=2)
        ttk.Button(run_box, text="停止(F9)", command=self.stop_run).pack(side=tk.LEFT, padx=2)

        run_box2 = ttk.Frame(right)
        run_box2.pack(fill=tk.X, pady=4)
        ttk.Button(run_box2, text="截取模板", command=self.capture_template).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(run_box2, text="保存配置", command=self.save_all).pack(side=tk.LEFT, padx=2)

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

    def _home_bg(self, w: int, h: int):
        """整页背景（原画），按尺寸缓存。"""
        key = (int(w), int(h))
        if getattr(self, "_home_bg_key", None) == key:
            return getattr(self, "_home_bg_photo", None)
        photo = None
        for name in ("home_bg.jpg", "home_bg.png"):
            try:
                from PIL import Image, ImageTk

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
        """辅助工具六边形按钮：双描边 + 玻璃填充 + 底部光带 + 两侧细线。"""
        cache = getattr(self, "_hex_cache", {})
        key = (int(hexw), int(hexh))
        if key in cache:
            return cache[key]
        result = None
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageTk

            ss = 3
            margin = 40
            W = int(hexw + margin * 2)
            H = int(hexh + margin * 2)
            img = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
            mx, my = margin * ss, margin * ss
            hw, hh = hexw * ss, hexh * ss
            cy = my + hh / 2
            cut = hh * 0.58
            outer = [
                (mx, cy),
                (mx + cut, my),
                (mx + hw - cut, my),
                (mx + hw, cy),
                (mx + hw - cut, my + hh),
                (mx + cut, my + hh),
            ]
            inner = self._offset_polygon(outer, 7 * ss)

            # 外发光
            glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(glow).line(
                outer + [outer[0]], fill=(80, 230, 255, 230), width=6 * ss, joint="curve"
            )
            glow = glow.filter(ImageFilter.GaussianBlur(8 * ss))
            glow = Image.alpha_composite(glow, glow)
            img = Image.alpha_composite(img, glow)

            # 玻璃填充 + 上方柔光
            fill = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(fill).polygon(outer, fill=(9, 44, 54, 215))
            light = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(light).ellipse(
                [mx + hw * 0.05, my - hh * 0.35, mx + hw * 0.75, my + hh * 0.9],
                fill=(120, 235, 245, 60),
            )
            light = light.filter(ImageFilter.GaussianBlur(9 * ss))
            mask = Image.new("L", img.size, 0)
            ImageDraw.Draw(mask).polygon(outer, fill=255)
            light.putalpha(
                Image.composite(light.getchannel("A"), Image.new("L", img.size, 0), mask)
            )
            img = Image.alpha_composite(img, fill)
            img = Image.alpha_composite(img, light)

            # 双描边
            d = ImageDraw.Draw(img)
            d.line(outer + [outer[0]], fill=(150, 245, 255, 255), width=2 * ss, joint="curve")
            d.line(inner + [inner[0]], fill=(120, 225, 245, 175), width=1 * ss, joint="curve")

            # 底部光带
            streak = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(streak).ellipse(
                [mx + hw * 0.10, my + hh - 10 * ss, mx + hw * 0.90, my + hh + 14 * ss],
                fill=(140, 248, 255, 235),
            )
            streak = streak.filter(ImageFilter.GaussianBlur(5 * ss))
            img = Image.alpha_composite(img, streak)

            # 两侧细线
            lines = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ld = ImageDraw.Draw(lines)
            ld.line([(0, cy), (mx - 2 * ss, cy)], fill=(120, 230, 250, 130), width=max(1, ss))
            ld.line(
                [(mx + hw + 2 * ss, cy), (W * ss, cy)],
                fill=(120, 230, 250, 130), width=max(1, ss),
            )
            img = Image.alpha_composite(img, lines)

            result = (ImageTk.PhotoImage(img.resize((W, H), Image.LANCZOS)), margin, margin)
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
            sx(52), sy(243), anchor="w", text="百炼英雄 · 助手",
            fill="#ffffff", font=(ui_font, 19, "bold"),
        )
        c.create_text(
            sx(52), sy(332), anchor="w", text="百炼英雄 · 电脑版",
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

    def _show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            "百炼助手\n\n"
            "游戏辅助工具（安卓 / 电脑同一套运行时）\n"
            "功能：红包、复活、地图刷新、闪现、取坐标、日常任务\n\n"
            "说明：使用脚本，封号自己负责。",
        )

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
            ("日常任务", "daily", "teal"),
            ("战斗参数", "combat", "red"),
            ("抽卡", "gacha", "teal"),
            ("运行指令", "commands", "teal"),
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

        ttk.Label(free, text="祈愿领取").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.assist_wish_btn = ttk.Button(free, text="开启", width=10, command=lambda: self.toggle_assist("wish"))
        self.assist_wish_btn.grid(row=1, column=3, padx=(6, 4), pady=(6, 0))
        self.assist_wish_status = tk.StringVar(value="未检查")
        ttk.Label(free, textvariable=self.assist_wish_status, foreground="#9fb0c9").grid(row=1, column=4, sticky=tk.W, pady=(6, 0))

        # ---------- 快速复活 ----------
        revive = ttk.LabelFrame(parent, text="快速复活", padding=6)
        revive.columnconfigure(4, weight=1)
        ttk.Label(revive, text="快速复活").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(revive, text="免费·原地", foreground="#4ade80").grid(
            row=0, column=1, columnspan=2, sticky=tk.W, padx=8
        )
        self._revive_click_job = None
        self.assist_revive_btn = ttk.Button(
            revive, text="快速复活", width=14, command=self.revive_click, style="Accent.TButton"
        )
        self.assist_revive_btn.grid(row=0, column=3, padx=(6, 4))
        self.assist_revive_status = tk.StringVar(value="尚未开启（点1次=复活1次；点2次=一直开）")
        ttk.Label(
            revive, textvariable=self.assist_revive_status, foreground="#9fb0c9", wraplength=330
        ).grid(row=1, column=0, columnspan=5, sticky=tk.W, pady=(4, 0))
        ttk.Button(revive, text="清除卡图", width=9, command=self.clear_stuck).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=(8, 0)
        )
        ttk.Label(
            revive, text="复活/传送没反应时点一下", foreground="#9fb0c9"
        ).grid(row=2, column=2, columnspan=3, sticky=tk.W, padx=(6, 0), pady=(8, 0))

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
        self.assist_blink_status = tk.StringVar(value="点「刷新怪物」，选中一只再点「过去」")
        ttk.Label(
            blink, textvariable=self.assist_blink_status, foreground="#9fb0c9", wraplength=330,
            justify=tk.LEFT,
        ).grid(row=1, column=0, columnspan=6, sticky=tk.W, pady=(4, 0))

        list_frame = ttk.Frame(blink)
        list_frame.grid(row=2, column=0, columnspan=6, sticky=tk.EW, pady=(4, 0))
        self.assist_monster_list = tk.Listbox(list_frame, height=7, activestyle="none")
        list_sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.assist_monster_list.yview)
        self.assist_monster_list.configure(yscrollcommand=list_sb.set)
        self.assist_monster_list.pack(side=tk.LEFT, fill=tk.X, expand=True)
        list_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.assist_monster_list.bind("<Double-Button-1>", lambda _e: self.blink_assist())
        self._monster_rows: List[Dict[str, Any]] = []
        self._picked_world = None
        self.assist_blink_var = tk.StringVar(value=str(assist.get("blink_target", "")))

        # ---------- 取坐标 ----------
        coord = ttk.LabelFrame(parent, text="取坐标", padding=6)
        ttk.Button(coord, text="取坐标", width=8, command=self.pick_coord).pack(side=tk.LEFT)
        ttk.Button(coord, text="飞过去", width=8, command=self.fly_to_picked).pack(side=tk.LEFT, padx=(6, 0))
        self.assist_coord_status = tk.StringVar(
            value="点「取坐标」，再到游戏画面上点一下；取到后点「飞过去」"
        )
        ttk.Label(
            coord, textvariable=self.assist_coord_status, foreground="#9fb0c9", wraplength=280
        ).pack(side=tk.LEFT, padx=6)

        # ---------- 日常任务 ----------
        daily = ttk.LabelFrame(parent, text="日常任务", padding=6)
        ttk.Button(daily, text="一键领日常", width=12, command=self.claim_daily).pack(side=tk.LEFT)
        ttk.Button(daily, text="自动做日常", width=12, command=self.auto_daily).pack(side=tk.LEFT, padx=4)
        self.assist_daily_status = tk.StringVar(value="自动领日常")
        ttk.Label(
            daily, textvariable=self.assist_daily_status, foreground="#9fb0c9", wraplength=200
        ).pack(side=tk.LEFT, padx=6)

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

        ttk.Label(combat, text="视野").grid(row=3, column=0, sticky=tk.W, pady=(6, 0))
        self.assist_vision_var = tk.StringVar(value=_vision_text(assist.get("vision_mode", -1)))
        ttk.Combobox(combat, textvariable=self.assist_vision_var, values=VISION_CHOICES, width=18,
                     state="readonly").grid(row=3, column=1, columnspan=2, sticky=tk.W, padx=8, pady=(6, 0))
        self.assist_vision_btn = ttk.Button(combat, text="应用", width=6, command=lambda: self.toggle_combat("vision"))
        self.assist_vision_btn.grid(row=3, column=3, padx=(6, 4), pady=(6, 0))
        self.assist_vision_status = tk.StringVar(value="尚未开启")
        ttk.Label(combat, textvariable=self.assist_vision_status, foreground="#9fb0c9", wraplength=320).grid(row=3, column=4, sticky=tk.W, pady=(6, 0))

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

    # ---------------------------------------------------------------- 运行指令

        self._tool_sections = {
            "map": refresh,
            "red": free,
            "revive": revive,
            "blink": blink,
            "daily": daily,
            "combat": combat,
        }

    def _show_tool(self, key: str) -> None:
        """点分类：只显示这一个功能；再点一次收起。"""
        if key in ("gacha", "commands"):
            self._switch_mode(key)
            return
        sections = getattr(self, "_tool_sections", {}) or {}
        for widget in sections.values():
            try:
                widget.pack_forget()
            except Exception:  # noqa: BLE001
                pass
        if key == "all":
            for widget in sections.values():
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

    def _build_commands_tab(self, parent: ttk.Frame) -> None:
        nav = ttk.Frame(parent)
        nav.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(nav, text="‹ 返回首页", command=lambda: self._switch_mode("home")).pack(side=tk.LEFT)
        ttk.Button(nav, text="辅助工具", command=lambda: self._switch_mode("assist")).pack(side=tk.RIGHT)

        c = self.colors
        bar = tk.Frame(parent, bg=c["page"])
        bar.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            bar, text="运行指令", bg=c["page"], fg=c["text"], font=("Microsoft YaHei UI", 11, "bold")
        ).pack(side=tk.LEFT)
        self.cmd_status = tk.StringVar(value="选一条指令，点「运行」")
        tk.Label(bar, textvariable=self.cmd_status, bg=c["page"], fg="#1a4d8f").pack(side=tk.LEFT, padx=12)
        self.cmd_run_btn = ttk.Button(
            bar, text="运行", width=10, style="Accent.TButton", command=self.toggle_instruction
        )
        self.cmd_run_btn.pack(side=tk.RIGHT, padx=6)

        cats = tk.Frame(parent, bg=c["page"])
        cats.pack(fill=tk.X, pady=(0, 8))
        self._cmd_cat_buttons: Dict[int, tk.Button] = {}
        for cat in library_categories(self.cmd_library):
            btn = tk.Button(
                cats,
                text=cat["name"],
                relief=tk.FLAT,
                bd=0,
                padx=8,
                pady=5,
                font=("Microsoft YaHei UI", 9),
                command=lambda cid=int(cat["id"]): self._select_cmd_category(cid),
            )
            btn.pack(side=tk.LEFT, padx=(0, 3))
            self._cmd_cat_buttons[int(cat["id"])] = btn
        search_row = tk.Frame(parent, bg=c["page"])
        search_row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(search_row, text="搜索", bg=c["page"], fg=c["sub"]).pack(side=tk.LEFT)
        self.cmd_search_var = tk.StringVar()
        search = ttk.Entry(search_row, textvariable=self.cmd_search_var)
        search.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        search.bind("<Return>", lambda _e: self.refresh_command_list())
        ttk.Button(search_row, text="搜索", width=6, command=self.refresh_command_list).pack(side=tk.LEFT)

        ttk.Label(
            parent,
            text="支持：等待/点击/滚轮/移动/传送/回城/采集/开宝箱；内置指令可编辑、可还原。",
            foreground="#6b7280",
            wraplength=440,
            justify=tk.LEFT,
        ).pack(side=tk.BOTTOM, anchor=tk.W, pady=(6, 0))

        mid = ttk.Panedwindow(parent, orient=tk.VERTICAL)
        mid.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(mid, padding=4)
        right = ttk.Frame(mid, padding=4)
        mid.add(left, weight=1)
        mid.add(right, weight=3)

        self.cmd_list_hint = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.cmd_list_hint).pack(anchor=tk.W)
        list_frame = ttk.Frame(left)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        self.cmd_list = tk.Listbox(list_frame, activestyle="none", exportselection=False, height=4)
        cmd_sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.cmd_list.yview)
        self.cmd_list.configure(yscrollcommand=cmd_sb.set)
        self.cmd_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cmd_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.cmd_list.bind("<<ListboxSelect>>", lambda _e: self._show_command_detail())
        self.cmd_list.bind("<Double-Button-1>", lambda _e: self.run_instruction())

        self.cmd_title_var = tk.StringVar(value="指令详情")
        ttk.Label(
            right, textvariable=self.cmd_title_var, font=("Microsoft YaHei UI", 10, "bold")
        ).pack(anchor=tk.W)
        text_frame = ttk.Frame(right)
        text_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        self.cmd_text = tk.Text(text_frame, wrap=tk.CHAR, height=10, undo=False)
        text_sb = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.cmd_text.yview)
        self.cmd_text.configure(yscrollcommand=text_sb.set)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        self.cmd_text.grid(row=0, column=0, sticky="nsew")
        text_sb.grid(row=0, column=1, sticky="ns")
        self.cmd_text.tag_configure("run", background="#fff3cd")
        self.cmd_text.configure(state=tk.DISABLED)

        row = ttk.Frame(right)
        row.pack(fill=tk.X, pady=(2, 0))
        ttk.Button(row, text="新建", style="Accent.TButton", command=self.new_instruction).pack(
            side=tk.LEFT, expand=True, fill=tk.X
        )
        ttk.Button(row, text="编辑", command=self.edit_instruction).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0)
        )
        ttk.Button(row, text="删除", command=self.delete_instruction).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0)
        )
        row2 = ttk.Frame(right)
        row2.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(row2, text="还原", command=self.restore_instruction).pack(
            side=tk.LEFT, expand=True, fill=tk.X
        )
        ttk.Button(row2, text="复制", command=self.copy_instruction).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0)
        )


        self.refresh_command_list()
        self._select_cmd_category(CATEGORY_ALL)

    def _select_cmd_category(self, category_id: int) -> None:
        self.cmd_category = category_id
        self.refresh_command_list()
        c = self.colors
        for cid, btn in getattr(self, "_cmd_cat_buttons", {}).items():
            if cid == category_id:
                btn.configure(bg=c["accent"], fg="#ffffff", activebackground=c["accent"], activeforeground="#ffffff")
            else:
                btn.configure(bg=c["soft"], fg=c["text"], activebackground="#e5e7eb", activeforeground=c["text"])

    def refresh_command_list(self, select_title: Optional[str] = None) -> None:
        if not hasattr(self, "cmd_list"):
            return
        self.cmd_library = load_library()
        keyword = self.cmd_search_var.get().strip() if hasattr(self, "cmd_search_var") else ""
        self.cmd_rows = library_instructions(self.cmd_library, self.cmd_category, keyword)
        self.cmd_list.delete(0, tk.END)
        for item in self.cmd_rows:
            content = str(item.get("content") or "")
            count = sum(1 for line in content.splitlines() if line.strip() and not line.strip().startswith("#"))
            mark = " ·我的" if item.get("custom") else (" ·已改" if item.get("edited") else "")
            self.cmd_list.insert(tk.END, "%s（%d 步%s）" % (item.get("title") or "未命名", count, mark))
        total = len(self.cmd_library.get("instructions") or [])
        self.cmd_list_hint.set("共 %d 条，当前显示 %d 条" % (total, len(self.cmd_rows)))
        if self.cmd_rows:
            index = 0
            if select_title:
                for i, item in enumerate(self.cmd_rows):
                    if str(item.get("title") or "") == select_title:
                        index = i
                        break
            self.cmd_list.selection_set(index)
            self._show_command_detail()

    def _selected_command(self) -> Optional[Dict[str, Any]]:
        try:
            index = int(self.cmd_list.curselection()[0])
        except (IndexError, AttributeError, tk.TclError):
            return None
        if 0 <= index < len(self.cmd_rows):
            return self.cmd_rows[index]
        return None

    def _show_command_detail(self) -> None:
        item = self._selected_command()
        if not item:
            return
        suffix = "（我的）" if item.get("custom") else ("（已修改）" if item.get("edited") else "")
        self.cmd_title_var.set("%s%s" % (item.get("title") or "", suffix))
        self.cmd_text.configure(state=tk.NORMAL)
        self.cmd_text.delete("1.0", tk.END)
        self.cmd_text.insert("1.0", format_content(str(item.get("content") or "")))
        self.cmd_text.configure(state=tk.DISABLED)

    def _instruction_editor(self, title: str, content: str, on_save, heading: str) -> None:
        """通用指令编辑器：on_save(正文) 负责保存；返回 False 表示失败、别关窗。"""
        win = tk.Toplevel(self.root)
        win.title(heading)
        win.transient(self.root)
        win.geometry("640x620")
        win.minsize(480, 420)

        ttk.Label(
            win,
            text=(
                "一行一条：# 开头是注释。支持 传送(章节,地图[,大陆]) / 移动(方向,秒) / "
                "采集(活动) / 等待(秒) / 回城() / 点击(X,Y) / 滚轮(上|下,量[,X,Y]) / 停止()"
            ),
            foreground="#6b7280",
            wraplength=600,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(10, 4))

        frame = ttk.Frame(win)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
        text = tk.Text(frame, wrap=tk.NONE, undo=True)
        sb_y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        sb_x = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
        text.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        text.grid(row=0, column=0, sticky="nsew")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text.insert("1.0", format_content(content))
        text.focus_set()

        note = tk.StringVar(value="")
        ttk.Label(win, textvariable=note, foreground="#c62828").pack(anchor=tk.W, padx=12)

        btns = ttk.Frame(win)
        btns.pack(fill=tk.X, padx=12, pady=10)

        def _save() -> None:
            body = text.get("1.0", tk.END).strip()
            if not body:
                note.set("内容不能为空")
                return
            try:
                if on_save(body) is False:
                    return
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("保存指令", str(exc), parent=win)
                return
            win.destroy()

        ttk.Button(btns, text="保存", style="Accent.TButton", command=_save).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=4)
        win.bind("<Escape>", lambda _e: win.destroy())
        win.grab_set()

    def edit_instruction(self) -> None:
        """编辑被选中的指令（内置的自己改过的也直接改本体，不用先另存一份）。"""
        item = self._selected_command()
        if not item:
            messagebox.showinfo("编辑指令", "先从列表里选一条指令")
            return
        title = str(item.get("title") or "未命名")

        def _on_save(body: str) -> None:
            if item.get("custom"):
                save_user_instruction(title, body, int(item.get("category_id") or 3), old_title=title)
            else:
                save_instruction_override(item.get("id"), body)
            self.refresh_command_list(select_title=title)
            self.cmd_status.set("已保存修改：%s" % title)

        self._instruction_editor(
            title, str(item.get("content") or ""), _on_save, "编辑指令 - %s" % title
        )

    NEW_INSTRUCTION_TEMPLATE = (
        "# 我的指令（把下面几行改成你要的步骤；# 开头的行不会执行）\n"
        "# 传送(3,2)\n"
        "# 移动(上,3)\n"
        "# 等待(2)\n"
        "# 采集(活动)\n"
        "# 回城()\n"
    )

    def new_instruction(self) -> None:
        """新建一条空白指令（存在「我的」里）。"""
        name = simpledialog.askstring(
            "新建指令", "指令名称（例如：我的刷怪路线）：", parent=self.root
        )
        if not name or not name.strip():
            return
        title = name.strip()
        existing = {
            str(row.get("title") or "")
            for row in (load_library().get("instructions") or [])
        }
        if title in existing:
            messagebox.showinfo("新建指令", "已经有同名指令了，换个名字。")
            return

        category = 3
        current = getattr(self, "cmd_category", CATEGORY_ALL)
        if current not in (CATEGORY_ALL, -1, None):
            try:
                category = int(current)
            except (TypeError, ValueError):
                category = 3

        def _on_save(body: str) -> None:
            save_user_instruction(title, body, category)
            self.refresh_command_list(select_title=title)
            self.cmd_status.set("已新建：%s" % title)

        self._instruction_editor(
            title, self.NEW_INSTRUCTION_TEMPLATE, _on_save, "新建指令 - %s" % title
        )

    def restore_instruction(self) -> None:
        """把改过的内置指令还原成原版。"""
        item = self._selected_command()
        if not item:
            return
        if item.get("custom"):
            messagebox.showinfo("还原内置指令", "「我的」指令请直接用「编辑」或「删除」")
            return
        if not item.get("edited"):
            messagebox.showinfo("还原内置指令", "这条是内置原版，没有改过")
            return
        if not messagebox.askyesno("还原内置指令", "把「%s」恢复成内置原版？" % (item.get("title") or "")):
            return
        clear_instruction_override(item.get("id"))
        self.refresh_command_list(select_title=str(item.get("title") or ""))
        self.cmd_status.set("已还原：%s" % (item.get("title") or ""))

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
        try:
            text = self.cmd_text
            last = int(str(text.index("end-1c")).split(".")[0])
            start = getattr(self, "_cmd_step_line", 1)
            found = 0
            for lineno in range(start, last + 1):
                if text.get("%d.0" % lineno, "%d.end" % lineno).strip() == raw.strip():
                    found = lineno
                    break
            if not found:
                for lineno in range(1, start):
                    if text.get("%d.0" % lineno, "%d.end" % lineno).strip() == raw.strip():
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
        item = self._selected_command()
        if not item:
            messagebox.showinfo("运行指令", "先从列表里选一条指令")
            return
        if self.instruction_runner.running:
            self.cmd_status.set("上一条还在跑，先点「停止」")
            return
        content = format_content(str(item.get("content") or ""))
        self._cmd_step_line = 1
        try:
            self.instruction_runner.start(
                str(item.get("title") or script_title(content) or "指令"), content, load_mapping()
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("运行指令", str(exc))
            return
        self._set_run_button(True)
        self.cmd_status.set("运行中…")

    def stop_instruction(self) -> None:
        if self.instruction_runner.running:
            self.instruction_runner.stop()
            self.cmd_status.set("正在停止…")

    def _on_instruction_step(self, index: int, total: int, text: str) -> None:
        """运行器线程回调：只丢队列，界面由主线程 _pump_cmd_events 更新。"""
        self._cmd_events.put(("step", index, total, text))

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
                if kind == "step":
                    _, index, total, raw = event
                    self.cmd_status.set("运行中 %d/%d：%s" % (index, total, raw))
                    self._highlight_step(str(raw))
                elif kind == "done":
                    _, ok, message = event
                    self._set_run_button(False)
                    self.cmd_status.set(("✔ " if ok else "■ ") + str(message or ""))
                    self.cmd_text.tag_remove("run", "1.0", tk.END)
            except tk.TclError:
                pass
        try:
            self.root.after(150, self._pump_cmd_events)
        except tk.TclError:
            pass

    def copy_instruction(self) -> None:
        item = self._selected_command()
        if not item:
            return
        content = format_content(str(item.get("content") or ""))
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.cmd_status.set("已复制到剪贴板")

    def delete_instruction(self) -> None:
        item = self._selected_command()
        if not item or not item.get("custom"):
            messagebox.showinfo("删除指令", "只能删除「我的」里的指令")
            return
        if not messagebox.askyesno("删除指令", "删除「%s」？" % (item.get("title") or "")):
            return
        delete_user_instruction(str(item.get("title") or ""))
        self.refresh_command_list()
        self.cmd_status.set("已删除")

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
        assist["revive_mode"] = "tp"
        assist["revive_delay"] = 0
        self.config["assist"] = assist

    def start_assist(self) -> None:
        self._read_assist_into_config()
        try:
            self.assist.set_feature("revive", 0)  # 现在只有手动复活，自动一律关
        except Exception:  # noqa: BLE001
            pass
        self.assist.set_names(self._assist_names())
        if not self.assist.running:
            self.assist.start()
            self.append_log("游戏辅助已自动连接：打开游戏窗口后会自己注入运行时")
        self._restore_assist_features()

    def _restore_assist_features(self) -> None:
        """重启工具后恢复上次开着的「小怪 / BOSS 地图刷新」。"""
        cfg = normalize_assist(self.config.get("assist"))
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

    def stop_assist(self) -> None:
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

        enabled = bool((state.get(feature) or {}).get("enabled"))
        self.assist.set_feature(feature, not enabled)
        label = {"redPacket": "快速抢红包", "wish": "祈愿领取"}.get(feature, feature)
        if feature == "redPacket":
            self.assist_red_btn.configure(text="关闭" if not enabled else "开启")
            self.assist_red_status.set("正在开启…" if not enabled else "正在关闭…")
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
        elif name == "vision":
            mode = _vision_mode(self.assist_vision_var.get())
            if mode < 0 and not enabled:
                self.append_log("视野已经是关闭状态")
                return
            send = mode
            btn, status, label = (self.assist_vision_btn, self.assist_vision_status, "视野修改")
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

    def revive_click(self) -> None:
        """单击=复活一次；双击（320ms 内点两下）=切换「一直开着」。"""
        job = getattr(self, "_revive_click_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass
            self._revive_click_job = None
            self.toggle_revive_auto()
            return
        self._revive_click_job = self.root.after(320, self._revive_single)

    def _revive_single(self) -> None:
        self._revive_click_job = None
        self.revive_now()

    def toggle_revive_auto(self) -> None:
        """切换「一直开着」：有人倒地就自动复活。"""
        if not self.assist.running:
            self.start_assist()
        revive = (self.assist.state or {}).get("revive") or {}
        if revive.get("enabled"):
            self.assist.set_feature("revive", 0)
            self.assist_revive_status.set("自动已关闭（点 2 次可再开）")
            self.append_log("已发送：关闭自动复活")
            return
        self.assist.set_feature("revive", {"mode": "tp", "delay": 0})
        self.assist_revive_status.set("一直开着：有人倒地就自动复活")
        self.append_log("已发送：开启自动复活（一直开着）")

    def clear_stuck(self) -> None:
        """清除卡住的切图标记：传送/回城点了没反应时用。"""
        if not self.assist.running:
            self.start_assist()
        result = self.assist.clear_stuck()
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
        self.assist_revive_status.set("手动复活中…")
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
            return
        bosses = sum(1 for r in rows if r.get("boss"))
        label = {"BOSS": "BOSS", "小怪": "小怪"}.get(mode, "全部")
        self.assist_blink_status.set(
            "当前地图 %d 只（BOSS %d / 小怪 %d）· %s：显示 %d 只"
            % (len(rows), bosses, len(rows) - bosses, label, len(view))
        )

    def auto_daily(self) -> None:
        """自动去完成任务（传送/战斗/采集），做完自动领取。"""
        if getattr(self, "_auto_daily_busy", False):
            messagebox.showinfo("自动做日常", "上一次还在跑")
            return
        if not messagebox.askyesno(
            "自动做日常",
            "会自动传送、打怪、采集来完成任务（最多 4 分钟），完成后自动领取。\n继续？",
        ):
            return
        if not self.assist.running:
            self.start_assist()
        self._auto_daily_busy = True
        self.assist_daily_status.set("自动做日常：准备中…")
        threading.Thread(target=self._auto_daily_worker, daemon=True).start()

    def _auto_daily_log(self, text: str) -> None:
        try:
            self.root.after(0, lambda: self.assist_daily_status.set(text))
        except tk.TclError:
            pass

    def _auto_daily_collect(self) -> None:
        try:
            runner = InstructionRunner(bridge_provider=lambda: self.assist.bridge, log=self.append_log)
            runner._mapping = load_mapping()
            runner._collect(["活动"])
        except Exception as exc:  # noqa: BLE001
            self.append_log("采集失败：%s" % exc)

    def _auto_daily_hop_to(self, x: float, y: float, tries: int = 20) -> bool:
        """分段瞬移过去（一次瞬移太远游戏会丢掉）；最远约 20 跳 ≈ 2 万米。"""
        for _ in range(tries):
            pos = self.assist.player_pos()
            if len(pos) != 2:
                return False
            dx, dy = float(x) - pos[0], float(y) - pos[1]
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < 220:
                return True
            step = min(dist - 100, 1200)
            self.assist.teleport_to(pos[0] + dx / dist * step, pos[1] + dy / dist * step)
            time.sleep(0.7)
        return False

    def _auto_daily_walk_search(self, boss_name: str, seconds: float = 1.2) -> bool:
        """传送落地后 BOSS 还没刷出来：按 8 个方向走一圈找它，找到就贴脸。"""
        try:
            runner = InstructionRunner(bridge_provider=lambda: self.assist.bridge, log=lambda _m: None)
            runner._mapping = load_mapping()
        except Exception:  # noqa: BLE001
            return False
        for _round in range(3):
            for direction in ("上", "右上", "右", "右下", "下", "左下", "左", "左上"):
                try:
                    runner._move([direction, str(seconds)])
                except Exception:  # noqa: BLE001
                    pass
                monsters = self.assist.list_monsters()
                cand = [m for m in monsters if boss_name in str(m.get("name") or "")]
                if cand:
                    nearest = min(cand, key=lambda m: int(m.get("d") or 0))
                    self._auto_daily_log("自动做日常：找到 %s（%s 米）" % (boss_name, nearest.get("d")))
                    self._auto_daily_hop_to(nearest.get("x"), nearest.get("y"))
                    return True
        return False

    def _auto_daily_kill_boss(self, task: Dict[str, Any], boss_name: str) -> None:
        """击杀指定 BOSS：走游戏的「前往 → 传送并追踪」，过去之后再闪现贴脸打。"""
        task_id = task.get("id")
        deadline = time.monotonic() + 90
        tried_go = False
        while time.monotonic() < deadline:
            fresh = {t["id"]: t for t in self.assist.daily_task_list()}.get(task_id) or {}
            if int(fresh.get("progress") or 0) >= int(fresh.get("target") or 1):
                return
            # 第一次：开面板 → 前往 → 传送并追踪
            if not tried_go:
                tried_go = True
                self._auto_daily_log("自动做日常：打开任务面板，前往「%s」" % boss_name)
                self.assist.open_daily_tasks()
                time.sleep(1.0)
                go = self.assist.task_go(boss_name)
                if go.get("ok"):
                    time.sleep(0.8)
                    self.assist.task_teleport_track()
                    time.sleep(3.0)
                else:
                    self._auto_daily_log("自动做日常：面板里没找到「%s」，直接闪现找" % boss_name)
            monsters = self.assist.list_monsters()
            cand = [m for m in monsters if boss_name in str(m.get("name") or "")]
            if cand:
                nearest = min(cand, key=lambda m: int(m.get("d") or 0))
                self._auto_daily_log("自动做日常：去找 %s（%s 米）" % (boss_name, nearest.get("d")))
                self._auto_daily_hop_to(nearest.get("x"), nearest.get("y"))
                time.sleep(8.0)
            else:
                self._auto_daily_log("自动做日常：全图没刷出 %s，朝任务方向走一段" % boss_name)
                if self._auto_daily_walk_search(boss_name):
                    time.sleep(8.0)
                else:
                    time.sleep(2.0)

    def _auto_daily_kill_count(self, task: Dict[str, Any]) -> None:
        """击杀 N 只怪：闪现到怪堆 + 重进地图刷怪，直到任务进度达标或超时。"""
        task_id = task.get("id")
        target = int(task.get("target") or 0)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            fresh = {t["id"]: t for t in self.assist.daily_task_list()}.get(task_id) or {}
            if int(fresh.get("progress") or 0) >= target:
                return
            monsters = self.assist.list_monsters()
            if monsters:
                nearest = min(monsters, key=lambda m: int(m.get("d") or 0))
                self._auto_daily_hop_to(nearest.get("x"), nearest.get("y"))
                time.sleep(4.0)
            else:
                self.assist.reload_map_now()
                time.sleep(3.5)

    def _auto_daily_share(self) -> None:
        """分享任务：调游戏分享入口 → 最小化/恢复窗口 → 等任务进度。"""
        try:
            self.assist.share_game()
        except Exception as exc:  # noqa: BLE001
            self.append_log("分享入口调用失败：%s" % exc)
        # 模拟"分享出去再回来"：最小化再恢复游戏窗口
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = 0

            def _find(title_prefix: str) -> int:
                found = []
                WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

                def cb(h, _):
                    n = user32.GetWindowTextLengthW(h)
                    if n:
                        buf = ctypes.create_unicode_buffer(n + 1)
                        user32.GetWindowTextW(h, buf, n + 1)
                        if buf.value.startswith(title_prefix):
                            found.append(h)
                    return True

                user32.EnumWindows(WNDENUMPROC(cb), 0)
                return found[0] if found else 0

            hwnd = _find("Zizuo游戏窗")
            if hwnd:
                user32.ShowWindow(hwnd, 6)   # 最小化
                time.sleep(1.6)
                user32.ShowWindow(hwnd, 9)   # 恢复
        except Exception as exc:  # noqa: BLE001
            self.append_log("最小化窗口失败：%s" % exc)
        time.sleep(2.0)

    def _auto_daily_worker(self) -> None:
        deadline = time.monotonic() + 240
        try:
            for _round in range(5):
                tasks = self.assist.daily_task_list()
                if not tasks:
                    break
                pending = [t for t in tasks if not t.get("claimed") and t.get("progress", 0) < t.get("target", 0)]
                if not pending:
                    break
                for task in pending:
                    if time.monotonic() > deadline:
                        break
                    try:
                        self.assist.claim_daily_tasks()
                    except Exception:  # noqa: BLE001
                        pass
                    name = DAILY_TASK_NAMES.get(task["id"], "任务%d" % task["id"])
                    if any(word in name for word in DAILY_TASK_SKIP):
                        continue
                    self._auto_daily_log("自动做日常：%s（%s/%s）" % (name, task.get("progress"), task.get("target")))
                    if "分享" in name:
                        self._auto_daily_share()
                        continue
                    if name.startswith("击杀BOSS"):
                        self._auto_daily_kill_boss(task, name[len("击杀BOSS"):].strip())
                        continue
                    if "只怪" in name:
                        self._auto_daily_kill_count(task)
                        continue
                    if "采集" in name:
                        self._auto_daily_collect()
                        continue
                    map_id = int(task.get("mapId") or 0)
                    if not map_id:
                        continue
                    for _attempt in range(3):
                        if time.monotonic() > deadline:
                            break
                        self.assist.goto_task_point(map_id, task.get("x") or 0, task.get("y") or 0)
                        time.sleep(12)
                        fresh = {t["id"]: t for t in self.assist.daily_task_list()}.get(task["id"])
                        if fresh and fresh.get("progress", 0) >= fresh.get("target", 0):
                            break
            result = self.assist.claim_daily_tasks()
            claimed = int(result.get("claimed") or 0)
            left = int(result.get("waiting") or 0)
            self._auto_daily_log("自动做日常结束：领了 %d 个，还剩 %d 个没完成" % (claimed, left))
            self.append_log("自动做日常：领了 %d 个，还剩 %d 个" % (claimed, left))
        except Exception as exc:  # noqa: BLE001
            self.append_log("自动做日常出错：%s" % exc)
            self._auto_daily_log("自动做日常出错：%s" % exc)
        finally:
            self._auto_daily_busy = False

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

    def pick_coord(self) -> None:
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

    def complete_daily(self) -> None:
        """用任务卡把没做完的日常任务秒完成（会消耗任务卡）。"""
        if not self.assist.running:
            self.start_assist()
        if not messagebox.askyesno(
            "一键完成日常",
            "会用「任务卡」把没完成的日常任务直接完成，每一条消耗一张任务卡。\n确定继续？",
        ):
            return
        self.assist_daily_status.set("正在用任务卡完成…")
        result = self.assist.complete_daily_with_cards()
        if not result.get("ok"):
            self.assist_daily_status.set("完成失败：%s" % (result.get("error") or "未知原因"))
            return
        used = int(result.get("used") or 0)
        failed = int(result.get("failed") or 0)
        self.assist_daily_status.set("已用任务卡完成 %d 条（失败 %d）" % (used, failed))
        self.append_log("一键完成日常：任务卡 %d 张，失败 %d" % (used, failed))
        self.root.after(1500, self.claim_daily)

    def claim_daily(self) -> None:
        """一键领日常：直接调游戏任务服务，把能领的奖励全部领掉（不开面板）。"""
        if not self.assist.running:
            self.start_assist()
        self.assist_daily_status.set("正在领取…")
        result = self.assist.claim_daily_tasks()
        if not result.get("ok"):
            self.assist_daily_status.set("领取失败：%s" % (result.get("error") or "未知原因"))
            return
        claimed = int(result.get("claimed") or 0)
        waiting = int(result.get("waiting") or 0)
        text = "已领取 %d 个，未完成 %d 个" % (claimed, waiting)
        self.assist_daily_status.set(text)
        self.append_log("一键领日常：" + text)

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
                       self.assist_cooldown_btn, self.assist_vision_btn,
                       self.assist_revive_btn):
            button.configure(state=tk.DISABLED if gap else tk.NORMAL)

        self.assist_mob_btn.configure(text="关闭" if mob.get("enabled") else "开启")
        self.assist_boss_btn.configure(text="关闭" if boss.get("enabled") else "开启")
        self.assist_damage_btn.configure(text="关闭" if damage.get("enabled") else "开启")
        self.assist_shield_btn.configure(text="关闭" if (combat.get("shield") or {}).get("enabled") else "开启")
        self.assist_cooldown_btn.configure(text="关闭" if cooldown.get("enabled") else "开启")
        self.assist_vision_btn.configure(text="恢复" if vision.get("enabled") else "应用")
        self.assist_red_btn.configure(text="关闭" if red.get("enabled") else "开启")
        self.assist_wish_btn.configure(text="关闭" if wish.get("enabled") else "开启")

        self.assist_mob_status.set(str(mob.get("reason") or "待接入"))
        self.assist_damage_status.set(str(damage.get("reason") or ("需要版本映射" if gap else "尚未开启")))
        self.assist_shield_status.set(str((combat.get("shield") or {}).get("reason") or ("需要版本映射" if gap else "尚未开启")))
        self.assist_cooldown_status.set(str(cooldown.get("reason") or ("需要版本映射" if gap else "尚未开启")))
        self.assist_vision_status.set(str(vision.get("reason") or ("需要版本映射" if gap else "尚未开启")))
        self.assist_boss_status.set(str(boss.get("reason") or "待接入"))
        self.assist_red_status.set(str(red.get("reason") or ("需要版本映射" if gap else "尚未开启")))
        self.assist_wish_status.set(str(wish.get("reason") or "待接入"))
        revive_reason = str(revive.get("reason") or ("需要版本映射" if gap else "尚未开启"))
        if revive.get("count"):
            revive_reason += f" · 今日复活 {int(revive.get('count') or 0)} 次"
        self.assist_revive_status.set(revive_reason)
        self.assist_revive_btn.configure(
            text="快速复活 · 自动开" if revive.get("enabled") else "快速复活"
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

    def _on_gacha_progress(self, info: Dict[str, Any]) -> None:
        draws = info.get("draws", 0)
        target_hits = info.get("target_hits", 0)
        counts = info.get("counts") or {}
        # 只展示目标卡数量，如 李白:2 狂战士:1
        detail = " ".join(f"{k}:{v}" for k, v in counts.items()) or "无"

        def _do() -> None:
            self.gacha_status.set(
                f"抽卡中… 总抽 {draws}，目标 {target_hits}（{detail}）"
            )

        self.root.after(0, _do)

    def _tick_cursor(self) -> None:
        from .capture import get_cursor_pos

        self._drain_logs()
        x, y = get_cursor_pos()
        self.pos_var.set(f"鼠标: ({x}, {y})")
        if self.recorder.recording:
            self.record_status.set(f"游戏内录制中… 已录 {len(self.recorder.steps)} 步")
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

        if self._game_proc is not None and self._game_proc.poll() is None:
            self.append_log("已有游戏窗口进程，正在重新打开…")
            try:
                self._game_proc.terminate()
            except Exception:  # noqa: BLE001
                pass

        try:
            self._game_proc = open_game_window(
                url=url, width=width, height=height,
                browser=str(self.config.get("browser_path") or "").strip() or None,
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

    def open_game_browser(self) -> None:
        url = str(self.config.get("game_url") or DEFAULT_GAME_URL).strip() or DEFAULT_GAME_URL
        webbrowser.open(url)
        self.append_log(f"已用系统浏览器打开: {url}")

    def refresh_slot_list(self, select: Optional[int] = None) -> None:
        current = self._selected_slot_index()
        self.slot_list.delete(0, tk.END)
        for i in range(SLOT_COUNT):
            self.slot_list.insert(tk.END, slot_label(self.slots, i))
        idx = select if select is not None else current
        if idx is None:
            idx = max(0, int(self.config.get("selected_slot", 1)) - 1)
        idx = max(0, min(SLOT_COUNT - 1, idx))
        self.slot_list.selection_clear(0, tk.END)
        self.slot_list.selection_set(idx)
        self.slot_list.activate(idx)
        self.slot_list.see(idx)
        self._load_slot_into_editor(idx, force=True)

    def _selected_slot_index(self) -> Optional[int]:
        sel = self.slot_list.curselection()
        if not sel:
            return None
        return int(sel[0])

    def _on_slot_select(self, _event=None) -> None:
        idx = self._selected_slot_index()
        if idx is None:
            return
        self.config["selected_slot"] = idx + 1
        self._load_slot_into_editor(idx)
        self.slot_hint.set(f"当前选中存档 {idx + 1}，F8 将运行此存档")

    def _load_slot_into_editor(self, index: int, force: bool = False) -> None:
        if self._loading_slot and not force:
            return
        self._loading_slot = True
        try:
            lines = self.slots[index] if 0 <= index < len(self.slots) else []
            text = "\n".join(lines)
            self.script_text.delete("1.0", tk.END)
            if text:
                self.script_text.insert("1.0", text)
            self.slot_hint.set(f"当前选中存档 {index + 1}，F8 将运行此存档")
        finally:
            self._loading_slot = False

    def get_editor_script(self) -> str:
        return self.script_text.get("1.0", tk.END).strip()

    def set_editor_script(self, script: str) -> None:
        self.script_text.delete("1.0", tk.END)
        if script:
            self.script_text.insert("1.0", script)

    def save_editor_to_slot(self) -> None:
        idx = self._selected_slot_index()
        if idx is None:
            messagebox.showinfo("提示", "请先选择存档 1-10")
            return
        script = self.get_editor_script()
        try:
            if script:
                script_to_steps(script)
            set_slot(self.slots, idx, script)
            save_slots(self.slots)
            self.refresh_slot_list(select=idx)
            self.append_log(f"已写入存档 {idx + 1}")
        except ValueError as exc:
            messagebox.showerror("脚本错误", str(exc))

    def clear_selected_slot(self) -> None:
        idx = self._selected_slot_index()
        if idx is None:
            return
        if not messagebox.askyesno("确认", f"清空存档 {idx + 1}？"):
            return
        set_slot(self.slots, idx, [])
        save_slots(self.slots)
        self.refresh_slot_list(select=idx)
        self.append_log(f"已清空存档 {idx + 1}")

    def write_to_slot_dialog(self) -> None:
        script = self.get_editor_script()
        if not script:
            messagebox.showinfo("提示", "编辑器里没有脚本代码")
            return
        try:
            script_to_steps(script)
        except ValueError as exc:
            messagebox.showerror("脚本错误", str(exc))
            return
        raw = simpledialog.askstring(
            "写入存档",
            "写入到哪个存档？输入 1-10：",
            initialvalue=str(self.config.get("selected_slot", 1)),
            parent=self.root,
        )
        if not raw:
            return
        try:
            n = int(raw.strip())
        except ValueError:
            messagebox.showerror("错误", "请输入 1-10 的数字")
            return
        if n < 1 or n > SLOT_COUNT:
            messagebox.showerror("错误", "存档编号必须是 1-10")
            return
        idx = n - 1
        set_slot(self.slots, idx, script)
        save_slots(self.slots)
        self.refresh_slot_list(select=idx)
        self.append_log(f"脚本已写入存档 {n}")

    def copy_script(self) -> None:
        script = self.get_editor_script()
        if not script:
            messagebox.showinfo("提示", "没有可复制的脚本")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(script)
        self.append_log("脚本已复制到剪贴板，可粘贴到任意存档")

    def paste_script(self) -> None:
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("提示", "剪贴板为空")
            return
        self.set_editor_script(str(text).strip())
        self.append_log("已从剪贴板粘贴到编辑器，再点「保存到选中存档」或「写入存档…」")

    def toggle_record(self) -> None:
        if self.recorder.recording:
            steps = self.recorder.stop()
            self.record_status.set("录制: 未开始（游戏窗口内相对坐标）")
            if not steps:
                self.append_log("没有录到任何点击（请点在游戏浏览器窗口内）")
                return
            default_name = time.strftime("游戏录制_%H%M%S")
            name = simpledialog.askstring(
                "脚本名称", "生成脚本代码的名称：", initialvalue=default_name, parent=self.root
            )
            if not name:
                name = default_name
            # 必须用实际录制客户区尺寸，不能用表单里的预设宽高（否则坐标会漂）
            window_size = self.recorder.recorded_size
            if window_size is None and self.recorder.window_region:
                window_size = (
                    self.recorder.window_region.width,
                    self.recorder.window_region.height,
                )
            script = steps_to_script(steps, name=name, window_size=window_size)
            self.set_editor_script(script)
            self.root.clipboard_clear()
            self.root.clipboard_append(script)
            rel_n = sum(1 for s in steps if s.get("relative"))
            self.append_log(
                f"已生成脚本代码: {name}（{len(steps)} 步，相对坐标 {rel_n}），"
                "已复制到剪贴板。选存档后点「保存到选中存档」，或点「写入存档…」"
            )
            messagebox.showinfo(
                "录制完成",
                "脚本代码已生成并复制。\n\n"
                "选左边存档 1-10 →「保存到选中存档」\n"
                "或点「写入存档…」填编号。\n"
                "然后选中该存档按 F8 运行。",
            )
            return

        if self.runner.running or self.gacha_runner.running:
            messagebox.showinfo("提示", "请先停止运行/抽卡再录制")
            return

        title = self.window_var.get().strip()
        if not title:
            messagebox.showinfo(
                "提示",
                "请先填写「目标窗口标题」并打开游戏。\n"
                "可点「打开游戏窗口」，标题含 Zizuo游戏窗。",
            )
            return

        self.config["target_window_title"] = title
        self.recorder.start(title)
        if not self.recorder.recording:
            self.record_status.set("录制失败：未找到窗口")
            return
        self.record_status.set("游戏内录制中… 已录 0 步")

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
        self.config["gacha"] = normalize_gacha(
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
        return True

    def _read_form_into_config(self) -> bool:
        try:
            self.config["target_window_title"] = self.window_var.get().strip()
            self.config["match_threshold"] = float(self.threshold_var.get().strip())
            self.config["loop_interval_ms"] = int(self.interval_var.get().strip())
            self.config["game_width"] = int(self.width_var.get().strip())
            self.config["game_height"] = int(self.height_var.get().strip())
            self.config["loop_playback"] = bool(self.loop_var.get())
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
            idx = self._selected_slot_index()
            if idx is not None:
                self.config["selected_slot"] = idx + 1
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
        save_slots(self.slots)
        self.append_log("配置与存档已保存")

    def start_run(self) -> None:
        if self.recorder.recording:
            messagebox.showinfo("提示", "正在录制，请先 F6 结束录制")
            return
        if self.gacha_runner.running:
            messagebox.showinfo("提示", "正在抽卡，请先停止")
            return
        if not self._read_form_into_config():
            return

        idx = self._selected_slot_index()
        if idx is None:
            messagebox.showinfo("提示", "请先选择存档 1-10")
            return

        script = self.get_editor_script()
        lines = script_to_lines(script) if script else list(self.slots[idx])
        if not lines:
            messagebox.showinfo("提示", f"存档 {idx + 1} 为空，请先录制并写入")
            return
        try:
            steps = script_to_steps(lines)
        except ValueError as exc:
            messagebox.showerror("脚本错误", str(exc))
            return
        if not steps:
            messagebox.showinfo("提示", "脚本里没有可执行的点击/等待")
            return

        set_slot(self.slots, idx, lines)
        save_slots(self.slots)
        save_config(self.config)
        self.refresh_slot_list(select=idx)

        name = f"存档{idx + 1}:{slot_title(lines, idx)}"
        task = new_task(name=name, mode="record", steps=steps)
        loop = bool(self.loop_var.get())
        self._lock_game_window("存档运行")
        self.runner.start(self.config, slot_task=task, loop=loop)
        self.append_log(
            f"运行存档 {idx + 1}（{len(steps)} 步，{'循环' if loop else '单次'}）"
        )

    def insert_find_teleport(self) -> None:
        line = "找图点击(传送)\n"
        self.script_text.insert(tk.INSERT, line)
        tpl = templates_dir() / "ui" / "传送.png"
        if not tpl.exists() and not (templates_dir() / "传送.png").exists():
            self.append_log(
                "已插入 找图点击(传送)。请先把鼠标移到传送按钮上，点「截取模板」保存为 传送"
            )
        else:
            self.append_log("已插入 找图点击(传送)")

    def capture_template(self) -> None:
        name = simpledialog.askstring(
            "截取模板",
            "模板名称：\n"
            "按钮用 金币招募 / 最大 / 刷新 / 放弃\n"
            "英雄用卡名如 李白（保存到人物图片目录）\n"
            "请先把鼠标移到目标正中再点确定：",
            initialvalue="招募",
            parent=self.root,
        )
        if not name:
            return
        name = name.strip().replace(".png", "").replace(".jpg", "")
        if not name:
            return
        messagebox.showinfo(
            "准备截取",
            "点确定后请在 2 秒内把鼠标移到目标按钮中心，不要点助手窗口。",
        )
        self.root.after(2000, lambda: self._do_capture_template(name))

    def _do_capture_template(self, name: str) -> None:
        import cv2

        try:
            # 按钮用稍扁的裁切；英雄脸可再截大一点
            half_w, half_h = (70, 36)
            if name in CARD_CATALOG:
                half_w, half_h = (55, 70)
            patch, (mx, my) = grab_cursor_patch(half_w=half_w, half_h=half_h)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("截取失败", str(exc))
            return

        if name in CARD_CATALOG:
            out_dir = cards_dir()
        else:
            out_dir = ui_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{name}.png"
        ok, buf = cv2.imencode(".png", patch)
        if not ok:
            messagebox.showerror("截取失败", "编码 PNG 失败")
            return
        path.write_bytes(buf.tobytes())
        self.append_log(f"已保存模板: {path}（鼠标@{mx},{my}）")
        self.refresh_gacha_templates()
        if name not in CARD_CATALOG and messagebox.askyesno(
            "插入脚本",
            f"模板已保存为 {name}.png\n是否在编辑器插入 找图点击({name})？",
        ):
            self.script_text.insert(tk.INSERT, f"找图点击({name})\n")

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
        try:
            max_draws = int(self.gacha_cdp_max_var.get().strip() or "0")
            delay_ms = int(self.gacha_cdp_delay_var.get().strip() or "1200")
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
        self.gacha_cdp_status.set("已开始，读取招募面板…")
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

    def start_gacha(self) -> None:
        if self.recorder.recording:
            messagebox.showinfo("提示", "正在录制，请先结束")
            return
        if self.runner.running:
            messagebox.showinfo("提示", "正在运行存档，请先停止")
            return
        if self.gacha_runner.running:
            messagebox.showinfo("提示", "抽卡已在运行")
            return
        if not self._read_form_into_config():
            return

        gacha = normalize_gacha(self.config.get("gacha"))
        if not gacha.get("enabled_cards"):
            messagebox.showinfo("提示", "请至少勾选一张目标英雄")
            return

        save_config(self.config)
        self.refresh_gacha_templates()
        self._lock_game_window("抽卡")
        self.gacha_status.set("抽卡进行中…")
        self.gacha_runner.start(config=self.config)
        self.append_log("已开始常驻招募抽卡")

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
        if self.runner.running or self.gacha_runner.running:
            return
        if unlock_game_window():
            self.append_log("已解锁游戏窗，可拖拽移动/缩放")

    def _on_run_finished(self) -> None:
        def _do() -> None:
            if not self.gacha_runner.running:
                self.gacha_status.set("抽卡: 空闲")
            self._unlock_game_window_if_idle()

        self.root.after(0, _do)

    def stop_run(self) -> None:
        if self.runner.running:
            self.runner.stop()
        if self.gacha_runner.running:
            self.gacha_runner.stop()
            self.gacha_status.set("抽卡: 已停止")
        try:
            self.stop_gacha_cdp()
        except Exception:  # noqa: BLE001
            pass
        self._unlock_game_window_if_idle()

    def bind_hotkeys(self) -> None:
        if self._hotkeys_bound:
            return

        def on_start():
            self.root.after(0, self.start_run)

        def on_stop():
            self.root.after(0, self.stop_run)

        def on_record():
            self.root.after(0, self.toggle_record)

        if keyboard is None:
            self._hotkeys_bound = True
            self.append_log("当前系统不支持全局热键，用界面按钮操作即可")
            return
        keyboard.add_hotkey("f8", on_start)
        keyboard.add_hotkey("f9", on_stop)
        keyboard.add_hotkey("f6", on_record)
        self._hotkeys_bound = True
        self.append_log("热键: F6录制  F8运行存档  F9停止(含抽卡)")
        self.append_log(f"存档: saves/script.json  抽卡结果: saves/gacha_result.json")

    def on_close(self) -> None:
        if self.recorder.recording:
            self.recorder.stop()
        self.runner.stop()
        self.gacha_runner.stop()
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
                save_slots(self.slots)
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
