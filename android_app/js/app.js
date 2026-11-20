/* 紫作助手 · 安卓版（HBuilder 5+ App）
 * 首页 → 打开游戏 / 功能页；游戏在独立 webview，本页通过 evalJS（+ plus.storage 兜底）通信。
 */
(function () {
  var GAME_URL = "https://mprogram.boomegg.cn/box/game/wx77200645d1c7f35f/h5?appid=wx77200645d1c7f35f&game_platform=h5";
  var POLL_MS = 800;

  var game = null;
  var injected = false;
  var injecting = false;
  var state = null;
  var emptyPolls = 0;
  var pollTimer = null;
  var pickTimer = null;
  var picked = null;
  var lastConfirm = 0;
  var build = "";
  var injectTries = 0;

  function $(id) { return document.getElementById(id); }

  function showPage(id) {
    ["pageHome", "pageFunc", "list"].forEach(function (p) {
      var el = $(p);
      if (el) el.classList.toggle("active", p === id);
    });
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

  function setStatus(text, kind) {
    var el = $("status");
    if (!el) return;
    el.className = "status" + (kind ? " " + kind : "");
    el.textContent = text;
  }

  function setInfo(text) {
    var el = $("info");
    if (el) el.textContent = text;
  }

  // ---------- 打开 / 关闭游戏 ----------
  function openGame() {
    if (!game) {
      game = plus.webview.create(GAME_URL, "zz_game", {
        top: "0px", bottom: "0px", left: "0px", right: "0px",
        bounce: "none", scalable: false
      });
      game.addEventListener("loaded", function () {
        setTimeout(inject, 600);
      }, false);
      setTimeout(inject, 2000);
    }
    game.show();
    $("homeState").textContent = "已打开";
    setStatus("正在连接游戏…");
  }

  function backToApp() {
    if (game) game.hide();
  }

  // ---------- 注入 + 轮询 ----------
  function detectBuild() {
    return unquote(evalGame(
      "(()=>{try{for(const r of performance.getEntriesByType('resource')){" +
      "const m=/mainScripts\\/index\\.([0-9a-z]+)\\.js/i.exec(r.name);if(m)return m[1];}}catch(e){}" +
      "return (window.__zzAssist&&window.__zzAssist.build)||'';})()"
    ));
  }

  function translateRuntime(js, b) {
    var table = (window.ZZ_RENAMES && window.ZZ_RENAMES[b]) || {};
    Object.keys(table).sort(function (a, c) { return c.length - a.length; }).forEach(function (oldName) {
      var newName = table[oldName];
      if (!newName || newName === oldName) return;
      js = js.replace(new RegExp("\\b" + oldName + "\\b", "g"), newName);
    });
    return js;
  }

  function inject() {
    if (!game || injected || injecting) return;
    injecting = true;
    var b = detectBuild();
    if (!b) {
      // evalJS 拿不到返回值时，用 plus.storage 让游戏页自己把版本号写回来
      evalGame(
        "try{var m=(performance.getEntriesByType('resource').map(function(r){return r.name}).join('|'))" +
        ".match(/mainScripts\\/index\\.([0-9a-z]+)\\.js/);" +
        "plus.storage.setItem('zz_build',m?m[1]:'')}catch(e){}"
      );
      setTimeout(function () {
        var sb = "";
        try { sb = plus.storage.getItem("zz_build") || ""; } catch (e) { sb = ""; }
        injecting = false;
        if (sb) {
          doInject(sb);
          return;
        }
        injectTries += 1;
        if (injectTries >= 3) {
          // 实在读不到版本号：直接用映射里最新的版本先注入，
          // 运行时自己还会再检测一次真实版本。
          var keys = Object.keys((window.ZZ_MAPPING || {}).builds || {});
          if (keys.length) {
            setStatus("读不到游戏版本，先按最新版本注入（" + keys[keys.length - 1] + "）…");
            doInject(keys[keys.length - 1]);
            return;
          }
        }
        setStatus("等待游戏加载（进入登录/主界面后自动连接）… 第 " + injectTries + " 次");
        setTimeout(inject, 1800);
      }, 500);
      return;
    }
    injecting = false;
    doInject(b);
  }

  function doInject(b) {
    if (injected) return;
    build = b;
    var js = translateRuntime(window.ZZ_RUNTIME || "", b);
    var mappingJson = JSON.stringify(window.ZZ_MAPPING || { builds: {} });
    var code =
      "try{window.__zzAssist&&(clearInterval(window.__zzAssist.__timer),clearInterval(window.__zzAssist.__redTimer));}catch(e){}" +
      "delete window.__zzAssist;" +
      "window.__zzAssistMapping=" + mappingJson + ";" +
      "window.__zzAssistStamp='android-app';" +
      js;
    evalGame(code);
    // 兜底通道：让游戏页把状态写进 plus.storage（如果 plus 在远程页可用）
    evalGame(
      "(function(){try{if(window.__zzBridge)return;window.__zzBridge=1;" +
      "setInterval(function(){try{window.plus&&plus.storage.setItem('zz_state',JSON.stringify(window.__zzAssist.snapshot()));}catch(e){}" +
      "try{if(window.plus){var c=plus.storage.getItem('zz_cmd');if(c){var o=JSON.parse(c);" +
      "if(o&&o.at>(window.__zzCmdAt||0)){window.__zzCmdAt=o.at;window.__zzAssist.set(o.feature,o.value);}}}}catch(e){}" +
      "},500);}catch(e){}})()"
    );
    injected = true;
    setStatus("已注入 · " + b + "，等待数据…", "ok");
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(poll, POLL_MS);
    setTimeout(poll, 500);
  }

  function poll() {
    if (!game || !injected) return;
    var raw = unquote(evalGame(
      "JSON.stringify(window.__zzAssist&&window.__zzAssist.snapshot?window.__zzAssist.snapshot():null)"
    ));
    if (!raw || raw === "null") {
      // 兜底：读 plus.storage 里游戏页写的数据
      try { raw = (plus.storage.getItem("zz_state") || ""); } catch (e) { raw = ""; }
      if (!raw || raw === "null") {
        emptyPolls += 1;
        if (emptyPolls === 5) setStatus("读不到游戏状态（evalJS 没返回，页面可能还没进主界面）", "bad");
        return;
      }
    }
    emptyPolls = 0;
    try { state = JSON.parse(raw); } catch (e) { return; }
    render();
  }

  function render() {
    if (!state) return;
    var ok = !!state.ready;
    setStatus(
      ok
        ? "已连接 · " + (state.build || build) + " · 红包 " + ((state.redPacket && state.redPacket.today) || 0) + " 个"
        : "未就绪：" + (state.error || "等待进入游戏主界面"),
      ok ? "ok" : "bad"
    );

    var red = state.redPacket || {};
    var rv = state.revive || {};
    var mob = state.mob || {};
    var boss = state.boss || {};

    setOn("fRed", red.enabled, "红包");
    setOn("fRevive", rv.enabled, "复活");
    setOn("fMob", mob.enabled, "小怪刷新");
    setOn("fBoss", boss.enabled, "BOSS刷新");

    var bits = [];
    if (red.reason) bits.push("红包：" + red.reason);
    if (rv.reason) bits.push("复活：" + rv.reason);
    if (picked) bits.push("坐标：(" + picked[0] + ", " + picked[1] + ")");
    if (bits.length) setInfo(bits.join("　"));

    // 全灭确认框自动点（直接调面板的确定）
    var conf = rv.confirmAt;
    if (conf && conf.x && Date.now() - lastConfirm > 1500) {
      lastConfirm = Date.now();
      evalGame("(()=>{try{const cc=window.cc;if(!cc||!cc.director)return '0';" +
        "const n=cc.find('Root/UIScene/UICanvas/Popup/ConfirmPopup', cc.director.getScene());if(!n)return '0';" +
        "for(const c of (n._components||[])){let cls='';try{cls=cc.js.getClassName(c)}catch(e){}" +
        "if(cls==='IHCJ'){try{c.SOET();return '1'}catch(e){return '0'}}}return '0'}catch(e){return '0'}})()");
    }
  }

  function setOn(id, on, label) {
    var el = $(id);
    if (!el) return;
    el.classList.toggle("on", !!on);
    el.textContent = label + (on ? " · 开" : "");
  }

  // ---------- 指令 ----------
  function send(feature, value) {
    var code = "JSON.stringify(window.__zzAssist&&window.__zzAssist.set?window.__zzAssist.set(" +
      JSON.stringify(feature) + "," + JSON.stringify(value) + "):{ok:false,message:'运行时未就绪'})";
    var raw = unquote(evalGame(code));
    try {
      plus.storage.setItem("zz_cmd", JSON.stringify({ feature: feature, value: value, at: Date.now() }));
    } catch (e) { /* ignore */ }
    var msg = "";
    try { msg = (JSON.parse(raw) || {}).message || ""; } catch (e) { msg = ""; }
    if (msg) setInfo(msg);
    return msg;
  }

  function toggle(feature) {
    var cur = state && state[feature] ? !!state[feature].enabled : false;
    send(feature, cur ? 0 : (feature === "boss" ? 3 : 1));
  }

  function startPick() {
    if (!game) { setInfo("先点「打开游戏」"); return; }
    setInfo("到游戏画面上点一下要记录的位置…");
    send("pickCoord", 1);
    if (pickTimer) clearInterval(pickTimer);
    var began = Date.now();
    pickTimer = setInterval(function () {
      var raw = unquote(evalGame("JSON.stringify(window.__zzPick)"));
      var p = null;
      try { p = JSON.parse(raw); } catch (e) { p = null; }
      if (p && p.at && p.at >= began) {
        clearInterval(pickTimer);
        pickTimer = null;
        if (p.canceled) { setInfo("已取消"); return; }
        picked = [p.x, p.y];
        var text = "坐标 (" + p.x + ", " + p.y + ")";
        setInfo(text + "  已复制");
        copyText(String(p.x) + "," + String(p.y));
        return;
      }
      if (Date.now() - began > 60000) { clearInterval(pickTimer); pickTimer = null; }
    }, 500);
  }

  function flyToPicked() {
    if (!picked) { setInfo("先点「取坐标」，再到画面上点一下"); return; }
    send("blinkTo", { x: picked[0], y: picked[1], mode: "instant" });
    setInfo("正在瞬移到 (" + picked[0] + ", " + picked[1] + ")…");
  }

  function copyText(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text);
        return;
      }
    } catch (e) { /* ignore */ }
    try {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    } catch (e) { /* ignore */ }
  }

  // ---------- 怪物列表 ----------
  function openMonsters() {
    var raw = unquote(evalGame("JSON.stringify(window.__zzAssist&&window.__zzAssist.monsters?window.__zzAssist.monsters():[])"));
    var items = [];
    try { items = JSON.parse(raw) || []; } catch (e) { items = []; }
    var box = $("listItems");
    box.innerHTML = "";
    if (!items.length) {
      box.innerHTML = '<div class="info">附近没读到怪（先进入地图再点刷新）</div>';
    }
    items.slice(0, 120).forEach(function (m) {
      var row = document.createElement("div");
      row.className = "item" + (m.boss ? " boss" : "");
      var left = document.createElement("div");
      var hp = (m.hp !== null && m.maxHp) ? " · 血 " + fmtNum(m.hp) + "/" + fmtNum(m.maxHp) : "";
      left.innerHTML = '<div class="name">' + (m.boss ? "★ " : "") + escapeHtml(m.name || "怪") + "</div>" +
        '<div class="meta">' + Math.round(m.d || 0) + " 米" + hp + "</div>";
      var go = document.createElement("button");
      go.className = "go";
      go.textContent = "过去";
      go.onclick = function () {
        send("blinkTo", { x: m.x, y: m.y, mode: "instant" });
        backToApp();
        showPage("pageFunc");
      };
      row.appendChild(left);
      row.appendChild(go);
      box.appendChild(row);
    });
    backToApp();
    showPage("list");
  }

  function fmtNum(n) {
    n = Number(n) || 0;
    if (n >= 100000000) return (n / 100000000).toFixed(1) + "亿";
    if (n >= 10000) return (n / 10000).toFixed(1) + "万";
    return String(n);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // ---------- 绑定 ----------
  function bind() {
    $("btnOpen").onclick = function () { openGame(); };
    $("btnFunc").onclick = function () { backToApp(); showPage("pageFunc"); };
    $("btnBackHome").onclick = function () { showPage("pageHome"); };
    $("fRed").onclick = function () { toggle("redPacket"); };
    $("fRevive").onclick = function () { toggle("revive"); };
    $("fMob").onclick = function () { toggle("mob"); };
    $("fBoss").onclick = function () { toggle("boss"); };
    $("fMonsters").onclick = openMonsters;
    $("fClear").onclick = function () { send("clearStuck", 1); };
    $("btnListBack").onclick = function () { showPage("pageFunc"); };
    $("btnListRefresh").onclick = openMonsters;

    plus.key.addEventListener("backbutton", function () {
      if ($("list").classList.contains("active")) { showPage("pageFunc"); return; }
      if (game && game.isVisible && game.isVisible()) { backToApp(); showPage("pageFunc"); return; }
      if ($("pageFunc").classList.contains("active")) { showPage("pageHome"); return; }
      plus.runtime.quit();
    });
  }

  function plusready() {
    try { plus.screen.lockOrientation("portrait-primary"); } catch (e) { /* ignore */ }
    bind();
    showPage("pageHome");
  }

  if (window.plus) plusready();
  else document.addEventListener("plusready", plusready, false);
})();
