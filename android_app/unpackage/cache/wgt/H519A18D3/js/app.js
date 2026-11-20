/* 紫作助手 · 安卓版（HBuilder 5+ App）
 * 游戏跑在一个独立 webview 里，本页通过 evalJS 注入运行时并轮询状态。
 */
(function () {
  var GAME_URL = "https://mprogram.boomegg.cn/box/game/wx77200645d1c7f35f/h5?appid=wx77200645d1c7f35f&game_platform=h5";
  var POLL_MS = 800;

  var game = null;
  var injected = false;
  var state = null;
  var emptyPolls = 0;
  var manualTimer = null;
  var lastConfirmAt = 0;

  function $(id) { return document.getElementById(id); }

  function panelHeight() {
    var bar = $("toolbar");
    return bar ? Math.max(96, bar.offsetHeight) : 108;
  }

  function layout() {
    if (!game) return;
    var top = panelHeight() + "px";
    try { game.setStyle({ top: top, bottom: "0px", left: "0px", right: "0px" }); } catch (e) {}
  }

  function evalGame(code) {
    if (!game) return "";
    try {
      var r = game.evalJS(code);
      return r === null || r === undefined ? "" : String(r);
    } catch (e) {
      return "";
    }
  }

  function unquote(s) {
    if (!s) return "";
    s = String(s).trim();
    if (s.length >= 2 && s.charAt(0) === '"' && s.charAt(s.length - 1) === '"') {
      try { return JSON.parse(s); } catch (e) { return s.slice(1, -1); }
    }
    return s;
  }

  function detectBuild() {
    return unquote(evalGame(
      "(()=>{try{for(const r of performance.getEntriesByType('resource')){" +
      "const m=/mainScripts\\/index\\.([0-9a-z]+)\\.js/i.exec(r.name);if(m)return m[1];}}catch(e){}" +
      "return (window.__zzAssist&&window.__zzAssist.build)||'';})()"
    ));
  }

  function translateRuntime(js, build) {
    var table = (window.ZZ_RENAMES && window.ZZ_RENAMES[build]) || {};
    var keys = Object.keys(table).sort(function (a, b) { return b.length - a.length; });
    keys.forEach(function (oldName) {
      var newName = table[oldName];
      if (!newName || newName === oldName) return;
      js = js.replace(new RegExp("\\b" + oldName + "\\b", "g"), newName);
    });
    return js;
  }

  function inject() {
    var build = detectBuild();
    if (!build) {
      $("gameTip").style.display = "flex";
      $("gameTip").textContent = "等待游戏加载（进入登录/主界面后会自动连接）…";
      setTimeout(inject, 1200);
      return;
    }
    var js = translateRuntime(window.ZZ_RUNTIME || "", build);
    var mappingJson = JSON.stringify(window.ZZ_MAPPING || { builds: {} });
    var code =
      "try{window.__zzAssist&&(clearInterval(window.__zzAssist.__timer),clearInterval(window.__zzAssist.__redTimer));}catch(e){}" +
      "delete window.__zzAssist;" +
      "window.__zzAssistMapping=" + mappingJson + ";" +
      "window.__zzAssistStamp='android-app';" +
      js;
    evalGame(code);
    injected = true;
    $("gameTip").style.display = "none";
    setTimeout(function () { poll(); }, 600);
    setInterval(poll, POLL_MS);
  }

  function setFeature(feature, value, cb) {
    var code = "JSON.stringify(window.__zzAssist&&window.__zzAssist.set?window.__zzAssist.set(" +
      JSON.stringify(feature) + "," + JSON.stringify(value) + "):{ok:false,message:'运行时未就绪'})";
    var raw = evalGame(code);
    if (cb) cb(raw);
    return raw;
  }

  function clickConfirmIfNeeded() {
    var code = "(()=>{try{const cc=window.cc;if(!cc||!cc.director)return '0';" +
      "const n=cc.find('Root/UIScene/UICanvas/Popup/ConfirmPopup', cc.director.getScene());if(!n)return '0';" +
      "for(const c of (n._components||[])){let cls='';try{cls=cc.js.getClassName(c)}catch(e){}" +
      "if(cls==='IHCJ'){try{c.SOET();return '1'}catch(e){return '0'}}}return '0'}catch(e){return '0'}})()";
    evalGame(code);
  }

  function poll() {
    if (!injected) return;
    var raw = evalGame("JSON.stringify(window.__zzAssist&&window.__zzAssist.snapshot?window.__zzAssist.snapshot():null)");
    raw = unquote(raw);
    if (!raw || raw === "null") {
      emptyPolls += 1;
      if (emptyPolls >= 4) {
        $("status").className = "status bad";
        $("status").textContent = "读不到游戏状态（页面可能还在加载 / evalJS 异常）";
      }
      return;
    }
    emptyPolls = 0;
    try { state = JSON.parse(raw); } catch (e) { return; }
    render();
  }

  function render() {
    if (!state) return;
    var ok = !!state.ready;
    var st = $("status");
    st.className = "status " + (ok ? "ok" : "bad");
    st.textContent = ok
      ? "已连接 · " + (state.build || "?") + " · 红包 " + ((state.redPacket && state.redPacket.today) || 0) + " 个"
      : "未就绪：" + (state.error || "等待进入游戏");

    var red = state.redPacket || {};
    var rv = state.revive || {};
    var mob = state.mob || {};
    var boss = state.boss || {};

    setBtn("btnRed", red.enabled, "红包");
    setBtn("btnRevive", rv.enabled, "复活");
    setBtn("btnMob", mob.enabled, "小怪刷新");
    setBtn("btnBoss", boss.enabled, "BOSS刷新");

    var bits = [];
    if (red.reason) bits.push("红包：" + red.reason);
    if (rv.reason) bits.push("复活：" + rv.reason);
    if (!bits.length) bits.push("粉色按钮=已开启；长按「复活」= 立即复活一次");
    $("info").textContent = bits.join("　");

    // 全灭确认框：自动点确认
    var conf = rv.confirmAt;
    if (conf && conf.x && Date.now() - lastConfirmAt > 1500) {
      lastConfirmAt = Date.now();
      clickConfirmIfNeeded();
    }
  }

  function setBtn(id, on, label) {
    var b = $(id);
    if (!b) return;
    b.className = on ? "on" : "off";
    b.textContent = label + (on ? " · 开" : "");
  }

  function toggle(feature) {
    var cur = state && state[feature] ? state[feature].enabled : false;
    var seconds = feature === "boss" ? 3 : 1;
    setFeature(feature, cur ? 0 : seconds, function (raw) {
      var msg = "";
      try { msg = JSON.parse(unquote(raw)).message || ""; } catch (e) {}
      if (msg) $("info").textContent = msg;
    });
  }

  function fmtNum(n) {
    n = Number(n) || 0;
    if (n >= 100000000) return (n / 100000000).toFixed(1) + "亿";
    if (n >= 10000) return (n / 10000).toFixed(1) + "万";
    return String(n);
  }

  function openList() {
    var raw = unquote(evalGame("JSON.stringify(window.__zzAssist&&window.__zzAssist.monsters?window.__zzAssist.monsters():[])"));
    var items = [];
    try { items = JSON.parse(raw) || []; } catch (e) { items = []; }
    if (!items.length) {
      $("info").textContent = "附近没读到怪（先进入地图，点刷新）";
    }
    var box = $("listItems");
    box.innerHTML = "";
    items.slice(0, 120).forEach(function (m) {
      var row = document.createElement("div");
      row.className = "item" + (m.boss ? " boss" : "");
      var left = document.createElement("div");
      left.className = "name";
      left.textContent = (m.boss ? "★ " : "") + (m.name || "怪") +
        "（" + Math.round(m.d || 0) + "米）";
      var right = document.createElement("div");
      right.className = "meta";
      var hp = (m.hp !== null && m.maxHp) ? " 血 " + fmtNum(m.hp) + "/" + fmtNum(m.maxHp) : "";
      right.textContent = hp;
      var go = document.createElement("button");
      go.textContent = "过去";
      go.onclick = function () {
        setFeature("blinkTo", { x: m.x, y: m.y, mode: "instant" });
        closeList();
      };
      var side = document.createElement("div");
      side.appendChild(right);
      side.appendChild(go);
      row.appendChild(left);
      row.appendChild(side);
      box.appendChild(row);
    });
    try { game.hide(); } catch (e) {}
    $("list").style.display = "flex";
  }

  function closeList() {
    $("list").style.display = "none";
    try { game.show(); } catch (e) {}
  }

  function bind() {
    $("btnRed").onclick = function () { toggle("redPacket"); };
    $("btnMob").onclick = function () { toggle("mob"); };
    $("btnBoss").onclick = function () { toggle("boss"); };
    $("btnBlink").onclick = openList;
    $("btnListRefresh").onclick = openList;
    $("btnListClose").onclick = closeList;
    $("btnClear").onclick = function () {
      var raw = setFeature("clearStuck", 1);
      var msg = "";
      try { msg = JSON.parse(unquote(raw)).message || ""; } catch (e) { msg = "已尝试清除"; }
      $("info").textContent = msg;
    };

    var rvBtn = $("btnRevive");
    rvBtn.onclick = function () {
      if (manualTimer) { clearTimeout(manualTimer); manualTimer = null; return; }
      manualTimer = setTimeout(function () {
        manualTimer = null;
        toggle("revive");
      }, 320);
    };
    // 长按 = 立即复活一次
    var pressTimer = null;
    rvBtn.addEventListener("touchstart", function () {
      pressTimer = setTimeout(function () {
        pressTimer = null;
        if (manualTimer) { clearTimeout(manualTimer); manualTimer = null; }
        var raw = setFeature("reviveNow", 1);
        $("info").textContent = "已发送：立即复活";
      }, 650);
    }, false);
    rvBtn.addEventListener("touchend", function () {
      if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
    }, false);
    rvBtn.addEventListener("touchcancel", function () {
      if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
    }, false);

    plus.key.addEventListener("backbutton", function () {
      if ($("list").style.display === "flex") { closeList(); return; }
      plus.runtime.quit();
    });
  }

  function start() {
    bind();
    game = plus.webview.create(GAME_URL, "zz_game", {
      top: panelHeight() + "px",
      bottom: "0px",
      left: "0px",
      right: "0px",
      bounce: "none",
      scalable: false
    });
    game.show();
    game.addEventListener("loaded", function () {
      $("gameTip").style.display = "none";
      inject();
    }, false);
    window.addEventListener("resize", layout, false);
    setTimeout(layout, 300);
  }

  if (window.plus) {
    plusready();
  } else {
    document.addEventListener("plusready", plusready, false);
  }

  function plusready() {
    try { plus.screen.lockOrientation("portrait-primary"); } catch (e) {}
    start();
  }
})();
