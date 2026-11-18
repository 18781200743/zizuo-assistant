"""1-10 存档槽（参考 x_blyx 的 script.json 结构）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .config_store import project_root
from .script_codec import script_to_lines, slot_title

SLOT_COUNT = 10


def saves_dir() -> Path:
    path = project_root() / "saves"
    path.mkdir(parents=True, exist_ok=True)
    return path


def script_path() -> Path:
    return saves_dir() / "script.json"


def empty_slots() -> List[List[str]]:
    return [[] for _ in range(SLOT_COUNT)]


def load_slots() -> List[List[str]]:
    path = script_path()
    if not path.exists():
        slots = empty_slots()
        save_slots(slots)
        return slots
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        data = []
    slots: List[List[str]] = []
    for i in range(SLOT_COUNT):
        if i < len(data) and isinstance(data[i], list):
            slots.append(script_to_lines(data[i]))
        else:
            slots.append([])
    return slots


def save_slots(slots: List[List[str]]) -> None:
    normalized = empty_slots()
    for i in range(SLOT_COUNT):
        if i < len(slots):
            normalized[i] = script_to_lines(slots[i])
    script_path().write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def set_slot(slots: List[List[str]], index: int, script: str | List[str]) -> None:
    if index < 0 or index >= SLOT_COUNT:
        raise IndexError("存档编号必须是 1-10")
    slots[index] = script_to_lines(script)


def slot_label(slots: List[List[str]], index: int) -> str:
    lines = slots[index] if 0 <= index < len(slots) else []
    if not lines:
        return f"存档{index + 1}  （空）"
    title = slot_title(lines, index)
    return f"存档{index + 1}  {title}  [{len(lines)}行]"
