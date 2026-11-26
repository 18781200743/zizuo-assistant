"""生成/查看当前版本密钥：python tools/make_key.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.license import SECRET, make_key  # noqa: E402

if __name__ == "__main__":
    print("SECRET:", SECRET)
    print("KEY   :", make_key())
