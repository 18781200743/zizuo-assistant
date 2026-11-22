"""扫描代码里残留的游戏内部名，和 assist_mapping.json 对照。

游戏每次发版都会重新混淆内部名。业务代码只应该通过映射字段访问游戏，
不应该再写死 VKXX / AGDA / ZAVH 这类名字。这个工具用来在适配新版本后检查漏网之鱼。

用法：
    python tools/check_game_names.py            # 只报告
    python tools/check_game_names.py --strict   # 有未归类名字就退出 1
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAPPING = ROOT / "app" / "assist_mapping.json"

SCAN = [
    "app/instruction_runner.py",
    "app/assist_script.py",
    "app/game_assist.py",
    "app/chest_4758a.py",
    "app/arena.py",
    "app/ui.py",
    "tools/gen_android_runner.py",
    "android_app/js/app.js",
    "android_app/js/runner.js",
]

# 本地变量 / 常量 / Python 标注，和游戏无关。
LOCAL_NAMES = {
    "BLE001", "MAPPING", "VERSION", "TICK_MS", "RETRY_MS", "CAN_OPEN", "HAS_OPEN",
    "OVER", "INVALID", "HUD", "XXXX", "ANGLES", "OUT", "ROOT", "E402",
    "CMD_KEY", "GAME_URL", "POLL_MS", "BOTH", "RGBA", "FLAT", "VERTICAL", "PIL",
    "LANCZOS", "NORMAL", "DISABLED", "DARK_TXT", "BG_TOP", "DARK_SUB", "HOME_W",
    "HOME_H", "INSERT", "CARD_BG", "TXT", "SUB", "APP", "UI", "OK", "ERR", "JSON",
    "HTTP", "HTTPS", "URL", "URI", "CPU", "GPU", "API", "SDK", "CDP", "DOM", "CSS",
    "HTML", "JS", "ID", "IDS", "IP", "TCP", "BOSS", "PVP", "PVE", "NPC", "GM",
    "FPS", "MS", "MAX", "MIN", "NONE", "NULL", "TRUE", "FALSE", "ADD", "DEL",
    "SET", "GET", "POST", "PUT", "END", "HOME", "STOP", "START", "MOVE", "WAIT",
    "CLICK", "WHEEL", "CLOSE", "OPEN", "SELF", "ERROR", "WARN", "INFO", "DEBUG",
    "LOG", "NAME", "TYPE", "DATA", "TEXT", "LEFT", "RIGHT", "UP", "DOWN", "TOP",
    "BOTTOM", "WIDTH", "HEIGHT", "SIZE", "X", "Y", "W", "H", "WX", "YX", "RGB",
    "PNG", "JPG", "GIF", "SVG", "PDF", "MAC", "WIN", "OS", "PC", "ADB", "TLS",
    "SSL", "DNS", "LAN", "WAN", "USB", "REVIVE_TP", "REVIVE_AD", "DARK_PAGE",
    "ASSET_DIR", "DARK_CARD", "DARK_LINE", "RUNNER_JS", "ZIZUO_DEV", "BG_BOTTOM",
    "CARD_LINE", "ALIASES", "RAW", "PREFIX", "CHEST", "JUMP", "WORD", "KEY",
    "MAX_COUNT", "FAST", "CENTER", "NO_GCOA", "NO_MAP", "NO_CORE", "NO_UNITS", "NO_",
}

# 已经删掉 UI、但后端还留着的日常任务字段（暂不参与运行命令）。
DEAD_NAMES = {
    "VRVD", "GPIC", "NMKO", "WGZM", "EDRL", "DHDO", "RSHY", "MIKP", "KRXF", "JRGO",
    "SNFA", "KYFH", "DHPZ", "KYUK", "LYJP", "UHNB", "ROPG", "KLBW",
}

# 映射里有的旧名，代码里只作为兜底默认值保留。
FALLBACK_NAMES = {"PLVT", "EFDX", "YLYI", "CLRB", "GUJG", "ADUX", "WUZI", "ZAVH"}


def mapping_names() -> set[str]:
    data = json.loads(MAPPING.read_text(encoding="utf-8"))
    out: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                out.add(str(key))
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,8}", value):
                out.add(value)

    walk(data.get("builds") or {})
    for path in ROOT.glob("app/chest_aliases_*.json"):
        try:
            extra = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        walk(extra.get("aliases") or {})
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="发现未归类名字时退出 1")
    args = parser.parse_args()

    known = mapping_names() | LOCAL_NAMES | DEAD_NAMES | FALLBACK_NAMES
    pattern = re.compile(r"\b[A-Z][A-Z0-9_]{2,8}\b")
    unknown: list[tuple[str, int, str]] = []

    for rel in SCAN:
        path = ROOT / rel
        if not path.exists():
            continue
        for lineno, line in enumerate(io.open(path, encoding="utf-8"), 1):
            for token in pattern.findall(line):
                if token not in known:
                    unknown.append((rel, lineno, token))

    if not unknown:
        print("OK：没有发现映射表之外的硬编码游戏名。")
        return 0

    counts = Counter(token for _rel, _line, token in unknown)
    print("发现 %d 处、%d 个未归类的全大写名字：" % (len(unknown), len(counts)))
    for token, count in counts.most_common():
        places = ["%s:%d" % (rel, line) for rel, line, t in unknown if t == token]
        print("  %-8s x%-3d %s" % (token, count, ", ".join(places[:6]) + (" ..." if len(places) > 6 else "")))
    print("\n如果是新版本的字段：加进 app/assist_mapping.json 的 runtimeNames 或对应语义段。")
    print("如果是本地变量/已删功能：加进 tools/check_game_names.py 的 LOCAL_NAMES / DEAD_NAMES。")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
