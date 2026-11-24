"""离线授权密钥生成器（账号绑定）。

用法：
    python tools/keygen.py                     # 打开图形界面
    python tools/keygen.py --init-keys         # 生成签发密钥对（只做一次）
    python tools/keygen.py --id 214672988 --days 30 --features farm,plank
    python tools/keygen.py --verify ZZK1-... --id 214672988

私钥保存在 license_keys/private_key.pem，只留在本机，不要发给别人。
公钥保存在 app/license_public.pem，随程序发布。
"""

from __future__ import annotations

import argparse
import sys
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.license import (  # noqa: E402
    FEATURE_LABELS,
    MAX_DAYS,
    LicenseError,
    account_code,
    describe_key,
    generate_keypair,
    load_signing_key,
    make_key,
    verify_key,
)

PRIVATE_KEY_PATH = ROOT / "license_keys" / "private_key.pem"
PUBLIC_KEY_PATH = ROOT / "app" / "license_public.pem"


def ensure_keys(path: Path = PRIVATE_KEY_PATH, public_path: Path = PUBLIC_KEY_PATH) -> bool:
    """私钥存在但公钥丢失时，从私钥恢复公钥。"""
    if path.exists() and not public_path.exists():
        from cryptography.hazmat.primitives import serialization

        private_key = load_signing_key(path)
        public_path.parent.mkdir(parents=True, exist_ok=True)
        public_path.write_bytes(
            private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
    return path.exists()


def _expire_text(permanent: bool, days: int) -> str:
    if permanent:
        return "永久"
    return "%s（%d 天）" % ((date.today() + timedelta(days=days)).isoformat(), days)


def _parse_features(value: str) -> list[str]:
    names = [part.strip().lower() for part in value.replace("，", ",").split(",")]
    invalid = [name for name in names if name and name not in FEATURE_LABELS]
    if invalid:
        raise SystemExit("未知功能：%s\n可用：%s" % (", ".join(invalid), ", ".join(FEATURE_LABELS)))
    return [name for name in names if name]


def run_cli(args: argparse.Namespace) -> int:
    if args.init_keys:
        if PRIVATE_KEY_PATH.exists() and not args.force:
            print("私钥已存在：%s" % PRIVATE_KEY_PATH)
            print("如果重新生成，之前签发的密钥会全部失效。确需重建请加 --force。")
            return 1
        generate_keypair(PRIVATE_KEY_PATH, PUBLIC_KEY_PATH)
        print("已生成签发密钥对：")
        print("  私钥（保密）：%s" % PRIVATE_KEY_PATH)
        print("  公钥（随程序）：%s" % PUBLIC_KEY_PATH)
        return 0

    if not ensure_keys():
        print("未找到私钥：%s" % PRIVATE_KEY_PATH)
        print("请先运行：python tools/keygen.py --init-keys")
        return 1

    if args.verify:
        result = verify_key(args.verify, args.id or "", today=date.today())
        print(result.message)
        if result.ok:
            print("功能：%s" % ("、".join(FEATURE_LABELS.get(n, n) for n in result.features) or "无"))
            print("到期：%s" % ("永久" if result.permanent else result.expires.isoformat()))
        return 0 if result.ok else 1

    if not args.id:
        print("缺少 --id 游戏ID")
        return 1
    features = _parse_features(args.features) if args.features else list(FEATURE_LABELS)
    if not features:
        features = list(FEATURE_LABELS)
    key = make_key(
        load_signing_key(PRIVATE_KEY_PATH),
        args.id,
        args.days,
        features,
        permanent=args.permanent,
    )
    print(key)
    print("-" * 60)
    print(describe_key(key))
    print("账号码：%s" % account_code(args.id))
    print("到期：%s" % _expire_text(args.permanent, args.days))
    if args.out:
        Path(args.out).write_text(key + "\n", encoding="utf-8")
        print("已写入：%s" % args.out)
    return 0


class KeygenApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("子座助手 · 授权密钥生成器")
        self.geometry("640x520")
        self.minsize(600, 480)
        self._build_ui()
        self._refresh_key_status()

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(self)
        top.pack(fill=tk.X, **pad)

        info = ttk.LabelFrame(top, text="授权信息", padding=8)
        info.pack(fill=tk.X)

        ttk.Label(info, text="游戏 ID").grid(row=0, column=0, sticky=tk.W)
        self.id_var = tk.StringVar()
        ttk.Entry(info, textvariable=self.id_var, width=40).grid(
            row=0, column=1, columnspan=3, sticky=tk.W, padx=(6, 0)
        )

        ttk.Label(info, text="有效天数").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.days_var = tk.StringVar(value="30")
        self.days_spin = ttk.Spinbox(
            info, from_=1, to=MAX_DAYS, textvariable=self.days_var, width=8
        )
        self.days_spin.grid(row=1, column=1, sticky=tk.W, padx=(6, 0), pady=(6, 0))
        self.permanent_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            info, text="永久", variable=self.permanent_var, command=self._toggle_days
        ).grid(row=1, column=2, sticky=tk.W, padx=(8, 0), pady=(6, 0))
        ttk.Label(info, text="从今天起算", foreground="#666666").grid(
            row=1, column=3, sticky=tk.W, padx=(8, 0), pady=(6, 0)
        )

        ttk.Label(info, text="功能").grid(row=2, column=0, sticky=tk.NW, pady=(8, 0))
        feat_frame = ttk.Frame(info)
        feat_frame.grid(row=2, column=1, columnspan=3, sticky=tk.W, padx=(6, 0), pady=(4, 0))
        self.feature_vars: dict[str, tk.BooleanVar] = {}
        for index, (name, label) in enumerate(FEATURE_LABELS.items()):
            var = tk.BooleanVar(value=True)
            self.feature_vars[name] = var
            ttk.Checkbutton(feat_frame, text=label, variable=var).grid(
                row=index // 4, column=index % 4, sticky=tk.W, padx=(0, 10)
            )

        btns = ttk.Frame(top)
        btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btns, text="生成密钥", style="Accent.TButton", command=self._generate).pack(
            side=tk.LEFT
        )
        ttk.Button(btns, text="复制密钥", command=self._copy).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="验证密钥", command=self._verify).pack(side=tk.LEFT, padx=(6, 0))

        out = ttk.LabelFrame(top, text="密钥", padding=8)
        out.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.key_text = tk.Text(out, height=5, wrap=tk.CHAR, font=("Consolas", 10))
        self.key_text.pack(fill=tk.BOTH, expand=True)

        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, foreground="#1a4d8f").pack(
            fill=tk.X, padx=10, pady=(0, 4)
        )

        bottom = ttk.Frame(self)
        bottom.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.key_status_var = tk.StringVar(value="")
        ttk.Label(
            bottom, textvariable=self.key_status_var, foreground="#7a5b00"
        ).pack(side=tk.LEFT)
        ttk.Button(bottom, text="生成/重建密钥对", command=self._rebuild_keys).pack(side=tk.RIGHT)

    def _toggle_days(self) -> None:
        state = tk.DISABLED if self.permanent_var.get() else tk.NORMAL
        self.days_spin.configure(state=state)

    def _refresh_key_status(self) -> None:
        if PRIVATE_KEY_PATH.exists():
            self.key_status_var.set("私钥：%s（只留本机，注意备份）" % PRIVATE_KEY_PATH)
        else:
            self.key_status_var.set("未找到私钥，请点右侧「生成/重建密钥对」")

    def _selected_features(self) -> list[str]:
        return [name for name, var in self.feature_vars.items() if var.get()]

    def _generate(self) -> None:
        try:
            game_id = self.id_var.get().strip()
            if not game_id:
                raise LicenseError("请先填写游戏 ID")
            if not ensure_keys():
                raise LicenseError("未找到私钥，请先生成密钥对")
            permanent = self.permanent_var.get()
            try:
                days = int(self.days_var.get())
            except ValueError as exc:
                raise LicenseError("有效天数必须是数字") from exc
            features = self._selected_features()
            if not features:
                raise LicenseError("至少选择一个功能")
            key = make_key(
                load_signing_key(PRIVATE_KEY_PATH),
                game_id,
                days,
                features,
                permanent=permanent,
            )
        except (LicenseError, OSError, ValueError) as exc:
            messagebox.showerror("生成失败", str(exc), parent=self)
            return

        self.key_text.delete("1.0", tk.END)
        self.key_text.insert("1.0", key)
        expire = _expire_text(permanent, days)
        self.status_var.set(
            "已生成 · 账号码 %s · 到期 %s" % (account_code(game_id), expire)
        )
        self._refresh_key_status()

    def _copy(self) -> None:
        key = self.key_text.get("1.0", tk.END).strip()
        if not key:
            messagebox.showinfo("提示", "还没有生成密钥", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(key)
        self.status_var.set("密钥已复制到剪贴板")

    def _verify(self) -> None:
        key = self.key_text.get("1.0", tk.END).strip()
        if not key:
            messagebox.showinfo("提示", "请先填写或生成密钥", parent=self)
            return
        result = verify_key(key, self.id_var.get().strip())
        if result.ok:
            features = "、".join(FEATURE_LABELS.get(n, n) for n in result.features) or "无"
            messagebox.showinfo(
                "验证通过",
                "%s\n功能：%s\n到期：%s"
                % (
                    result.message,
                    features,
                    "永久" if result.permanent else result.expires.isoformat(),
                ),
                parent=self,
            )
        else:
            messagebox.showerror("验证失败", result.message, parent=self)

    def _rebuild_keys(self) -> None:
        if PRIVATE_KEY_PATH.exists() and not messagebox.askyesno(
            "确认重建",
            "重建密钥对后，之前签发的所有密钥都会失效。\n确定继续吗？",
            parent=self,
        ):
            return
        generate_keypair(PRIVATE_KEY_PATH, PUBLIC_KEY_PATH)
        self._refresh_key_status()
        messagebox.showinfo(
            "完成",
            "已生成新的密钥对。\n私钥：%s\n公钥：%s" % (PRIVATE_KEY_PATH, PUBLIC_KEY_PATH),
            parent=self,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="离线授权密钥生成器")
    parser.add_argument("--id", help="游戏 ID（账号绑定）")
    parser.add_argument("--days", type=int, default=30, help="有效天数，默认 30")
    parser.add_argument("--features", default="", help="功能，逗号分隔：farm,plank,gacha,...")
    parser.add_argument("--permanent", action="store_true", help="永久授权")
    parser.add_argument("--out", help="把密钥写入文件")
    parser.add_argument("--init-keys", action="store_true", help="生成签发密钥对")
    parser.add_argument("--force", action="store_true", help="配合 --init-keys 覆盖旧私钥")
    parser.add_argument("--verify", help="验证一个密钥")
    args = parser.parse_args()

    if (
        args.init_keys
        or args.id
        or args.verify
        or args.features
        or args.out
        or args.permanent
    ):
        return run_cli(args)

    app = KeygenApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
