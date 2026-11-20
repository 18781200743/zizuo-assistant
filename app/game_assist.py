"""游戏辅助控制器：把 JS 运行时注入游戏页，轮询状态，落盘统计。

分工：
    * JS 运行时（app/assist_script.py）留在游戏页里跑状态机 —— 红包监听、死亡观察、
      祈愿倒计时都在页面内，不依赖 Python 定时，窗口最小化也不影响。
    * 本模块只做三件事：装运行时、按 600ms 取一次快照、把开关指令排队发过去。
    * UI 线程只读本地快照，不会被网络/页面卡住。
"""

from __future__ import annotations

import hashlib
import json
import time
import queue
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .assist_script import RUNTIME_JS, RUNTIME_VERSION
from .cdp_click import DEFAULT_CDP_PORT, find_page_ws
from .config_store import project_root

POLL_SECONDS = 0.6
CALL_TIMEOUT = 6.0

_FEATURE_LABEL = {
    "mob": "小怪死亡等待",
    "boss": "BOSS 死亡等待",
    "redPacket": "快速抢红包",
    "wish": "祈愿领取",
    "damage": "英雄倍伤",
    "vision": "视野修改",
    "cooldown": "技能冷却",
    "revive": "快速复活",
    "heroRevive": "英雄复活时间",
    "stopAll": "全部关闭",
}

# 战斗参数放在快照的 combat 段里
_COMBAT_KEYS = ("damage", "vision", "cooldown")


def stats_path() -> Path:
    path = project_root() / "saves"
    path.mkdir(parents=True, exist_ok=True)
    return path / "assist_stats.json"


def mapping_path() -> Path:
    """字段映射文件的位置。

    源码运行就是 app/assist_mapping.json；
    打包成 exe 后依次找：exe 旁边 → 打包内置（_MEIPASS/app）→ 模块同目录。
    exe 旁边那份优先级最高，方便游戏更新后直接改映射、不用重新打包。
    """
    candidates = []
    if getattr(sys, "frozen", False):
        try:
            candidates.append(project_root() / "assist_mapping.json")
        except Exception:  # noqa: BLE001
            pass
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "app" / "assist_mapping.json")
    candidates.append(Path(__file__).with_name("assist_mapping.json"))
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return candidates[-1]


def load_mapping() -> Dict[str, Any]:
    """按游戏版本存的字段映射：游戏内部名每版都会重随机，只能按版本对表。"""
    try:
        data = json.loads(mapping_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"builds": {}}
    if not isinstance(data, dict):
        return {"builds": {}}
    builds = data.get("builds")
    return {"builds": builds if isinstance(builds, dict) else {}}


def _load_stats() -> Dict[str, Any]:
    try:
        data = json.loads(stats_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _save_stats(data: Dict[str, Any]) -> None:
    try:
        stats_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _merge_daily(stats: Dict[str, Any], key: str, date: str, value: Any) -> None:
    """同一天的计数只增不减：页面刷新清零时用落盘值兜住。"""
    if not date:
        return
    if not isinstance(value, (int, float)) or value < 0:
        return
    old = stats.get(key) or {}
    if isinstance(old, dict) and old.get("date") == date:
        stats[key] = {"date": date, "value": max(int(old.get("value") or 0), int(value))}
    else:
        stats[key] = {"date": date, "value": int(value)}


class AssistBridge:
    """一条常驻 CDP 连接（websocket-client），负责注入与取数。"""

    def __init__(self, port: int = DEFAULT_CDP_PORT) -> None:
        self.port = int(port)
        self._ws: Any = None
        self._msg_id = 0
        self._lock = threading.RLock()
        self.last_error = ""

    # ---- 连接 ----

    def close(self) -> None:
        # 故意不拿 _lock：锁可能正被等响应的线程占着，
        # 从这里直接换掉并关掉连接，卡住的 recv 会立刻报错退出。
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _connect(self) -> bool:
        try:
            import websocket
        except ImportError:
            self.last_error = "缺少 websocket-client，请先 pip install websocket-client"
            return False
        ws_url = find_page_ws(self.port)
        if not ws_url:
            self.last_error = "未连上游戏窗口（9229 调试端口没响应）：点「重新连接」，或用「打开游戏窗口」重开一次"
            return False
        try:
            ws = websocket.create_connection(ws_url, timeout=CALL_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"连接游戏页失败：{exc}"
            return False
        try:
            ws.settimeout(CALL_TIMEOUT)
        except Exception:  # noqa: BLE001
            pass
        self._ws = ws
        return True

    def _ensure(self) -> bool:
        if self._ws is not None:
            return True
        with self._lock:
            if self._ws is not None:
                return True
            return self._connect()

    # ---- 基础调用 ----

    def _call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self._ensure():
            raise RuntimeError(self.last_error or "未连接游戏页")
        with self._lock:
            ws = self._ws
            if ws is None:
                raise RuntimeError(self.last_error or "未连接游戏页")
            self._msg_id += 1
            mid = self._msg_id
            try:
                ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}, ensure_ascii=False))
                # 整个调用最多等 CALL_TIMEOUT 秒（不能每条消息各等一次，否则可能卡几分钟）
                deadline = time.monotonic() + CALL_TIMEOUT
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise TimeoutError("游戏页响应超时")
                    try:
                        ws.settimeout(left)
                    except Exception:  # noqa: BLE001
                        pass
                    raw = ws.recv()
                    if not raw:
                        continue
                    data = json.loads(raw)
                    if data.get("id") == mid:
                        return data
            except Exception as exc:  # noqa: BLE001
                self.close()
                raise RuntimeError(f"游戏页通讯中断：{exc}") from exc
        raise RuntimeError("游戏页无响应")

    def eval_js(self, expression: str) -> Any:
        data = self._call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": False},
        )
        if data.get("error"):
            raise RuntimeError(str(data["error"].get("message") or data["error"]))
        result = data.get("result") or {}
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or detail.get("text") or "JS 执行失败"
            raise RuntimeError(str(text))
        return (result.get("result") or {}).get("value")

    # ---- 运行时 ----

    def ensure_runtime(self, names: Optional[Dict[str, Any]] = None) -> str:
        """返回 'installed' / 'ok' / 'fail:<原因>'。

        页面里已有运行时也可能已经过期（改了注入代码、换了映射表但没关窗口），
        所以这里比对「代码版本 + 映射内容」的指纹，不一致就重装。
        """
        mapping_json = json.dumps(load_mapping(), ensure_ascii=False, sort_keys=True)
        stamp = hashlib.md5((str(RUNTIME_VERSION) + '|' + mapping_json).encode('utf-8')).hexdigest()
        try:
            fresh = self.eval_js(
                "(window.__zzAssist && window.__zzAssist.version === %d"
                " && window.__zzAssistStamp === %s) ? 'ok' : 'need'"
                % (RUNTIME_VERSION, json.dumps(stamp))
            )
            if fresh == 'ok':
                if names:
                    self.eval_js(
                        "window.__zzAssistNames = %s;" % json.dumps(names, ensure_ascii=False)
                    )
                return 'ok'
        except RuntimeError:
            return 'fail:' + self.last_error

        prefix = (
            "window.__zzAssistMapping = %s;\n"
            "window.__zzAssistStamp = %s;\n" % (mapping_json, json.dumps(stamp))
        )
        if names:
            prefix += "window.__zzAssistNames = %s;" % json.dumps(names, ensure_ascii=False)
        expression = (
            "(function(){try{"
            + prefix
            + RUNTIME_JS
            + ";return window.__zzAssist?'installed':'fail:运行时未注册';"
            "}catch(e){return 'fail:'+((e&&e.message)||e)}})()"
        )
        try:
            value = self.eval_js(expression)
        except RuntimeError as exc:
            self.last_error = str(exc)
            return f"fail:{exc}"
        text = str(value or "")
        if text.startswith("fail:"):
            self.last_error = text[5:]
        return text

    def snapshot(self) -> Optional[Dict[str, Any]]:
        raw = self.eval_js(
            "JSON.stringify(window.__zzAssist ? window.__zzAssist.snapshot() : null)"
        )
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return None
        return data if isinstance(data, dict) else None

    def command(self, feature: str, value: Any) -> Dict[str, Any]:
        expression = (
            "JSON.stringify(window.__zzAssist ? window.__zzAssist.set(%s, %s)"
            " : {ok:false,message:'运行时未安装，请重新打开游戏窗口'})"
            % (json.dumps(feature), json.dumps(value))
        )
        raw = self.eval_js(expression)
        if not raw:
            return {"ok": False, "message": "游戏页无返回"}
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return {"ok": False, "message": "返回格式异常"}
        return data if isinstance(data, dict) else {"ok": False, "message": "返回格式异常"}

    def diagnose(self) -> Dict[str, Any]:
        raw = self.eval_js(
            "JSON.stringify(window.__zzAssist ? window.__zzAssist.diagnose() : null)"
        )
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return {}
        return data if isinstance(data, dict) else {}


class GameAssist:
    """辅助总控：后台线程轮询 + 指令队列，UI 只读 self.state。"""

    def __init__(
        self,
        log: Optional[Callable[[str], None]] = None,
        on_state: Optional[Callable[[Dict[str, Any]], None]] = None,
        port: int = DEFAULT_CDP_PORT,
    ) -> None:
        self.log = log or (lambda _msg: None)
        self.on_state = on_state or (lambda _state: None)
        self.bridge = AssistBridge(port=port)
        self.state: Dict[str, Any] = {}
        self.running = False
        self.names: Dict[str, Any] = {}

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._desired: Dict[str, Any] = {}
        self._last_message = ""
        self._status = ""
        self._stats = _load_stats()
        # 全灭确认框：页面只给坐标，点击由这里用 CDP 真鼠标发（页面内假事件游戏不认）
        self._last_confirm_click = 0.0

    # ---- 对外 ----

    def start(self) -> None:
        if self.running and self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        # 先把页面里开着的功能关掉，否则关掉助手后游戏里还在自动跑
        if self.running and (self.state or {}).get("ready"):
            try:
                self.bridge.command("stopAll", True)
            except Exception:  # noqa: BLE001
                pass
        self._stop.set()
        self.running = False
        self._desired.clear()
        self.bridge.close()

    def _status_log(self, text: str) -> None:
        """连接状态只在变化时写一行：界面不会刷屏，也不会一直停在旧提示上。"""
        if text == self._status:
            return
        self._status = text
        try:
            self.log(text)
        except Exception:  # noqa: BLE001
            pass

    def reconnect(self) -> None:
        """强制重连游戏页：关掉旧连接，下一轮轮询会自动重新接上。"""
        self.bridge.close()
        self._status = ""
        self._last_message = ""
        if not self.running:
            self.start()

    def set_feature(self, feature: str, value: Any) -> None:
        """只排队，真正的调用在轮询线程里做，UI 永远不会卡。"""
        self._desired[feature] = value
        self._queue.put((feature, value))
        self._optimistic(feature, value)
        if not self.running:
            self.start()

    def _optimistic(self, feature: str, value: Any) -> None:
        """点完先把界面状态改成「正在开启/关闭」，等页面快照回来再校正。

        不这么做的话，从点击到下一份快照之间界面还是旧状态，看起来像没生效。
        """
        state = self.state
        if not isinstance(state, dict) or not state:
            return
        if feature == "stopAll":
            for key in ("mob", "boss", "redPacket", "wish"):
                item = state.get(key)
                if isinstance(item, dict):
                    item["enabled"] = False
                    item["reason"] = "已关闭"
            combat = state.get("combat")
            if isinstance(combat, dict):
                for key in _COMBAT_KEYS:
                    item = combat.get(key)
                    if isinstance(item, dict):
                        item["enabled"] = False
                        item["reason"] = "已关闭"
            revive = state.get("revive")
            if isinstance(revive, dict):
                revive["enabled"] = False
                revive["reason"] = "已关闭"
            hero_revive = state.get("heroRevive")
            if isinstance(hero_revive, dict):
                hero_revive["enabled"] = False
                hero_revive["reason"] = "已关闭"
            return
        if feature == "heroRevive":
            item = state.get("heroRevive")
            if isinstance(item, dict):
                if isinstance(value, dict):
                    try:
                        seconds = float(value.get("seconds") or 0)
                    except (TypeError, ValueError):
                        seconds = 0.0
                else:
                    try:
                        seconds = float(value or 0)
                    except (TypeError, ValueError):
                        seconds = 0.0
                item["enabled"] = seconds > 0
                if seconds > 0:
                    item["seconds"] = seconds
                item["reason"] = "正在开启…" if seconds > 0 else "正在关闭…"
            return
        if feature == "revive":
            item = state.get("revive")
            if isinstance(item, dict):
                closing = value in (0, False, None)
                item["enabled"] = not closing
                if isinstance(value, dict):
                    item["mode"] = value.get("mode", item.get("mode", "item"))
                    try:
                        item["delayMs"] = int(float(value.get("delay", 0.5)) * 1000)
                    except (TypeError, ValueError):
                        pass
                item["reason"] = "正在关闭…" if closing else "正在开启…"
            return
        if feature in _COMBAT_KEYS:
            combat = state.get("combat")
            item = combat.get(feature) if isinstance(combat, dict) else None
            if isinstance(item, dict):
                if feature == "vision":
                    try:
                        mode = int(value)
                    except (TypeError, ValueError):
                        mode = -1
                    item["enabled"] = mode >= 0
                    item["reason"] = "正在应用…" if mode >= 0 else "正在关闭…"
                else:
                    try:
                        number = float(value or 0)
                    except (TypeError, ValueError):
                        number = 0.0
                    item["enabled"] = number > 0
                    item["reason"] = "正在开启…" if number > 0 else "正在关闭…"
            return
        item = state.get(feature)
        if not isinstance(item, dict):
            return
        if feature in ("mob", "boss"):
            try:
                seconds = float(value or 0)
            except (TypeError, ValueError):
                seconds = 0.0
            item["enabled"] = seconds > 0
            if seconds > 0:
                item["seconds"] = seconds
                item["reason"] = f"正在开启（{seconds:g} 秒）…"
            else:
                item["reason"] = "正在关闭…"
        else:
            item["enabled"] = bool(value)
            item["reason"] = "正在开启…" if value else "正在关闭…"

    def stop_all(self) -> None:
        self.set_feature("stopAll", True)
        self._desired.clear()

    def set_names(self, names: Dict[str, Any]) -> None:
        """更新字段名/BOSS 关键字覆盖项；已连接时在后台同步到页面（不卡界面）。"""
        self.names = dict(names or {})
        if not self.running:
            return
        threading.Thread(target=self._push_names, daemon=True).start()

    def _push_names(self) -> None:
        try:
            self.bridge.eval_js(
                "window.__zzAssistNames = %s;" % json.dumps(self.names, ensure_ascii=False)
            )
        except Exception:  # noqa: BLE001
            pass

    def display_stats(self) -> Tuple[int, int]:
        """界面显示的今日红包数 / 金币：JS 与落盘值取大者。"""
        red = self.state.get("redPacket") or {}
        gold_state = self.state.get("gold") or {}
        return (
            self._pick_daily("redCount", red.get("date"), red.get("today")),
            self._pick_daily("gold", gold_state.get("date"), gold_state.get("today")),
        )

    def _pick_daily(self, key: str, date: Any, value: Any) -> int:
        try:
            js_value = max(0, int(value or 0))
        except (TypeError, ValueError):
            js_value = 0
        stored = self._stats.get(key) or {}
        if not date:
            return js_value or int(stored.get("value") or 0)
        if stored.get("date") == date:
            return max(int(stored.get("value") or 0), js_value)
        return js_value

    def self_test(self, feature: str = "boss", seconds: float = 3.0) -> Dict[str, Any]:
        """自测：开启重进地图 + 假装该目标被击杀，看会不会真的重进。

        只写 page 状态，不动游戏里的东西；用来确认「点了没反应」到底是
        辅助没生效，还是界面没刷新。
        """
        if feature not in ("mob", "boss"):
            return {"error": "只支持自测小怪或 BOSS"}
        label = "BOSS" if feature == "boss" else "小怪"
        if not self.running:
            self.start()
        try:
            installed = self.bridge.ensure_runtime(self.names)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"连接游戏页失败：{exc}"}
        if installed.startswith("fail:"):
            return {"error": installed[5:]}
        expression = (
            "(function(){try{"
            "var a=window.__zzAssist;"
            "if(!a)return 'fail:运行时未安装';"
            "var r=a.set(%s,%s);"
            "if(!r||!r.ok)return 'fail:'+((r&&r.message)||'未知原因');"
            "return String(a.__fakeDeath(%s)||'ok');"
            "}catch(e){return 'fail:'+((e&&e.message)||e)}})()"
            % (
                json.dumps(feature),
                json.dumps(float(seconds)),
                json.dumps(feature),
            )
        )
        try:
            raw = self.bridge.eval_js(expression)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"自测指令发送失败：{exc}"}
        text = str(raw or "")
        if not text:
            return {"error": "游戏页无返回，可能刚重载，请稍后再试"}
        if text.startswith("fail:"):
            return {"error": text[5:]}
        self._desired[feature] = float(seconds)
        self._optimistic(feature, float(seconds))
        self._safe_log(f"自测：已假装击杀{label}，{seconds:g} 秒后应重进地图")
        return {"ok": True, "id": text, "feature": feature}

    def list_monsters(self) -> list:
        """读当前地图里活着的怪（名字/坐标/距离）。"""
        try:
            raw = self.bridge.eval_js(
                "JSON.stringify(window.__zzAssist && window.__zzAssist.monsters "
                "? window.__zzAssist.monsters() : [])"
            )
            data = json.loads(raw or "[]")
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []

    def open_daily_tasks(self) -> dict:
        """打开任务面板（日常页）。"""
        try:
            raw = self.bridge.eval_js(
                "JSON.stringify(window.__zzAssist && window.__zzAssist.openDailyTasks "
                "? window.__zzAssist.openDailyTasks() : null)"
            )
            return json.loads(raw or "null") or {}
        except Exception:  # noqa: BLE001
            return {}

    def claim_daily_tasks(self) -> dict:
        """直接调游戏任务服务领取（不开面板，和参考工具一样）。"""
        try:
            raw = self.bridge.eval_js(
                "JSON.stringify(window.__zzAssist && window.__zzAssist.claimDailyViaService "
                "? window.__zzAssist.claimDailyViaService() : null)"
            )
            return json.loads(raw or "null") or {}
        except Exception:  # noqa: BLE001
            return {}

    def complete_daily_with_cards(self) -> dict:
        """用任务卡快速完成日常任务（消耗任务卡）。"""
        try:
            raw = self.bridge.eval_js(
                "JSON.stringify(window.__zzAssist && window.__zzAssist.completeDailyWithCards "
                "? window.__zzAssist.completeDailyWithCards() : null)"
            )
            return json.loads(raw or "null") or {}
        except Exception:  # noqa: BLE001
            return {}

    def daily_task_list(self) -> list:
        """读日常任务：编号/进度/目标/是否已领/目标地图坐标。"""
        js = r"""
(function(){try{
  var mod=null;
  for (var pair of System.entries()) { var ex=pair[1]; if(ex&&ex.SNFA){mod=ex;break;} }
  if(!mod) return '[]';
  var s=mod.SNFA.AWXW;
  var rows=(s&&typeof s.DHPZ==='function')?s.DHPZ():[];
  var out=[];
  for (var i=0;i<rows.length;i++){
    var t=rows[i]; if(!t) continue;
    out.push({id:t.UHNB, progress:(t.GPIC&&t.GPIC.WZMG)||0, target:t.DHDO||t.RSHY||0,
      claimed:!!t.KLBW, mapId:t.MIKP||0, x:t.KRXF||0, y:t.JRGO||0});
  }
  return JSON.stringify(out);
}catch(e){return 'ERR '+((e&&e.message)||e)}})()
"""
        try:
            raw = self.bridge.eval_js(js)
            data = json.loads(raw or "[]")
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []

    def task_go(self, task_name: str) -> dict:
        """任务面板里点指定任务的「前往」。"""
        try:
            raw = self.bridge.eval_js(
                "JSON.stringify(window.__zzAssist && window.__zzAssist.clickTaskGo "
                "? window.__zzAssist.clickTaskGo(%s) : null)" % json.dumps(task_name, ensure_ascii=False)
            )
            return json.loads(raw or "null") or {}
        except Exception:  # noqa: BLE001
            return {}

    def task_teleport_track(self) -> dict:
        """点弹窗里的「传送并追踪」。"""
        try:
            raw = self.bridge.eval_js(
                "JSON.stringify(window.__zzAssist && window.__zzAssist.clickTeleportTrack "
                "? window.__zzAssist.clickTeleportTrack() : null)"
            )
            return json.loads(raw or "null") or {}
        except Exception:  # noqa: BLE001
            return {}

    def reload_map_now(self) -> bool:
        """立刻重进当前地图（刷怪）。"""
        try:
            self.bridge.eval_js(
                "(function(){try{var a=window.__zzAssist;"
                "if(a&&a.reloadMapNow){a.reloadMapNow();}return 'ok'}catch(e){return 'ERR'}})()"
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    def share_game(self) -> dict:
        """调游戏分享入口（分享任务用）。"""
        try:
            raw = self.bridge.eval_js(
                "JSON.stringify(window.__zzAssist && window.__zzAssist.shareGame "
                "? window.__zzAssist.shareGame() : null)"
            )
            return json.loads(raw or "null") or {}
        except Exception:  # noqa: BLE001
            return {}

    def goto_task_point(self, map_id: int, x: int, y: int) -> str:
        """按任务给的目标点传送过去（游戏自己的 NMKO）。"""
        js = (
            "(function(){try{var w=window.VKXX;if(!w) return 'no';"
            "if(typeof w.NMKO!=='function') return 'no NMKO';"
            "w.NMKO(%d,%d,%d,(function(){}));return 'ok';"
            "}catch(e){return 'ERR '+((e&&e.message)||e)}})()" % (int(map_id), int(x), int(y))
        )
        try:
            return str(self.bridge.eval_js(js))
        except Exception as exc:  # noqa: BLE001
            return "ERR " + str(exc)

    def list_heroes(self) -> list:
        """读游戏里拥有的英雄列表（编号/名字/品质）。"""
        try:
            raw = self.bridge.eval_js(
                "JSON.stringify(window.__zzAssist && window.__zzAssist.heroes "
                "? window.__zzAssist.heroes() : [])"
            )
            data = json.loads(raw or "[]")
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []

    def diagnose(self) -> Dict[str, Any]:
        """同步诊断（UI 点按钮时调用一次，最多等 6 秒）。"""
        if not self.running:
            self.start()
        try:
            installed = self.bridge.ensure_runtime(self.names)
            if installed.startswith("fail:"):
                return {"error": installed[5:]}
            return self.bridge.diagnose()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    # ---- 内部 ----

    def _apply(self, feature: str, value: Any) -> None:
        label = _FEATURE_LABEL.get(feature, feature)
        try:
            result = self.bridge.command(feature, value)
        except Exception as exc:  # noqa: BLE001
            self._safe_log(f"辅助[{label}] 发送失败：{exc}")
            return
        ok = bool(result.get("ok"))
        text = str(result.get("message") or "")
        if ok:
            self._safe_log(f"辅助[{label}] {text}")
        else:
            self._desired.pop(feature, None)
            self._safe_log(f"辅助[{label}] 未生效：{text}")

    def _safe_log(self, text: str) -> None:
        try:
            self.log(text)
        except Exception:  # noqa: BLE001
            pass

    def _reapply(self) -> None:
        """页面重载后运行时是新装的，把用户之前开着的功能补回来。"""
        for feature, value in list(self._desired.items()):
            if feature == "stopAll":
                continue
            self._apply(feature, value)

    def _click_confirm_if_needed(self, snap: Dict[str, Any]) -> None:
        """全灭面板点「放弃」后弹的确认框：按页面给的坐标点一下「确认」。"""
        revive = (snap or {}).get("revive") or {}
        point = revive.get("confirmAt") or {}
        try:
            x = float(point.get("x"))
            y = float(point.get("y"))
        except (TypeError, ValueError):
            return
        if x <= 0 or y <= 0:
            return
        now = time.monotonic()
        if now - self._last_confirm_click < 1.5:
            return
        self._last_confirm_click = now
        try:
            call = getattr(self.bridge, "_call", None)
            if call is None:
                return
            call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none", "buttons": 0})
            time.sleep(0.03)
            call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1})
            time.sleep(0.03)
            call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "buttons": 0, "clickCount": 1})
            self._status_log("全灭面板弹出确认框，已自动点「确认」")
        except Exception as exc:  # noqa: BLE001
            self._status_log("点确认框失败：%s" % exc)

    def _persist(self, snap: Dict[str, Any]) -> None:
        red = snap.get("redPacket") or {}
        gold = snap.get("gold") or {}
        before = json.dumps(self._stats, sort_keys=True)
        _merge_daily(self._stats, "redCount", str(red.get("date") or ""), red.get("today"))
        _merge_daily(self._stats, "gold", str(gold.get("date") or ""), gold.get("today"))
        if json.dumps(self._stats, sort_keys=True) != before:
            _save_stats(self._stats)

    def _loop(self) -> None:
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001
            # 线程绝不能因为一次异常就永久停掉（否则界面会一直停在旧日志上）
            try:
                self.log(f"辅助线程异常退出：{exc}")
            except Exception:  # noqa: BLE001
                pass
        finally:
            self.running = False
            self.bridge.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            # 1) 指令优先，避免开关延迟
            while True:
                try:
                    feature, value = self._queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    installed = self.bridge.ensure_runtime(self.names)
                except Exception as exc:  # noqa: BLE001
                    self._status_log(f"辅助：等待游戏窗口（{exc}）")
                    continue
                if installed.startswith("fail:"):
                    self._status_log(f"辅助：已连上游戏页，但运行时还没装好（{installed[5:]}）")
                    continue
                if installed == "installed":
                    # 页面刚重载：先把之前开着的功能补回来
                    self._reapply()
                self._apply(feature, value)

            # 2) 取快照
            self._poll_once()

            try:
                self.on_state(self.state)
            except Exception:  # noqa: BLE001
                pass

            self._stop.wait(POLL_SECONDS)

    def _poll_once(self) -> None:
        """连一次游戏页、取一份快照，并把「为什么没就绪」说清楚。"""
        try:
            installed = self.bridge.ensure_runtime(self.names)
        except Exception as exc:  # noqa: BLE001
            self.state = {"ok": False, "error": str(exc)}
            self._status_log(f"辅助：等待游戏窗口（{exc}）")
            return
        if installed.startswith("fail:"):
            reason = installed[5:] or "未知原因"
            self.state = {"ok": False, "error": reason}
            self._status_log(f"辅助：已连上游戏页，但运行时还没装好（{reason}）")
            return
        if installed == "installed":
            self._status_log("辅助：已连上游戏页，运行时注入完成")
            self._reapply()
        try:
            snap = self.bridge.snapshot()
        except Exception as exc:  # noqa: BLE001
            self.state = {"ok": False, "error": str(exc)}
            self._status_log(f"辅助：游戏页断开（{exc}），正在重连…")
            return
        if not snap:
            self.state = {"ok": False, "error": "游戏页还没有返回状态"}
            self._status_log("辅助：已连上游戏页，等待页面加载完成…")
            return
        self.state = snap
        self._persist(snap)
        self._click_confirm_if_needed(snap)
        if snap.get("ready"):
            self._status_log(
                "辅助：运行时已就绪（版本 %s），点「开启」就生效"
                % (snap.get("build") or "未知")
            )
        elif snap.get("missingMapping"):
            self._status_log("辅助：该游戏版本还没有字段映射（补 app/assist_mapping.json）")
        else:
            self._status_log("辅助：已连上游戏页，等待进入游戏主界面…")
