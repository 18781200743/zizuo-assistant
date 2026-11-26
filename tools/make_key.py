"""生成与游戏角色 ID 绑定的密钥：

    python tools/make_key.py 123456789
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.license import SECRET, make_key  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python tools/make_key.py <游戏角色ID>")
        raise SystemExit(1)
    game_id = sys.argv[1].strip()
    print("游戏ID:", game_id)
    print("KEY   :", make_key(game_id))
