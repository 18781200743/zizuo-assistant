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

from .instruction_library import ScriptError, library_instructions, load_library, parse_script

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

# 采集关键字（和参考工具的说明一致；游戏里的资源点名是「火龙岛红树」「蓝矿」这种）
COLLECT_KEYWORDS = {
    "木材": ("红树", "树", "木头", "木材", "木"),
    "矿石": ("矿",),
    "蓝矿石": ("蓝矿",),
    "红矿石": ("红矿",),
    "彩矿": ("彩矿", "矿石"),
    "宝石": ("宝石",),
    "幸运草": ("幸运草",),
    "训练石": ("训练石",),
    "肉块": ("肉",),
    "活动": ("*",),
}

# 「日常那种」资源：树、木头、矿、灌木、草、肉、藤。
# 采集(活动) 会把它们排除掉，只采活动专属的采集物（宝箱、光球、活动道具等）。
DAILY_RESOURCE_WORDS = ("树", "木", "矿", "灌木", "草", "肉", "藤", "仙人掌")

# 采集(活动) 只在角色周围这个半径内找（游戏坐标）；找不到就原地不动，不为了采一棵树飞很远。
DEFAULT_COLLECT_RADIUS = 900


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
    """传送：走游戏原生传送接口（VKXX.LUSS(地图, 传送点, 回调)），能落到指定传送点。"""
    return r"""
(function () {
  try {
    var m = %s;
    var b = (m.builds || {})[m.current] || {};
    var mc = b.map || {};
    var world = window[mc.holder || 'VKXX'];
    if (!world) return 'ERR 游戏还没进地图';
    var api = world[mc.teleport || 'LUSS'];
    if (typeof api !== 'function') return 'ERR 游戏没有传送入口';
    window.__zzTp = { done: false, at: Date.now(), map: %d, point: %d };
    api.call(world, %d, %d, function () { window.__zzTp.done = true; });
    return 'ok';
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % (_js(mapping), int(map_id), int(teleport_id), int(map_id), int(teleport_id))


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
    var mgr = core[mc.manager || 'PFTN']();
    var units = mgr[mc.container || 'UFYI']();
    if (units && typeof units[mc.each || 'OOIV'] === 'function') {
      units[mc.each || 'OOIV'](collect);
    }    if (!best) return 'none';
    if (typeof world.ZAVH !== 'function') return 'ERR 游戏没有传送入口';
    world.ZAVH(best.x, best.y, false, function () {});
    return 'ok ' + best.name;
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % (_js(mapping), _js(str(teleport_id)))


def _js_chest_service() -> str:
    """宝箱服务 = 模块服务容器 HQQD 里的 HDPD（实测）。"""
    return r"""
(function () {
  for (var pair of window.System.entries()) {
    var ex = pair[1];
    if (!ex || typeof ex !== 'object') continue;
    var gen = ex.HQQD;
    if (gen && gen.VKXX && gen.HDPD) return gen.HDPD;
  }
  return null;
})()
"""


def _js_chest_targets(mapping: Dict[str, Any]) -> str:
    """当前地图里能开宝箱的 BOSS（名字带 BOSS 的怪）。"""
    return r"""
(function () {
  try {
    var m = %s;
    var b = (m.builds || {})[m.current] || {};
    var mc = b.map || {};
    var u = mc.unit || {};
    var world = window[mc.holder || 'VKXX'];
    var core = world[mc.core || 'ZRRU'];
    var units = core[mc.manager || 'PFTN']()[mc.container || 'UFYI']();
    var words = (mc.bossKeywords || ['boss', '首领', '精英', '头目']).map(function (w) { return String(w).toLowerCase(); });
    var rows = [];
    units[mc.each || 'OOIV'](function (index, unit) {
      if (!unit) return false;
      var tag = String(unit[u.tag || 'CLRB'] || '');
      if (tag !== 'Monster') return false;
      var name = String((unit[u.point || 'YLYI'] || {}).name || '');
      var text = name.toLowerCase();
      var hit = words.some(function (w) { return text.indexOf(w) >= 0; });
      if (!hit) return false;
      rows.push({cfg: String(unit[u.configId || 'EFDX']), uid: String(unit.PLVT), name: name});
      return false;
    });
    return JSON.stringify(rows);
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % _js(mapping)


def _js_chest_claim(cfg: str, uid: str, mult: int) -> str:
    return r"""
(function () {
  try {
    var chest = %s;
    if (!chest) return 'ERR 没找到宝箱服务';
    window.__zzClaim = {s: 'pending'};
    var cfg = %s, uid = %s, mult = %d;
    Promise.resolve()
      .then(function () { return chest.DELV(cfg); })
      .then(function () { return chest.KMYW(cfg, mult, true, uid, 0); })
      .then(function () { return chest.IVTK(cfg, mult, true); })
      .then(function (r) {
        var text = JSON.stringify(r) || '';
        var bad = text.indexOf('Err') >= 0 || text.indexOf('claimed') >= 0;
        window.__zzClaim = {s: bad ? 'err' : 'ok', raw: text.slice(0, 200)};
      }, function (e) {
        window.__zzClaim = {s: 'err', raw: String((e && e.message) || e).slice(0, 200)};
      });
    return 'called';
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % (_js_chest_service(), _js(str(cfg)), _js(str(uid)), int(mult))


def _js_teleport_panel(name_prefix: str) -> str:
    """面板点击式传送：点左边地区页签（按名字）。"""
    return r"""
(function () {
  try {
    var cc = window.cc;
    var scene = cc.director.getScene();
    var root = null;
    (function walk(n, d) {
      if (!n || root || d > 30) return;
      if (String(n.name || '') === 'TeleportSelectView') { root = n; return; }
      (n.children || []).forEach(function (k) { walk(k, d + 1); });
    })(scene, 0);
    if (!root) return 'ERR 请先打开游戏里的传送面板';
    var want = %s;
    var labels = function (node) {
      var out = [];
      (function l(x) {
        (x._components || []).forEach(function (c) {
          var cls = ''; try { cls = cc.js.getClassName(c) || ''; } catch (e) { cls = ''; }
          if (cls === 'cc.Label' && x.activeInHierarchy) out.push(String(c.string || ''));
        });
        (x.children || []).forEach(l);
      })(node);
      return out.join('|');
    };
    var hit = null;
    (function walk2(n) {
      if (hit) return;
      var nm = String(n.name || '');
      if (nm.indexOf('TeleportTypePageItemView') === 0 && labels(n).indexOf(want) >= 0) hit = n;
      (n.children || []).forEach(walk2);
    })(root);
    if (!hit) return 'ERR 面板里没找到「' + want + '」';
    var btn = null; try { btn = hit.getComponent(cc.Button); } catch (e) { btn = null; }
    if (btn && btn.clickEvents && btn.clickEvents.length) {
      var ev = btn.clickEvents[0];
      var target = ev.target || hit;
      var comp = null;
      try { comp = ev.component ? target.getComponent(ev.component) : null; } catch (e) { comp = null; }
      if (!comp) comp = target;
      var fn = comp && ev.handler ? comp[ev.handler] : null;
      if (typeof fn === 'function') { fn.call(comp, ev.customEventData || null); return 'ok'; }
    }
    return 'ERR 地区页签点不动';
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % _js(name_prefix)


def _js_teleport_panel_item(name_part: str) -> str:
    """点右边地图条目（按名字）。"""
    return r"""
(function () {
  try {
    var cc = window.cc;
    var scene = cc.director.getScene();
    var root = null;
    (function walk(n, d) {
      if (!n || root || d > 30) return;
      if (String(n.name || '') === 'TeleportSelectView') { root = n; return; }
      (n.children || []).forEach(function (k) { walk(k, d + 1); });
    })(scene, 0);
    if (!root) return 'ERR 传送面板不见了';
    var want = %s;
    var labels = function (node) {
      var out = [];
      (function l(x) {
        (x._components || []).forEach(function (c) {
          var cls = ''; try { cls = cc.js.getClassName(c) || ''; } catch (e) { cls = ''; }
          if (cls === 'cc.Label' && x.activeInHierarchy) out.push(String(c.string || ''));
        });
        (x.children || []).forEach(l);
      })(node);
      return out.join('|');
    };
    var hit = null;
    (function walk2(n) {
      if (hit) return;
      if (String(n.name || '') === 'TeleportSelectItemView' && labels(n).indexOf(want) >= 0) hit = n;
      (n.children || []).forEach(walk2);
    })(root);
    if (!hit) return 'ERR 没找到地图「' + want + '」';
    var btn = null; try { btn = hit.getComponent(cc.Button); } catch (e) { btn = null; }
    if (btn && btn.clickEvents && btn.clickEvents.length) {
      var ev = btn.clickEvents[0];
      var target = ev.target || hit;
      var comp = null;
      try { comp = ev.component ? target.getComponent(ev.component) : null; } catch (e) { comp = null; }
      if (!comp) comp = target;
      var fn = comp && ev.handler ? comp[ev.handler] : null;
      if (typeof fn === 'function') { fn.call(comp, ev.customEventData || null); return 'ok'; }
    }
    return 'ERR 条目点不动';
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % _js(name_part)


def _js_teleport_unlocked(pairs: List[List[int]]) -> str:
    """查每个传送点是否已解锁（csd_fuwu.csd_moxingBiao 里的 csd_yijiesuo）。"""
    return r"""
(function () {
  try {
    var gen = null;
    for (var pair of window.System.entries()) {
      var ex = pair[1];
      if (ex && typeof ex === 'object' && ex.HQQD && ex.HQQD.VKXX) { gen = ex.HQQD; break; }
    }
    if (!gen || !gen.OXIA) return '{}';
    var table = gen.OXIA.OTWF;
    if (!table || typeof table.get !== 'function') return '{}';
    var out = {};
    var list = %s;
    for (var i = 0; i < list.length; i += 1) {
      var mapId = list[i][0], pointId = list[i][1];
      var key = mapId + '.' + pointId;
      try {
        var points = table.get(mapId);
        var point = points && points.get ? points.get(pointId) : null;
        out[key] = point ? !!point.KFUG : null;
      } catch (e) { out[key] = null; }
    }
    return JSON.stringify(out);
  } catch (e) { return '{}'; }
})()
""" % _js(pairs)


def _js_collect_state(entity: Any) -> str:
    """采到没有：按目标实体自己的状态判断（采掉后 AINJ 变真，或者实体直接消失）。

    不要用「事件计数」判断：映射里的 OHLC 是世界同步回调，每秒会触发好几次，
    之前日志里的「采到 24 次」其实是同步次数，跟采集无关（实测踩坑）。
    """
    return r"""
(function () {
  try {
    var NB = %s;
    var c = window.VKXX.ZRRU;
    var mgr = c.PFTN();
    var units = mgr.UFYI();
    var hit = null;
    units.OOIV(function (i, u) {
      if (!u) return false;
      if (u.NBDV === NB) { hit = u; return true; }
      return false;
    });
    if (!hit) return 'gone';
    var dead = false, collectible = true;
    try { dead = !!hit.AINJ(); } catch (e) {}
    try { collectible = typeof hit.QATG === 'function' ? !!hit.QATG() : true; } catch (e) {}
    if (!collectible) return 'gone';
    return dead ? 'dead' : 'alive';
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % _js(entity)


def _js_collect_target(keyword: str, skip: Optional[List[tuple]] = None, radius: float = DEFAULT_COLLECT_RADIUS) -> str:
    """在角色附近找资源点（按名字关键字），返回坐标（不瞬移）。

    - 只找半径 radius 以内的，远的不去（避免为了采一棵树满地图飞）。
    - 关键字是「活动」时排除日常资源（树/木/矿/灌木/草/肉/藤），只认活动专属采集物。
    - 已经采过的（AINJ 为真）直接跳过。
    """
    words = list(COLLECT_KEYWORDS.get(keyword, (keyword,)))
    is_activity = "*" in words
    return r"""
(function () {
  try {
    var words = %s;
    var skip = %s;
    var radius = %s;
    var daily = %s;
    var m = window.__zzAssistMapping || { builds: {} };
    var b = (m.builds || {})[m.current] || {};
    var mc = b.map || {};
    var u = mc.unit || {};
    var world = window[mc.holder || 'VKXX'];
    var core = world[mc.core || 'ZRRU'];
    var units = null;
    var ids = [];
    try {
      var mgr = core[mc.manager || 'PFTN']();
      units = mgr[mc.container || 'UFYI']();
      var team = core[mc.team || 'PMMA'](world[mc.player || 'KBVD']);
      ids = mgr[mc.members || 'YUWW'](team) || [];
    } catch (e) { /* ignore */ }
    var leader = ids.length ? core[mc.read || 'YVSZ'](ids[0]) : null;
    var lpt = leader ? (leader[u.point || 'GUJG'] || {}) : {};
    var lx = lpt.x !== undefined ? lpt.x : (leader ? (leader.ADUX || 0) : 0);
    var ly = lpt.y !== undefined ? lpt.y : (leader ? (leader.WUZI || 0) : 0);
    var best = null;
    var visit = function (index, unit) {
      if (!unit) return false;
      var point = unit[u.point || 'YLYI'] || {};
      var ux = point.x !== undefined ? point.x : unit.ADUX;
      var uy = point.y !== undefined ? point.y : unit.WUZI;
      if (!isFinite(ux) || !isFinite(uy)) return false;
      var name = String(point.name || '');
      var tag = String(unit[u.tag || 'CLRB'] || '');
      var text = (name + ' ' + tag).toLowerCase();
      var anyKind = words.indexOf('*') >= 0;
      var collectible = false;
      try {
        if (typeof unit.QATG === 'function') collectible = !!unit.QATG();
        else collectible = !!(point.id || point.pointID || point.name);
      } catch (e) {}
      if (!collectible) return false;
      // 已经采完/被采掉的不要（旧名 AINJ，新名 KHGB / HCQJ / UMMW）
      try {
        if (typeof unit.AINJ === 'function') { if (unit.AINJ()) return false; }
        else if (typeof unit.KHGB === 'function') { if (unit.KHGB()) return false; }
        else if (unit.HCQJ === true || unit.UMMW === true) return false;
      } catch (e) {}
      if (anyKind) {
        // 活动：无名字的可采实体就是活动采集物（实测 efdx=5115601~5115603，采到会进背包）；
        // 有名字的只排除日常资源（树/木/矿/灌木/草/肉/藤）。
        if (name) {
          for (var j = 0; j < daily.length; j += 1) {
            if (text.indexOf(String(daily[j])) >= 0) return false;
          }
        }
      } else {
        if (!name) return false;
        var hit = words.some(function (w) { return text.indexOf(String(w).toLowerCase()) >= 0; });
        if (!hit) return false;
      }
      var d = Math.hypot(ux - lx, uy - ly);
      // 超出角色附近范围的不采
      if (d > radius) return false;
      for (var i = 0; i < skip.length; i += 1) {
        if (Math.hypot(ux - skip[i][0], uy - skip[i][1]) < 80) return false;
      }
      if (!best || d < best.d) best = { x: ux, y: uy, d: d, nb: unit.NBDV, name: name || '活动采集物' };
      return false;
    };
    if (units && typeof units[mc.each || 'OOIV'] === 'function') units[mc.each || 'OOIV'](visit);
    if (!best) return 'none';
    return JSON.stringify({name: best.name, x: Math.round(best.x), y: Math.round(best.y), nb: best.nb});
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % (_js(words), _js(skip or []), _js(float(radius)), _js(list(DAILY_RESOURCE_WORDS)))


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
        self._mapping: Dict[str, Any] = {}
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
        self._mapping = mapping
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
            self._chest(args)
            return
        if name in ("跳转", "jump"):
            self._jump(args)
            return
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

    def _viewport(self) -> Dict[str, float]:
        try:
            raw = self._eval("JSON.stringify({w: innerWidth, h: innerHeight})")
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            w = float(data.get("w") or 0)
            h = float(data.get("h") or 0)
            if w > 0 and h > 0:
                return {"w": w, "h": h}
        except Exception:  # noqa: BLE001
            pass
        return {"w": 372.0, "h": 798.0}

    def _point(self, x: float, y: float, view: Optional[Dict[str, float]] = None) -> tuple:
        """指令里的坐标是「百分比」（参考工具显示坐标也是百分比），超过 100 当像素。"""
        view = view or self._viewport()
        if -1.0 <= x <= 100.0 and -1.0 <= y <= 100.0:
            return x / 100.0 * view["w"], y / 100.0 * view["h"]
        return x, y

    def _click(self, x: float, y: float) -> None:
        """真鼠标点击：走 CDP Input（游戏只认可信事件，DOM 派发点不动按钮）。"""
        cx, cy = self._point(x, y)
        self._cdp_click(cx, cy)
        self._sleep(0.15)

    def _cdp(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        bridge = self._bridge_provider()
        if bridge is None:
            raise RuntimeError("还没连上游戏窗口")
        call = getattr(bridge, "_call", None)
        if call is None:
            raise RuntimeError("当前连接不支持 CDP 输入")
        return call(method, params)

    def _cdp_click(self, x: float, y: float) -> None:
        self._cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none", "buttons": 0})
        self._cdp(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1},
        )
        self._sleep(0.05)
        self._cdp(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": x, "y": y, "button": "left", "buttons": 0, "clickCount": 1},
        )

    def _cdp_wheel(self, amount: float, x: float, y: float) -> None:
        self._cdp(
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": amount, "button": "none", "buttons": 0},
        )

    def _wheel(self, args: List[str]) -> None:
        if not args:
            raise RuntimeError("滚轮 缺少参数")
        direction = str(args[0]).strip()
        amount = self._number(args, 1, "滚动量") if len(args) > 1 else 3
        x = self._number(args, 2, "X") if len(args) > 2 else None
        y = self._number(args, 3, "Y") if len(args) > 3 else None
        sign = -1 if direction in ("上", "up") else 1
        view = self._viewport()
        if x is None or y is None:
            x = view["w"] / 2.0
            y = view["h"] / 2.0
        else:
            x, y = self._point(float(x), float(y), view)
        self._cdp_wheel(sign * amount * 120, float(x), float(y))
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
        candidates = [
            (k, v) for k, v in table.items()
            if k.startswith("%d.%d." % (chapter, section))
        ]
        candidates.sort(key=lambda kv: kv[0])
        if not candidates:
            raise RuntimeError("地图 %d-%d 还没收录传送点" % (chapter, section))
        if len(args) > 2 and str(args[2]).strip() != "":
            key = "%d.%d.%d" % (chapter, section, continent)
            candidates = [kv for kv in candidates if kv[0] == key] or candidates
            candidates = candidates[:1]
        else:
            # 没写大陆：优先挑「已解锁」的那个（和参考工具一致，锁住的点不能传）
            try:
                pairs = [
                    [int(v.get("map_id")), int(v.get("teleport_id") or 0)]
                    for _k, v in candidates
                ]
                unlocked = json.loads(str(self._eval(_js_teleport_unlocked(pairs)) or "{}"))
            except Exception:  # noqa: BLE001
                unlocked = {}
            if isinstance(unlocked, dict) and unlocked:
                usable = [
                    kv for kv in candidates
                    if unlocked.get("%s.%s" % (int(kv[1].get("map_id")), int(kv[1].get("teleport_id") or 0))) is True
                ]
                if usable:
                    candidates = usable
        key, spot = candidates[0]
        map_id = int(spot.get("map_id"))
        teleport_id = int(spot.get("teleport_id") or 0)
        self._log_line("    传送 → %s（地图 %d，传送点 %d）" % (spot.get("name") or "", map_id, teleport_id))
        state_js = (
            "(function(){try{var m=%s;var b=(m.builds||{})[m.current]||{};var mc=b.map||{};"
            "var w=window[mc.holder||'VKXX'];var st=w[mc.state||'AGDA']||{};"
            "return JSON.stringify({map:String(st[mc.mapId||'POMD']),loading:!!st[mc.loading||'MAOP'],"
            "done:!!(window.__zzTp&&window.__zzTp.done)})}catch(e){return '{}'}})()" % _js(mapping)
        )

        def _state() -> Dict[str, Any]:
            try:
                return json.loads(str(self._eval(state_js) or "{}"))
            except ValueError:
                return {}

        before = _state()
        before_map = str(before.get("map") or "")

        def _send(mid: int, tid: int) -> None:
            result = self._eval(_js_enter_map(mapping, mid, tid))
            if str(result).startswith("ERR"):
                raise RuntimeError("传送失败：%s" % str(result)[4:])

        # 表里同一章/地图可能有多个大陆的传送点；未解锁的点游戏会静默拒绝，
        # 所以按顺序一个个试，哪个能切图就用哪个（不再依赖解锁字段，字段会随版本变）。
        spots = [
            (int(v.get("map_id")), int(v.get("teleport_id") or 0), str(v.get("name") or ""))
            for _k, v in candidates
        ]
        # 面板点击式传送（传送面板开着时优先用这个，不依赖会变的 map_id/point_id）
        try:
            chapter_no = int(float(str(args[0]).strip()))
            section_no = int(float(str(args[1]).strip()))
        except (TypeError, ValueError):
            chapter_no, section_no = 0, 0
        spot_name = str(candidates[0][1].get("name") or "")
        area_name, _, map_name = spot_name.partition("-")
        if area_name and map_name:
            ret = str(self._eval(_js_teleport_panel(area_name)) or "")
            if ret == "ok" or ret.startswith("ok"):
                self._log_line("    已点地区「%s」，等地图列表…" % area_name)
                self._sleep(0.8)
                ret2 = str(self._eval(_js_teleport_panel_item(map_name)) or "")
                if ret2.startswith("ok"):
                    self._log_line("    已点地图，等加载…")
                    deadline = time.monotonic() + 25.0
                    while time.monotonic() < deadline:
                        if self._stop.is_set():
                            raise _Stopped()
                        time.sleep(0.5)
                        st = _state()
                        if not st.get("loading") and str(st.get("map")) != before_map:
                            self._log_line("    传送完成（地图 %s）" % st.get("map"))
                            self._sleep(0.8)
                            return
                    self._log_line("    面板点了，但地图没变，改走接口传送")
                else:
                    self._log_line("    %s" % ret2)
            else:
                self._log_line("    %s，改走接口传送" % ret)

        # 已经在这张地图里就不用传送（有些地图的传送点没解锁，但人已经在图上）
        if before_map and any(str(mid) == before_map for mid, _tid, _n in spots):
            self._log_line("    已经在目标地图 %s，跳过传送" % before_map)
            return

        started = False
        for spot_i, (mid, tid, spot_name) in enumerate(spots):
            if self._stop.is_set():
                raise _Stopped()
            self._log_line("    传送 → %s（地图 %d，传送点 %d）" % (spot_name, mid, tid))
            for attempt in range(2):
                _send(mid, tid)
                start_deadline = time.monotonic() + 4.0
                while time.monotonic() < start_deadline:
                    if self._stop.is_set():
                        raise _Stopped()
                    time.sleep(0.3)
                    state = _state()
                    if state.get("loading") or state.get("done") or str(state.get("map")) != before_map:
                        started = True
                        break
                if started:
                    map_id, teleport_id = mid, tid
                    break
                if attempt == 0:
                    self._log_line("    这个点没反应，再发一次")
                    time.sleep(1.5)
            if started:
                break
            self._log_line("    这个传送点不可用（可能未解锁），换下一个")
            time.sleep(1.0)
        if not started:
            raise RuntimeError("传送失败：这一章所有传送点都没反应（可能都没解锁）")

        deadline = time.monotonic() + 35.0
        landed = False
        while time.monotonic() < deadline:
            if self._stop.is_set():
                raise _Stopped()
            time.sleep(0.5)
            state = _state()
            if not state.get("loading") and (str(state.get("map")) == str(map_id) or state.get("done")):
                landed = True
                break
        if not landed:
            raise RuntimeError("传送失败：地图加载超时（地图 %d）" % map_id)
        time.sleep(0.8)

    def _jump(self, args: List[str]) -> None:
        """跳转(指令名或编号)：接着跑另一条指令（我的优先，最多套 5 层）。"""
        if not args:
            raise RuntimeError("跳转 需要写目标指令名或编号")
        key = str(args[0]).strip()
        library = load_library()
        mine = library_instructions(library, -1)
        pool = mine or library_instructions(library, 0)
        target: Optional[Dict[str, Any]] = None
        if key.isdigit():
            index = int(key) - 1
            if 0 <= index < len(pool):
                target = pool[index]
        if target is None:
            for item in library_instructions(library, 0):
                if str(item.get("title") or "").strip() == key:
                    target = item
                    break
        if target is None:
            raise RuntimeError("跳转目标不存在：%s" % key)
        depth = getattr(self, "_jump_depth", 0)
        if depth >= 5:
            raise RuntimeError("跳转层数太深（可能有死循环）")
        steps = parse_script(str(target.get("content") or ""))
        title = str(target.get("title") or key)
        self._log_line("    ↳ 跳转到「%s」（%d 步）" % (title, len(steps)))
        self._jump_depth = depth + 1
        try:
            for step in steps:
                if self._stop.is_set():
                    raise _Stopped()
                self._exec(str(step["name"]), step["args"], self._mapping)
        finally:
            self._jump_depth = depth
        self._log_line("    ↳ 「%s」跑完，继续后面的步骤" % title)

    def _chest(self, args: List[str]) -> None:
        """开宝箱(个数,奖励模式)：个数不填=尽量全开；奖励模式=普通/双倍。"""
        limit = 0
        if args and str(args[0]).strip() != "":
            try:
                limit = max(0, int(float(str(args[0]).strip())))
            except ValueError as exc:
                raise RuntimeError("开宝箱 的个数必须是数字") from exc
        mode = ""
        if len(args) > 1:
            mode = str(args[1]).strip()
        mult = 2 if ("双倍" in mode or mode.lower() in ("double", "2")) else 1
        mapping = getattr(self, "_mapping", None) or {}
        raw = self._eval(_js_chest_targets(mapping))
        text = str(raw)
        if text.startswith("ERR"):
            raise RuntimeError("开宝箱失败：%s" % text[4:])
        try:
            targets = json.loads(text) if text.startswith("[") else []
        except ValueError:
            targets = []
        if not targets:
            self._log_line("    当前地图没有可开箱的 BOSS")
            return
        self._log_line("    发现 %d 个 BOSS，奖励模式：%s" % (len(targets), "双倍" if mult == 2 else "普通"))
        done = 0
        for target in targets:
            if self._stop.is_set():
                raise _Stopped()
            if limit and done >= limit:
                break
            cfg = str(target.get("cfg") or "")
            uid = str(target.get("uid") or "")
            if not cfg:
                continue
            self._eval(_js_chest_claim(cfg, uid, mult))
            state = {}
            for _ in range(12):
                if self._stop.is_set():
                    raise _Stopped()
                time.sleep(0.5)
                try:
                    state = json.loads(str(self._eval("JSON.stringify(window.__zzClaim || {})") or "{}"))
                except ValueError:
                    state = {}
                if state.get("s") in ("ok", "err"):
                    break
            if state.get("s") == "ok":
                done += 1
                self._log_line("    开箱成功：%s" % (target.get("name") or cfg))
                self._sleep(0.8)
            else:
                reason = str(state.get("raw") or "")
                if "claimed" in reason:
                    reason = "已经领过"
                elif "Err" in reason:
                    reason = reason[reason.find("Err"):][:40]
                else:
                    reason = reason[:40] or "未知原因"
                self._log_line("    跳过 %s（%s）" % (target.get("name") or cfg, reason))
        self._log_line("    共开箱 %d 个" % done)

    def _collect(self, args: List[str]) -> None:
        keyword = str(args[0]).strip() if args else "活动"
        try:
            cfg = load_automation_config()
            radius = float(cfg.get("collect_radius") or DEFAULT_COLLECT_RADIUS)
        except (TypeError, ValueError):
            radius = DEFAULT_COLLECT_RADIUS
        tried: List[tuple] = []
        got = 0
        for _ in range(10):
            if self._stop.is_set():
                raise _Stopped()
            result = self._eval(_js_collect_target(keyword, tried, radius))
            text = str(result)
            if text.startswith("ERR"):
                raise RuntimeError("采集失败：%s" % text[4:])
            if text == "none" or not text.startswith("{"):
                break
            try:
                target = json.loads(text)
            except ValueError:
                break
            tx = float(target.get("x") or 0)
            ty = float(target.get("y") or 0)
            nb = target.get("nb")
            tried.append((tx, ty))
            self._log_line("    去采 %s（%d,%d）" % (target.get("name"), int(tx), int(ty)))
            # 站到资源旁边（不踩正中间，游戏是靠近就开始采）
            if not self._teleport_to(tx + 28, ty):
                self._log_line("    瞬移没过去，换下一个")
                continue
            self._sleep(3.2)
            state = str(self._eval(_js_collect_state(nb)) or "")
            if state == "alive":
                self._nudge_towards(target)
                self._sleep(2.2)
                state = str(self._eval(_js_collect_state(nb)) or "")
            if state == "alive" and self._teleport_to(tx, ty):
                # 还是没采到：干脆站到资源点上再等
                self._sleep(2.6)
                state = str(self._eval(_js_collect_state(nb)) or "")
            if state in ("dead", "gone"):
                got += 1
                self._log_line("    采到了")
            else:
                self._log_line("    这个点没采到，换下一个")
        if got:
            self._log_line("    共采到 %d 个点" % got)
        else:
            self._log_line("    角色附近 %d 米内没有可采的「%s」，原地不动" % (int(radius), keyword))

    def _pos(self) -> Optional[tuple]:
        try:
            raw = self._eval(
                "(function(){try{var c=window.VKXX.ZRRU;var ids=c.PFTN().YUWW(c.PMMA(window.VKXX.KBVD))||[];"
                "var u=ids.length?c.YVSZ(ids[0]):null;return u?JSON.stringify([u.ADUX,u.WUZI]):'null'}"
                "catch(e){return 'null'}})()"
            )
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, list) and len(data) == 2:
                return float(data[0]), float(data[1])
        except Exception:  # noqa: BLE001
            pass
        return None

    def _teleport_to(self, x: float, y: float, retries: int = 3) -> bool:
        """游戏原生瞬移（无动画）。刚进图/过场时会被丢，所以带校验重试。"""
        for _ in range(max(1, retries)):
            if self._stop.is_set():
                raise _Stopped()
            try:
                self._eval(
                    "(function(){try{window.VKXX.ZAVH(%s,%s,false,function(){});return 'ok'}"
                    "catch(e){return 'ERR '+(e&&e.message||e)}})()" % (_js(float(x)), _js(float(y)))
                )
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1.0)
            pos = self._pos()
            if pos and abs(pos[0] - x) < 260 and abs(pos[1] - y) < 260:
                return True
        return False

    def _nudge_towards(self, target: Dict[str, Any]) -> None:
        """朝资源方向轻推一下摇杆（方向取最近的 45°）。"""
        try:
            tx = float(target.get("x") or 0)
            ty = float(target.get("y") or 0)
        except (TypeError, ValueError):
            return
        pos = self._pos()
        if not pos:
            return
        mx, my = pos
        dx, dy = tx - mx, ty - my
        if abs(dx) < 1 and abs(dy) < 1:
            return
        # 屏幕方向 = (dx, -dy)，摇杆 dy 是屏幕向下
        import math

        angle = math.degrees(math.atan2(-dy, dx))
        buckets = [
            ("右", 0), ("右上", 45), ("上", 90), ("左上", 135),
            ("左", 180), ("左下", -135), ("下", -90), ("右下", -45),
        ]
        name = min(buckets, key=lambda b: abs(((angle - b[1] + 180) % 360) - 180))[0]
        try:
            self._move([name, "0.6"])
        except Exception:  # noqa: BLE001
            pass


class _Stopped(Exception):
    """内部用：用户点了停止。"""
