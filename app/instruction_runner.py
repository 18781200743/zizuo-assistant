"""运行命令：把「指令脚本」逐行发给游戏页执行。

指令语言和 game_storage_sync 的「自定义指令」一致，一行一条，例如：
    传送(3,2) / 移动(上,3) / 移动(15.0,1.5) / 采集(活动) / 回城()

支持：
    移动(方向或角度,秒) / 等待(秒) / 点击(X%,Y%) / 滚轮(方向,量[,X%,Y%])
    传送(章节,地图[,大陆]) / 回城() / 采集(物品) / 开宝箱([个数][,普通|双倍]) / 停止()
只做执行核心（单脚本）：跳转(槽位) 需要多槽位，暂不支持。
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .instruction_library import ScriptError, parse_script
from .commands_store import SLOT_COUNT
from .chest_4758a import run_chest_4758a

# 和 game_storage_sync 一致：方向名对应的角度（0=上，顺时针）
DIRECTION_ANGLES: Dict[str, float] = {
    "上": 0.0,
    "右上": 45.0,
    "右": 90.0,
    "右下": 135.0,
    "下": 180.0,
    "左下": 225.0,
    "左": 270.0,
    "左上": 315.0,
}

# 采集按“掉落物品名”匹配（和参考工具一致）。
# 注意：这里写的是背包物品名，不是地图上的资源点名。
COLLECT_KEYWORDS = {
    "木材": ("木材", "木头", "木"),
    "矿石": ("蓝矿石", "红矿石", "矿石", "矿"),
    "蓝矿石": ("蓝矿石", "蓝矿"),
    "红矿石": ("红矿石", "红矿"),
    "彩矿": ("彩矿", "矿石"),
    "宝石": ("宝石",),
    "幸运草": ("幸运草",),
    "训练石": ("训练石",),
    "肉块": ("肉块", "肉"),
    "活动": ("*",),
}

# 采集(活动) 只看“不是日常资源”的掉落，和参考工具的判断一致。
DAILY_DROP_WORDS = ("宝石", "训练石", "幸运草", "彩矿", "矿石", "木材", "肉块", "金币", "钻石")

# 参考工具：英雄停止采集/打怪 5 秒后收口；这里最多等 15 秒，避免脚本卡住。
COLLECT_IDLE_MS = 5000
COLLECT_MAX_WAIT = 15.0
# 允许把“上一条移动步骤里已经开始、刚结算完”的采集算进来。
COLLECT_LOOKBACK_MS = 15000


def _config_path() -> Path:
    return Path(__file__).resolve().parent / "automation_config.json"


def load_automation_config() -> Dict[str, Any]:
    try:
        return json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def with_current_build(mapping: Dict[str, Any], build: Optional[str] = None) -> Dict[str, Any]:
    """确定当前使用哪个 build 的映射；没有对应 build 就明确报“未适配”。"""
    data = dict(mapping or {})
    builds = data.get("builds") or {}
    if build:
        if build not in builds:
            raise ScriptError(
                "游戏版本 %s 未适配运行命令（映射里没有这个 build），请先更新字段映射" % build
            )
        data["current"] = build
    elif "current" not in data:
        if not builds:
            raise ScriptError("没有可用的游戏版本映射")
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
    """回城：和 exe 一样调用游戏原生地图传送回主城（默认 1,1）。"""
    return r"""
(function () {
  try {
    var m = %s;
    var b = (m.builds || {})[m.current] || {};
    var mc = b.map || {};
    var world = window[mc.holder || 'VKXX'];
    if (!world) return 'ERR 游戏还没进地图';
    var api = mc.teleport || 'LUSS';
    if (typeof world[api] !== 'function') return 'ERR 游戏没有传送入口（' + api + '）';
    var rn = b.runtimeNames || {};
    var leave = rn.MEMK || 'MEMK';
    try { if (typeof world[leave] === 'function') world[leave](); } catch (e) { /* 没有就算了 */ }
    window.__zzHome = { done: false, at: Date.now(), map: 1 };
    world[api](1, 1, function () { window.__zzHome.done = true; });
    return 'ok 1,1';
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % _js(mapping)


def _js_home_camp(mapping: Dict[str, Any]) -> str:
    """旧版回城：传送到本图营地坐标（读取 TCLA/CLAT 或新版本名）。"""
    return r"""
(function () {
  try {
    var m = %s;
    var b = (m.builds || {})[m.current] || {};
    var rev = b.revive || {};
    var ct = rev.campTeleport || {};
    var names = ct.campCoords || ['TCLA', 'CLAT'];
    var world = window[(b.map || {}).holder || 'VKXX'];
    var core = world && world[(b.map || {}).core || 'ZRRU'];
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
    var rn = b.runtimeNames || {};
    var leave = rn.MEMK || 'MEMK';
    try { if (typeof world[leave] === 'function') world[leave](); } catch (e) { /* 没有就算了 */ }
    var api = ct.api || rn.ZAVH || 'ZAVH';
    if (typeof world[api] !== 'function') return 'ERR 游戏没有传送入口（' + api + '）';
    window.__zzHome = { done: false, at: Date.now() };
    world[api](spot.x, spot.y, true, function () { window.__zzHome.done = true; });
    return 'ok ' + Math.round(spot.x) + ',' + Math.round(spot.y);
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % _js(mapping)


def _js_home_state(mapping: Dict[str, Any]) -> str:
    return r"""
(function () {
  try {
    var m = %s;
    var b = (m.builds || {})[m.current] || {};
    var mc = b.map || {};
    var world = window[mc.holder || 'VKXX'];
    var st = world && world[mc.state || 'AGDA'];
    return JSON.stringify({
      done: !!(window.__zzHome && window.__zzHome.done),
      loading: !!(st && st[mc.loading || 'MAOP']),
      map: String(st ? st[mc.mapId || 'POMD'] : '')
    });
  } catch (e) { return '{}'; }
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


def _js_chest_service(mapping: Dict[str, Any]) -> str:
    """宝箱服务：从映射里取服务容器/世界/服务名，不再写死。"""
    b = (((mapping or {}).get("builds") or {}).get((mapping or {}).get("current") or "") or {})
    chest = b.get("chest") or {}
    holder = str(chest.get("holder") or "HQQD")
    world_name = str(chest.get("world") or (b.get("map") or {}).get("holder") or "VKXX")
    service = str(chest.get("service") or "HDPD")
    return r"""
(function () {
  for (var pair of window.System.entries()) {
    var ex = pair[1];
    if (!ex || typeof ex !== 'object') continue;
    var gen = ex[%s];
    if (gen && gen[%s] && gen[%s]) return gen[%s];
  }
  return null;
})()
""" % (_js(holder), _js(world_name), _js(service), _js(service))


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
      rows.push({cfg: String(unit[u.configId || 'EFDX']), uid: String(unit[u.entity || 'PLVT']), name: name});
      return false;
    });
    return JSON.stringify(rows);
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % _js(mapping)


def _js_chest_claim(mapping: Dict[str, Any], cfg: str, uid: str, mult: int) -> str:
    b = (((mapping or {}).get("builds") or {}).get((mapping or {}).get("current") or "") or {})
    chest = b.get("chest") or {}
    pre = str(chest.get("preCall") or "DELV")
    claim = str(chest.get("claim") or "KMYW")
    finish = str(chest.get("finish") or "IVTK")
    return r"""
(function () {
  try {
    var chest = %s;
    if (!chest) return 'ERR 没找到宝箱服务';
    window.__zzClaim = {s: 'pending'};
    var cfg = %s, uid = %s, mult = %d;
    Promise.resolve()
      .then(function () { return chest[%s](cfg); })
      .then(function () { return chest[%s](cfg, mult, true, uid, 0); })
      .then(function () { return chest[%s](cfg, mult, true); })
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
""" % (
        _js_chest_service(mapping), _js(str(cfg)), _js(str(uid)), int(mult),
        _js(pre), _js(claim), _js(finish),
    )



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


def _js_collect_watch(mapping: Dict[str, Any]) -> str:
    """在采集事件分发器上挂一个只读监听，记录游戏自己采到的掉落。

    和 game_storage_sync 一样：采集指令不寻路、不瞬移，只等游戏自己采集；
    这里额外把事件缓存下来，方便“移动 → 采集”这种脚本把移动途中采到的算进来。
    """
    return r"""
(function () {
  try {
    var m = %s;
    var b = (m.builds || {})[m.current] || {};
    var mc = b.map || {};
    var ct = b.collect || {};
    var world = window[mc.holder || 'VKXX'];
    var core = world && world[mc.core || 'ZRRU'];
    if (!core) return 'ERR 游戏还没进地图';
    var q = core[ct.queue || 'YHST'];
    var holder = q && typeof q === 'function' ? q.call(core) : null;
    var bus = holder && holder[ct.dispatch || 'KCQB'];
    var entry = ct.entry || 'PZQH';
    if (!bus || typeof bus[entry] !== 'function') return 'ERR 采集事件还没就绪';
    var WATCH_VERSION = 3;
    if (bus.__zzCollectHooked === true && bus.__zzCollectWatchVersion === WATCH_VERSION) return 'ok';

    var gen = ct.serviceGen || 'DYCU';
    var services = null;
    try {
      var pairs = [...System.entries()];
      for (var i = 0; i < pairs.length; i += 1) {
        var ex = pairs[i][1];
        var bag = ex && ex[gen];
        if (bag && bag[mc.holder || 'GCOA'] === world) { services = bag; break; }
      }
    } catch (e) { services = null; }
    var itemModule = services && services[ct.itemModule || 'AHWN'];
    var parse = itemModule && itemModule[ct.dropParse || 'TEHJ'];
    var attrKey = ct.attribute || 'TKWM';
    var dropKey = ct.drops || 'SZSN';
    var nameKey = ct.itemName || 'FKAZ';
    var countKey = ct.itemCount || 'SSYS';

    var log = window.__zzCollectLog = window.__zzCollectLog || [];
    if (typeof window.__zzCollectConsumed !== 'number') window.__zzCollectConsumed = 0;
    function text(v) { return v == null ? '' : String(v); }
    function readDrops(unit) {
      var out = [];
      try {
        var attr = unit && unit[attrKey];
        var base = attr && attr[dropKey];
        if (!base || typeof base.length !== 'number') return out;
        for (var i = 0; i < base.length; i += 1) {
          var raw = base[i], item = raw;
          try { if (parse) item = parse(raw); } catch (e) { item = raw; }
          var name = '';
          try { if (item && typeof item[nameKey] === 'function') name = text(item[nameKey]()); } catch (e) {}
          if (!name) continue;
          var count = 0;
          try { if (item && typeof item[countKey] === 'function') count = Number(item[countKey]()) || 0; } catch (e) {}
          out.push({ name: name, count: count });
        }
      } catch (e) {}
      return out;
    }
    function mergeDrops(oldDrops, newDrops) {
      var out = [], seen = {};
      (oldDrops || []).concat(newDrops || []).forEach(function (drop) {
        if (!drop || !drop.name) return;
        var key = drop.name;
        if (seen[key] !== undefined) {
          if ((Number(drop.count) || 0) > 0) out[seen[key]].count = Number(drop.count) || 0;
          return;
        }
        seen[key] = out.length;
        out.push({ name: String(drop.name), count: Number(drop.count) || 0 });
      });
      return out;
    }
    function record(kind, unit, hero, extraDrops) {
      if (!unit) return;
      var id = 0, type = 0, x = 0, y = 0;
      try { id = Number(unit[ct.entityId || 'CXKW']) || 0; } catch (e) {}
      try { var attr = unit[attrKey]; type = Number(attr && attr[ct.collectType || 'GKIJ']) || 0; } catch (e) {}
      try { x = Math.round(Number(unit[mc.unit && mc.unit.x ? mc.unit.x : 'MKCS']) || 0); } catch (e) {}
      try { y = Math.round(Number(unit[mc.unit && mc.unit.y ? mc.unit.y : 'FYYQ']) || 0); } catch (e) {}
      var drops = mergeDrops(readDrops(unit), extraDrops || []);
      var now = Date.now();
      for (var i = log.length - 1; i >= 0 && i >= log.length - 8; i -= 1) {
        var old = log[i];
        if (old && old.id === id && old.x === x && old.y === y && now - old.t < 8000) {
          old.t = now;
          old.kind = kind;
          old.type = type || old.type;
          old.hero = old.hero || !!hero;
          old.drops = mergeDrops(old.drops, drops);
          return;
        }
      }
      log.push({ t: now, kind: kind, id: id, type: type, x: x, y: y, hero: !!hero, drops: drops });
      if (log.length > 80) log.shift();
    }
    function readExtraDrops(unit, count) {
      var out = [];
      try {
        if (count == null || !isFinite(Number(count))) return out;
        var map = world[mc.scene || 'STDU'];
        var cache = map && map[ct.rewardCache || 'ICXC'];
        var fn = cache && cache[ct.extraDrops || 'GUNK'];
        if (typeof fn !== 'function') return out;
        var type = 0;
        try { type = unit[ct.unitType || 'QNMZ']; } catch (e) {}
        var extra = fn.call(cache, unit, type, Number(count));
        if (!extra || typeof extra.length !== 'number') return out;
        for (var i = 0; i < extra.length; i += 1) {
          var raw = extra[i];
          try { if (raw && raw[ct.extraItem || 'GMMR'] != null) raw = raw[ct.extraItem || 'GMMR']; } catch (e) {}
          var item = raw;
          try { if (parse) item = parse(raw); } catch (e) { item = raw; }
          var name = '';
          try { if (item && typeof item[nameKey] === 'function') name = text(item[nameKey]()); } catch (e) {}
          if (!name) continue;
          var n = 0;
          try { if (item && typeof item[countKey] === 'function') n = Number(item[countKey]()) || 0; } catch (e) {}
          out.push({ name: name, count: n });
        }
      } catch (e) {}
      return out;
    }
    function recordAction() {
      var now = Date.now();
      var last = log.length ? log[log.length - 1] : null;
      if (last && last.kind === 'battle' && now - last.t < 1000) {
        last.t = now;
        return;
      }
      log.push({ t: now, kind: 'battle', id: 0, type: 0, x: 0, y: 0, hero: true, drops: [] });
      if (log.length > 80) log.shift();
    }
    function wrapBus(name, handler) {
      var current = bus[name];
      var original = current && current.__zzCollectOriginal ? current.__zzCollectOriginal : current;
      if (typeof original !== 'function') return;
      var wrapper = function () {
        try { handler(arguments); } catch (e) {}
        return original.apply(this, arguments);
      };
      wrapper.__zzCollectOriginal = original;
      try {
        Object.defineProperty(bus, name, {
          configurable: true, writable: true, value: wrapper, enumerable: false
        });
      } catch (e) {
        try { bus[name] = wrapper; } catch (e2) {}
      }
    }

    var actors = window.__zzCollectActors = window.__zzCollectActors || {};
    wrapBus(entry, function (args) {
      var event = args[0] || {};
      var payload = event[ct.eventContent || 'AWDY'] || {};
      var unit = payload[ct.eventTarget || 'YALQ'];
      var collector = payload[ct.eventCollector || 'WTEB'];
      var isCollect = false;
      try {
        isCollect = unit && typeof unit[ct.isCollect || 'FFCO'] === 'function'
          ? !!unit[ct.isCollect || 'FFCO']() : false;
      } catch (e) {}
      if (!isCollect) return;
      var hero = false;
      try {
        hero = collector && typeof collector[ct.isHero || 'GYQR'] === 'function'
          ? !!collector[ct.isHero || 'GYQR']() : false;
      } catch (e) {}
      var id = '';
      try { id = String(unit[ct.entityId || 'CXKW'] || ''); } catch (e) {}
      if (hero && id) actors[id] = Date.now();
      record('start', unit, hero);
    });

    wrapBus(ct.done || 'IPDY', function (args) {
      var event = args[0] || {};
      var unit = null;
      try {
        var map = world[mc.scene || 'STDU'];
        var table = map && map[ct.unitTable || 'LTDS'];
        var row = table && table.call(map, event[ct.eventUnitId || 'MCTA']);
        unit = row && row[ct.unitEntity || 'BSSG'];
      } catch (e) {}
      if (!unit) return;
      var isCollect = false;
      try {
        isCollect = typeof unit[ct.isCollect || 'FFCO'] === 'function'
          ? !!unit[ct.isCollect || 'FFCO']() : false;
      } catch (e) {}
      if (!isCollect) return;
      var id = '';
      try { id = String(unit[ct.entityId || 'CXKW'] || ''); } catch (e) {}
      var hero = !!(id && actors[id] && Date.now() - actors[id] < 30000);
      if (id) delete actors[id];
      record('done', unit, hero, readExtraDrops(unit, event[ct.completionCount || 'OXCG']));
    });
    wrapBus(ct.battle || 'PYRD', function (args) {
      var event = args[0] || {};
      var payload = event[ct.eventContent || 'AWDY'] || {};
      var actor = payload[ct.battleActor || 'BSSG'];
      var owner = payload[ct.battleOwner || 'YPTT'];
      var target = payload[ct.battleTarget || 'EPUO'];
      function hero(unit) {
        try {
          return unit && typeof unit[ct.isHero || 'GYQR'] === 'function' && !!unit[ct.isHero || 'GYQR']();
        } catch (e) { return false; }
      }
      var monster = false;
      try {
        monster = target && typeof target[ct.isMonster || 'FYSR'] === 'function'
          ? !!target[ct.isMonster || 'FYSR']() : false;
      } catch (e) {}
      if (monster && (hero(actor) || hero(owner))) recordAction();
    });

    bus.__zzCollectHooked = true;
    bus.__zzCollectWatchVersion = WATCH_VERSION;
    return 'ok';
  } catch (e) { return 'ERR ' + ((e && e.message) || e); }
})()
""" % _js(mapping)


def _collect_matches(keyword: str, drops: Any) -> bool:
    """按背包掉落名判断是否采到目标。"""
    names = []
    if isinstance(drops, list):
        for drop in drops:
            if not isinstance(drop, dict):
                continue
            name = str(drop.get("name") or "").strip()
            if name:
                names.append(name)
    if not names:
        return False
    if keyword == "活动":
        return any(
            not any(word in name for word in DAILY_DROP_WORDS)
            for name in names
        )
    words = [str(word).lower() for word in COLLECT_KEYWORDS.get(keyword, (keyword,))]
    return any(any(word in name.lower() for word in words) for name in names)


class InstructionRunner:
    """单次执行的指令运行器（和参考工具一样：跑完就停，不做无限循环）。"""

    def __init__(
        self,
        bridge_provider: Callable[[], Any],
        log: Optional[Callable[[str], None]] = None,
        on_step: Optional[Callable[[int, int, str], None]] = None,
        on_finished: Optional[Callable[[bool, str], None]] = None,
        on_slot: Optional[Callable[[int], None]] = None,
    ) -> None:
        self._bridge_provider = bridge_provider
        self._log = log or (lambda _msg: None)
        self._on_step = on_step or (lambda _i, _n, _text: None)
        self._on_finished = on_finished or (lambda _ok, _msg: None)
        self._on_slot = on_slot or (lambda _no: None)
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

    def start(
        self,
        title: str,
        content: str,
        mapping: Optional[Dict[str, Any]] = None,
        build: Optional[str] = None,
        slots: Optional[Dict[int, str]] = None,
        start_slot: int = 1,
    ) -> None:
        if self.running:
            raise RuntimeError("上一条指令还在跑，先点「停止」")

        raw_slots: Dict[int, str] = {}
        if slots:
            for key, value in slots.items():
                try:
                    no = int(key)
                except (TypeError, ValueError):
                    continue
                if 1 <= no <= SLOT_COUNT:
                    raw_slots[no] = str(value or "")
        if not raw_slots:
            raw_slots = {1: content}

        parsed: Dict[int, List[Dict[str, Any]]] = {}
        for no, text in raw_slots.items():
            try:
                steps = parse_script(text)
            except ScriptError as exc:
                raise ScriptError("槽位 %d：%s" % (no, exc)) from exc
            if steps:
                parsed[no] = steps
        if not parsed:
            raise ScriptError("所有槽位都是空的")
        if start_slot not in parsed:
            raise ScriptError("槽位 %d 是空的" % start_slot)

        resolved = with_current_build(mapping or {}, build)
        self._stop.clear()
        self.title = title
        self.running = True
        self._thread = threading.Thread(
            target=self._run, args=(title, parsed, resolved, int(start_slot)), daemon=True
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

    def _run(
        self,
        title: str,
        slots_steps: Dict[int, List[Dict[str, Any]]],
        mapping: Dict[str, Any],
        start_slot: int,
    ) -> None:
        ok = False
        message = "已完成"
        self._mapping = mapping
        slot = int(start_slot)
        steps = slots_steps.get(slot) or []
        jumps = 0
        try:
            self._log_line(
                "▶ 运行指令：%s（槽位 %d，%d 步）" % (title, slot, len(steps))
            )
            self._collect_watch_error = ""
            self._eval(JS_INSTALL)
            try:
                self._eval(_js_collect_watch(mapping))
                # 本次脚本开始前的采集事件不算，移动途中新采的才算。
                self._eval("window.__zzCollectConsumed=Date.now();'ok'")
            except Exception:  # noqa: BLE001
                pass

            self._on_slot(slot)
            index = 0
            while True:
                if self._stop.is_set():
                    raise _Stopped()
                if index >= len(steps):
                    break
                step = steps[index]
                index += 1
                name = step["name"]
                args = step["args"]
                self._on_step(index, len(steps), step["raw"])
                self._log_line(
                    "  [槽位%d %d/%d] %s" % (slot, index, len(steps), step["raw"])
                )
                self._ensure_collect_watch()
                try:
                    self._exec(name, args, mapping)
                except _JumpTo as jump:
                    jumps += 1
                    if jumps > 1000:
                        raise RuntimeError("跳转次数超过 1000 次，可能有死循环")
                    target = int(jump.slot)
                    target_steps = slots_steps.get(target) or []
                    if not target_steps:
                        raise RuntimeError("槽位 %d 是空的" % target)
                    slot = target
                    steps = target_steps
                    index = 0
                    self._on_slot(slot)
                    self._log_line("    ↪ 跳转 → 槽位 %d（%d 步）" % (slot, len(steps)))
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
            if len(args) != 1:
                raise RuntimeError("等待指令需要一个秒数，例如「等待(2)」")
            seconds = self._number(args, 0, "等待秒数")
            if seconds < 0:
                raise RuntimeError("等待秒数必须是大于或等于 0 的数字")
            self._sleep(seconds)
            return
        if name in ("停止", "结束", "stop"):
            if args:
                raise RuntimeError("停止指令不接收参数，请使用「停止()」")
            raise _Stopped()
        if name in ("点击", "单击", "click"):
            if len(args) != 2:
                raise RuntimeError("点击指令需要 X、Y 百分比，例如「点击(39.00,16.00)」")
            self._click(self._number(args, 0, "X"), self._number(args, 1, "Y"))
            return
        if name in ("滚轮", "wheel"):
            self._wheel(args)
            return
        if name in ("移动", "move"):
            self._move(args)
            return
        if name in ("回城", "home"):
            if args:
                raise RuntimeError("回城指令不接收参数，请使用「回城()」")
            self._home(mapping)
            return
        if name in ("传送", "teleport"):
            if len(args) not in (2, 3):
                raise RuntimeError("传送指令需要章节和地图，可选大陆，例如「传送(1,2)」或「传送(1,2,0)」")
            self._teleport(args, mapping)
            return
        if name in ("跳转", "jump"):
            if len(args) != 1:
                raise RuntimeError("跳转指令需要一个槽位编号，例如「跳转(2)」")
            try:
                slot_no = int(round(float(str(args[0]).strip())))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("跳转的槽位编号必须是数字") from exc
            if slot_no < 1 or slot_no > SLOT_COUNT:
                raise RuntimeError("跳转的槽位编号必须是 1-%d" % SLOT_COUNT)
            raise _JumpTo(slot_no)
        if name in ("采集", "collect"):
            if len(args) != 1 or not str(args[0]).strip() or len(str(args[0])) > 40:
                raise RuntimeError("采集指令需要一个物品名称或「活动」，例如「采集(红矿石)」或「采集(活动)」")
            self._collect(args)
            return
        if name in ("开宝箱", "chest"):
            if len(args) > 2:
                raise RuntimeError("开宝箱指令格式为「开宝箱(个数)」或「开宝箱(个数,普通/双倍)」")
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
            value = float(str(args[index]).strip())
        except ValueError as exc:
            raise RuntimeError("%s 不是数字：%s" % (label, args[index])) from exc
        if not math.isfinite(value):
            raise RuntimeError("%s 不是有效数字：%s" % (label, args[index]))
        return value

    @staticmethod
    def _angle(value: Any) -> float:
        text = str(value).strip()
        if text in DIRECTION_ANGLES:
            return DIRECTION_ANGLES[text]
        if text.endswith("度"):
            text = text[:-1].strip()
        try:
            angle = float(text)
        except ValueError as exc:
            raise RuntimeError(
                "移动方向必须是八方向名称或角度数值，例如「右上」或「15.0」"
            ) from exc
        if not math.isfinite(angle):
            raise RuntimeError("移动方向的角度不是有效数字：%s" % value)
        return angle % 360.0

    @staticmethod
    def _percent(value: float, label: str) -> float:
        if not math.isfinite(value) or value < 0 or value > 100:
            raise RuntimeError("%s 必须是 0 到 100 之间的视口百分比" % label)
        return value

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
        """指令里的坐标是视口百分比（0~100），和 game_storage_sync 一致。"""
        view = view or self._viewport()
        px = self._percent(float(x), "X") / 100.0 * view["w"]
        py = self._percent(float(y), "Y") / 100.0 * view["h"]
        return px, py

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

    def _cdp_wheel(self, dx: float, dy: float, x: float, y: float) -> None:
        self._cdp(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel", "x": x, "y": y,
                "deltaX": dx, "deltaY": dy, "button": "none", "buttons": 0,
            },
        )

    def _wheel(self, args: List[str]) -> None:
        """滚轮(上|下|左|右, 视口百分比[, X%, Y%])，和 game_storage_sync 一致。"""
        if len(args) not in (2, 4):
            raise RuntimeError("滚轮指令格式为「滚轮(下,25.00)」或「滚轮(下,25.00,27.00,28.00)」")
        direction = str(args[0]).strip()
        if direction not in ("上", "下", "左", "右"):
            raise RuntimeError("滚轮方向必须是「上」、「下」、「左」或「右」")
        amount = self._number(args, 1, "滚轮总滚动量")
        if amount <= 0:
            raise RuntimeError("滚轮总滚动量必须是大于 0 的数字")
        view = self._viewport()
        if len(args) == 4:
            x, y = self._point(
                self._number(args, 2, "滚轮 X"), self._number(args, 3, "滚轮 Y"), view
            )
        else:
            x, y = view["w"] / 2.0, view["h"] / 2.0
        if direction in ("上", "下"):
            dx = 0.0
            dy = (-1.0 if direction == "上" else 1.0) * amount / 100.0 * view["h"]
        else:
            dx = (-1.0 if direction == "左" else 1.0) * amount / 100.0 * view["w"]
            dy = 0.0
        self._cdp_wheel(dx, dy, float(x), float(y))
        self._sleep(0.2)

    def _move(self, args: List[str]) -> None:
        """移动(方向或角度, 秒)：按住虚拟摇杆走指定方向，和 exe 一样走 CDP 原生输入。"""
        if len(args) != 2:
            raise RuntimeError("移动指令需要方向和持续秒数，例如「移动(右上,6.5)」")
        angle = self._angle(args[0])
        seconds = self._number(args, 1, "移动秒数")
        if seconds < 0.1:
            raise RuntimeError("移动拖动时间必须是大于或等于 0.1 秒的数字")
        rad = math.radians(angle)
        dx = math.sin(rad)
        dy = -math.cos(rad)
        cfg = load_automation_config()
        center = cfg.get("movement_center") or {"x": 50.0, "y": 78.0}
        radius_pct = float(cfg.get("movement_radius") or 8.0)
        view = self._viewport()
        cx = view["w"] * float(center.get("x") or 50.0) / 100.0
        cy = view["h"] * float(center.get("y") or 78.0) / 100.0
        radius = min(view["w"], view["h"]) * radius_pct / 100.0
        tx = cx + radius * dx
        ty = cy + radius * dy
        self._cdp(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": cx, "y": cy, "button": "none", "buttons": 0},
        )
        self._cdp(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "buttons": 1, "clickCount": 1},
        )
        self._sleep(0.05)
        try:
            end = time.monotonic() + max(0.1, seconds)
            while time.monotonic() < end:
                if self._stop.is_set():
                    raise _Stopped()
                self._cdp(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": tx, "y": ty, "button": "left", "buttons": 1},
                )
                time.sleep(0.08)
        finally:
            try:
                self._cdp(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseReleased", "x": tx, "y": ty, "button": "left", "buttons": 0, "clickCount": 1},
                )
            except Exception:  # noqa: BLE001
                pass
        self._sleep(0.2)

    # ---- 地图指令

    def _home(self, mapping: Dict[str, Any]) -> None:
        before = self._home_state(mapping).get("map")
        result = self._eval(_js_home(mapping))
        if str(result).startswith("ERR"):
            self._log_line("    %s，改走营地传送" % str(result)[4:])
            result = self._eval(_js_home_camp(mapping))
            if str(result).startswith("ERR"):
                raise RuntimeError("回城失败：%s" % str(result)[4:])
        else:
            self._log_line("    回城 → 主城（%s）" % str(result)[3:])
        if self._wait_home(mapping, before, 8.0):
            return
        self._log_line("    主城传送没反应，改走营地传送")
        result = self._eval(_js_home_camp(mapping))
        if str(result).startswith("ERR"):
            raise RuntimeError("回城失败：%s" % str(result)[4:])
        self._wait_home(mapping, before, 4.0)

    def _home_state(self, mapping: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return json.loads(str(self._eval(_js_home_state(mapping)) or "{}"))
        except ValueError:
            return {}

    def _wait_home(self, mapping: Dict[str, Any], before: Any, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.5, timeout)
        while time.monotonic() < deadline:
            if self._stop.is_set():
                raise _Stopped()
            state = self._home_state(mapping)
            if state.get("done") and not state.get("loading"):
                return True
            after = str(state.get("map") or "")
            if before and after and after != str(before) and not state.get("loading"):
                return True
            time.sleep(0.3)
        return False

    def _teleport(self, args: List[str], mapping: Dict[str, Any]) -> None:
        if len(args) < 2:
            raise RuntimeError("传送指令需要章节和地图，可选大陆，例如「传送(1,2)」或「传送(1,2,0)」")
        try:
            chapter = int(round(float(str(args[0]).strip())))
            section = int(round(float(str(args[1]).strip())))
        except ValueError as exc:
            raise RuntimeError("传送 的章节/地图必须是数字") from exc
        if chapter < 1 or section < 1:
            raise RuntimeError("传送的章节和地图必须是大于或等于 1 的整数")
        continent = 1
        if len(args) > 2 and str(args[2]).strip() != "":
            try:
                continent = int(round(float(str(args[2]).strip())))
            except ValueError as exc:
                raise RuntimeError("传送 的大陆必须是数字") from exc
            if continent < 0:
                raise RuntimeError("传送的大陆必须是大于或等于 0 的整数")
        cfg = load_automation_config()
        table = cfg.get("teleport_mappings") or {}
        exact = "%d.%d.%d" % (chapter, section, continent)
        candidates = [(exact, table[exact])] if exact in table else []
        if not candidates:
            # 省略/未收录的大陆：退回同一章/地图的传送点，按编号顺序试。
            candidates = [
                (k, v) for k, v in table.items()
                if k.startswith("%d.%d." % (chapter, section))
            ]
            candidates.sort(key=lambda kv: kv[0])
        if not candidates:
            raise RuntimeError("地图 %d-%d 还没收录传送点" % (chapter, section))
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
        same_map = bool(before_map) and any(str(mid) == before_map for mid, _tid, _n in spots)
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
                    deadline = time.monotonic() + (6.0 if same_map else 25.0)
                    since_click = time.monotonic()
                    while time.monotonic() < deadline:
                        if self._stop.is_set():
                            raise _Stopped()
                        time.sleep(0.5)
                        st = _state()
                        if not st.get("loading") and str(st.get("map")) != before_map:
                            self._log_line("    传送完成（地图 %s）" % st.get("map"))
                            self._sleep(0.8)
                            return
                        # 同图传送：地图 id 不会变，点完条目后等一会儿就按已重定位处理
                        if same_map and time.monotonic() - since_click >= 2.5:
                            self._log_line("    已点目标地图（同图重定位），继续")
                            self._sleep(0.8)
                            return
                    self._log_line("    面板点了，但地图没变，改走接口传送")
                else:
                    self._log_line("    %s" % ret2)
            else:
                self._log_line("    %s，改走接口传送" % ret)

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
        """跳转需要多脚本槽位；当前只做执行核心（单脚本），直接给出明确提示。"""
        raise RuntimeError("单脚本模式不支持「跳转」（需要多脚本槽位）")

    def _chest(self, args: List[str]) -> None:
        """开宝箱(个数,奖励模式)：个数不填=尽量全开；奖励模式=普通/双倍。"""
        mapping = getattr(self, "_mapping", {}) or {}
        cb = ((mapping.get("builds") or {}).get(mapping.get("current") or "") or {}).get("chest") or {}
        if cb.get("supported") is False:
            raise RuntimeError("开宝箱 暂未适配当前游戏版本（%s）" % (mapping.get("current") or "未知"))
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
        build = str(mapping.get("current") or "")
        if build == "4758a" and cb.get("supported"):
            reward_mode = "double" if mult == 2 else ("auto" if not mode else "normal")
            run_chest_4758a(self, limit, reward_mode)
            return
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
            self._eval(_js_chest_claim(mapping, cfg, uid, mult))
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

    def _ensure_collect_watch(self) -> None:
        """确保当前地图的采集事件监听已挂上（切图后自动重挂）。"""
        try:
            result = str(self._eval(_js_collect_watch(self._mapping)) or "")
            if result.startswith("ERR") and result != getattr(self, "_collect_watch_error", ""):
                self._collect_watch_error = result
                self._log_line("    采集监听未就绪：%s" % result[4:])
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if msg != getattr(self, "_collect_watch_error", ""):
                self._collect_watch_error = msg
                self._log_line("    采集监听未就绪：%s" % msg)

    def _collect(self, args: List[str]) -> None:
        """采集：和参考工具一样，只等游戏自己采，不寻路、不瞬移。"""
        keyword = str(args[0]).strip() if args else "活动"
        self._ensure_collect_watch()
        self._log_line("    等游戏自己采集「%s」…" % keyword)

        now_ms = int(time.time() * 1000)
        try:
            consumed = int(self._eval("Number(window.__zzCollectConsumed)||0") or 0)
        except Exception:  # noqa: BLE001
            consumed = 0
        since = max(consumed, now_ms - COLLECT_LOOKBACK_MS)
        deadline = time.monotonic() + COLLECT_MAX_WAIT
        last_action = now_ms
        seen = set()
        matched = None
        observed = []

        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                raw = str(self._eval("JSON.stringify((window.__zzCollectLog||[]).slice(-80))") or "[]")
                events = json.loads(raw) if raw.startswith("[") else []
            except (TypeError, ValueError):
                events = []
            done_matched = False
            for event in events:
                if not isinstance(event, dict):
                    continue
                stamp = int(event.get("t") or 0)
                if stamp < since:
                    continue
                key = (stamp, event.get("kind"), event.get("id"), event.get("x"), event.get("y"))
                if key in seen:
                    continue
                seen.add(key)
                if event.get("hero"):
                    last_action = max(last_action, stamp)
                drops = event.get("drops") or []
                for drop in drops:
                    if not isinstance(drop, dict):
                        continue
                    name = str(drop.get("name") or "").strip()
                    if not name:
                        continue
                    label = "%s×%d" % (name, int(drop.get("count") or 0))
                    if label not in observed:
                        observed.append(label)
                if _collect_matches(keyword, drops):
                    matched = (event, drops)
                    if event.get("kind") == "done":
                        done_matched = True
                        break
            if done_matched:
                break
            # 参考工具：英雄动作停止 5 秒后继续下一步，确保采集/结算完成。
            if int(time.time() * 1000) - last_action >= COLLECT_IDLE_MS:
                break
            self._sleep(0.3)

        try:
            self._eval("window.__zzCollectConsumed=Date.now();'ok'")
        except Exception:  # noqa: BLE001
            pass

        if matched:
            _event, drops = matched
            names = []
            for drop in drops[:4]:
                if not isinstance(drop, dict):
                    continue
                name = str(drop.get("name") or "").strip()
                count = int(drop.get("count") or 0)
                if name:
                    names.append("%s×%d" % (name, count) if count > 0 else name)
            self._log_line("    采到了：%s" % ("、".join(names) or keyword))
        else:
            self._log_line("    没有等到「%s」采集（附近没有就继续）" % keyword)
            if observed:
                self._log_line("    最近看到掉落：%s" % "、".join(observed[:8]))

class _Stopped(Exception):
    """内部用：用户点了停止。"""


class _JumpTo(Exception):
    """内部用：跳转到另一个槽位。"""

    def __init__(self, slot: int) -> None:
        super().__init__()
        self.slot = int(slot)
