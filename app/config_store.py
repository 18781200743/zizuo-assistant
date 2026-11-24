"""配置读写。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .gacha_store import DEFAULT_GACHA, normalize_gacha


DEFAULT_ASSIST: Dict[str, Any] = {
    # 小怪 / BOSS 死亡后各等多少秒再重进地图
    "mob_seconds": 1,
    "boss_seconds": 3,
    # 开关状态：重启工具后自动恢复（以前重启就变「已关闭」）
    "mob_enabled": False,
    "boss_enabled": False,
    # 界面启动时没有游戏窗就自动开一个（关掉后仍可手动点「打开游戏窗口」）
    "auto_open_window": False,
    # 游戏窗 CDP 调试端口，与 game_window 里的 --remote-debugging-port 保持一致
    "cdp_port": 9229,
    # 战斗参数（参考工具同款，默认关闭）：
    #   damage_value   倍伤倍率（1 = 不改）
    #   vision_mode    视野：-1 关闭 / 0 不让怪发现 / 1 扩大索敌
    #   cooldown_seconds 技能冷却（秒）
    "damage_value": 2,
    "vision_mode": -1,
    "cooldown_seconds": 3,
    # 快速复活：mode = item 道具 / ad 广告 / camp 放弃回城；delay 秒
    "revive_mode": "tp",
    "revive_delay": 0.5,
    # 自动复活开关：重启助手后按这里恢复（之前重启一律被关掉）
    "revive_enabled": False,
    # 单英雄复活：倒地多少秒后动手；hero_revive_mode = item / ad / campfly
    "hero_revive_seconds": 1,
    "hero_revive_mode": "item",
    # BOSS 判定：怪物名包含这些关键字；也可以直接用怪物编号
    "boss_keywords": ["BOSS", "Boss", "boss", "首领", "魔王", "君主", "领主", "大魔王"],
    "boss_ids": [],
    # 字段名覆盖：游戏改名后在这里填 {"redClaim": "新的方法名"} 即可，不用改代码
    "names": {},
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "hotkey_start": "f8",
    "hotkey_stop": "f9",
    "hotkey_capture_point": "f7",
    "hotkey_record": "f6",
    "match_threshold": 0.85,
    "click_delay_ms": 300,
    "loop_interval_ms": 800,
    "target_window_title": "Zizuo游戏窗",
    "game_url": "https://mprogram.boomegg.cn/box/game/wx77200645d1c7f35f/h5?appid=wx77200645d1c7f35f&game_platform=h5",
    "game_width": 390,
    "game_height": 844,
    # 游戏窗后端：auto=先 WebView2，9229 起不来自动切 Edge；edge/chrome=直接用浏览器窗口
    "game_window_backend": "auto",
    # 打开游戏的方式：ask=每次问 / builtin=内置游戏窗口 / browser=系统浏览器
    "game_open_mode": "ask",
    "selected_slot": 1,
    "loop_playback": False,
    "tasks": [],
    "gacha": DEFAULT_GACHA.copy(),
    "assist": DEFAULT_ASSIST.copy(),
}


def normalize_assist(value: Any) -> Dict[str, Any]:
    """辅助配置容错：缺项补默认值，类型不对就丢弃该项。"""
    data = DEFAULT_ASSIST.copy()
    if isinstance(value, dict):
        for key, default in DEFAULT_ASSIST.items():
            if key not in value:
                continue
            item = value[key]
            if key in ("mob_enabled", "boss_enabled", "revive_enabled"):
                data[key] = bool(item)
            elif key in ("mob_seconds", "boss_seconds"):
                try:
                    seconds = float(item)
                except (TypeError, ValueError):
                    continue
                data[key] = max(0, min(600, seconds))
            elif key == "damage_value":
                try:
                    data[key] = max(1.0, min(70000.0, float(item)))
                except (TypeError, ValueError):
                    continue
            elif key == "cooldown_seconds":
                try:
                    data[key] = max(0.1, min(3600.0, float(item)))
                except (TypeError, ValueError):
                    continue
            elif key == "revive_delay":
                try:
                    data[key] = max(0.0, min(10.0, float(item)))
                except (TypeError, ValueError):
                    continue
            elif key == "hero_revive_seconds":
                try:
                    data[key] = max(1.0, min(60.0, float(item)))
                except (TypeError, ValueError):
                    continue
            elif key == "hero_revive_mode":
                text = str(item or "").strip().lower()
                # timer = 本地改复活倒计时，实测会被服务端抓，直接迁到服务器复活
                if text == "timer":
                    text = "item"
                if text in ("item", "ad", "campfly"):
                    data[key] = text
            elif key == "blink_target":
                if isinstance(item, str):
                    data[key] = item.strip()[:40]
            elif key == "revive_mode":
                text = str(item or "").strip().lower()
                # 旧的「放弃回城」(camp/free) 统一迁成更快、同样免费的「传送回营地」
                # 免费优先：旧模式统一成「传送回营地复活」
                if text in ("camp", "free", "instant"):
                    text = "tp"
                if text in ("tp", "instant", "item", "ad", "campfly"):
                    data[key] = text
            elif key == "vision_mode":
                try:
                    data[key] = int(item)
                except (TypeError, ValueError):
                    continue
                if data[key] not in (-1, 0, 1):
                    data[key] = -1
            elif key == "cdp_port":
                try:
                    data[key] = int(item)
                except (TypeError, ValueError):
                    continue
            elif key in ("boss_keywords", "boss_ids"):
                if isinstance(item, list):
                    data[key] = [x for x in item if isinstance(x, (str, int))]
            elif key == "names":
                if isinstance(item, dict):
                    data[key] = {str(k): str(v) for k, v in item.items() if isinstance(v, str)}
            elif key == "auto_open_window":
                data[key] = bool(item)
    return data


def project_root() -> Path:
    """源码目录；PyInstaller 打包后为 exe 所在目录。

    macOS 的 .app 放在 /Applications 下是不可写的，配置改放到
    ~/Library/Application Support/Zizuo助手。
    """
    import sys

    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support" / "Zizuo助手"
            base.mkdir(parents=True, exist_ok=True)
            return base
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return project_root() / "config.json"


def templates_dir() -> Path:
    path = project_root() / "templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config() -> Dict[str, Any]:
    path = config_path()
    if not path.exists():
        save_config(DEFAULT_CONFIG.copy())
        return DEFAULT_CONFIG.copy()
    data = json.loads(path.read_text(encoding="utf-8"))
    merged = DEFAULT_CONFIG.copy()
    merged.update(data)
    if not isinstance(merged.get("tasks"), list):
        merged["tasks"] = []
    try:
        slot = int(merged.get("selected_slot", 1))
    except (TypeError, ValueError):
        slot = 1
    merged["selected_slot"] = max(1, min(10, slot))
    merged["gacha"] = normalize_gacha(merged.get("gacha"))
    merged["assist"] = normalize_assist(merged.get("assist"))
    return merged


def save_config(data: Dict[str, Any]) -> None:
    config_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def new_task(
    name: str,
    mode: str = "template",
    image: str = "",
    x: int = 0,
    y: int = 0,
    enabled: bool = True,
    steps: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "name": name,
        "enabled": enabled,
        "mode": mode,  # template | point | record
        "image": image,
        "x": x,
        "y": y,
        "offset_x": 0,
        "offset_y": 0,
        "clicks": 1,
        "steps": steps or [],
    }


def list_tasks(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(config.get("tasks") or [])
