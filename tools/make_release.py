"""打成别人解压即用的发布包（源码便携包 + 可选 exe）。"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
RELEASE_NAME = f"zizuo助手_{datetime.now().strftime('%Y%m%d')}"


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
    (stage / "config.example.json").write_text(
        (ROOT / "config.json").read_text(encoding="utf-8")
        if (ROOT / "config.json").exists()
        else "{}",
        encoding="utf-8",
    )

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
        "app.gacha",
        "--hidden-import",
        "app.cdp_click",
        # 字段映射是数据文件，必须显式带进包里，否则 exe 会说「没有字段映射」
        "--add-data",
        f"{ROOT / 'app' / 'assist_mapping.json'};app",
        # 运行指令：指令库 + 后台地图/摇杆参数
        "--add-data",
        f"{ROOT / 'app' / 'instructions.json'};app",
        "--add-data",
        f"{ROOT / 'app' / 'automation_config.json'};app",
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
