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
  var floatView = null;
  var runnerSource = "";
  var runnerLoading = false;
  var runnerCallbacks = [];
  var revivePendingUntil = 0;
  var reviveFlashTimer = null;
  var guildTimer = null;
  var guildRows = [];
  var guildExportFile = null;
  var guildTeamsCache = {};
  var mazeTimer = null;
  var mazeLastStamp = "";
  var mazeLast = null;
  var autoChestTimer = null;
  var autoChestOn = false;
  var clickTpOn = false;
  var rpcSeq = 0;

  // 屏幕常亮：挂机时不让手机自动息屏/锁屏。
  // 需要 manifest.json 里声明 Android 唤醒锁权限（正式打包才生效）。
  function keepAwake(on) {
    try {
      if (window.plus && plus.device && plus.device.setWakelock) {
        plus.device.setWakelock(on !== false);
      }
    } catch (e) { /* 基座/没权限时忽略 */ }
    // 第二层保险：新版 WebView 的屏幕唤醒锁（不支持就忽略）
    try {
      if (on !== false) {
        if (navigator.wakeLock && !window.__zzScreenLock) {
          navigator.wakeLock.request("screen").then(function (lock) {
            window.__zzScreenLock = lock;
            lock.addEventListener("release", function () { window.__zzScreenLock = null; });
          }).catch(function () {});
        }
      } else if (window.__zzScreenLock) {
        window.__zzScreenLock.release();
        window.__zzScreenLock = null;
      }
    } catch (e) { /* ignore */ }
  }

  function $(id) { return document.getElementById(id); }

  // 复制文本：优先用页面剪贴板，失败再退回 5+ 的剪贴板接口
  function copyText(text) {
    try {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      ta.style.top = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      var ok = document.execCommand("copy");
      document.body.removeChild(ta);
      if (ok) return true;
    } catch (e) { /* ignore */ }
    try {
      if (window.plus && plus.navigator && plus.navigator.setClipboard) {
        plus.navigator.setClipboard(text);
        return true;
      }
    } catch (e) { /* ignore */ }
    return false;
  }

  function showPage(id) {
    ["pageHome", "pageFunc", "list"].forEach(function (p) {
      var el = $(p);
      if (el) el.classList.toggle("active", p === id);
    });
    if (window.ZZLicense && window.ZZLicense.refreshHome) {
      try { window.ZZLicense.refreshHome(); } catch (e) { /* ignore */ }
    }
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

  function needLicense(name, next) {
    if (window.ZZLicense && window.ZZLicense.ensure) {
      window.ZZLicense.ensure(name, next);
      return;
    }
    next();
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
    if (el) {
      el.className = "status" + (kind ? " " + kind : "");
      el.textContent = text;
    }
    var home = $("homeStatus");
    if (home) home.textContent = text;
  }

  function setInfo(text) {
    var el = $("info");
    if (el) el.textContent = text;
  }

  // ---------- 打开 / 关闭游戏 ----------
  function openGame() {
    if (!game) {
      try {
        var oldGame = plus.webview.getWebviewById("zz_game");
        if (oldGame) oldGame.close();
      } catch (e) { /* ignore */ }
      game = plus.webview.create(GAME_URL, "zz_game", {
        top: "0px", bottom: "0px", left: "0px", right: "0px",
        bounce: "none", scalable: false
      });
      game.addEventListener("loaded", function () {
        // 游戏页重新加载后，之前注入的抽卡运行时也会没掉
        injected = false;
        injecting = false;
        gachaReady = false;
        gachaRunning = false;
        gachaSession = false;
        gachaEpoch += 1;
        if (gachaPollTimer) { clearTimeout(gachaPollTimer); gachaPollTimer = null; }
        gachaDebug = window.__zzGachaDebug = { loadedAt: Date.now() };
        setTimeout(inject, 600);
      }, false);
      setTimeout(inject, 2000);
    }
    try { game.setStyle({ zindex: 10 }); } catch (e) { /* ignore */ }
    lowerLaunch();
    game.show();
    showFloat();
    keepAwake(true);
    $("homeState").textContent = "已打开";
    setStatus("正在连接游戏…");
  }

  function floatStyle() {
    var sw = window.innerWidth || screen.width || 360;
    var size = 44;
    // 固定在右上角（状态栏下方、更多按钮上方），不再拖动
    return {
      top: "24px", left: String(Math.max(0, sw - size - 8)) + "px",
      width: size + "px", height: size + "px",
      background: "transparent", zindex: 999, bounce: "none", scalable: false
    };
  }

  function ensureFloat() {
    if (floatView) return floatView;
    // 清掉可能残留的旧悬浮窗，避免出现两个助手球
    try {
      var all = plus.webview.all() || [];
      for (var i = 0; i < all.length; i++) {
        var w = all[i];
        if (w && String(w.id || "") === "zz_float") {
          try { w.close(); } catch (e) { /* ignore */ }
        }
      }
    } catch (e) { /* ignore */ }
    try {
      floatView = plus.webview.create("float.html", "zz_float", floatStyle());
      try { floatView.setStyle(floatStyle()); } catch (e) { /* ignore */ }
    } catch (e) {
      floatView = null;
    }
    return floatView;
  }

  function showFloat() {
    var f = ensureFloat();
    if (f) {
      try { f.setStyle(floatStyle()); } catch (e) { /* ignore */ }
      try { f.show(); } catch (e) { /* ignore */ }
    }
  }

  function hideFloat() {
    if (floatView) {
      try { floatView.hide(); } catch (e) { /* ignore */ }
    }
    try {
      var f2 = plus.webview.getWebviewById("zz_float");
      if (f2) f2.hide();
    } catch (e) { /* ignore */ }
  }

  function cleanupStaleWebviews() {
    // HBuilderX 热重载/重复运行时，旧的悬浮球和游戏 webview 可能还留在原生层，
    // 会出现两个「助手」按钮，旧的挡住新的导致点不动。
    try {
      var all = plus.webview.all() || [];
      for (var i = 0; i < all.length; i++) {
        var w = all[i];
        var id = String((w && w.id) || "");
        if (id === "zz_float" || id === "zz_game") {
          try { w.close(); } catch (e) { /* ignore */ }
        }
      }
    } catch (e) { /* ignore */ }
    floatView = null;
    game = null;
    injected = false;
    injecting = false;
  }

  function lowerLaunch() {
    // 游戏显示时把主界面压到游戏下面，否则点「助手」后主界面会一直盖住游戏
    try {
      var launch = plus.webview.getLaunchWebview();
      if (launch) launch.setStyle({ zindex: 1 });
    } catch (e) { /* ignore */ }
  }

  function raiseLaunch() {
    try {
      var launch = plus.webview.getLaunchWebview();
      if (launch) {
        launch.setStyle({ zindex: 20 });
        launch.show();
      }
    } catch (e) { /* ignore */ }
  }

  // 运行命令组件放在 js/runner.js；启动时读进来，注入游戏 webview 里执行。
  function loadRunnerSource(done) {
    if (done) runnerCallbacks.push(done);
    if (runnerSource) {
      var ready = runnerCallbacks;
      runnerCallbacks = [];
      ready.forEach(function (fn) { try { fn(); } catch (e) { /* ignore */ } });
      return;
    }
    if (runnerLoading) return;
    runnerLoading = true;
    var finish = function () {
      runnerLoading = false;
      if (runnerSource && injected) injectRunner();
      var list = runnerCallbacks;
      runnerCallbacks = [];
      list.forEach(function (fn) { try { fn(); } catch (e) { /* ignore */ } });
    };
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", "js/runner.js", true);
      xhr.onreadystatechange = function () {
        if (xhr.readyState !== 4) return;
        if (xhr.status === 200 || xhr.status === 0) runnerSource = xhr.responseText || "";
        finish();
      };
      xhr.send();
    } catch (e) {
      finish();
    }
  }

  function currentRunMapping() {
    var source = window.ZZ_MAPPING || { builds: {} };
    var builds = source.builds || {};
    var name = build && builds[build] ? build : "";
    return { builds: builds, current: name };
  }

  function injectRunner() {
    if (!game || !injected || !runnerSource) return;
    var code = "(function(){try{" +
      "window.__zzRunMapping=" + JSON.stringify(currentRunMapping()) + ";" +
      "window.__zzRunMove={cx:50,cy:78,r:8};" +
      "window.ZZ_TELEPORT=" + JSON.stringify(window.ZZ_TELEPORT || {}) + ";" +
      runnerSource +
      "}catch(e){}})()";
    evalGame(code);
  }

  // 游戏里点悬浮球 → 弹出辅助面板
  window.zzOpenAssist = function () {
    // 先收掉悬浮球，否则它会盖在面板上吃掉点击
    hideFloat();
    try {
      // 先把游戏 webview 藏起来，保证点击落在面板上（不藏会点不动）
      if (game) game.hide();
      hideFloat();
      raiseLaunch();
    } catch (e) { /* ignore */ }
    hideFloat();
    showPage("pageFunc");
  };

  // 面板里的「返回」→ 回到游戏
  function backToGame() {
    if (game) {
      try { game.setStyle({ zindex: 10 }); } catch (e) { /* ignore */ }
      lowerLaunch();
      try { game.show(); } catch (e) { /* ignore */ }
      showFloat();
      return true;
    }
    return false;
  }

  function backToApp() {
    if (game) game.hide();
    hideFloat();
    raiseLaunch();
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
    if (!(window.ZZ_MAPPING && window.ZZ_MAPPING.builds && window.ZZ_MAPPING.builds[b])) {
      setStatus("游戏版本 " + b + " 未适配，请先更新字段映射", "error");
      return;
    }
    build = b;
    var js = translateRuntime(window.ZZ_RUNTIME || "", b);
    var mappingJson = JSON.stringify(window.ZZ_MAPPING || { builds: {} });
    var code =
      "try{window.__zzAssist&&(clearInterval(window.__zzAssist.__timer),clearInterval(window.__zzAssist.__redTimer));}catch(e){}" +
      "delete window.__zzAssist;" +
      "window.__zzAssistMapping=" + mappingJson + ";" +
      "window.__zzAssistStamp='android-app';" +
      "window.__zzAssistNextStamp='android-app';" +
      "window.__zzRpcHandle=" + String(handleGameRpc) + ";" +
      js;
    evalGame(code);
    // 兜底通道：让游戏页把状态写进 plus.storage（如果 plus 在远程页可用）
    evalGame(
      "(function(){try{if(window.__zzBridge)return;window.__zzBridge=1;" +
      "setInterval(function(){try{window.plus&&plus.storage.setItem('zz_state',JSON.stringify(window.__zzAssist.snapshot()));}catch(e){}" +
      "try{if(window.plus){var c=plus.storage.getItem('zz_cmd');if(c){var o=JSON.parse(c);" +
      "if(o&&o.at>(window.__zzCmdAt||0)){window.__zzCmdAt=o.at;window.__zzAssist.set(o.feature,o.value);}}}}catch(e){}" +
      "try{if(window.plus){var q=plus.storage.getItem('zz_rpc');if(q){var req=JSON.parse(q);" +
      "if(req&&req.id&&req.id!==window.__zzRpcId){window.__zzRpcId=req.id;" +
      "var out=window.__zzRpcHandle?window.__zzRpcHandle(req):{id:req.id,ok:false,error:'RPC未安装'};" +
      "plus.storage.setItem('zz_rpc_res',JSON.stringify(out));}}}}catch(e){}" +
      "},500);}catch(e){}})()"
    );
    injected = true;
    injectRunner();
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

    setOn("fRed", red.enabled, "开启");
    var reviveBtn = $("fRevive");
    if (reviveBtn && Date.now() >= revivePendingUntil) {
      reviveBtn.classList.toggle("on", !!rv.enabled);
      reviveBtn.textContent = rv.enabled ? "关闭" : "快速复活";
    }
    setOn("fMob", mob.enabled, "开启");
    setOn("fBoss", boss.enabled, "开启");
    var combat = state.combat || {};
    setOn("fDmg", (combat.damage || {}).enabled, "开启");
    setOn("fShield", (combat.shield || {}).enabled, "开启");
    setOn("fCd", (combat.cooldown || {}).enabled, "开启");
    setOn("fVision", (combat.vision || {}).enabled, "应用");

    var wish = state.wish || {};
    var wishBtn = $("fWish");
    if (wishBtn) {
      wishBtn.classList.toggle("on", !!wish.enabled);
      wishBtn.textContent = wish.enabled ? "关闭" : "开启";
    }

    var bits = [];
    if (red.reason) bits.push("红包：" + red.reason);
    if (rv.reason) bits.push("复活：" + rv.reason);
    if (picked) bits.push("坐标：(" + picked[0] + ", " + picked[1] + ")");
    if (bits.length) setInfo(bits.join("　"));

    // 全灭确认框自动点（直接调面板的确定）
    var conf = rv.confirmAt;
    if (conf && conf.x && Date.now() - lastConfirm > 1500) {
      lastConfirm = Date.now();
      var cp = ((window.ZZ_MAPPING || {}).builds || {})[build] || {};
      cp = cp.confirmPopup || {};
      var cpCls = String(cp.className || "IHCJ").replace(/[^A-Za-z0-9_]/g, "");
      var cpFn = String(cp.confirmMethod || "SOET").replace(/[^A-Za-z0-9_]/g, "");
      evalGame("(()=>{try{const cc=window.cc;if(!cc||!cc.director)return '0';" +
        "const n=cc.find('Root/UIScene/UICanvas/Popup/ConfirmPopup', cc.director.getScene());if(!n)return '0';" +
        "for(const c of (n._components||[])){let cls='';try{cls=cc.js.getClassName(c)}catch(e){}" +
        "if(cls==='" + cpCls + "'){try{c['" + cpFn + "']();return '1'}catch(e){return '0'}}}return '0'}catch(e){return '0'}})()");
    }
  }

  function flashRevive(text) {
    var el = $("reviveInfo");
    if (!el) return;
    el.textContent = text;
    if (reviveFlashTimer) clearTimeout(reviveFlashTimer);
    reviveFlashTimer = setTimeout(function () { el.textContent = ""; }, 1600);
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

  var GACHA_HEROES = [
    [84007, "敖丙", 6], [84008, "雅丽娜", 6], [84013, "乌瑟尔", 6], [84016, "卡卡西", 6],
    [84017, "达斯坦", 6], [84018, "杜康", 6], [85001, "李白", 6], [85002, "玛法里奥", 6],
    [82005, "莱特宁", 5], [83003, "王昭君", 5], [83027, "史矛革", 5], [83030, "卡尔", 5],
    [83031, "吉尔", 5], [84003, "亚瑟", 5], [84005, "缘壹", 5], [84009, "拉斐尔", 5],
    [84011, "谢必安", 5], [84014, "莉娜", 5], [84019, "甘道夫", 5]
  ];
  var gachaReady = false;
  var gachaRunning = false;
  var gachaSession = false;
  var gachaStartTs = 0;
  var gachaGoldBase = null;
  var gachaFragBase = null;
  var gachaCounters = {};
  var gachaPollTimer = null;
  var gachaPollBusy = false;
  var gachaEpoch = 0;
  var gachaStarting = false;
  var gachaDebug = window.__zzGachaDebug = window.__zzGachaDebug || {};
  var GACHA_CHECK_KEY = "zz_gacha_checked";

  function gachaCheckedIds() {
    var ids = [];
    var boxes = document.querySelectorAll(".gHero");
    for (var i = 0; i < boxes.length; i++) {
      if (boxes[i].checked) ids.push(String(boxes[i].value));
    }
    return ids;
  }

  function gachaHeroIds() {
    return GACHA_HEROES.map(function (row) { return String(row[0]); });
  }

  function updateGachaCount() {
    var el = $("gCount");
    if (el) el.textContent = "最大倍数招募（已选 " + gachaCheckedIds().length + "）";
  }

  function saveGachaChecked() {
    try { localStorage.setItem(GACHA_CHECK_KEY, JSON.stringify(gachaCheckedIds())); } catch (e) { /* ignore */ }
    updateGachaCount();
  }

  function loadGachaChecked() {
    try {
      var arr = JSON.parse(localStorage.getItem(GACHA_CHECK_KEY) || "[]");
      return Array.isArray(arr) ? arr.map(String) : [];
    } catch (e) { return []; }
  }

  function setHeroCounters() {
    var cells = document.querySelectorAll("#gHeroes .hero");
    for (var i = 0; i < cells.length; i++) {
      var id = String(cells[i].getAttribute("data-id") || "");
      var cnt = cells[i].querySelector(".cnt");
      if (cnt) cnt.textContent = gachaCounters[id] || "0/0/0";
    }
  }

  function buildHeroList() {
    var box = $("gHeroes");
    if (!box) return;
    var checked = loadGachaChecked();
    box.innerHTML = "";
    GACHA_HEROES.forEach(function (row) {
      var id = String(row[0]);
      var item = document.createElement("label");
      item.className = "hero " + (row[2] >= 6 ? "red" : "gold");
      item.setAttribute("data-id", id);
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = id;
      cb.className = "gHero";
      cb.checked = checked.indexOf(id) >= 0;
      cb.onchange = saveGachaChecked;
      var name = document.createElement("span");
      name.className = "nm";
      name.textContent = row[1];
      var cnt = document.createElement("span");
      cnt.className = "cnt";
      cnt.textContent = gachaCounters[id] || "0/0/0";
      item.appendChild(cb);
      item.appendChild(name);
      item.appendChild(cnt);
      box.appendChild(item);
    });
    updateGachaCount();
  }

  function setAllGacha(value) {
    var boxes = document.querySelectorAll(".gHero");
    for (var i = 0; i < boxes.length; i++) boxes[i].checked = !!value;
    saveGachaChecked();
  }

  function reloadGachaHeroes() {
    gachaCounters = {};
    buildHeroList();
    setHeroCounters();
  }

  var gachaInjecting = false;
  var gachaWaiters = [];

  function finishGacha(ok, reason) {
    gachaInjecting = false;
    if (!ok) {
      gachaReady = false;
      setInfo(reason === "先点「打开游戏」" ? reason
        : "抽卡运行时注入失败" + (reason ? "：" + reason : "") + "，请重新打开游戏");
    }
    var list = gachaWaiters;
    gachaWaiters = [];
    list.forEach(function (fn) { try { fn(ok); } catch (e) { /* ignore */ } });
  }

  function ensureGacha(cb) {
    if (gachaReady) {
      if (cb) cb(true);
      return true;
    }
    if (cb) gachaWaiters.push(cb);
    if (gachaInjecting) return false;
    if (!game) {
      finishGacha(false, "先点「打开游戏」");
      return false;
    }
    var js = translateRuntime(window.ZZ_GACHA_RUNTIME || "", build);
    if (!js) {
      finishGacha(false, "抽卡运行时空了");
      return false;
    }
    var mapping = JSON.stringify(window.ZZ_MAPPING || { builds: {} });
    gachaInjecting = true;
    var tries = 0;
    // 游戏页可能还没加载完/刚切回来，注入会失败；多试几次，每次都重新注入
    (function attempt() {
      tries += 1;
      evalGame("delete window.__zzGacha;window.__zzGachaErr='';window.__zzGachaMapping=" + mapping + ";" +
        "try{(0,eval)(" + JSON.stringify(js) + ");}catch(e){window.__zzGachaErr=String((e&&e.message)||e)}");
      gameRpcJson("gachaReady", {}, function (res) {
        if (res && res.ok) {
          gachaReady = true;
          finishGacha(true, "");
          return;
        }
        if (tries < 8 && game) {
          setTimeout(attempt, 400);
          return;
        }
        finishGacha(false, (res && res.error) || "请先打开游戏并等状态变成「已连接」");
      });
    })();
    return false;
  }

  function gachaFormatCost(value) {
    var n = Math.max(0, Math.round(Number(value) || 0));
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function gachaFormatTime(secs) {
    secs = Math.max(0, Math.floor(secs || 0));
    var m = Math.floor(secs / 60);
    var s = secs % 60;
    return m + ":" + (s < 10 ? "0" + s : String(s));
  }

  function setGachaButton(running) {
    var btn = $("gStart");
    if (!btn) return;
    btn.textContent = running ? "停止" : "开始招募";
    btn.classList.toggle("stop", !!running);
  }

  function setGachaHint(text) {
    var el = $("gHint");
    if (el) el.textContent = text || "打开酒馆/招募界面，再点「开始招募」";
  }

  function resetGachaView() {
    gachaEpoch += 1;
    gachaSession = false;
    gachaStartTs = 0;
    gachaGoldBase = null;
    gachaFragBase = null;
    gachaCounters = {};
    ["gDraws", "gRed", "gCost", "gGreenStat"].forEach(function (id) {
      var el = $(id);
      if (el) el.textContent = "0";
    });
    var t = $("gTime");
    if (t) t.textContent = "0:00";
    setHeroCounters();
  }

  function clearGachaStats() {
    if (gachaRunning) {
      setGachaHint("先点「停止」再清空");
      return;
    }
    resetGachaView();
    setGachaHint("统计已清空");
  }

  function startGachaPoll() {
    gachaDebug.schedAt = Date.now();
    if (gachaPollTimer) clearTimeout(gachaPollTimer);
    gachaPollTimer = setTimeout(gachaPollOnce, 700);
  }

  function gachaPollOnce() {
    gachaPollTimer = null;
    gachaDebug.pollN = (gachaDebug.pollN || 0) + 1;
    gachaDebug.pollAt = Date.now();
    if (!gachaReady) return;
    if (gachaPollBusy) {
      gachaDebug.busySkip = (gachaDebug.busySkip || 0) + 1;
      gachaPollTimer = setTimeout(gachaPollOnce, 500);
      return;
    }
    gachaPollBusy = true;
    var epoch = gachaEpoch;
    gameRpcJson("gachaStats", { ids: gachaHeroIds() }, function (snap, error) {
      gachaPollBusy = false;
      gachaDebug.busy = false;
      gachaDebug.err = error || "";
      gachaDebug.resAt = Date.now();
      gachaDebug.resRunning = snap ? !!snap.running : null;
      gachaDebug.resDraws = snap && snap.stats ? (Number(snap.stats.draws) || 0) : null;
      gachaDebug.epochOk = epoch === gachaEpoch;
      if (epoch === gachaEpoch) {
        try { if (snap) renderGacha(snap); } catch (e) { gachaDebug.renderErr = String((e && e.message) || e); }
      }
      // 响应超时/游戏卡顿时不要停掉轮询，否则抽卡还在跑、统计却永远不动
      if (gachaRunning && !gachaPollTimer) startGachaPoll();
    }, 15000);
  }

  function renderGacha(snap) {
    if (!snap) return;
    var st = snap.stats || {};
    if (snap.running && !gachaSession) {
      gachaSession = true;
      if (!gachaStartTs) gachaStartTs = Date.now();
    }
    var wasRunning = gachaRunning;
    gachaRunning = !!snap.running;
    if (wasRunning && !gachaRunning) gachaEpoch += 1;
    gachaDebug.run = gachaRunning;
    gachaDebug.draws = st && st.draws !== undefined ? Number(st.draws) || 0 : null;

    if (gachaSession) {
      if (gachaGoldBase === null && typeof snap.gold === "number" && isFinite(snap.gold)) {
        gachaGoldBase = snap.gold;
      }
      var frags = snap.fragments;
      if (gachaFragBase === null && frags && typeof frags === "object" && Object.keys(frags).length) {
        gachaFragBase = {};
        Object.keys(frags).forEach(function (k) {
          gachaFragBase[String(k)] = Number(frags[k]) || 0;
        });
      }
      var counters = snap.counters || {};
      GACHA_HEROES.forEach(function (row) {
        var id = String(row[0]);
        var info = counters[id] || counters[row[0]] || {};
        var appear = Number(info.appear) || 0;
        var hit = Number(info.hit) || 0;
        var frag = 0;
        if (gachaFragBase && frags && frags[id] !== undefined && gachaFragBase[id] !== undefined) {
          frag = Math.max(0, (Number(frags[id]) || 0) - (Number(gachaFragBase[id]) || 0));
        }
        gachaCounters[id] = appear + "/" + hit + "/" + frag;
      });
      setHeroCounters();

      var draws = Number(st.draws) || 0;
      var reds;
      if (st.redAppear === undefined || st.redAppear === null) {
        reds = 0;
        (snap.records || []).forEach(function (r) {
          if (Number(r && r.quality) >= 6) reds += Number(r.count) || 0;
        });
      } else {
        reds = Number(st.redAppear) || 0;
      }
      var greens = Number(st.greenSpent) || 0;
      var cost = 0;
      if (gachaGoldBase !== null && typeof snap.gold === "number" && isFinite(snap.gold)) {
        cost = Math.max(0, gachaGoldBase - snap.gold);
      }
      var gd = $("gDraws"); if (gd) gd.textContent = String(draws);
      var gr = $("gRed"); if (gr) gr.textContent = String(reds);
      var gc = $("gCost"); if (gc) gc.textContent = gachaFormatCost(cost);
      var gg = $("gGreenStat"); if (gg) gg.textContent = String(greens);
      if (gachaRunning && gachaStartTs) {
        var gt = $("gTime");
        if (gt) gt.textContent = gachaFormatTime((Date.now() - gachaStartTs) / 1000);
      }
    }

    setGachaButton(gachaRunning);
    if (!gachaSession && !gachaRunning) {
      setGachaHint("打开酒馆/招募界面，再点「开始招募」");
    } else {
      var parts = [];
      if (snap.action) parts.push(String(snap.action));
      if (snap.stopReason && String(snap.stopReason) !== String(snap.action || "")) {
        parts.push(String(snap.stopReason));
      }
      if (!snap.ready && snap.stateReason) parts.push(String(snap.stateReason));
      if (parts.length) setGachaHint(parts.join(" | "));
      else if (!gachaRunning) setGachaHint("已停止");
    }

    if (gachaRunning) {
      if (!gachaPollTimer && !gachaPollBusy) startGachaPoll();
    } else if (gachaPollTimer) {
      clearTimeout(gachaPollTimer);
      gachaPollTimer = null;
    }
    gachaDebug.renderedAt = Date.now();
  }

  function gachaStats() {
    if (!gachaReady) {
      ensureGacha(function (ok) { if (ok) gachaStats(); });
      return;
    }
    var epoch = gachaEpoch;
    gachaDebug.manualAt = Date.now();
    gameRpcJson("gachaStats", { ids: gachaHeroIds() }, function (snap, error) {
      gachaDebug.manualErr = error || "";
      if (epoch === gachaEpoch) {
        try { renderGacha(snap); } catch (e) { gachaDebug.renderErr = String((e && e.message) || e); }
      }
    }, 15000);
  }

  function startGachaRun() {
    if (gachaRunning) {
      gachaEpoch += 1;
      gameRpcJson("gachaStop", {}, function () {
        gachaRunning = false;
        setGachaButton(false);
        setGachaHint("已停止");
        if (gachaPollTimer) {
          clearTimeout(gachaPollTimer);
          gachaPollTimer = null;
        }
      });
      return;
    }
    if (gachaStarting) return;
    if (!game) { setGachaHint("先点「打开游戏」"); return; }
    var targets = gachaCheckedIds();
    if (!targets.length) { setGachaHint("先勾选要保留的英雄"); return; }
    var opts = {
      targets: targets, keepQuality: 0,
      maxDraws: numVal("gMax", 0), delayMs: 1200,
      refresh: false, maxGreen: numVal("gGreen", 0),
      maxBet: true, alwaysAbandon: true
    };
    saveGachaChecked();
    gachaStarting = true;
    setGachaHint("正在启动…");
    ensureGacha(function (ok) {
      if (!ok) { gachaStarting = false; return; }
      gameRpcJson("gachaStart", { opts: opts }, function (res, error) {
        gachaStarting = false;
        if (error || !res || !res.ok) {
          setGachaHint("开始失败：" + (error || (res && res.message) || "未知原因"));
          setGachaButton(false);
          return;
        }
        gachaEpoch += 1;
        gachaSession = true;
        gachaStartTs = Date.now();
        gachaGoldBase = (typeof res.gold === "number" && isFinite(res.gold)) ? res.gold : null;
        gachaFragBase = null;
        if (res.fragments && typeof res.fragments === "object" && Object.keys(res.fragments).length) {
          gachaFragBase = {};
          Object.keys(res.fragments).forEach(function (k) {
            gachaFragBase[String(k)] = Number(res.fragments[k]) || 0;
          });
        }
        gachaCounters = {};
        gachaRunning = true;
        setGachaButton(true);
        renderGacha(res);
        setGachaHint("已开始，读取招募面板…");
        startGachaPoll();
      });
    });
  }

  function numVal(id, fallback) {
    var el = $(id);
    var v = el ? Number(el.value) : NaN;
    if (!isFinite(v) || v < 0) return fallback;
    return v;
  }

  function toggleCombat(feature, onValue, offValue) {
    var item = (state && state.combat && state.combat[feature]) || null;
    var on = !!(item && item.enabled);
    send(feature, on ? offValue : onValue);
    setTimeout(poll, 200);
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
    var busy = false;
    pickTimer = setInterval(function () {
      if (busy) return;
      busy = true;
      gameRpcJson("pickState", {}, function (p, error) {
        busy = false;
        if (error) return;
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
      });
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

  // ---------- 怪物列表（PC 同款：刷新 -> 选中 -> 闪现） ----------
  var monsterRows = [];
  var selectedMonster = -1;

  function refreshMonsterList(retry) {
    gameRpcJson("monsters", {},
      function (all, error) {
        if (error || !all) {
          $("blinkInfo").textContent = "寻怪读取失败：" + (error || "无返回");
          return;
        }
        var items = all.slice();
        var filter = ($("blinkFilter") || {}).value || "all";
        if (filter === "boss") {
          items = items.filter(function (m) { return !!m.boss; });
        } else if (filter === "small") {
          items = items.filter(function (m) { return !m.boss; });
        }
        if (!items.length && !retry) {
          $("blinkInfo").textContent = "正在读取附近怪物…";
          setTimeout(function () { refreshMonsterList(true); }, 600);
          return;
        }
        monsterRows = items;
        selectedMonster = -1;
        renderMonsterList();
        if (items.length) {
          $("blinkInfo").textContent = "共 " + items.length + " 只，点一只选中再点「闪现」";
          return;
        }
        gameRpcJson("probe", {},
          function (info, probeErr) {
            if (probeErr || !info) {
              $("blinkInfo").textContent = "寻怪读取失败：" + (probeErr || "无返回");
            } else if (info.error) {
              $("blinkInfo").textContent = "寻怪读取失败：" + info.error + "（等进入地图后再刷新）";
            } else if (!info.units) {
              $("blinkInfo").textContent = "地图单位还没加载，等进入地图后再刷新";
            } else if (!info.monsters) {
              $("blinkInfo").textContent = "有 " + info.units + " 个单位，但都不是可寻路怪物（NPC/玩家/假人），去野外地图再刷新";
            } else {
              $("blinkInfo").textContent = "有 " + info.monsters + " 只怪但列表为空，稍后再刷新（" + (info.names || []).join("、") + "）";
            }
          }
        );
      }
    );
  }

  function renderMonsterList() {
    var box = $("monsterList");
    if (!box) return;
    box.innerHTML = "";
    monsterRows.slice(0, 120).forEach(function (m, index) {
      var row = document.createElement("div");
      row.className = "item" + (m.boss ? " boss" : "") + (index === selectedMonster ? " sel" : "");
      var left = document.createElement("div");
      var hp = (m.hp !== null && m.maxHp) ? " · 血 " + fmtNum(m.hp) + "/" + fmtNum(m.maxHp) : "";
      var stats = (m.stats || []).map(function (s) {
        var label = s && (s.label || s.key);
        var text = s && (s.text !== undefined && s.text !== null ? s.text : s.value);
        return label ? (label + " " + text) : "";
      }).filter(function (x) { return !!x; }).join(" · ");
      left.innerHTML = '<div class="name">' + (m.boss ? "★ " : "") + escapeHtml(m.name || "怪") + "</div>" +
        '<div class="meta">' + Math.round(m.d || 0) + " 米" + hp + "</div>" +
        (stats ? '<div class="stats">' + escapeHtml(stats) + "</div>" : "");
      row.appendChild(left);
      row.onclick = function () {
        selectedMonster = index;
        renderMonsterList();
        $("blinkInfo").textContent = "已选中：" + (m.name || "怪") + "，点「闪现」";
      };
      box.appendChild(row);
    });
  }

  function blinkSelected() {
    if (selectedMonster < 0 || !monsterRows[selectedMonster]) {
      $("blinkInfo").textContent = "先点「刷新怪物」，再选中一只";
      return;
    }
    var m = monsterRows[selectedMonster];
    var mode = ($("blinkMode") || {}).value || "instant";
    send("blinkTo", { x: m.x, y: m.y, mode: mode });
    $("blinkInfo").textContent = (mode === "fly" ? "正在飞过去：" : "正在瞬移：") + (m.name || "怪");
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
  // ---------- 公会情报 / 公会迷宫 / 自动宝箱 / 鼠标瞬移 ----------

  function gameJson(code) {
    var raw = unquote(evalGame(code));
    try { return JSON.parse(raw); } catch (e) { return null; }
  }

  // 远程游戏页的 evalJS 在 HBuilder 里拿不到返回值，改用 plus.storage 做请求/响应。
  // 游戏页只执行白名单动作，不使用 eval。
  function gameRpc(action, params, cb, timeoutMs) {
    var id = "r" + (++rpcSeq) + "_" + Date.now();
    var timer = null;
    var done = false;
    function finish(value, error) {
      if (done) return;
      done = true;
      if (timer) clearInterval(timer);
      cb(value, error || "");
    }
    try {
      plus.storage.setItem("zz_rpc", JSON.stringify({
        id: id, action: String(action), params: params || {}, at: Date.now()
      }));
    } catch (e) {
      finish(null, "存储不可用");
      return;
    }
    var tries = 0;
    var maxTries = Math.max(10, Math.ceil((Number(timeoutMs) || 5000) / 200));
    timer = setInterval(function () {
      tries += 1;
      var raw = null;
      try { raw = plus.storage.getItem("zz_rpc_res"); } catch (e) { raw = null; }
      if (raw) {
        var out = null;
        try { out = JSON.parse(raw); } catch (e) { out = null; }
        if (out && out.id === id) {
          try { plus.storage.removeItem("zz_rpc_res"); } catch (e) { /* ignore */ }
          if (out.ok) finish(out.value, "");
          else finish(null, out.error || "执行失败");
          return;
        }
      }
      if (tries >= maxTries) finish(null, "等待游戏响应超时");
    }, 200);
  }

  function gameRpcJson(action, params, cb, timeoutMs) {
    gameRpc(action, params, function (value, error) {
      if (error) { cb(null, error); return; }
      if (typeof value === "string") {
        try { cb(JSON.parse(value), ""); } catch (e) { cb(null, "返回格式异常"); }
      } else {
        cb(value, "");
      }
    }, timeoutMs);
  }

  // 注入到游戏页执行的白名单动作；必须自包含
  function handleGameRpc(req) {
    var out = { id: req && req.id, ok: false, value: null, error: "" };
    try {
      var a = window.__zzAssist;
      if (!a) throw new Error("运行时未注入");
      var p = (req && req.params) || {};
      var action = req && req.action;
      if (action === "monsters") {
        out.value = JSON.stringify(a.monsters());
      } else if (action === "snapshot") {
        out.value = JSON.stringify(a.snapshot());
      } else if (action === "clickTeleport") {
        out.value = JSON.stringify(a.clickTeleport(!!p.on));
      } else if (action === "guildStart") {
        out.value = JSON.stringify(a.guildIntelStart(Number(p.camp) || 0));
      } else if (action === "guildState") {
        out.value = JSON.stringify(a.guildIntelState());
      } else if (action === "guildTeamsStart") {
        out.value = JSON.stringify(a.guildTeamsStart(Number(p.camp) || 0, Number(p.spot) || 0));
      } else if (action === "guildTeamsState") {
        out.value = JSON.stringify(a.guildTeamsState());
      } else if (action === "plankStart") {
        out.value = JSON.stringify(a.autoJumpStart({}));
      } else if (action === "plankState") {
        out.value = JSON.stringify(a.autoJumpState());
      } else if (action === "plankStop") {
        if (a.autoJumpStop) a.autoJumpStop();
        out.value = JSON.stringify({ ok: true });
      } else if (action === "farmStart") {
        out.value = JSON.stringify(a.farmAutoStart({ mode: String(p.mode || "visit") }));
      } else if (action === "farmState") {
        out.value = JSON.stringify(a.farmAutoState());
      } else if (action === "farmStop") {
        if (a.farmAutoStop) a.farmAutoStop();
        out.value = JSON.stringify({ ok: true });
      } else if (action === "mazeQuery") {
        out.value = JSON.stringify(a.mazeQuery());
      } else if (action === "pickState") {
        out.value = JSON.stringify(window.__zzPick || null);
      } else if (action === "gachaReady") {
        out.value = JSON.stringify({
          ok: !!(window.__zzGacha && window.__zzGacha.start),
          version: (window.__zzGacha && window.__zzGacha.version) || 0,
          error: String(window.__zzGachaErr || "")
        });
      } else if (action === "gachaStats") {
        var gs = window.__zzGacha ? window.__zzGacha.snapshot() : null;
        if (gs) {
          var wantIds = Array.isArray(p.ids) ? p.ids : null;
          if (wantIds && gs.fragments) {
            var keepFrag = {};
            wantIds.forEach(function (id) {
              if (gs.fragments[id] !== undefined) keepFrag[id] = gs.fragments[id];
            });
            gs.fragments = keepFrag;
          }
          if (wantIds && gs.counters) {
            var keepCnt = {};
            wantIds.forEach(function (id) {
              if (gs.counters[id] !== undefined) keepCnt[id] = gs.counters[id];
            });
            gs.counters = keepCnt;
          }
          gs.candidates = [];
          if (gs.records) {
            gs.records = gs.records.map(function (r) {
              return { quality: r && r.quality, count: r && r.count };
            });
          }
        }
        out.value = JSON.stringify(gs);
      } else if (action === "gachaStart") {
        if (!window.__zzGacha || typeof window.__zzGacha.start !== "function") throw new Error("抽卡运行时未就绪");
        out.value = JSON.stringify(window.__zzGacha.start(p.opts || {}));
      } else if (action === "gachaStop") {
        if (window.__zzGacha && typeof window.__zzGacha.stop === "function") window.__zzGacha.stop();
        out.value = JSON.stringify({ ok: true });
      } else if (action === "runStart") {
        if (!window.__zzRun || typeof window.__zzRun.start !== "function") throw new Error("运行命令组件还没注入");
        out.value = JSON.stringify(window.__zzRun.start(p.payload || {}));
      } else if (action === "runStatus") {
        if (!window.__zzRun || typeof window.__zzRun.snapshot !== "function") throw new Error("运行命令组件还没注入");
        out.value = JSON.stringify(window.__zzRun.snapshot());
      } else if (action === "runStop") {
        if (window.__zzRun && typeof window.__zzRun.stop === "function") window.__zzRun.stop();
        out.value = JSON.stringify({ ok: true });
      } else if (action === "probe") {
        var probe;
        try {
          var G = window.GCOA;
          if (!G) probe = { error: "NO_GCOA" };
          else if (!G.QESF || !G.STDU) probe = { error: "NO_MAP" };
          else if (!G.VZZY) probe = { error: "NO_CORE" };
          else {
            var mg = G.VZZY.LSLA && G.VZZY.LSLA();
            var un = mg && mg.ICUU && mg.ICUU();
            if (!un || typeof un.VSJG !== "function") probe = { error: "NO_UNITS" };
            else {
              var n = 0, m = 0, names = [];
              un.VSJG(function (i, u) {
                n += 1;
                var isM = typeof u.FYSR === "function" ? u.FYSR() : true;
                var dead = typeof u.KHGB === "function" ? u.KHGB() : false;
                if (isM && !dead) {
                  m += 1;
                  if (names.length < 3) names.push((u.GUJG && u.GUJG.name) || ("cfg" + u.CXKW));
                }
              });
              probe = { units: n, monsters: m, names: names };
            }
          }
        } catch (cause) {
          probe = { error: String((cause && cause.message) || cause) };
        }
        out.value = JSON.stringify(probe);
      } else {
        throw new Error("未知操作");
      }
      out.ok = true;
    } catch (e) {
      out.error = String((e && e.message) || e);
    }
    return out;
  }

  function fmtPower(value) {
    var n = Number(value);
    if (!isFinite(n)) return "0";
    return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function isPlaceholderPower(value) {
    var n = Number(value);
    return isFinite(n) && n >= 0 && n <= 300 && n % 100 === 0;
  }

  function memberPowerText(value) {
    return isPlaceholderPower(value) ? "未配置" : fmtPower(value);
  }

  function groupPower(members) {
    var total = 0, unset = 0, real = 0;
    (members || []).forEach(function (m) {
      if (isPlaceholderPower(m.power)) unset += 1;
      else { real += 1; total += Number(m.power || 0); }
    });
    return { total: total, unset: unset, hasReal: real > 0 };
  }

  function guildTeamText(teams) {
    if (!teams || !teams.length) return "未读取";
    return teams.map(function (team) {
      return "队" + (team.slot || "?") + " " + fmtPower(team.power || 0);
    }).join(" / ");
  }

  function renderGuild(rows) {
    guildRows = (rows || []).slice().sort(function (a, b) {
      return (Number(a.camp) || 0) - (Number(b.camp) || 0)
        || (Number(a.id) || 0) - (Number(b.id) || 0);
    });
    var ownCount = 0, ownPower = 0, ownUnset = 0;
    var enemyCount = 0, enemyPower = 0, enemyUnset = 0;
    var html = guildRows.map(function (row) {
      var camp = Number(row.camp) || 0;
      var label = camp === 1 ? "我方" : "敌方";
      var members = row.members || [];
      var g = groupPower(members);
      var count = row.error ? 0 : Number(row.count || members.length);
      var powerText = row.error ? "0" : (members.length && !g.hasReal ? "未配置" : fmtPower(g.total));
      var summary;
      if (row.error) {
        summary = "查询失败：" + escapeHtml(row.error);
      } else if (!members.length) {
        summary = "（无人驻守）";
      } else {
        var cache = guildTeamsCache[(Number(row.camp) || 0) + ":" + (Number(row.id) || 0)] || null;
        summary = members.slice(0, 4).map(function (m) {
          var teams = cache && cache[m.id];
          var teamText = teams ? "，三队 " + guildTeamText(teams) : "";
          return escapeHtml(m.name || m.id || "?") + "(" + memberPowerText(m.power) + teamText + ")";
        }).join("、");
        if (members.length > 4) summary += " 等 " + members.length + " 人";
      }
      if (camp === 1) { ownCount += count; ownPower += g.total; ownUnset += g.unset; }
      else if (camp === 2) { enemyCount += count; enemyPower += g.total; enemyUnset += g.unset; }
      return '<div class="list-row"><b>' + label + " " + (Number(row.id) || 0) + ' 号</b>'
        + " · " + count + " 人 · 总战力 " + powerText
        + '<br><span class="muted">' + summary + "</span></div>";
    }).join("");
    var list = $("gList");
    if (list) list.innerHTML = html || '<div class="info">没有数据</div>';
    var hint = (ownUnset + enemyUnset) ? ("（" + (ownUnset + enemyUnset) + " 人未配置队伍）") : "";
    var status = $("gStatus");
    if (status) {
      status.textContent = "我方 " + ownCount + " 人 / 战力 " + fmtPower(ownPower)
        + "；敌方 " + enemyCount + " 人 / 战力 " + fmtPower(enemyPower) + hint;
    }
  }

  function guildQuery() {
    var camp = Number(($("gCamp") || {}).value) || 0;
    guildTeamsCache = {};
    $("gStatus").textContent = "查询中…";
    gameRpcJson("guildStart", { camp: camp }, function (res, error) {
      if (error || !res || !res.ok) {
        $("gStatus").textContent = "查询失败：" + (error || (res && res.error) || "运行时还没注入");
        return;
      }
      $("gStatus").textContent = "查询中 0/" + (res.total || 26) + "…";
      if (guildTimer) clearInterval(guildTimer);
      var busy = false;
      guildTimer = setInterval(function () {
        if (busy) return;
        busy = true;
        gameRpcJson("guildState", {}, function (st, err) {
          busy = false;
          if (err || !st) return;
          if (st.running) {
            $("gStatus").textContent = "查询中 " + (st.done || 0) + "/" + (st.total || 0) + "…";
            return;
          }
          clearInterval(guildTimer);
          guildTimer = null;
          renderGuild(st.rows || []);
        });
      }, 800);
    });
  }

  function guildReadTeams(done) {
    var rows = (guildRows || []).filter(function (row) {
      return (row.members || []).length && !row.error;
    });
    if (!rows.length) {
      $("gStatus").textContent = "没有可读取的据点";
      if (done) done(0);
      return;
    }
    var index = 0;
    var errors = 0;

    function next() {
      if (index >= rows.length) {
        renderGuild(guildRows);
        $("gStatus").textContent = "分队战力已读取";
        if (done) done(errors);
        return;
      }
      var row = rows[index];
      var camp = Number(row.camp) || 0;
      var spot = Number(row.id) || 0;
      var key = camp + ":" + spot;
      if (guildTeamsCache[key]) {
        index += 1;
        next();
        return;
      }
      $("gStatus").textContent = "读取分队 " + (index + 1) + "/" + rows.length
        + "（" + (camp === 1 ? "我方" : "敌方") + " " + spot + "号）…";
      gameRpcJson("guildTeamsStart", { camp: camp, spot: spot }, function (res, error) {
        if (error || !res || !res.ok) {
          errors += 1;
          index += 1;
          next();
          return;
        }
        function poll() {
          gameRpcJson("guildTeamsState", {}, function (st, err) {
            if (err || !st) {
              errors += 1;
              index += 1;
              next();
              return;
            }
            if (st.running) {
              setTimeout(poll, 400);
              return;
            }
            guildTeamsCache[key] = st.teams || {};
            renderGuild(guildRows);
            index += 1;
            next();
          });
        }
        poll();
      });
    }

    next();
  }

  function guildTsvCell(value) {
    var text = String(value === null || value === undefined ? "" : value);
    return text.replace(/[\t\r\n]+/g, " ");
  }

  function saveTextFile(name, text, cb) {
    function finish(path, url) {
      guildExportFile = { name: name, path: path || "", url: url || "" };
      cb("已导出：" + (path || url || name) + "（内容 " + text.length + " 字符）");
    }

    function copyFallback() {
      copyText(text);
      cb("保存失败，已复制到剪贴板");
    }

    if (!window.plus) {
      copyText(text);
      cb("已复制到剪贴板");
      return;
    }

    // Android 10+：用 MediaStore 写入系统「下载/小太阳脚本」，文件管理器可见
    if (plus.android) {
      var uri = null;
      var resolver = null;
      try {
        var Downloads = plus.android.importClass("android.provider.MediaStore$Downloads");
        var ContentValues = plus.android.importClass("android.content.ContentValues");
        var Environment = plus.android.importClass("android.os.Environment");
        var values = new ContentValues();
        values.put("_display_name", name);
        values.put("mime_type", "text/plain");
        values.put("relative_path", "Download/小太阳脚本");
        resolver = plus.android.runtimeMainActivity().getContentResolver();
        uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values);
        if (uri) {
          var os = resolver.openOutputStream(uri);
          var OutputStreamWriter = plus.android.importClass("java.io.OutputStreamWriter");
          var writer = new OutputStreamWriter(os);
          writer.write(text, 0, text.length);
          writer.flush();
          writer.close();
          var down = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
          var full = down.getAbsolutePath() + "/小太阳脚本/" + name;
          finish(full, String(uri.toString()));
          return;
        }
      } catch (e) {
        try {
          if (uri && resolver) resolver.delete(uri, null, null);
        } catch (e2) { /* ignore */ }
      }
    }

    // 旧系统或 MediaStore 失败：写到 5+ 应用下载目录
    if (plus.io) {
      try {
        plus.io.resolveLocalFileSystemURL("_downloads/", function (root) {
          root.getDirectory("小太阳脚本", { create: true }, function (dirEntry) {
            dirEntry.getFile(name, { create: true }, function (entry) {
              entry.createWriter(function (writer) {
                writer.onwrite = function () {
                  var url = "";
                  var path = "";
                  try { url = entry.toLocalURL(); } catch (e) { url = ""; }
                  try { path = plus.io.convertLocalFileSystemURL(url) || url; } catch (e) { path = url; }
                  finish(path, url);
                };
                writer.onerror = copyFallback;
                writer.write(text);
              }, copyFallback);
            }, copyFallback);
          }, copyFallback);
        }, copyFallback);
        return;
      } catch (e) { /* fall through */ }
    }
    copyFallback();
  }

  function guildExport() {
    if (!guildRows.length) {
      $("gStatus").textContent = "请先查询据点";
      return;
    }
    guildReadTeams(function () {
      var lines = [[
        "阵营", "据点", "序号", "成员", "总战力",
        "队1战力", "队2战力", "队3战力", "等级", "区服", "ID"
      ]];
      guildRows.forEach(function (row) {
        var camp = Number(row.camp) || 0;
        var label = camp === 1 ? "我方" : "敌方";
        var spot = Number(row.id) || 0;
        var cache = guildTeamsCache[camp + ":" + spot] || {};
        (row.members || []).forEach(function (member, index) {
          var teams = cache[member.id] || [];
          var bySlot = {};
          teams.forEach(function (team) {
            bySlot[Number(team.slot) || 0] = Number(team.power) || 0;
          });
          lines.push([
            label, spot, index + 1, member.name || "?",
            Number(member.power) || 0,
            bySlot[1] === undefined ? "" : bySlot[1],
            bySlot[2] === undefined ? "" : bySlot[2],
            bySlot[3] === undefined ? "" : bySlot[3],
            member.level || 0, member.server || "", member.id || ""
          ]);
        });
      });
      lines.push([]);
      lines.push(["汇总"]);
      lines.push(["阵营", "人数", "真实总战力", "未配置人数"]);
      [1, 2].forEach(function (camp) {
        var members = [];
        guildRows.forEach(function (row) {
          if ((Number(row.camp) || 0) === camp) {
            members = members.concat(row.members || []);
          }
        });
        var g = groupPower(members);
        lines.push([camp === 1 ? "我方" : "敌方", members.length, g.total, g.unset]);
      });
      var text = "\ufeff" + lines.map(function (line) {
        return line.map(guildTsvCell).join("\t");
      }).join("\r\n");
      var stamp = new Date();
      function pad(n) { return n < 10 ? "0" + n : "" + n; }
      var name = "公会战情报_" + stamp.getFullYear() + pad(stamp.getMonth() + 1)
        + pad(stamp.getDate()) + "_" + pad(stamp.getHours()) + pad(stamp.getMinutes()) + ".txt";
      saveTextFile(name, text, function (message) {
        $("gStatus").textContent = message;
      });
    });
  }

  function guildShare() {
    if (!guildExportFile || !(guildExportFile.url || guildExportFile.path)) {
      $("gStatus").textContent = "请先点「全部导出」";
      return;
    }
    var url = guildExportFile.url || "";
    var path = guildExportFile.path || "";

    // Android 原生分享：用 content:// 带上 TXT 文件本体
    if (window.plus && plus.android && url.indexOf("content://") === 0) {
      try {
        var Intent = plus.android.importClass("android.content.Intent");
        var Uri = plus.android.importClass("android.net.Uri");
        var intent = new Intent(Intent.ACTION_SEND);
        intent.setType("text/plain");
        intent.putExtra(Intent.EXTRA_STREAM, Uri.parse(url));
        var ClipData = plus.android.importClass("android.content.ClipData");
        intent.setClipData(ClipData.newRawUri("公会战情报", Uri.parse(url)));
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        var chooser = Intent.createChooser(intent, "分享公会战情报");
        plus.android.runtimeMainActivity().startActivity(chooser);
        $("gStatus").textContent = "已打开分享";
        return;
      } catch (e) { /* 继续尝试 5+ 分享 */ }
    }

    try {
      if (window.plus && plus.share && plus.share.sendWithSystem) {
        plus.share.sendWithSystem({
          type: "file",
          content: "公会战情报",
          files: [path || url]
        }, function () {
          $("gStatus").textContent = "已打开分享";
        }, function () {
          copyText(path || url);
          $("gStatus").textContent = "分享不可用，已复制文件路径";
        });
        return;
      }
    } catch (e) { /* ignore */ }
    copyText(path || url);
    $("gStatus").textContent = "已复制文件路径";
  }

  function guildOpenFile() {
    if (!guildExportFile || !(guildExportFile.url || guildExportFile.path)) {
      $("gStatus").textContent = "请先点「全部导出」";
      return;
    }
    var url = guildExportFile.url || "";
    if (window.plus && plus.android && url.indexOf("content://") === 0) {
      try {
        var Intent = plus.android.importClass("android.content.Intent");
        var Uri = plus.android.importClass("android.net.Uri");
        var intent = new Intent(Intent.ACTION_VIEW);
        intent.setDataAndType(Uri.parse(url), "text/plain");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        plus.android.runtimeMainActivity().startActivity(intent);
        return;
      } catch (e) { /* 没有能打开 txt 的应用 */ }
    }
    guildShare();
  }

  function guildCopyPath() {
    if (!guildExportFile) {
      $("gStatus").textContent = "请先点「全部导出」";
      return;
    }
    var path = guildExportFile.path || guildExportFile.url || "";
    copyText(path);
    $("gStatus").textContent = "已复制路径：" + path;
  }

  function guildCopy() {
    if (!guildRows.length) {
      $("gStatus").textContent = "还没有查询结果";
      return;
    }
    var lines = [];
    guildRows.forEach(function (row) {
      var camp = Number(row.camp) === 1 ? "我方" : "敌方";
      var members = row.members || [];
      var g = groupPower(members);
      var powerText = row.error ? "0" : (members.length && !g.hasReal ? "未配置" : fmtPower(g.total));
      lines.push(camp + " " + (Number(row.id) || 0) + " 号据点  人数 "
        + (row.error ? 0 : Number(row.count || members.length)) + "  总战力 " + powerText);
      if (g.unset) lines.push("  其中 " + g.unset + " 人未配置队伍（占位战力不计入总战力）");
      if (row.error) {
        lines.push("  查询失败：" + row.error);
        return;
      }
      var cache = guildTeamsCache[(Number(row.camp) || 0) + ":" + (Number(row.id) || 0)] || {};
      members.forEach(function (m) {
        var teams = cache[m.id];
        lines.push("  " + (m.name || "?") + "  总战力 " + memberPowerText(m.power)
          + "  三队 " + guildTeamText(teams)
          + "  等级 " + (m.level || 0) + "  区服 " + (m.server || "-")
          + "  ID " + (m.id || "-"));
      });
    });
    copyText(lines.join("\n"));
    $("gStatus").textContent = "已复制 " + guildRows.length + " 条据点记录";
  }

  function pctText(value) {
    var n = Number(value);
    return isFinite(n) ? n.toFixed(2) + "%" : "-";
  }

  function renderMaze(snap, cached) {
    mazeLast = snap;
    var summary = "难度 " + (snap.difficulty || "-")
      + " · 第 " + (snap.floor || "-") + " 层"
      + " · 首领 " + (snap.bossId || "-")
      + " · 总血量 " + fmtPower(snap.maxHp || 0)
      + " · 抗暴击 " + pctText(snap.antiCrit)
      + " · 抗暴伤 " + pctText(snap.antiCritDamage)
      + " · 命中 " + pctText(snap.hitRate);
    $("mSummary").textContent = summary;
    var attrs = snap.attributes || [];
    $("mList").innerHTML = attrs.length ? attrs.map(function (a) {
      return '<div class="list-row"><b>' + escapeHtml(a.name || ("属性 #" + a.id))
        + "</b>：" + escapeHtml(a.value || a.raw || "") + "</div>";
    }).join("") : '<div class="info">没有读到属性列表</div>';
    var stamp = snap.capturedAt || "";
    $("mStatus").textContent = cached
      ? ("显示上次查询结果（" + (stamp || "时间未知") + "）")
      : ("查询成功：" + (stamp || "已记录"));
  }

  function queryMaze(manual) {
    gameRpcJson("mazeQuery", {}, function (res, error) {
      if (error || !res) {
        if (manual) $("mStatus").textContent = "查询失败：" + (error || "运行时还没注入");
        return;
      }
      if (res.ok && res.snapshot) {
        var stamp = String(res.snapshot.capturedAt || "");
        if (!manual && stamp === mazeLastStamp) return;
        mazeLastStamp = stamp;
        renderMaze(res.snapshot, false);
        return;
      }
      if (res.cached) {
        var cachedStamp = String(res.cached.capturedAt || "cached");
        if (!manual && cachedStamp === mazeLastStamp) return;
        mazeLastStamp = cachedStamp;
        renderMaze(res.cached, true);
        $("mStatus").textContent = "当前不在首领战斗：" + (res.error || "") + "（下面是上次结果）";
        return;
      }
      if (manual) $("mStatus").textContent = "查询失败：" + (res.error || "运行时还没注入");
    });
  }

  function startMazeAuto() {
    if (mazeTimer) clearInterval(mazeTimer);
    queryMaze(false);
    mazeTimer = setInterval(function () {
      var panel = $("mazePanel");
      if (!panel || !panel.classList.contains("open")) {
        clearInterval(mazeTimer);
        mazeTimer = null;
        return;
      }
      queryMaze(false);
    }, 2000);
  }

  function mazeCopy() {
    if (!mazeLast) {
      $("mStatus").textContent = "还没有查询结果";
      return;
    }
    var lines = [$("mSummary").textContent];
    (mazeLast.attributes || []).forEach(function (a) {
      lines.push((a.name || ("属性 #" + a.id)) + "：" + (a.value || a.raw || ""));
    });
    copyText(lines.join("\n"));
    $("mStatus").textContent = "已复制首领属性";
  }

  function showFeature(key) {
    var features = [
      ["daily", "dailyPanel", "playDaily"],
      ["plank", "plankPanel", "playPlank"],
      ["farm", "farmPanel", "playFarm"],
      ["talisman", "talismanPanel", "playTalisman"],
      ["guild", "guildPanel", "playGuild"],
      ["maze", "mazePanel", "playMaze"]
    ];
    features.forEach(function (item) {
      var name = item[0];
      var panel = $(item[1]);
      var button = $(item[2]);
      if (panel) panel.classList.toggle("open", name === key);
      if (button) button.classList.toggle("sel", name === key);
      if (panel && name === key) {
        setTimeout(function () {
          try { panel.scrollIntoView({ behavior: "smooth", block: "start" }); } catch (e) {
            try { panel.scrollIntoView(); } catch (e2) { /* ignore */ }
          }
        }, 80);
      }
    });
    if (guildTimer) { clearInterval(guildTimer); guildTimer = null; }
    if (mazeTimer) { clearInterval(mazeTimer); mazeTimer = null; }
    if (key === "maze") startMazeAuto();
  }

  var plankRunning = false;
  var plankTimer = null;

  function plankSetStatus(text) {
    var el = $("plankStatus");
    if (el) el.textContent = String(text || "");
  }

  function plankReset() {
    plankRunning = false;
    if (plankTimer) {
      clearTimeout(plankTimer);
      plankTimer = null;
    }
    var btn = $("plankRun");
    if (btn) btn.textContent = "开始自动跳";
  }

  function plankPoll() {
    if (!plankRunning) return;
    gameRpcJson("plankState", {}, function (st, err) {
      if (err || !st) {
        plankSetStatus("读取状态失败：" + (err || "无返回"));
        plankTimer = setTimeout(plankPoll, 800);
        return;
      }
      if (st.error) {
        plankSetStatus("失败：" + st.error);
        plankReset();
        return;
      }
      if (!st.running) {
        plankSetStatus("已结束：" + (st.last || "完成"));
        plankReset();
        return;
      }
      var jumps = Number(st.jumps || 0);
      plankSetStatus("自动跳中：已跳 " + jumps + " 次" + (st.last ? " · " + st.last : ""));
      plankTimer = setTimeout(plankPoll, 500);
    });
  }

  function togglePlank() {
    if (plankRunning) {
      gameRpcJson("plankStop", {}, function () { /* ignore */ });
      plankSetStatus("已停止");
      plankReset();
      return;
    }
    gameRpcJson("plankStart", {}, function (result, error) {
      if (error || !result) {
        plankSetStatus("启动失败：" + (error || "游戏没有返回"));
        return;
      }
      if (!result.ok) {
        plankSetStatus(result.message || "启动失败");
        return;
      }
      plankRunning = true;
      var btn = $("plankRun");
      if (btn) btn.textContent = "停止";
      plankSetStatus("自动跳中…");
      plankPoll();
    });
  }

  var farmRunning = false;
  var farmTimer = null;
  var farmLast = "";
  var farmLogLines = [];

  function farmSetStatus(text) {
    var el = $("farmStatus");
    if (el) el.textContent = String(text || "");
  }

  function farmLog(text) {
    var el = $("farmLog");
    if (!el) return;
    var line = String(text || "").trim();
    if (!line) return;
    farmLogLines.push(line);
    if (farmLogLines.length > 8) farmLogLines.shift();
    el.textContent = farmLogLines.join("\n");
  }

  function farmReset() {
    farmRunning = false;
    if (farmTimer) {
      clearTimeout(farmTimer);
      farmTimer = null;
    }
  }

  function farmPoll() {
    if (!farmRunning) return;
    gameRpcJson("farmState", {}, function (st, err) {
      if (err || !st) {
        farmSetStatus("读取状态失败：" + (err || "无返回"));
        farmTimer = setTimeout(farmPoll, 900);
        return;
      }
      if (st.error) {
        farmSetStatus("失败：" + st.error);
        farmLog("停止：" + st.error);
        farmReset();
        return;
      }
      if (!st.running) {
        farmSetStatus("已结束：" + (st.last || "完成"));
        farmLog("结束：" + (st.last || "完成"));
        farmReset();
        return;
      }
      var mode = String(st.mode || "");
      var text = mode === "auto" ? "自动循环" : (mode === "own" ? "收菜" : "偷菜/施肥");
      if (mode === "auto") {
        var phase = String(st.phase || "");
        text += phase === "visit" ? " · 好友农场"
          : ((phase === "enter" || phase === "leave") ? " · 切换农场" : " · 个人农场");
      }
      text += "：已处理 " + (Number(st.steps) || 0) + " 次";
      if (st.emptyPlots) text += " · 空地 " + st.emptyPlots + " 块";
      var counters = st.counters || {};
      if (mode !== "own" && counters.known) {
        text += " · 偷菜剩 " + (counters.steal || 0) + " · 施肥剩 " + (counters.fertilize || 0);
      }
      if (mode !== "visit") {
        if (st.ownMature) text += " · 成熟 " + st.ownMature + " 块";
        var nextAt = Number(st.nextOwnAt) || 0;
        var left = nextAt ? nextAt - Math.floor(Date.now() / 1000) : -1;
        if (left > 0) {
          var lh = Math.floor(left / 3600);
          var lm = Math.floor((left % 3600) / 60);
          var ls = left % 60;
          text += " · 下次成熟 " + (lh ? lh + ":" : "") +
            (lm < 10 ? "0" + lm : lm) + ":" + (ls < 10 ? "0" + ls : ls);
        }
      }
      if (st.last) text += " · " + st.last;
      farmSetStatus(text);
      if (st.last && st.last !== farmLast) {
        farmLast = st.last;
        farmLog(st.last);
      }
      farmTimer = setTimeout(farmPoll, 700);
    });
  }

  function farmStart(mode, retry) {
    retry = Number(retry) || 0;
    if (farmRunning) {
      farmStop();
      setTimeout(function () { farmStart(mode, retry); }, 350);
      return;
    }
    gameRpcJson("farmStart", { mode: mode }, function (result, error) {
      if (error || !result) {
        if (retry < 8) {
          farmSetStatus("正在连接游戏运行时…");
          setTimeout(function () { farmStart(mode, retry + 1); }, 700);
          return;
        }
        farmSetStatus("启动失败：" + (error || "游戏没有返回"));
        farmLog("启动失败：" + (error || "游戏没有返回"));
        return;
      }
      if (!result.ok) {
        farmSetStatus(result.message || "启动失败");
        farmLog(result.message || "启动失败");
        return;
      }
      farmRunning = true;
      farmLast = "";
      farmSetStatus("运行中…");
      farmLog("开始：" + (mode === "auto" ? "自动循环" : (mode === "own" ? "收自己的菜" : "偷菜/施肥")));
      farmPoll();
    });
  }

  function farmStop() {
    gameRpcJson("farmStop", {}, function () { /* ignore */ });
    farmReset();
    farmSetStatus("已停止");
    farmLog("已停止农场自动");
  }

  function setClickTp(on, retried) {
    clickTpOn = !!on;
    var check = $("blinkClickTp");
    if (check) check.checked = clickTpOn;

    gameRpcJson("clickTeleport", { on: clickTpOn }, function (res, error) {
      if (error || !res) {
        if (!retried) {
          $("blinkInfo").textContent = "正在连接游戏，稍后自动重试…";
          if (!injected) {
            try { inject(); } catch (e) { /* ignore */ }
          }
          setTimeout(function () { setClickTp(clickTpOn, true); }, 1200);
          return;
        }
        if (check) check.checked = clickTpOn;
        $("blinkInfo").textContent = clickTpOn
          ? "鼠标瞬移：已请求开启（游戏暂未返回结果），再点一次可关闭"
          : "鼠标瞬移：已请求关闭";
        return;
      }
      if (!res.ok) {
        clickTpOn = false;
        if (check) check.checked = false;
        $("blinkInfo").textContent = "鼠标瞬移开启失败：" + (res.error || "未知原因");
        return;
      }
      if (check) check.checked = clickTpOn;
      $("blinkInfo").textContent = clickTpOn
        ? "鼠标瞬移已开启：在游戏画面上点哪里就瞬移到哪里"
        : "鼠标瞬移已关闭";
    });
  }

  function stopAutoChest() {
    autoChestOn = false;
    if (autoChestTimer) { clearInterval(autoChestTimer); autoChestTimer = null; }
    var btn = $("fAutoChest");
    if (btn) {
      btn.classList.remove("on");
      btn.textContent = "开启";
    }
  }

  function toggleAutoChest() {
    if (autoChestOn) {
      stopAutoChest();
      setInfo("自动宝箱已关闭");
      return;
    }
    loadRunnerSource(function () {
      injectRunner();
      autoChestOn = true;
      var btn = $("fAutoChest");
      if (btn) {
        btn.classList.add("on");
        btn.textContent = "关闭";
      }
      setInfo("自动宝箱已开启：每 8 秒检查一次");
      var tick = function () {
        if (!autoChestOn) return;
        gameRpcJson("runStatus", {}, function (snap) {
          if (!autoChestOn || !snap || snap.running) return;
          gameRpcJson("runStart", { payload: "开宝箱()" }, function () { /* 结果由下次状态检查处理 */ });
        });
      };
      tick();
      autoChestTimer = setInterval(tick, 8000);
    });
  }

  function bind() {
    $("btnOpen").onclick = function () { openGame(); };
    $("btnFunc").onclick = function () { backToApp(); showPage("pageFunc"); };
    var sponsorBtn = $("btnSponsor");
    var sponsorMask = $("sponsorMask");
    if (sponsorBtn && sponsorMask) {
      sponsorBtn.onclick = function () { sponsorMask.classList.add("active"); };
    }
    if (sponsorMask) {
      sponsorMask.onclick = function (e) {
        if (e.target === sponsorMask) sponsorMask.classList.remove("active");
      };
    }
    var sponsorClose = $("sponsorClose");
    if (sponsorClose) {
      sponsorClose.onclick = function () {
        var mask = $("sponsorMask");
        if (mask) mask.classList.remove("active");
      };
    }
    var qqEl = $("homeQQ");
    if (qqEl) {
      qqEl.onclick = function () {
        if (!copyText("1090324996")) return;
        qqEl.textContent = "QQ群：1090324996（已复制）";
        setTimeout(function () { qqEl.textContent = "QQ群：1090324996（点击复制）"; }, 1500);
      };
    }
    $("btnBackHome").onclick = function () {
      if (backToGame()) return;
      showPage("pageHome");
    };
    $("fRed").onclick = function () {
      needLicense("地图 / 红包", function () { toggle("redPacket"); });
    };
    $("fRevive").onclick = function () {
      needLicense("复活 / 闪现", function () {
        var on = !!(state && state.revive && state.revive.enabled);
        revivePendingUntil = Date.now() + 1500;
        send("revive", on ? 0 : { mode: "tp", delay: 0 });
        var btn = $("fRevive");
        if (btn) {
          btn.classList.toggle("on", !on);
          btn.textContent = on ? "快速复活" : "关闭";
        }
        flashRevive(on ? "关闭成功" : "开启成功");
      });
    };
    $("fReviveNow").onclick = function () {
      needLicense("复活 / 闪现", function () {
        send("reviveNow", 1);
        flashRevive("手动复活成功");
      });
    };
    $("fMob").onclick = function () { toggle("mob"); };
    $("fBoss").onclick = function () { toggle("boss"); };
    var refreshMonstersBtn = $("fRefreshMonsters");
    if (refreshMonstersBtn) refreshMonstersBtn.onclick = function () {
      needLicense("复活 / 闪现", refreshMonsterList);
    };
    var blinkGoBtn = $("fBlinkGo");
    if (blinkGoBtn) blinkGoBtn.onclick = function () {
      needLicense("复活 / 闪现", blinkSelected);
    };
    var blinkFilter = $("blinkFilter");
    if (blinkFilter) blinkFilter.onchange = refreshMonsterList;
    var clickTpCheck = $("blinkClickTp");
    if (clickTpCheck) clickTpCheck.onchange = function () { setClickTp(!!clickTpCheck.checked); };
    var autoChestBtn = $("fAutoChest");
    if (autoChestBtn) autoChestBtn.onclick = function () {
      needLicense("地图 / 红包", toggleAutoChest);
    };
    var playDailyBtn = $("playDaily");
    if (playDailyBtn) playDailyBtn.onclick = function () { showFeature("daily"); };
    var playPlankBtn = $("playPlank");
    if (playPlankBtn) playPlankBtn.onclick = function () {
      needLicense("跳一跳", function () { showFeature("plank"); });
    };
    var plankRunBtn = $("plankRun");
    if (plankRunBtn) plankRunBtn.onclick = function () {
      needLicense("跳一跳", togglePlank);
    };
    var playTalismanBtn = $("playTalisman");
    if (playTalismanBtn) playTalismanBtn.onclick = function () { showFeature("talisman"); };
    var playGuildBtn = $("playGuild");
    if (playGuildBtn) playGuildBtn.onclick = function () {
      needLicense("公会情报", function () { showFeature("guild"); });
    };
    var playMazeBtn = $("playMaze");
    if (playMazeBtn) playMazeBtn.onclick = function () { showFeature("maze"); };
    var gQueryBtn = $("gQuery");
    if (gQueryBtn) gQueryBtn.onclick = function () {
      needLicense("公会情报", guildQuery);
    };
    var gCopyBtn = $("gCopy");
    if (gCopyBtn) gCopyBtn.onclick = function () {
      needLicense("公会情报", guildCopy);
    };
    var gTeamsBtn = $("gTeams");
    if (gTeamsBtn) gTeamsBtn.onclick = function () {
      needLicense("公会情报", function () { guildReadTeams(); });
    };
    var gExportBtn = $("gExport");
    if (gExportBtn) gExportBtn.onclick = function () {
      needLicense("公会情报", guildExport);
    };
    var gOpenBtn = $("gOpen");
    if (gOpenBtn) gOpenBtn.onclick = function () {
      needLicense("公会情报", guildOpenFile);
    };
    var gShareBtn = $("gShare");
    if (gShareBtn) gShareBtn.onclick = function () {
      needLicense("公会情报", guildShare);
    };
    var gPathBtn = $("gPath");
    if (gPathBtn) gPathBtn.onclick = function () {
      needLicense("公会情报", guildCopyPath);
    };
    var playFarmBtn = $("playFarm");
    if (playFarmBtn) playFarmBtn.onclick = function () { showFeature("farm"); };
    var farmAutoBtn = $("farmAuto");
    if (farmAutoBtn) farmAutoBtn.onclick = function () { farmStart("auto"); };
    var farmOwnBtn = $("farmOwn");
    if (farmOwnBtn) farmOwnBtn.onclick = function () { farmStart("own"); };
    var farmVisitBtn = $("farmVisit");
    if (farmVisitBtn) farmVisitBtn.onclick = function () { farmStart("visit"); };
    var farmStopBtn = $("farmStop");
    if (farmStopBtn) farmStopBtn.onclick = farmStop;
    var mQueryBtn = $("mQuery");
    if (mQueryBtn) mQueryBtn.onclick = function () { queryMaze(true); };
    var mCopyBtn = $("mCopy");
    if (mCopyBtn) mCopyBtn.onclick = mazeCopy;
    var homeVer = $("homeVer");
    if (homeVer) {
      var rv = (String(window.ZZ_RUNTIME || "").match(/const VERSION = (\d+);/) || [])[1] || "";
      homeVer.textContent = "脚本版本 " + (window.ZZ_APP_VERSION || "—")
        + (rv ? (" · 运行时 " + rv) : "");
    }

    $("fMob").onclick = function () {
      needLicense("地图 / 红包", function () {
        var on = !!(state && state.mob && state.mob.enabled);
        send("mob", on ? 0 : numVal("mobSec", 1));
      });
    };
    $("fBoss").onclick = function () {
      needLicense("地图 / 红包", function () {
        var on = !!(state && state.boss && state.boss.enabled);
        send("boss", on ? 0 : numVal("bossSec", 3));
      });
    };
    var wishBtn = $("fWish");
    if (wishBtn) wishBtn.onclick = function () {
      needLicense("地图 / 红包", function () { toggle("wish"); });
    };

    var reBtn = $("btnReconnect");
    if (reBtn) reBtn.onclick = function () {
      injected = false;
      injectTries = 0;
      setStatus("正在重新连接游戏窗口…");
      inject();
    };

    var stopBtn = $("btnStopAll");
    if (stopBtn) stopBtn.onclick = function () {
      stopAutoChest();
      try {
        evalGame("(window.__zzAssist&&window.__zzAssist.autoJumpStop" +
          "&&window.__zzAssist.autoJumpStop(),'ok')");
      } catch (e) { /* ignore */ }
      plankReset();
      plankSetStatus("已停止");
      evalGame("(window.__zzAssist&&window.__zzAssist.stopAll&&window.__zzAssist.stopAll(),'ok')");
      setStatus("已全部关闭");
      render();
    };

    var dmgBtn = $("fDmg");
    if (dmgBtn) dmgBtn.onclick = function () { toggleCombat("damage", numVal("dmgVal", 2), 0); };
    var shieldBtn = $("fShield");
    if (shieldBtn) shieldBtn.onclick = function () { toggleCombat("shield", numVal("shieldVal", 100), 0); };
    var cdBtn = $("fCd");
    if (cdBtn) cdBtn.onclick = function () { toggleCombat("cooldown", numVal("cdVal", 3), 0); };
    var visionBtn = $("fVision");
    if (visionBtn) visionBtn.onclick = function () {
      var sel = $("visionVal");
      var mode = sel ? Number(sel.value) : 0;
      send("vision", mode);
      setTimeout(poll, 200);
    };

    buildHeroList();
    // 兜底看门狗：请求回调万一丢了，也能把统计轮询重新拉起来
    setInterval(function () {
      if (gachaRunning && !gachaPollTimer && !gachaPollBusy) startGachaPoll();
    }, 3000);

    var gStart = $("gStart");
    if (gStart) gStart.onclick = startGachaRun;

    var gClear = $("gClear");
    if (gClear) gClear.onclick = clearGachaStats;

    var gAll = $("gAll");
    if (gAll) gAll.onclick = function () { setAllGacha(true); };

    var gNone = $("gNone");
    if (gNone) gNone.onclick = function () { setAllGacha(false); };

    var gReload = $("gReload");
    if (gReload) gReload.onclick = reloadGachaHeroes;

    // ---------- 运行命令 ----------
    var cmdText = $("cmdText");
    var cmdInfo = $("cmdInfo");
    var cmdTimer = null;
    var CMD_KEY = "zz_cmd_text";
    var CMD_SLOT_KEY = "zz_cmd_slots_v1";
    var CMD_SLOT_COUNT = 10;
    var CMD_DEFAULT = "# 运行命令：一行一条，# 开头是注释\n" +
      "# 点击(50.00,50.00)\n# 等待(1)\n# 移动(上,2)\n" +
      "# 不回城也能直接传送：传送(章节,地图)，例如 传送(1,2)\n";

    function cmdStatus(text) {
      if (cmdInfo) cmdInfo.textContent = text;
      setInfo(text);
    }

    function cmdSlotTitle(content) {
      var lines = String(content || "").split(/\r?\n/);
      for (var i = 0; i < lines.length; i += 1) {
        var line = lines[i].trim();
        if (line.charAt(0) === "#") {
          var text = line.replace(/^#+/, "").replace(/^-+/, "").trim();
          if (text) return text;
        } else if (line) break;
      }
      return "";
    }

    function cmdSlotLabel(slot, index) {
      var content = String((slot && slot.content) || "");
      var title = String((slot && slot.title) || "").trim() || cmdSlotTitle(content) || "未命名";
      if (!content.trim()) {
        return String((slot && slot.title) || "").trim()
          ? "槽位 " + (index + 1) + " · " + title + " · 空"
          : "槽位 " + (index + 1) + " · 空";
      }
      var lines = content.split(/\r?\n/).filter(function (line) {
        var t = line.trim();
        return t && t.charAt(0) !== "#";
      }).length;
      return "槽位 " + (index + 1) + " · " + title + " [" + lines + "步]";
    }

    function loadCmdSlots() {
      var slots = [];
      for (var i = 0; i < CMD_SLOT_COUNT; i += 1) slots.push({ title: "", content: "" });
      var data = null;
      try { data = JSON.parse(localStorage.getItem(CMD_SLOT_KEY) || "null"); } catch (e) { data = null; }
      if (data && data.slots && data.slots.length) {
        for (var j = 0; j < CMD_SLOT_COUNT && j < data.slots.length; j += 1) {
          var row = data.slots[j] || {};
          slots[j] = {
            title: String(row.title || ""),
            content: String(row.content || "")
          };
        }
        var active = Math.round(Number(data.active) || 1);
        return { slots: slots, active: Math.min(Math.max(active, 1), CMD_SLOT_COUNT) };
      }
      var legacy = "";
      try { legacy = localStorage.getItem(CMD_KEY) || ""; } catch (e) { legacy = ""; }
      slots[0] = { title: cmdSlotTitle(legacy), content: legacy || CMD_DEFAULT };
      return { slots: slots, active: 1 };
    }

    var cmdSlotData = loadCmdSlots();
    var cmdSlots = cmdSlotData.slots;
    var cmdActive = cmdSlotData.active - 1;
    var cmdSave = $("cmdSave");
    var cmdNew = $("cmdNew");
    var cmdRename = $("cmdRename");
    var cmdClear = $("cmdClear");
    var cmdRecord = $("cmdRecord");

    function saveCmdSlots() {
      try {
        localStorage.setItem(CMD_SLOT_KEY, JSON.stringify({
          active: cmdActive + 1,
          slots: cmdSlots
        }));
      } catch (e) { /* ignore */ }
    }

    function refreshCmdSlots(selectOnly) {
      if (!cmdText) return;
      if (!selectOnly) {
        cmdText.value = String((cmdSlots[cmdActive] && cmdSlots[cmdActive].content) || "");
      }
    }

    function saveEditorToSlot() {
      if (!cmdText) return;
      var content = cmdText.value;
      var oldTitle = String((cmdSlots[cmdActive] && cmdSlots[cmdActive].title) || "");
      cmdSlots[cmdActive] = {
        title: oldTitle || cmdSlotTitle(content),
        content: content
      };
      try { localStorage.setItem(CMD_KEY, content); } catch (e) { /* ignore */ }
    }

    var CMD_CATEGORY_NAMES = { 1: "采集", 2: "日常", 3: "其他", 5: "刷怪" };
    var cmdCategory = $("cmdCategory");
    var cmdPreset = $("cmdPreset");
    var cmdPresets = [];
    var cmdVisiblePresets = [];
    var cmdCategoryId = 0;
    var cmdPresetLoaded = false;

    function cmdCategoryName(id) {
      return CMD_CATEGORY_NAMES[id] || ("分类" + id);
    }

    function buildCmdPresets() {
      cmdPresets = [];
      (window.ZZ_SYNC_PRESETS || []).forEach(function (it) {
        cmdPresets.push({
          title: it.title || "未命名指令",
          content: it.content || "",
          category: Number(it.category) || 3
        });
      });
      (window.ZZ_INSTRUCTIONS || []).forEach(function (it) {
        cmdPresets.push({
          title: it.title || "未命名指令",
          content: it.content || "",
          category: Number(it.category) || 3
        });
      });
    }

    function refreshCmdCategoryOptions() {
      if (!cmdCategory) return;
      var ids = [0];
      cmdPresets.forEach(function (it) {
        var cid = Number(it.category) || 3;
        if (ids.indexOf(cid) < 0) ids.push(cid);
      });
      if (ids.indexOf(-1) < 0) ids.push(-1);
      ids.sort(function (a, b) {
        if (a === 0) return -1;
        if (b === 0) return 1;
        if (a === -1) return 1;
        if (b === -1) return -1;
        return a - b;
      });
      cmdCategory.innerHTML = "";
      ids.forEach(function (id) {
        var op = document.createElement("option");
        op.value = String(id);
        op.textContent = id === 0 ? "全部" : (id === -1 ? "我的" : cmdCategoryName(id));
        cmdCategory.appendChild(op);
      });
      cmdCategory.value = String(cmdCategoryId);
    }

    function refreshCmdPresetOptions() {
      if (!cmdPreset) return;
      cmdVisiblePresets = [];
      cmdPreset.innerHTML = "";
      if (cmdCategoryId === -1) {
        cmdSlots.forEach(function (slot, index) {
          var label = cmdSlotLabel(slot, index);
          cmdVisiblePresets.push({
            slotIndex: index,
            title: label,
            content: String((slot && slot.content) || "")
          });
          var op = document.createElement("option");
          op.value = String(index);
          op.textContent = label;
          cmdPreset.appendChild(op);
        });
        cmdPreset.value = String(cmdActive);
        return;
      }
      cmdPresets.forEach(function (it) {
        if (!String(it.content || "").trim()) return;
        var cid = Number(it.category) || 3;
        if (cmdCategoryId && cid !== cmdCategoryId) return;
        var idx = cmdVisiblePresets.length;
        cmdVisiblePresets.push(it);
        var op = document.createElement("option");
        op.value = String(idx);
        op.textContent = it.title || "未命名指令";
        cmdPreset.appendChild(op);
      });
      cmdPreset.value = "";
    }

    function selectCmdSlot(index) {
      cmdActive = Math.min(Math.max(Number(index) || 0, 0), CMD_SLOT_COUNT - 1);
      refreshCmdSlots();
      saveCmdSlots();
      refreshCmdPresetOptions();
      cmdStatus("当前：" + cmdSlotLabel(cmdSlots[cmdActive], cmdActive));
    }

    if (cmdText) {
      cmdText.oninput = function () {
        cmdPresetLoaded = false;
        saveEditorToSlot();
        saveCmdSlots();
        if (cmdCategoryId === -1) {
          try {
            cmdPreset.options[cmdActive].textContent = cmdSlotLabel(cmdSlots[cmdActive], cmdActive);
          } catch (e) { /* ignore */ }
        }
      };
    }

    if (cmdCategory) {
      cmdCategory.onchange = function () {
        cmdCategoryId = Number(cmdCategory.value);
        if (isNaN(cmdCategoryId)) cmdCategoryId = 0;
        refreshCmdPresetOptions();
      };
    }

    if (cmdSave) {
      cmdSave.onclick = function () {
        cmdPresetLoaded = false;
        saveEditorToSlot();
        saveCmdSlots();
        refreshCmdPresetOptions();
        cmdStatus("已保存：" + cmdSlotLabel(cmdSlots[cmdActive], cmdActive));
      };
    }
    if (cmdNew) {
      cmdNew.onclick = function () {
        var empty = -1;
        for (var i = 0; i < cmdSlots.length; i += 1) {
          if (!String(cmdSlots[i].content || "").trim()) { empty = i; break; }
        }
        if (empty < 0) { cmdStatus("10 个槽位都满了，先清空一个"); return; }
        var name = window.prompt("新脚本名称", "未命名");
        if (name === null) return;
        if (!cmdPresetLoaded) saveEditorToSlot();
        cmdPresetLoaded = false;
        cmdSlots[empty] = { title: String(name || "").trim() || "未命名", content: "" };
        cmdActive = empty;
        cmdCategoryId = -1;
        refreshCmdCategoryOptions();
        refreshCmdSlots();
        refreshCmdPresetOptions();
        saveCmdSlots();
        cmdStatus("已新建：槽位 " + (empty + 1));
      };
    }
    if (cmdRename) {
      cmdRename.onclick = function () {
        var current = String((cmdSlots[cmdActive] && cmdSlots[cmdActive].title) || "");
        var name = window.prompt("重命名槽位 " + (cmdActive + 1), current);
        if (name === null) return;
        name = String(name).trim();
        if (!name) return;
        cmdSlots[cmdActive].title = name;
        saveCmdSlots();
        refreshCmdPresetOptions();
        cmdStatus("已重命名：" + name);
      };
    }
    if (cmdClear) {
      cmdClear.onclick = function () {
        if (!window.confirm("清空 " + cmdSlotLabel(cmdSlots[cmdActive], cmdActive) + "？")) return;
        cmdPresetLoaded = false;
        cmdSlots[cmdActive] = {
          title: String((cmdSlots[cmdActive] && cmdSlots[cmdActive].title) || ""),
          content: ""
        };
        refreshCmdSlots();
        saveCmdSlots();
        refreshCmdPresetOptions();
        cmdStatus("已清空：槽位 " + (cmdActive + 1));
      };
    }
    if (cmdPreset) {
      cmdPreset.onchange = function () {
        if (!cmdPreset.value) return;
        var item = cmdVisiblePresets[Number(cmdPreset.value)];
        if (!item) return;
        if (item.slotIndex !== undefined) {
          if (Number(item.slotIndex) !== cmdActive) {
            if (!cmdPresetLoaded) saveEditorToSlot();
            cmdPresetLoaded = false;
            selectCmdSlot(item.slotIndex);
          }
          return;
        }
        if (!cmdText) return;
        cmdText.value = String(item.content || "");
        cmdPresetLoaded = true;
        cmdStatus("已载入：" + item.title + "（点保存写入当前槽位）");
      };
    }

    buildCmdPresets();
    refreshCmdCategoryOptions();
    refreshCmdSlots();
    refreshCmdPresetOptions();

    function startCmd() {
      if (!game) { cmdStatus("先打开游戏"); return; }
      if (!injected) { cmdStatus("游戏还没连上，等状态变成已注入再试"); return; }
      if (!build || !(window.ZZ_MAPPING && window.ZZ_MAPPING.builds && window.ZZ_MAPPING.builds[build])) {
        cmdStatus("游戏版本 " + (build || "未知") + " 未适配运行命令，请先更新映射");
        return;
      }
      saveEditorToSlot();
      cmdPresetLoaded = false;
      saveCmdSlots();
      var payload = { slots: {}, start: cmdActive + 1 };
      cmdSlots.forEach(function (slot, index) {
        var text = String((slot && slot.content) || "");
        if (text.trim()) payload.slots[index + 1] = text;
      });
      var content = cmdText ? cmdText.value : "";
      if (!content.trim()) { cmdStatus("先写点指令"); return; }
      if (!payload.slots[payload.start]) { cmdStatus("当前槽位是空的"); return; }
      if (!runnerSource) {
        cmdStatus("正在准备运行命令组件…");
        loadRunnerSource(function () {
          if (runnerSource) startCmd();
          else cmdStatus("运行命令组件加载失败");
        });
        return;
      }
      try { localStorage.setItem(CMD_KEY, content); } catch (e) { /* ignore */ }
      // 运行命令时把游戏切到前台，否则模拟触摸/传送在隐藏的 webview 里可能不生效
      if (game) backToGame();
      loadRunnerSource(function () {
        injectRunner();
        setTimeout(function () {
          gameRpcJson("runStart", { payload: payload }, function (result, error) {
            if (error || !result || !result.ok) {
              cmdStatus((result && result.message) || error || "启动失败");
              return;
            }
            cmdStatus("运行中…");
            cmdPoll();
          });
        }, 400);
      });
    }

    function cmdPoll() {
      if (cmdTimer) { clearTimeout(cmdTimer); cmdTimer = null; }
      gameRpcJson("runStatus", {}, function (st, error) {
        if (error || !st) {
          cmdTimer = setTimeout(cmdPoll, 900);
          return;
        }
        if (st.running) {
          cmdStatus(st.text || st.name || "运行中…");
          cmdTimer = setTimeout(cmdPoll, 700);
          return;
        }
        if (st.error) cmdStatus("运行结束：" + st.error);
        else cmdStatus("运行结束");
      });
    }

    var cmdRun = $("cmdRun");
    if (cmdRun) cmdRun.onclick = startCmd;
    var cmdStop = $("cmdStop");
    if (cmdStop) cmdStop.onclick = function () {
      gameRpcJson("runStop", {}, function () { /* ignore */ });
      if (cmdTimer) { clearTimeout(cmdTimer); cmdTimer = null; }
      cmdStatus("已停止");
    };

    var cats = [
      ["catMap", "secMap"],
      ["catRevive", "secRevive"],
      ["catGacha", "secGacha"],
      ["catCmd", "secCmd"],
      ["catPlay", "secPlay"]
    ];
    cats.forEach(function (pair) {
      var btn = $(pair[0]);
      var sec = $(pair[1]);
      if (!btn || !sec) return;
      btn.onclick = function () {
        var run = function () {
          var willOpen = !sec.classList.contains("open");
          cats.forEach(function (p2) {
            var b2 = $(p2[0]);
            var s2 = $(p2[1]);
            if (s2) s2.classList.remove("open");
            if (b2) b2.classList.remove("sel");
          });
          if (pair[0] !== "catPlay") {
            if (guildTimer) { clearInterval(guildTimer); guildTimer = null; }
            if (mazeTimer) { clearInterval(mazeTimer); mazeTimer = null; }
          }
          if (willOpen) {
            sec.classList.add("open");
            btn.classList.add("sel");
            if (pair[0] === "catGacha") gachaStats();
          }
        };
        if (pair[0] === "catMap") needLicense("地图 / 红包", run);
        else if (pair[0] === "catRevive") needLicense("复活 / 闪现", run);
        else run();
      };
    });
    $("fClear").onclick = function () {
      needLicense("复活 / 闪现", function () { send("clearStuck", 1); flashRevive("清除成功"); });
    };
    $("btnListBack").onclick = function () { showPage("pageFunc"); };
    var listRefreshBtn = $("btnListRefresh");
    if (listRefreshBtn) listRefreshBtn.onclick = refreshMonsterList;

    plus.key.addEventListener("backbutton", function () {
      if ($("list").classList.contains("active")) { showPage("pageFunc"); return; }
      if (backToGame()) return;
      if ($("pageFunc").classList.contains("active")) { showPage("pageHome"); return; }
      plus.runtime.quit();
    });
  }

  function plusready() {
    try { plus.screen.lockOrientation("portrait-primary"); } catch (e) { /* ignore */ }
    // 挂机时保持屏幕常亮，并定时补一次（部分系统会自己清掉唤醒锁）
    keepAwake(true);
    setInterval(function () { if (!document.hidden) keepAwake(true); }, 30000);
    document.addEventListener("resume", function () { keepAwake(true); }, false);
    document.addEventListener("pause", function () { keepAwake(false); }, false);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) keepAwake(true);
    }, false);
    cleanupStaleWebviews();
    loadRunnerSource();
    if (window.ZZLicense) {
      window.ZZLicense.init({ evalGame: evalGame, copyText: copyText });
    }
    bind();
    showPage("pageHome");
  }

  if (window.plus) plusready();
  else document.addEventListener("plusready", plusready, false);
})();
