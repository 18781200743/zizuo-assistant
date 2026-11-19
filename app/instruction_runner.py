"""运行指令：把「指令脚本」逐行发给游戏页执行。

指令语言来自参考工具（一行一条，例如 传送(3,2) / 移动(上,3) / 采集(活动) / 回城()）。

已支持：
    等待(秒) / 停止() / 点击(X,Y) / 滚轮(方向,量,X,Y) / 移动(方向,秒) / 回城() / 传送(章节,地图[,大陆])
实验性：
    采集(物品)  —— 找到最近的资源点，瞬移过去等游戏自己采集
暂未支持：
    开宝箱(...) / 跳转(槽位)
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .instruction_library import ScriptError, parse_script

DIRECTIONS: Dict[str, tuple] = {
    "上": (0.0, -1.0),
    "下": (0.0, 1.0),
    "左": (-1.0, 0.0),
    "右": (1.0, 0.0),
    "左上": (-0.7071, -0.7071),
    "右上": (0.7071, -0.7071),
    "左下": (-0.7071, 0.7071),
    "右下": (0.7071, 0.7071),
}

# 采集关键字（和参考工具的说明一致）
COLLECT_KEYWORDS = {
    "木材": ("木材", "木头"),
    "矿石": ("矿",),
    "蓝矿石": ("蓝矿",),
    "红矿石": ("红矿",),
    "彩矿": ("彩矿", "矿石"),
    "宝石": ("宝石",),
    "幸运草": ("幸运草",),
    "训练石": ("训练石",),
    "肉块": ("肉",),
    "活动": ("活动", "宝箱", "礼物", "采集"),
}


def _config_path() -> Path:
    return Path(__file__).resolve().parent / "automation_config.json"


def load_automation_config() -> Dict[str, Any]:
    try:
        return json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def with_current_build(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """映射表里没写 current 时，默认用第一个 build（目前只有 f61cf）。"""
    data = dict(mapping or {})
    builds = data.get("builds") or {}
    if "current" not in data and builds:
        data["current"] = sorted(builds.keys())[0]
    return data


# ------------------------------------------------------------------ 注入的输入层

JS_INSTALL = r"""
(function () {
  if (window.__zzCmd && window.__zzCmd.v === 3) return 'ok';
  var find = function (x, y) {
    try { return document.elementFromPoint(x, y) || document.body || document.documentElement; }
    catch (e) { return document.body || document.documentElement; }
  };
  var make = function (type, x, y, buttons) {
    var opts = {
      bubbles: true, cancelable: true, composed: true, view: window,
      clientX: x, clientY: y, screenX: x, screenY: y,
      pointerId: 1, pointerType: 'mouse', isPrimary: true,
      button: 0, buttons: buttons, detail: 1
    };
    try {
      if (type.indexOf('pointer') === 0) return new PointerEvent(type, opts);
      if (type.indexOf('wheel') === 0) return new WheelEvent(type, opts);
      if (type.indexOf('touch') === 0) {
        var touch = new Touch({ identifier: 1, target: find(x, y), clientX: x, clientY: y,
          pageX: x, pageY: y, screenX: x, screenY: y, radiusX: 1, radiusY: 1, force: 1 });
        var list = type === 'touchend' ? [] : [touch];
        return new TouchEvent(type, { bubbles: true, cancelable: true, composed: true,
          touches: list, targetTouches: list, changedTouches: [touch] });
      }
      return new MouseEvent(type, opts);
    } catch (e) { return null; }
  };
  var fire = function (el, type, x, y, buttons) {
    var ev = make(type, x, y, buttons);
    if (ev) { try { el.dispatchEvent(ev); } catch (e) { /* ignore */ } }
  };
  var el = null;
  window.__zzCmd = {
    v: 3,
    click: function (x, y) {
      var t = find(x, y);
      fire(t, 'pointerdown', x, y, 1); fire(t, 'mousedown', x, y, 1);
      fire(t, 'pointerup', x, y, 0); fire(t, 'mouseup', x, y, 0);
      fire(t, 'click', x, y, 0);
      return 'ok';
    },
    wheel: function (dx, dy, x, y) {
      var t = find(x, y);
      var ev = make('wheel', x, y, 0);
      if (ev) {
        try {
          Object.defineProperty(ev, 'deltaX', { value: dx });
          Object.defineProperty(ev, 'deltaY', { value: dy });
        } catch (e) { /* ignore */ }
        try { t.dispatchEvent(ev); } catch (e) { /* ignore */ }
      }
      return 'ok';
    },
    down: function (x, y) {
      el = find(x, y);
      fire(el, 'pointerdown', x, y, 1); fire(el, 'mousedown', x, y, 1);
      return 'ok';
    },
    move: function (x, y) {
      var t = el || find(x, y);
      fire(t, 'pointermove', x, y, 1); fire(t, 'mousemove', x, y, 1);
      return 'ok';
    },
    up: function (x, y) {
      var t = el || find(x, y);
      fire(t, 'pointerup', x, y, 0); fire(t, 'mouseup', x, y, 0);
      el = null;
      return 'ok';
    },
    size: function () { return { w: window.innerWidth, h: window.innerHeight }; }
  };
  return 'ok';
})()
"""


def _js_home(mapping: Dict[str, Any]) -> str:
    """回城：走游戏自己的返回点（营地坐标 TCLA/CLAT）。"""
    return r"""
(function () {
  try {
    var m = %s;
    var b = (m.builds || {})[m.current] || {};
    var rev = b.revive || {};
    var ct = rev.campTeleport || {};
    var names = ct.campCoords || ['TCLA', 'CLAT'];
    var world = window[(b.map || {}).holder || 'VKXX'];
    var core = world[(b.map || {}).core || 'ZRRU'];
    if (!world || !core) return 'ERR 游戏还没进地图';
    var spot = null;
    for (var i = 0; i < names.length; i += 1) {
      try {
        var fn = core[names[i]];
        if (typeof fn === 'function') {
          var p = fn.call(core);
          if (p && isFinite(p.x) && isFinite(p.y)) { spot = p; break; }
        }
      } catch (e) { /* 换下一个 */ }
    }
    if (!spot) return 'ERR 读不到营地坐标';
    if (typeof world.ZAVH !== 'function') return 'ERR 游戏没有传送入口';
    world.ZAVH(spot.x, spot.y, true, function () {});
    return 'ok ' + Math.round(spot.x) + ',' + Math.round(spot.y);
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % _js(mapping)


def _js_enter_map(mapping: Dict[str, Any], map_id: int, teleport_id: int) -> str:
    """传送：先重进地图，再瞬移到对应传送点（传送点的 pointID 就是 teleport_id）。"""
    return r"""
(function () {
  try {
    var m = %s;
    var b = (m.builds || {})[m.current] || {};
    var world = window[(b.map || {}).holder || 'VKXX'];
    if (!world) return 'ERR 游戏还没进地图';
    if (typeof world.XIPH !== 'function') return 'ERR 游戏没有切图入口';
    window.__zzCmdMapWait = { map: %d, at: Date.now() };
    world.XIPH(%d);
    return 'ok';
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % (_js(mapping), int(map_id), int(map_id))


def _js_teleport_point(mapping: Dict[str, Any], teleport_id: int) -> str:
    """在当前地图里找 pointID == teleport_id 的传送点，瞬移过去。"""
    return r"""
(function () {
  try {
    var m = %s;
    var b = (m.builds || {})[m.current] || {};
    var mc = b.map || {};
    var u = mc.unit || {};
    var world = window[mc.holder || 'VKXX'];
    var core = world[mc.core || 'ZRRU'];
    var mgr = core[mc.manager || 'PFTN']();
    var best = null;
    var collect = function (index, unit) {
      if (!unit) return false;
      var point = unit[u.point || 'YLYI'] || {};
      if (String(point.pointID) === %s) {
        best = { x: unit.ADUX, y: unit.WUZI, name: String(point.name || '') };
        return true;
      }
      return false;
    };
    var units = core[mc.container || 'UFYI'];
    if (units && typeof units[mc.each || 'OOIV'] === 'function') {
      units[mc.each || 'OOIV'](collect);
    }
    if (!best) return 'none';
    if (typeof world.ZAVH !== 'function') return 'ERR 游戏没有传送入口';
    world.ZAVH(best.x, best.y, false, function () {});
    return 'ok ' + best.name;
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % (_js(mapping), _js(str(teleport_id)))


def _js_collect_target(keyword: str, radius: int = 3) -> str:
    """在当前地图找最近的资源点（按名字关键字），瞬移过去。"""
    words = list(COLLECT_KEYWORDS.get(keyword, (keyword,)))
    return r"""
(function () {
  try {
    var words = %s;
    var m = window.__zzAssistMapping || { builds: {} };
    var b = (m.builds || {})[m.current] || {};
    var mc = b.map || {};
    var u = mc.unit || {};
    var world = window[mc.holder || 'VKXX'];
    var core = world[mc.core || 'ZRRU'];
    var holder = core;
    var units = core[mc.container || 'UFYI'];
    var ids = [];
    try {
      var mgr = core[mc.manager || 'PFTN']();
      var team = core[mc.team || 'PMMA'](world[mc.player || 'KBVD']);
      ids = mgr[mc.members || 'YUWW'](team) || [];
    } catch (e) { /* ignore */ }
    var leader = ids.length ? core[mc.read || 'YVSZ'](ids[0]) : null;
    var lx = leader ? leader.ADUX : 0;
    var ly = leader ? leader.WUZI : 0;
    var best = null;
    var visit = function (index, unit) {
      if (!unit) return false;
      var point = unit[u.point || 'YLYI'] || {};
      var name = String(point.name || '');
      if (!name) return false;
      var tag = String(unit[u.tag || 'CLRB'] || '');
      var text = (name + ' ' + tag).toLowerCase();
      var hit = words.some(function (w) { return text.indexOf(String(w).toLowerCase()) >= 0; });
      if (!hit) return false;
      var d = Math.hypot(unit.ADUX - lx, unit.WUZI - ly);
      if (!best || d < best.d) best = { x: unit.ADUX, y: unit.WUZI, name: name, d: d };
      return false;
    };
    if (units && typeof units[mc.each || 'OOIV'] === 'function') units[mc.each || 'OOIV'](visit);
    if (!best) return 'none';
    if (typeof world.ZAVH !== 'function') return 'ERR 游戏没有传送入口';
    world.ZAVH(best.x, best.y, false, function () {});
    return 'ok ' + best.name + ' (' + Math.round(best.x) + ',' + Math.round(best.y) + ')';
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % _js(words)


def _js_map_state(mapping: Dict[str, Any]) -> str:
    return r"""
(function () {
  try {
    var m = %s;
    var b = (m.builds || {})[m.current] || {};
    var mc = b.map || {};
    var world = window[mc.holder || 'VKXX'];
    var st = world[mc.state || 'AGDA'];
    return String(st ? st[mc.mapId || 'POMD'] : '');
  } catch (e) { return ''; }
})()
""" % _js(mapping)


class InstructionRunner:
    """单次执行的指令运行器（和参考工具一样：跑完就停，不做无限循环）。"""

    def __init__(
        self,
        bridge_provider: Callable[[], Any],
        log: Optional[Callable[[str], None]] = None,
        on_step: Optional[Callable[[int, int, str], None]] = None,
        on_finished: Optional[Callable[[bool, str], None]] = None,
    ) -> None:
        self._bridge_provider = bridge_provider
        self._log = log or (lambda _msg: None)
        self._on_step = on_step or (lambda _i, _n, _text: None)
        self._on_finished = on_finished or (lambda _ok, _msg: None)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.running = False
        self.title = ""

    # ------------------------------------------------------------ 对外

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def start(self, title: str, content: str, mapping: Optional[Dict[str, Any]] = None) -> None:
        if self.running:
            raise RuntimeError("上一条指令还在跑，先点「停止」")
        steps = parse_script(content)
        if not steps:
            raise ScriptError("这条指令是空的")
        self._stop.clear()
        self.title = title
        self.running = True
        self._thread = threading.Thread(
            target=self._run, args=(title, steps, with_current_build(mapping or {})), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            bridge = self._bridge_provider()
            bridge.eval_js("(window.__zzCmd && window.__zzCmd.up && window.__zzCmd.up(0,0));'ok'")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 执行

    def _log_line(self, text: str) -> None:
        self._log(text)

    def _eval(self, js: str) -> Any:
        bridge = self._bridge_provider()
        if bridge is None:
            raise RuntimeError("还没连上游戏窗口")
        return bridge.eval_js(js)

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, float(seconds))
        while not self._stop.is_set():
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.1, left))
        raise _Stopped()

    def _run(self, title: str, steps: List[Dict[str, Any]], mapping: Dict[str, Any]) -> None:
        ok = False
        message = "已完成"
        total = len(steps)
        try:
            self._log_line("▶ 运行指令：%s（%d 步）" % (title, total))
            self._eval(JS_INSTALL)
            for index, step in enumerate(steps, 1):
                if self._stop.is_set():
                    raise _Stopped()
                name = step["name"]
                args = step["args"]
                self._on_step(index, total, step["raw"])
                self._log_line("  [%d/%d] %s" % (index, total, step["raw"]))
                self._exec(name, args, mapping)
            ok = True
            message = "指令已跑完"
        except _Stopped:
            message = "已停止"
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or exc.__class__.__name__
            self._log_line("✖ " + message)
        finally:
            try:
                self._eval("(window.__zzCmd && window.__zzCmd.up && window.__zzCmd.up(0,0));'ok'")
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                self.running = False
            self._log_line(("✔ " if ok else "■ ") + message)
            self._on_finished(ok, message)

    # ------------------------------------------------------------ 指令

    def _exec(self, name: str, args: List[str], mapping: Dict[str, Any]) -> None:
        if name in ("等待", "延时", "wait"):
            self._sleep(self._number(args, 0, "等待秒数"))
            return
        if name in ("停止", "结束", "stop"):
            raise _Stopped()
        if name in ("点击", "单击", "click"):
            self._click(self._number(args, 0, "X"), self._number(args, 1, "Y"))
            return
        if name in ("滚轮", "wheel"):
            self._wheel(args)
            return
        if name in ("移动", "move"):
            self._move(args)
            return
        if name in ("回城", "home"):
            self._home(mapping)
            return
        if name in ("传送", "teleport"):
            self._teleport(args, mapping)
            return
        if name in ("采集", "collect"):
            self._collect(args)
            return
        if name in ("开宝箱", "chest"):
            raise RuntimeError("开宝箱还没做（正在研究游戏开箱接口）")
        if name in ("跳转", "jump"):
            raise RuntimeError("跳转(槽位)还没做（需要先做指令槽位）")
        raise RuntimeError("不认识的指令：%s" % name)

    # ---- 基础指令

    @staticmethod
    def _number(args: List[str], index: int, label: str) -> float:
        if index >= len(args) or str(args[index]).strip() == "":
            raise RuntimeError("%s 缺少参数" % label)
        try:
            return float(str(args[index]).strip())
        except ValueError as exc:
            raise RuntimeError("%s 不是数字：%s" % (label, args[index])) from exc

    def _click(self, x: float, y: float) -> None:
        result = self._eval("window.__zzCmd.click(%s,%s)" % (_js(x), _js(y)))
        if str(result) != "ok":
            raise RuntimeError("点击失败：%s" % result)
        self._sleep(0.15)

    def _wheel(self, args: List[str]) -> None:
        if not args:
            raise RuntimeError("滚轮 缺少参数")
        direction = str(args[0]).strip()
        amount = self._number(args, 1, "滚动量") if len(args) > 1 else 3
        x = self._number(args, 2, "X") if len(args) > 2 else None
        y = self._number(args, 3, "Y") if len(args) > 3 else None
        sign = -1 if direction in ("上", "up") else 1
        if x is None or y is None:
            js = (
                "(function(){var s=window.__zzCmd.size();"
                "window.__zzCmd.wheel(0,%s,s.w/2,s.h/2);return 'ok'})()" % _js(sign * amount)
            )
        else:
            js = "window.__zzCmd.wheel(0,%s,%s,%s)" % (_js(sign * amount), _js(x), _js(y))
        self._eval(js)
        self._sleep(0.2)

    def _move(self, args: List[str]) -> None:
        if not args:
            raise RuntimeError("移动 缺少方向")
        direction = str(args[0]).strip()
        if direction not in DIRECTIONS:
            raise RuntimeError("方向要写：上/下/左/右/左上/右上/左下/右下")
        seconds = self._number(args, 1, "移动秒数") if len(args) > 1 else 1.0
        dx, dy = DIRECTIONS[direction]
        cfg = load_automation_config()
        center = cfg.get("movement_center") or {"x": 50.0, "y": 78.0}
        radius = float(cfg.get("movement_radius") or 8.0)
        js = (
            "(function(){var s=window.__zzCmd.size();"
            "var cx=s.w*%s/100, cy=s.h*%s/100;"
            "var r=Math.min(s.w,s.h)*%s/100;"
            "window.__zzCmd.down(cx,cy);"
            "window.__zzCmd.move(cx+%s*r, cy+%s*r);"
            "return 'ok'})()"
            % (_js(float(center.get("x") or 50.0)), _js(float(center.get("y") or 78.0)),
               _js(radius), _js(dx), _js(dy))
        )
        self._eval(js)
        try:
            end = time.monotonic() + max(0.05, seconds)
            while time.monotonic() < end:
                if self._stop.is_set():
                    raise _Stopped()
                time.sleep(0.08)
                self._eval(
                    "(function(){var s=window.__zzCmd.size();"
                    "var cx=s.w*%s/100, cy=s.h*%s/100;"
                    "var r=Math.min(s.w,s.h)*%s/100;"
                    "window.__zzCmd.move(cx+%s*r, cy+%s*r);return 'ok'})()"
                    % (_js(float(center.get("x") or 50.0)), _js(float(center.get("y") or 78.0)),
                       _js(radius), _js(dx), _js(dy))
                )
        finally:
            try:
                self._eval("window.__zzCmd.up(0,0)")
            except Exception:  # noqa: BLE001
                pass
        self._sleep(0.2)

    # ---- 地图指令

    def _home(self, mapping: Dict[str, Any]) -> None:
        result = self._eval(_js_home(mapping))
        if str(result).startswith("ERR"):
            raise RuntimeError("回城失败：%s" % str(result)[4:])
        self._sleep(1.6)

    def _teleport(self, args: List[str], mapping: Dict[str, Any]) -> None:
        if len(args) < 2:
            raise RuntimeError("传送 需要「章节,地图」两个参数")
        try:
            chapter = int(float(str(args[0]).strip()))
            section = int(float(str(args[1]).strip()))
        except ValueError as exc:
            raise RuntimeError("传送 的章节/地图必须是数字") from exc
        continent = 0
        if len(args) > 2 and str(args[2]).strip() != "":
            try:
                continent = int(float(str(args[2]).strip()))
            except ValueError as exc:
                raise RuntimeError("传送 的大陆必须是数字") from exc
        cfg = load_automation_config()
        table = cfg.get("teleport_mappings") or {}
        key = "%d.%d.%d" % (chapter, section, continent)
        spot = table.get(key)
        if not spot:
            # 没写大陆时，取这张地图的第一个传送点
            for k, v in table.items():
                if k.startswith("%d.%d." % (chapter, section)):
                    spot = v
                    break
        if not spot:
            raise RuntimeError("地图 %d-%d 还没收录传送点" % (chapter, section))
        map_id = int(spot.get("map_id"))
        teleport_id = int(spot.get("teleport_id") or 0)
        self._log_line("    传送 → %s（地图 %d，传送点 %d）" % (spot.get("name") or "", map_id, teleport_id))
        result = self._eval(_js_enter_map(mapping, map_id, teleport_id))
        if str(result).startswith("ERR"):
            raise RuntimeError("传送失败：%s" % str(result)[4:])
        state_js = _js_map_state(mapping)
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            if self._stop.is_set():
                raise _Stopped()
            time.sleep(0.4)
            current = str(self._eval(state_js) or "")
            if current == str(map_id):
                break
        else:
            raise RuntimeError("进地图超时（地图 %d）" % map_id)
        time.sleep(1.0)
        if teleport_id:
            picked = self._eval(_js_teleport_point(mapping, teleport_id))
            if str(picked).startswith("ok"):
                self._log_line("    已到传送点 %s" % str(picked)[3:])
            elif str(picked) == "none":
                self._log_line("    地图里没找到传送点 %d，留在入口" % teleport_id)
            else:
                self._log_line("    传送点定位失败：%s" % str(picked))
        time.sleep(0.8)

    def _collect(self, args: List[str]) -> None:
        keyword = str(args[0]).strip() if args else "活动"
        hits = 0
        for _ in range(4):
            if self._stop.is_set():
                raise _Stopped()
            result = self._eval(_js_collect_target(keyword))
            text = str(result)
            if text.startswith("ERR"):
                raise RuntimeError("采集失败：%s" % text[4:])
            if text == "none":
                break
            hits += 1
            self._log_line("    采集 %s" % text[3:])
            time.sleep(2.2)
        if not hits:
            self._log_line("    附近没有「%s」资源，跳过" % keyword)


class _Stopped(Exception):
    """内部用：用户点了停止。"""
