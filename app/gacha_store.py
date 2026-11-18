"""抽卡配置常量与结果统计读写。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 当前可用英雄（人物图片目录有新图会自动加入）
_DEFAULT_CARD_FALLBACK: List[str] = ["李白", "狂战士", "蜘蛛", "龙三"]


def project_root() -> Path:
    """与 config_store 一致：源码根目录 / 打包后 exe 旁。"""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resolve_hero_images_dir() -> Path:
    """便携查找顺序：程序旁 人物图片 → 旧路径 → templates。"""
    candidates = [
        project_root() / "人物图片",
        Path(r"F:\F\game\人物图片"),  # 兼容本机旧目录
    ]
    for path in candidates:
        if path.exists():
            return path
    fallback = project_root() / "人物图片"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


# 人物图片目录（英雄脸 + 按钮模板；可直接发给别人）
HERO_IMAGES_DIR = resolve_hero_images_dir()

# 同目录下的按钮/非英雄文件名（不进英雄勾选）
_UI_NAME_BLOCKLIST = {
    "最大",
    "招募",
    "金币招募",
    "金币招募300",
    "金币图标",
    "招募按钮",
    "宝石招募",
    "绿砖",
    "抽卡按钮",
    "刷新",
    "放弃",
    "自动",
    "减号",
}


def _is_hero_image(stem: str) -> bool:
    if not stem or stem.startswith("_") or stem.startswith("."):
        return False
    if stem in _UI_NAME_BLOCKLIST:
        return False
    return True


def _scan_hero_names() -> List[str]:
    names: List[str] = []
    hero_dir = resolve_hero_images_dir()
    if hero_dir.exists():
        for path in sorted(hero_dir.iterdir()):
            if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".bmp"):
                continue
            if not path.is_file():
                continue
            if not _is_hero_image(path.stem):
                continue
            names.append(path.stem)
    return names or list(_DEFAULT_CARD_FALLBACK)


CARD_CATALOG: List[str] = _scan_hero_names()

# 常驻招募界面按钮模板名（与人物图同目录）
UI_TEMPLATES = {
    # 金币招募：完整按钮 + 仅金币图标（兼容100/300）
    "recruit_coin": ["金币招募", "金币图标", "金币招募300", "招募", "招募按钮"],
    # 绿砖/宝石开抽（默认禁用）
    "recruit_gem": ["宝石招募", "绿砖", "抽卡按钮"],
    "max": ["最大"],
    "refresh": ["刷新"],
    "abandon": ["放弃"],
    "minus": ["减号"],
}

DEFAULT_GACHA: Dict[str, Any] = {
    "enabled_cards": [],
    "slot": 1,
    "max_draws": 0,
    "settle_ms": 2800,
    "match_threshold": 0.82,
    "no_hit_action": "正常抽卡",  # 无目标：直接点金币100，不点最大
    "use_green_gem": False,  # 禁止点绿砖/宝石按钮
    "use_max_button": True,  # 有目标：先点最大再金币抽（100→300）
    "stop_on_hit": False,
}


def cards_dir() -> Path:
    """优先用人物图片目录；没有则回退 templates/cards。"""
    global HERO_IMAGES_DIR
    HERO_IMAGES_DIR = resolve_hero_images_dir()
    if HERO_IMAGES_DIR.exists():
        return HERO_IMAGES_DIR
    path = project_root() / "templates" / "cards"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ui_dir() -> Path:
    """按钮图也先在人物图片目录找，再 templates/ui。"""
    global HERO_IMAGES_DIR
    HERO_IMAGES_DIR = resolve_hero_images_dir()
    if HERO_IMAGES_DIR.exists():
        return HERO_IMAGES_DIR
    path = project_root() / "templates" / "ui"
    path.mkdir(parents=True, exist_ok=True)
    return path


def refresh_card_catalog() -> List[str]:
    global CARD_CATALOG, HERO_IMAGES_DIR
    HERO_IMAGES_DIR = resolve_hero_images_dir()
    CARD_CATALOG = _scan_hero_names()
    return list(CARD_CATALOG)


def card_template_path(name: str) -> Path:
    return cards_dir() / f"{name}.png"


def list_available_card_templates() -> Dict[str, Path]:
    refresh_card_catalog()
    found: Dict[str, Path] = {}
    search_dirs = []
    if HERO_IMAGES_DIR.exists():
        search_dirs.append(HERO_IMAGES_DIR)
    local = project_root() / "templates" / "cards"
    if local.exists():
        search_dirs.append(local)
    for name in CARD_CATALOG:
        for folder in search_dirs:
            hit = None
            for ext in (".png", ".jpg", ".jpeg", ".bmp"):
                alt = folder / f"{name}{ext}"
                if alt.exists():
                    hit = alt
                    break
            if hit is not None:
                found[name] = hit
                break
    return found


def find_ui_template(aliases: List[str]) -> Optional[Path]:
    folders = []
    if HERO_IMAGES_DIR.exists():
        folders.append(HERO_IMAGES_DIR)
    folders.append(project_root() / "templates" / "ui")
    folders.append(project_root() / "templates")
    for alias in aliases:
        for folder in folders:
            if not folder.exists():
                continue
            for ext in (".png", ".jpg", ".jpeg", ".bmp"):
                path = folder / f"{alias}{ext}"
                if path.exists():
                    return path
    return None


def list_ui_templates(aliases: List[str]) -> List[Path]:
    found: List[Path] = []
    seen = set()
    for alias in aliases:
        path = find_ui_template([alias])
        if path is None:
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        found.append(path)
    return found


def list_ui_template_status() -> Dict[str, Optional[Path]]:
    return {key: find_ui_template(names) for key, names in UI_TEMPLATES.items()}


def normalize_gacha(raw: Any) -> Dict[str, Any]:
    data = DEFAULT_GACHA.copy()
    if isinstance(raw, dict):
        data.update(raw)
    enabled = data.get("enabled_cards") or []
    if not isinstance(enabled, list):
        enabled = []
    catalog = set(refresh_card_catalog())
    data["enabled_cards"] = [str(x) for x in enabled if str(x) in catalog]
    try:
        slot = int(data.get("slot", 1))
    except (TypeError, ValueError):
        slot = 1
    data["slot"] = max(1, min(10, slot))
    try:
        data["max_draws"] = max(0, int(data.get("max_draws", 0)))
    except (TypeError, ValueError):
        data["max_draws"] = 0
    try:
        data["settle_ms"] = max(0, int(data.get("settle_ms", 2800)))
    except (TypeError, ValueError):
        data["settle_ms"] = 2800
    try:
        data["match_threshold"] = float(data.get("match_threshold", 0.82))
    except (TypeError, ValueError):
        data["match_threshold"] = 0.82
    # 无目标只允许正常金币抽，避免旧「放弃」模板误点「最大」
    data["no_hit_action"] = "正常抽卡"
    data["use_green_gem"] = bool(data.get("use_green_gem", False))
    data["use_max_button"] = bool(data.get("use_max_button", True))
    data["stop_on_hit"] = bool(data.get("stop_on_hit", False))
    return data


def gacha_result_path() -> Path:
    path = project_root() / "saves"
    path.mkdir(parents=True, exist_ok=True)
    return path / "gacha_result.json"


def load_gacha_results() -> List[Dict[str, Any]]:
    path = gacha_result_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return data
    return []


def append_gacha_result(session: Dict[str, Any]) -> None:
    rows = load_gacha_results()
    rows.append(session)
    gacha_result_path().write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def new_gacha_session(enabled_cards: List[str]) -> Dict[str, Any]:
    # 只统计勾选的目标卡，其他英雄不记
    counts = {name: 0 for name in enabled_cards}
    return {
        "开始时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "结束时间": "",
        "目标卡": list(enabled_cards),
        "结果": {
            **counts,
            "总次数": 0,
            "目标命中次数": 0,
            "命中目标": [],
        },
    }
