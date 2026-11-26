"""生成密钥：

    python tools/make_key.py 123456789     # 绑定角色ID
    python tools/make_key.py --super       # 超级密钥（不绑定ID）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.license import SECRET, make_key, make_super_key  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] in ("--super", "-s"):
        print("SUPER :", make_super_key())
        raise SystemExit(0)
    if len(sys.argv) < 2:
        print("用法: python tools/make_key.py <游戏角色ID>")
        print("      python tools/make_key.py --super   生成超级密钥")
        raise SystemExit(1)
    game_id = sys.argv[1].strip()
    print("游戏ID:", game_id)
    print("KEY   :", make_key(game_id))
