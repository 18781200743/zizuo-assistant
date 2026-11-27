"""打成别人解压即用的发布包（源码便携包 + 可选 exe）。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
RELEASE_NAME = f"zizuo助手_{datetime.now().strftime('%Y%m%d')}"

# 打包发布时绝对不带的个人字段（仅顶层）：密钥、命令、任务、抽卡记录、自定义背景
PERSONAL_TOP_KEYS = {"license", "commands_text", "tasks", "gacha_cdp", "ui"}

# 敏感字段名（不区分大小写）；注意不要用单独的 key，避免误伤 hotkey / boss_keywords
SENSITIVE_KEY_RE = re.compile(
    r"(license|game_?id|role_?id|openid|secret|token|cookie|password|passwd|"
    r"account|login|session|auth|credential)",
    re.IGNORECASE,
)

# 密钥/OpenID 形态，出现在任何值里都清掉
SENSITIVE_VALUE_RES = (
    re.compile(r"\bXS-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}\b", re.IGNORECASE),
    re.compile(r"\bSS-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}\b", re.IGNORECASE),
    re.compile(r"\bo[A-Za-z0-9_-]{20,}\b"),
)


def sanitize_config(value, path="$"):
    """递归去掉密钥、游戏 ID 等个人字段，返回 (清理后的数据, 被移除的路径列表)。"""
    removed = []
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            child = path + "." + str(key)
            if path == "$" and key in PERSONAL_TOP_KEYS:
                removed.append(child)
                continue
            if SENSITIVE_KEY_RE.search(str(key)):
                removed.append(child)
                continue
            cleaned, sub = sanitize_config(item, child)
            out[key] = cleaned
            removed.extend(sub)
        return out, removed
    if isinstance(value, list):
        out = []
        for index, item in enumerate(value):
            cleaned, sub = sanitize_config(item, path + "[" + str(index) + "]")
            out.append(cleaned)
            removed.extend(sub)
        return out, removed
    if isinstance(value, str) and any(rx.search(value) for rx in SENSITIVE_VALUE_RES):
        return "", removed + [path]
    return value, removed


def check_config() -> list[str]:
    """检查 config.json 并把清理后的示例配置写到 config.example.json 供预览。"""
    path = ROOT / "config.json"
    if not path.exists():
        print("[安全] 没有 config.json，跳过检查")
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("[安全] config.json 读取失败：%s" % exc)
        return []
    example, removed = sanitize_config(raw)
    text = json.dumps(example, ensure_ascii=False, indent=2)
    leaks = []
    for rx in SENSITIVE_VALUE_RES:
        leaks.extend(match.group(0) for match in rx.finditer(text))
    if removed:
        print("[安全] config.json 含个人/敏感字段，示例配置已移除：")
        for item in removed:
            print("       -", item)
    else:
        print("[安全] config.json 未发现敏感字段")
    if leaks:
        print("[安全][警告] 清理后仍出现密钥形态，请手动检查：", ", ".join(leaks))
    else:
        print("[安全] 未发现 XS-/SS- 密钥或 OpenID 残留")
    return removed


def write_example_config(stage: Path) -> None:
    path = ROOT / "config.json"
    example_path = stage / "config.example.json"
    if not path.exists():
        example_path.write_text("{}\n", encoding="utf-8")
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        example, removed = sanitize_config(raw)
        example_path.write_text(
            json.dumps(example, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if removed:
            print("[安全] config.example.json 已移除：", ", ".join(removed))
    except (OSError, ValueError) as exc:
        print("[安全] config.json 读取失败，示例配置留空：", exc)
        example_path.write_text("{}\n", encoding="utf-8")


def _copy_tree(src: Path, dst: Path, ignore_names: set[str]) -> None:
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in ignore_names or item.name.startswith("."):
            continue
        target = dst / item.name
        if item.is_dir():
            if item.name in {"__pycache__", ".venv", "dist", "build", "saves"}:
                continue
            _copy_tree(item, target, ignore_names)
        else:
            if item.suffix.lower() in {".pyc", ".pyo"}:
                continue
            shutil.copy2(item, target)


def make_portable_zip() -> Path:
    """源码便携包：别人装 Python 后双击 run.bat。"""
    stage = DIST / RELEASE_NAME
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    # 程序代码
    for name in (
        "app",
        "main.py",
        "run.bat",
        "requirements.txt",
        "README.md",
        "分享给别人.txt",
    ):
        src = ROOT / name
        if not src.exists():
            continue
        if src.is_dir():
            _copy_tree(src, stage / name, {"__pycache__"})
        else:
            shutil.copy2(src, stage / name)

    # 模板图（英雄+按钮）——和模板文件一样发给别人就能用
    for folder in ("人物图片", "templates"):
        src = ROOT / folder
        if src.exists():
            _copy_tree(src, stage / folder, set())

    # 空存档目录（不带你的登录态/个人存档）
    (stage / "saves").mkdir(exist_ok=True)
    (stage / "saves" / ".gitkeep").write_text("", encoding="utf-8")
    write_example_config(stage)

    zip_path = DIST / f"{RELEASE_NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in stage.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(stage.parent).as_posix())
    print(f"[OK] 便携源码包: {zip_path}")
    return zip_path


def make_exe() -> Path | None:
    """可选：打成 exe 文件夹（体积较大，对方无需装 Python）。"""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[SKIP] 未安装 PyInstaller，仅打 zip 包")
        return None

    work = DIST / "_pyi_work"
    out = DIST / f"{RELEASE_NAME}_exe"
    if out.exists():
        # 旧版本可能还在跑，锁住里面的 webview 缓存：删不掉就算了，后面会覆盖
        shutil.rmtree(out, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "Zizuo助手",
        "--icon",
        str(ROOT / "app" / "assets" / "sunny.ico"),
        "--distpath",
        str(out),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        "--collect-all",
        "webview",
        "--collect-all",
        "cv2",
        "--hidden-import",
        "websocket",
        "--hidden-import",
        "win32timezone",
        "--hidden-import",
        "app",
        "--hidden-import",
        "app.ui",
        "--hidden-import",
        "app.game_window",
        "--hidden-import",
        "app.cdp_click",
        # 字段映射是数据文件，必须显式带进包里，否则 exe 会说「没有字段映射」
        "--add-data",
        f"{ROOT / 'app' / 'assist_mapping.json'};app",
        # 运行指令：指令库 + 后台地图/摇杆参数
        "--add-data",
        f"{ROOT / 'app' / 'instructions.json'};app",
        "--add-data",
        f"{ROOT / 'app' / 'commands_presets.json'};app",
        "--add-data",
        f"{ROOT / 'app' / 'chest_aliases_4758a.json'};app",
        "--add-data",
        f"{ROOT / 'app' / 'automation_config.json'};app",
        # 首页头图等界面素材
        "--add-data",
        f"{ROOT / 'app' / 'assets'};app/assets",
        str(ROOT / "main.py"),
    ]
    print("运行:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))

    # 单文件模式：exe 直接在 out 目录里
    exe_path = out / "Zizuo助手.exe"
    if not exe_path.exists():
        found = list(out.rglob("Zizuo助手.exe"))
        if not found:
            raise SystemExit("没找到打包好的 exe")
        exe_path = found[0]

    app_dir = out / "单文件版"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    app_dir.mkdir(parents=True)
    shutil.copy2(exe_path, app_dir / "Zizuo助手.exe")

    zip_path = DIST / f"{RELEASE_NAME}_exe.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(app_dir / "Zizuo助手.exe", "Zizuo助手.exe")
    print(f"[OK] exe 目录: {app_dir}")
    print(f"[OK] exe 压缩包: {zip_path}")
    return zip_path


def main() -> None:
    if "--check" in sys.argv:
        check_config()
        print("\n只检查配置，不打包。")
        return
    DIST.mkdir(exist_ok=True)
    # 确保本机模板已在项目内
    hero = ROOT / "人物图片"
    legacy = Path(r"F:\F\game\人物图片")
    if legacy.exists():
        hero.mkdir(exist_ok=True)
        for f in legacy.iterdir():
            if f.is_file():
                shutil.copy2(f, hero / f.name)
        print(f"[OK] 已同步人物图片 -> {hero}")

    only_exe = "--exe" in sys.argv
    only_zip = "--zip" in sys.argv
    z1 = z2 = None
    if not only_exe:
        z1 = make_portable_zip()
    if not only_zip:
        z2 = make_exe()
    print("\n发给别人用：")
    if z1:
        print(f"  源码包（需 Python）: {z1}")
    if z2:
        print(f"  exe 包（免 Python）: {z2}")
        print("  或直接运行目录里的 Zizuo助手.exe")
    print("对方解压后看「使用说明.txt」/「分享给别人.txt」")


if __name__ == "__main__":
    main()
