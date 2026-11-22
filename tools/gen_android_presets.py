"""生成安卓用的「同步助手」指令列表：sync_presets.js。

数据源：app/commands_presets.json（从 game_storage_sync 导出的自定义指令）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.instruction_library import load_sync_presets  # noqa: E402

OUT = ROOT / "android_app" / "js"


def main() -> None:
    items = [
        {
            "title": str(item.get("title") or ""),
            "content": str(item.get("content") or ""),
            "source": str(item.get("source") or "同步助手"),
            "category": int(item.get("category_id") or 3),
        }
        for item in load_sync_presets()
    ]
    (OUT / "sync_presets.js").write_text(
        "window.ZZ_SYNC_PRESETS = " + json.dumps(items, ensure_ascii=False) + ";\n",
        encoding="utf-8",
        newline="\n",
    )
    print("sync_presets.js:", len(items), "条")


if __name__ == "__main__":
    main()
