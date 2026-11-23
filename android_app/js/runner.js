(function(){try{
(function () {
  if (window.__zzCmd && window.__zzCmd.v === 4) return 'ok';
  var find = function (x, y) {
    // 优先直接发给游戏画布：切图/加载时可能有盖在上面的节点，
    // elementFromPoint 会拿到临时元素，加载完就变成脱节节点，后续事件全丢。
    try { if (window.cc && cc.game && cc.game.canvas) return cc.game.canvas; } catch (e) { /* ignore */ }
    var c = null;
    try { c = document.getElementById("GameCanvas") || document.querySelector("canvas"); } catch (e) { c = null; }
    if (c) return c;
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
  var touchMode = ("ontouchstart" in window) || (navigator.maxTouchPoints || 0) > 0;
  window.__zzCmd = {
    v: 4,
    click: function (x, y) {
      var t = find(x, y);
      if (touchMode) {
        fire(t, 'touchstart', x, y, 1);
        fire(t, 'touchend', x, y, 0);
      } else {
        fire(t, 'pointerdown', x, y, 1); fire(t, 'mousedown', x, y, 1);
        fire(t, 'pointerup', x, y, 0); fire(t, 'mouseup', x, y, 0);
        fire(t, 'click', x, y, 0);
      }
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
      if (touchMode) {
        fire(el, 'touchstart', x, y, 1);
      } else {
        fire(el, 'pointerdown', x, y, 1); fire(el, 'mousedown', x, y, 1);
      }
      return 'ok';
    },
    move: function (x, y) {
      var t = find(x, y) || el;
      if (touchMode) {
        fire(t, 'touchmove', x, y, 1);
      } else {
        fire(t, 'pointermove', x, y, 1); fire(t, 'mousemove', x, y, 1);
      }
      return 'ok';
    },
    up: function (x, y) {
      var t = find(x, y) || el;
      if (touchMode) {
        fire(t, 'touchend', x, y, 0);
      } else {
        fire(t, 'pointerup', x, y, 0); fire(t, 'mouseup', x, y, 0);
      }
      el = null;
      return 'ok';
    },
    size: function () { return { w: window.innerWidth, h: window.innerHeight }; }
  };
  return 'ok';
})()
}catch(e){}})();

(function () {
  if (window.__zzRun && window.__zzRun.v === 4) return;
  var ANGLES = {
    "上": 0, "右上": 45, "右": 90, "右下": 135,
    "下": 180, "左下": 225, "左": 270, "左上": 315
  };
  var state = { running: false, stop: false, step: 0, total: 0, text: "", error: "", name: "", slot: 1, done: null };
  var CHEST = {
    service: "DNTD", query: "XMQR", claim: "SUZK", detail: "ZDKN",
    quotaInfo: "EIYZ", quotaTotal: "BJAX", quotaUsed: "QNQM",
    holder: "GCOA", world: "VZZY", moxing: "STDU", container: "OQCB", table: "YVTD",
    compModule: "OIMR", comp: "WUEK", typeModule: "QOBZ", types: "IJAN", bossType: "BWVI",
    unit: "BSSG", unitId: "TOXH", interact: "SARG", data: "DVPV", has: "HCCM",
    typeField: "LFWE", bossField: "LHUO", srcField: "NNPY", idxField: "POBP",
    yInfo: "WZMM", yIsBoss: "LMLA", yBoss: "CAVE", ySrc: "ZKDC", yIdx: "NJCG",
    normal: "NMMA", double: "KAQL", doubleBtn: "ZUOO"
  };
  var COLLECT_KEYWORDS = {
    "木材": ["木材", "木头", "木"],
    "矿石": ["蓝矿石", "红矿石", "矿石", "矿"],
    "蓝矿石": ["蓝矿石", "蓝矿"],
    "红矿石": ["红矿石", "红矿"],
    "彩矿": ["彩矿", "矿石"],
    "宝石": ["宝石"],
    "幸运草": ["幸运草"],
    "训练石": ["训练石"],
    "肉块": ["肉块", "肉"],
    "活动": ["*"]
  };
  var DAILY_DROP_WORDS = ["宝石", "训练石", "幸运草", "彩矿", "矿石", "木材", "肉块", "金币", "钻石"];
  var COLLECT_IDLE_MS = 5000;
  var COLLECT_MAX_WAIT_MS = 15000;
  var COLLECT_LOOKBACK_MS = 15000;

  function mapCfg() {
    var m = window.__zzRun.MAPPING || { builds: {} };
    var b = (m.builds || {})[m.current] || {};
    return { m: m, b: b, mc: b.map || {} };
  }

  function evalJs(code) {
    // 用 Function 在当前页面执行（和执行器同一环境）
    try { return (0, eval)(code); } catch (e) { return "ERR " + ((e && e.message) || e); }
  }

  function sleep(ms) {
    return new Promise(function (res, rej) {
      var t0 = Date.now();
      (function tick() {
        if (state.stop) return rej(new Error("STOP"));
        if (Date.now() - t0 >= ms) return res();
        setTimeout(tick, 50);
      })();
    });
  }

  function parseAll(content) {
    var lines = String(content || "").replace(/\r\n?/g, "\n").split("\n");
    var steps = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].replace(/（/g, "(").replace(/）/g, ")").replace(/，/g, ",").trim();
      if (!line || line.charAt(0) === "#") continue;
      var m = /^([^()\s]+)\s*\((.*)\)\s*$/.exec(line);
      if (!m) throw new Error("第 " + (i + 1) + " 行不是「指令(参数)」格式：" + line);
      var body = m[2].trim();
      var args = body ? body.split(",").map(function (s) { return s.trim(); }) : [];
      steps.push({ name: m[1], args: args, raw: line });
    }
    return steps;
  }

  function parseSlots(payload) {
    var slots = {};
    var start = 1;
    if (payload && typeof payload === "object" && payload.slots) {
      Object.keys(payload.slots).forEach(function (key) {
        var no = Number(key);
        if (!isFinite(no) || no < 1 || no > 99) return;
        var steps = parseAll(payload.slots[key] || "");
        if (steps.length) slots[Math.round(no)] = steps;
      });
      start = Math.round(Number(payload.start) || 1);
    } else {
      var steps = parseAll(String(payload || ""));
      if (steps.length) slots[1] = steps;
    }
    return { slots: slots, start: start };
  }

  function num(args, i, label) {
    if (i >= args.length || !String(args[i]).trim()) throw new Error(label + " 缺少参数");
    var v = Number(args[i]);
    if (!isFinite(v)) throw new Error(label + " 不是数字：" + args[i]);
    return v;
  }

  function angle(value) {
    var text = String(value || "").trim();
    if (Object.prototype.hasOwnProperty.call(ANGLES, text)) return ANGLES[text];
    if (text.slice(-1) === "度") text = text.slice(0, -1).trim();
    var n = Number(text);
    if (!isFinite(n)) throw new Error("移动方向必须是八方向名称或角度数值，例如「右上」或「15.0」");
    return ((n % 360) + 360) % 360;
  }

  function pct(value, label) {
    var n = Number(value);
    if (!isFinite(n) || n < 0 || n > 100) throw new Error(label + " 必须是 0 到 100 之间的视口百分比");
    return n;
  }

  function viewSize() {
    var s = window.__zzCmd && window.__zzCmd.size ? window.__zzCmd.size() : null;
    return { w: s && s.w ? s.w : innerWidth, h: s && s.h ? s.h : innerHeight };
  }

  // 游戏页被助手盖住/切到后台时，Cocos 会暂停，模拟摇杆不会生效。
  function mapLoading() {
    try {
      var mc = (mapCfg().mc || {});
      var world = window[mc.holder || "GCOA"];
      var scene = world && world[mc.scene || "STDU"];
      if (scene && mc.loading && scene[mc.loading]) return true;
    } catch (e) { /* ignore */ }
    return false;
  }

  function pagePlayable() {
    try { if (document.visibilityState === "hidden") return false; } catch (e) { /* ignore */ }
    try {
      if (window.cc && cc.game && typeof cc.game.isPaused === "function" && cc.game.isPaused()) {
        return false;
      }
    } catch (e) { /* ignore */ }
    // 切图/传送时游戏会锁住摇杆：这段时间按下去会被直接丢掉
    if (mapLoading()) return false;
    return true;
  }

  function waitPlayable(limitMs) {
    var began = Date.now();
    return new Promise(function (resolve) {
      (function check() {
        if (state.stop) return resolve(false);
        if (pagePlayable()) return resolve(true);
        if (Date.now() - began >= limitMs) return resolve(false);
        setTimeout(check, 150);
      })();
    });
  }

  // ---------- 各指令 ----------
  function cmdWait(args) {
    var secs = num(args, 0, "等待秒数");
    if (secs < 0) throw new Error("等待秒数必须是大于或等于 0 的数字");
    return sleep(secs * 1000);
  }

  function cmdClick(args) {
    var x = pct(num(args, 0, "点击 X"), "点击 X");
    var y = pct(num(args, 1, "点击 Y"), "点击 Y");
    var s = viewSize();
    // 刚传送完还在加载时点击也会被游戏忽略，先等一小会儿
    return waitPlayable(8000).then(function () {
      var res = evalJs("(function(){try{return String(window.__zzCmd&&window.__zzCmd.click?window.__zzCmd.click(" +
        (x / 100 * s.w) + "," + (y / 100 * s.h) + "):'no')}catch(e){return 'ERR '+e.message}})()");
      if (res && String(res).indexOf("ERR") === 0) throw new Error(String(res));
      return sleep(120);
    });
  }

  function cmdWheel(args) {
    if (args.length !== 2 && args.length !== 4) {
      throw new Error("滚轮指令格式为「滚轮(下,25.00)」或「滚轮(下,25.00,27.00,28.00)」");
    }
    var dir = String(args[0] || "").trim();
    if (["上", "下", "左", "右"].indexOf(dir) < 0) {
      throw new Error("滚轮方向必须是「上」、「下」、「左」或「右」");
    }
    var amount = num(args, 1, "滚轮总滚动量");
    if (!(amount > 0)) throw new Error("滚轮总滚动量必须是大于 0 的数字");
    var s = viewSize();
    var x = s.w / 2, y = s.h / 2;
    if (args.length === 4) {
      x = pct(num(args, 2, "滚轮 X"), "滚轮 X") / 100 * s.w;
      y = pct(num(args, 3, "滚轮 Y"), "滚轮 Y") / 100 * s.h;
    }
    var dx = 0, dy = 0;
    if (dir === "上" || dir === "下") dy = (dir === "上" ? -1 : 1) * amount / 100 * s.h;
    else dx = (dir === "左" ? -1 : 1) * amount / 100 * s.w;
    evalJs("(function(){try{window.__zzCmd.wheel(" + dx + "," + dy + "," + x + "," + y + ")}catch(e){}})()");
    return sleep(120);
  }

  function cmdMove(args) {
    if (!args.length) throw new Error("移动指令需要方向和持续秒数，例如「移动(右上,6.5)」");
    var a = angle(args[0]);
    var secs = args.length > 1 ? num(args, 1, "移动秒数") : 1.0;
    if (secs < 0.1) throw new Error("移动拖动时间必须是大于或等于 0.1 秒的数字");
    var rad = a * Math.PI / 180;
    var dx = Math.sin(rad), dy = -Math.cos(rad);
    var cfg = window.__zzRun.MOVE || {};
    var cx = cfg.cx === undefined ? 50 : cfg.cx;
    var cy = cfg.cy === undefined ? 78 : cfg.cy;
    var r = cfg.r === undefined ? 8 : cfg.r;
    var code = "(function(){try{var s=window.__zzCmd.size();" +
      "var cx=s.w*" + cx + "/100,cy=s.h*" + cy + "/100;" +
      "var rr=Math.min(s.w,s.h)*" + r + "/100;";
    var down = code + "window.__zzCmd.down(cx,cy);return 'ok'}catch(e){return 'ERR '+e.message}})()";
    var move = code + "window.__zzCmd.move(cx+rr*" + dx + ",cy+rr*" + dy + ");return 'ok'}catch(e){return 'ERR '+e.message}})()";
    var up = "(function(){try{window.__zzCmd.up()}catch(e){}})()";

    // 传送/切图时游戏线程会卡住，按真实经过时间算会把移动时间全耗掉。
    // 这里改成按“游戏没卡顿的时间”累计，卡住的那段直接跳过。
    return waitPlayable(30000).then(function (ok) {
      if (!ok) throw new Error(state.stop ? "STOP" : "地图还在加载或游戏画面没在前台，摇杆暂时用不了；请稍等或让游戏保持显示");
      var res = evalJs(down);
      if (String(res).indexOf("ERR") === 0) throw new Error(String(res));
      return new Promise(function (resolve, reject) {
        var moved = 0;
        var last = performance.now();
        var began = Date.now();
        var hardLimit = secs * 1000 + 30000;
        var done = false;
        var needDown = false;
        function finish(err) {
          if (done) return;
          done = true;
          clearInterval(timer);
          evalJs(up);
          if (err) reject(err); else resolve();
        }
        var timer = setInterval(function () {
          if (state.stop) return finish(new Error("STOP"));
          var now = performance.now();
          var dt = now - last;
          last = now;
          if (Date.now() - began > hardLimit) {
            return finish(new Error("移动超时：游戏画面可能被切到后台了"));
          }
          if (!pagePlayable()) {
            // 游戏被盖住/暂停：不计时，等它回到前台再继续
            needDown = true;
            return;
          }
          // 单帧超过 300ms 说明主线程卡了一下（切图/加载），这段时间不算移动
          if (dt > 0 && dt < 300) moved += dt;
          else if (dt >= 300) needDown = true;
          if (moved >= secs * 1000) return finish();
          // 切图/暂停后 Cocos 可能把之前的触摸丢掉了，重新按一次摇杆
          if (needDown) {
            needDown = false;
            evalJs(down);
          }
          evalJs(move);
        }, 60);
      });
    });
  }

  function cmdHome() {
    var cfg = mapCfg();
    var b = cfg.b || {};
    var rev = b.revive || {};
    var ct = rev.campTeleport || {};
    var rn = b.runtimeNames || {};
    var names = ct.campCoords || ["TCLA", "CLAT"];
    var holder = cfg.mc.holder || "VKXX";
    var core = cfg.mc.core || "ZRRU";
    var api = ct.api || rn.ZAVH || "ZAVH";
    var leave = rn.MEMK || "MEMK";
    // 优先和 exe 一样走主城地图传送 1,1；不行再退回营地坐标。
    try {
      var world = window[holder];
      var mapApi = cfg.mc.teleport || "LJCS";
      if (world && typeof world[mapApi] === "function") {
        world[mapApi](1, 1, function () {});
        return sleep(700).then(function () {
          return waitPlayable(30000);
        }).then(function (ok) {
          if (!ok) throw new Error(state.stop ? "STOP" : "回城后地图加载太久，摇杆用不了；请让游戏画面保持显示");
          return sleep(400);
        });
      }
    } catch (e) { /* fall through */ }
    var code = "(function(){try{var world=window['" + holder + "'];var core=world&&world['" + core + "'];" +
      "if(!world||!core)return 'ERR 游戏还没进地图';var spot=null;" +
      "var names=" + JSON.stringify(names) + ";" +
      "for(var i=0;i<names.length;i++){try{var fn=core[names[i]];if(typeof fn==='function'){var p=fn.call(core);" +
      "if(p&&isFinite(p.x)&&isFinite(p.y)){spot=p;break;}}}catch(e){}}" +
      "if(!spot)return 'ERR 读不到营地坐标';" +
      "try{if(typeof world['" + leave + "']==='function')world['" + leave + "']()}catch(e){}" +
      "if(typeof world['" + api + "']!=='function')return 'ERR 游戏没有传送入口（" + api + "）';" +
      "world['" + api + "'](spot.x,spot.y,true,function(){});return 'ok'}catch(e){return 'ERR '+e.message}})()";
    var res = evalJs(code);
    if (String(res).indexOf("ERR") === 0) throw new Error(String(res));
    // 接口传送后要等游戏真正加载完（STDU.VOAT 变 false），否则接下来的摇杆会被丢掉
    return sleep(700).then(function () {
      return waitPlayable(30000);
    }).then(function (ok) {
      if (!ok) throw new Error(state.stop ? "STOP" : "传送后地图加载太久，摇杆用不了；请让游戏画面保持显示");
      return sleep(400);
    });
  }

  function cmdTeleport(args) {
    if (args.length < 2) throw new Error("传送指令需要章节和地图，可选大陆，例如「传送(1,2)」或「传送(1,2,1)」");
    var chapter = Math.round(num(args, 0, "章节"));
    var section = Math.round(num(args, 1, "地图"));
    if (chapter < 1 || section < 1) throw new Error("传送的章节和地图必须是大于或等于 1 的整数");
    var continent = args.length > 2 && String(args[2]).trim() !== "" ? Math.round(num(args, 2, "大陆")) : 1;
    if (continent < 0) throw new Error("传送的大陆必须是大于或等于 0 的整数");
    var table = window.ZZ_TELEPORT || {};
    var prefix = chapter + "." + section + ".";
    var keys = Object.keys(table).filter(function (k) { return k.indexOf(prefix) === 0; }).sort();
    if (!keys.length) throw new Error("地图 " + chapter + "-" + section + " 还没收录传送点");
    var exact = prefix + continent;
    var pick = table[exact] || table[keys[0]];
    if (!pick) throw new Error("地图 " + chapter + "-" + section + " 传送点数据缺失");
    var cfg = mapCfg();
    var world = (cfg.mc.holder || "VKXX");
    var api = (cfg.mc.teleport || "LUSS");
    var code = "(function(){try{var w=window['" + world + "'];if(!w)return 'ERR 游戏还没进地图';" +
      "var f=w['" + api + "'];if(typeof f!=='function')return 'ERR 游戏没有传送入口';" +
      "f.call(w," + Number(pick.map_id) + "," + Number(pick.teleport_id || 0) +
      ",function(){});return 'ok'}catch(e){return 'ERR '+e.message}})()";
    // 地图还在加载时，游戏会直接忽略传送请求
    return waitPlayable(30000).then(function (ok) {
      if (!ok) throw new Error(state.stop ? "STOP" : "地图还在加载或游戏画面没在前台，暂时不能传送；请稍后再试");
      var res = evalJs(code);
      if (String(res).indexOf("ERR") === 0) throw new Error(String(res));
      // 接口传送后要等游戏真正加载完（STDU.VOAT 变 false），否则接下来的摇杆会被丢掉
      return sleep(700).then(function () {
        return waitPlayable(30000);
      }).then(function (ready) {
        if (!ready) throw new Error(state.stop ? "STOP" : "传送后地图加载太久，摇杆用不了；请让游戏画面保持显示");
        return sleep(400);
      });
    });
  }

  function cmdAutoJump(args) {
    if (args && args.length) throw new Error("跳一跳指令不接收参数，请使用「跳一跳()」");
    var res = evalJs(
      "(function(){try{var a=window.__zzAssist;" +
      "if(!a||!a.autoJumpStart)return JSON.stringify({ok:false,message:'运行时还没注入'});" +
      "return JSON.stringify(a.autoJumpStart({}));}" +
      "catch(e){return JSON.stringify({ok:false,message:String((e&&e.message)||e)})}})()"
    );
    var started = null;
    try { started = JSON.parse(res); } catch (e) { started = null; }
    if (!started || !started.ok) {
      throw new Error((started && started.message) || "跳一跳启动失败");
    }
    return new Promise(function (resolve, reject) {
      (function poll() {
        if (state.stop) {
          evalJs("window.__zzAssist&&window.__zzAssist.autoJumpStop&&window.__zzAssist.autoJumpStop()");
          return reject(new Error("STOP"));
        }
        var raw = evalJs(
          "(function(){try{var a=window.__zzAssist;" +
          "return a&&a.autoJumpState?JSON.stringify(a.autoJumpState()):'{}';}" +
          "catch(e){return '{}'}})()"
        );
        var st = null;
        try { st = JSON.parse(raw); } catch (e) { st = null; }
        if (!st) return setTimeout(poll, 500);
        if (st.error) return reject(new Error(st.error));
        if (!st.running) return resolve();
        setTimeout(poll, 500);
      })();
    });
  }

  function installCollectWatch() {
    try {
      var cfg = mapCfg(), b = cfg.b || {}, mc = cfg.mc || {}, ct = b.collect || {};
      var world = window[mc.holder || "VKXX"];
      var core = world && world[mc.core || "ZRRU"];
      if (!core) return "ERR 游戏还没进地图";
      var q = core[ct.queue || "YHST"];
      var holder = q && typeof q === "function" ? q.call(core) : null;
      var bus = holder && holder[ct.dispatch || "KCQB"];
      var entry = ct.entry || "PZQH";
      if (!bus || typeof bus[entry] !== "function") return "ERR 采集事件还没就绪";
      var WATCH_VERSION = 3;
      if (bus.__zzCollectHooked === true && bus.__zzCollectWatchVersion === WATCH_VERSION) return "ok";

      var gen = ct.serviceGen || "DYCU";
      var services = null;
      try {
        var pairs = [...System.entries()];
        for (var i = 0; i < pairs.length; i += 1) {
          var ex = pairs[i][1], bag = ex && ex[gen];
          if (bag && bag[mc.holder || "GCOA"] === world) { services = bag; break; }
        }
      } catch (e) { services = null; }
      var itemModule = services && services[ct.itemModule || "AHWN"];
      var parse = itemModule && itemModule[ct.dropParse || "TEHJ"];
      var attrKey = ct.attribute || "TKWM";
      var dropKey = ct.drops || "SZSN";
      var nameKey = ct.itemName || "FKAZ";
      var countKey = ct.itemCount || "SSYS";

      var log = window.__zzCollectLog = window.__zzCollectLog || [];
      if (typeof window.__zzCollectConsumed !== "number") window.__zzCollectConsumed = 0;
      function text(v) { return v == null ? "" : String(v); }
      function readDrops(unit) {
        var out = [];
        try {
          var attr = unit && unit[attrKey];
          var base = attr && attr[dropKey];
          if (!base || typeof base.length !== "number") return out;
          for (var i = 0; i < base.length; i += 1) {
            var raw = base[i], item = raw;
            try { if (parse) item = parse(raw); } catch (e) { item = raw; }
            var name = "";
            try { if (item && typeof item[nameKey] === "function") name = text(item[nameKey]()); } catch (e) {}
            if (!name) continue;
            var count = 0;
            try { if (item && typeof item[countKey] === "function") count = Number(item[countKey]()) || 0; } catch (e) {}
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
        try { id = Number(unit[ct.entityId || "CXKW"]) || 0; } catch (e) {}
        try { var attr = unit[attrKey]; type = Number(attr && attr[ct.collectType || "GKIJ"]) || 0; } catch (e) {}
        try { x = Math.round(Number(unit[mc.unit && mc.unit.x ? mc.unit.x : "MKCS"]) || 0); } catch (e) {}
        try { y = Math.round(Number(unit[mc.unit && mc.unit.y ? mc.unit.y : "FYYQ"]) || 0); } catch (e) {}
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
          var map = world[mc.scene || "STDU"];
          var cache = map && map[ct.rewardCache || "ICXC"];
          var fn = cache && cache[ct.extraDrops || "GUNK"];
          if (typeof fn !== "function") return out;
          var type = 0;
          try { type = unit[ct.unitType || "QNMZ"]; } catch (e) {}
          var extra = fn.call(cache, unit, type, Number(count));
          if (!extra || typeof extra.length !== "number") return out;
          for (var i = 0; i < extra.length; i += 1) {
            var raw = extra[i];
            try { if (raw && raw[ct.extraItem || "GMMR"] != null) raw = raw[ct.extraItem || "GMMR"]; } catch (e) {}
            var item = raw;
            try { if (parse) item = parse(raw); } catch (e) { item = raw; }
            var name = "";
            try { if (item && typeof item[nameKey] === "function") name = text(item[nameKey]()); } catch (e) {}
            if (!name) continue;
            var n = 0;
            try { if (item && typeof item[countKey] === "function") n = Number(item[countKey]()) || 0; } catch (e) {}
            out.push({ name: name, count: n });
          }
        } catch (e) {}
        return out;
      }
      function recordAction() {
        var now = Date.now();
        var last = log.length ? log[log.length - 1] : null;
        if (last && last.kind === "battle" && now - last.t < 1000) {
          last.t = now;
          return;
        }
        log.push({ t: now, kind: "battle", id: 0, type: 0, x: 0, y: 0, hero: true, drops: [] });
        if (log.length > 80) log.shift();
      }
      function wrapBus(name, handler) {
        var current = bus[name];
        var original = current && current.__zzCollectOriginal ? current.__zzCollectOriginal : current;
        if (typeof original !== "function") return;
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
        var payload = event[ct.eventContent || "AWDY"] || {};
        var unit = payload[ct.eventTarget || "YALQ"];
        var collector = payload[ct.eventCollector || "WTEB"];
        var isCollect = false;
        try {
          isCollect = unit && typeof unit[ct.isCollect || "FFCO"] === "function"
            ? !!unit[ct.isCollect || "FFCO"]() : false;
        } catch (e) {}
        if (!isCollect) return;
        var hero = false;
        try {
          hero = collector && typeof collector[ct.isHero || "GYQR"] === "function"
            ? !!collector[ct.isHero || "GYQR"]() : false;
        } catch (e) {}
        var id = "";
        try { id = String(unit[ct.entityId || "CXKW"] || ""); } catch (e) {}
        if (hero && id) actors[id] = Date.now();
        record("start", unit, hero);
      });

      wrapBus(ct.done || "IPDY", function (args) {
        var event = args[0] || {};
        var unit = null;
        try {
          var map = world[mc.scene || "STDU"];
          var table = map && map[ct.unitTable || "LTDS"];
          var row = table && table.call(map, event[ct.eventUnitId || "MCTA"]);
          unit = row && row[ct.unitEntity || "BSSG"];
        } catch (e) {}
        if (!unit) return;
        var isCollect = false;
        try {
          isCollect = typeof unit[ct.isCollect || "FFCO"] === "function"
            ? !!unit[ct.isCollect || "FFCO"]() : false;
        } catch (e) {}
        if (!isCollect) return;
        var id = "";
        try { id = String(unit[ct.entityId || "CXKW"] || ""); } catch (e) {}
        var hero = !!(id && actors[id] && Date.now() - actors[id] < 30000);
        if (id) delete actors[id];
        record("done", unit, hero, readExtraDrops(unit, event[ct.completionCount || "OXCG"]));
      });
      wrapBus(ct.battle || "PYRD", function (args) {
        var event = args[0] || {};
        var payload = event[ct.eventContent || "AWDY"] || {};
        var actor = payload[ct.battleActor || "BSSG"];
        var owner = payload[ct.battleOwner || "YPTT"];
        var target = payload[ct.battleTarget || "EPUO"];
        function hero(unit) {
          try {
            return unit && typeof unit[ct.isHero || "GYQR"] === "function" && !!unit[ct.isHero || "GYQR"]();
          } catch (e) { return false; }
        }
        var monster = false;
        try {
          monster = target && typeof target[ct.isMonster || "FYSR"] === "function"
            ? !!target[ct.isMonster || "FYSR"]() : false;
        } catch (e) {}
        if (monster && (hero(actor) || hero(owner))) recordAction();
      });

      bus.__zzCollectHooked = true;
      bus.__zzCollectWatchVersion = WATCH_VERSION;
      return "ok";
    } catch (e) { return "ERR " + ((e && e.message) || e); }
  }

  function ensureCollectWatch() {
    try {
      var result = installCollectWatch();
      if (typeof result === "string" && result.indexOf("ERR") === 0 && result !== state.collectWatchError) {
        state.collectWatchError = result;
      }
      return result;
    } catch (e) {
      return "ERR " + ((e && e.message) || e);
    }
  }

  function collectMatches(keyword, drops) {
    var names = [];
    (drops || []).forEach(function (drop) {
      var name = drop && drop.name ? String(drop.name).trim() : "";
      if (name) names.push(name);
    });
    if (!names.length) return false;
    if (keyword === "活动") {
      return names.some(function (name) {
        return !DAILY_DROP_WORDS.some(function (word) { return name.indexOf(word) >= 0; });
      });
    }
    var words = COLLECT_KEYWORDS[keyword] || [keyword];
    var keys = words.map(function (word) { return String(word).toLowerCase(); });
    return names.some(function (name) {
      var lower = name.toLowerCase();
      return keys.some(function (word) { return lower.indexOf(word) >= 0; });
    });
  }

  async function cmdCollect(args) {
    var keyword = args && String(args[0] || "").trim() ? String(args[0]).trim() : "活动";
    ensureCollectWatch();
    state.text = "等游戏自己采集「" + keyword + "」…";

    var now = Date.now();
    var consumed = 0;
    try { consumed = Number(window.__zzCollectConsumed) || 0; } catch (e) {}
    var since = Math.max(consumed, now - COLLECT_LOOKBACK_MS);
    var deadline = now + COLLECT_MAX_WAIT_MS;
    var lastAction = now;
    var seen = Object.create(null);
    var matched = null;

    while (Date.now() < deadline) {
      if (state.stop) throw new Error("STOP");
      var events = window.__zzCollectLog || [];
      var doneMatched = false;
      for (var i = 0; i < events.length; i += 1) {
        var ev = events[i];
        if (!ev || typeof ev !== "object") continue;
        var t = Number(ev.t) || 0;
        if (t < since) continue;
        var key = t + ":" + ev.kind + ":" + ev.id + ":" + ev.x + ":" + ev.y;
        if (seen[key]) continue;
        seen[key] = 1;
        if (ev.hero) lastAction = Math.max(lastAction, t);
        if (collectMatches(keyword, ev.drops || [])) {
          matched = ev;
          if (ev.kind === "done") { doneMatched = true; break; }
        }
      }
      if (doneMatched) break;
      if (Date.now() - lastAction >= COLLECT_IDLE_MS) break;
      await sleep(300);
    }

    try { window.__zzCollectConsumed = Date.now(); } catch (e) {}
    if (matched) {
      var names = [];
      (matched.drops || []).slice(0, 4).forEach(function (drop) {
        if (!drop || !drop.name) return;
        var count = Number(drop.count) || 0;
        names.push(count > 0 ? String(drop.name) + "×" + count : String(drop.name));
      });
      state.text = "采到了：" + (names.join("、") || keyword);
    } else {
      state.text = "没有等到「" + keyword + "」采集（附近没有就继续）";
    }
  }

  function chestSvcFrom(v) {
    if (!v) return null;
    if (typeof v[CHEST.query] === "function" && typeof v[CHEST.claim] === "function") return v;
    try { if (v.IZBA && typeof v.IZBA[CHEST.query] === "function" && typeof v.IZBA[CHEST.claim] === "function") return v.IZBA; } catch (e) {}
    return null;
  }

  async function chestService() {
    var pairs = [];
    try { pairs = [...System.entries()]; } catch (e) { pairs = []; }
    for (var pair of pairs) {
      var ex = pair[1];
      var bags = [[ex, "ex"], [ex && ex.fw_gen, "fw"]];
      for (var bi = 0; bi < bags.length; bi += 1) {
        var bag = bags[bi][0];
        if (!bag) continue;
        var svc = chestSvcFrom(bag[CHEST.service]);
        if (svc) return svc;
        var names = [];
        try { names = Object.getOwnPropertyNames(bag); } catch (e) { names = []; }
        for (var ni = 0; ni < names.length; ni += 1) {
          svc = chestSvcFrom(bag[names[ni]]);
          if (svc) return svc;
        }
      }
    }
    try {
      if (System && typeof System.import === "function") {
        var m = await System.import("chunks:///_virtual/" + CHEST.service + ".ts");
        var found = chestSvcFrom(m && m[CHEST.service]);
        if (found) return found;
        if (m && m.IZBA) return chestSvcFrom(m.IZBA);
        var keys = m ? Object.getOwnPropertyNames(m) : [];
        for (var k = 0; k < keys.length; k += 1) {
          found = chestSvcFrom(m[keys[k]]);
          if (found) return found;
        }
      }
    } catch (e) { /* 模块没加载就继续报服务未就绪 */ }
    return null;
  }

  function chestDrops() {
    var G = window[CHEST.holder];
    var V = G && G[CHEST.world];
    var M = G && G[CHEST.moxing];
    var C = M && M[CHEST.container];
    var U = C && C[CHEST.table];
    if (!V || !U) throw new Error("地图实体未就绪");
    var comp = null, types = null;
    try { comp = (System.get("chunks:///_virtual/" + CHEST.compModule + ".ts") || {})[CHEST.comp]; } catch (e) {}
    try { types = (System.get("chunks:///_virtual/" + CHEST.typeModule + ".ts") || {})[CHEST.types]; } catch (e) {}
    if (!comp || !types) {
      var pairs = [];
      try { pairs = [...System.entries()]; } catch (e) { pairs = []; }
      for (var p = 0; p < pairs.length; p += 1) {
        var ex = pairs[p][1];
        if (!comp && ex && ex[CHEST.comp]) comp = ex[CHEST.comp];
        if (!types && ex && ex[CHEST.types]) types = ex[CHEST.types];
      }
    }
    if (!comp || !types) throw new Error("宝箱组件未就绪");
    var data = V[CHEST.data](comp) || {};
    var bossType = types[CHEST.bossType];
    var rows = [];
    var iter = typeof U.values === "function" ? U.values() : U.entries();
    for (var item of iter) {
      var unit = Array.isArray(item) ? item[1] : item;
      var id = unit && unit[CHEST.unit] && unit[CHEST.unit][CHEST.unitId];
      if (!isFinite(id) || id <= 0) continue;
      if (data[CHEST.typeField][id] !== bossType) continue;
      if (!V[CHEST.has](id, comp)) continue;
      if (!(data[CHEST.bossField][id] > 0)) continue;
      rows.push({
        id: id,
        boss: data[CHEST.bossField][id],
        src: data[CHEST.srcField][id],
        idx: data[CHEST.idxField][id],
        unit: unit
      });
    }
    return rows;
  }

  function chestView(drop) {
    var scene = window.cc && window.cc.director && window.cc.director.getScene();
    var found = null;
    (function walk(n) {
      if (!n || found) return;
      var comps = n.components || n._components || [];
      for (var i = 0; i < comps.length; i += 1) {
        var c = comps[i];
        var info = c && c[CHEST.yInfo];
        if (info && info[CHEST.yIsBoss] === true && info[CHEST.yBoss] === drop.boss
          && (!drop.src || info[CHEST.ySrc] === drop.src)
          && (!drop.idx || info[CHEST.yIdx] === drop.idx)) {
          found = c;
          break;
        }
      }
      var kids = n.children || [];
      for (var k = 0; k < kids.length; k += 1) walk(kids[k]);
    })(scene);
    return found;
  }

  async function cmdChest(args) {
    var limit = 0;
    if (args && String(args[0] || "").trim() !== "") {
      var n = Number(args[0]);
      if (!isFinite(n) || n < 0) throw new Error("开宝箱 的个数必须是数字");
      limit = Math.floor(n);
    }
    var double = !!(args && args[1] && String(args[1]).indexOf("双倍") >= 0);
    var svc = await chestService();
    if (!svc) throw new Error("宝箱服务未就绪");
    var response = await svc[CHEST.query]();
    var info = response && response[CHEST.quotaInfo];
    if (!info) throw new Error("读取宝箱次数失败");
    var total = Number(info[CHEST.quotaTotal]) || 0;
    var used = Number(info[CHEST.quotaUsed]) || 0;
    var remaining = Math.max(0, total - used);
    state.text = "宝箱：剩余 " + remaining + "/" + total;
    if (remaining <= 0) return;
    var drops = chestDrops();
    if (!drops.length) {
      state.text = "当前地图没有可开的 BOSS 掉落宝箱";
      return;
    }
    var done = 0;
    for (var i = 0; i < drops.length; i += 1) {
      if (state.stop) throw new Error("STOP");
      if (limit && done >= limit) break;
      if (remaining <= 0) break;
      var drop = drops[i];
      try {
        drop.unit[CHEST.interact]();
      } catch (e) {
        continue;
      }
      var view = null;
      for (var t = 0; t < 16; t += 1) {
        await sleep(250);
        view = chestView(drop);
        if (view) break;
      }
      if (!view) continue;
      try {
        var useDouble = double && view[CHEST.doubleBtn] && view[CHEST.doubleBtn].node
          && view[CHEST.doubleBtn].node.active === true;
        var claim = useDouble && typeof view[CHEST.double] === "function"
          ? view[CHEST.double]() : view[CHEST.normal]();
        await claim;
      } catch (e) {
        continue;
      }
      done += 1;
      remaining -= 1;
      state.text = "开箱成功 " + done + " 个（剩余 " + remaining + "/" + total + "）";
      await sleep(800);
    }
  }

  async function runOne(step) {
    var n = step.name;
    if (n === "等待" || n === "延时" || n === "wait") return cmdWait(step.args);
    if (n === "停止" || n === "结束" || n === "stop") throw new Error("STOP");
    if (n === "点击" || n === "单击" || n === "click") return cmdClick(step.args);
    if (n === "滚轮" || n === "wheel") return cmdWheel(step.args);
    if (n === "移动" || n === "move") return cmdMove(step.args);
    if (n === "回城" || n === "home") return cmdHome();
    if (n === "传送" || n === "teleport") return cmdTeleport(step.args);
    if (n === "跳一跳" || n === "自动跳" || n === "jumpjump") return cmdAutoJump(step.args);
    if (n === "采集" || n === "collect") return cmdCollect(step.args);
    if (n === "开宝箱" || n === "chest") return cmdChest(step.args);
    if (n === "跳转" || n === "jump") {
      var no = Number(step.args && step.args[0]);
      if (!isFinite(no) || no < 1) throw new Error("跳转的槽位编号必须是数字");
      var jump = new Error("JUMP");
      jump.__jump = Math.round(no);
      throw jump;
    }
    throw new Error("不认识的指令：" + n);
  }

  function snapshot() {
    return {
      running: state.running, step: state.step, total: state.total,
      text: state.text, error: state.error, name: state.name, slot: state.slot
    };
  }

  function stop() {
    state.stop = true;
    state.running = false;
    try { evalJs("(function(){try{window.__zzCmd.up()}catch(e){}})()"); } catch (e) { /* ignore */ }
  }

  function start(payload) {
    if (state.running) return { ok: false, message: "上一条还在跑" };
    var parsed;
    try { parsed = parseSlots(payload); } catch (e) {
      return { ok: false, message: (e && e.message) || String(e) };
    }
    var slots = parsed.slots;
    var slot = parsed.start;
    if (!slots[slot]) return { ok: false, message: "槽位 " + slot + " 是空的" };

    state.running = true;
    state.stop = false;
    state.error = "";
    state.slot = slot;
    state.step = 0;
    state.total = slots[slot].length;
    state.text = "";
    state.collectWatchError = "";
    try { installCollectWatch(); window.__zzCollectConsumed = Date.now(); } catch (e) {}
    state.done = (async function () {
      try {
        var steps = slots[slot];
        var k = 0;
        var jumps = 0;
        while (true) {
          if (state.stop) throw new Error("STOP");
          if (k >= steps.length) break;
          state.step = k + 1;
          state.total = steps.length;
          state.text = steps[k].raw;
          ensureCollectWatch();
          try {
            await runOne(steps[k]);
          } catch (e) {
            if (e && e.__jump) {
              jumps += 1;
              if (jumps > 1000) throw new Error("跳转次数超过 1000 次，可能有死循环");
              var target = Number(e.__jump);
              if (!slots[target]) throw new Error("槽位 " + target + " 是空的");
              slot = target;
              steps = slots[slot];
              k = 0;
              state.slot = slot;
              state.total = steps.length;
              state.step = 0;
              state.text = "跳转 → 槽位 " + slot;
              continue;
            }
            throw e;
          }
          k += 1;
        }
        state.running = false;
        return { ok: true, message: "指令已跑完" };
      } catch (e) {
        state.running = false;
        var msg = (e && e.message) || String(e);
        if (msg === "STOP") return { ok: false, message: "已停止" };
        state.error = msg;
        return { ok: false, message: msg };
      }
    })();
    state.done.catch(function () {});
    return { ok: true, message: "已开始" };
  }

  function run(content) {
    var result = start(content);
    if (!result.ok) return Promise.reject(new Error(result.message));
    return state.done;
  }

  var recordState = null;
  var recordLastT = 0;

  function recordPct(x, y) {
    return [
      Math.round(x / Math.max(1, window.innerWidth) * 10000) / 100,
      Math.round(y / Math.max(1, window.innerHeight) * 10000) / 100
    ];
  }

  function recordToLines(events) {
    var lines = [];
    (events || []).forEach(function (ev) {
      if (!ev || typeof ev !== "object") return;
      var t = Number(ev.t) || 0;
      if (recordLastT && t > recordLastT) {
        var delay = (t - recordLastT) / 1000;
        if (delay >= 0.05) lines.push("等待(" + delay.toFixed(2) + ")");
      }
      if (t) recordLastT = t;
      if (ev.kind === "click") {
        lines.push("点击(" + Number(ev.x || 0).toFixed(2) + "," + Number(ev.y || 0).toFixed(2) + ")");
      } else if (ev.kind === "move") {
        lines.push("移动(" + Number(ev.angle || 0).toFixed(1) + "," + Number(ev.seconds || 0).toFixed(2) + ")");
      }
    });
    return lines;
  }

  function recordStart() {
    if (recordState) return { ok: true };
    var rec = { events: [], down: null };
    recordState = rec;
    recordLastT = 0;
    function push(ev) { ev.t = Date.now(); rec.events.push(ev); }
    function onDown(e) {
      if (e.isTrusted === false) return;
      if (e.button !== undefined && e.button !== 0) return;
      var p = recordPct(e.clientX, e.clientY);
      rec.down = { x: p[0], y: p[1], angle: null, segT: Date.now(), moved: false };
    }
    function onMove(e) {
      var d = rec.down;
      if (!d) return;
      var p = recordPct(e.clientX, e.clientY);
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
        push({ kind: "move", angle: d.angle, seconds: Math.round(duration * 100) / 100 });
        d.angle = angle;
        d.segT = Date.now();
      }
    }
    function onUp(e) {
      var d = rec.down;
      rec.down = null;
      if (!d) return;
      var p = recordPct(e.clientX, e.clientY);
      if (d.moved && d.angle !== null) {
        var duration = (Date.now() - d.segT) / 1000;
        if (duration >= 0.1) {
          push({ kind: "move", angle: d.angle, seconds: Math.round(duration * 100) / 100 });
          return;
        }
      }
      push({ kind: "click", x: p[0], y: p[1] });
    }
    rec.listeners = [
      ["pointerdown", onDown],
      ["pointermove", onMove],
      ["pointerup", onUp],
      ["pointercancel", onUp]
    ];
    rec.listeners.forEach(function (pair) { document.addEventListener(pair[0], pair[1], true); });
    rec.remove = function () {
      rec.listeners.forEach(function (pair) { document.removeEventListener(pair[0], pair[1], true); });
      recordState = null;
    };
    return { ok: true };
  }

  function recordPoll() {
    if (!recordState) return { lines: [] };
    return { lines: recordToLines(recordState.events.splice(0)) };
  }

  function recordStop() {
    if (!recordState) return { lines: [] };
    var rec = recordState;
    var events = rec.events.splice(0);
    rec.remove();
    return { lines: recordToLines(events) };
  }

  window.__zzRun = {
    v: 4, MAPPING: window.__zzRunMapping || { builds: {} },
    MOVE: window.__zzRunMove || {}, snapshot: snapshot,
    start: start, run: run, stop: stop,
    recordStart: recordStart, recordPoll: recordPoll, recordStop: recordStop
  };
})();
