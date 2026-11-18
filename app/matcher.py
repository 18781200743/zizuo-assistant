"""模板匹配找图。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np


@dataclass
class MatchResult:
    found: bool
    x: int = 0
    y: int = 0
    score: float = 0.0
    width: int = 0
    height: int = 0

    @property
    def center(self) -> Tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def load_template(path: str) -> Optional[np.ndarray]:
    file = Path(path)
    if not file.exists():
        return None
    img = cv2.imdecode(np.fromfile(str(file), dtype=np.uint8), cv2.IMREAD_COLOR)
    return img


def match_template(
    screen_bgr: np.ndarray,
    template_bgr: np.ndarray,
    threshold: float = 0.85,
    multi_scale: bool = True,
) -> MatchResult:
    if screen_bgr is None or template_bgr is None:
        return MatchResult(False)

    screen = np.ascontiguousarray(screen_bgr)
    template = np.ascontiguousarray(template_bgr)
    sh, sw = screen.shape[:2]
    th, tw = template.shape[:2]
    if th < 4 or tw < 4 or sh < 4 or sw < 4:
        return MatchResult(False)

    gray_screen = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    gray_tpl = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

    # 小图/文字按钮：窗口缩放后尺寸常对不上，多尺度扫一遍
    scales = [1.0]
    if multi_scale:
        scales = [0.70, 0.80, 0.90, 1.0, 1.10, 1.25, 1.40, 1.60]

    best = MatchResult(False)
    use_sqdiff = float(gray_tpl.std()) < 2.0

    for scale in scales:
        w = max(4, int(round(tw * scale)))
        h = max(4, int(round(th * scale)))
        if w >= sw or h >= sh:
            continue
        if scale == 1.0:
            tpl = gray_tpl
        else:
            interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            tpl = cv2.resize(gray_tpl, (w, h), interpolation=interp)

        if use_sqdiff:
            result = cv2.matchTemplate(gray_screen, tpl, cv2.TM_SQDIFF_NORMED)
            min_val, _, min_loc, _ = cv2.minMaxLoc(result)
            score = float(1.0 - min_val)
            loc = min_loc
        else:
            result = cv2.matchTemplate(gray_screen, tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            score = float(max_val)
            loc = max_loc

        if score > best.score:
            best = MatchResult(False, loc[0], loc[1], score, w, h)

    best.found = best.score >= threshold
    return best
