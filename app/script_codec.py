"""录制步骤 ↔ 脚本代码（可复制到存档）。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


_CLICK_RE = re.compile(
    r"^(?:点击|单击|左键|click)\s*\(\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*\)\s*$",
    re.IGNORECASE,
)
_RCLICK_RE = re.compile(
    r"^(?:右键|右击|rclick)\s*\(\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*\)\s*$",
    re.IGNORECASE,
)
_WAIT_RE = re.compile(
    r"^(?:等待|延时|wait)\s*\(\s*([+-]?\d+(?:\.\d+)?)\s*\)\s*$",
    re.IGNORECASE,
)
_FIND_RE = re.compile(
    r"^(?:找图点击|找图|图像点击|match)\s*\(\s*([^)]+?)\s*\)\s*$",
    re.IGNORECASE,
)
_SIZE_RE = re.compile(
    r"size\s*=\s*(\d+)\s*[xX×]\s*(\d+)",
    re.IGNORECASE,
)


def steps_to_lines(
    steps: List[Dict[str, Any]],
    name: str = "",
    window_size: Tuple[int, int] | None = None,
) -> List[str]:
    lines: List[str] = []
    if name:
        lines.append(f"# {name}")
    if window_size:
        w, h = window_size
        lines.append(f"# relative=1 size={w}x{h}")
    elif any(s.get("relative") for s in steps):
        lines.append("# relative=1")

    pending_wait_s = 0.0
    for step in steps:
        delay_ms = int(step.get("delay_ms", 0) or 0)
        if delay_ms > 0:
            pending_wait_s = delay_ms / 1000.0

        if pending_wait_s > 0:
            text = f"{pending_wait_s:.3f}".rstrip("0").rstrip(".")
            lines.append(f"等待({text})")
            pending_wait_s = 0.0

        mode = str(step.get("mode") or "click")
        if mode in ("find", "template", "找图"):
            image = str(step.get("image") or step.get("name") or "").strip()
            if image:
                lines.append(f"找图点击({image})")
            continue

        x = step.get("x", 0)
        y = step.get("y", 0)
        button = str(step.get("button") or "left").lower()
        if button == "right":
            lines.append(f"右键({x},{y})")
        else:
            lines.append(f"点击({x},{y})")
    return lines


def steps_to_script(
    steps: List[Dict[str, Any]],
    name: str = "",
    window_size: Tuple[int, int] | None = None,
) -> str:
    return "\n".join(steps_to_lines(steps, name=name, window_size=window_size))


def script_to_lines(script: str | List[str]) -> List[str]:
    if isinstance(script, list):
        raw = [str(x) for x in script]
    else:
        raw = str(script or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [line.strip() for line in raw if line.strip()]


def lines_to_steps(lines: List[str]) -> List[Dict[str, Any]]:
    relative = True
    size: Tuple[int, int] | None = None
    steps: List[Dict[str, Any]] = []
    pending_delay_ms = 0

    for line in lines:
        if line.startswith("#"):
            low = line.lower()
            if "relative=0" in low or "relative = 0" in low:
                relative = False
            m = _SIZE_RE.search(line)
            if m:
                size = (int(m.group(1)), int(m.group(2)))
            continue

        m = _WAIT_RE.match(line)
        if m:
            pending_delay_ms += int(round(float(m.group(1)) * 1000))
            continue

        m = _FIND_RE.match(line)
        if m:
            image = m.group(1).strip().strip("\"'")
            steps.append(
                {
                    "mode": "find",
                    "image": image,
                    "delay_ms": pending_delay_ms,
                    "button": "left",
                    "clicks": 1,
                    "relative": True,
                    "x": 0,
                    "y": 0,
                }
            )
            pending_delay_ms = 0
            continue

        m = _RCLICK_RE.match(line)
        if m:
            x, y = _parse_xy(m.group(1), m.group(2), size)
            steps.append(
                {
                    "mode": "click",
                    "x": x,
                    "y": y,
                    "delay_ms": pending_delay_ms,
                    "button": "right",
                    "clicks": 1,
                    "relative": relative,
                }
            )
            pending_delay_ms = 0
            continue

        m = _CLICK_RE.match(line)
        if m:
            x, y = _parse_xy(m.group(1), m.group(2), size)
            steps.append(
                {
                    "mode": "click",
                    "x": x,
                    "y": y,
                    "delay_ms": pending_delay_ms,
                    "button": "left",
                    "clicks": 1,
                    "relative": relative,
                }
            )
            pending_delay_ms = 0
            continue

        raise ValueError(f"无法识别的脚本行: {line}")

    return steps


def script_to_steps(script: str | List[str]) -> List[Dict[str, Any]]:
    return lines_to_steps(script_to_lines(script))


def slot_title(lines: List[str], index: int) -> str:
    for line in lines:
        if line.startswith("#"):
            text = line.lstrip("#").strip()
            if text and not text.lower().startswith("relative"):
                return text
    if lines:
        return lines[0][:24]
    return "空"


def _parse_xy(xs: str, ys: str, size: Tuple[int, int] | None) -> Tuple[int, int]:
    xf = float(xs)
    yf = float(ys)
    if size and 0 <= xf <= 1.5 and 0 <= yf <= 1.5 and ("." in xs or "." in ys):
        return int(round(xf * size[0])), int(round(yf * size[1]))
    return int(round(xf)), int(round(yf))
