"""生成 android_app 里用的运行时/映射文件。

用法：python tools\\gen_android_assets.py
游戏更新（字段映射改了）之后跑一次，再重新打包 APK。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.assist_script import RUNTIME_JS  # noqa: E402
from app.game_assist import load_mapping  # noqa: E402
from app.build_info import APP_VERSION  # noqa: E402
from app.gacha_script import build_runtime as build_gacha_runtime  # noqa: E402

OUT = ROOT / "android_app" / "js"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mapping = load_mapping()

    (OUT / "runtime.js").write_text(
        "window.ZZ_RUNTIME = " + json.dumps(RUNTIME_JS, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (OUT / "mapping.js").write_text(
        "window.ZZ_MAPPING = " + json.dumps(mapping, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    renames = {
        build: (data.get("runtimeNames") or {})
        for build, data in (mapping.get("builds") or {}).items()
    }
    (OUT / "renames.js").write_text(
        "window.ZZ_RENAMES = " + json.dumps(renames, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (OUT / "version.js").write_text(
        "window.ZZ_APP_VERSION = " + json.dumps(APP_VERSION, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (OUT / "gacha.js").write_text(
        "window.ZZ_GACHA_RUNTIME = " + json.dumps(build_gacha_runtime(), ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )

    for name in ("runtime.js", "mapping.js", "renames.js", "version.js", "gacha.js"):
        size = (OUT / name).stat().st_size / 1024
        print(f"{name}: {size:.1f} KB")


if __name__ == "__main__":
    main()
