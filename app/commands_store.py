"""运行命令的多槽位存储（1-10）。

每个槽位保存一段脚本正文，标题取脚本第一行 `# xxx`。
文件：saves/commands_slots.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config_store import project_root
from .instruction_library import format_content, script_title

SLOT_COUNT = 10


def slots_path() -> Path:
    path = project_root() / "saves" / "commands_slots.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def empty_slot() -> Dict[str, Any]:
    return {"title": "", "content": ""}


def empty_slots() -> List[Dict[str, Any]]:
    return [empty_slot() for _ in range(SLOT_COUNT)]


def _normalize_slot(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        content = format_content(raw)
        return {"title": script_title(content), "content": content}
    if not isinstance(raw, dict):
        return empty_slot()
    content = format_content(str(raw.get("content") or ""))
    title = str(raw.get("title") or "").strip() or script_title(content)
    return {"title": title, "content": content}


def load_slots(default_content: str = "") -> Dict[str, Any]:
    """返回 {"slots": [...10], "active": 1..10}；文件不存在时用 default_content 初始化 1 号槽。"""
    data: Any = None
    path = slots_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None

    if isinstance(data, dict):
        raw_slots = data.get("slots")
        active = data.get("active")
    elif isinstance(data, list):
        raw_slots = data
        active = 1
    else:
        raw_slots = None
        active = 1

    slots = empty_slots()
    if isinstance(raw_slots, list):
        for i in range(min(SLOT_COUNT, len(raw_slots))):
            slots[i] = _normalize_slot(raw_slots[i])
    elif default_content:
        slots[0] = _normalize_slot(default_content)

    try:
        active_no = int(active)
    except (TypeError, ValueError):
        active_no = 1
    active_no = min(max(active_no, 1), SLOT_COUNT)
    return {"slots": slots, "active": active_no}


def save_slots(data: Dict[str, Any]) -> None:
    raw_slots = data.get("slots") if isinstance(data, dict) else None
    slots = empty_slots()
    if isinstance(raw_slots, list):
        for i in range(min(SLOT_COUNT, len(raw_slots))):
            slots[i] = _normalize_slot(raw_slots[i])
    try:
        active = int(data.get("active") if isinstance(data, dict) else 1)
    except (TypeError, ValueError):
        active = 1
    active = min(max(active, 1), SLOT_COUNT)
    payload = {"active": active, "slots": slots}
    slots_path().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def set_slot(slots: List[Dict[str, Any]], index: int, content: str, title: Optional[str] = None) -> None:
    if index < 0 or index >= SLOT_COUNT:
        raise IndexError("槽位编号必须是 1-10")
    text = format_content(content)
    current_title = ""
    if 0 <= index < len(slots) and isinstance(slots[index], dict):
        current_title = str(slots[index].get("title") or "").strip()
    new_title = (title or "").strip()
    if not new_title:
        # 没显式改名就保留用户自定义名字，避免每次保存都被脚本首行覆盖
        new_title = current_title or script_title(text)
    slots[index] = {
        "title": new_title,
        "content": text,
    }


def slot_label(slot: Dict[str, Any], index: int) -> str:
    content = str(slot.get("content") or "")
    title = str(slot.get("title") or "").strip() or script_title(content)
    if not content.strip():
        if title:
            return "槽位 %d · %s · 空" % (index + 1, title)
        return "槽位 %d · 空" % (index + 1)
    lines = sum(1 for line in content.splitlines() if line.strip() and not line.strip().startswith("#"))
    return "槽位 %d · %s [%d步]" % (index + 1, title or "未命名", lines)
