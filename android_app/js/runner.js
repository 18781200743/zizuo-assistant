(function(){try{
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
}catch(e){}})();

(function () {
  if (window.__zzRun && window.__zzRun.v === 1) return;
  var DIR = {
    "上": [0, -1], "下": [0, 1], "左": [-1, 0], "右": [1, 0],
    "左上": [-0.7071, -0.7071], "右上": [0.7071, -0.7071],
    "左下": [-0.7071, 0.7071], "右下": [0.7071, 0.7071]
  };
  var state = { running: false, stop: false, step: 0, total: 0, text: "", error: "", name: "" };

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

  function parseLine(raw) {
    var line = String(raw || "").trim();
    if (!line || line.charAt(0) === "#") return null;
    var m = /^([^()（）\s]+)\s*[（(](.*)[)）]\s*$/.exec(line);
    if (!m) return null;
    var args = m[2].split(/[,，]/).map(function (s) { return s.trim(); });
    return { name: m[1], args: args, raw: line };
  }

  function num(args, i, label) {
    if (i >= args.length || !String(args[i]).trim()) throw new Error(label + " 缺少参数");
    var v = Number(args[i]);
    if (!isFinite(v)) throw new Error(label + " 不是数字：" + args[i]);
    return v;
  }

  // ---------- 各指令 ----------
  function cmdWait(args) { return sleep(num(args, 0, "等待秒数") * 1000); }

  function cmdClick(args) {
    var x = num(args, 0, "X"), y = num(args, 1, "Y");
    var res = evalJs("(function(){try{var s=window.__zzCmd&&window.__zzCmd.size&&window.__zzCmd.size();" +
      "var w=s?s.w:innerWidth,h=s?s.h:innerHeight;" +
      "return String(window.__zzCmd&&window.__zzCmd.click?window.__zzCmd.click(" + x + "," + y + "):'no')}catch(e){return 'ERR '+e.message}})()");
    if (res && String(res).indexOf("ERR") === 0) throw new Error(String(res));
    return sleep(120);
  }

  function cmdWheel(args) {
    var dir = String(args[0] || "下");
    var amount = num(args, 1, "滚动量");
    var dy = (dir === "上" ? -1 : 1) * amount * 100;
    evalJs("(function(){try{var s=window.__zzCmd.size();" +
      "window.__zzCmd.wheel(0," + dy + ",s.w/2,s.h/2)}catch(e){}})()");
    return sleep(120);
  }

  function cmdMove(args) {
    var dir = String(args[0] || "").trim();
    if (!DIR[dir]) throw new Error("方向要写：上/下/左/右/左上/右上/左下/右下");
    var secs = args.length > 1 ? num(args, 1, "移动秒数") : 1.0;
    var d = DIR[dir];
    var cfg = window.__zzRun.MOVE || {};
    var cx = cfg.cx === undefined ? 50 : cfg.cx;
    var cy = cfg.cy === undefined ? 78 : cfg.cy;
    var r = cfg.r === undefined ? 8 : cfg.r;
    var code = "(function(){try{var s=window.__zzCmd.size();var cx=s.w*" + cx + "/100,cy=s.h*" + cy + "/100;" +
      "var rr=Math.min(s.w,s.h)*" + r + "/100;window.__zzCmd.down(cx+rr*" + d[0] + ",cy+rr*" + d[1] + ");return 'ok'}catch(e){return 'ERR '+e.message}})()";
    var res = evalJs(code);
    if (String(res).indexOf("ERR") === 0) throw new Error(String(res));
    return sleep(secs * 1000).then(function () {
      evalJs("(function(){try{window.__zzCmd.up()}catch(e){}})()");
    });
  }

  function cmdHome() {
    var cfg = mapCfg();
    var b = cfg.b || {};
    var rev = b.revive || {};
    var ct = rev.campTeleport || {};
    var names = ct.campCoords || ["TCLA", "CLAT"];
    var holder = cfg.mc.holder || "VKXX";
    var core = cfg.mc.core || "ZRRU";
    var code = "(function(){try{var world=window['" + holder + "'];var core=world&&world['" + core + "'];" +
      "if(!world||!core)return 'ERR 游戏还没进地图';var spot=null;" +
      "var names=" + JSON.stringify(names) + ";" +
      "for(var i=0;i<names.length;i++){try{var fn=core[names[i]];if(typeof fn==='function'){var p=fn.call(core);" +
      "if(p&&isFinite(p.x)&&isFinite(p.y)){spot=p;break;}}}catch(e){}}" +
      "if(!spot)return 'ERR 读不到营地坐标';" +
      "if(typeof world['" + (cfg.mc.teleport || "LUSS") + "']!=='function')return 'ERR 游戏没有传送入口';" +
      "world['" + (cfg.mc.teleport || "LUSS") + "'](spot.x,spot.y,true,function(){});return 'ok'}catch(e){return 'ERR '+e.message}})()";
    var res = evalJs(code);
    if (String(res).indexOf("ERR") === 0) throw new Error(String(res));
    return sleep(800);
  }

  function cmdTeleport(args) {
    if (args.length < 2) throw new Error("传送 需要「章节,地图」两个参数");
    var chapter = Math.round(num(args, 0, "章节"));
    var section = Math.round(num(args, 1, "地图"));
    var continent = args.length > 2 && String(args[2]).trim() !== "" ? Math.round(num(args, 2, "大陆")) : null;
    var table = window.ZZ_TELEPORT || {};
    var prefix = chapter + "." + section + ".";
    var keys = Object.keys(table).filter(function (k) { return k.indexOf(prefix) === 0; }).sort();
    if (!keys.length) throw new Error("地图 " + chapter + "-" + section + " 还没收录传送点");
    var pick = null;
    if (continent !== null) {
      var exact = prefix + continent;
      pick = table[exact] || table[keys[0]];
    } else {
      pick = table[keys[0]];
    }
    if (!pick) throw new Error("地图 " + chapter + "-" + section + " 传送点数据缺失");
    var cfg = mapCfg();
    var world = (cfg.mc.holder || "VKXX");
    var api = (cfg.mc.teleport || "LUSS");
    var code = "(function(){try{var w=window['" + world + "'];if(!w)return 'ERR 游戏还没进地图';" +
      "var f=w['" + api + "'];if(typeof f!=='function')return 'ERR 游戏没有传送入口';" +
      "f.call(w," + Number(pick.map_id) + "," + Number(pick.teleport_id || 0) +
      ",function(){});return 'ok'}catch(e){return 'ERR '+e.message}})()";
    var res = evalJs(code);
    if (String(res).indexOf("ERR") === 0) throw new Error(String(res));
    return sleep(900);
  }

  function cmdCollect(args) {
    return Promise.resolve().then(function () {
      throw new Error("采集 需要电脑版（安卓暂不支持自动采集）");
    });
  }

  function cmdChest() {
    return Promise.resolve().then(function () {
      throw new Error("开宝箱 需要电脑版（安卓暂不支持）");
    });
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
    if (n === "采集" || n === "collect") return cmdCollect(step.args);
    if (n === "开宝箱" || n === "chest") return cmdChest();
    throw new Error("不认识的指令：" + n);
  }

  function snapshot() {
    return {
      running: state.running, step: state.step, total: state.total,
      text: state.text, error: state.error, name: state.name
    };
  }

  function stop() {
    state.stop = true;
    state.running = false;
    try { evalJs("(function(){try{window.__zzCmd.up()}catch(e){}})()"); } catch (e) { /* ignore */ }
  }

  async function run(content) {
    if (state.running) throw new Error("上一条还在跑");
    var lines = String(content || "").split("\n");
    var steps = [];
    for (var i = 0; i < lines.length; i++) {
      var st = parseLine(lines[i]);
      if (st) steps.push(st);
    }
    if (!steps.length) throw new Error("这条指令是空的");
    state.running = true;
    state.stop = false;
    state.error = "";
    state.total = steps.length;
    state.step = 0;
    try {
      for (var k = 0; k < steps.length; k++) {
        if (state.stop) throw new Error("STOP");
        state.step = k + 1;
        state.text = steps[k].raw;
        await runOne(steps[k]);
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
  }

  window.__zzRun = {
    v: 1, MAPPING: window.__zzRunMapping || { builds: {} },
    MOVE: window.__zzRunMove || {}, snapshot: snapshot, run: run, stop: stop
  };
})();
