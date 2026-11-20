"""生成安卓用的抽卡运行时。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.gacha_script import GACHA_JS  # noqa: E402

OUT = ROOT / "android_app" / "js" / "gacha.js"
OUT.write_text(
    "window.ZZ_GACHA_RUNTIME = " + json.dumps(GACHA_JS, ensure_ascii=False) + ";\n",
    encoding="utf-8",
    newline="\n",
)
print("gacha.js", round(OUT.stat().st_size / 1024, 1), "KB")
