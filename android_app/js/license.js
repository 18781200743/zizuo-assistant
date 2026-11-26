/* 小太阳脚本 · 安卓版密钥
 * 规则与 app/license.py 完全一致：
 *   XS-XXXX-XXXX-XXXX  绑定游戏 ID
 *   SS-XXXX-XXXX-YYYY  超级密钥，不绑 ID
 * 每个游戏 ID 的密钥分开保存，切号自动识别。
 */
(function (global) {
  var SECRET = "xiaotaiyang-2026-11-26-v1";
  var SUPER_SECRET = "xiaotaiyang-super-2026-11-26-v1";
  var STORE_KEY = "zz_license_v1";
  var ROLE_STORE_KEY = "zz_roleid_v1";

  var evalFn = null;
  var copyFn = null;
  var bound = false;
  var pendingNext = null;
  var idCache = { at: 0, id: "" };

  // ---------- SHA-256（与 tools/keygen.html 同款，纯 JS，无需联网） ----------
  function sha256Hex(ascii) {
    function rightRotate(v, a) { return (v >>> a) | (v << (32 - a)); }
    var mathPow = Math.pow, maxWord = mathPow(2, 32);
    var result = "", words = [], asciiBitLength = ascii.length * 8;
    var hash = sha256Hex.h = sha256Hex.h || [];
    var k = sha256Hex.k = sha256Hex.k || [];
    var primeCounter = k.length, isComposite = {};
    for (var candidate = 2; primeCounter < 64; candidate++) {
      if (!isComposite[candidate]) {
        for (var i = 0; i < 313; i += candidate) isComposite[i] = candidate;
        hash[primeCounter] = (mathPow(candidate, 0.5) * maxWord) | 0;
        k[primeCounter++] = (mathPow(candidate, 1 / 3) * maxWord) | 0;
      }
    }
    ascii += "\x80";
    while (ascii.length % 64 - 56) ascii += "\x00";
    for (i = 0; i < ascii.length; i++) {
      var j = ascii.charCodeAt(i);
      if (j >> 8) return ""; // ASCII only
      words[i >> 2] |= j << ((3 - i) % 4) * 8;
    }
    words[words.length] = (asciiBitLength / maxWord) | 0;
    words[words.length] = asciiBitLength;
    for (j = 0; j < words.length;) {
      var w = words.slice(j, j += 16);
      var oldHash = hash;
      hash = hash.slice(0, 8);
      for (i = 0; i < 64; i++) {
        var w15 = w[i - 15], w2 = w[i - 2];
        var a = hash[0], e = hash[4];
        var temp1 = hash[7]
          + (rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25))
          + ((e & hash[5]) ^ ((~e) & hash[6]))
          + k[i]
          + (w[i] = (i < 16) ? w[i] : (
              w[i - 16]
              + (rightRotate(w15, 7) ^ rightRotate(w15, 18) ^ (w15 >>> 3))
              + w[i - 7]
              + (rightRotate(w2, 17) ^ rightRotate(w2, 19) ^ (w2 >>> 10))
            ) | 0
          );
        var temp2 = (rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22))
          + ((a & hash[1]) ^ (a & hash[2]) ^ (hash[1] & hash[2]));
        hash = [(temp1 + temp2) | 0].concat(hash);
        hash[4] = (hash[4] + temp1) | 0;
      }
      for (i = 0; i < 8; i++) hash[i] = (hash[i] + oldHash[i]) | 0;
    }
    for (i = 0; i < 8; i++) {
      for (j = 3; j + 1; j--) {
        var b = (hash[i] >> (j * 8)) & 255;
        result += ((b < 16) ? "0" : "") + b.toString(16);
      }
    }
    return result.toUpperCase();
  }

  // ---------- 密钥算法 ----------
  function norm(text) {
    return String(text || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
  }

  function makeKey(gameId) {
    var id = String(gameId || "").replace(/\s+/g, "");
    if (!id) return "";
    var digest = sha256Hex(SECRET + ":" + id);
    if (!digest) return "";
    return "XS-" + digest.slice(0, 4) + "-" + digest.slice(4, 8) + "-" + digest.slice(8, 12);
  }

  function isSuperKey(text) {
    var n = norm(text);
    if (!/^SS[0-9A-F]{12}$/.test(n)) return false;
    var nonce = n.slice(2, 10);
    var sig = n.slice(10, 14);
    var expect = sha256Hex("xiaotaiyang-super:" + SUPER_SECRET + ":" + nonce).slice(0, 4);
    return sig === expect;
  }

  function isValidKey(text, gameId) {
    if (!text) return false;
    if (isSuperKey(text)) return true;
    var id = String(gameId || "").replace(/\s+/g, "");
    if (!id) return false;
    return norm(text) === norm(makeKey(id));
  }

  // ---------- 存储 ----------
  function readRaw(key) {
    try {
      if (global.plus && global.plus.storage) return global.plus.storage.getItem(key) || "";
    } catch (e) { /* ignore */ }
    try { return global.localStorage ? (global.localStorage.getItem(key) || "") : ""; } catch (e) { return ""; }
  }

  function writeRaw(key, value) {
    try {
      if (global.plus && global.plus.storage) global.plus.storage.setItem(key, value);
    } catch (e) { /* ignore */ }
    try { if (global.localStorage) global.localStorage.setItem(key, value); } catch (e) { /* ignore */ }
  }

  function loadStore() {
    var data = null;
    var raw = readRaw(STORE_KEY);
    if (raw) {
      try { data = JSON.parse(raw); } catch (e) { data = null; }
    }
    if (!data || typeof data !== "object") data = {};
    if (!data.keys || typeof data.keys !== "object") data.keys = {};
    return data;
  }

  function saveStore(data) {
    writeRaw(STORE_KEY, JSON.stringify(data));
  }

  function cleanId(value) {
    var s = String(value || "").trim();
    if (s.length >= 2 && s.charAt(0) === '"' && s.charAt(s.length - 1) === '"') {
      try { s = JSON.parse(s); } catch (e) { s = s.slice(1, -1); }
    }
    return String(s || "").replace(/["'\s]/g, "");
  }

  function lastId(data) {
    return cleanId(data && data.lastId);
  }

  // ---------- 读取游戏角色 ID ----------
  var ROLE_CODE =
    "(function(){try{" +
    "for (const p of window.System.entries()) {" +
    "var e=p[1]; if(!e||typeof e!=='object') continue;" +
    "var m=e.HBJS||e; if(m&&typeof m==='object'&&m.GZZR) return String(m.GZZR);" +
    "}" +
    "for (const p of window.System.entries()) {" +
    "var e2=p[1]; if(!e2||typeof e2!=='object') continue;" +
    "var m2=e2.HBJS||e2; if(m2&&typeof m2==='object'&&m2.XRFZ) return String(m2.XRFZ);" +
    "}" +
    "var rid=window.localStorage.getItem('RoleID')||window.localStorage.getItem('roleId');" +
    "if(rid) return String(rid);" +
    "return String(window.localStorage.getItem('OpenID')||'');" +
    "}catch(e){return ''}})()";

  var STORE_CODE =
    "(function(){try{var rid=" + ROLE_CODE + ";" +
    "if(rid){try{window.plus.storage.setItem('" + ROLE_STORE_KEY + "',String(rid));" +
    "window.plus.storage.setItem('" + ROLE_STORE_KEY + "_at',String(Date.now()));}catch(e){}}" +
    "}catch(e){}})()";

  function currentRoleId() {
    var now = Date.now();
    if (idCache.id && now - idCache.at < 1200) return idCache.id;
    var id = "";
    if (evalFn) {
      try { id = cleanId(evalFn(ROLE_CODE)); } catch (e) { id = ""; }
      if (!id) {
        try { evalFn(STORE_CODE); } catch (e) { /* ignore */ }
        try {
          var v = cleanId(readRaw(ROLE_STORE_KEY));
          var at = parseInt(readRaw(ROLE_STORE_KEY + "_at"), 10) || 0;
          if (v && now - at < 8000) id = v;
        } catch (e) { /* ignore */ }
      }
    }
    idCache = { at: id ? now : 0, id: id };
    return id;
  }

  // ---------- 解锁判断 ----------
  function isUnlocked(gameId) {
    var data = loadStore();
    if (isSuperKey(data.superKey || "")) return true;
    var id = cleanId(gameId || "") || lastId(data);
    if (!id) return false;
    var key = data.keys[id];
    if (!key && lastId(data) === id && data.key) key = data.key; // 兼容旧字段
    if (!key) return false;
    return isValidKey(key, id);
  }

  // ---------- 页面 ----------
  function setStatus(text, ok) {
    var el = document.getElementById("licStatus");
    if (!el) return;
    el.textContent = text;
    el.className = "lic-status" + (ok ? " ok" : "");
  }

  function refreshHome() {
    var btn = document.getElementById("btnLicense");
    if (!btn) return;
    var unlocked = isUnlocked(currentRoleId());
    btn.textContent = unlocked ? "已解锁" : "密钥";
    if (unlocked) btn.classList.add("ok");
    else btn.classList.remove("ok");
  }

  function openLicense(opts) {
    bindUI();
    pendingNext = (opts && opts.next) || null;
    var mask = document.getElementById("licMask");
    if (!mask) return;
    var data = loadStore();
    var id = currentRoleId();
    var idEl = document.getElementById("licId");
    var keyEl = document.getElementById("licKey");
    if (idEl) idEl.value = id;
    if (keyEl) keyEl.value = "";
    if (isSuperKey(data.superKey || "")) setStatus("已解锁（超级密钥）", true);
    else if (id && isUnlocked(id)) setStatus("此账号已绑定", true);
    else if (id) setStatus("此账号未绑定", false);
    else setStatus("请先进入游戏", false);
    mask.classList.add("active");
    setTimeout(function () {
      if (keyEl) {
        try { keyEl.focus(); } catch (e) { /* ignore */ }
      }
    }, 120);
  }

  function closeLicense(success) {
    var mask = document.getElementById("licMask");
    if (mask) mask.classList.remove("active");
    var next = success ? pendingNext : null;
    pendingNext = null;
    refreshHome();
    if (next) {
      setTimeout(function () {
        try { next(); } catch (e) { /* ignore */ }
      }, 220);
    }
  }

  function submit() {
    var data = loadStore();
    var idEl = document.getElementById("licId");
    var keyEl = document.getElementById("licKey");
    var id = currentRoleId() || cleanId(idEl ? idEl.value : "");
    var key = String(keyEl ? keyEl.value : "").trim();
    if (!key) {
      setStatus("请输入密钥", false);
      return;
    }
    if (!isValidKey(key, id)) {
      setStatus("密钥无效", false);
      return;
    }
    if (isSuperKey(key)) {
      data.superKey = key;
    } else {
      if (!id) {
        setStatus("请先进入游戏", false);
        return;
      }
      data.keys[id] = key;
    }
    data.lastId = id || lastId(data);
    data.unlocked = true;
    saveStore(data);
    idCache = { at: 0, id: id || "" };
    setStatus(isSuperKey(key) ? "已解锁（超级密钥）" : "已绑定此账号", true);
    setTimeout(function () { closeLicense(true); }, 450);
  }

  function copyId() {
    var el = document.getElementById("licId");
    var text = el ? String(el.value || "") : "";
    if (!text) return;
    if (copyFn) {
      try { copyFn(text); } catch (e) { /* ignore */ }
    } else {
      try {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      } catch (e) { /* ignore */ }
    }
    setStatus("游戏ID已复制", true);
  }

  function bindUI() {
    if (bound) return;
    var mask = document.getElementById("licMask");
    if (!mask) return;
    bound = true;
    var closeBtn = document.getElementById("licClose");
    var cancelBtn = document.getElementById("licCancel");
    var okBtn = document.getElementById("licOk");
    var copyBtn = document.getElementById("licCopy");
    var keyEl = document.getElementById("licKey");
    var homeBtn = document.getElementById("btnLicense");
    if (closeBtn) closeBtn.onclick = function () { closeLicense(false); };
    if (cancelBtn) cancelBtn.onclick = function () { closeLicense(false); };
    if (okBtn) okBtn.onclick = submit;
    if (copyBtn) copyBtn.onclick = copyId;
    if (keyEl) {
      keyEl.onkeyup = function (e) {
        if (e && (e.key === "Enter" || e.keyCode === 13)) submit();
      };
    }
    mask.onclick = function (e) {
      if (e.target === mask) closeLicense(false);
    };
    if (homeBtn) homeBtn.onclick = function () { openLicense({ next: null }); };
  }

  function init(opts) {
    opts = opts || {};
    if (opts.evalGame) {
      evalFn = opts.evalGame;
      idCache = { at: 0, id: "" };
    }
    if (opts.copyText) copyFn = opts.copyText;
    bindUI();
    refreshHome();
  }

  function ensure(name, next) {
    var id = currentRoleId();
    if (isUnlocked(id)) {
      next();
      return;
    }
    openLicense({ name: name, next: next });
  }

  global.ZZLicense = {
    init: init,
    ensure: ensure,
    open: openLicense,
    close: closeLicense,
    isUnlocked: isUnlocked,
    currentRoleId: currentRoleId,
    refreshHome: refreshHome,
    _makeKey: makeKey,
    _isSuperKey: isSuperKey,
    _isValidKey: isValidKey
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindUI, false);
  } else {
    setTimeout(bindUI, 0);
  }
})(window);
