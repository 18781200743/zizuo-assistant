"""运行命令录制：在游戏页监听真实鼠标/滚轮，生成指令脚本。

和 game_storage_sync 一样，录制的是游戏页内的真实输入：
- 点击 -> 点击(X%,Y%)
- 摇杆拖动 -> 移动(角度,秒)
- 滚轮 -> 滚轮(方向,量)
事件之间的间隔 -> 等待(秒)
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

INSTALL_JS = r"""
(function () {
  if (window.__zzRecorderV2) return 'ok';
  var rec = { events: [], down: null, wheel: null };
  window.__zzRecorderV2 = rec;
  function pct(x, y) {
    return [
      Math.round(x / Math.max(1, window.innerWidth) * 10000) / 100,
      Math.round(y / Math.max(1, window.innerHeight) * 10000) / 100
    ];
  }
  function push(ev) {
    ev.t = Date.now();
    rec.events.push(ev);
    if (rec.events.length > 5000) rec.events.shift();
  }
  function wheelDir(dx, dy) {
    if (Math.abs(dx) > Math.abs(dy)) return dx < 0 ? '左' : '右';
    return dy < 0 ? '上' : '下';
  }
  function flushWheel() {
    var w = rec.wheel;
    rec.wheel = null;
    if (w && w.amount > 0.05) {
      push({ kind: 'wheel', dir: w.dir, amount: Math.round(w.amount * 100) / 100 });
    }
  }
  function onDown(e) {
    if (e.button !== 0 || e.isTrusted === false) return;
    flushWheel();
    var p = pct(e.clientX, e.clientY);
    rec.down = {
      x: p[0], y: p[1], t: Date.now(), moved: false,
      angle: null, segT: Date.now()
    };
  }
  function onMove(e) {
    var d = rec.down;
    if (!d) return;
    var p = pct(e.clientX, e.clientY);
    var total = Math.hypot(p[0] - d.x, p[1] - d.y);
    if (total > 0.6) d.moved = true;
    if (!d.moved) return;
    var angle = Math.atan2(p[0] - d.x, -(p[1] - d.y)) * 180 / Math.PI;
    angle = Math.round(((angle + 360) % 360) * 10) / 10;
    if (d.angle === null) {
      d.angle = angle;
      d.segT = Date.now();
      return;
    }
    var diff = Math.abs((((angle - d.angle + 180) % 360) + 360) % 360 - 180);
    var duration = (Date.now() - d.segT) / 1000;
    if (diff > 22 && duration >= 0.1) {
      push({ kind: 'move', angle: d.angle, seconds: Math.round(duration * 100) / 100 });
      d.angle = angle;
      d.segT = Date.now();
    }
  }
  function onUp(e) {
    var d = rec.down;
    rec.down = null;
    if (!d) return;
    var p = pct(e.clientX, e.clientY);
    if (d.moved && d.angle !== null) {
      var duration = (Date.now() - d.segT) / 1000;
      if (duration >= 0.1) {
        push({ kind: 'move', angle: d.angle, seconds: Math.round(duration * 100) / 100 });
        return;
      }
    }
    push({ kind: 'click', x: p[0], y: p[1] });
  }
  function onWheel(e) {
    if (e.isTrusted === false) return;
    var dx = Number(e.deltaX) || 0;
    var dy = Number(e.deltaY) || 0;
    if (e.shiftKey && dx === 0 && dy !== 0) { dx = dy; dy = 0; }
    var dir = wheelDir(dx, dy);
    var amount = Math.abs(dx) > Math.abs(dy)
      ? Math.abs(dx) / Math.max(1, window.innerWidth) * 100
      : Math.abs(dy) / Math.max(1, window.innerHeight) * 100;
    if (e.deltaMode === 1) amount *= 8;
    if (e.deltaMode === 2) amount *= 100;
    if (!rec.wheel || rec.wheel.dir !== dir) {
      flushWheel();
      rec.wheel = { dir: dir, amount: 0 };
    }
    rec.wheel.amount += amount;
  }
  document.addEventListener('pointerdown', onDown, true);
  document.addEventListener('pointermove', onMove, true);
  document.addEventListener('pointerup', onUp, true);
  document.addEventListener('pointercancel', onUp, true);
  document.addEventListener('wheel', onWheel, { capture: true, passive: true });
  rec.remove = function () {
    document.removeEventListener('pointerdown', onDown, true);
    document.removeEventListener('pointermove', onMove, true);
    document.removeEventListener('pointerup', onUp, true);
    document.removeEventListener('pointercancel', onUp, true);
    document.removeEventListener('wheel', onWheel, true);
    flushWheel();
    delete window.__zzRecorderV2;
  };
  return 'ok';
})()
"""

POLL_JS = (
    "JSON.stringify((window.__zzRecorderV2 && window.__zzRecorderV2.events.splice(0)) || [])"
)
STOP_JS = r"""
(function () {
  var rec = window.__zzRecorderV2;
  if (!rec) return '[]';
  var events = rec.events.splice(0);
  rec.remove();
  return JSON.stringify(events);
})()
"""


class CommandRecorder:
    def __init__(
        self,
        bridge_provider: Callable[[], Any],
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._bridge_provider = bridge_provider
        self._log = log or (lambda _msg: None)
        self.recording = False
        self._last_t = 0

    def start(self) -> bool:
        bridge = self._bridge_provider()
        if bridge is None:
            return False
        try:
            result = str(bridge.eval_js(INSTALL_JS) or "")
        except Exception as exc:  # noqa: BLE001
            self._log("录制启动失败：%s" % exc)
            return False
        if result.strip() != "ok":
            self._log("录制启动失败：%s" % result.strip())
            return False
        self._last_t = 0
        self.recording = True
        return True

    def poll(self) -> List[str]:
        bridge = self._bridge_provider()
        if bridge is None:
            return []
        try:
            raw = bridge.eval_js(POLL_JS)
        except Exception:  # noqa: BLE001
            return []
        return self._to_lines(raw)

    def stop(self) -> List[str]:
        self.recording = False
        bridge = self._bridge_provider()
        if bridge is None:
            return []
        try:
            raw = bridge.eval_js(STOP_JS)
        except Exception:  # noqa: BLE001
            return []
        return self._to_lines(raw)

    def _to_lines(self, raw: Any) -> List[str]:
        try:
            events = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return []
        if not isinstance(events, list):
            return []
        lines: List[str] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                stamp = int(event.get("t") or 0)
            except (TypeError, ValueError):
                stamp = 0
            if self._last_t and stamp > self._last_t:
                delay = (stamp - self._last_t) / 1000.0
                if delay >= 0.05:
                    lines.append("等待(%.2f)" % delay)
            if stamp:
                self._last_t = stamp
            kind = str(event.get("kind") or "")
            if kind == "click":
                lines.append(
                    "点击(%.2f,%.2f)"
                    % (float(event.get("x") or 0), float(event.get("y") or 0))
                )
            elif kind == "wheel":
                lines.append(
                    "滚轮(%s,%.2f)"
                    % (event.get("dir") or "下", float(event.get("amount") or 0))
                )
            elif kind == "move":
                lines.append(
                    "移动(%.1f,%.2f)"
                    % (float(event.get("angle") or 0), float(event.get("seconds") or 0))
                )
        return lines
