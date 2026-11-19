"""主界面：1-10 存档 + 脚本代码 + 抽卡。"""

from __future__ import annotations

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
from .game_assist import GameAssist
from .build_info import dev_mode
from .gacha_cdp import GachaCdp

# 是否允许「用系统浏览器打开游戏」。
# 先关闭：浏览器里没有 9229 调试端口，辅助/抽卡注入不了。以后要恢复改成 True。
ENABLE_BROWSER_OPEN = False

# 抽卡固定目标名单（要保留的英雄）；界面可勾选，保存在 config 的 gacha_cdp.checked
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
REVIVE_TP = "传送回营地复活（免费，0.2 秒，怪会回满血，推荐）"
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
        self.root.title("桌面找图助手")
        self.root.geometry("1080x720")
        self.root.minsize(960, 640)

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
        # 开发模式：源码运行显示开发功能；打包成 exe 默认隐藏（--dev / ZIZUO_DEV=1 可强制打开）
        self.dev_mode = dev_mode()
        self.record_status = tk.StringVar(value="录制: 未开始（游戏窗口内相对坐标）")
        self.slot_hint = tk.StringVar(value="")
        self.gacha_status = tk.StringVar(value="抽卡: 未开始")
        self.card_vars: Dict[str, tk.BooleanVar] = {}

        self._build()
        self.refresh_slot_list(select=int(self.config.get("selected_slot", 1)) - 1)
        self.refresh_gacha_templates()
        self.bind_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # 开界面就自动接上游戏窗（参考工具那样：不用点「启动辅助」）
        self.root.after(1200, self._auto_connect_assist)

    def _build(self) -> None:
        # 最上方：模式选择（一眼可见、中间隔开）
        mode_bar = ttk.Frame(self.root, padding=(12, 10, 12, 6))
        mode_bar.pack(fill=tk.X)
        self.mode_var = tk.StringVar(value="gacha")
        self._mode_btn_gacha = tk.Button(
            mode_bar,
            text="抽卡",
            width=14,
            height=2,
            font=("", 12, "bold"),
            relief=tk.SUNKEN,
            bg="#dceafc",
            activebackground="#c5dbf7",
            command=lambda: self._switch_mode("gacha"),
        )
        self._mode_btn_gacha.pack(side=tk.LEFT, padx=(0, 36))
        self._mode_btn_assist = tk.Button(
            mode_bar,
            text="游戏辅助",
            width=14,
            height=2,
            font=("", 12, "bold"),
            relief=tk.RAISED,
            activebackground="#c5dbf7",
            command=lambda: self._switch_mode("assist"),
        )
        self._mode_btn_assist.pack(side=tk.LEFT, padx=(36, 0))
        ttk.Label(mode_bar, text="← 点这边切换功能").pack(side=tk.LEFT, padx=24)
        ttk.Button(
            mode_bar,
            text=("打开游戏（选内置/浏览器）" if ENABLE_BROWSER_OPEN else "打开游戏窗口"),
            command=self.ask_open_game,
        ).pack(side=tk.RIGHT, padx=6)

        # 窗口参数：变量始终创建（保存/读取要用），控件只在开发模式显示
        self.window_var = tk.StringVar(
            value=self.config.get("target_window_title") or GAME_WINDOW_TITLE
        )
        self.width_var = tk.StringVar(value=str(self.config.get("game_width", 390)))
        self.height_var = tk.StringVar(value=str(self.config.get("game_height", 844)))
        self.preset_var = tk.StringVar(value="常见 390x844")
        self.threshold_var = tk.StringVar(value=str(self.config.get("match_threshold", 0.85)))
        self.interval_var = tk.StringVar(value=str(self.config.get("loop_interval_ms", 800)))

        if self.dev_mode:
            top = ttk.Frame(self.root, padding=(8, 0, 8, 4))
            top.pack(fill=tk.X)
            ttk.Label(top, text="目标窗口标题").pack(side=tk.LEFT)
            ttk.Entry(top, textvariable=self.window_var, width=16).pack(side=tk.LEFT, padx=4)

            size_row = ttk.Frame(self.root, padding=(8, 0, 8, 4))
            size_row.pack(fill=tk.X)
            ttk.Label(size_row, text="游戏窗宽").pack(side=tk.LEFT)
            ttk.Entry(size_row, textvariable=self.width_var, width=6).pack(
                side=tk.LEFT, padx=4
            )
            ttk.Label(size_row, text="高").pack(side=tk.LEFT)
            ttk.Entry(size_row, textvariable=self.height_var, width=6).pack(
                side=tk.LEFT, padx=4
            )
            preset = ttk.Combobox(
                size_row,
                textvariable=self.preset_var,
                values=list(SIZE_PRESETS.keys()),
                width=14,
                state="readonly",
            )
            preset.pack(side=tk.LEFT, padx=6)
            preset.bind("<<ComboboxSelected>>", self._on_preset)
            ttk.Button(size_row, text="打开游戏窗口", command=self.open_game_window).pack(
                side=tk.LEFT, padx=4
            )
            if ENABLE_BROWSER_OPEN:
                ttk.Button(size_row, text="系统浏览器", command=self.open_game_browser).pack(
                    side=tk.LEFT, padx=2
                )

        self._content = ttk.Frame(self.root)
        self._content.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self._page_slots = ttk.Frame(self._content, padding=4)
        self._page_gacha = ttk.Frame(self._content, padding=4)
        self._page_assist = ttk.Frame(self._content, padding=4)
        # 存档脚本功能暂时隐藏（后续重写），页面仍构建但默认不显示
        self._build_slots_tab(self._page_slots)
        self._build_gacha_tab(self._page_gacha)
        self._build_assist_tab(self._page_assist)
        self._page_gacha.pack(fill=tk.BOTH, expand=True)

        if self.dev_mode:
            log_frame = ttk.LabelFrame(self.root, text="日志", padding=4)
            log_frame.pack(fill=tk.BOTH, expand=False, padx=8, pady=(0, 8))
            self.log = tk.Text(log_frame, height=10, wrap=tk.WORD)
            self.log.pack(fill=tk.BOTH, expand=True)
        else:
            self.log = None

        self.root.after(200, self._tick_cursor)

    def _switch_mode(self, mode: str) -> None:
        self.mode_var.set(mode)
        pages = {
            "gacha": self._page_gacha,
            "assist": self._page_assist,
        }
        buttons = {
            "gacha": self._mode_btn_gacha,
            "assist": self._mode_btn_assist,
        }
        for name, page in pages.items():
            if name == mode:
                page.pack(fill=tk.BOTH, expand=True)
            else:
                page.pack_forget()
        for name, button in buttons.items():
            if name == mode:
                button.configure(relief=tk.SUNKEN, bg="#dceafc")
            else:
                button.configure(relief=tk.RAISED, bg="SystemButtonFace")
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

    def _build_gacha_tab(self, parent: ttk.Frame) -> None:
        gacha = normalize_gacha(self.config.get("gacha"))
        enabled = set(gacha.get("enabled_cards") or [])

        hero_box = ttk.LabelFrame(
            parent, text="目标英雄（勾选要保留的英雄；不勾就用下面的「目标英雄」文本）", padding=6
        )
        hero_box.pack(fill=tk.BOTH, expand=False, pady=(0, 6))

        hero_bar = ttk.Frame(hero_box)
        hero_bar.pack(fill=tk.X)
        ttk.Button(hero_bar, text="重新载入名单", command=self.reload_gacha_heroes).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(hero_bar, text="全选", command=lambda: self._set_all_heroes(True)).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(hero_bar, text="全不选", command=lambda: self._set_all_heroes(False)).pack(
            side=tk.LEFT, padx=2
        )
        self.gacha_hero_count = tk.StringVar(value="固定名单")
        ttk.Label(hero_bar, textvariable=self.gacha_hero_count, foreground="#555").pack(
            side=tk.LEFT, padx=8
        )
        ttk.Label(
            hero_box,
            text="说明：这是固定目标名单（勾选要保留的英雄）。脚本会一直抽/刷新候选，"
            "候选里出现这些英雄就切最大倍率抽并保留；不在名单里的抽到就放弃。",
            wraplength=980,
            foreground="#a35a00",
        ).pack(anchor=tk.W, pady=(4, 0))

        self._gacha_hero_canvas = tk.Canvas(hero_box, height=170, highlightthickness=0)
        hero_sb = ttk.Scrollbar(hero_box, orient=tk.VERTICAL, command=self._gacha_hero_canvas.yview)
        self._gacha_hero_inner = ttk.Frame(self._gacha_hero_canvas)
        self._gacha_hero_inner.bind(
            "<Configure>",
            lambda _e: self._gacha_hero_canvas.configure(
                scrollregion=self._gacha_hero_canvas.bbox("all")
            ),
        )
        self._gacha_hero_canvas.create_window((0, 0), window=self._gacha_hero_inner, anchor="nw")
        self._gacha_hero_canvas.configure(yscrollcommand=hero_sb.set)
        self._gacha_hero_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hero_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._gacha_hero_vars: Dict[int, tk.BooleanVar] = {}
        self._gacha_hero_counter_vars: Dict[int, tk.StringVar] = {}
        self._hero_name_cache: Dict[int, str] = {}

        cdp_box = ttk.LabelFrame(
            parent, text="直连抽卡（不截图，直接调游戏招募接口；需先打开酒馆/招募界面）", padding=8
        )
        cdp_box.pack(fill=tk.X, pady=(0, 8))

        # 参数变量始终创建（保存/开始要用），对应的控件只在开发模式显示
        self.gacha_cdp_targets_var = tk.StringVar(value=str(gacha.get("cdp_targets", "")))
        self.gacha_cdp_red_var = tk.BooleanVar(value=bool(gacha.get("cdp_keep_red", False)))
        self.gacha_cdp_max_var = tk.StringVar(value=str(gacha.get("cdp_max_draws", 0)))
        self.gacha_cdp_delay_var = tk.StringVar(value=str(gacha.get("cdp_delay_ms", 1200)))
        self.gacha_cdp_refresh_var = tk.BooleanVar(value=bool(gacha.get("cdp_refresh", False)))
        self.gacha_cdp_green_var = tk.StringVar(value=str(gacha.get("cdp_max_green", 0)))
        self.gacha_cdp_maxbet_var = tk.BooleanVar(value=bool(gacha.get("cdp_max_bet", True)))
        self.gacha_pool_var = tk.StringVar(value="当前候选：未读取（打开招募面板后开始抽卡会自动读取）")
        self.gacha_records_var = tk.StringVar(value="抽中：尚未出货")

        if self.dev_mode:
            ttk.Label(cdp_box, text="目标英雄(名字或编号，逗号分隔)").grid(
                row=0, column=0, sticky=tk.W, padx=4, pady=2
            )
            ttk.Entry(cdp_box, textvariable=self.gacha_cdp_targets_var, width=30).grid(
                row=0, column=1, sticky=tk.W, padx=4, pady=2
            )
            ttk.Button(cdp_box, text="列出可选英雄", command=self.list_gacha_heroes).grid(
                row=0, column=7, sticky=tk.W, padx=6
            )
            ttk.Checkbutton(
                cdp_box,
                text="只保留红色(不看名单)",
                variable=self.gacha_cdp_red_var,
            ).grid(row=0, column=2, sticky=tk.W, padx=8)
            ttk.Label(cdp_box, text="最大抽数(0不限)").grid(
                row=0, column=3, sticky=tk.W, padx=4, pady=2
            )
            ttk.Entry(cdp_box, textvariable=self.gacha_cdp_max_var, width=8).grid(
                row=0, column=4, sticky=tk.W, padx=4, pady=2
            )
            ttk.Label(cdp_box, text="间隔ms").grid(row=0, column=5, sticky=tk.W, padx=4)
            ttk.Entry(cdp_box, textvariable=self.gacha_cdp_delay_var, width=8).grid(
                row=0, column=6, sticky=tk.W, padx=4
            )

            refresh_row = ttk.Frame(cdp_box)
            refresh_row.grid(row=1, column=0, columnspan=8, sticky=tk.W, pady=2)
            ttk.Checkbutton(
                refresh_row,
                text="先刷新候选池，出目标再抽（省钱；免费优先，付费受预算限制）",
                variable=self.gacha_cdp_refresh_var,
            ).pack(side=tk.LEFT, padx=4)
            ttk.Label(refresh_row, text="绿钻刷新预算(0=不付费)").pack(side=tk.LEFT, padx=(12, 4))
            ttk.Entry(refresh_row, textvariable=self.gacha_cdp_green_var, width=8).pack(
                side=tk.LEFT
            )
            ttk.Checkbutton(
                refresh_row,
                text="候选里有目标就用最大倍率抽",
                variable=self.gacha_cdp_maxbet_var,
            ).pack(side=tk.LEFT, padx=(16, 4))

        cdp_btns = ttk.Frame(cdp_box)
        cdp_btns.grid(row=2, column=0, columnspan=8, sticky=tk.W, pady=4)
        self.gacha_cdp_btn = ttk.Button(cdp_btns, text="开始直连抽卡", command=self.start_gacha_cdp)
        self.gacha_cdp_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(cdp_btns, text="停止直连抽卡", command=self.stop_gacha_cdp).pack(
            side=tk.LEFT, padx=2
        )

        self.gacha_cdp_status = tk.StringVar(value="需要先打开游戏里的酒馆/招募界面")
        ttk.Label(cdp_box, textvariable=self.gacha_cdp_status, wraplength=980, foreground="#555").grid(
            row=3, column=0, columnspan=8, sticky=tk.W
        )
        if self.dev_mode:
            ttk.Label(cdp_box, textvariable=self.gacha_pool_var, wraplength=980, foreground="#1a4d8f").grid(
                row=4, column=0, columnspan=8, sticky=tk.W
            )
            ttk.Label(cdp_box, textvariable=self.gacha_records_var, wraplength=980, foreground="#0a7a2f").grid(
                row=5, column=0, columnspan=8, sticky=tk.W
            )

        # 旧版「截图识别抽卡」参数只保留变量，界面已隐藏（后面重写再用）
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

        # 固定目标名单先建好，勾选状态从配置恢复
        self.reload_gacha_heroes()

    # ---------------- 游戏辅助 ----------------

    def _build_assist_tab(self, parent: ttk.Frame) -> None:
        assist = normalize_assist(self.config.get("assist"))

        if self.dev_mode:
            ttk.Label(
                parent,
                text=(
                    "直接调用游戏自己的接口（把 JS 运行时装进游戏页），不是截图找图："
                    "聊天里出现红包就立刻领、目标死亡后自动重进地图、祈愿到点自动领取并放回。"
                    " 打开游戏窗口并进入游戏主界面后会自动连上，不用点启动。\n"
                    "注意：游戏内部字段名每个版本都会重新随机（当前版本 903 个服务名全是 4 位随机码），"
                    "所以按版本存了映射表 app/assist_mapping.json。映射对不上时按钮会置灰并提示更新。"
                ),
                wraplength=980,
            ).pack(anchor=tk.W, pady=(0, 6))

        box = ttk.LabelFrame(parent, text="游戏辅助", padding=10)
        box.pack(fill=tk.X)
        box.columnconfigure(4, weight=1)

        self.assist_message_var = tk.StringVar(value="辅助已自动连接，等待游戏窗口…")
        ttk.Label(box, textvariable=self.assist_message_var, foreground="#1a4d8f").grid(
            row=0, column=0, columnspan=6, sticky=tk.W, pady=(0, 8)
        )

        ttk.Label(box, text="小怪死亡后等待").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.assist_mob_var = tk.StringVar(value=_seconds_text(assist.get("mob_seconds", 1)))
        ttk.Entry(box, textvariable=self.assist_mob_var, width=8, justify="center").grid(
            row=1, column=1, padx=6
        )
        ttk.Label(box, text="秒").grid(row=1, column=2, sticky=tk.W)
        self.assist_mob_btn = ttk.Button(
            box, text="开启", width=10, command=lambda: self.toggle_assist("mob")
        )
        self.assist_mob_btn.grid(row=1, column=3, padx=8)
        self.assist_mob_status = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.assist_mob_status, foreground="#666").grid(
            row=1, column=4, columnspan=2, sticky=tk.W
        )

        ttk.Label(box, text="BOSS 死亡后等待").grid(row=2, column=0, sticky=tk.W, pady=4)
        self.assist_boss_var = tk.StringVar(value=_seconds_text(assist.get("boss_seconds", 3)))
        ttk.Entry(box, textvariable=self.assist_boss_var, width=8, justify="center").grid(
            row=2, column=1, padx=6
        )
        ttk.Label(box, text="秒").grid(row=2, column=2, sticky=tk.W)
        self.assist_boss_btn = ttk.Button(
            box, text="开启", width=10, command=lambda: self.toggle_assist("boss")
        )
        self.assist_boss_btn.grid(row=2, column=3, padx=8)
        self.assist_gold_var = tk.StringVar(value="BOSS 金币 0")
        ttk.Label(box, textvariable=self.assist_gold_var, foreground="#a35a00").grid(
            row=2, column=4, sticky=tk.W, padx=6
        )
        self.assist_boss_status = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.assist_boss_status, foreground="#666").grid(
            row=2, column=5, sticky=tk.W
        )

        ttk.Separator(box, orient=tk.HORIZONTAL).grid(
            row=3, column=0, columnspan=6, sticky=tk.EW, pady=8
        )

        ttk.Label(box, text="祈愿领取").grid(row=4, column=0, sticky=tk.W, pady=4)
        self.assist_wish_btn = ttk.Button(
            box, text="开启", width=10, command=lambda: self.toggle_assist("wish")
        )
        self.assist_wish_btn.grid(row=4, column=3, padx=8)
        self.assist_wish_status = tk.StringVar(value="未检查")
        ttk.Label(box, textvariable=self.assist_wish_status, foreground="#666").grid(
            row=4, column=4, columnspan=2, sticky=tk.W
        )

        ttk.Label(box, text="快速抢红包").grid(row=5, column=0, sticky=tk.W, pady=4)
        self.assist_green_var = tk.StringVar(value="已领 0 个红包")
        ttk.Label(box, textvariable=self.assist_green_var, foreground="#0a7a2f").grid(
            row=5, column=1, columnspan=2, sticky=tk.W
        )
        self.assist_red_btn = ttk.Button(
            box, text="开启", width=10, command=lambda: self.toggle_assist("redPacket")
        )
        self.assist_red_btn.grid(row=5, column=3, padx=8)
        self.assist_red_status = tk.StringVar(value="尚未开启")
        ttk.Label(box, textvariable=self.assist_red_status, foreground="#666").grid(
            row=5, column=4, columnspan=2, sticky=tk.W
        )

        # ---- 战斗参数（参考工具同款：倍伤 / 视野 / 技能冷却） ----
        ttk.Separator(box, orient=tk.HORIZONTAL).grid(
            row=6, column=0, columnspan=6, sticky=tk.EW, pady=8
        )

        ttk.Label(box, text="英雄倍伤").grid(row=7, column=0, sticky=tk.W, pady=4)
        self.assist_damage_var = tk.StringVar(
            value=_seconds_text(assist.get("damage_value", 2))
        )
        ttk.Entry(box, textvariable=self.assist_damage_var, width=8, justify="center").grid(
            row=7, column=1, padx=6
        )
        ttk.Label(box, text="倍").grid(row=7, column=2, sticky=tk.W)
        self.assist_damage_btn = ttk.Button(
            box, text="开启", width=10, command=lambda: self.toggle_combat("damage")
        )
        self.assist_damage_btn.grid(row=7, column=3, padx=8)
        self.assist_damage_status = tk.StringVar(value="尚未开启")
        ttk.Label(
            box, textvariable=self.assist_damage_status, foreground="#666", wraplength=560
        ).grid(row=7, column=4, columnspan=2, sticky=tk.W)

        ttk.Label(box, text="视野").grid(row=8, column=0, sticky=tk.W, pady=4)
        self.assist_vision_var = tk.StringVar(
            value=_vision_text(assist.get("vision_mode", -1))
        )
        self.assist_vision_box = ttk.Combobox(
            box,
            textvariable=self.assist_vision_var,
            values=VISION_CHOICES,
            width=20,
            state="readonly",
        )
        self.assist_vision_box.grid(row=8, column=1, columnspan=2, sticky=tk.W, padx=6)
        self.assist_vision_btn = ttk.Button(
            box, text="应用", width=10, command=lambda: self.toggle_combat("vision")
        )
        self.assist_vision_btn.grid(row=8, column=3, padx=8)
        self.assist_vision_status = tk.StringVar(value="尚未开启")
        ttk.Label(
            box, textvariable=self.assist_vision_status, foreground="#666", wraplength=560
        ).grid(row=8, column=4, columnspan=2, sticky=tk.W)

        ttk.Label(box, text="技能冷却").grid(row=9, column=0, sticky=tk.W, pady=4)
        self.assist_cooldown_var = tk.StringVar(
            value=_seconds_text(assist.get("cooldown_seconds", 3))
        )
        ttk.Entry(
            box, textvariable=self.assist_cooldown_var, width=8, justify="center"
        ).grid(row=9, column=1, padx=6)
        ttk.Label(box, text="秒").grid(row=9, column=2, sticky=tk.W)
        self.assist_cooldown_btn = ttk.Button(
            box, text="开启", width=10, command=lambda: self.toggle_combat("cooldown")
        )
        self.assist_cooldown_btn.grid(row=9, column=3, padx=8)
        self.assist_cooldown_status = tk.StringVar(value="尚未开启")
        ttk.Label(
            box, textvariable=self.assist_cooldown_status, foreground="#666", wraplength=560
        ).grid(row=9, column=4, columnspan=2, sticky=tk.W)

        # ---- 快速复活：全灭后自动复活（道具 / 广告 / 回营地） ----
        ttk.Separator(box, orient=tk.HORIZONTAL).grid(
            row=10, column=0, columnspan=6, sticky=tk.EW, pady=8
        )

        ttk.Label(box, text="快速复活").grid(row=11, column=0, sticky=tk.W, pady=4)
        self.assist_revive_mode_var = tk.StringVar(
            value=_revive_text(assist.get("revive_mode", "item"))
        )
        ttk.Combobox(
            box,
            textvariable=self.assist_revive_mode_var,
            values=REVIVE_CHOICES,
            width=26,
            state="readonly",
        ).grid(row=11, column=1, sticky=tk.W, padx=6)
        ttk.Label(box, text="延迟").grid(row=11, column=2, sticky=tk.W)
        self.assist_revive_delay_var = tk.StringVar(
            value=_seconds_text(assist.get("revive_delay", 0.5))
        )
        ttk.Entry(
            box, textvariable=self.assist_revive_delay_var, width=6, justify="center"
        ).grid(row=11, column=3, sticky=tk.W, padx=4)
        ttk.Label(box, text="秒").grid(row=11, column=4, sticky=tk.W)
        self.assist_revive_btn = ttk.Button(
            box, text="开启", width=10, command=self.toggle_revive
        )
        self.assist_revive_btn.grid(row=11, column=5, padx=8)
        self.assist_revive_status = tk.StringVar(value="尚未开启")
        ttk.Label(
            box, textvariable=self.assist_revive_status, foreground="#666", wraplength=980
        ).grid(row=12, column=0, columnspan=6, sticky=tk.W)

        ttk.Label(box, text="英雄复活").grid(row=13, column=0, sticky=tk.W, pady=4)
        self.assist_hero_revive_var = tk.StringVar(
            value=_seconds_text(assist.get("hero_revive_seconds", 1))
        )
        ttk.Entry(
            box, textvariable=self.assist_hero_revive_var, width=8, justify="center"
        ).grid(row=13, column=1, sticky=tk.W, padx=6)
        ttk.Label(box, text="秒后").grid(row=13, column=2, sticky=tk.W)
        self.assist_hero_revive_mode_var = tk.StringVar(
            value=_hero_revive_text(assist.get("hero_revive_mode", "item"))
        )
        ttk.Combobox(
            box,
            textvariable=self.assist_hero_revive_mode_var,
            values=HERO_REVIVE_CHOICES,
            width=28,
            state="readonly",
        ).grid(row=13, column=3, sticky=tk.W, padx=6)
        self.assist_hero_revive_btn = ttk.Button(
            box, text="开启", width=10, command=self.toggle_hero_revive
        )
        self.assist_hero_revive_btn.grid(row=13, column=4, padx=8)
        self.assist_hero_revive_status = tk.StringVar(value="尚未开启")
        ttk.Label(
            box, textvariable=self.assist_hero_revive_status, foreground="#666", wraplength=980
        ).grid(row=14, column=0, columnspan=6, sticky=tk.W)

        # ---- 闪现寻怪：传送到最近的怪 / 指定怪 ----
        ttk.Label(box, text="闪现寻怪").grid(row=15, column=0, sticky=tk.W, pady=4)
        self.assist_blink_var = tk.StringVar(value=str(assist.get("blink_target", "")))
        ttk.Entry(box, textvariable=self.assist_blink_var, width=22).grid(
            row=15, column=1, columnspan=2, sticky=tk.W, padx=6
        )
        self.assist_blink_btn = ttk.Button(
            box, text="闪现", width=10, command=self.blink_assist
        )
        self.assist_blink_btn.grid(row=15, column=3, padx=8)
        self.assist_blink_status = tk.StringVar(value="留空 = 最近的怪；填名字/编号 = 指定怪")
        ttk.Label(
            box, textvariable=self.assist_blink_status, foreground="#666", wraplength=560
        ).grid(row=15, column=4, columnspan=2, sticky=tk.W)

        ttk.Label(
            box,
            text=(
                "倍伤 / 视野 / 冷却会改动游戏战斗数值，存在被判定异常的风险，默认关闭；快速复活走游戏自己的传送接口（服务端认可），不花道具/广告；"
                "开着倍伤 / 视野 / 冷却时不能开小怪 / BOSS 地图刷新（参考工具也是这个规则）。"
            ),
            foreground="#c62828",
            wraplength=980,
            justify=tk.LEFT,
        ).grid(row=16, column=0, columnspan=6, sticky=tk.W, pady=(6, 0))

        warn = ttk.Frame(parent)
        warn.pack(fill=tk.X, pady=10)
        ttk.Label(warn, text="温馨提示：辅助有风险，使用须谨慎！", foreground="#c62828").pack(
            side=tk.LEFT
        )
        ttk.Button(warn, text="全部关闭", width=12, command=self.stop_all_assist).pack(
            side=tk.RIGHT
        )
        ttk.Button(
            warn, text="立即回营地复活", width=16, command=self.revive_now
        ).pack(side=tk.RIGHT, padx=8)

        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=4)
        ttk.Button(row, text="重新连接", command=self.reconnect_assist).pack(side=tk.LEFT, padx=2)
        if self.dev_mode:
            # 只有开发时显示：诊断/自测是调试入口，发布版隐藏
            ttk.Button(row, text="诊断（查字段）", command=self.diagnose_assist).pack(
                side=tk.LEFT, padx=2
            )
            ttk.Button(row, text="自测重进", command=self.self_test_assist).pack(
                side=tk.LEFT, padx=2
            )
        ttk.Button(row, text="保存辅助设置", command=self.save_all).pack(side=tk.LEFT, padx=2)
        ttk.Label(
            row, text="辅助随界面自动连接，关掉界面就停止。", foreground="#666"
        ).pack(side=tk.LEFT, padx=12)

        self.root.after(800, self._tick_assist)

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
        revive_mode_var = getattr(self, "assist_revive_mode_var", None)
        if revive_mode_var is not None:
            assist["revive_mode"] = _revive_mode(revive_mode_var.get())
        hero_revive_var = getattr(self, "assist_hero_revive_var", None)
        if hero_revive_var is not None:
            try:
                assist["hero_revive_seconds"] = max(
                    1.0, min(60.0, float(hero_revive_var.get().strip()))
                )
            except (ValueError, AttributeError, TypeError):
                pass
        hero_revive_mode = getattr(self, "assist_hero_revive_mode_var", None)
        if hero_revive_mode is not None:
            assist["hero_revive_mode"] = _hero_revive_mode(hero_revive_mode.get())
        self.config["assist"] = assist

    def start_assist(self) -> None:
        self._read_assist_into_config()
        self.assist.set_names(self._assist_names())
        if not self.assist.running:
            self.assist.start()
            self.append_log("游戏辅助已自动连接：打开游戏窗口后会自己注入运行时")

    def _auto_connect_assist(self) -> None:
        """界面一打开就连上（和参考工具一样，不需要「启动辅助」这一步）。"""
        if self._assist_auto_started:
            return
        self._assist_auto_started = True
        try:
            self.start_assist()
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"游戏辅助自动连接失败：{exc}")
        self.root.after(600, self._auto_open_game_window)

    def _game_open_mode(self) -> str:
        mode = str(self.config.get("game_open_mode") or "ask").strip().lower()
        if not ENABLE_BROWSER_OPEN and mode == "browser":
            return "ask"
        return mode if mode in ("ask", "builtin", "browser") else "ask"

    def _auto_open_game_window(self) -> None:
        """启动时打开游戏：默认直接开内置窗口（系统浏览器已关闭）。"""
        if not normalize_assist(self.config.get("assist")).get("auto_open_window", True):
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
            btn, status, label = (
                self.assist_damage_btn,
                self.assist_damage_status,
                "英雄倍伤",
            )
        elif name == "vision":
            mode = _vision_mode(self.assist_vision_var.get())
            if mode < 0 and not enabled:
                self.append_log("视野已经是关闭状态")
                return
            send = mode
            btn, status, label = (
                self.assist_vision_btn,
                self.assist_vision_status,
                "视野修改",
            )
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
            btn, status, label = (
                self.assist_cooldown_btn,
                self.assist_cooldown_status,
                "技能冷却",
            )

        self._read_assist_into_config()
        self.assist.set_feature(name, send)
        closing = (send < 0) if name == "vision" else (send <= 0)
        if name == "vision":
            btn.configure(text="应用")
        else:
            btn.configure(text="开启" if closing else "关闭")
        status.set("正在关闭…" if closing else "正在应用…")
        self.append_log(f"已发送：{'关闭' if closing else '开启'}{label}")

    def revive_now(self) -> None:
        """手动触发一轮：回营地复活 → 飞回原位。"""
        if not self.assist.running:
            self.start_assist()
        self.assist.set_feature("reviveNow", 1)
        self.append_log("已发送：立即回营地复活（营地满血 → 飞回原位）")

    def toggle_revive(self) -> None:
        """快速复活：开启时把「方式 + 延迟」一起发过去，再点一次就是关闭。"""
        if not self.assist.running:
            self.start_assist()
        revive = (self.assist.state or {}).get("revive") or {}
        enabled = bool(revive.get("enabled"))
        if enabled:
            self.assist.set_feature("revive", 0)
            self.assist_revive_btn.configure(text="开启")
            self.assist_revive_status.set("正在关闭…")
            self.append_log("已发送：关闭快速复活")
            return
        try:
            delay = float(self.assist_revive_delay_var.get().strip())
        except (ValueError, AttributeError):
            messagebox.showerror("参数错误", "复活延迟需要是数字")
            return
        if not 0 <= delay <= 10:
            messagebox.showerror("参数错误", "复活延迟需要在 0 - 10 秒之间")
            return
        mode = _revive_mode(self.assist_revive_mode_var.get())
        self._read_assist_into_config()
        self.assist.set_feature("revive", {"mode": mode, "delay": delay})
        self.assist_revive_btn.configure(text="关闭")
        self.assist_revive_status.set("正在开启…")
        label = {"instant": "立即复活", "tp": "传送回营地复活", "free": "免费回营地复活", "item": "道具复活", "ad": "广告复活", "camp": "回营地复活"}.get(mode, mode)
        self.append_log(f"已发送：开启快速复活（{label}，延迟 {delay:g} 秒）")

    def blink_assist(self) -> None:
        """闪现到最近的怪（输入框填名字/编号就传送到指定怪）。"""
        if not self.assist.running:
            self.start_assist()
        target = self.assist_blink_var.get().strip()
        self._read_assist_into_config()
        self.assist.set_feature("blink", target)
        self.assist_blink_status.set("正在闪现…" if not target else f"正在找「{target}」…")
        self.append_log(
            "已发送：闪现到" + (f"「{target}」" if target else "最近的怪")
        )

    def toggle_hero_revive(self) -> None:
        """单英雄复活时间：死一个多久原地站起来。开着再点 = 关闭。"""
        if not self.assist.running:
            self.start_assist()
        hero = (self.assist.state or {}).get("heroRevive") or {}
        if hero.get("enabled"):
            self.assist.set_feature("heroRevive", 0)
            self.assist_hero_revive_btn.configure(text="开启")
            self.assist_hero_revive_status.set("正在关闭…")
            self.append_log("已发送：关闭英雄复活时间（恢复游戏原值）")
            return
        try:
            seconds = float(self.assist_hero_revive_var.get().strip())
        except (ValueError, AttributeError):
            messagebox.showerror("参数错误", "复活时间需要是数字")
            return
        if not 1 <= seconds <= 60:
            messagebox.showerror("参数错误", "复活时间需要在 1 - 60 秒之间")
            return
        mode = _hero_revive_mode(self.assist_hero_revive_mode_var.get())
        self._read_assist_into_config()
        self.assist.set_feature(
            "heroRevive",
            {
                "seconds": seconds,
                "ad": mode == "ad",
                "campfly": mode == "campfly",
                "timer": mode == "timer",
            },
        )
        self.assist_hero_revive_btn.configure(text="关闭")
        self.assist_hero_revive_status.set("正在开启…")
        label = {
                "ad": "向服务器申请广告复活",
            "campfly": "回营地复活并飞回原位",
        }.get(mode, "向服务器申请道具复活")
        self.append_log(f"已发送：我方英雄倒地 {seconds:g} 秒后{label}")

    def _update_assist_labels(self) -> None:
        state = self.assist.state or {}
        if not state:
            self.assist_message_var.set("辅助已自动连接，等待游戏窗口（点上方「打开游戏窗口」即可）")
            return
        gap = bool(state.get("missingMapping")) and not state.get("ready")
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
        hero_revive = state.get("heroRevive") or {}

        blink = state.get("blink") or {}
        self.assist_blink_btn.configure(
            state=tk.DISABLED if gap else tk.NORMAL,
            text="闪现中…" if blink.get("busy") else "闪现",
        )
        self.assist_blink_status.set(str(blink.get("reason") or "留空 = 最近的怪；填名字/编号 = 指定怪"))

        for button in (self.assist_mob_btn, self.assist_boss_btn,
                       self.assist_red_btn, self.assist_wish_btn,
                       self.assist_damage_btn, self.assist_vision_btn,
                       self.assist_cooldown_btn, self.assist_revive_btn,
                       self.assist_hero_revive_btn):
            button.configure(state=tk.DISABLED if gap else tk.NORMAL)

        self.assist_mob_btn.configure(text="关闭" if mob.get("enabled") else "开启")
        self.assist_boss_btn.configure(text="关闭" if boss.get("enabled") else "开启")
        self.assist_red_btn.configure(text="关闭" if red.get("enabled") else "开启")
        self.assist_wish_btn.configure(text="关闭" if wish.get("enabled") else "开启")
        self.assist_damage_btn.configure(text="关闭" if damage.get("enabled") else "开启")
        self.assist_vision_btn.configure(text="恢复" if vision.get("enabled") else "应用")
        self.assist_cooldown_btn.configure(text="关闭" if cooldown.get("enabled") else "开启")
        self.assist_revive_btn.configure(text="关闭" if revive.get("enabled") else "开启")
        self.assist_hero_revive_btn.configure(
            text="关闭" if hero_revive.get("enabled") else "开启"
        )

        self.assist_mob_status.set(str(mob.get("reason") or "待接入"))
        self.assist_boss_status.set(str(boss.get("reason") or "待接入"))
        self.assist_red_status.set(str(red.get("reason") or ("需要版本映射" if gap else "尚未开启")))
        self.assist_wish_status.set(str(wish.get("reason") or "待接入"))
        self.assist_damage_status.set(str(damage.get("reason") or ("需要版本映射" if gap else "尚未开启")))
        self.assist_vision_status.set(str(vision.get("reason") or ("需要版本映射" if gap else "尚未开启")))
        self.assist_cooldown_status.set(str(cooldown.get("reason") or ("需要版本映射" if gap else "尚未开启")))
        revive_reason = str(revive.get("reason") or ("需要版本映射" if gap else "尚未开启"))
        if revive.get("count"):
            revive_reason += f" · 今日复活 {int(revive.get('count') or 0)} 次"
        self.assist_revive_status.set(revive_reason)
        self.assist_hero_revive_status.set(
            str(hero_revive.get("reason") or ("需要版本映射" if gap else "尚未开启"))
        )

        green, gold = self.assist.display_stats()
        self.assist_green_var.set(f"已领 {green} 个红包")
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
            hero_revive = info.get("heroRevive")
            if isinstance(hero_revive, dict):
                if hero_revive.get("error"):
                    self.append_log(f"  单英雄复活：{hero_revive['error']}")
                else:
                    self.append_log(
                        "  单英雄复活：接口 %s，当前我方英雄 %s 个（倒地 %s 个）"
                        % (
                            "可用" if hero_revive.get("reviveMethod") else "缺失",
                            hero_revive.get("heroes", 0),
                            hero_revive.get("dead", 0),
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
            self._game_proc = open_game_window(url=url, width=width, height=height)
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

    def _set_all_heroes(self, value: bool) -> None:
        for var in getattr(self, "_gacha_hero_vars", {}).values():
            try:
                var.set(value)
            except tk.TclError:
                pass

    def _fixed_checked_ids(self) -> Optional[set]:
        section = self.config.get("gacha_cdp") or {}
        checked = section.get("checked")
        if isinstance(checked, list):
            ids = set()
            for item in checked:
                text = str(item).strip()
                if text.isdigit():
                    ids.add(int(text))
            return ids
        return None

    def reload_gacha_heroes(self) -> None:
        fixed = list(DEFAULT_FIXED_HEROES)
        checked = self._fixed_checked_ids()
        for widget in self._gacha_hero_inner.winfo_children():
            widget.destroy()
        self._gacha_hero_vars.clear()
        self._gacha_hero_counter_vars.clear()
        self._hero_name_cache = {hero_id: name for hero_id, name in fixed}
        for index, (hero_id, name) in enumerate(fixed):
            cell = ttk.Frame(self._gacha_hero_inner)
            cell.grid(row=index // 3, column=index % 3, sticky=tk.W, padx=6, pady=1)
            var = tk.BooleanVar(value=(checked is None or hero_id in checked))
            ttk.Checkbutton(cell, text=name, variable=var).pack(side=tk.LEFT)
            counter = tk.StringVar(value="出现 0 / 中 0")
            ttk.Label(cell, textvariable=counter, foreground="#777", width=12).pack(
                side=tk.LEFT, padx=(6, 0)
            )
            self._gacha_hero_vars[hero_id] = var
            self._gacha_hero_counter_vars[hero_id] = counter
        self.gacha_hero_count.set(
            f"固定名单 {len(fixed)} 个（已勾 {sum(1 for v in self._gacha_hero_vars.values() if v.get())}）"
        )

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
            messagebox.showinfo("提示", "请勾选目标英雄，或勾选「只保留红色」")
            return

        # 勾选状态落盘，下次打开还在
        try:
            self._read_form_into_config()
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
        )
        if not result.get("ok"):
            self.gacha_cdp_status.set("开始失败：" + str(result.get("message")))
            self.append_log("直连抽卡开始失败：" + str(result.get("message")))
            return
        self.gacha_cdp_btn.configure(text="抽卡中…")
        self.gacha_cdp_status.set("已开始，读取招募面板…")
        self.append_log("已开始直连抽卡")
        self.root.after(600, self._tick_gacha_cdp)

    def stop_gacha_cdp(self) -> None:
        try:
            self._gacha_cdp().stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.gacha_cdp_btn.configure(text="开始直连抽卡")
        except tk.TclError:
            pass
        try:
            self.gacha_cdp_status.set("已停止")
        except tk.TclError:
            pass

    def _tick_gacha_cdp(self) -> None:
        try:
            snap = self._gacha_cdp().snapshot()
        except Exception:  # noqa: BLE001
            snap = None
        if snap:
            stats = snap.get("stats") or {}
            text = (
                f"抽 {stats.get('draws', 0)} / 放弃 {stats.get('abandoned', 0)}"
                f" / 保留 {stats.get('kept', 0)}"
            )
            if stats.get("refreshes"):
                text += f" / 刷新 {stats.get('refreshes')}（绿钻 {stats.get('greenSpent', 0)}）"
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
            for hero_id, var in getattr(self, "_gacha_hero_counter_vars", {}).items():
                info = counters.get(str(hero_id)) or counters.get(hero_id) or {}
                appear = int(info.get("appear") or 0)
                hit = int(info.get("hit") or 0)
                try:
                    var.set(f"出现 {appear} / 中 {hit}")
                except tk.TclError:
                    pass

            records = snap.get("records") or []
            if records:
                records = sorted(
                    records,
                    key=lambda r: (-int(r.get("count") or 0), -int(r.get("quality") or 0)),
                )
                text_records = "，".join(
                    f"{r.get('name')}×{r.get('count')}" for r in records[:20]
                )
                try:
                    self.gacha_records_var.set("抽中：" + text_records)
                except tk.TclError:
                    pass
            else:
                try:
                    self.gacha_records_var.set("抽中：尚未出货")
                except tk.TclError:
                    pass
            if snap.get("action"):
                text += " | " + str(snap.get("action"))
            if snap.get("stopReason"):
                text += " | " + str(snap.get("stopReason"))
            if not snap.get("ready") and snap.get("stateReason"):
                text += " | " + str(snap.get("stateReason"))
            try:
                self.gacha_cdp_status.set(text)
            except tk.TclError:
                return
            if not snap.get("running"):
                try:
                    self.gacha_cdp_btn.configure(text="开始直连抽卡")
                except tk.TclError:
                    pass
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
