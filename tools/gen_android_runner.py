"""生成安卓用的运行指令：runner.js（指令执行器）+ instructions.js（内置指令）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.instruction_library import load_library  # noqa: E402
from app.instruction_runner import JS_INSTALL, load_automation_config  # noqa: E402

OUT = ROOT / "android_app" / "js"

RUNNER_JS = r"""
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
    var cfg = mapCfg();
    var world = (cfg.mc.holder || "VKXX");
    var api = (cfg.mc.teleport || "LUSS");
    var code = "(function(){try{var w=window['" + world + "'];if(!w)return 'ERR 游戏还没进地图';" +
      "var f=w['" + api + "'];if(typeof f!=='function')return 'ERR 游戏没有传送入口';" +
      "f.call(w," + chapter + "," + section + ",function(){});return 'ok'}catch(e){return 'ERR '+e.message}})()";
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
"""


def main() -> None:
    library = load_library()
    cfg = load_automation_config()
    items = [
        {
            "title": str(it.get("title") or ""),
            "category": int(it.get("category_id") or 0),
            "content": str(it.get("content") or ""),
        }
        for it in (library.get("instructions") or [])
    ]
    (OUT / "instructions.js").write_text(
        "window.ZZ_INSTRUCTIONS = " + json.dumps(items, ensure_ascii=False) + ";\n",
        encoding="utf-8", newline="\n",
    )
    (OUT / "runner.js").write_text(
        "(function(){try{" + JS_INSTALL + "}catch(e){}})();\n" + RUNNER_JS,
        encoding="utf-8", newline="\n",
    )
    move = {
        "cx": (cfg.get("movement_center") or {}).get("x", 50.0),
        "cy": (cfg.get("movement_center") or {}).get("y", 78.0),
        "r": cfg.get("movement_radius") or 8.0,
    }
    print("instructions.js:", len(items), "条")
    print("runner.js ok, move:", move)


if __name__ == "__main__":
    main()
