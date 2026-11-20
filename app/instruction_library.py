"""运行指令：指令库读取 + 指令脚本解析。

指令库来自参考工具的公开指令库（已抓取保存到 app/instructions.json），
格式是一行一条指令的纯文本脚本，例如：

    #幸运夺宝采集
    传送(3,2)
    移动(上,3)
    采集(活动)
    回城()
    停止()
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config_store import project_root

# 指令分类「全部」用 0 表示
CATEGORY_ALL = 0


def library_path() -> Path:
    return Path(__file__).resolve().parent / "instructions.json"


def _user_library_path() -> Path:
    path = project_root() / "saves" / "instructions_user.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _overrides_path() -> Path:
    """内置指令的修改记录（内置库在打包后的 _internal 里，直接改会被下次更新覆盖）。"""
    path = project_root() / "saves" / "instructions_overrides.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_library() -> Dict[str, Any]:
    """内置指令库 + 用户自己保存的指令（内置的被改过就用改过的内容）。"""
    data = _read_json(library_path(), {})
    if not isinstance(data, dict):
        data = {}
    categories = list(data.get("categories") or [])
    definitions = list(data.get("definitions") or [])
    instructions = [dict(item) for item in (data.get("instructions") or []) if isinstance(item, dict)]

    # 内置指令的修改：按 id 覆盖内容，原版仍保留在 instructions.json 里
    overrides = _read_json(_overrides_path(), {})
    if isinstance(overrides, dict) and overrides:
        for item in instructions:
            key = str(item.get("id") if item.get("id") is not None else "")
            if key and key in overrides:
                item["content"] = str(overrides[key])
                item["edited"] = True

    user = _read_json(_user_library_path(), [])
    if isinstance(user, list):
        for item in user:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            item = dict(item)
            item["id"] = item.get("id") or item["title"]
            item["custom"] = True
            item["category_id"] = int(item.get("category_id") or 3)
            instructions.append(item)

    return {
        "categories": categories,
        "definitions": definitions,
        "instructions": instructions,
    }


def save_instruction_override(instruction_id: Any, content: str) -> None:
    """修改内置指令：内容写进 saves/instructions_overrides.json，重启也还在。"""
    key = str(instruction_id if instruction_id is not None else "").strip()
    if not key:
        raise ValueError("这条指令没有编号，无法保存修改")
    content = (content or "").strip()
    if not content:
        raise ValueError("指令内容是空的")
    path = _overrides_path()
    data = _read_json(path, {})
    if not isinstance(data, dict):
        data = {}
    data[key] = content
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def clear_instruction_override(instruction_id: Any) -> None:
    """把内置指令还原成原版。"""
    key = str(instruction_id if instruction_id is not None else "").strip()
    if not key:
        return
    path = _overrides_path()
    data = _read_json(path, {})
    if not isinstance(data, dict):
        return
    if key in data:
        data.pop(key)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def library_categories(library: Dict[str, Any]) -> List[Dict[str, Any]]:
    """分类列表：全部 + 内置分类 + 我的。"""
    rows: List[Dict[str, Any]] = [{"id": CATEGORY_ALL, "name": "全部"}]
    for item in library.get("categories") or []:
        rows.append({"id": int(item.get("id") or 0), "name": str(item.get("name") or "")})
    rows.append({"id": -1, "name": "我的"})
    return rows


def library_instructions(
    library: Dict[str, Any], category_id: int = CATEGORY_ALL, keyword: str = ""
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in library.get("instructions") or []:
        if category_id == -1:
            if not item.get("custom"):
                continue
        elif category_id != CATEGORY_ALL and int(item.get("category_id") or 0) != category_id:
            continue
        if keyword:
            text = (str(item.get("title") or "") + "\n" + str(item.get("content") or "")).lower()
            if keyword.lower() not in text:
                continue
        rows.append(item)
    if category_id == CATEGORY_ALL:
        def _order(item: Dict[str, Any]):
            custom = 0 if item.get("custom") else 1
            try:
                return (custom, 0, float(item.get("id") or 0), "")
            except (TypeError, ValueError):
                return (custom, 1, 0.0, str(item.get("title") or ""))

        rows.sort(key=_order)
    return rows


def save_user_instruction(
    title: str, content: str, category_id: int = 3, old_title: Optional[str] = None
) -> List[Dict[str, Any]]:
    """保存/覆盖「我的指令」，返回最新的用户指令列表。"""
    title = (title or "").strip()
    if not title:
        raise ValueError("指令名称不能为空")
    content = (content or "").strip()
    if not content:
        raise ValueError("指令内容是空的")
    path = _user_library_path()
    current = _read_json(path, [])
    if not isinstance(current, list):
        current = []
    rows = [it for it in current if isinstance(it, dict)]
    target = old_title or title
    for item in rows:
        if str(item.get("title") or "") == target:
            item["title"] = title
            item["content"] = content
            item["category_id"] = int(category_id or 3)
            break
    else:
        rows.append(
            {
                "id": "user-%d" % (len(rows) + 1),
                "title": title,
                "category_id": int(category_id or 3),
                "content": content,
                "custom": True,
            }
        )
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def delete_user_instruction(title: str) -> List[Dict[str, Any]]:
    path = _user_library_path()
    current = _read_json(path, [])
    if not isinstance(current, list):
        current = []
    rows = [it for it in current if isinstance(it, dict) and str(it.get("title") or "") != title]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def definition_map(library: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for item in library.get("definitions") or []:
        name = str(item.get("command_name") or "").strip()
        if name:
            rows[name] = item
    return rows


# ---------------------------------------------------------------- 解析

_CMD_RE = re.compile(r"^\s*([A-Za-z_\u4e00-\u9fff][^()]*?)\s*\((.*)\)\s*$")


def split_args(text: str) -> List[str]:
    """按逗号切参数（支持引号里的逗号）。"""
    args: List[str] = []
    buf: List[str] = []
    quote = ""
    depth = 0
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch in (",", "，") and depth == 0:
            args.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail or args:
        args.append(tail)
    return [a.strip().strip("'\"") for a in args]


class ScriptError(ValueError):
    pass


def parse_script(content: str) -> List[Dict[str, Any]]:
    """把脚本拆成一行一步；# 开头是注释，空行忽略。"""
    steps: List[Dict[str, Any]] = []
    for no, raw in enumerate((content or "").splitlines(), 1):
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        match = _CMD_RE.match(line)
        if not match:
            raise ScriptError("第 %d 行不是「指令(参数)」格式：%s" % (no, line))
        name = match.group(1).strip()
        body = match.group(2).strip()
        args = split_args(body) if body else []
        steps.append({"line": no, "name": name, "args": args, "raw": line})
    return steps


def script_title(content: str) -> str:
    for raw in (content or "").splitlines():
        line = raw.strip().lstrip("\ufeff")
        if line.startswith("#"):
            text = line.lstrip("#").strip().lstrip("-").strip()
            if text:
                return text
        elif line:
            break
    return ""


def format_content(content: str) -> str:
    """统一缩进/去尾部空白，方便显示和复制。"""
    lines = []
    for raw in (content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        lines.append(raw.rstrip())
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


# ---------------------------------------------------------------- 指令码（分享文本）

_SHARE_PREFIX = "ZZCMD1:"


def export_code(title: str, content: str) -> str:
    """导出一条指令的可复制文本（我们自己定义的格式）。"""
    import base64

    payload = json.dumps({"title": title, "content": content}, ensure_ascii=False)
    return _SHARE_PREFIX + base64.b64encode(payload.encode("utf-8")).decode("ascii")


def import_code(text: str) -> Tuple[str, str]:
    """导入指令码；也接受直接粘贴的脚本正文。"""
    import base64

    raw = (text or "").strip()
    if not raw:
        raise ValueError("没有内容")
    if raw.startswith(_SHARE_PREFIX):
        try:
            data = json.loads(base64.b64decode(raw[len(_SHARE_PREFIX):]).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("指令码格式不对：%s" % exc) from exc
        return str(data.get("title") or "导入的指令"), format_content(str(data.get("content") or ""))
    # 直接粘贴脚本：标题取第一行 #xxx，没有就用「导入的指令」
    content = format_content(raw)
    if not content:
        raise ValueError("没有可用的指令内容")
    return script_title(content) or "导入的指令", content
