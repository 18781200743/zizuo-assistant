"""注入游戏页的 JS 运行时（红包 / 祈愿领取 / 小怪·BOSS 死亡后重进地图 / BOSS 金币）。

为什么用 JS 注入而不是找图点击：
    游戏是 Cocos Creator 的 H5 包，聊天红包、地图实体、祈愿全是页面里的 JS 对象。
    直接在页面里调用游戏自己的服务函数，比截图找图快一个数量级，也不会因为窗口
    拖动/缩放而错位。

字段名的现实情况（重要）：
    这个游戏的每个业务类名、属性名在发版时都会被重新混淆成 4 位随机码
    （实测当前版本：服务注册表里的 903 个服务叫 UVLR / GWIV / OKLW / YCFT…，
    红包含义上的方法名是 CHWX / GMEZ，字段是 VTXW / FXZF / BABC），
    所以「按固定名字去游戏里找」是行不通的 —— 字段映射必须按游戏版本存。
    映射表在 app/assist_mapping.json，键是 assets/mainScripts/index.<build>.js 的哈希。

    只有两类东西是稳定的，运行时用来定位与自检：
      * 服务注册表：一个返回 new Map([["XXXX", cls.AWXW], ...]) 的工厂函数（当前 903 项）；
      * 大写下划线的常量键不会被混淆（CAN_OPEN/HAS_OPEN/OVER/INVALID、
        ACTIVITY_RED_PACKET_* 等），可以当锚点校验。

安全策略：
    * 映射先校验再用：注册表里没有该服务、或方法不存在，就明确报「需要该版本映射」，
      绝不在名字不匹配时乱调接口；
    * 红包按「编号去重 + 先查状态再领 + 两次领取之间留间隔」，不会重复提交；
    * 任何异常只停用一个功能，不影响其它功能，也不会打断游戏本身。
"""

from __future__ import annotations

RUNTIME_VERSION = 191
"""运行时代码版本：改 RUNTIME_JS 的行为时 +1。

页面里的旧运行时不会被自动覆盖（`window.__zzAssist` 已存在就直接复用），
所以只要改了注入代码或映射表结构，就必须 +1，否则窗口里跑的还是旧的。
"""

RUNTIME_JS = r"""
(() => {
  const VERSION = 191;

  // 同一页里可能还留着旧版本注入的运行时（改了代码但没关窗口），
  // 那套状态机会继续报旧错误，这里先把它停掉再重装。
  //
  // 只比版本号不够：改了运行时代码但忘了 +1 时，页面会一直留着旧代码，
  // 外层又以为「已是最新」，于是每次轮询都重发一次开关，
  // 把 BOSS/小怪 的死亡计时反复清零（表现为「BOSS 死了不刷新」）。
  // 所以再比一个内容指纹（由注入方写进 __zzAssistNextStamp）。
  const STAMP = typeof window.__zzAssistNextStamp === 'string' ? window.__zzAssistNextStamp : '';
  const previous = window.__zzAssist;
  if (previous && previous.version === VERSION && (!STAMP || previous.__stamp === STAMP)) return;
  if (previous) {
    try { if (previous.__timer) clearInterval(previous.__timer); } catch (_) { /* ignore */ }
    try { if (previous.__redTimer) clearInterval(previous.__redTimer); } catch (_) { /* ignore */ }
    try { if (typeof previous.stopAll === 'function') previous.stopAll(); } catch (_) { /* ignore */ }
    try { delete window.__zzAssist; } catch (_) { window.__zzAssist = undefined; }
  }
  const TICK_MS = 250;        // 主循环间隔（地图/祈愿/复活等）
  const RED_TICK_MS = 60;     // 红包专用高频轮询：尽量贴着 30~60ms 一帧
  const RETRY_MS = 3000;      // 解析失败的重试间隔
  const CLAIM_GAP_MS = 50;    // 两次领取之间的最小间隔，避免瞬间刷请求
  const RED_SCAN_MSG = 100;   // 每个频道扫满（游戏每个频道只留 100 条）：
                              // 实测聊天共 3 个频道 —— 世界 world_*、跨服 cross_*、公会 guild_*
  const RED_SEEN_MAX = 600;   // 已处理红包 id 的缓存上限，超出就清空重建
  const RED_STORE_KEY = 'zz_red_daily';  // 今日红包数/绿钻存在页面里，刷新页面不清零

  const MAPPING = window.__zzAssistMapping || { builds: {} };

  // 微信扫码登录：游戏在手机 UA 下不会返回 wxPcLoginLink，手机登录页就把
  // “其他登录方式 → 微信”隐藏了。这里用游戏 init 接口在 os=pc 时返回的官方
  // 扫码链接补上；state 用当前 H5 的 appid，游戏换 appid 时也能跟着变。
  const WECHAT_QR_APPID = 'wx565e856587e12457';
  const WECHAT_QR_REDIRECT = 'https%3A%2F%2Fsprogram.xnxykj.cn%2Fbox%2Fh5%2Fwxpc_login_redirect';
  let wechatLoginCheckedAt = 0;
  function wechatLoginLink() {
    let appid = '';
    try {
      const cfg = window.AladinConfig || {};
      appid = (cfg.appIdConfig && cfg.appIdConfig.h5) || '';
    } catch (_) { /* ignore */ }
    return 'https://open.weixin.qq.com/connect/qrconnect?appid=' + WECHAT_QR_APPID
      + '&redirect_uri=' + WECHAT_QR_REDIRECT
      + '&response_type=code&scope=snsapi_login&state='
      + encodeURIComponent(appid || 'wx77200645d1c7f35f') + '#wechat_redirect';
  }
  function ensureWechatLogin() {
    const at = Date.now();
    if (at - wechatLoginCheckedAt < 1000) return;
    wechatLoginCheckedAt = at;
    try {
      const frames = document.querySelectorAll('iframe');
      let phone = null;
      for (let i = 0; i < frames.length; i++) {
        if (String(frames[i].src || '').indexOf('phone.html') >= 0) { phone = frames[i]; break; }
      }
      if (!phone) return;
      const doc = phone.contentDocument;
      if (!doc) return;
      const section = doc.getElementById('other-login-section');
      if (!section) return;
      const link = window.__zzWechatLoginLink || wechatLoginLink();
      if (!link) return;
      if (localStorage.getItem('wxPcLoginLink') !== link) {
        localStorage.setItem('wxPcLoginLink', link);
      }
      if (section.style.display !== 'flex') section.style.display = 'flex';
    } catch (_) { /* 登录页结构变化时忽略 */ }
  }

  const now = () => Date.now();
  const msgOf = cause => (cause && cause.message) || String(cause);
  const today = () => {
    const d = new Date();
    const p = n => String(n).padStart(2, '0');
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  };

  const state = {
    ok: true,
    ready: false,
    error: '',
    missingMapping: false,
    build: '',
    message: '',
    reg: null,
    ctx: null,
    wishCtx: null,
    nextProbeAt: 0,
    mob: { enabled: false, seconds: 1, reason: '尚未开启' },
    boss: { enabled: false, seconds: 3, reason: '尚未开启' },
    red: {
      enabled: false, reason: '尚未开启', today: 0, date: '', claimed: 0, failed: 0,
      pending: 0, seen: Object.create(null), lastAt: 0, available: false,
      // 失败重试：id -> { tries, at }；seen[id] = -2 表示等重试
      retry: Object.create(null),
      lastCode: 0, lastFailId: '', failReason: '', hookId: '',
      // day：今天（用于跨天清零）；green：今日领到的绿钻；greenSeen：上一次读到的绿钻余额
      day: '', green: 0, greenSeen: null, greenUntil: 0,
      // exactGreen：从领取返回的 JWTZ 里按自己的 PHKL 精确累加；exactCount>0 时优先显示
      exactGreen: 0, exactCount: 0, credit: Object.create(null),
      lastAmount: 0, amountError: '',
    },
    wish: {
      enabled: false, reason: '尚未开启', claimed: 0,
      // null 表示还没确认过；false 是游戏明确说当前角色/活动不可用（界面据此置灰）。
      available: null, pending: false,
      // 下一个到期槽位的服务器秒数；0 表示等下一轮查询。
      dueAt: 0,
      // 各槽位的服务器结束秒数，供界面本地倒计时。
      waiting: [],
    },
    gold: { today: 0, date: '' },
    // 公会情报：查询我方/敌方据点驻守成员与三队总战力。
    guildIntel: { running: false, rows: [], error: '', done: 0, total: 0, camp: 0 },
    // 公会迷宫首领：保存最近一次查询到的首领快照。
    maze: { last: null },
    // 快速复活：全灭面板出现后自动复活（道具 / 广告 / 回营地），延迟可配
    revive: {
      enabled: false,
      mode: 'item',        // item=道具复活 / ad=广告复活 / camp=放弃回营地复活
      delayMs: 500,
      count: 0,
      reason: '尚未开启',
      phase: '',
      pendingAt: 0,
      nextTryAt: 0,
      busy: false,
      savedMapId: 0,
      lastResult: '',
      returnTo: null,
      returnNextAt: 0,
    },
    // 闪现寻怪：传送到最近的怪（或指定名字/编号的怪）
    blink: { busy: false, last: '', reason: '尚未使用' },
    // 回营地复活 + 飞回原位（参考脚本 gohome → goto → fly）
    campFly: {
      running: false,
      phase: '',
      savedMapId: 0,
      savedX: 0,
      savedY: 0,
      phaseAt: 0,
      startedAt: 0,
      reason: '',
      count: 0,
      lastResult: '',
    },
    // 单英雄复活时间：把每个我方英雄的「复活秒数」改成指定值（死一个 1 秒就原地站起来）
    heroRevive: {
      enabled: false,
      seconds: 1,
      ad: false,
      campfly: false,
      timer: false,
      heroes: 0,
      count: 0,
      busy: false,
      nextTryAt: 0,
      reason: '尚未开启',
    },
    // 战斗参数（参考工具同款）：默认全关，开了就要自己承担风险
    combat: {
      damage: { enabled: false, value: 1, reason: '尚未开启' },
      shield: { enabled: false, value: 0, reason: '尚未开启' },
      vision: { enabled: false, value: -1, reason: '尚未开启' },
      cooldown: { enabled: false, value: 0, reason: '尚未开启' },
    },
    map: {
      sceneId: 0,
      enterBlocked: false,
      // 死亡计时：实体编号 -> 第一次发现它死亡的时间戳（参考工具同样按单位计时）
      deathSince: Object.create(null),
      // 已经计过击杀的实体（重试重进时不要重复计数）
      deadCounted: Object.create(null),
      // 本 tick 看到的怪物：实体编号 -> info，只用于界面诊断
      seen: Object.create(null),
      // 自测用：直接排队的「已死亡」事件，下一轮 tick 当作真死处理
      fakeDeaths: [],
      // 到点该重进地图的时间戳（毫秒）；0 表示没在等
      mobAt: 0, bossAt: 0,
      lastEnterAt: 0, entering: false,
      // 这次等待是谁引起的（小怪 / BOSS），只用于界面提示
      pendingKind: '', lastAction: '', lastActionAt: 0, lastBossName: '',
      kills: { mob: 0, boss: 0 },
      // BOSS 死亡后开一个结算窗口，窗口内的金币增量才算到 BOSS 头上
      settleUntil: 0, bossGold: 0,
      goldLabel: null, goldLast: null, goldNextFindAt: 0,
    },
    timer: 0,
  };

  /* ------------------------------------------------------------------ *
   * 一、定位游戏：版本号 → 映射 → 服务注册表
   * ------------------------------------------------------------------ */

  /** 版本号 = assets/mainScripts/index.<hash>.js 的 hash。 */
  function detectBuild() {
    const pick = text => {
      const m = String(text || '').match(/mainScripts\/index\.([0-9a-z]+)\.js/i);
      return m ? m[1] : '';
    };
    try {
      const list = performance.getEntriesByType('resource') || [];
      for (const item of list) {
        const hit = pick(item && item.name);
        if (hit) return hit;
      }
    } catch (_) { /* ignore */ }
    try { return pick(location.href) || ''; } catch (_) { return ''; }
  }

  function mapping() {
    const builds = (MAPPING && MAPPING.builds) || {};
    if (state.build && builds[state.build]) return builds[state.build];
    return null;
  }

  /** 服务注册表：形如 e("XXXX", () => new Map([["NAME", cls.AWXW], ...])) 的工厂。 */
  function registry() {
    if (state.reg) return state.reg;
    const system = window.System;
    if (!system || typeof system.entries !== 'function') {
      throw Error('游戏尚未加载完成（未找到模块表），请进入游戏主界面后重试');
    }
    for (const pair of system.entries()) {
      const exports = pair && pair[1];
      if (!exports || (typeof exports !== 'object' && typeof exports !== 'function')) continue;
      let keys = [];
      try { keys = Object.getOwnPropertyNames(exports); } catch (_) { continue; }
      if (keys.length > 12) continue;
      for (const key of keys) {
        let value;
        try { value = exports[key]; } catch (_) { continue; }
        if (typeof value !== 'function') continue;
        let source = '';
        try { source = Function.prototype.toString.call(value); } catch (_) { continue; }
        // 注册表工厂的源码里是一长串 ["名字", xxx.AWXW]
        if (source.indexOf('new Map([["') < 0) continue;
        if ((source.match(/\],\["/g) || []).length < 100) continue;
        let map = null;
        try { map = value(); } catch (_) { continue; }
        if (map && typeof map.get === 'function' && typeof map.forEach === 'function' && map.size > 100) {
          state.reg = map;
          return map;
        }
      }
    }
    throw Error('找不到游戏服务注册表（游戏结构可能大改了）');
  }

  /** 取某个类的单例：单例访问器是继承来的静态 getter，名字每版不同，按结构找。 */
  function singletonOf(cls) {
    let cursor = cls;
    let guard = 0;
    while (cursor && guard < 8) {
      guard += 1;
      let names = [];
      try { names = Object.getOwnPropertyNames(cursor); } catch (_) { names = []; }
      for (const name of names) {
        if (name === 'prototype' || name === 'length' || name === 'name') continue;
        let desc = null;
        try { desc = Object.getOwnPropertyDescriptor(cursor, name); } catch (_) { continue; }
        if (!desc || typeof desc.get !== 'function') continue;
        let value = null;
        try { value = desc.get.call(cls); } catch (_) { continue; }
        if (value && typeof value === 'object') return value;
      }
      try { cursor = Object.getPrototypeOf(cursor); } catch (_) { cursor = null; }
    }
    return null;
  }

  function serviceClass(name) {
    const system = window.System;
    for (const pair of system.entries()) {
      const exports = pair && pair[1];
      if (!exports) continue;
      let value;
      try { value = exports[name]; } catch (_) { continue; }
      if (typeof value === 'function' && value.prototype) return value;
    }
    return null;
  }

  const hasMethod = (owner, key) => {
    if (!owner || !key) return false;
    try { return typeof owner[key] === 'function'; } catch (_) { return false; }
  };

  function resolve() {
    if (state.ctx) return state.ctx;
    if (!state.build) {
      // 页面还没加载完 / 还在登录页：这不是映射问题，别误报成「版本没映射」
      throw Error('游戏页面还没加载完成，进入游戏主界面后会自动连接');
    }
    const map = mapping();
    if (!map) {
      state.missingMapping = true;
      throw Error(
        '该游戏版本（' + state.build + '）没有字段映射：'
        + '游戏内部字段名每版都会重新随机，需要补一条映射才能启用'
      );
    }
    const reg = registry();
    const redCfg = map.redPacket || {};
    const chatCfg = map.chat || {};

    const redPacket = reg.get(redCfg.service);
    if (!redPacket || !hasMethod(redPacket, redCfg.status) || !hasMethod(redPacket, redCfg.claim)) {
      throw Error('映射里的红包服务（' + redCfg.service + '）在游戏里对不上，需要更新该版本的映射');
    }
    let chat = reg.get(chatCfg.service) || null;
    if (chat && !(chat[chatCfg.messages] && typeof chat[chatCfg.messages].forEach === 'function')) {
      chat = null;
    }

    let hub = null;
    const hubCfg = map.hub || {};
    if (hubCfg.service) {
      const cls = serviceClass(hubCfg.service);
      if (cls) hub = singletonOf(cls);
    }

    state.ctx = { map, reg, redPacket, chat, hub, redCfg, chatCfg };
    try { installRedHooks(); } catch (_) { /* 钩子失败不影响轮询兜底 */ }
    return state.ctx;
  }

  /* ------------------------------------------------------------------ *
   * 二、红包：收集聊天里的红包 → 查状态 → 领取
   * ------------------------------------------------------------------ */

  /** 游戏自己维护的「可领红包」快照 + 聊天消息里挂着的红包详情。 */
  function packetSources() {
    const ctx = state.ctx;
    const out = [];
    const redCfg = ctx.redCfg;

    if (redCfg.claimable) {
      try {
        const newest = ctx.redPacket[redCfg.claimable];
        if (newest && typeof newest === 'object') out.push(newest);
      } catch (_) { /* 取值器可能因为没数据抛错 */ }
    }

    if (ctx.chat) {
      const groups = ctx.chat[ctx.chatCfg.messages];
      try {
        groups.forEach(list => {
          if (!Array.isArray(list)) return;
          // 扫满整段（最多 100 条）：红包可能在 24 条以外，漏了就被别人抢走
          const start = Math.max(0, list.length - RED_SCAN_MSG);
          for (let i = list.length - 1; i >= start; i -= 1) {
            const message = list[i];
            const detail = message && message[ctx.chatCfg.detail];
            const info = detail && detail[ctx.chatCfg.info];
            if (info && typeof info === 'object') out.push(info);
          }
        });
      } catch (_) { /* 消息结构变了就直接用快照 */ }
    }
    return out;
  }

  /** 读绿钻余额：复用抽卡映射里的余额服务（AUJP.PKQL(88)），读不到返回 null。 */
  function greenBalance() {
    try {
      const map = mapping() || {};
      const g = map.gacha;
      if (!g || !g.refresh) return null;
      const system = window.System;
      const mod = system && typeof system.get === 'function' ? system.get(g.holderModule) : null;
      const holder = mod && g.holder ? mod[g.holder] : null;
      const svc = holder && holder[g.refresh.balanceService];
      const read = svc && svc[g.refresh.balance];
      if (typeof read !== 'function') return null;
      const value = Number(read.call(svc, g.refresh.greenPid || 88));
      return Number.isFinite(value) ? value : null;
    } catch (_) {
      return null;
    }
  }

  /** 每日计数落在页面 localStorage 里：页面刷新/工具重开都接着算，跨天自动归零。 */
  function loadRedDay() {
    const day = today();
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(RED_STORE_KEY) || 'null'); } catch (_) { saved = null; }
    const sameDay = saved && saved.date === day;
    state.red.day = day;
    state.red.date = day;
    state.red.today = sameDay ? Math.max(0, Number(saved.count) || 0) : 0;
    state.red.green = sameDay ? Math.max(0, Number(saved.green) || 0) : 0;
    state.red.exactGreen = sameDay ? Math.max(0, Number(saved.exactGreen) || 0) : 0;
    state.red.exactCount = sameDay ? Math.max(0, Number(saved.exactCount) || 0) : 0;
    state.red.greenSeen = greenBalance();
    saveRedDay();
    if (state.red.enabled) {
      state.red.reason = '今日已领 ' + state.red.today + ' 个红包 · ' + redGreenValue() + ' 绿钻';
    }
  }

  /** 工具那边落盘的今日统计灌回页面：页面刷新、换运行时后数字不会退回 0。 */
  function seedRedDay(data) {
    try {
      if (!data || typeof data !== 'object') return 0;
      const day = String(data.date || '');
      if (day && day !== today()) return 0;
      const count = Math.max(0, Number(data.count) || 0);
      rollRedDay();
      let changed = 0;
      if (count > state.red.today) { state.red.today = count; changed = 1; }
      if (changed) saveRedDay();
      if (state.red.enabled) {
        state.red.reason = '今日已领 ' + state.red.today + ' 个红包 · ' + redGreenValue() + ' 绿钻';
      }
      return changed;
    } catch (_) { return 0; }
  }

  function saveRedDay() {
    try {
      localStorage.setItem(RED_STORE_KEY, JSON.stringify({
        date: state.red.day, count: state.red.today, green: state.red.green,
        exactGreen: state.red.exactGreen, exactCount: state.red.exactCount,
      }));
    } catch (_) { /* 隐私模式写不进去就只记内存 */ }
  }

  function rollRedDay() {
    if (state.red.day !== today()) loadRedDay();
  }

  /** 显示用：有精确金额就用精确金额，没有才退回余额估算。 */
  function redGreenValue() {
    return state.red.exactCount > 0 ? state.red.exactGreen : state.red.green;
  }

  /** 当前登录角色 ID，用来从红包领取记录里找自己。 */
  function redSelfRoleId() {
    try {
      for (const pair of window.System.entries()) {
        const exp = pair && pair[1];
        if (!exp) continue;
        let keys;
        try { keys = Object.getOwnPropertyNames(exp); } catch (_) { continue; }
        for (let i = 0; i < keys.length; i += 1) {
          let value;
          try { value = exp[keys[i]]; } catch (_) { continue; }
          if (!value || typeof value !== 'object') continue;
          const model = value.HBJS || value;
          if (model && model.GZZR) return String(model.GZZR);
        }
      }
    } catch (_) { /* ignore */ }
    return '';
  }

  /** 精确记一个红包：同一个包只记一次。 */
  function creditRedAmount(id, amount) {
    if (!id) return 0;
    const credit = state.red.credit || (state.red.credit = Object.create(null));
    if (credit[id]) return 0;
    const value = Number(amount);
    if (!Number.isFinite(value) || value < 0) return 0;
    credit[id] = 1;
    rollRedDay();
    state.red.exactGreen = Math.max(0, Number(state.red.exactGreen || 0) + value);
    state.red.exactCount = Math.max(0, Number(state.red.exactCount || 0) + 1);
    state.red.lastAmount = value;
    saveRedDay();
    if (state.red.enabled) {
      state.red.reason = '今日已领 ' + state.red.today + ' 个红包 · ' + redGreenValue() + ' 绿钻';
    }
    return value;
  }

  /** 从领取返回的 CZGN.JWTZ 里找自己的 PHKL，累加精确金额。 */
  function creditFromClaimResponse(request, resp) {
    try {
      const fields = (state.ctx && state.ctx.redCfg && state.ctx.redCfg.fields) || {};
      const info = resp && resp[fields.claimInfo || 'CZGN'];
      const rows = info && info[fields.claimList || 'JWTZ'];
      if (!Array.isArray(rows) || !rows.length) return 0;
      const selfId = redSelfRoleId();
      const amountKey = fields.claimAmount || 'PHKL';
      const selfKey = fields.claimSelf || 'ACHI';
      const playerKey = fields.claimPlayer || 'GZZR';
      let amount = null;
      for (let i = 0; i < rows.length; i += 1) {
        const row = rows[i];
        if (!row) continue;
        const mine = !!row[selfKey]
          || (selfId && String(row[playerKey] || '') === selfId);
        if (!mine) continue;
        const value = Number(row[amountKey]);
        if (Number.isFinite(value)) amount = value;
        break;
      }
      if (amount === null) return 0;
      return creditRedAmount(String(request.FDUU), amount);
    } catch (cause) {
      state.red.amountError = '解析红包金额失败：' + msgOf(cause);
      return 0;
    }
  }

  /** 用绿钻余额的增量算「领取到多少绿钻」：只在没有精确数据时兜底。 */
  function syncRedGreen() {
    const balance = greenBalance();
    if (balance === null) return;
    if (state.red.greenSeen === null || balance < state.red.greenSeen) {
      state.red.greenSeen = balance;
      return;
    }
    const delta = balance - state.red.greenSeen;
    if (delta <= 0) return;
    state.red.greenSeen = balance;
    state.red.green = Math.max(0, Number(state.red.green || 0) + delta);
    saveRedDay();
    if (state.red.enabled) {
      state.red.reason = '今日已领 ' + state.red.today + ' 个红包 · ' + redGreenValue() + ' 绿钻';
    }
  }

  function packetId(info) {
    const key = (state.ctx && state.ctx.redCfg.fields && state.ctx.redCfg.fields.id) || '';
    if (!key) return '';
    const value = info[key];
    return value === undefined || value === null ? '' : String(value);
  }

  /** 领取失败：短时间内重试；确认抢完/已领才永久跳过。 */
  function retryRed(id) {
    const retry = state.red.retry || (state.red.retry = Object.create(null));
    const item = retry[id] || { tries: 0 };
    item.tries += 1;
    state.red.failed += 1;
    if (item.tries >= 3) {
      state.red.seen[id] = -1;
      delete retry[id];
      return;
    }
    state.red.seen[id] = -2;
    item.at = now() + 120 * item.tries;
    retry[id] = item;
  }

  function claim(info) {
    const ctx = state.ctx;
    const fields = ctx.redCfg.fields || {};
    const id = packetId(info);
    if (!id) return;
    state.red.pending += 1;
    const success = () => {
      delete state.red.retry[id];
      state.red.seen[id] = 1;
      rollRedDay();
      state.red.claimed += 1;
      state.red.today += 1;
      state.red.date = today();
      saveRedDay();
      // 余额更新可能比 Promise 晚一点，接下来几轮都对一次余额
      state.red.greenUntil = now() + 2500;
      syncRedGreen();
      state.red.reason = '今日已领 ' + state.red.today + ' 个红包 · ' + redGreenValue() + ' 绿钻';
    };
    const settle = code => {
      state.red.pending = Math.max(0, state.red.pending - 1);
      if (Number(code) === 1) success();
      else retryRed(id);
    };
    try {
      // 第 4 个参数是游戏的失败回调；传空函数，避免服务器返回 303005 时内部调用 undefined 抛错
      const done = ctx.redPacket[ctx.redCfg.claim](
        id, info[fields.channel], info[fields.type], function () {}
      );
      if (done && typeof done.then === 'function') {
        done.then(settle, () => {
          state.red.pending = Math.max(0, state.red.pending - 1);
          retryRed(id);
        });
      } else {
        settle(done);
      }
    } catch (cause) {
      state.red.pending = Math.max(0, state.red.pending - 1);
      state.red.reason = '领取调用失败：' + msgOf(cause);
      retryRed(id);
    }
  }

  function pruneRedSeen() {
    const seen = state.red.seen;
    const keys = Object.keys(seen);
    if (keys.length <= RED_SEEN_MAX) return;
    // 全清会重复问状态，但比重扫整个历史便宜；24 条窗口内重复次数很少
    state.red.seen = Object.create(null);
    state.red.retry = Object.create(null);
  }

  /** 按红包自身的发送时间取排序值（新的优先）；没有时间字段就保持原顺序。 */
  function redTime(info, fields) {
    const key = fields && fields.time;
    if (!key) return 0;
    const value = Number(info[key]);
    return Number.isFinite(value) ? value : 0;
  }

  function tickRed(force) {
    rollRedDay();
    if (!state.red.enabled || !state.ready) return;
    if (state.red.greenUntil && now() < state.red.greenUntil) syncRedGreen();
    if (!force && now() - state.red.lastAt < CLAIM_GAP_MS) return;
    // 已经有几个请求在飞就别再堆，避免瞬间并发被服务端限流
    if (state.red.pending >= 6) return;
    pruneRedSeen();
    let list = [];
    try { list = packetSources(); } catch (_) { return; }
    state.red.available = list.length > 0;
    if (!list.length) return;

    const claimable = (state.ctx.map.statusCodes || {}).claimable;
    const fields = (state.ctx.redCfg && state.ctx.redCfg.fields) || {};
    const nowMs = now();
    const candidates = [];
    for (const info of list) {
      const id = packetId(info);
      if (!id) continue;
      const known = state.red.seen[id];
      if (known !== undefined && known !== -2) continue;
      if (known === -2) {
        const item = state.red.retry[id];
        if (item && nowMs < item.at) continue;
      }
      let status = -1;
      try { status = state.ctx.redPacket[state.ctx.redCfg.status](info); } catch (_) { continue; }
      if (Number(status) !== Number(claimable)) {
        state.red.seen[id] = Number(status);
        if (state.red.retry) delete state.red.retry[id];
        continue;
      }
      candidates.push({ info, id, ts: redTime(info, fields), seq: candidates.length });
    }
    // 新包优先：刚发出来的 10 个名额都在，老包可能已经快满
    candidates.sort((a, b) => (b.ts - a.ts) || (a.seq - b.seq));

    let picks = 0;
    for (const item of candidates) {
      if (state.red.seen[item.id] === 9) continue;
      state.red.seen[item.id] = 9;  // 先占位，防同一 tick 重复
      state.red.lastAt = now();
      claim(item.info);
      picks += 1;
      if (picks >= 3) break;
    }

    // 清掉很久以前的失败记录，避免 map 无限增长
    const retry = state.red.retry;
    if (retry) {
      for (const id of Object.keys(retry)) {
        if (nowMs - (retry[id].at || 0) > 15000) delete retry[id];
      }
    }
  }

  /**
   * 红包事件钩子：
   *   1) 新红包的聊天事件一到就立刻抢（不等 60ms 轮询）；
   *   2) 顺便记下领取请求的服务器返回码，方便判断是「已抢完」还是「请求太快」。
   * 钩子挂在游戏单例上；重装运行时（同版本不同内容）会先拆掉旧钩子再挂新的。
   */
  function installRedHooks() {
    try {
      const ctx = state.ctx;
      if (!ctx || !ctx.redPacket || !ctx.redCfg) return;
      const svc = ctx.redPacket;
      const token = (typeof STAMP === 'string' ? STAMP : String(VERSION)) + '|' + VERSION;
      if (svc.__zzRedHookId === token) return;

      // 拆旧钩子（可能是上一套运行时挂的）
      if (svc.__zzRedHookId) {
        try {
          if (svc.__zzOrigRecv && ctx.redCfg.onReceive) {
            svc[ctx.redCfg.onReceive] = svc.__zzOrigRecv;
          }
        } catch (_) { /* ignore */ }
        try { if (svc.__zzOrigEjim) svc.EJIM = svc.__zzOrigEjim; } catch (_) { /* ignore */ }
      }
      svc.__zzOrigRecv = null;
      svc.__zzOrigEjim = null;

      const recv = ctx.redCfg.onReceive;
      if (recv && typeof svc[recv] === 'function') {
        const orig = svc[recv];
        svc.__zzOrigRecv = orig;
        svc[recv] = function () {
          const result = orig.apply(this, arguments);
          try {
            setTimeout(() => { try { tickRed(true); } catch (_) { /* ignore */ } }, 0);
          } catch (_) { /* ignore */ }
          return result;
        };
      }

      if (typeof svc.EJIM === 'function') {
        const origEjim = svc.EJIM;
        svc.__zzOrigEjim = origEjim;
        svc.EJIM = function (request) {
          const promise = origEjim.apply(this, arguments);
          try {
            // 领取请求是带「红包编号/频道/类型」的请求；详情请求没有类型字段，排除掉
            if (promise && typeof promise.then === 'function'
              && request && request.FDUU !== undefined
              && request.JFFB !== undefined) {
              promise.then(resp => {
                const code = resp && resp.ZMCL ? Number(resp.ZMCL.TCFK) : null;
                if (code !== null && code !== 0) {
                  state.red.lastCode = code;
                  state.red.lastFailId = String(request.FDUU);
                  state.red.failReason = '领取失败：服务器返回 ' + code;
                } else {
                  creditFromClaimResponse(request, resp);
                }
              }, () => {
                state.red.failReason = '领取请求失败（网络）';
              });
            }
          } catch (_) { /* ignore */ }
          return promise;
        };
      }

      svc.__zzRedHookId = token;
      state.red.hookId = token;
    } catch (_) { /* 钩子失败不影响轮询兜底 */ }
  }

  /* ------------------------------------------------------------------ *
   * 三、祈愿领取：到期领取 → 确认槽位空出 → 把原英雄放回原槽位
   *
   * 和游戏自己的槽位面板走同一套服务：
   *   * 不轮询：开启和每次到期各查询一次，等待期间只用游戏服务器时间本地倒计时；
   *   * 只处理「搜索中到点」和「已完成」两种状态，其它槽位一概不碰；
   *   * 领取后必须看到槽位变成空闲，再把原英雄放回并确认重新开始，否则停下。
   * ------------------------------------------------------------------ */

  const WISH_MIN_GAP_MS = 2000;  // 两次祈愿请求之间的最小间隔，避免到点后空转

  /** 按映射定位祈愿服务与服务器时钟；名字对不上就明确报错，绝不乱调。 */
  function wishContext() {
    const map = mapping();
    if (!map) {
      state.missingMapping = true;
      throw Error('该游戏版本（' + (state.build || '未知') + '）没有字段映射，需要补一条映射才能启用祈愿领取');
    }
    const cfg = map.wish;
    if (!cfg) throw Error('该版本映射里还没有祈愿领取');
    const cached = state.wishCtx;
    if (cached && cached.build === state.build && cached.map === map) {
      if (!hasMethod(cached.service, cfg.query) || !hasMethod(cached.service, cfg.claim) || !hasMethod(cached.service, cfg.put)) {
        throw Error('祈愿服务入口已变化，请重新加载页面后再开启');
      }
      return cached;
    }
    const reg = registry();
    const service = reg.get(cfg.service);
    if (!service) throw Error('映射里的祈愿服务（' + cfg.service + '）在游戏里找不到，需要更新该版本的映射');
    for (const key of [cfg.query, cfg.claim, cfg.put]) {
      if (!hasMethod(service, key)) throw Error('祈愿服务缺少 ' + key + '，需要更新该版本的映射');
    }
    const model = cfg.model ? service[cfg.model] : service;
    if (!model || typeof model !== 'object') throw Error('祈愿模型还没就绪，请进入游戏后重试');
    if (!cfg.slots) throw Error('祈愿映射缺少槽位列表字段');

    const clockCfg = cfg.clock || {};
    let clock = null;
    if (clockCfg.module && clockCfg.method) {
      let clockModule = null;
      try { clockModule = window.System.get('chunks:///_virtual/' + clockCfg.module + '.ts'); } catch (_) { clockModule = null; }
      const exported = clockModule && clockModule[clockCfg.export || clockCfg.module];
      const singleton = exported && exported[clockCfg.singleton || 'AWXW'];
      if (singleton && typeof singleton[clockCfg.method] === 'function') {
        clock = () => Number(singleton[clockCfg.method]());
      }
    }
    if (!clock) throw Error('找不到游戏服务器时间，需要更新该版本的映射');

    const status = cfg.statuses || {};
    const statuses = [status.idle, status.searching, status.done];
    if (statuses.some(value => !Number.isSafeInteger(value)) || new Set(statuses).size !== 3) {
      throw Error('祈愿状态映射不可用，需要更新该版本的映射');
    }
    const ctx = {
      build: state.build, map, cfg, service, model, clock,
      fields: cfg.fields || {},
      status: { idle: status.idle, searching: status.searching, done: status.done },
      result: cfg.result || {},
    };
    state.wishCtx = ctx;
    return ctx;
  }

  /** 当前槽位快照；编号重复或搜索中的时间不合理就直接停，避免错领到别的槽位。 */
  function wishSlots(ctx) {
    const list = ctx.model[ctx.cfg.slots];
    if (!Array.isArray(list)) throw Error('祈愿槽位列表还没就绪，请重新开启祈愿领取');
    const seen = new Set();
    return list.map(record => {
      const slot = {
        id: record[ctx.fields.id],
        hero: record[ctx.fields.hero],
        status: record[ctx.fields.status],
        start: record[ctx.fields.start],
        end: record[ctx.fields.end],
      };
      if (!Number.isSafeInteger(slot.id) || slot.id <= 0 || seen.has(slot.id)) throw Error('祈愿槽位数据无效');
      seen.add(slot.id);
      // 只有搜索中/已完成才带英雄；空闲和未解锁槽位不去猜英雄，也不补时间。
      if ((slot.status === ctx.status.searching || slot.status === ctx.status.done)
        && (!Number.isSafeInteger(slot.hero) || slot.hero <= 0)) {
        throw Error('祈愿槽位 ' + slot.id + ' 没有英雄，无法自动领取');
      }
      if (slot.status === ctx.status.searching
        && (!Number.isFinite(slot.start) || slot.start < 0
          || !Number.isFinite(slot.end) || slot.end <= slot.start)) {
        throw Error('祈愿槽位 ' + slot.id + ' 的搜索时间无效');
      }
      return slot;
    });
  }

  /** 原生回执必须是成功码：空回执也算失败，不能继续放入或重试领取。 */
  function wishOk(ctx, response, action) {
    const wrapper = ctx.result.wrapper;
    const key = ctx.result.code;
    if (!wrapper || !key) return;
    const body = response && response[wrapper];
    const code = body ? body[key] : undefined;
    if (code !== ctx.result.success) {
      throw Error(action + '失败（回执 ' + (code === undefined ? '空' : code) + '），请检查游戏后重新开启祈愿领取');
    }
  }

  /** 用最新槽位算下一次到期时间，同时刷新界面要显示的槽位倒计时。 */
  function wishPlan(ctx) {
    const slots = wishSlots(ctx);
    const active = slots.filter(slot => slot.status === ctx.status.searching || slot.status === ctx.status.done);
    const waiting = active.filter(slot => slot.status === ctx.status.searching && slot.end > 0);
    state.wish.waiting = waiting.map(slot => ({ id: slot.id, hero: slot.hero, end: slot.end }));
    state.wish.dueAt = waiting.length ? Math.min.apply(null, waiting.map(slot => slot.end)) : 0;
    return { slots: slots, active: active, waiting: waiting };
  }

  /** 一轮：查询 → 领取到期的槽位 → 把原英雄放回 → 重新算倒计时。 */
  async function wishPass() {
    const ctx = wishContext();
    const cfg = ctx.cfg;
    state.wish.pending = true;
    state.wish.lastPassAt = now();
    try {
      state.wish.reason = '正在读取祈愿搜索时间…';
      const response = await ctx.service[cfg.query]();
      if (!state.wish.enabled) return;
      if (!response) {
        // 游戏在活动时间窗外会直接返回空值，这里并不是辅助出问题。
        state.wish.available = false;
        throw Error('祈愿活动当前未开放（不在开放时间内），请稍后再试');
      }
      wishOk(ctx, response, '读取祈愿');
      if (cfg.available && hasMethod(ctx.service, cfg.available)) {
        try { state.wish.available = !!ctx.service[cfg.available](); } catch (_) { /* 取不到就不据此置灰 */ }
      }

      const server = ctx.clock();
      const plan = wishPlan(ctx);
      if (!plan.active.length) throw Error('没有正在祈愿的英雄，请先在槽位放入英雄再开启');
      const due = plan.active.filter(slot => slot.status === ctx.status.done
        || (slot.end > 0 && slot.end <= server));

      for (const slot of due) {
        if (!state.wish.enabled) return;
        // 等待期间槽位可能被手动取消或换过英雄，动手前再核对一次。
        const latest = wishSlots(ctx).filter(item => item.id === slot.id)[0];
        if (!latest || latest.status !== slot.status || latest.hero !== slot.hero) continue;
        state.wish.reason = '正在领取槽位 ' + slot.id + ' 的祈愿…';
        const claimed = await ctx.service[cfg.claim](slot.id);
        if (!state.wish.enabled) return;
        wishOk(ctx, claimed, '领取槽位 ' + slot.id);
        state.wish.claimed += 1;
        const emptied = wishSlots(ctx).filter(item => item.id === slot.id)[0];
        if (!emptied || emptied.status !== ctx.status.idle) {
          throw Error('槽位 ' + slot.id + ' 领取后状态已变化，已停止自动放入英雄');
        }
        state.wish.reason = '已领取，正在把英雄 ' + slot.hero + ' 放回槽位 ' + slot.id + '…';
        const placed = await ctx.service[cfg.put](slot.id, slot.hero);
        if (!state.wish.enabled) return;
        wishOk(ctx, placed, '把英雄 ' + slot.hero + ' 放回槽位 ' + slot.id);
        const again = wishSlots(ctx).filter(item => item.id === slot.id)[0];
        if (!again || again.status !== ctx.status.searching || again.hero !== slot.hero
          || !(again.end > ctx.clock())) {
          throw Error('英雄 ' + slot.hero + ' 未确认重新祈愿，已停止，请检查槽位 ' + slot.id);
        }
      }

      const final = wishPlan(ctx);
      state.wish.reason = '正在等待 ' + final.waiting.length + ' 个祈愿完成；本次已领取 '
        + state.wish.claimed + ' 次';
    } finally {
      state.wish.pending = false;
    }
  }

  function wishStop(reason) {
    state.wish.enabled = false;
    state.wish.dueAt = 0;
    state.wish.waiting = [];
    state.wish.reason = reason || '已关闭';
  }

  function wishFail(cause) {
    if (!state.wish.enabled) return;
    wishStop('祈愿领取已停止：' + msgOf(cause));
  }

  function wishStart() {
    if (state.wish.pending) throw Error('上一笔祈愿请求还没结束，请稍后再开启');
    wishContext();
    state.wish.enabled = true;
    state.wish.claimed = 0;
    state.wish.dueAt = 0;
    state.wish.waiting = [];
    state.wish.lastPassAt = 0;
    state.wish.reason = '正在检查祈愿功能与权限，并读取搜索时间…';
    void wishPass().catch(wishFail);
  }

  /** 到点才动手；等待期间只读服务器时间，不发请求。 */
  function tickWish() {
    if (!state.wish.enabled || state.wish.pending) return;
    if (now() - state.wish.lastPassAt < WISH_MIN_GAP_MS) return;
    let ctx = null;
    let server = 0;
    try {
      ctx = wishContext();
      server = ctx.clock();
    } catch (cause) {
      wishFail(cause);
      return;
    }
    if (!Number.isFinite(server) || server <= 0) {
      wishFail('祈愿服务器时间无效，请重新开启');
      return;
    }
    if (state.wish.dueAt > 0 && server < state.wish.dueAt) return;
    void wishPass().catch(wishFail);
  }

  /* ------------------------------------------------------------------ *
   * 四、地图：小怪 / BOSS 死亡后等待 → 重新进图 + BOSS 金币统计
   *
   * 死亡判定只用一件事：怪物的实体编号（PLTV）从单位列表里消失了。
   * 跑远的怪只会变成空节点、不会掉出列表，所以「消失」基本就等于「被击杀」；
   * 一次消失一大片则是切场景/重载地图，按场景变化处理，不算击杀。
   * ------------------------------------------------------------------ */

  const MAP_MIN_GAP_MS = 8000;    // 两次重新进图之间至少隔这么久，防抖
  const MAP_MASS_GONE = 20;      // 一次消失这么多单位，基本是重载地图，不算击杀
  const BOSS_SETTLE_MS = 20000;  // BOSS 死亡后统计金币的窗口

  function mapConfig() {
    const build = mapping();
    const cfg = build && build.map;
    if (!cfg || !cfg.unit || !cfg.enter) {
      throw Error('当前版本还没有地图字段映射，需要更新映射表');
    }
    return cfg;
  }

  /**
   * 场景上下文：完全按参考工具的做法，走「实体世界」而不是地图视图。
   *   holder(dt_fuwu)   = VKXX
   *   core(dt_shitiShijie) = VKXX.ZRRU
   *   core[manager]()  = 实体管理入口，core[manager]()[container]() = 单位容器
   * 容器用 each(index, unit) 迭代，单位自带 monster()/dead()/attribute 等。
   */
  function mapContext() {
    const cfg = mapConfig();
    const holder = window[cfg.holder];
    if (!holder) throw Error('还没进入游戏（找不到世界服务），请先进入地图');
    const state_ = holder[cfg.state];
    const active = holder[cfg.scene];
    if (!state_ || !active) throw Error('当前不在可以刷新的地图里（找不到场景状态）');
    const core = holder[cfg.core];
    if (!core) throw Error('实体世界还没就绪，请稍后再开');
    const manager = typeof core[cfg.manager] === 'function' ? core[cfg.manager]() : null;
    const units = manager && typeof manager[cfg.container] === 'function' ? manager[cfg.container]() : null;
    if (!units || typeof units[cfg.each] !== 'function') throw Error('地图单位还没加载完成，请稍后再开');
    let component = null;
    if (cfg.component) {
      try { component = window.System.get('chunks:///_virtual/' + cfg.component + '.ts')[cfg.component]; } catch (_) { component = null; }
    }
    return { cfg, holder, core, units, component, state: state_, active };
  }

  /** 当前是否在「普通地图」（没在副本/过场/加载中），对应参考的 active===normal && !changing。 */
  function mapNormal(ctx) {
    const cfg = ctx.cfg;
    if (!ctx.core || !ctx.state) return false;
    if (cfg.changing && ctx.holder[cfg.changing]) return false;
    if (ctx.holder[cfg.scene] !== ctx.state) return false;
    return true;
  }

  /** BOSS 关键字 / 编号：映射表给默认值，界面上填的覆盖项优先。 */
  function bossMatchers(cfg) {
    const extra = window.__zzAssistNames || {};
    const collect = (list, fallback) => {
      const out = [];
      const push = item => {
        const text = String(item === undefined || item === null ? '' : item).trim();
        if (text) out.push(text);
      };
      if (Array.isArray(list)) list.forEach(push);
      if (Array.isArray(fallback)) fallback.forEach(push);
      return out;
    };
    const keywords = collect(extra.bossKeywords || extra.boss_keywords, cfg.bossKeywords);
    const ids = collect(extra.bossIds || extra.boss_ids, cfg.bossIds);
    return { keywords, ids };
  }

  /**
   * 这个实体是不是要盯的怪。
   *
   * 和参考工具一致：只认实体世界里的怪（unit[monster]() === true），并且
   *   - unit[attribute][server] === true（QJVQ.OMRK：服务端自己刷新的单位）跳过；
   *   - core.has(unit[index], component) 为真（带服务器刷新组件 KCYO）也跳过。
   * 死亡只看 unit[dead]()（AINJ，是否已死亡），不看「从列表消失」。
   */
  function unitInfo(unit, ctx, matchers) {
    const cfg = ctx.cfg;
    const u = cfg.unit;
    if (!unit || (u.monster && typeof unit[u.monster] === 'function' && unit[u.monster]() !== true)) return null;
    const attr = u.attribute ? unit[u.attribute] : null;
    if (attr && u.server && attr[u.server] === true) return null;
    if (ctx.component && u.has && u.index && ctx.core && typeof ctx.core[u.has] === 'function'
      && ctx.core[u.has](unit[u.index], ctx.component)) return null;
    if (u.camp && cfg.camps && unit[u.camp] !== cfg.camps.monster) return null;
    const tag = typeof unit[u.tag] === 'string' ? unit[u.tag] : '';
    const point = unit[u.point] || {};
    const template = unit[u.template];
    const name = String(point.name || '');
    const text = (tag + ' ' + name + ' ' + String(point.pointID || '')).toLowerCase();
    const keyword = matchers.keywords.some(word => text.indexOf(word.toLowerCase()) >= 0);
    const idHit = matchers.ids.some(id => String(template) === id || text.indexOf(id.toLowerCase()) >= 0);
    const entity = unit[u.entity] !== undefined && unit[u.entity] !== null ? unit[u.entity] : unit[u.id];
    if (entity === undefined || entity === null) return null;
    let dead = false;
    try { dead = u.dead && typeof unit[u.dead] === 'function' ? !!unit[u.dead]() : false; } catch (_) { dead = false; }
    return { kind: keyword || idHit ? 'boss' : 'mob', entity, name: name || tag, dead, unit };
  }

  function mapDayRoll() {
    const date = today();
    if (state.gold.date !== date) {
      state.gold.date = date;
      state.gold.today = 0;
      state.map.bossGold = 0;
      state.map.kills.mob = 0;
      state.map.kills.boss = 0;
    }
  }

  /** 顶部 HUD 里金币数字所在的 cc.Label（找到后缓存，节点失效再找）。 */
  function findGoldLabel(cfg) {
    const cc = window.cc;
    if (!cc || !cc.director) return null;
    const root = cc.director.getScene();
    if (!root) return null;
    let host = null;
    const stack = [root];
    while (stack.length) {
      const node = stack.pop();
      if (node.name === cfg.gold.node) { host = node; break; }
      const children = node.children || [];
      for (let i = 0; i < children.length; i += 1) stack.push(children[i]);
    }
    if (!host) return null;
    const queue = [host];
    let fallback = null;
    while (queue.length) {
      const node = queue.shift();
      const components = node._components || [];
      for (const component of components) {
        let text;
        try { text = component && component.string; } catch (_) { continue; }
        if (typeof text !== 'string') continue;
        if (node.name === cfg.gold.label) return component;
        if (!fallback && text) fallback = component;
      }
      const children = node.children || [];
      for (const child of children) queue.push(child);
    }
    return fallback;
  }

  /** "1,234" / "12.3万" / "1亿" 都换算成整数。 */
  function parseAmount(text) {
    const cleaned = String(text || '').replace(/[,\s]/g, '');
    const match = cleaned.match(/([0-9]+(?:\.[0-9]+)?)([^0-9.]?)/);
    if (!match) return null;
    const base = Number(match[1]);
    if (!Number.isFinite(base)) return null;
    const unit = (match[2] || '').toLowerCase();
    let scale = 1;
    if (unit === '千' || unit === 'k') scale = 1e3;
    else if (unit === '万' || unit === 'w') scale = 1e4;
    else if (unit === '亿') scale = 1e8;
    else if (unit === 'm') scale = 1e6;
    return Math.round(base * scale);
  }

  /** 只在 BOSS 结算窗口内累加金币增量。 */
  function trackGold() {
    const cfg = mapConfig();
    if (!state.map.goldLabel || !state.map.goldLabel.node || !state.map.goldLabel.node.isValid) {
      state.map.goldLabel = null;
      if (now() >= state.map.goldNextFindAt) {
        state.map.goldNextFindAt = now() + 5000;
        state.map.goldLabel = findGoldLabel(cfg);
      }
      if (!state.map.goldLabel) return;
      state.map.goldLast = null;
    }
    const value = parseAmount(state.map.goldLabel.string);
    if (value === null) return;
    const last = state.map.goldLast;
    state.map.goldLast = value;
    if (!state.map.settleUntil || now() > state.map.settleUntil) return;
    if (last === null || value <= last) return;
    const gain = value - last;
    mapDayRoll();
    state.map.bossGold += gain;
    state.gold.today += gain;
  }

  /**
   * 重新进入当前地图：调用游戏自己的「地图正常重进」。
   *
   * 这是参考工具的同一套做法（映射别名 az_dituZhengchangChongjin）：
   *   world = VKXX（dt_fuwu），enter = VKXX.XIPH，状态 = VKXX.AGDA（dt_zhuangtai），
   *   mapId = VKXX.AGDA.POMD（dt_dangqianId）。
   * VKXX.XIPH(mapId) 内部会自己发请求并把 resp.EADX 交给 FROI，成功返回 true，
   * 同时 VKXX.ZRRU（dt_shitiShijie 实体世界）会变成新对象 —— 实测 true 且引用已更换。
   *
   * 之前用 scene.CKBZ(14, …) 是错的：那是「切到另一张地图」的入口，目标就是当前地图时
   * 服务端只回 WCHC=0、不带场景数据（连续 100 秒重试也一样）。
   */
  function mapEnter(ctx) {
    try {
    const cfg = ctx.cfg;
    const world = ctx.holder;
    if (typeof world[cfg.enter] !== 'function') throw Error('游戏里没有地图重进接口，需要更新映射');
    const normal = cfg.state ? world[cfg.state] : null;
    const mapId = normal && cfg.mapId ? normal[cfg.mapId] : null;
    if (mapId === undefined || mapId === null) throw Error('拿不到当前地图编号，请先进入地图');

    state.map.entering = true;
    state.map.lastEnterAt = now();
    if (!state.map.handled) state.map.handled = Object.create(null);
    try {
      const seen = state.map.seen || {};
      for (const id of Object.keys(seen)) {
        if (seen[id] && seen[id].dead) state.map.handled[id] = true;
      }
    } catch (_) { /* ignore */ }
    state.map.seen = Object.create(null);
    const kind = state.map.pendingKind;
    const label = kind === 'boss' ? 'BOSS' : '小怪';

    return Promise.resolve(fastEnterMap(world, cfg, mapId)).then(result => {
      if (result !== true) {
        // 游戏没开始切图（多数是刚切过图的冷却）：退避重试，别 5 秒一次硬刷
        state.map.entering = false;
        state.map.pendingKind = '';
        state.map.reloadFails = (state.map.reloadFails || 0) + 1;
        const waitMs = Math.min(120000, 20000 * state.map.reloadFails);
        state.map.bossAt = now() + waitMs;
        state.map.mobAt = now() + waitMs;
        runMapReport('重进地图没生效（游戏拒绝切图），' + Math.round(waitMs / 1000) + ' 秒后重试');
        return '';
      }
      state.map.reloadFails = 0;
      // 和参考工具一致：重进成功后清空这轮死亡计时；怪若还没活，下一轮会重新计时再重进
      state.map.deathSince = Object.create(null);
      state.map.enterBlocked = false;
      mapDayRoll();
      runMapReport(label + '死亡后已重新进入地图 ' + mapId);
      state.map.entering = false;
      state.map.pendingKind = '';
      return '';
    }).catch(cause => {
      state.map.entering = false;
      state.map.pendingKind = '';
      throw cause;
    });
    } catch (cause) {
      // 同步抛错（接口缺失/游戏拒绝）也必须解除 entering，不然刷新会永久停住
      state.map.entering = false;
      state.map.pendingKind = '';
      return Promise.reject(cause);
    }
  }

  function runMapReport(text) {
    state.map.lastAction = text;
    state.map.lastActionAt = now();
  }

  /**
   * 每个 tick 扫一遍实体世界的怪物（和参考工具同一套）：
   *   - 只看 unit[monster]() 为真的实体，跳过服务端自己刷新的（QJVQ.OMRK / KCYO 组件）；
   *   - 死亡 = unit[dead]()（AINJ），不是「从列表消失」；
   *   - 记录每只怪「第一次被发现死亡」的时间，死够设定秒数就重进地图；
   *   - 重进成功后清空这轮计时：如果怪还没活，下一轮会重新计时再重进一次，
   *     直到服务端把怪刷回来（参考工具就是这个行为）。
   */
  function tickMap() {
    const mobOn = !!state.mob.enabled;
    const bossOn = !!state.boss.enabled;
    if (!mobOn && !bossOn) return;
    const cfg = mapConfig();
    const ctx = mapContext();

    // 副本/过场/加载中不算；回到普通地图后重新观察
    if (!mapNormal(ctx)) {
      state.map.deathSince = Object.create(null);
      state.map.mobAt = 0;
      state.map.bossAt = 0;
      return;
    }

    const matchers = bossMatchers(cfg);
    const nowMs = now();
    const seen = Object.create(null);
    ctx.units[cfg.each](function (_index, unit) {
      let info = null;
      try { info = unitInfo(unit, ctx, matchers); } catch (_) { info = null; }
      if (info) seen[info.entity] = info;
      return false;
    });

    let bossAt = 0, mobAt = 0, bossName = '';
    for (const entity of Object.keys(seen)) {
      const info = seen[entity];
      if (!info.dead) {
        delete state.map.deathSince[entity];
        delete state.map.deadCounted[entity];
        if (state.map.handled) delete state.map.handled[entity];
        continue;
      }
      // 已经为这只怪重进过一次、但它还没活过来：别再触发刷新（否则会 9 秒一次无限重进）
      if (state.map.handled && state.map.handled[entity]) continue;
      // 每只怪只计一次击杀（重进后清空 deathSince 会重试，但不能再加一次击杀）
      if (!state.map.deadCounted[entity]) {
        state.map.deadCounted[entity] = true;
        mapDayRoll();
        if (info.kind === 'boss') { state.map.kills.boss += 1; bossName = info.name; }
        else state.map.kills.mob += 1;
      }
      if (state.map.deathSince[entity] === undefined) state.map.deathSince[entity] = nowMs;
      const seconds = Math.max(1, info.kind === 'boss' ? state.boss.seconds : state.mob.seconds);
      const due = state.map.deathSince[entity] + seconds * 1000;
      if (info.kind === 'boss') { if (!bossAt || due < bossAt) bossAt = due; if (!bossName) bossName = info.name; }
      else if (!mobAt || due < mobAt) mobAt = due;
    }
    for (const entity of Object.keys(state.map.deathSince)) if (!seen[entity]) delete state.map.deathSince[entity];
    for (const entity of Object.keys(state.map.deadCounted)) if (!seen[entity]) delete state.map.deadCounted[entity];
    state.map.seen = seen;

    // 自测：假死亡直接当「已经死了设定秒数」
    for (const info of state.map.fakeDeaths.splice(0)) {
      mapDayRoll();
      const due = nowMs - 1;   // 用「已过期」而不是 now+1：同一帧里就能触发，避免 1ms 竞态把假死丢掉
      if (info.kind === 'boss') { state.map.kills.boss += 1; bossAt = due; bossName = info.name || '自测怪'; }
      else { state.map.kills.mob += 1; mobAt = due; }
    }

    state.map.lastBossName = bossName;
    state.map.bossAt = bossAt;
    state.map.mobAt = mobAt;
    if (bossAt && bossOn) {
      state.map.settleUntil = nowMs + BOSS_SETTLE_MS;
      state.map.pendingKind = 'boss';
      state.boss.reason = 'BOSS「' + (bossName || '未知') + '」已死亡，'
        + Math.max(0, Math.ceil((bossAt - nowMs) / 1000)) + ' 秒后重进地图';
    }
    if (mobAt && mobOn && !bossAt) {
      state.map.pendingKind = state.map.pendingKind || 'mob';
      state.mob.reason = '小怪已死亡，'
        + Math.max(0, Math.ceil((mobAt - nowMs) / 1000)) + ' 秒后重进地图';
    }

    if (state.map.entering && now() - (state.map.lastEnterAt || 0) > 20000) {
      // 重进卡住（游戏没回调/被拒绝）时恢复监控，不然界面会一直停在「0 秒后重进地图」
      state.map.entering = false;
      state.map.pendingKind = '';
      state.map.bossAt = 0;
      state.map.mobAt = 0;
      runMapReport('重进地图超时，已恢复监控');
    }
    if (!state.map.entering) {
      const bossDue = bossOn && state.map.bossAt && now() >= state.map.bossAt;
      const mobDue = mobOn && state.map.mobAt && now() >= state.map.mobAt;
      if (bossDue || mobDue) {
        state.map.mobAt = 0;
        state.map.bossAt = 0;
        if (state.map.enterBlocked) {
          state.map.pendingKind = '';
        } else {
          if (!state.map.pendingKind) state.map.pendingKind = bossDue ? 'boss' : 'mob';
          void Promise.resolve().then(() => mapEnter(ctx)).catch(cause => {
            state.map.entering = false;
            state.map.pendingKind = '';
            const text = '重新进图失败：' + msgOf(cause);
            state.mob.reason = text;
            state.boss.reason = text;
            runMapReport(text);
          });
        }
      }
    }

    try { trackGold(); } catch (_) { /* 金币读不到不影响重进 */ }

    if (!state.map.entering) {
      const gold = state.map.bossGold ? '；今日 BOSS 金币 ' + state.map.bossGold : '';
      const kills = '（今日小怪 ' + state.map.kills.mob + ' / BOSS ' + state.map.kills.boss + '）';
      const blocked = state.map.enterBlocked ? '；这张地图不支持自动重进（进副本后自动恢复）' : '';
      if (!state.mob.reason || state.mob.reason.indexOf('失败') < 0) {
        state.mob.reason = mobOn
          ? (state.map.mobAt
            ? '小怪已死亡，' + Math.max(0, Math.ceil((state.map.mobAt - now()) / 1000)) + ' 秒后重进地图'
            : '监控中：小怪死亡后 ' + state.mob.seconds + ' 秒重进地图' + kills + blocked)
          : '已关闭';
      }
      if (!state.boss.reason || state.boss.reason.indexOf('失败') < 0) {
        state.boss.reason = bossOn
          ? (state.map.bossAt
            ? 'BOSS 已死亡，' + Math.max(0, Math.ceil((state.map.bossAt - now()) / 1000)) + ' 秒后重进地图' + gold
            : '监控中：BOSS 死亡后 ' + state.boss.seconds + ' 秒重进地图' + gold + blocked)
          : '已关闭';
      }
    }
  }

  function mapStop(reason) {
    state.mob.enabled = false;
    state.boss.enabled = false;
    state.map.mobAt = 0;
    state.map.bossAt = 0;
    state.map.seen = Object.create(null);
    state.map.deathSince = Object.create(null);
    state.map.pendingKind = '';
    const text = reason || '已关闭';
    state.mob.reason = text;
    state.boss.reason = text;
  }

  /* ------------------------------------------------------------------ *
   * 六、战斗参数：英雄倍伤 / 视野（初始索敌）/ 技能冷却
   *
   * 全部照参考工具的接口实现（别名 → 4 位随机名的映射见 assist_mapping.json）：
   *   倍伤：hook 受伤结算 JPVU.prototype.BMGQ，只放大「我方英雄」打出的伤害字段；
   *   视野：改怪物配置 MonsterAttribute.QOML（初始索敌半径），当前地图实例再写 QJVQ.NAYM；
   *   冷却：改技能冷却对象 VSWM.XAEU（毫秒）——优先写当前战斗配置，退回全局技能表。
   * 这三项和「地图刷新」互斥：参考工具同样要求先关地图刷新再改战斗参数。
   * ------------------------------------------------------------------ */

  const combat = {
    damage: {
      hook: null, context: null, seen: null, value: 1, enabled: false, reason: '',
      shieldEnabled: false, shieldValue: 0,
    },
    vision: { written: [], core: null, target: null, enabled: false, reason: '' },
    cooldown: { written: [], config: null, enabled: false, reason: '' },
  };
  let combatSyncAt = 0;

  function combatConfig() {
    const build = mapping();
    if (!build) {
      state.missingMapping = true;
      throw Error('该游戏版本还没有字段映射，需要更新映射表');
    }
    const cfg = build.combat;
    if (!cfg || !cfg.config || !cfg.damage || !cfg.cooldown) {
      throw Error('该游戏版本还没有倍伤 / 视野 / 冷却映射，需要更新映射表');
    }
    return cfg;
  }

  /** 按导出名找类：有些类所在的模块名不等于类名，直接扫模块表最稳。 */
  function combatClass(name) {
    const system = window.System;
    if (!system || typeof system.entries !== 'function') {
      throw Error('游戏还没加载完成，请进入游戏主界面后重试');
    }
    for (const pair of system.entries()) {
      const exports = pair && pair[1];
      if (!exports) continue;
      let value = null;
      try { value = exports[name]; } catch (_) { continue; }
      if (typeof value === 'function' && value.prototype) return value;
    }
    return null;
  }

  /** 配置读取器：参考工具的 az_peizhiGen + fw_tongyongDanli + az_chaPeizhi。 */
  function combatManager(cfg) {
    const cls = combatClass(cfg.config.module);
    if (!cls) throw Error('游戏配置模块（' + cfg.config.module + '）还没加载，请进入游戏主界面后重试');
    let manager = null;
    try { manager = cls[cfg.config.singleton]; } catch (_) { manager = null; }
    if (!manager || typeof manager[cfg.config.table] !== 'function') manager = singletonOf(cls);
    if (!manager || typeof manager[cfg.config.table] !== 'function') {
      throw Error('游戏配置读取入口还没就绪，请进入游戏场景后重试');
    }
    return manager;
  }

  function combatWorld(cfg) {
    const holder = window[cfg.world.world];
    if (holder) return holder;
    const system = window.System;
    if (!system || typeof system.entries !== 'function') throw Error('游戏还没加载完成');
    const bags = [...system.entries()]
      .map(pair => pair && pair[1] && pair[1][cfg.world.registry])
      .filter(Boolean);
    const worlds = [...new Set(bags.map(bag => bag[cfg.world.world]).filter(Boolean))];
    if (worlds.length !== 1) throw Error('当前游戏地图还没就绪，请进入地图后重试');
    return worlds[0];
  }

  /** 只认可写的数据属性（不触发 getter/setter）。 */
  function writableNumber(owner, key) {
    if (!owner || typeof owner !== 'object' || !key) return false;
    let cursor = owner;
    let guard = 0;
    while (cursor && guard < 8) {
      guard += 1;
      let desc = null;
      try { desc = Object.getOwnPropertyDescriptor(cursor, key); } catch (_) { return false; }
      if (desc) {
        return 'value' in desc && !!desc.writable && typeof desc.value === 'number' && Number.isFinite(desc.value);
      }
      try { cursor = Object.getPrototypeOf(cursor); } catch (_) { return false; }
    }
    return false;
  }

  /** 写一个数值并记住原值；同一个对象/字段只记一次，关掉时按原样还原。 */
  function combatWrite(list, owner, key, value) {
    if (!writableNumber(owner, key)) return false;
    for (const entry of list) if (entry.owner === owner && entry.key === key) return true;
    const entry = {
      owner, key, original: owner[key], last: value,
      own: Object.getOwnPropertyDescriptor(owner, key),
    };
    try { owner[key] = value; } catch (_) { return false; }
    if (owner[key] !== value) return false;
    list.push(entry);
    return true;
  }

  function combatRestore(list) {
    const left = [];
    for (const entry of list) {
      try {
        if (entry.owner[entry.key] !== entry.last) { left.push(entry); continue; }
        if (entry.own) Object.defineProperty(entry.owner, entry.key, entry.own);
        else delete entry.owner[entry.key];
        if (entry.owner[entry.key] !== entry.original) left.push(entry);
      } catch (_) { left.push(entry); }
    }
    return left;
  }

  /* ---- 倍伤 ---- */

  function damageContext(cfg) {
    const moduleName = cfg.damage.unitModule;
    const cls = combatClass(moduleName);
    if (!cls) throw Error('伤害结算模块（' + moduleName + '）还没加载，请进入游戏后重试');
    const owner = cls.prototype;
    const desc = Object.getOwnPropertyDescriptor(owner, cfg.damage.receive);
    if (!desc || typeof desc.value !== 'function' || !desc.writable || !desc.configurable) {
      throw Error('当前游戏版本不支持英雄伤害结算（入口不是可写方法）');
    }
    const world = combatWorld(cfg);
    const core = world[cfg.world.core];
    if (!core) throw Error('实体世界还没就绪，请进入地图后重试');
    const heroes = () => {
      const manager = typeof core[cfg.world.manager] === 'function' ? core[cfg.world.manager]() : null;
      if (typeof core[cfg.world.team] !== 'function' || typeof core[cfg.world.read] !== 'function'
        || !manager || typeof manager[cfg.world.members] !== 'function') {
        throw Error('当前地图还没有我方队伍');
      }
      const team = core[cfg.world.team](world[cfg.world.player]);
      if (!Number.isInteger(team) || team <= 0) throw Error('当前场景还没找到我方队伍');
      const members = manager[cfg.world.members](team);
      if (!Array.isArray(members)) throw Error('我方英雄列表格式异常');
      return members
        .map(id => core[cfg.world.read](id))
        .filter(unit => unit && typeof unit[cfg.damage.hero] === 'function' && unit[cfg.damage.hero]() === true);
    };
    heroes();
    return { owner, descriptor: desc, fields: cfg.damage, core, heroes };
  }

  function damageMultiply(box, packet) {
    const fields = box.context.fields;
    if (!packet || typeof packet !== 'object') throw Error('英雄伤害结算数据异常');
    if (box.seen.has(packet)) return;
    const attacker = packet[fields.attacker];
    if (!attacker) return;
    const hero = attacker[fields.origin];
    if (!hero || typeof hero[fields.hero] !== 'function' || hero[fields.hero]() !== true) return;
    if (box.context.heroes().indexOf(hero) < 0) return;
    const values = [];
    for (const key of fields.numbers) {
      if (!writableNumber(packet, key)) throw Error('伤害字段不可写，已停止倍伤');
      const value = packet[key] * box.value;
      if (!Number.isFinite(value)) throw Error('倍伤结果超出范围，请降低倍率');
      values.push(value);
    }
    fields.numbers.forEach((key, index) => { packet[key] = values[index]; });
    box.seen.add(packet);
  }

  /**
   * 英雄免伤：同一个伤害包里，被打的是我方英雄（KXRF）就按比例削减。
   * 只削减伤害数值，不改任何单位属性，也不碰血条以外的逻辑。
   * 判定只看「这是不是英雄」，不依赖当前队伍的实体列表——这样切图后也继续生效。
   */
  function damageShield(box, packet) {
    const fields = box.context.fields;
    if (!packet || typeof packet !== 'object') return;
    const key = fields.target || 'KXRF';
    const target = packet[key];
    if (!target) return;
    if (typeof target[fields.hero] !== 'function' || target[fields.hero]() !== true) return;
    const keep = Math.max(0, Math.min(1, 1 - (box.shieldValue || 0) / 100));
    const values = [];
    for (const name of fields.numbers) {
      if (!writableNumber(packet, name)) throw Error('伤害字段不可写，已停止免伤');
      values.push(keep <= 0 ? 0 : packet[name] * keep);
    }
    fields.numbers.forEach((name, index) => { packet[name] = values[index]; });
    if (fields.zeroField && packet[fields.zeroField] !== undefined && keep <= 0) {
      packet[fields.zeroField] = 0;
    }
  }

  /** 装伤害结算钩子（倍伤 / 免伤 共用同一个，避免重复包一层）。 */
  function ensureDamageHook(cfg) {
    const box = combat.damage;
    if (box.hook && box.hook.owner[box.hook.key] === box.hook.wrapper) return box.context;
    const context = damageContext(cfg);
    box.seen = new WeakSet();
    const wrapper = function (packet) {
      if (box.enabled) {
        try {
          damageMultiply(box, packet);
        } catch (cause) {
          box.enabled = false;
          state.combat.damage.enabled = false;
          state.combat.damage.reason = '已停止：' + msgOf(cause);
        }
      }
      if (box.shieldEnabled) {
        try {
          damageShield(box, packet);
        } catch (cause) {
          box.shieldEnabled = false;
          state.combat.shield.enabled = false;
          state.combat.shield.reason = '已停止：' + msgOf(cause);
        }
      }
      // 辅助自己出错也照常执行游戏原来的结算流程
      return Reflect.apply(context.descriptor.value, this, arguments);
    };
    Object.defineProperty(context.owner, cfg.damage.receive, { ...context.descriptor, value: wrapper });
    box.hook = { owner: context.owner, key: cfg.damage.receive, descriptor: context.descriptor, wrapper };
    box.context = context;
    return context;
  }

  function setDamage(cfg, value) {
    const box = combat.damage;
    const range = (cfg.ranges && cfg.ranges.damage) || [1, 70000];
    if (!(value >= range[0] && value <= range[1])) {
      throw Error('倍率需要在 ' + range[0] + ' - ' + range[1] + ' 之间');
    }
    const context = ensureDamageHook(cfg);
    box.value = value;
    box.enabled = true;
    return { multiplier: value, heroes: context.heroes().length };
  }

  function setShield(cfg, percent) {
    const box = combat.damage;
    if (!(percent > 0 && percent <= 100)) throw Error('免伤比例需要在 1 - 100 之间');
    const context = ensureDamageHook(cfg);
    box.shieldValue = percent;
    box.shieldEnabled = true;
    return { percent, heroes: context.heroes().length };
  }

  /** 倍伤和免伤共用同一个钩子：两个都关了才还原。 */
  function stopDamageHook() {
    const box = combat.damage;
    if (box.enabled || box.shieldEnabled) return;
    const hook = box.hook;
    if (hook && hook.owner[hook.key] === hook.wrapper) {
      try { Object.defineProperty(hook.owner, hook.key, hook.descriptor); } catch (_) { /* 外部锁定就等换页恢复 */ }
    }
    box.hook = null;
    box.context = null;
  }

  function stopDamage() {
    combat.damage.enabled = false;
    stopDamageHook();
  }

  function stopShield() {
    combat.damage.shieldEnabled = false;
    stopDamageHook();
  }

  /* ---- 视野（初始索敌半径） ---- */

  function visionRows(cfg) {
    const manager = combatManager(cfg);
    const monsters = manager[cfg.config.table](cfg.config.monsterTable);
    if (!Array.isArray(monsters)) throw Error('怪物配置表（' + cfg.config.monsterTable + '）读取失败');
    const rows = [];
    for (const row of monsters) {
      if (!row) continue;
      const value = row[cfg.config.vision];
      if (value === 350 || value === 500) rows.push(row);
    }
    if (!rows.length) throw Error('怪物配置里没找到可改的视野字段（版本可能变了）');
    return { manager, monsters, rows };
  }

  function applyVision(cfg, mode) {
    const target = mode === 0 ? 0 : ((cfg.vision && cfg.vision.expand) || 1400);
    const scan = visionRows(cfg);
    const world = combatWorld(cfg);
    const core = world[cfg.world.core];
    const list = combat.vision.written;
    const ids = new Set();
    for (const row of scan.rows) ids.add(row[cfg.world.configId]);
    let configs = 0;
    if (core && typeof core[cfg.world.monsterConfig] === 'function') {
      for (const row of scan.rows) {
        const id = row[cfg.world.configId];
        let conf = null;
        try { conf = core[cfg.world.monsterConfig](id); } catch (_) { conf = null; }
        if (!conf) continue;
        if (conf[cfg.world.configId] !== undefined && conf[cfg.world.configId] !== id) continue;
        if (combatWrite(list, conf, cfg.config.vision, target)) configs += 1;
      }
    }
    // 已经在场上的怪：只改「原本就在 350/500 这档」的那几类，别波及别的怪
    let units = 0;
    try {
      const ctx = mapContext();
      ctx.units[ctx.cfg.each](function (_index, unit) {
        const u = ctx.cfg.unit;
        if (u.monster && typeof unit[u.monster] === 'function' && unit[u.monster]() !== true) return false;
        const unitId = unit[cfg.world.configId] !== undefined ? unit[cfg.world.configId] : unit[cfg.world.entityId];
        if (!ids.has(unitId) && !ids.has(unit[cfg.world.entityId])) return false;
        const attr = u.attribute ? unit[u.attribute] : null;
        if (attr && combatWrite(list, attr, cfg.world.visionLive, target)) units += 1;
        return false;
      });
    } catch (_) { /* 不在普通地图时只改配置，进图后再生效 */ }
    if (!configs && !units) throw Error('没找到可以改的视野字段（版本可能变了）');
    combat.vision.core = core || null;
    combat.vision.target = target;
    return { target, configs, units };
  }

  /* ---- 技能冷却 ---- */

  /** 我方 10 个英雄自己的技能编号（只改这些，怪物不受影响）。 */
  function heroSkillIds(cfg) {
    const ids = new Set();
    try {
      const world = combatWorld(cfg);
      const core = world[cfg.world.core];
      const manager = core[cfg.world.manager]();
      const team = core[cfg.world.team](world[cfg.world.player]);
      const members = manager[cfg.world.members](team) || [];
      for (const id of members) {
        const unit = core[cfg.world.read](id);
        const attr = unit ? unit[cfg.world.attribute] : null;
        const list = attr && attr[cfg.world.attributeList || 'CNIS'];
        if (!Array.isArray(list)) continue;
        for (const row of list) {
          const skillKey = cfg.world.skillId || 'ZLTC';
          if (row && Number.isSafeInteger(row[skillKey])) ids.add(row[skillKey]);
        }
      }
    } catch (_) { /* 不在普通地图时返回空的，调用方会退化成全局 */ }
    return ids;
  }

  function cooldownRows(cfg) {
    const manager = combatManager(cfg);
    const skills = manager[cfg.config.table](cfg.config.skillTable);
    if (!Array.isArray(skills)) throw Error('技能配置表（' + cfg.config.skillTable + '）读取失败');
    const wanted = heroSkillIds(cfg);
    const rows = [];
    for (const row of skills) {
      if (!row) continue;
      const id = row[cfg.config.skill];
      if (!Number.isSafeInteger(id)) continue;
      // 只改我方英雄的技能；拿不到队伍时才退化成全局
      if (wanted.size && !wanted.has(id)) continue;
      const cd = row[cfg.config.cooldown];
      if (!cd || typeof cd !== 'object') continue;
      const ms = cd[cfg.config.ms];
      if (typeof ms !== 'number' || !Number.isFinite(ms)) continue;
      // 有冷却的技能都算（原来只认 40~60 秒的大招）
      if (ms < 1000) continue;
      rows.push({ id, row, cd });
    }
    if (!rows.length) throw Error('技能配置里没找到可改的冷却（版本可能变了）');
    return { manager, skills, rows, heroes: wanted.size };
  }

  /** 原生冷却读取入口（参考工具：az_lengqueDuquMokuai / az_lengqueDuquFangfa）。 */
  function cooldownLive(cfg) {
    const world = combatWorld(cfg);
    if (typeof world[cfg.cooldown.config] !== 'function') throw Error('当前战斗配置入口还没就绪');
    const config = world[cfg.cooldown.config]();
    if (!config || typeof config[cfg.cooldown.skill] !== 'function') throw Error('当前战斗技能配置还没就绪');
    return { world, config };
  }

  function applyCooldown(cfg, seconds) {
    const ms = Math.round(seconds * 1000);
    const range = (cfg.ranges && cfg.ranges.cooldown) || [0.1, 3600];
    if (!(seconds >= range[0] && seconds <= range[1])) {
      throw Error('冷却需要在 ' + range[0] + ' - ' + range[1] + ' 秒之间');
    }
    const scan = cooldownRows(cfg);
    let live = null;
    try { live = cooldownLive(cfg); } catch (_) { live = null; }
    const list = combat.cooldown.written;
    let ok = 0;
    for (const item of scan.rows) {
      let cd = item.cd;
      if (live) {
        let row = null;
        try { row = live.config[cfg.cooldown.skill](item.id); } catch (_) { row = null; }
        if (row && row[cfg.cooldown.field]) cd = row[cfg.cooldown.field];
      }
      if (cd && combatWrite(list, cd, cfg.cooldown.ms, ms)) ok += 1;
    }
    if (!ok) throw Error('技能冷却字段不可写（当前版本可能变了）');
    combat.cooldown.config = live ? live.config : null;
    return { skills: ok, ms, live: !!live };
  }

  /* ---- 对外：开关 / 快照 / 守护 ---- */

  function stopCombat(name) {
    const item = state.combat[name];
    const box = combat[name];
    if (name === 'damage') {
      stopDamage();
      item.enabled = false;
      item.reason = '已关闭';
      return;
    }
    if (name === 'shield') {
      stopShield();
      item.enabled = false;
      item.reason = '已关闭';
      return;
    }
    try { box.written = combatRestore(box.written); } catch (_) { /* 留到下一轮再试 */ }
    if (name === 'vision') box.core = null;
    else box.config = null;
    item.enabled = false;
    item.reason = box.written.length
      ? '已关闭（有 ' + box.written.length + ' 项没还原，重进游戏后会自然恢复）'
      : '已关闭';
  }

  function setCombat(name, value) {
    // 注意：实测倍伤 / 免伤 / 冷却 / 视野都会被服务端校验（弹「战斗数据与服务器不一致」），
    // 功能保留但默认关，要不要开由使用者自己决定。
    const cfg = combatConfig();
    const item = state.combat[name];
    // 免伤只改伤害数值，不碰单位属性，可以和地图刷新一起开
    if (name !== 'shield' && (state.mob.enabled || state.boss.enabled)) {
      throw Error('请先关闭小怪和 BOSS 地图刷新，再修改战斗参数');
    }
    const number = Number(value);
    const close = name === 'vision'
      ? (!Number.isFinite(number) || number < 0)
      : (!Number.isFinite(number) || number <= 0);
    if (close) {
      stopCombat(name);
      return { ok: true, message: '已关闭' };
    }
    if (item.enabled) stopCombat(name);

    if (name === 'damage') {
      const info = setDamage(cfg, number);
      item.enabled = true;
      item.value = number;
      item.reason = '已开启：我方英雄伤害 ×' + number + '（队伍 ' + info.heroes + ' 人）';
      return { ok: true, message: item.reason };
    }
    if (name === 'shield') {
      const info = setShield(cfg, number);
      item.enabled = true;
      item.value = number;
      item.reason = '已开启：我方英雄受到伤害 -' + number + '%（队伍 ' + info.heroes + ' 人）';
      return { ok: true, message: item.reason };
    }
    if (name === 'vision') {
      if (number !== 0 && number !== 1) throw Error('视野只有 0（不让怪发现）和 1（扩大索敌）两种');
      const info = applyVision(cfg, number);
      item.enabled = true;
      item.value = number;
      item.reason = number === 0
        ? '已开启：初始索敌 0（怪不会主动发现你）'
        : '已开启：索敌半径 ' + info.target + '（配置 ' + info.configs + ' 项 / 单位 ' + info.units + ' 只）';
      return { ok: true, message: item.reason };
    }
    const info = applyCooldown(cfg, number);
    item.enabled = true;
    item.value = number;
    item.reason = '已开启：' + info.skills + ' 个技能冷却 ' + number + ' 秒'
      + (info.live ? '（当前战斗立即生效）' : '（改的是全局技能表，下次进战斗生效）');
    return { ok: true, message: item.reason };
  }

  function combatActive() {
    // 免伤不算「会改战斗数值」的那类：它只削伤害数字，不和地图刷新冲突
    return ['damage', 'vision', 'cooldown'].some(name => state.combat[name].enabled);
  }

  function tickCombat() {
    // 战斗参数不依赖红包服务是否可用，只要有当前版本映射就能跑
    if (!mapping()) return;
    if (now() < combatSyncAt) return;
    combatSyncAt = now() + 2000;

    // 关掉时有没还原成功的项，继续尝试
    for (const name of ['vision', 'cooldown']) {
      const box = combat[name];
      if (!state.combat[name].enabled && box.written.length) {
        try { box.written = combatRestore(box.written); } catch (_) { /* ignore */ }
      }
    }

    if (state.combat.damage.enabled || state.combat.shield.enabled) {
      const box = combat.damage;
      if (!box.hook || box.hook.owner[box.hook.key] !== box.hook.wrapper) {
        const text = '已停止：游戏重新加载了伤害结算入口，请重新开启';
        if (state.combat.damage.enabled) {
          stopDamage();
          state.combat.damage.enabled = false;
          state.combat.damage.reason = text;
        }
        if (state.combat.shield.enabled) {
          stopShield();
          state.combat.shield.enabled = false;
          state.combat.shield.reason = text;
        }
      }
    }

    if (state.combat.vision.enabled && combat.vision.core) {
      let alive = true;
      try {
        const cfg = combatConfig();
        alive = combatWorld(cfg)[cfg.world.core] === combat.vision.core;
      } catch (_) { alive = false; }
      if (!alive) {
        stopCombat('vision');
        state.combat.vision.reason = '已停止：地图已切换，请重新开启';
      }
    }

    if (state.combat.cooldown.enabled && combat.cooldown.config) {
      let alive = true;
      try {
        const cfg = combatConfig();
        const world = combatWorld(cfg);
        alive = typeof world[cfg.cooldown.config] === 'function'
          && world[cfg.cooldown.config]() === combat.cooldown.config;
      } catch (_) { alive = false; }
      if (!alive) {
        stopCombat('cooldown');
        state.combat.cooldown.reason = '已停止：战斗配置已刷新，请重新开启';
      }
    }
  }

  /* ------------------------------------------------------------------ *
   * 七、快速复活：全灭面板（KYUK）出现后自动复活
   *
   * 三种方式（映射见 assist_mapping.json 的 revive 段）：
   *   item = 调 yx_fuwu.RZFJ(false)，等同面板上的「复活」，消耗复活道具；
   *   ad   = 调 yx_fuwu.RZFJ(true)，等同「看广告复活」，不消耗道具；
   *   camp = 调面板的「放弃/返回」(HYHO)，回营地后英雄自动复活，
   *          再调地图重进 XIPH 回到原地图继续刷。
   * 延迟 delayMs 可配（0~10 秒），也就是「1 秒复活 / 2 秒复活」。
   * ------------------------------------------------------------------ */

  let reviveScanAt = 0;

  function reviveConfig() {
    const build = mapping();
    if (!build) {
      state.missingMapping = true;
      throw Error('该游戏版本还没有字段映射，需要更新映射表');
    }
    const cfg = build.revive;
    if (!cfg || !cfg.panel || !cfg.abandon) {
      throw Error('该游戏版本还没有快速复活映射，需要更新映射表');
    }
    return cfg;
  }

  function gameBag(cfg) {
    const system = window.System;
    if (!system || typeof system.get !== 'function') {
      throw Error('游戏还没加载完成，请进入游戏主界面后重试');
    }
    const mod = system.get(cfg.holderModule);
    const bag = mod && mod[cfg.holder];
    if (!bag) throw Error('游戏服务还没就绪，请进入游戏主界面后重试');
    return bag;
  }

  /**
   * 找当前弹出的「确认 / 确定」按钮，返回屏幕坐标（CSS 像素）。
   * 全灭面板点「放弃」后，游戏会再弹一个确认小窗；页内派发的假点击游戏不认，
   * 所以这里只算坐标，由界面用 CDP 真鼠标点一下。
   */
  function findConfirmPoint() {
    try {
      const cc = window.cc;
      const scene = cc && cc.director && typeof cc.director.getScene === 'function'
        ? cc.director.getScene() : null;
      if (!scene) return null;
      let W = 700, H = 1501.6;
      try {
        if (cc.view && typeof cc.view.getVisibleSize === 'function') {
          const vs = cc.view.getVisibleSize();
          if (vs && vs.width > 0 && vs.height > 0) { W = vs.width; H = vs.height; }
        }
      } catch (_) { /* 用默认设计分辨率 */ }
      const stack = [scene];
      let guard = 0;
      while (stack.length && guard < 6000) {
        guard += 1;
        const node = stack.pop();
        if (!node || node.activeInHierarchy === false) continue;
        const comps = node._components || [];
        for (let i = 0; i < comps.length; i += 1) {
          let cname = '';
          try { cname = (cc.js && typeof cc.js.getClassName === 'function' && cc.js.getClassName(comps[i])) || ''; } catch (_) { cname = ''; }
          if (cname !== 'cc.Label') continue;
          const text = String(comps[i].string || '').trim();
          if (text !== '确认' && text !== '确定') continue;
          const wp = node.getWorldPosition ? node.getWorldPosition() : null;
          if (!wp) continue;
          return {
            x: Math.round(wp.x / W * window.innerWidth * 10) / 10,
            y: Math.round((H - wp.y) / H * window.innerHeight * 10) / 10,
            text,
          };
        }
        const kids = node.children || [];
        for (let j = 0; j < kids.length; j += 1) stack.push(kids[j]);
      }
    } catch (_) { /* 没有就算了 */ }
    return null;
  }

  /** 全灭界面：按 cocos 类名找（KYUK），找不到再用「放弃方法 + 放弃按钮」兜底。 */
  function findDefeatPanel(cfg) {
    const cc = window.cc;
    const scene = cc && cc.director && typeof cc.director.getScene === 'function'
      ? cc.director.getScene() : null;
    if (!scene) return null;
    const stack = [scene];
    let guard = 0;
    while (stack.length && guard < 8000) {
      guard += 1;
      const node = stack.pop();
      if (!node || node.activeInHierarchy === false) continue;
      const comps = node.components || node._components || [];
      for (let i = 0; i < comps.length; i += 1) {
        const comp = comps[i];
        if (!comp) continue;
        let name = '';
        try {
          name = (cc.js && typeof cc.js.getClassName === 'function' && cc.js.getClassName(comp)) || '';
        } catch (_) { name = ''; }
        if (name && name === cfg.panel) return comp;
        if (!name && typeof comp[cfg.abandon] === 'function' && comp[cfg.abandonButton]) return comp;
      }
      const children = node.children || [];
      for (let j = 0; j < children.length; j += 1) stack.push(children[j]);
    }
    return null;
  }

  /** 调原生复活（useAd=true 就是看广告那种），带超时保护。 */
  function callNativeRevive(cfg, useAd) {
    const yx = gameBag(cfg)[cfg.heroService];
    if (!yx || typeof yx[cfg.revive] !== 'function') {
      throw Error('英雄服务里没有复活接口（' + cfg.revive + '），该版本可能改了');
    }
    return new Promise(resolve => {
      let settled = false;
      const finish = value => {
        if (settled) return;
        settled = true;
        resolve(value);
      };
      const timer = setTimeout(() => finish({ ok: false, error: '复活请求超时' }), 8000);
      try {
        yx[cfg.revive](!!useAd, function (ok, resp) {
          clearTimeout(timer);
          finish({ ok: !!ok, code: resp && resp.RUKU ? resp.RUKU.WCHC : undefined });
        });
      } catch (cause) {
        clearTimeout(timer);
        finish({ ok: false, error: msgOf(cause) });
      }
    });
  }

  /** 手动复活：点一下按钮就按免费极速流程走一遍（不依赖自动开关）。 */
  async function manualRevive() {
    const cfg = reviveConfig();
    const world = window[mapConfig().holder];
    const core = world[mapConfig().core];
    const spot = teamLeaderSpot();
    if (!spot) throw Error('读不到队伍坐标（先进入地图）');
    const box = state.revive;

    // 全灭：游戏不接受传送，只能放弃回城
    const panel = findDefeatPanel(cfg);
    if (panel) {
      box.reason = '全灭：正在放弃回城…';
      await reviveByCamp(cfg, panel);
      box.reason = '已回城复活，等地图就绪…';
      await waitWorldReady(20000);
      try { teleportNoFx(spot.x, spot.y); } catch (_) { try { teleportFire(spot.x, spot.y); } catch (_) { /* ignore */ } }
      await new Promise(r => setTimeout(r, 400));
      box.count += 1;
      box.lastResult = '手动复活（全灭回城）';
      return '全灭：已回城复活并传回原地';
    }

    const dead0 = deadHeroCount();
    if (!dead0) return '现在没有英雄倒地，不需要复活';

    // ① 先试只结束战斗（原地复活，0.1 秒，不传送）
    box.reason = '手动复活中…';
    leaveBattle();
    const tMemk = now();
    while (now() - tMemk < 800) {
      await new Promise(r => setTimeout(r, 40));
      if (deadHeroCount() === 0) {
        box.count += 1;
        box.lastResult = '手动复活完成（原地）';
        return '已原地复活 ' + dead0 + ' 个英雄（0.1 秒）';
      }
    }

    // ② 没起来就走营地瞬移
    const camp = campSpot(core, cfg.campTeleport);
    if (!camp) throw Error('读不到营地坐标（TCLA/CLAT）');
    leaveBattle();
    teleportNoFx(camp.x, camp.y);
    await new Promise(r => setTimeout(r, 80));
    try { teleportNoFx(spot.x, spot.y); } catch (_) { /* ignore */ }

    const t = now();
    while (now() - t < 3000) {
      await new Promise(r => setTimeout(r, 50));
      if (deadHeroCount() === 0) {
        box.count += 1;
        box.lastResult = '手动复活完成';
        return '已复活 ' + dead0 + ' 个英雄，已回到原地';
      }
    }

    // 兜底：等到真的复活再回来
    leaveBattle();
    teleportNoFx(camp.x, camp.y);
    const t2 = now();
    while (now() - t2 < 5000) {
      await new Promise(r => setTimeout(r, 80));
      if (deadHeroCount() === 0) break;
    }
    try { teleportNoFx(spot.x, spot.y); } catch (_) { /* ignore */ }
    box.count += 1;
    box.lastResult = '手动复活完成（兜底）';
    return deadHeroCount() === 0 ? '已复活并回到原地' : '复活没成功（营地坐标可能不对）';
  }

  /** 游戏自带的「立即复活」：RZFJ 发请求 + HVLS 应用服务器返回，原地把倒地英雄拉起来。
   *  只消耗绿币（映射里的 currencyId），不传送、怪不回血。 */
  function instantRevive(cfg) {
    const yx = gameBag(cfg)[cfg.heroService];
    const api = (cfg.instant && cfg.instant.api) || 'RZFJ';
    const applyName = (cfg.instant && cfg.instant.apply) || 'HVLS';
    if (!yx || typeof yx[api] !== 'function') {
      throw Error('英雄服务里没有复活接口（' + api + '），该版本可能改了');
    }
    return new Promise(resolve => {
      let settled = false;
      const finish = value => { if (!settled) { settled = true; resolve(value); } };
      const timer = setTimeout(() => finish({ ok: false, error: '复活请求超时' }), 8000);
      try {
        yx[api](false, function (ok, resp) {
          clearTimeout(timer);
          const code = resp && resp.RUKU ? resp.RUKU.WCHC : undefined;
          if (ok && resp) {
            // 和游戏面板一样：用服务器返回的战斗数据把客户端状态刷新过来
            try {
              const world = window[mapConfig().holder];
              if (world && typeof world[applyName] === 'function') world[applyName](resp.EADX);
            } catch (_) { /* ignore */ }
          }
          finish({ ok: !!ok, code });
        });
      } catch (cause) {
        clearTimeout(timer);
        finish({ ok: false, error: msgOf(cause) });
      }
    });
  }

  /** 回营地复活：点「放弃」回营地，再自动回原地图。 */
  function reviveByCamp(cfg, panel) {
    const mapCfg = mapConfig();
    const holder = window[mapCfg.holder];
    const stateObj = holder && mapCfg.state ? holder[mapCfg.state] : null;
    const mapId = stateObj && mapCfg.mapId ? stateObj[mapCfg.mapId] : null;
    if (typeof panel[cfg.abandon] !== 'function') throw Error('全灭面板没有「放弃/返回」入口');
    state.revive.savedMapId = mapId || 0;
    panel[cfg.abandon]();
    return new Promise((resolve, reject) => {
      setTimeout(() => {
        try {
          if (!state.revive.savedMapId) {
            resolve({ ok: true, how: '已放弃回营地（没记到地图编号，不自动回图）' });
            return;
          }
          if (!holder || typeof holder[mapCfg.enter] !== 'function') {
            throw Error('游戏里没有地图重进接口，无法回到地图 ' + state.revive.savedMapId);
          }
          const result = fastEnterMap(holder, mapCfg, state.revive.savedMapId);
          if (result === false) throw Error('回营地后返回地图失败（游戏返回 false）');
          resolve({ ok: true, how: '已放弃回营地复活并返回地图 ' + state.revive.savedMapId });
        } catch (cause) {
          reject(cause);
        }
      }, 2500);
    });
  }

  function reviveReset() {
    const box = state.revive;
    box.phase = '';
    box.pendingAt = 0;
    box.nextTryAt = 0;
    box.busy = false;
  }


  /** 营地坐标：游戏自己的返回点（TCLA / CLAT，返回按钮用的就是它）。 */
  function campSpot(core, cfg) {
    // 有些地图（如海底）游戏给的 CLAT 不在当前区域的坐标系里，可以在映射表里写死
    try {
      const mapCfg = mapConfig();
      const holder = window[mapCfg.holder];
      const st = holder && mapCfg.state ? holder[mapCfg.state] : null;
      const id = st ? st[mapCfg.mapId] : null;
      const table = (cfg && cfg.byMap) || {};
      const fixed = id === null || id === undefined ? null : table[String(id)];
      if (fixed && fixed.length === 2) return { x: Number(fixed[0]), y: Number(fixed[1]) };
    } catch (_) { /* 读不到就用游戏自己的 */ }
    const names = (cfg && cfg.campCoords) || ['TCLA', 'CLAT'];
    for (const name of names) {
      try {
        if (!core || typeof core[name] !== 'function') continue;
        const p = core[name]();
        if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) return p;
      } catch (_) { /* 换下一个 */ }
    }
    return null;
  }

  /** 游戏传送请求：VKXX.ZAVH(x, y, true, cb)（服务端认可，不会哈希不一致）。 */
  function teleportTo(x, y) {
    const mapCfg = mapConfig();
    const world = window[mapCfg.holder];
    const api = 'ZAVH';
    if (!world || typeof world[api] !== 'function') {
      throw Error('游戏没有传送入口（VKXX.' + api + '）');
    }
    return new Promise((resolve, reject) => {
      let done = false;
      const timer = setTimeout(() => {
        if (done) return;
        done = true;
        reject(Error('传送请求超时（游戏可能还在加载）'));
      }, 8000);
      try {
        world[api](x, y, true, function () {
          if (done) return;
          done = true;
          clearTimeout(timer);
          resolve(1);
        });
      } catch (cause) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        reject(cause);
      }
    });
  }


  /** 真·瞬移：ZAVH 的 t=false 分支——不发加载动画、直接改位置，服务端请求照样会发。 */
  function teleportNoFx(x, y) {
    const mapCfg = mapConfig();
    const world = window[mapCfg.holder];
    if (!world || typeof world.ZAVH !== 'function') throw Error('游戏没有传送入口（VKXX.ZAVH）');
    dropLoading();
    world.ZAVH(x, y, false, function () { /* 回调是同步的 */ });
  }

  /** 结束当前战斗状态（游戏自己的回城第一步，缺了它营地不会复活）。 */
  function leaveBattle() {
    try {
      const world = window[mapConfig().holder];
      if (world && typeof world.MEMK === 'function') world.MEMK();
      return true;
    } catch (_) { return false; }
  }

  /** 发传送请求但不等加载结束：服务端收到就生效，位置通常 0.1~0.3 秒就变。 */
  function teleportFire(x, y) {
    const mapCfg = mapConfig();
    const world = window[mapCfg.holder];
    if (!world || typeof world.ZAVH !== 'function') throw Error('游戏没有传送入口（VKXX.ZAVH）');
    // 游戏在加载状态会直接忽略传送请求，先清掉
    dropLoading();
    world.ZAVH(x, y, true, function () { /* 不等它 */ });
  }

  /** 等地图真正加载完（core 就绪 + 不在加载/切图），连续 3 次才算稳。 */
  async function waitWorldReady(limitMs) {
    const began = now();
    let ok = 0;
    while (now() - began < limitMs) {
      await new Promise(r => setTimeout(r, 150));
      let ready = false;
      try {
        const mapCfg2 = mapConfig();
        const w = window[mapCfg2.holder];
        const st = w && mapCfg2.state ? w[mapCfg2.state] : null;
        const c = w && w[mapCfg2.core];
        ready = !!(c && typeof c[mapCfg2.manager] === 'function' && c[mapCfg2.manager]())
          && !(st && st.MAOP)
          && !(st && mapCfg2.changing && st[mapCfg2.changing]);
      } catch (_) { ready = false; }
      ok = ready ? ok + 1 : 0;
      if (ok >= 3) return true;
    }
    return false;
  }

  /** 快速复活日志（最近 12 条，方便排查） */
  function reviveLog(msg) {
    try {
      const box = state.revive;
      if (!box.log) box.log = [];
      let pos = null;
      try { pos = teamLeaderSpot(); } catch (_) { pos = null; }
      box.log.push({
        t: new Date().toTimeString().slice(0, 8),
        dead: (() => { try { return deadHeroCount(); } catch (_) { return -1; } })(),
        pos: pos ? [Math.round(pos.x), Math.round(pos.y)] : null,
        msg: String(msg || '').slice(0, 40),
      });
      if (box.log.length > 12) box.log.splice(0, box.log.length - 12);
    } catch (_) { /* ignore */ }
  }

  /** 隐藏重进地图时的过场加载页（UICanvas/Transition/MapTransitionView）。 */
  function hideMapTransition() {
    try {
      const cc = window.cc;
      const scene = cc && cc.director && typeof cc.director.getScene === 'function'
        ? cc.director.getScene() : null;
      if (!scene) return;
      const stack = [scene];
      let guard = 0;
      while (stack.length && guard < 5000) {
        guard += 1;
        const n = stack.pop();
        if (!n) continue;
        const nm = String(n.name || '');
        if (n.active && (nm === 'MapTransitionView' || nm === 'Transition')) n.active = false;
        const kids = n.children || [];
        for (let i = 0; i < kids.length; i += 1) stack.push(kids[i]);
      }
    } catch (_) { /* ignore */ }
  }

  /** 重进地图：隐藏加载页，并在新地图数据就绪时立刻返回（不等过场动画）。 */
  /** 取服务容器（模块导出里的 HQQD，里面有 OXIA 等 900 多个服务）。 */
  function servicesBag() {
    try {
      const system = window.System;
      if (!system || typeof system.entries !== 'function') return null;
      for (const pair of system.entries()) {
        const ex = pair[1];
        if (ex && typeof ex === 'object' && ex.HQQD && ex.HQQD.VKXX) return ex.HQQD;
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  /**
   * 重进地图时把过场加载页压成透明 + 动画加速：玩家只看到黑屏，看不到加载页。
   *
   * 两条红线（11-19 踩过）：
   *   1. 绝不能把 MapTransitionView / Transition 设 active=false —— 过场完成回调会被掐掉，
   *      XIPH 的 Promise 永远 pending，之后所有切图卡死，只能重开窗口。
   *   2. 必须等游戏自己的进图 Promise 结束再还原，中途还原/打断同样会卡住流程。
   * 所以这里只改两样「纯表现」的东西：节点上的 UIOpacity（透明度）、sp.Skeleton.timeScale。
   * 节点始终 active、动画始终播完、回调始终触发，只是画面上看不见。
   *
   * 实测进图总耗时 4.6 秒，其中「真在加载」的是：
   *   XIMY 地图地块图加载 1.6s + VSMO 等地块图就绪 0.6s
   * 纯动画只有 IQIP 退场 0.56s（外加开场的出场动画）。
   */
  const TRANSITION_SPEED = 4;
  let transitionOpacityClass = null;

  /** 这个版本 cc.UIOpacity 没挂到 cc 上，从预制体里已有的 UIOpacity 组件反查类。 */
  function opacityClassOf(root) {
    if (transitionOpacityClass) return transitionOpacityClass;
    try {
      const cc = window.cc;
      if (cc && cc.UIOpacity) { transitionOpacityClass = cc.UIOpacity; return transitionOpacityClass; }
    } catch (_) { /* 继续反查 */ }
    try {
      const stack = [root];
      let guard = 0;
      while (stack.length && guard < 400) {
        guard += 1;
        const n = stack.pop();
        if (!n) continue;
        const comps = n._components || [];
        for (let i = 0; i < comps.length; i += 1) {
          let name = '';
          try { name = window.cc.js.getClassName(comps[i]) || ''; } catch (_) { name = ''; }
          if (name === 'cc.UIOpacity') {
            transitionOpacityClass = comps[i].constructor;
            return transitionOpacityClass;
          }
        }
        const kids = n.children || [];
        for (let i = 0; i < kids.length; i += 1) stack.push(kids[i]);
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  function setTransitionVisible(visible) {
    try {
      const root = transitionRootNode();
      if (!root) return false;
      const cls = opacityClassOf(root);
      if (!cls) return false;
      const op = root.getComponent(cls) || root.addComponent(cls);
      if (!op) return false;
      const target = visible ? 255 : 0;
      if (op.opacity !== target) op.opacity = target;
      return true;
    } catch (_) { return false; }
  }

  function transitionRootNode() {
    try {
      const scene = window.cc && window.cc.director && window.cc.director.getScene();
      if (!scene) return null;
      const stack = [scene];
      let guard = 0;
      while (stack.length && guard < 6000) {
        guard += 1;
        const n = stack.pop();
        if (!n) continue;
        if (String(n.name || '') === 'Transition') return n;
        const kids = n.children || [];
        for (let i = 0; i < kids.length; i += 1) stack.push(kids[i]);
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  /** 过场预制体里的骨骼动画组件（出场/退场都是用它们播的）。 */
  function transitionSkeletons() {
    const out = [];
    try {
      const root = transitionRootNode();
      if (!root) return out;
      const stack = [root];
      let guard = 0;
      while (stack.length && guard < 3000) {
        guard += 1;
        const n = stack.pop();
        if (!n) continue;
        const comps = n._components || [];
        for (let i = 0; i < comps.length; i += 1) {
          const c = comps[i];
          // sp.Skeleton：有 timeScale 也有 setAnimation
          if (c && typeof c.timeScale === 'number' && typeof c.setAnimation === 'function') out.push(c);
        }
        const kids = n.children || [];
        for (let i = 0; i < kids.length; i += 1) stack.push(kids[i]);
      }
    } catch (_) { /* ignore */ }
    return out;
  }

  /** 重进地图期间隐藏加载页 + 动画加速；游戏进图 Promise 结束（动画播完）后还原。 */
  function fastTransitionWhile(running) {
    const touched = [];
    const tick = () => {
      try {
        setTransitionVisible(false);
        for (const skel of transitionSkeletons()) {
          if (skel.timeScale === TRANSITION_SPEED) continue;
          if (touched.indexOf(skel) < 0) touched.push(skel);
          skel.timeScale = TRANSITION_SPEED;
        }
      } catch (_) { /* ignore */ }
    };
    tick();
    const timer = setInterval(tick, 50);
    let stopped = false;
    const restore = () => {
      if (stopped) return;
      stopped = true;
      clearInterval(timer);
      setTimeout(() => {
        for (const skel of touched) {
          try { if (skel && skel.isValid !== false && skel.timeScale !== 1) skel.timeScale = 1; } catch (_) { /* ignore */ }
        }
        // 动画收完再把加载页恢复不透明：下次手动切图、报错弹窗照常显示
        setTransitionVisible(true);
      }, 200);
    };
    let settled = false;
    let hasPromise = false;
    try {
      if (running && typeof running.then === 'function') {
        hasPromise = true;
        running.then(() => { settled = true; restore(); }, () => { settled = true; restore(); });
      }
    } catch (_) { /* ignore */ }
    if (!hasPromise) {
      // 游戏这次没返回 Promise（或直接拒绝）：别让它一直 4 倍速，3 秒后还原
      setTimeout(restore, 3000);
      return restore;
    }
    const startedAt = now();
    const guard = () => {
      if (stopped) return;
      if (settled || now() - startedAt > 15000) { restore(); return; }
      setTimeout(guard, 500);
    };
    setTimeout(guard, 1000);
    return restore;
  }

  /** 当前地图里第一个「已解锁」的传送点编号（用于 LUSS 重载地图）。 */
  function pickTeleportPoint(mapId) {
    const rl = (mapping().map && mapping().map.reload) || {};
    try {
      const bag = servicesBag();
      const svc = bag && bag[rl.service || 'OXIA'];
      const table = svc && svc[rl.table || 'OTWF'];
      const points = table && typeof table.get === 'function' ? table.get(mapId) : null;
      let pick = 0;
      if (points && typeof points.forEach === 'function') {
        points.forEach(function (point, id) {
          if (!pick && point && point[rl.unlocked || 'KFUG'] === true) pick = Number(id);
        });
      }
      return pick;
    } catch (_) { return 0; }
  }

  /**
   * 重进地图（主世界）：XIPH(当前地图) 是空操作，改用 LUSS(当前地图, 传送点)，
   * 这个会真正触发场景重载。回调 6 秒左右返回；拿不到传送点就退回 XIPH。
   */
  function reloadByTeleport(holder, cfg, mapId) {
    const rl = (mapping().map && mapping().map.reload) || {};
    const api = holder[rl.api || 'LUSS'];
    const pointId = pickTeleportPoint(mapId);
    if (typeof api !== 'function' || !pointId) {
      return Promise.resolve(fastEnterMap(holder, cfg, mapId));
    }
    let saved = null;
    try { saved = teamLeaderSpot(); } catch (_) { saved = null; }
    return new Promise(resolve => {
      let finished = false;
      const finish = value => { if (!finished) { finished = true; resolve(value); } };
      try {
        api.call(holder, mapId, pointId, () => finish(true));
      } catch (_) {
        finish(false);
        return;
      }
      const t0 = now();
      let sawLoading = false;
      const check = () => {
        if (finished) return;
        try {
          const st = holder[cfg.state];
          if (st && st.MAOP === true) sawLoading = true;
        } catch (_) { /* ignore */ }
        if (sawLoading && now() - t0 > 1200 && !(holder[cfg.state] || {}).MAOP) finish(true);
        if (now() - t0 > 20000) finish(sawLoading);
        setTimeout(check, 150);
      };
      setTimeout(check, 200);
    }).then(ok => {
      // 重载后落在传送点，把队伍分段瞬移回原来刷怪的位置
      if (!ok || !saved) return ok;
      return new Promise(resolve => {
        const t0 = now();
        const hop = () => {
          let here = null;
          try { here = teamLeaderSpot(); } catch (_) { here = null; }
          if (!here) {
            if (now() - t0 > 6000) { resolve(true); return; }
            setTimeout(hop, 300);
            return;
          }
          const dx = saved.x - here.x;
          const dy = saved.y - here.y;
          const dist = Math.hypot(dx, dy);
          if (dist < 380 || now() - t0 > 12000) { resolve(true); return; }
          const step = Math.min(dist, 2600);
          try {
            teleportNoFx(here.x + dx / dist * step, here.y + dy / dist * step);
          } catch (_) { /* ignore */ }
          setTimeout(hop, 260);
        };
        setTimeout(hop, 900);
      });
    });
  }

  function fastEnterMap(holder, cfg, mapId) {
    const name = (cfg && cfg.enter) || 'XIPH';
    if (!holder || typeof holder[name] !== 'function') {
      throw Error('游戏里没有地图重进接口，需要更新映射');
    }
    const mapCfg = mapConfig();
    const before = holder[mapCfg.core];
    let raw = null;
    try { raw = holder[name](mapId); } catch (cause) { throw cause; }
    // 重进期间把加载页压透明（黑屏）+ 过场动画 4 倍速，玩家看不到加载页。
    // 节点保持 active、动画照常播完，真在加载的地图资源没法跳过。
    // 注意：绝对不能像 11-19 那样把过场节点 active=false —— 会掐掉完成回调、之后再也切不了图。
    try { fastTransitionWhile(raw); } catch (_) { /* 隐藏/加速失败不影响进图 */ }
    // 旧版说明：XIPH 返回 Promise，成功 resolve true，并且 VKXX.ZRRU 会换成新对象。
    // 两个信号谁先到都算成功；Promise 明确返回 false 才算失败，且等它结束再决定，避免重复调用。
    let promiseResult = null;
    let settled = false;
    try {
      if (raw && typeof raw.then === 'function') {
        raw.then(v => { promiseResult = v === true; settled = true; },
                 () => { promiseResult = false; settled = true; });
      }
    } catch (_) { /* ignore */ }
    return new Promise(resolve => {
      const t0 = now();
      let done = false;
      const finish = value => {
        if (done) return;
        done = true;
        resolve(value);
      };
      const check = () => {
        if (done) return;
        let world = null;
        try { world = holder[mapCfg.core]; } catch (_) { world = null; }
        if (world && world !== before) { finish(true); return; }
        if (settled) {
          if (promiseResult === true) {
            // 游戏说成功但世界对象还没换：再等 2 秒看 ZRRU
            if (now() - t0 > 12000) finish(true);
            else setTimeout(check, 100);
            return;
          }
          finish(false);
          return;
        }
        if (now() - t0 > 12000) { finish(false); return; }
        setTimeout(check, 100);
      };
      setTimeout(check, 100);
    });
  }

  /** 和游戏自己的「回城」一样：先调 MEMK（结束战斗状态），再传送到营地。 */
  function campTeleport(camp) {
    leaveBattle();
    reviveLog('MEMK + 瞬移营地');
    teleportNoFx(camp.x, camp.y);
    return true;
  }

  /** 隐藏传送时的「落地动画」：每个英雄脚下那个转圈特效（TransitionSprite）。 */
  function hideLanding() {
    try {
      const cc = window.cc;
      const scene = cc && cc.director && typeof cc.director.getScene === 'function'
        ? cc.director.getScene() : null;
      if (!scene) return;
      const stack = [scene];
      let guard = 0;
      while (stack.length && guard < 4000) {
        guard += 1;
        const n = stack.pop();
        if (!n) continue;
        if (n.active && String(n.name || '').indexOf('TransitionSprite') >= 0) n.active = false;
        const kids = n.children || [];
        for (let i = 0; i < kids.length; i += 1) stack.push(kids[i]);
      }
    } catch (_) { /* 没有就算了 */ }
  }

  /** 传送落地时游戏把英雄淡出/藏起来，把它们的颜色和显示拉回来。 */
  function forceHeroVisible() {
    try {
      const cc = window.cc;
      const scene = cc && cc.director && typeof cc.director.getScene === 'function'
        ? cc.director.getScene() : null;
      if (!scene) return;
      const stack = [scene];
      let guard = 0;
      while (stack.length && guard < 4000) {
        guard += 1;
        const n = stack.pop();
        if (!n) continue;
        if (/^HeroUnit/.test(String(n.name || ''))) {
          if (!n.active) n.active = true;
          try {
            const sp = n.getComponent ? n.getComponent(cc.Sprite) : null;
            if (sp && sp.color && sp.color.a !== 255) sp.color = cc.color(255, 255, 255, 255);
          } catch (_) { /* ignore */ }
          try {
            const op = n.getComponent ? n.getComponent(cc.UIOpacity) : null;
            if (op && op.opacity !== 255) op.opacity = 255;
          } catch (_) { /* ignore */ }
        }
        const kids = n.children || [];
        for (let i = 0; i < kids.length; i += 1) stack.push(kids[i]);
      }
    } catch (_) { /* ignore */ }
  }

  /** 传送的落地特效是延迟 0.5~1 秒才冒出来的，要持续按住 3 秒。 */
  function suppressLanding(ms) {
    const until = now() + (ms || 3000);
    const tick = () => {
      try { hideLanding(); } catch (_) { /* ignore */ }
      try { forceHeroVisible(); } catch (_) { /* ignore */ }
      try {
        const world = window[mapConfig().holder];
        if (world) {
          if (world.UMID) world.UMID.MAOP = false;
          const st = world[mapConfig().state];
          if (st) st.MAOP = false;
        }
      } catch (_) { /* ignore */ }
      if (now() < until) setTimeout(tick, 60);
    };
    tick();
  }

  /** 关掉传送的加载遮罩：不关的话游戏会忽略下一跳，要干等 5 秒。 */
  function dropLoading() {
    const mapCfg = mapConfig();
    const world = window[mapCfg.holder];
    if (!world) return;
    try {
      const um = world.UMID;
      if (um) {
        um.MAOP = false;
        if (typeof um.ANIO === 'function') um.ANIO();
      }
    } catch (_) { /* 没有就算了 */ }
    try { if (world[mapCfg.state]) world[mapCfg.state].MAOP = false; } catch (_) {}
    hideLanding();
  }

  /** 当前地图里活着的怪：按到队长的距离排序。 */
  function blinkTargets() {
    const mapCfg = mapConfig();
    const ctx = mapContext();
    const core = ctx.core;
    const world = ctx.holder;
    const mgr = core[mapCfg.manager]();
    const team = core[mapCfg.team](world[mapCfg.player]);
    const ids = mgr[mapCfg.members](team) || [];
    const leader = ids.length ? core[mapCfg.read](ids[0]) : null;
    const lx = leader ? leader.ADUX : 0;
    const ly = leader ? leader.WUZI : 0;
    const out = [];
    ctx.units[ctx.cfg.each](function (_index, unit) {
      if (!unit) return false;
      const u = ctx.cfg.unit;
      if (u.monster && typeof unit[u.monster] === 'function' && unit[u.monster]() !== true) return false;
      if (u.dead && typeof unit[u.dead] === 'function' && unit[u.dead]()) return false;
      const point = unit[u.point] || {};
      out.push({
        x: unit.ADUX,
        y: unit.WUZI,
        name: String(point.name || ''),
        cfg: unit[u.id],
        d: Math.hypot(unit.ADUX - lx, unit.WUZI - ly),
      });
      return false;
    });
    out.sort((a, b) => a.d - b.d);
    return out;
  }

  /** 当前地图里活着的怪（名字/坐标/距离），按距离从近到远。 */
  let statsHelperCache = null;

  /** 找游戏的属性格式化工具：导出对象里带 GKNU（格式化数值）和 DAIC（属性名）的那个。 */
  function statsHelper() {
    if (statsHelperCache) return statsHelperCache;
    try {
      const system = window.System;
      if (!system || typeof system.entries !== 'function') return null;
      for (const pair of system.entries()) {
        const exports = pair && pair[1];
        if (!exports) continue;
        let keys = [];
        try { keys = Object.getOwnPropertyNames(exports); } catch (_) { continue; }
        for (const key of keys) {
          let value;
          try { value = exports[key]; } catch (_) { continue; }
          if (value && typeof value.GKNU === 'function' && typeof value.DAIC === 'function') {
            statsHelperCache = value;
            return value;
          }
        }
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  /** 读一只怪的属性（攻击/攻速/移速/命中/暴击…）：值来自游戏属性组件，文案用游戏自己的格式化函数。 */
  function monsterStatsOf(unit, mapCfg) {
    try {
      const cfg = mapCfg && mapCfg.monsterStats;
      const u = mapCfg && mapCfg.unit;
      if (!cfg || !u || !u.attribute) return [];
      const attr = unit[u.attribute];
      if (!attr) return [];
      const helper = statsHelper();
      const out = [];
      for (const key of Object.keys(cfg)) {
        const item = cfg[key] || {};
        const method = item.method || item;
        if (typeof attr[method] !== 'function') continue;
        let value;
        try { value = attr[method](); } catch (_) { continue; }
        if (typeof value !== 'number' || !Number.isFinite(value)) continue;
        let label = String(item.label || key);
        let text = String(value);
        const attrId = Number(item.attr);
        if (helper && attrId) {
          try {
            const found = helper.DAIC(attrId);
            if (found) label = String(found);
          } catch (_) { /* ignore */ }
          try {
            const shown = helper.GKNU(attrId, value);
            if (shown !== undefined && shown !== null && String(shown) !== '') text = String(shown);
          } catch (_) { /* ignore */ }
        }
        out.push({ key: key, label: label, value: value, text: text });
      }
      return out;
    } catch (_) {
      return [];
    }
  }

  function monsterList() {
    try {
      const mapCfg = mapConfig();
      const ctx = mapContext();
      const core = ctx.core;
      const world = ctx.holder;
      const mgr = core[mapCfg.manager]();
      const team = core[mapCfg.team](world[mapCfg.player]);
      const ids = mgr[mapCfg.members](team) || [];
      const leader = ids.length ? core[mapCfg.read](ids[0]) : null;
      const lx = leader ? leader.ADUX : 0;
      const ly = leader ? leader.WUZI : 0;
      const matchers = bossMatchers(mapCfg);
      const bossOf = (unit) => {
        const u = ctx.cfg.unit;
        const tag = typeof unit[u.tag] === 'string' ? unit[u.tag] : '';
        const point = unit[u.point] || {};
        const text = (tag + ' ' + String(point.name || '') + ' ' + String(point.pointID || '')).toLowerCase();
        if (matchers.keywords.some(w => text.indexOf(String(w).toLowerCase()) >= 0)) return true;
        const template = unit[u.template];
        return matchers.ids.some(id => String(template) === String(id));
      };
      const out = [];
      ctx.units[ctx.cfg.each](function (_index, unit) {
        if (!unit) return false;
        const u = ctx.cfg.unit;
        if (typeof unit[u.monster] === 'function' && unit[u.monster]() !== true) return false;
        if (typeof unit[u.dead] === 'function' && unit[u.dead]()) return false;
        const point = unit[u.point] || {};
        let hp = null, maxHp = null;
        try { if (typeof unit.VTGJ === 'function') hp = unit.VTGJ(); } catch (_) { hp = null; }
        try { if (typeof unit.NXDX === 'function') maxHp = unit.NXDX(); } catch (_) { maxHp = null; }
        const stats = monsterStatsOf(unit, mapCfg);
        out.push({
          x: Math.round(unit.ADUX),
          y: Math.round(unit.WUZI),
          name: String(point.name || ''),
          cfg: unit[u.id],
          boss: bossOf(unit),
          d: Math.round(Math.hypot(unit.ADUX - lx, unit.WUZI - ly)),
          hp: Number.isFinite(hp) ? Math.round(hp) : null,
          maxHp: Number.isFinite(maxHp) ? Math.round(maxHp) : null,
          stats: stats,
        });
        return false;
      });
      // BOSS 排前面，同类按距离
      out.sort((a, b) => (b.boss - a.boss) || (a.d - b.d));
      return out;
    } catch (_) {
      return [];
    }
  }

  /* ---- 日常任务（任务面板 UI） ---- */

  /** 在场景里按名字找节点（浅层守卫，够用）。 */
  function findNodeByName(name) {
    try {
      const scene = window.cc && window.cc.director && window.cc.director.getScene();
      if (!scene) return null;
      let hit = null;
      const stack = [scene];
      let guard = 0;
      while (stack.length && guard < 8000) {
        guard += 1;
        const n = stack.pop();
        if (!n) continue;
        if (String(n.name || '') === name) return n;
        const kids = n.children || [];
        for (let i = 0; i < kids.length; i += 1) stack.push(kids[i]);
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  /** 当前渲染出来的日常任务行（每条一个 LYJP）。 */
  function dailyTaskRows() {
    const rows = [];
    try {
      const scene = window.cc.director.getScene();
      const stack = [scene];
      let guard = 0;
      while (stack.length && guard < 8000) {
        guard += 1;
        const n = stack.pop();
        if (!n) continue;
        if (String(n.name || '') === 'DailyTasksListItemView' && n.activeInHierarchy) {
          (n._components || []).forEach(c => {
            let cls = '';
            try { cls = window.cc.js.getClassName(c) || ''; } catch (_) { cls = ''; }
            if (cls === 'LYJP') rows.push(c);
          });
        }
        const kids = n.children || [];
        for (let i = 0; i < kids.length; i += 1) stack.push(kids[i]);
      }
    } catch (_) { /* ignore */ }
    return rows;
  }

  /** 打开任务面板（右下「任务」按钮）并切到日常页。 */
  function openDailyTasks() {
    try {
      if (dailyTaskRows().length) return { ok: true, opened: true, rows: dailyTaskRows().length };
      const btn = findNodeByName('TasksBoardMenuIconView');
      if (!btn) return { ok: false, error: '没找到任务按钮' };
      btn.emit('click');
      return { ok: true, opened: false, rows: 0 };
    } catch (cause) {
      return { ok: false, error: msgOf(cause) };
    }
  }

  /** 任务面板里任意页签的行组件（DailyTasksListItemView / WeeklyTasksListItemView ...）。 */
  function taskRowsAny() {
    const rows = [];
    try {
      const scene = window.cc.director.getScene();
      const stack = [scene];
      let guard = 0;
      while (stack.length && guard < 8000) {
        guard += 1;
        const n = stack.pop();
        if (!n) continue;
        const nm = String(n.name || '');
        if (n.activeInHierarchy && /ListItemView$/.test(nm)) {
          (n._components || []).forEach(c => {
            try { if (typeof c.VPMG === 'function') rows.push(c); } catch (_) { /* ignore */ }
          });
        }
        const kids = n.children || [];
        for (let i = 0; i < kids.length; i += 1) stack.push(kids[i]);
      }
    } catch (_) { /* ignore */ }
    return rows;
  }

  /** 领取当前页签里所有可领取的奖励。 */
  function claimCurrentTab() {
    const rows = taskRowsAny();
    let claimed = 0;
    for (const comp of rows) {
      let can = false;
      try { can = !!(comp.VRVD && comp.VRVD.node && comp.VRVD.node.active); } catch (_) { can = false; }
      if (!can) continue;
      try { comp.VPMG(); claimed += 1; } catch (_) { /* ignore */ }
    }
    return { rows: rows.length, claimed };
  }

  /**
   * 一键领取：遍历任务面板的每个页签（日常/周常/成就/公会日常/工会），把能领的都领掉。
   * 结果写进 window.__zzDailyResult，Python 侧等一会儿后读。
   */
  function claimAllDailyTabs() {
    try {
      window.__zzDailyResult = null;
      const tg = findNodeByName('TabGroup');
      if (!tg) { window.__zzDailyResult = { ok: false, error: '任务面板没打开' }; return 'no-panel'; }
      const tabs = (tg.children || []).slice();
      let index = 0;
      let claimed = 0;
      let rows = 0;
      const step = () => {
        if (index >= tabs.length) {
          window.__zzDailyResult = { ok: true, claimed, rows, tabs: tabs.length };
          return;
        }
        const tab = tabs[index];
        try { tab.emit('click'); } catch (_) { /* ignore */ }
        setTimeout(() => {
          const got = claimCurrentTab();
          claimed += got.claimed;
          rows += got.rows;
          index += 1;
          step();
        }, 450);
      };
      step();
      return 'started';
    } catch (cause) {
      window.__zzDailyResult = { ok: false, error: msgOf(cause) };
      return 'ERR ' + msgOf(cause);
    }
  }

  /** 游戏的任务服务（模块导出 SNFA 的单例）。 */
  function taskService() {
    try {
      for (const pair of window.System.entries()) {
        const ex = pair[1];
        if (ex && typeof ex === 'object' && ex.SNFA && ex.SNFA.AWXW) return ex.SNFA.AWXW;
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  /** 四条任务线：日常 1 / 周常 2 / 成就 3 / 公会 4。 */
  function taskGroups(svc) {
    const groups = [];
    if (typeof svc.DHPZ === 'function') groups.push({ id: 1, name: '日常', rows: svc.DHPZ() });
    if (typeof svc.HEOT === 'function') groups.push({ id: 2, name: '周常', rows: svc.HEOT() });
    if (typeof svc.CNUM === 'function') groups.push({ id: 3, name: '成就', rows: svc.CNUM() });
    if (typeof svc.NSCD === 'function') groups.push({ id: 4, name: '公会', rows: svc.NSCD() });
    return groups;
  }

  /**
   * 直接调游戏任务服务领取（和参考工具一样，不打开任务面板）。
   *   ROGV(任务线, 该任务在线里的下标)
   * 可领判定：进度 >= 目标 且 KLBW（已领取标记）为 false。
   */
  function claimDailyViaService() {
    try {
      const svc = taskService();
      if (!svc) return { ok: false, error: '没找到任务服务（SNFA）' };
      let claimed = 0;
      let waiting = 0;
      const details = [];
      for (const group of taskGroups(svc)) {
        const rows = Array.isArray(group.rows) ? group.rows : [];
        for (let index = 0; index < rows.length; index += 1) {
          const task = rows[index];
          if (!task) continue;
          // ROPG/GXFI：1=进行中，2=已完成待领取，3=已领取
          const status = task.ROPG !== undefined ? task.ROPG : task.GXFI;
          if (status === 2) {
            try {
              svc.ROGV(group.id, index);
              claimed += 1;
              details.push(group.name + ':' + task.UHNB);
            } catch (_) { waiting += 1; }
          } else {
            waiting += 1;
          }
        }
      }
      return { ok: true, claimed, waiting, details };
    } catch (cause) {
      return { ok: false, error: msgOf(cause) };
    }
  }

  /**
   * 用「任务卡」快速完成日常任务（参考工具同款：SNFA.KYFH(任务编号, 任务线)）。
   * 只处理日常（任务线 1）；已完成待领 / 已领的跳过。
   */
  function completeDailyWithCards() {
    try {
      const svc = taskService();
      if (!svc) return { ok: false, error: '没找到任务服务（SNFA）' };
      if (typeof svc.KYFH !== 'function') return { ok: false, error: '该版本没有任务卡接口' };
      const rows = Array.isArray(svc.DHPZ()) ? svc.DHPZ() : [];
      let used = 0;
      let failed = 0;
      const ids = [];
      for (let i = 0; i < rows.length; i += 1) {
        const task = rows[i];
        if (!task) continue;
        const status = task.ROPG !== undefined ? task.ROPG : task.GXFI;
        if (status === 2 || status === 3) continue;   // 已完成待领 / 已领取都跳过
        try { svc.KYFH(task.UHNB, 1); used += 1; ids.push(task.UHNB); } catch (_) { failed += 1; }
      }
      return { ok: true, used, failed, ids };
    } catch (cause) {
      return { ok: false, error: msgOf(cause) };
    }
  }

  // ---------------- 日常领取：任务(接口) / 福利战令 / 商店免费 / 邮件 ----------------
  const dailyClaim = {
    running: false,
    step: '',
    last: '',
    error: '',
    counts: { task: 0, welfare: 0, shop: 0, mail: 0, cards: 0, market: 0, fund: 0, activity: 0 },
    log: [],
    token: 0,
  };

  function dailyLog(text) {
    const line = String(text || '');
    dailyClaim.last = line;
    dailyClaim.log.push(line);
    if (dailyClaim.log.length > 40) dailyClaim.log.shift();
  }

  function dailySleep(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }
  function dailyActiveNode(name) {
    try {
      const scene = window.cc.director.getScene();
      const stack = [scene];
      let guard = 0;
      while (stack.length && guard < 9000) {
        guard += 1;
        const node = stack.pop();
        if (!node) continue;
        if (String(node.name || '') === name && node.activeInHierarchy) return node;
        const kids = node.children || [];
        for (let i = 0; i < kids.length; i += 1) stack.push(kids[i]);
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  function dailyNodesByName(name) {
    const rows = [];
    try {
      const scene = window.cc.director.getScene();
      (function walk(node) {
        if (String(node.name || '') === name && node.activeInHierarchy) rows.push(node);
        (node.children || []).forEach(walk);
      })(scene);
    } catch (_) { /* ignore */ }
    return rows;
  }

  function dailyViews() {
    const views = [];
    try {
      const scene = window.cc.director.getScene();
      (function walk(node) {
        const nm = String(node.name || '');
        if (node.activeInHierarchy && nm.indexOf('Modal_') === 0) views.push(nm.replace(/^Modal_/, ''));
        (node.children || []).forEach(walk);
      })(scene);
    } catch (_) { /* ignore */ }
    return views;
  }

  /** 关掉当前所有弹窗（视图基类 SCWE = 关闭自己）。 */
  function dailyCloseAll(limit) {
    let closed = 0;
    for (let i = 0; i < (limit || 6); i += 1) {
      const views = dailyViews();
      if (!views.length) break;
      const name = views[views.length - 1];
      let view = dailyActiveNode(name);
      if (!view) {
        // 有些弹窗的视图名和 Modal 名不一致（如 Modal_MailView -> MailListPopupView）
        const base = name.replace(/View$/, '');
        try {
          const scene = window.cc.director.getScene();
          (function walk(node) {
            if (view) return;
            const nm = String(node.name || '');
            if (node.activeInHierarchy && nm.indexOf('Modal_') !== 0 && /View$/.test(nm)
              && (nm.indexOf(name) >= 0 || (base.length >= 3 && nm.indexOf(base) >= 0))) {
              view = node;
              return;
            }
            (node.children || []).forEach(walk);
          })(scene);
        } catch (_) { /* ignore */ }
      }
      if (!view) break;
      let done = false;
      (view.components || view._components || []).forEach(function (comp) {
        if (done) return;
        let cls = '';
        try { cls = window.cc.js.getClassName(comp) || ''; } catch (_) { cls = ''; }
        if (cls.indexOf('cc.') === 0) return;
        if (typeof comp.SCWE === 'function') {
          try { comp.SCWE(); closed += 1; done = true; } catch (_) { /* ignore */ }
        }
      });
      if (!done) {
        // 没有 SCWE：找关闭按钮
        const names = ['btn_close', 'closeBtn', 'CloseBtn', 'BtnClose', 'close', 'Close', 'btn'];
        let hit = null;
        (function find(node, depth) {
          if (hit || depth > 10) return;
          if (node.activeInHierarchy && names.indexOf(String(node.name || '')) >= 0) {
            hit = node;
            return;
          }
          (node.children || []).forEach(function (kid) { find(kid, depth + 1); });
        })(view, 0);
        if (hit) {
          const comps = hit.components || hit._components || [];
          for (let c = 0; c < comps.length; c += 1) {
            let cls = '';
            try { cls = window.cc.js.getClassName(comps[c]) || ''; } catch (_) { cls = ''; }
            if (cls === 'BPNN' || cls === 'cc.Button') {
              try { hit.emit('click', comps[c]); closed += 1; done = true; } catch (_) { /* ignore */ }
              break;
            }
          }
        }
      }
      if (!done) {
        // 再不行就点弹窗遮罩本身
        const modal = dailyActiveNode('Modal_' + name);
        if (modal) {
          const comps = modal.components || modal._components || [];
          for (let m = 0; m < comps.length; m += 1) {
            let cls = '';
            try { cls = window.cc.js.getClassName(comps[m]) || ''; } catch (_) { cls = ''; }
            if (cls === 'cc.Button') {
              try { modal.emit('click', comps[m]); closed += 1; done = true; } catch (_) { /* ignore */ }
              break;
            }
          }
        }
      }
      if (!done) break;
    }
    return closed;
  }

  function dailyNodeText(node, maxDepth) {
    const parts = [];
    (function sub(n, depth) {
      if (depth > (maxDepth || 4)) return;
      (n.components || n._components || []).forEach(function (comp) {
        try {
          if (comp instanceof window.cc.Label && comp.string) parts.push(String(comp.string).trim());
        } catch (_) { /* ignore */ }
      });
      (n.children || []).forEach(function (kid) { sub(kid, depth + 1); });
    })(node, 0);
    return parts.filter(function (x) { return x; }).join('|');
  }

  function dailyClickNode(node) {
    if (!node) return false;
    let btn = null;
    (node.components || node._components || []).forEach(function (comp) {
      if (btn) return;
      let cls = '';
      try { cls = window.cc.js.getClassName(comp) || ''; } catch (_) { cls = ''; }
      if (cls === 'BPNN' || cls === 'cc.Button') btn = comp;
    });
    if (!btn) {
      (function find(kid, depth) {
        if (btn || depth > 2) return;
        (kid.components || kid._components || []).forEach(function (comp) {
          if (btn) return;
          let cls = '';
          try { cls = window.cc.js.getClassName(comp) || ''; } catch (_) { cls = ''; }
          if (cls === 'BPNN' || cls === 'cc.Button') btn = comp;
        });
        (kid.children || []).forEach(function (next) { find(next, depth + 1); });
      })(node, 0);
    }
    if (!btn) return false;
    try {
      node.emit('click', btn);
      return true;
    } catch (_) {
      return false;
    }
  }

  function dailyClickName(name) {
    return dailyClickNode(dailyActiveNode(name));
  }

  /** 点当前面板里带指定文字的按钮；跳过解锁/购买/充值/前往。 */
  function dailyClickTexts(patterns, limit) {
    let clicked = 0;
    try {
      const scene = window.cc.director.getScene();
      (function walk(node) {
        if (clicked >= (limit || 20)) return;
        if (node.activeInHierarchy) {
          const comps = node.components || node._components || [];
          let isBtn = false;
          for (let i = 0; i < comps.length; i += 1) {
            let cls = '';
            try { cls = window.cc.js.getClassName(comps[i]) || ''; } catch (_) { cls = ''; }
            if (cls === 'BPNN' || cls === 'cc.Button') { isBtn = true; break; }
          }
          if (isBtn) {
            const text = dailyNodeText(node, 3);
            const bad = /解锁|购买|充值|前往|升级|挑战/.test(text);
            if (!bad) {
              for (let p = 0; p < patterns.length; p += 1) {
                if (text.indexOf(patterns[p]) >= 0) {
                  if (dailyClickNode(node)) {
                    clicked += 1;
                    dailyLog('领取：' + text.slice(0, 24));
                  }
                  break;
                }
              }
            }
          }
        }
        (node.children || []).forEach(walk);
      })(scene);
    } catch (_) { /* ignore */ }
    return clicked;
  }

  function dailyConfirm() {
    let clicked = 0;
    try {
      const scene = window.cc.director.getScene();
      (function walk(node) {
        if (clicked >= 6) return;
        if (node.activeInHierarchy) {
          const comps = node.components || node._components || [];
          let isBtn = false;
          for (let i = 0; i < comps.length; i += 1) {
            let cls = '';
            try { cls = window.cc.js.getClassName(comps[i]) || ''; } catch (_) { cls = ''; }
            if (cls === 'BPNN' || cls === 'cc.Button') { isBtn = true; break; }
          }
          if (isBtn) {
            const text = dailyNodeText(node, 3).replace(/\s/g, '');
            if (/^(确定|确认|购买|领取|领取奖励|立即领取)$/.test(text)) {
              if (dailyClickNode(node)) clicked += 1;
            }
          }
        }
        (node.children || []).forEach(walk);
      })(scene);
    } catch (_) { /* ignore */ }
    return clicked;
  }

  function dailyShopFree(limit) {
    let clicked = 0;
    try {
      const scene = window.cc.director.getScene();
      (function walk(node) {
        if (clicked >= (limit || 30)) return;
        if (String(node.name || '') === 'clickBtn' && node.activeInHierarchy) {
          const text = dailyNodeText(node, 6);
          if (text.indexOf('免费') >= 0 && text.indexOf('已售罄') < 0) {
            if (dailyClickNode(node)) {
              clicked += 1;
              dailyLog('免费礼包：' + text.slice(0, 24));
            }
          }
        }
        (node.children || []).forEach(walk);
      })(scene);
    } catch (_) { /* ignore */ }
    return clicked;
  }

  function dailyClaimsState() {
    return {
      running: dailyClaim.running,
      step: dailyClaim.step,
      last: dailyClaim.last,
      error: dailyClaim.error,
      counts: dailyClaim.counts,
      log: dailyClaim.log.slice(-12),
    };
  }

  async function dailyClaimsRun(items, token) {
    const alive = function () { return dailyClaim.running && dailyClaim.token === token; };
    const pause = async function (ms) { await dailySleep(ms); return alive(); };
    dailyClaim.running = true;
    dailyClaim.error = '';
    dailyClaim.log = [];
    dailyClaim.counts = { task: 0, welfare: 0, shop: 0, mail: 0, cards: 0, market: 0, fund: 0, activity: 0 };
    dailyLog('开始日常领取');
    try {
      dailyClaim.step = '关闭弹窗';
      dailyCloseAll(6);
      if (!(await pause(500))) return;

      if (items && items.task) {
        dailyClaim.step = '任务奖励';
        if (!taskService()) {
          // 任务模块是打开面板才加载的：先打开任务面板
          dailyClickName('TasksBoardMenuIconView');
          if (!(await pause(1800))) return;
        }
        const res = claimDailyViaService();
        if (res && res.ok) {
          dailyClaim.counts.task = Number(res.claimed || 0);
          dailyLog('任务奖励：领取 ' + dailyClaim.counts.task + ' 个');
        } else {
          dailyLog('任务奖励：' + ((res && res.error) || '读取失败'));
        }
        if (!(await pause(700))) return;
      }

      if (items && items.market) {
        dailyClaim.step = '黑市免费';
        await marketClaimFree();
        dailyClaim.counts.market = Number(marketAuto.bought || 0);
        dailyLog('黑市：' + (marketAuto.last || ('免费 ' + dailyClaim.counts.market + ' 份'))
          + (marketAuto.error ? '（' + marketAuto.error + '）' : ''));
        if (!(await pause(500))) return;
      }

      if (items && items.cards) {
        dailyClaim.step = '任务卡完成';
        const used = completeDailyWithCards();
        dailyClaim.counts.cards = Number(used && used.used || 0);
        if (used && used.ok) {
          dailyLog('任务卡：使用 ' + dailyClaim.counts.cards + ' 张');
          const again = claimDailyViaService();
          if (again && again.ok && again.claimed) {
            dailyClaim.counts.task += Number(again.claimed || 0);
            dailyLog('任务卡后补领：' + again.claimed + ' 个');
          }
        } else {
          dailyLog('任务卡：' + ((used && (used.error || used.message)) || '不可用'));
        }
        if (!(await pause(900))) return;
      }

      if (items && items.welfare) {
        dailyClaim.step = '福利 / 战令';
        dailyCloseAll(4);
        if (dailyClickName('WalfareCenterMenuIconView')) {
          if (!(await pause(2200))) return;
          let got = 0;
          if (dailyClickName('onekeyGetRewardBtn')) got += 1;
          got += dailyClickTexts(['一键领取', '领取'], 25);
          dailyClaim.counts.welfare = got;
          dailyLog('福利/战令：领取 ' + got + ' 次');
          if (!(await pause(900))) return;
          dailyConfirm();
          dailyCloseAll(4);
        } else {
          dailyLog('福利：没找到入口');
        }
        if (!(await pause(500))) return;
      }

      if (items && items.fund) {
        dailyClaim.step = '超值基金';
        dailyCloseAll(4);
        if (dailyClickName('FundCenterMenuIconView')) {
          if (!(await pause(2200))) return;
        }
        await fundClaimAll();
        dailyClaim.counts.fund = Number(fundAuto.claimed || 0);
        dailyLog(fundAuto.last || ('超值基金：' + dailyClaim.counts.fund + ' 个')
          + (fundAuto.error ? '（' + fundAuto.error + '）' : ''));
        dailyConfirm();
        dailyCloseAll(4);
        if (!(await pause(400))) return;
      }

      if (items && items.activity) {
        dailyClaim.step = '活动';
        dailyCloseAll(4);
        if (dailyClickName('ActivityCenterMenuIconView')) {
          if (!(await pause(2200))) return;
          const got = dailyClickTexts(['一键领取', '领取'], 40);
          dailyClaim.counts.activity = got;
          dailyLog('活动：领取 ' + got + ' 次');
          if (!(await pause(700))) return;
          dailyConfirm();
          dailyCloseAll(4);
        } else {
          dailyLog('活动：没找到入口');
        }
        if (!(await pause(400))) return;
      }

      if (items && items.shop) {
        dailyClaim.step = '商店免费礼包';
        dailyCloseAll(4);
        if (dailyClickName('ShopMenuIconView')) {
          if (!(await pause(2200))) return;
          const navs = dailyNodesByName('ShopBottomBtnItem');
          if (navs.length > 1) {
            dailyClickNode(navs[1]);
            if (!(await pause(1600))) return;
          }
          const tabs = ['tabItem1', 'tabItem2', 'tabItem3'];
          for (let i = 0; i < tabs.length; i += 1) {
            if (!dailyClickName(tabs[i])) continue;
            if (!(await pause(1600))) return;
            const got = dailyShopFree(30);
            dailyClaim.counts.shop += got;
            if (got) dailyConfirm();
            if (!(await pause(600))) return;
          }
          dailyLog('商店免费：' + dailyClaim.counts.shop + ' 个');
          dailyCloseAll(4);
        } else {
          dailyLog('商店：没找到入口');
        }
        if (!(await pause(500))) return;
      }

      if (items && items.mail) {
        dailyClaim.step = '邮件';
        dailyCloseAll(4);
        if (dailyClickName('MoreMenuIconView')) {
          if (!(await pause(900))) return;
          if (dailyClickName('EmailMenuIconView')) {
            if (!(await pause(2000))) return;
            const got = dailyClickTexts(['一键领取', '全部领取', '领取'], 40);
            dailyClaim.counts.mail = got;
            dailyLog('邮件：领取 ' + got + ' 次');
            if (!(await pause(700))) return;
            dailyConfirm();
            dailyCloseAll(4);
          } else {
            dailyLog('邮件：没找到入口');
          }
        } else {
          dailyLog('更多：没找到入口');
        }
      }

      dailyClaim.step = '完成';
      dailyLog('日常领取完成');
    } catch (cause) {
      dailyClaim.error = msgOf(cause);
      dailyLog('出错：' + msgOf(cause));
    } finally {
      dailyClaim.running = false;
      dailyClaim.step = '';
    }
  }

  // ---------------- 超值基金（按参考脚本接口） ----------------
  const FUND_TYPES = [4002, 4004, 4005, 4006, 4007, 4008, 3932, 3935, 4015, 4016, 4018, 4135];
  const fundAuto = { last: '', error: '', claimed: 0, log: [] };

  function fundLog(text) {
    const line = String(text || '');
    fundAuto.last = line;
    fundAuto.log.push(line);
    if (fundAuto.log.length > 30) fundAuto.log.shift();
  }

  function fundRegistry() {
    const exports = rogueExports();
    for (let i = 0; i < exports.length; i += 1) {
      const ex = exports[i];
      if (ex && typeof ex === 'object' && ex.GVPY) {
        try {
          const fn = ex.GVPY.AWXW;
          return fn && typeof fn === 'object' ? fn : ex.GVPY;
        } catch (_) {
          return ex.GVPY;
        }
      }
    }
    return null;
  }

  async function fundClaimAll() {
    fundAuto.error = '';
    fundAuto.claimed = 0;
    fundAuto.log = [];
    fundLog('开始领取超值基金');
    try {
      const reg = fundRegistry();
      if (!reg) {
        fundAuto.error = '基金模块还没加载';
        fundLog(fundAuto.error);
        return;
      }
      for (let i = 0; i < FUND_TYPES.length; i += 1) {
        const type = FUND_TYPES[i];
        let svc = null;
        try { svc = reg.TZMS(type); } catch (_) { svc = null; }
        if (!svc) continue;
        const model = svc.RTEZ;
        if (!model || !model.DBUI) continue;
        try { await svc.IMWP(); } catch (_) { /* ignore */ }
        let ready = false;
        try {
          ready = (model.YCPD || []).some(function (item) {
            try { return item.FMAE() === 2; } catch (_) { return false; }
          });
        } catch (_) { ready = false; }
        if (!ready) continue;
        try {
          await svc.LEQV();
          fundAuto.claimed += 1;
          fundLog('基金 ' + type + ' 领取成功');
        } catch (cause) {
          fundLog('基金 ' + type + ' 领取失败：' + msgOf(cause));
        }
        await dailySleep(800);
      }
      fundLog(fundAuto.claimed ? ('超值基金：领取 ' + fundAuto.claimed + ' 个') : '超值基金：暂无奖励');
    } catch (cause) {
      fundAuto.error = msgOf(cause);
      fundLog('出错：' + msgOf(cause));
    }
  }

  // ---------------- 黑市（只买免费） ----------------
  const marketAuto = {
    running: false,
    last: '',
    error: '',
    bought: 0,
    log: [],
  };

  function marketLog(text) {
    const line = String(text || '');
    marketAuto.last = line;
    marketAuto.log.push(line);
    if (marketAuto.log.length > 30) marketAuto.log.shift();
  }

  function marketFindService() {
    const exports = rogueExports();
    for (let i = 0; i < exports.length; i += 1) {
      const ex = exports[i];
      if (ex && typeof ex === 'object' && ex.NKZU && typeof ex.NKZU === 'object') return ex.NKZU;
    }
    return null;
  }

  function marketCategories() {
    const exports = rogueExports();
    for (let i = 0; i < exports.length; i += 1) {
      const ex = exports[i];
      if (ex && typeof ex === 'object' && ex.UBIM && typeof ex.UBIM === 'object') {
        return Object.keys(ex.UBIM).map(function (k) { return ex.UBIM[k]; })
          .filter(function (v) { return Number.isInteger(v); });
      }
    }
    return [];
  }

  function marketFreeItems(svc) {
    const model = svc && svc.MFAX;
    const list = model && model.QKLX;
    if (!Array.isArray(list)) return [];
    return list.filter(function (item) {
      if (!item) return false;
      const price = Number(item.OHRU);
      const currency = Number(item.AYWH);
      const remaining = Number(item.GDYL);
      if (price !== 0 || currency !== 0) return false;
      if (!(remaining > 0)) return false;
      if (item.SSXU) return false;           // 需要看广告，跳过
      try { if (typeof item.WMBY === 'function' && !item.WMBY()) return false; } catch (_) { return false; }
      try { if (typeof item.YHDB === 'function' && !item.YHDB()) return false; } catch (_) { return false; }
      return true;
    });
  }

  async function marketClaimFree() {
    marketAuto.running = true;
    marketAuto.error = '';
    marketAuto.bought = 0;
    marketAuto.log = [];
    marketLog('开始黑市免费领取');
    try {
      let svc = marketFindService();
      if (!svc) {
        // 黑市模块是打开界面才加载的：先点主界面的黑市图标
        if (dailyClickName('BlackMarketMenuIconView')) {
          marketLog('打开黑市界面…');
          await dailySleep(2200);
          svc = marketFindService();
        }
      }
      if (!svc) {
        marketLog('没有黑市接口，改用界面只点免费商品');
        await marketUiClaimFree();
        return;
      }
      const tabs = marketCategories();
      if (!tabs.length) {
        marketAuto.error = '黑市分类还没加载';
        marketLog(marketAuto.error);
        return;
      }
      for (let t = 0; t < tabs.length; t += 1) {
        try { await svc.DFNL(tabs[t]); } catch (_) { /* ignore */ }
      }
      const items = marketFreeItems(svc);
      if (!items.length) {
        marketLog('没有可领取的免费商品');
        return;
      }
      for (let i = 0; i < items.length; i += 1) {
        const item = items[i];
        const id = item.FYXR;
        const zu = item.BXTK;
        let remaining = Number(item.GDYL) || 0;
        const label = '商品 ' + id;
        marketLog('领取：' + label + ' × ' + remaining);
        for (let n = 0; n < remaining; n += 1) {
          if (svc.AWFN) {
            marketLog('游戏正在购买其他商品，稍后再试');
            break;
          }
          let result = null;
          try {
            svc.AWFN = true;
            result = await svc.ROFZ(id, zu, 1, false);
            if (result && typeof result.YVVI === 'function' && !result.YVVI()) {
              result.YLBY();
              result = await svc.ADHB(result);
            }
          } catch (cause) {
            marketAuto.error = '领取失败：' + msgOf(cause);
            marketLog(marketAuto.error);
            break;
          } finally {
            try { svc.AWFN = false; } catch (_) { /* ignore */ }
          }
          marketAuto.bought += 1;
          try { if (typeof item.HHXQ === 'function') item.HHXQ(1); } catch (_) { /* ignore */ }
          try { if (typeof svc.CDMY === 'function') svc.CDMY(); } catch (_) { /* ignore */ }
          try { if (typeof svc.NDZZ === 'function') svc.NDZZ(); } catch (_) { /* ignore */ }
          await dailySleep(600);
        }
        await dailySleep(300);
      }
      marketLog('黑市免费领取完成，共 ' + marketAuto.bought + ' 份');
      dailyCloseAll(3);
    } catch (cause) {
      marketAuto.error = msgOf(cause);
      marketLog('出错：' + msgOf(cause));
    } finally {
      marketAuto.running = false;
    }
  }

  /** 黑市没有服务接口时的兜底：只在界面上点「免费」商品，钻石标价一律跳过。 */
  async function marketUiClaimFree() {
    marketAuto.running = true;
    marketAuto.error = '';
    marketAuto.bought = 0;
    marketAuto.log = [];
    marketLog('开始黑市（界面）免费领取');
    try {
      let root = dailyActiveNode('BlackMarketView');
      if (!root) {
        if (!dailyClickName('BlackMarketMenuIconView')) {
          marketAuto.error = '没找到黑市入口';
          marketLog(marketAuto.error);
          return;
        }
        await dailySleep(2400);
        root = dailyActiveNode('BlackMarketView');
      }
      if (!root) {
        marketAuto.error = '黑市界面没打开';
        marketLog(marketAuto.error);
        return;
      }
      const tried = Object.create(null);
      for (let round = 0; round < 40; round += 1) {
        const cards = [];
        (function walk(node) {
          if (String(node.name || '') === 'bg2' && node.activeInHierarchy) cards.push(node);
          (node.children || []).forEach(walk);
        })(root);
        let picked = null;
        for (let i = 0; i < cards.length; i += 1) {
          const card = cards[i];
          const texts = dailyNodeText(card, 4);
          const price = (arenaChild(card, 'priceLabel') ? dailyNodeText(arenaChild(card, 'priceLabel'), 2) : '').trim();
          const limit = (arenaChild(card, 'limitDesc') ? dailyNodeText(arenaChild(card, 'limitDesc'), 2) : '').trim();
          const name = (arenaChild(card, 'name') ? dailyNodeText(arenaChild(card, 'name'), 2) : '').trim();
          // 只看明确免费的：价格必须为空/0/免费；数字标价（绿钻 30/60…）全部跳过
          const free = !price || price === '0' || price.indexOf('免费') >= 0;
          if (!free) continue;
          if (texts.indexOf('已售罄') >= 0) continue;
          const m = limit.match(/(\d+)\s*\/\s*(\d+)/);
          let remaining = 1;
          if (m) remaining = Number(m[1]);
          if (!(remaining > 0)) continue;
          const key = name + '|' + limit;
          if (tried[key]) continue;
          picked = { card: card, name: name, limit: limit, key: key };
          break;
        }
        if (!picked) break;
        tried[picked.key] = true;
        marketLog('领取：' + (picked.name || '免费商品'));
        if (!dailyClickNode(picked.card)) break;
        await dailySleep(1200);
        dailyConfirm();
        await dailySleep(900);
        marketAuto.bought += 1;
      }
      marketLog('黑市（界面）完成，共 ' + marketAuto.bought + ' 份');
      dailyCloseAll(3);
    } catch (cause) {
      marketAuto.error = msgOf(cause);
      marketLog('出错：' + msgOf(cause));
    } finally {
      marketAuto.running = false;
    }
  }

  function marketState() {
    return {
      running: marketAuto.running,
      last: marketAuto.last,
      error: marketAuto.error,
      bought: marketAuto.bought,
      log: marketAuto.log.slice(-10),
    };
  }

  // ---------------- 杀戮尖塔扫荡 ----------------
  const rogueAuto = {
    running: false,
    step: '',
    last: '',
    error: '',
    done: 0,
    targetFloor: 0,
    info: {},
    token: 0,
    log: [],
  };

  function rogueLog(text) {
    const line = String(text || '');
    rogueAuto.last = line;
    rogueAuto.log.push(line);
    if (rogueAuto.log.length > 30) rogueAuto.log.shift();
  }

  function rogueExports() {
    try {
      return [...window.System.entries()].map(function (pair) { return pair[1]; });
    } catch (_) {
      return [];
    }
  }

  function rogueFindService() {
    const exports = rogueExports();
    for (let i = 0; i < exports.length; i += 1) {
      const ex = exports[i];
      if (ex && typeof ex === 'object' && ex.GSAH && typeof ex.GSAH === 'object') return ex.GSAH;
    }
    return null;
  }

  function rogueFindEnums() {
    const exports = rogueExports();
    for (let i = 0; i < exports.length; i += 1) {
      const ex = exports[i];
      if (ex && typeof ex === 'object' && ex.QNUP && ex.ZOUH) return { challenge: ex.QNUP, activity: ex.ZOUH };
    }
    return null;
  }

  function rogueFindConfig() {
    const exports = rogueExports();
    for (let i = 0; i < exports.length; i += 1) {
      const ex = exports[i];
      if (ex && typeof ex === 'object' && ex.CGWQ) {
        try {
          const fn = ex.CGWQ.AWXW;
          return fn && typeof fn === 'object' ? fn : (typeof ex.CGWQ === 'function' ? ex.CGWQ.AWXW : null);
        } catch (_) {
          return null;
        }
      }
    }
    return null;
  }

  function rogueModel() {
    const svc = rogueFindService();
    if (!svc) return { ok: false, error: '游戏里没有杀戮尖塔服务' };
    const holder = svc.RTEZ;
    const model = holder && holder.ERQM;
    if (!model) return { ok: false, svc: svc, error: '杀戮尖塔数据还没加载' };
    return {
      ok: true,
      svc: svc,
      model: model,
      cleared: Number(model.BMFY),
      limit: Number(model.VJSQ),
      remaining: Number(model.VFDQ),
      exhausted: model.TOBI === true,
      permission: model.XWLN === true,
      challengeState: model.JQAN,
      activityState: model.IJFJ,
    };
  }

  function rogueQuotaKey() {
    let openId = '';
    try { openId = String(window.localStorage.getItem('OpenID') || ''); } catch (_) { /* ignore */ }
    return 'zz_rogue_sweeps:' + (openId || 'default');
  }

  function rogueToday() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  function rogueQuota() {
    try {
      const raw = window.localStorage.getItem(rogueQuotaKey());
      if (!raw) return { day: rogueToday(), count: 0 };
      const data = JSON.parse(raw);
      if (!data || data.day !== rogueToday() || !Number.isInteger(data.count)) return { day: rogueToday(), count: 0 };
      return data;
    } catch (_) {
      return { day: rogueToday(), count: 0 };
    }
  }

  function rogueSetQuota(count) {
    try {
      window.localStorage.setItem(rogueQuotaKey(), JSON.stringify({ day: rogueToday(), count: count }));
    } catch (_) { /* ignore */ }
  }

  function rogueState() {
    const m = rogueModel();
    const enums = rogueFindEnums();
    const idle = enums ? enums.challenge && enums.challenge.ZNHE : null;
    const active = enums ? enums.activity && enums.activity.PXOQ : null;
    const quota = rogueQuota();
    const info = m.ok
      ? {
          cleared: m.cleared,
          limit: m.limit,
          remaining: m.remaining,
          exhausted: m.exhausted,
          permission: m.permission,
          available: m.challengeState === idle && m.activityState === active,
          usedToday: quota.count,
        }
      : { error: m.error };
    return {
      running: rogueAuto.running,
      step: rogueAuto.step,
      last: rogueAuto.last,
      error: rogueAuto.error,
      done: rogueAuto.done,
      targetFloor: rogueAuto.targetFloor,
      info: info,
      log: rogueAuto.log.slice(-10),
    };
  }

  async function rogueRun(floorInput, token) {
    const alive = function () { return rogueAuto.running && rogueAuto.token === token; };
    rogueAuto.running = true;
    rogueAuto.error = '';
    rogueAuto.done = 0;
    rogueAuto.targetFloor = Number(floorInput) || 0;
    rogueAuto.log = [];
    rogueAuto.step = '检查活动';
    rogueLog('开始杀戮尖塔扫荡');
    try {
      const m = rogueModel();
      if (!m.ok) {
        rogueAuto.error = m.error;
        rogueLog(m.error);
        return;
      }
      rogueAuto.step = '刷新状态';
      try {
        if (typeof m.svc.TNXU === 'function') await m.svc.TNXU();
      } catch (_) { /* ignore */ }
      if (!alive()) return;
      const enums = rogueFindEnums();
      const idle = enums && enums.challenge ? enums.challenge.ZNHE : null;
      const active = enums && enums.activity ? enums.activity.PXOQ : null;
      const now = rogueModel();
      if (!now.ok) { rogueAuto.error = now.error; rogueLog(now.error); return; }
      if (!(now.challengeState === idle && now.activityState === active)) {
        rogueAuto.error = '活动未开放或正在挑战中';
        rogueLog('活动未开放或正在挑战中');
        return;
      }
      if (!now.permission) {
        rogueAuto.error = '没有扫荡权限';
        rogueLog('没有扫荡权限');
        return;
      }
      const config = rogueFindConfig();
      let maxFloor = 0;
      try { maxFloor = Number(config && config.EGJU ? config.EGJU() : 0); } catch (_) { maxFloor = 0; }
      let target = rogueAuto.targetFloor;
      if (!(target > 0)) target = now.cleared > 0 ? now.cleared : maxFloor;
      if (!(target > 0)) { rogueAuto.error = '填写的扫荡层数无效'; rogueLog('填写的扫荡层数无效'); return; }
      if (maxFloor > 0 && target > maxFloor) {
        rogueAuto.error = '填写的难度未开放';
        rogueLog('填写的难度未开放');
        return;
      }
      if (target > now.cleared) {
        rogueAuto.error = '尚未通关难度 ' + target;
        rogueLog('尚未通关难度 ' + target);
        return;
      }
      rogueAuto.targetFloor = target;
      const quota = rogueQuota();
      if (quota.count >= 2) {
        rogueAuto.error = '今日已扫荡两次';
        rogueLog('今日已扫荡两次');
        return;
      }
      let count = quota.count;
      for (let i = 0; i < 2; i += 1) {
        if (!alive()) return;
        rogueAuto.step = '扫荡难度 ' + target + '（第 ' + (count + 1) + ' 次）';
        const cur = rogueModel();
        if (!cur.ok) { rogueAuto.error = cur.error; break; }
        if (cur.exhausted || (cur.limit > 0 && cur.remaining <= 0)) {
          rogueLog('游戏扫荡次数已用尽');
          break;
        }
        try {
          const ok = await cur.svc.SLLF(target);
          if (ok !== true) { rogueAuto.error = '扫荡没有返回成功'; rogueLog('扫荡没有返回成功'); break; }
        } catch (cause) {
          rogueAuto.error = '扫荡失败：' + msgOf(cause);
          rogueLog(rogueAuto.error);
          break;
        }
        count += 1;
        rogueAuto.done += 1;
        rogueSetQuota(count);
        rogueLog('扫荡成功（' + count + '/2）');
        try { if (typeof cur.svc.TNXU === 'function') await cur.svc.TNXU(); } catch (_) { /* ignore */ }
        await dailySleep(1200);
        if (count >= 2) break;
      }
      rogueAuto.step = '完成';
      rogueLog('杀戮尖塔扫荡完成');
    } catch (cause) {
      rogueAuto.error = msgOf(cause);
      rogueLog('出错：' + msgOf(cause));
    } finally {
      rogueAuto.running = false;
      rogueAuto.step = '';
    }
  }

  function rogueStart(opts) {
    if (rogueAuto.running) return { ok: true, message: '正在扫荡中' };
    rogueAuto.token += 1;
    const token = rogueAuto.token;
    void rogueRun(opts && opts.floor, token);
    return { ok: true, message: '开始杀戮尖塔扫荡' };
  }

  function rogueStop() {
    rogueAuto.running = false;
    rogueAuto.token += 1;
    rogueAuto.step = '';
    rogueLog('已停止');
    return { ok: true };
  }

  // ---------------- 荣耀竞技场 ----------------
  const arenaAuto = {
    running: false,
    step: '',
    last: '',
    error: '',
    done: 0,
    wins: 0,
    rank: '',
    token: 0,
    log: [],
  };

  function arenaLog(text) {
    const line = String(text || '');
    arenaAuto.last = line;
    arenaAuto.log.push(line);
    if (arenaAuto.log.length > 30) arenaAuto.log.shift();
  }

  function arenaView() {
    return dailyActiveNode('NormalArenaMainView') || dailyActiveNode('ArenaMainView');
  }

  function arenaOpponents() {
    const rows = [];
    const root = arenaView();
    if (!root) return rows;
    (function walk(node) {
      if (String(node.name || '') === 'ArenaMainItem' && node.activeInHierarchy) rows.push(node);
      (node.children || []).forEach(walk);
    })(root);
    return rows;
  }

  function arenaChild(node, name) {
    let hit = null;
    (function walk(n) {
      if (hit) return;
      if (String(n.name || '') === name && n.activeInHierarchy) { hit = n; return; }
      (n.children || []).forEach(walk);
    })(node);
    return hit;
  }

  function arenaText(node, name) {
    const target = arenaChild(node, name);
    if (!target) return '';
    return dailyNodeText(target, 2);
  }

  function arenaState() {
    const root = arenaView();
    const rank = root ? arenaText(root, 'rankText') : '';
    return {
      running: arenaAuto.running,
      step: arenaAuto.step,
      last: arenaAuto.last,
      error: arenaAuto.error,
      done: arenaAuto.done,
      wins: arenaAuto.wins,
      rank: rank || arenaAuto.rank,
      log: arenaAuto.log.slice(-10),
      viewed: !!root,
      resultAt: arenaResultPoint(),
    };
  }

  function arenaCloseResult() {
    let root = dailyActiveNode('ArenaBattleResultView');
    if (!root) {
      const modal = dailyActiveNode('Modal_ArenaBattleResultView');
      if (modal) {
        const comps = modal.components || modal._components || [];
        for (let i = 0; i < comps.length; i += 1) {
          let cls = '';
          try { cls = window.cc.js.getClassName(comps[i]) || ''; } catch (_) { cls = ''; }
          if (cls === 'cc.Button') {
            try { modal.emit('click', comps[i]); return true; } catch (_) { /* ignore */ }
          }
        }
      }
      // 结果面板可能没有独立节点，退化为按名字找
      root = dailyActiveNode('Modal_ArenaBattleResultView');
    }
    if (!root) return false;
    let anyButton = null;
    let textButton = null;
    (function walk(node) {
      const comps = node.components || node._components || [];
      let btn = null;
      for (let i = 0; i < comps.length; i += 1) {
        let cls = '';
        try { cls = window.cc.js.getClassName(comps[i]) || ''; } catch (_) { cls = ''; }
        if (cls === 'BPNN' || cls === 'cc.Button') { btn = comps[i]; break; }
      }
      if (btn && node.activeInHierarchy) {
        if (!anyButton) anyButton = { node: node, comp: btn };
        const text = dailyNodeText(node, 3);
        if (!textButton && /确定|继续|关闭|领取/.test(text)) textButton = { node: node, comp: btn };
      }
      (node.children || []).forEach(walk);
    })(root);
    const target = textButton || anyButton;
    if (target) {
      try {
        target.node.emit('click', target.comp);
        return true;
      } catch (_) { /* 继续找 */ }
    }
    // 结果面板外的按钮：按文字找到 Label，再向上找可点击组件
    let clicked = false;
    try {
      const scene = window.cc.director.getScene();
      (function walk(node) {
        if (clicked) return;
        (node.components || node._components || []).forEach(function (comp) {
          if (clicked) return;
          let label = '';
          try {
            if (comp instanceof window.cc.Label && comp.string) label = String(comp.string);
          } catch (_) { label = ''; }
          if (!/确定|继续挑战|关闭/.test(label)) return;
          let parent = node;
          for (let depth = 0; depth < 4 && parent; depth += 1) {
            const comps = parent.components || parent._components || [];
            for (let i = 0; i < comps.length; i += 1) {
              let cls = '';
              try { cls = window.cc.js.getClassName(comps[i]) || ''; } catch (_) { cls = ''; }
              if (cls === 'BPNN' || cls === 'cc.Button') {
                try { parent.emit('click', comps[i]); clicked = true; } catch (_) { /* ignore */ }
                return;
              }
            }
            parent = parent.parent;
          }
        });
        (node.children || []).forEach(walk);
      })(scene);
    } catch (_) { /* ignore */ }
    return clicked;
  }

  function arenaResultPoint() {
    if (!dailyActiveNode('Modal_ArenaBattleResultView')) return null;
    try {
      let W = 700;
      let H = 1501.6;
      try {
        if (window.cc.view && typeof window.cc.view.getVisibleSize === 'function') {
          const vs = window.cc.view.getVisibleSize();
          if (vs && vs.width > 0 && vs.height > 0) { W = vs.width; H = vs.height; }
        }
      } catch (_) { /* 默认设计分辨率 */ }
      let point = null;
      (function walk(node) {
        if (point) return;
        (node.components || node._components || []).forEach(function (comp) {
          if (point) return;
          let text = '';
          try {
            if (comp instanceof window.cc.Label && comp.string) text = String(comp.string).trim();
          } catch (_) { text = ''; }
          if (!/确定|继续挑战|点击即可返回/.test(text)) return;
          const wp = node.getWorldPosition ? node.getWorldPosition() : null;
          if (!wp) return;
          point = {
            x: Math.round(wp.x / W * window.innerWidth * 10) / 10,
            y: Math.round((H - wp.y) / H * window.innerHeight * 10) / 10,
            text: text,
          };
        });
        (node.children || []).forEach(walk);
      })(window.cc.director.getScene());
      return point;
    } catch (_) {
      return null;
    }
  }

  async function arenaFight(item) {
    const free = arenaChild(item, 'freeBtn');
    const attack = arenaChild(item, 'attackBtn');
    const btn = free || attack;
    if (!btn) return false;
    if (!dailyClickNode(btn)) return false;
    await dailySleep(2600);
    // 战斗/结算界面：点确定、继续挑战，直到回到竞技场主界面
    for (let i = 0; i < 30; i += 1) {
      if (arenaView()) return true;
      if (arenaCloseResult()) {
        await dailySleep(1200);
        if (arenaView()) return true;
        continue;
      }
      dailyClickTexts(['跳过', '自动'], 3);
      await dailySleep(1000);
    }
    return !!arenaView();
  }

  async function arenaRun(count, takeFirst, token) {
    const alive = function () { return arenaAuto.running && arenaAuto.token === token; };
    arenaAuto.running = true;
    arenaAuto.error = '';
    arenaAuto.done = 0;
    arenaAuto.wins = 0;
    arenaAuto.log = [];
    arenaAuto.step = '检查界面';
    arenaLog('开始荣耀竞技场');
    try {
      // 上次战斗停在结算界面：先关掉再继续
      if (arenaCloseResult()) await dailySleep(1400);
      if (!arenaView()) {
        arenaAuto.error = '请先打开荣耀竞技场';
        arenaLog('请先打开荣耀竞技场');
        return;
      }
      const limit = Math.max(0, Number(count) || 0) || 99;
      while (arenaAuto.done < limit) {
        if (!alive()) return;
        const items = arenaOpponents();
        if (!items.length) {
          arenaLog('没有可挑战的对手');
          break;
        }
        // 抢第一：优先打列表第一个（排名最高的对手）
        let pick = items[0];
        if (!takeFirst) {
          pick = items.find(function (it) { return !!arenaChild(it, 'freeBtn'); }) || items[0];
        }
        arenaAuto.step = '第 ' + (arenaAuto.done + 1) + ' 场';
        const name = arenaText(pick, 'userName') || '对手';
        arenaLog('挑战：' + name);
        const ok = await arenaFight(pick);
        arenaAuto.done += 1;
        if (!ok) {
          arenaAuto.error = '战斗没有返回竞技场';
          arenaLog('战斗没有返回竞技场');
          break;
        }
        const st = arenaState();
        arenaAuto.rank = st.rank || arenaAuto.rank;
        arenaLog('完成第 ' + arenaAuto.done + ' 场' + (arenaAuto.rank ? '（' + arenaAuto.rank + '）' : ''));
        await dailySleep(1200);
      }
      arenaAuto.step = '完成';
      arenaLog('竞技场完成');
    } catch (cause) {
      arenaAuto.error = msgOf(cause);
      arenaLog('出错：' + msgOf(cause));
    } finally {
      arenaAuto.running = false;
      arenaAuto.step = '';
    }
  }

  function arenaStart(opts) {
    if (arenaAuto.running) return { ok: true, message: '正在挑战中' };
    arenaAuto.token += 1;
    const token = arenaAuto.token;
    void arenaRun(opts && opts.count, !!(opts && opts.takeFirst), token);
    return { ok: true, message: '开始竞技场挑战' };
  }

  function arenaStop() {
    arenaAuto.running = false;
    arenaAuto.token += 1;
    arenaAuto.step = '';
    arenaLog('已停止');
    return { ok: true };
  }

  function dailyClaimsStart(items) {
    if (dailyClaim.running) return { ok: true, message: '正在领取中' };
    dailyClaim.token += 1;
    const token = dailyClaim.token;
    void dailyClaimsRun(items || { task: true, welfare: true, shop: true, mail: true, cards: false, market: true, fund: true, activity: true }, token);
    return { ok: true, message: '开始日常领取' };
  }

  function dailyClaimsStop() {
    dailyClaim.running = false;
    dailyClaim.token += 1;
    dailyClaim.step = '';
    dailyLog('已停止');
    return { ok: true };
  }

  /** 立刻重进当前地图（刷怪用），成功返回 true。 */
  function reloadMapNow() {
    try {
      const mapCfg = mapConfig();
      const holder = window[mapCfg.holder];
      const st = holder && mapCfg.state ? holder[mapCfg.state] : null;
      const mapId = st && mapCfg.mapId ? st[mapCfg.mapId] : null;
      if (!mapId) return Promise.resolve(false);
      return fastEnterMap(holder, mapCfg, mapId);
    } catch (_) {
      return Promise.resolve(false);
    }
  }

  /**
   * 取坐标：装一个一次性点击监听，把点到的屏幕位置换算成世界坐标。
   * 结果写进 window.__zzPick = {x, y, screen:[sx,sy]}。
   */
  function pickCoord() {
    try {
      if (window.__zzPickOff) { try { window.__zzPickOff(); } catch (_) {} }
      window.__zzPick = null;
      window.__zzPickArmedAt = Date.now();
      const canvas = window.cc && window.cc.game && window.cc.game.canvas;
      if (!canvas) return { ok: false, error: '找不到画布' };
      const handler = function (ev) {
        try {
          // 游戏地图用的是 GameMapCamera/Camera（透视相机），screenToWorld 在这版不可靠，
          // 但 worldToScreen 是准的 → 用「世界→屏幕」反推仿射变换，再求逆。
          const scene = window.cc.director.getScene();
          let cam = null;
          try {
            const camNode = window.cc.find('Root/GameScene/GameMapCamera/Camera', scene);
            if (camNode) cam = camNode.getComponent(window.cc.Camera);
          } catch (_) { cam = null; }
          if (!cam) {
            (function walk(n, d) {
              if (!n || cam || d > 10) return;
              const c = n.getComponent && n.getComponent(window.cc.Camera);
              if (c && c.projection === 1) cam = c;
              (n.children || []).forEach(k => walk(k, d + 1));
            })(scene, 0);
          }
          let baseX = 0, baseY = 0;
          try {
            const mv = window.cc.find('Root/GameScene/GameMapCanvas/MapView', scene);
            if (mv) {
              const bp = mv.getWorldPosition();
              baseX = bp.x; baseY = bp.y;
            }
          } catch (_) { baseX = 0; baseY = 0; }

          const rect = canvas.getBoundingClientRect();
          const vp = window.cc.view.getVisibleSizeInPixel();
          const sx = (ev.clientX - rect.left) * vp.width / rect.width;
          const sy = vp.height - (ev.clientY - rect.top) * vp.height / rect.height;
          let wx = null, wy = null;
          if (cam) {
            try {
              const cp = cam.node.getWorldPosition();
              // 相机中心（游戏世界坐标，已经是 ADUX/WUZI 那套）
              const cx = cp.x - baseX, cy = cp.y - baseY;
              const toScreen = (gx, gy) => {
                const s = cam.worldToScreen(new window.cc.Vec3(gx + baseX, gy + baseY, 0));
                return [s.x, s.y];
              };
              const p0 = toScreen(cx, cy);
              const p1 = toScreen(cx + 1000, cy);
              const p2 = toScreen(cx, cy + 1000);
              // 屏幕 = M * 世界（每 1000 单位的屏幕增量）
              const m00 = (p1[0] - p0[0]) / 1000, m01 = (p2[0] - p0[0]) / 1000;
              const m10 = (p1[1] - p0[1]) / 1000, m11 = (p2[1] - p0[1]) / 1000;
              const det = m00 * m11 - m01 * m10;
              if (Math.abs(det) > 1e-9) {
                const dx = sx - p0[0], dy = sy - p0[1];
                wx = Math.round(cx + (m11 * dx - m01 * dy) / det);
                wy = Math.round(cy + (-m10 * dx + m00 * dy) / det);
              }
            } catch (_) { wx = null; wy = null; }
          }
          if (wx === null || wy === null) {
            // 兜底：老的 screenToWorld
            try {
              if (cam && typeof cam.screenToWorld === 'function') {
                const w = cam.screenToWorld(new window.cc.Vec3(sx, sy, 0));
                if (w) { wx = Math.round(w.x - baseX); wy = Math.round(w.y - baseY); }
              }
            } catch (_) { /* ignore */ }
          }
          window.__zzPick = { x: wx, y: wy, at: Date.now(), screen: [Math.round(sx), Math.round(sy)],
            client: [Math.round(ev.clientX), Math.round(ev.clientY)] };
        } catch (e) {
          window.__zzPick = { error: String((e && e.message) || e) };
        }
        try { window.__zzPickOff(); } catch (_) {}
      };
      canvas.addEventListener('pointerdown', handler, { once: true });
      window.__zzPickOff = function () { canvas.removeEventListener('pointerdown', handler); window.__zzPickOff = null; };
      return { ok: true };
    } catch (cause) {
      return { ok: false, error: msgOf(cause) };
    }
  }

  /**
   * 鼠标瞬移：点地图任意位置，换算成地图坐标后直接调用游戏闪现。
   * 换算方式与 pickCoord 一致：用 GameMapCamera 的 worldToScreen 反推仿射矩阵。
   */
  function clickTeleport(on) {
    try {
      if (window.__zzClickTpOff) { try { window.__zzClickTpOff(); } catch (_) {} }
      if (window.__zzPickOff) { try { window.__zzPickOff(); } catch (_) {} }
      window.__zzClickTp = { on: !!on, last: null, error: '', count: 0 };
      if (!on) return { ok: true, on: false };
      const canvas = window.cc && window.cc.game && window.cc.game.canvas;
      if (!canvas) return { ok: false, error: '找不到画布' };
      const handler = function (ev) {
        try {
          if (!window.__zzClickTp || !window.__zzClickTp.on) return;
          const scene = window.cc.director.getScene();
          let cam = null;
          try {
            const camNode = window.cc.find('Root/GameScene/GameMapCamera/Camera', scene);
            if (camNode) cam = camNode.getComponent(window.cc.Camera);
          } catch (_) { cam = null; }
          if (!cam) {
            (function walk(n, d) {
              if (!n || cam || d > 10) return;
              const c = n.getComponent && n.getComponent(window.cc.Camera);
              if (c && c.projection === 1) cam = c;
              (n.children || []).forEach(k => walk(k, d + 1));
            })(scene, 0);
          }
          let baseX = 0, baseY = 0;
          try {
            const mv = window.cc.find('Root/GameScene/GameMapCanvas/MapView', scene);
            if (mv) {
              const bp = mv.getWorldPosition();
              baseX = bp.x; baseY = bp.y;
            }
          } catch (_) { baseX = 0; baseY = 0; }
          const rect = canvas.getBoundingClientRect();
          if (!rect.width || !rect.height) return;
          const cy = ev.clientY - rect.top;
          const cx = ev.clientX - rect.left;
          // 避开顶部 HUD 和底部摇杆/按钮区
          if (cy < rect.height * 0.12 || cy > rect.height * 0.84) return;
          const vp = window.cc.view.getVisibleSizeInPixel();
          const sx = cx * vp.width / rect.width;
          const sy = vp.height - cy * vp.height / rect.height;
          let wx = null, wy = null;
          if (cam) {
            try {
              const cp = cam.node.getWorldPosition();
              const gx0 = cp.x - baseX, gy0 = cp.y - baseY;
              const toScreen = (gx, gy) => {
                const sc = cam.worldToScreen(new window.cc.Vec3(gx + baseX, gy + baseY, 0));
                return [sc.x, sc.y];
              };
              const p0 = toScreen(gx0, gy0);
              const p1 = toScreen(gx0 + 1000, gy0);
              const p2 = toScreen(gx0, gy0 + 1000);
              const m00 = (p1[0] - p0[0]) / 1000, m01 = (p2[0] - p0[0]) / 1000;
              const m10 = (p1[1] - p0[1]) / 1000, m11 = (p2[1] - p0[1]) / 1000;
              const det = m00 * m11 - m01 * m10;
              if (Math.abs(det) > 1e-9) {
                const dx = sx - p0[0], dy = sy - p0[1];
                wx = Math.round(gx0 + (m11 * dx - m01 * dy) / det);
                wy = Math.round(gy0 + (-m10 * dx + m00 * dy) / det);
              }
            } catch (_) { wx = null; wy = null; }
          }
          if (wx === null || wy === null) {
            try {
              if (cam && typeof cam.screenToWorld === 'function') {
                const w = cam.screenToWorld(new window.cc.Vec3(sx, sy, 0));
                if (w) { wx = Math.round(w.x - baseX); wy = Math.round(w.y - baseY); }
              }
            } catch (_) { /* ignore */ }
          }
          if (wx === null || wy === null) {
            window.__zzClickTp.error = '坐标换算失败';
            return;
          }
          window.__zzClickTp.last = { x: wx, y: wy, client: [Math.round(ev.clientX), Math.round(ev.clientY)] };
          window.__zzClickTp.count += 1;
          try {
            const p = blinkToCoords(wx, wy, 'instant');
            if (p && typeof p.catch === 'function') {
              p.catch(function (e) { window.__zzClickTp.error = msgOf(e); });
            }
          } catch (e) {
            window.__zzClickTp.error = msgOf(e);
          }
        } catch (e) {
          try { window.__zzClickTp.error = String((e && e.message) || e); } catch (_) {}
        }
      };
      canvas.addEventListener('pointerdown', handler, true);
      window.__zzClickTpOff = function () {
        try { canvas.removeEventListener('pointerdown', handler, true); } catch (_) {}
        window.__zzClickTpOff = null;
        if (window.__zzClickTp) window.__zzClickTp.on = false;
      };
      return { ok: true, on: true };
    } catch (cause) {
      return { ok: false, error: msgOf(cause) };
    }
  }

  /** 公会迷宫：配置（按当前游戏版本的字段名）。 */
  function mazeConfig() {
    state.build = state.build || detectBuild();
    const map = mapping();
    return (map && map.maze) || null;
  }

  function mazeText(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'bigint') return String(value);
    return String(value);
  }

  function mazePercent(fn) {
    try {
      const value = fn();
      return typeof value === 'number' && Number.isFinite(value) ? value / 100 : null;
    } catch (_) { return null; }
  }

  /** 从战斗内核里遍历当前单位，找配置编号等于首领编号的怪物。 */
  function mazeBossUnits(cfg, engine, bossId) {
    const units = [];
    const manager = engine && engine[cfg.unitManager];
    if (typeof manager !== 'function') throw Error('战斗单位管理器还没加载');
    const root = manager.call(engine);
    const container = root && root[cfg.unitContainer] && root[cfg.unitContainer]();
    if (!container || typeof container[cfg.unitIterate] !== 'function') throw Error('战斗单位容器还没加载');
    container[cfg.unitIterate](function (_id, raw) {
      try {
        if (String(raw[cfg.unitConfig]) !== String(bossId)) return;
        const isMonster = typeof raw[cfg.isMonster] === 'function' ? raw[cfg.isMonster]() : true;
        if (isMonster) units.push(raw);
      } catch (_) { /* 单个单位读取失败不影响其他单位 */ }
    });
    return units;
  }

  /** 读取首领血量/抗性/命中，并尽量补上全部属性名称和单位。 */
  function mazeReadBoss(cfg, unit) {
    const attrs = unit[cfg.unitAttrs] || {};
    const result = {
      maxHp: '',
      antiCrit: null,
      antiCritDamage: null,
      hitRate: null,
      attributes: [],
    };
    try { result.maxHp = mazeText(unit[cfg.maxHp]()); } catch (_) { result.maxHp = ''; }
    result.antiCrit = mazePercent(attrs[cfg.antiCrit]);
    result.antiCritDamage = mazePercent(attrs[cfg.antiCritDamage]);
    result.hitRate = mazePercent(attrs[cfg.hitRate]);

    let tools = null;
    try {
      const mod = System.get('chunks:///_virtual/' + cfg.attrTool + '.ts');
      tools = mod && mod[cfg.attrTool];
    } catch (_) { tools = null; }

    try {
      const container = attrs[cfg.attrContainer];
      const readValue = attrs[cfg.attrValue];
      if (!container || typeof container.keys !== 'function' || typeof readValue !== 'function') return result;
      const ids = new Set();
      for (const key of container.keys()) {
        const id = Number(key);
        if (Number.isFinite(id)) ids.add(id);
      }
      // 抗暴击 / 抗暴伤 / 命中即使容器里没有也补上；5 是生命上限，单独显示。
      [8, 10, 12].forEach(id => ids.add(id));
      const sorted = [...ids].filter(id => Number.isFinite(id) && id > 0 && id !== 5).sort((a, b) => a - b);
      for (const id of sorted) {
        let raw = null;
        try { raw = readValue.call(attrs, id); } catch (_) { continue; }
        if (raw === null || raw === undefined) continue;
        let name = '属性 #' + id;
        let text = String(raw);
        if (tools) {
          try {
            const label = tools[cfg.attrName](id);
            if (label) name = String(label).replace(/<[^>]*>/g, '');
          } catch (_) { /* 名称没加载时保留编号 */ }
          try {
            const formatted = tools[cfg.attrText](id, raw);
            if (formatted) text = String(formatted).replace(/<[^>]*>/g, '');
          } catch (_) { /* 单位没加载时显示原始值 */ }
        }
        result.attributes.push({ id: id, name: name, value: text, raw: mazeText(raw) });
      }
    } catch (_) { /* 全部属性读取失败时至少保留血量和抗性 */ }
    return result;
  }

  /**
   * 查询公会迷宫当前首领（只读）。必须在首领战斗里执行；
   * 不在战斗时返回上次查到的快照，方便对照。
   */
  function mazeQuery() {
    const cfg = mazeConfig();
    if (!cfg) return { ok: false, error: '当前游戏版本没有公会迷宫映射', cached: null };
    const cached = state.maze.last || null;
    try {
      const holder = window[cfg.holder];
      const scene = holder && holder[cfg.scene];
      if (!scene) throw Error('不在游戏地图里');
      const engine = scene[cfg.engine];
      if (!engine) throw Error('当前不在公会迷宫首领战斗');
      const bossData = engine[cfg.bossData];
      const bossId = bossData && bossData[cfg.bossId];
      if (!bossId) throw Error('首领战斗数据还没加载，请稍后再查');

      let battleType = null;
      try {
        const challenge = holder[cfg.challengeService];
        const model = challenge && challenge[cfg.model];
        if (model && typeof model[cfg.battleType] === 'function') battleType = model[cfg.battleType]();
      } catch (_) { battleType = null; }
      if (battleType !== null && battleType !== undefined && Number(battleType) !== 2) {
        throw Error('当前不是公会迷宫首领战斗');
      }

      const units = mazeBossUnits(cfg, engine, bossId);
      if (!units.length) throw Error('首领实体还没加载，请稍后再查');
      if (units.length > 1) throw Error('当前首领实体不唯一，先不要查询');

      const stats = mazeReadBoss(cfg, units[0]);
      let difficulty = null;
      let floor = null;
      try {
        const maze = holder[cfg.mazeService];
        const model = maze && maze[cfg.model];
        if (model) difficulty = Number(model[cfg.difficulty]) || null;
      } catch (_) { difficulty = null; }
      try {
        const challenge = holder[cfg.challengeService];
        const model = challenge && challenge[cfg.model];
        if (model && typeof model[cfg.floor] === 'function') floor = Number(model[cfg.floor]()) || null;
      } catch (_) { floor = null; }

      const snapshot = {
        difficulty: difficulty,
        floor: floor,
        bossId: String(bossId),
        maxHp: stats.maxHp,
        antiCrit: stats.antiCrit,
        antiCritDamage: stats.antiCritDamage,
        hitRate: stats.hitRate,
        attributes: stats.attributes,
        capturedAt: new Date().toISOString(),
      };
      state.maze.last = snapshot;
      return { ok: true, snapshot: snapshot, cached: false };
    } catch (cause) {
      return { ok: false, error: msgOf(cause), cached: cached };
    }
  }

  /** 公会情报：配置（按当前游戏版本的字段名）。 */
  function guildConfig() {
    // 运行时刚注入时 state.build 可能还没填，先现场识别一次。
    state.build = state.build || detectBuild();
    const map = mapping();
    return (map && map.guild) || null;
  }

  /** 依次读取候选字段，函数型 getter 会调用；空值继续下一个。 */
  function guildValue(obj, names, fallback) {
    const list = Array.isArray(names) ? names : [names];
    for (const name of list) {
      if (!name) continue;
      let value = null;
      try { value = obj ? obj[name] : null; } catch (_) { value = null; }
      if (typeof value === 'function') {
        try { value = value.call(obj); } catch (_) { value = null; }
      }
      if (value !== null && value !== undefined && String(value).trim() !== '') return value;
    }
    return fallback;
  }

  /** 从据点回包里取驻守成员数组。 */
  function guildMemberList(cfg, raw) {
    if (!raw || typeof raw !== 'object') return [];
    const keys = Array.isArray(cfg.member) ? cfg.member : [cfg.member];
    for (const key of keys) {
      if (!key) continue;
      let value = null;
      try { value = raw[key]; } catch (_) { value = null; }
      if (typeof value === 'function') {
        try { value = value.call(raw); } catch (_) { value = null; }
      }
      if (Array.isArray(value)) return value;
    }
    try {
      for (const key of Object.keys(raw)) {
        const value = raw[key];
        if (Array.isArray(value) && value.length && value[0] && typeof value[0] === 'object') return value;
      }
    } catch (_) { /* ignore */ }
    return [];
  }

  /** 单条驻守成员：名字、战力、等级、区服。 */
  function guildParseMember(cfg, member) {
    const m = member && typeof member === 'object' ? member : {};
    const player = guildValue(m, [cfg.playerA, cfg.playerB], null) || m;
    const power = Number(guildValue(m, [cfg.powerB, cfg.powerA], 0));
    return {
      id: String(guildValue(player, [cfg.idB, cfg.idA], '')),
      name: String(guildValue(player, [cfg.nameB, cfg.nameA, cfg.nameC, cfg.nameD], guildValue(m, [cfg.nameE], ''))),
      power: Number.isFinite(power) && power > 0 ? Math.trunc(power) : 0,
      level: Number(guildValue(player, [cfg.level], 0)) || 0,
      server: String(guildValue(player, [cfg.server], '')),
      teams: Number(guildValue(m, [cfg.teams], 0)) || 0,
    };
  }

  function guildParseStronghold(cfg, camp, id, raw) {
    const members = guildMemberList(cfg, raw).map(item => guildParseMember(cfg, item))
      .filter(item => item.id || item.name);
    const total = members.reduce((sum, item) => sum + (item.power || 0), 0);
    return { camp: camp, id: id, count: members.length, totalPower: total, members: members, error: '' };
  }

  /** 在所有模块里找共享服务容器，再取公会战服务。 */
  function guildFindService(cfg) {
    const bagName = cfg.bag;
    const serviceName = cfg.service;
    let pairs = [];
    try { pairs = [...System.entries()]; } catch (_) { pairs = []; }
    for (const pair of pairs) {
      const exports = pair[1];
      if (!exports) continue;
      const bags = [
        exports,
        exports[bagName],
        exports.IZBA,
        exports.IZBA && exports.IZBA[bagName],
      ];
      for (const bag of bags) {
        if (!bag || typeof bag !== 'object') continue;
        let service = null;
        try { service = bag[serviceName]; } catch (_) { service = null; }
        if (service && typeof service === 'object') return service;
      }
    }
    return null;
  }

  /**
   * 开始查询公会据点。camp：0=我方+敌方，1=我方，2=敌方；每方 1~13 号据点。
   * 查询是异步的，用 guildIntelState() 轮询进度和结果。
   */
  function guildIntelStart(camp) {
    const cfg = guildConfig();
    if (!cfg || !cfg.query) return { ok: false, error: '当前游戏版本没有公会情报映射' };
    const current = state.guildIntel;
    if (current && current.running) return { ok: true, running: true, total: current.total, done: current.done };
    const service = guildFindService(cfg);
    if (!service || typeof service[cfg.query] !== 'function') {
      return { ok: false, error: '公会战服务还没加载，请先在游戏里打开一次公会/公会战界面' };
    }
    const camps = camp === 1 || camp === 2 ? [camp] : [1, 2];
    const job = {
      running: true, rows: [], error: '', done: 0, total: camps.length * 13,
      camp: camp || 0, startedAt: now(), finishedAt: 0,
    };
    state.guildIntel = job;
    let campIndex = 0;
    let stronghold = 1;
    const step = function () {
      if (campIndex >= camps.length) {
        job.running = false;
        job.finishedAt = now();
        return;
      }
      const campId = camps[campIndex];
      const strongholdId = stronghold;
      let pending;
      try {
        pending = service[cfg.query](campId, strongholdId);
      } catch (cause) {
        job.rows.push({
          camp: campId, id: strongholdId, count: 0, totalPower: 0, members: [],
          error: msgOf(cause),
        });
        advance();
        return;
      }
      Promise.resolve(pending).then(function (raw) {
        try {
          job.rows.push(guildParseStronghold(cfg, campId, strongholdId, raw));
        } catch (cause) {
          job.rows.push({
            camp: campId, id: strongholdId, count: 0, totalPower: 0, members: [],
            error: msgOf(cause),
          });
        }
        advance();
      }, function (cause) {
        job.rows.push({
          camp: campId, id: strongholdId, count: 0, totalPower: 0, members: [],
          error: msgOf(cause),
        });
        advance();
      });
      function advance() {
        job.done += 1;
        stronghold += 1;
        if (stronghold > 13) {
          stronghold = 1;
          campIndex += 1;
        }
        setTimeout(step, 120);
      }
    };
    setTimeout(step, 30);
    return { ok: true, total: job.total };
  }

  function guildIntelState() {
    const job = state.guildIntel || { running: false, rows: [], error: '', done: 0, total: 0 };
    return {
      running: !!job.running,
      rows: job.rows || [],
      error: job.error || '',
      done: job.done || 0,
      total: job.total || 0,
      camp: job.camp || 0,
      startedAt: job.startedAt || 0,
      finishedAt: job.finishedAt || 0,
    };
  }

  /** 读取一个据点里每个成员的三个队战力（KBJA 成员详情，逐个查）。 */
  function guildTeamsStart(camp, stronghold) {
    const cfg = guildConfig();
    if (!cfg || !cfg.memberDetail) {
      return { ok: false, error: '当前游戏版本没有成员详情映射' };
    }
    const intel = state.guildIntel || {};
    const rows = intel.rows || [];
    const campId = Number(camp) || 0;
    const spotId = Number(stronghold) || 0;
    const row = rows.find(function (item) {
      return Number(item.camp) === campId && Number(item.id) === spotId;
    });
    if (!row) return { ok: false, error: '请先查询这个据点' };
    const members = (row.members || []).filter(function (item) { return item && item.id; });
    if (!members.length) return { ok: false, error: '这个据点没有成员' };
    const service = guildFindService(cfg);
    if (!service || typeof service[cfg.memberDetail] !== 'function') {
      return { ok: false, error: '公会战服务还没加载，请先打开一次公会战界面' };
    }
    state.guildTeams = state.guildTeams || { job: null };
    const running = state.guildTeams.job;
    if (running && running.running) {
      if (running.camp === campId && running.id === spotId) {
        return { ok: true, running: true, total: running.total, done: running.done };
      }
      return { ok: false, error: '正在读取其他据点的队伍，请稍等' };
    }

    const job = {
      running: true, camp: campId, id: spotId, done: 0, total: members.length,
      teams: {}, error: '', startedAt: now(),
    };
    state.guildTeams.job = job;
    let index = 0;
    const step = function () {
      if (index >= members.length) {
        job.running = false;
        return;
      }
      const member = members[index];
      index += 1;
      let pending;
      try {
        pending = service[cfg.memberDetail](campId, spotId, member.id, true);
      } catch (cause) {
        job.error = msgOf(cause);
        advance();
        return;
      }
      Promise.resolve(pending).then(function (detail) {
        try {
          const list = detail && detail[cfg.teamList];
          if (Array.isArray(list)) {
            const teams = list.map(function (team, i) {
              const slot = Number(team[cfg.teamSlot]) || (i + 1);
              const power = Number(team[cfg.teamPower]);
              return {
                slot: slot,
                power: Number.isFinite(power) && power > 0 ? Math.trunc(power) : 0,
              };
            }).sort(function (a, b) { return a.slot - b.slot; });
            job.teams[member.id] = teams;
          }
        } catch (_) { /* 单个成员失败不影响其他成员 */ }
        advance();
      }, function (cause) {
        job.error = msgOf(cause);
        advance();
      });
      function advance() {
        job.done += 1;
        setTimeout(step, 80);
      }
    };
    setTimeout(step, 30);
    return { ok: true, total: job.total };
  }

  function guildTeamsState() {
    const box = state.guildTeams || {};
    const job = box.job || null;
    if (!job) return { running: false, camp: 0, id: 0, done: 0, total: 0, teams: {}, error: '' };
    return {
      running: !!job.running,
      camp: job.camp || 0,
      id: job.id || 0,
      done: job.done || 0,
      total: job.total || 0,
      teams: job.teams || {},
      error: job.error || '',
    };
  }

  /** 在任务面板里找到指定名字的任务行，点它的「前往」。 */
  function clickTaskGo(taskName) {
    try {
      const scene = window.cc.director.getScene();
      const rows = [];
      (function walk(n, d) {
        if (!n || d > 25) return;
        if (String(n.name || '') === 'DailyTasksListItemView' && n.activeInHierarchy) rows.push(n);
        (n.children || []).forEach(k => walk(k, d + 1));
      })(scene, 0);
      for (const row of rows) {
        let target = '';
        let go = null;
        (function walk(n, d) {
          if (!n || d > 6) return;
          (n._components || []).forEach(c => {
            let cls = '';
            try { cls = window.cc.js.getClassName(c) || ''; } catch (_) { cls = ''; }
            if (cls === 'cc.Label' && String(n.name || '') === 'TargetLabel') target = String(c.string || '');
          });
          if (String(n.name || '') === 'GoBtn') go = n;
          (n.children || []).forEach(k => walk(k, d + 1));
        })(row, 0);
        if (target.indexOf(taskName) < 0 || !go) continue;
        go.emit('click');
        return { ok: true, target };
      }
      return { ok: false, error: '任务面板里没找到「' + taskName + '」' };
    } catch (cause) {
      return { ok: false, error: msgOf(cause) };
    }
  }

  /** 点弹窗里的「传送并追踪」。 */
  function clickTeleportTrack() {
    try {
      const scene = window.cc.director.getScene();
      let hit = null;
      (function walk(n, d) {
        if (!n || hit || d > 25) return;
        (n._components || []).forEach(c => {
          let cls = '';
          try { cls = window.cc.js.getClassName(c) || ''; } catch (_) { cls = ''; }
          if (cls === 'cc.Label' && String(c.string || '').indexOf('传送并追踪') >= 0) hit = n;
        });
        (n.children || []).forEach(k => walk(k, d + 1));
      })(scene, 0);
      if (!hit) return { ok: false, error: '没有「传送并追踪」按钮' };
      let p = hit;
      for (let i = 0; i < 6 && p; i += 1) {
        let clicked = false;
        (p._components || []).forEach(c => {
          let cls = '';
          try { cls = window.cc.js.getClassName(c) || ''; } catch (_) { cls = ''; }
          if (cls === 'UXXM') {
            try { c._onTouchBegan({}); c._onTouchEnded({}); } catch (_) { /* ignore */ }
            p.emit('click');
            clicked = true;
          }
        });
        if (clicked) return { ok: true };
        p = p.parent;
      }
      return { ok: false, error: '没找到可点的按钮' };
    } catch (cause) {
      return { ok: false, error: msgOf(cause) };
    }
  }

  /** 调游戏自己的分享入口（任务「分享游戏1次」用）。 */
  function shareGame() {
    try {
      for (const pair of window.System.entries()) {
        const ex = pair[1];
        if (!ex || typeof ex !== 'object') continue;
        if (typeof ex.XYIY === 'function') {
          try { ex.XYIY(); return { ok: true, via: 'export' }; } catch (cause) { return { ok: false, error: msgOf(cause) }; }
        }
        for (const key of Object.keys(ex)) {
          let v; try { v = ex[key]; } catch (_) { continue; }
          if (typeof v === 'function' && typeof v.XYIY === 'function') {
            try { v.XYIY(); return { ok: true, via: key }; } catch (cause) { return { ok: false, error: msgOf(cause) }; }
          }
          if (v && typeof v === 'object') {
            if (typeof v.XYIY === 'function') {
              try { v.XYIY(); return { ok: true, via: key + '.obj' }; } catch (_) { /* 试下一个 */ }
            }
            if (v.AWXW && typeof v.AWXW.XYIY === 'function') {
              try { v.AWXW.XYIY(); return { ok: true, via: key + '.AWXW' }; } catch (_) { /* 试下一个 */ }
            }
          }
        }
      }
      return { ok: false, error: '没找到分享入口（XYIY）' };
    } catch (cause) {
      return { ok: false, error: msgOf(cause) };
    }
  }

  /** 兼容旧调用：只领当前页签。 */
  function claimDailyTasks() {
    try { return claimCurrentTab(); } catch (cause) { return { ok: false, error: msgOf(cause) }; }
  }

  /** 直接飞到/闪到指定坐标。mode = instant（瞬移）| fly（飞过去，播过场）。 */
  function blinkToCoords(x, y, mode) {
    const box = state.blink;
    if (box.busy) throw Error('上一次闪现还没结束');
    const fly = mode === 'fly';
    box.busy = true;
    const began = now();
    box.reason = (fly ? '正在飞过去…' : '正在闪现…');
    Promise.resolve()
      .then(async () => {
        if (fly) {
          await teleportTo(x, y);
          box.last = '已到达（' + ((now() - began) / 1000).toFixed(1) + ' 秒）';
        } else {
          teleportNoFx(x, y);
          let arrived = false;
          const deadline = now() + 4000;
          while (now() < deadline) {
            await new Promise(r => setTimeout(r, 60));
            let p = null;
            try { p = teamLeaderSpot(); } catch (_) { p = null; }
            if (p && Math.abs(p.x - x) < 260 && Math.abs(p.y - y) < 260) { arrived = true; break; }
          }
          box.last = arrived
            ? '已闪现到 (' + Math.round(x) + ',' + Math.round(y) + ')'
            : '闪现没到位（可以再点一次）';
        }
        box.reason = box.last;
      })
      .catch(cause => {
        box.reason = '移动失败：' + msgOf(cause);
      })
      .then(() => { box.busy = false; });
    return { ok: true, message: fly ? '正在飞过去' : '正在闪现' };
  }

  /** 闪现到最近的怪；filter 为空=最近，否则按名字包含 / 编号相等。 */
  function doBlink(filter) {
    const box = state.blink;
    if (box.busy) throw Error('上一次闪现还没结束');
    const targets = blinkTargets();
    if (!targets.length) throw Error('当前地图没找到活着的怪');
    const text = String(filter === undefined || filter === null ? '' : filter).trim();
    let pick = null;
    if (text) {
      pick = targets.find(t => t.name.indexOf(text) >= 0 || String(t.cfg) === text);
      if (!pick) throw Error('没找到名字/编号是「' + text + '」的活怪');
    } else {
      pick = targets[0];
    }
    box.busy = true;
    box.reason = '正在闪现到 ' + (pick.name || pick.cfg) + '…';
    const began = now();
    Promise.resolve()
      .then(async () => {
        // 不等游戏的传送加载动画：发请求 → 位置 0.1~0.3 秒就变，落地特效一直按住
        teleportNoFx(pick.x, pick.y);
        suppressLanding(1500);
        let arrived = false;
        const deadline = now() + 8000;
        while (now() < deadline) {
          await new Promise(r => setTimeout(r, 80));
          hideLanding();
          dropLoading();
          let p = null;
          try { p = teamLeaderSpot(); } catch (_) { p = null; }
          if (p && Math.abs(p.x - pick.x) < 260 && Math.abs(p.y - pick.y) < 260) { arrived = true; break; }
          if (now() - began > 700 && !arrived && !box.__retried) {
            box.__retried = true;
            try { teleportFire(pick.x, pick.y); } catch (_) { /* ignore */ }
          }
        }
        box.__retried = false;
        hideLanding();
        const cost = ((now() - began) / 1000).toFixed(1);
        box.last = arrived
          ? '已闪现到 ' + (pick.name || pick.cfg) + '（' + cost + ' 秒）'
          : '闪现没到位（' + cost + ' 秒，可以再点一次）';
        box.reason = box.last;
      })
      .catch(cause => {
        box.reason = '闪现失败：' + msgOf(cause);
      })
      .then(() => { box.busy = false; });
    return { ok: true, message: '正在闪现到 ' + (pick.name || pick.cfg) };
  }

  function teamLeaderSpot() {
    const mapCfg = mapConfig();
    const world = window[mapCfg.holder];
    const core = world[mapCfg.core];
    const mgr = core[mapCfg.manager]();
    const team = core[mapCfg.team](world[mapCfg.player]);
    const ids = mgr[mapCfg.members](team) || [];
    const unit = ids.length ? core[mapCfg.read](ids[0]) : null;
    if (!unit) return null;
    return { x: unit.ADUX, y: unit.WUZI };
  }


  /** 原地复活：调用游戏重生系统 LBUD，把所有倒地英雄拉起来（不移动、不花资源）。 */
  function localReviveAll(cfg) {
    const mapCfg = mapConfig();
    const world = window[mapCfg.holder];
    const core = world[mapCfg.core];
    if (!core || typeof core.UDWZ !== 'function') throw Error('实体世界还没就绪');
    const noyr = core.UDWZ() && core.UDWZ().NOYR;
    if (!noyr || typeof noyr.LBUD !== 'function') throw Error('重生系统还没就绪');
    const opts = (cfg && cfg.opts) || { CTXT: true };
    return noyr.LBUD(world[mapCfg.player], null, opts) || 0;
  }

  /** 数一下当前队伍里倒地的英雄。 */
  function deadHeroCount() {
    const mapCfg = mapConfig();
    const world = window[mapCfg.holder];
    const core = world[mapCfg.core];
    const mgr = core[mapCfg.manager]();
    const team = core[mapCfg.team](world[mapCfg.player]);
    const ids = mgr[mapCfg.members](team) || [];
    let dead = 0;
    for (const id of ids) {
      const unit = core[mapCfg.read](id);
      if (unit && mapCfg.unit.dead && typeof unit[mapCfg.unit.dead] === 'function' && unit[mapCfg.unit.dead]()) {
        dead += 1;
      }
    }
    return dead;
  }

  let reviveLocalAt = 0;

  function tickRevive() {
    const box = state.revive;
    if (!box.enabled) return;
    if (!mapping()) return;

    // 客户端原地复活（LBUD）已被服务端哈希校验抓到，永久停用
    if (box.mode === 'local') {
      box.reason = '「原地复活」会被服务端判为数据不一致，已停用；请用「传送回营地复活」';
      return;
    }
    // 上一轮复活成功但没传回原位：继续补传，别把人留在营地
    if (box.returnTo && now() < box.returnTo.until) {
      let p = null;
      try { p = teamLeaderSpot(); } catch (_) { p = null; }
      if (p && Math.abs(p.x - box.returnTo.x) < 320 && Math.abs(p.y - box.returnTo.y) < 320) {
        box.returnTo = null;
        box.reason = '已回到刚才的位置';
      } else if (!box.busy) {
        if (now() >= (box.returnNextAt || 0)) {
          box.returnNextAt = now() + 1500;
          try {
            teleportFire(box.returnTo.x, box.returnTo.y);
            box.reason = '正在传回刚才的位置…';
          } catch (_) { /* ignore */ }
        }
        return;
      }
    } else if (box.returnTo) {
      box.returnTo = null;
    }

    const nowMs = now();
    if (box.busy) {
      // 复活流程进行中：全灭面板的「放弃」会弹确认小窗，这里持续把确认按钮坐标交出去
      if (nowMs >= (box.confirmScanAt || 0)) {
        box.confirmScanAt = nowMs + 500;
        try { box.confirmAt = findConfirmPoint(); } catch (_) { box.confirmAt = null; }
      }
      return;
    }
    box.confirmAt = null;
    box.confirmScanAt = 0;
    if (nowMs < box.nextTryAt) return;
    // 场景遍历比较重，最多每 400ms 找一次面板
    if (nowMs < reviveScanAt) return;
    reviveScanAt = nowMs + 400;

    let cfg = null;
    let panel = null;
    try {
      cfg = reviveConfig();
      panel = findDefeatPanel(cfg);
    } catch (cause) {
      box.reason = '复活未就绪：' + msgOf(cause);
      return;
    }

    if (!panel) {
      // 没有全灭面板时：传送/道具/广告这几种也按「有英雄倒地」触发
      let dead = 0;
      if (box.mode === 'instant' || box.mode === 'tp' || box.mode === 'item' || box.mode === 'ad') {
        try {
          const hcfg = (mapping() && mapping().heroRevive) || {};
          const ctx = mapContext();
          ctx.units[ctx.cfg.each](function (_index, unit) {
            if (!unit) return false;
            if (typeof unit[hcfg.heroFlag] !== 'function' || unit[hcfg.heroFlag]() !== true) return false;
            if (typeof unit[hcfg.deadFlag] === 'function' && unit[hcfg.deadFlag]() === true) dead += 1;
            return false;
          });
        } catch (_) { dead = 0; }
      }
      box.deadCount = dead;
      if (!dead) {
        if (state.campFly.running) {
          box.reason = state.campFly.reason;
          return;
        }
        if (box.phase === 'waiting') {
          reviveReset();
          box.reason = box.mode === 'instant'
            ? '监控中：英雄倒地就原地立即复活（20 绿币/次，怪不回血）'
            : box.mode === 'tp'
            ? '监控中：英雄倒地就传营地复活，约 0.3 秒回原位（全灭约 11 秒）'
            : '监控中：英雄倒地或全灭后 ' + (box.delayMs / 1000) + ' 秒自动'
              + (box.mode === 'campfly' ? '回营地复活并飞回原位' : '复活');
        }
        return;
      }
    }

    // 全灭面板点「放弃」后会弹确认小窗：把「确认」按钮的坐标交给界面去点
    try {
      box.confirmAt = panel ? findConfirmPoint() : null;
    } catch (_) { box.confirmAt = null; }

    if (!box.pendingAt) {
      // 传送复活是服务端瞬发的，不需要等；立即复活留 0.5 秒把同一波倒地的合在一起
      box.pendingAt = nowMs + (box.mode === 'tp' ? 0 : box.mode === 'instant' ? 500 : box.delayMs);
      box.phase = 'waiting';
      const deadNow = box.deadCount || 0;
      box.reason = (panel ? '检测到全灭' : '检测到 ' + deadNow + ' 个英雄倒地')
        + '，' + (box.delayMs / 1000) + ' 秒后复活…';
      return;
    }
    if (nowMs < box.pendingAt) return;

    // 立即复活：游戏自带的服务器复活，原地起来，怪不回血（消耗绿币）
    if (box.mode === 'instant') {
      if (box.busy) return;
      box.busy = true;
      box.pendingAt = 0;
      box.phase = 'instant';
      const price = Number((cfg.instant && cfg.instant.price) || 20);
      const currencyId = (cfg.instant && cfg.instant.currencyId) || 0;
      instantRevive(cfg)
        .then(res => {
          if (res.ok) {
            box.count += 1;
            let left = null;
            try { left = gameBag(cfg).AUJP.PKQL(currencyId); } catch (_) { left = null; }
            box.lastResult = '立即复活（原地，-' + price + ' 绿'
              + (left === null ? '' : '，剩 ' + left) + '）';
            box.reason = box.lastResult + ' · 今日 ' + box.count + ' 次';
            box.returnTo = null;
            box.nextTryAt = now() + 800;

            // 防烧钱：刚复活又秒死，说明队伍打不过，连续 3 次就停 10 秒
            const gap = now() - (box.lastAt || 0);
            box.lastAt = now();
            box.rapid = gap < 5000 ? (box.rapid || 0) + 1 : 0;
            if (box.rapid >= 3) {
              box.rapid = 0;
              box.nextTryAt = now() + 10000;
              box.reason = '连续秒死，暂停 10 秒（不然绿币一直掉，换个图或升级再来）';
            }
          } else {
            box.reason = '立即复活失败：' + (res.error || ('错误码 ' + res.code))
              + '（绿币不够就换成「传送回营地复活」）';
            box.nextTryAt = now() + 3000;
          }
        })
        .catch(cause => {
          box.reason = '立即复活失败：' + msgOf(cause);
          box.nextTryAt = now() + 3000;
        })
        .then(() => { box.busy = false; box.phase = ''; });
      return;
    }

    // 传送回营地复活：传到营地（TCLA/CLAT）→ 等复活 → 传回原位
    if (box.mode === 'tp') {
      if (box.busy) return;
      box.busy = true;
      box.pendingAt = 0;
      box.phase = 'tp';
      const reviveCfg = mapping().revive || {};
      const began = now();

      // 全灭（失败面板已弹）时游戏不接受传送：位置不动、还会卡在加载。
      // 这种情况只能点面板的「放弃」回城复活（免费），回城后再自动回图。
      if (panel) {
        let spot = null;
        try { spot = teamLeaderSpot(); } catch (_) { spot = null; }
        const began2 = now();
        Promise.resolve()
          .then(() => reviveByCamp(cfg, panel))
          .then(async info => {
            if (spot) {
              box.reason = '已回城复活，等地图就绪…';
              await waitWorldReady(20000);
              box.reason = '已回城复活，正在传回原地…';
              for (let attempt = 0; attempt < 2; attempt += 1) {
                try { dropLoading(); } catch (_) { /* ignore */ }
                try { teleportFire(spot.x, spot.y); } catch (_) { break; }
                let arrived = false;
                const t = now();
                while (now() - t < 4000) {
                  await new Promise(r => setTimeout(r, 100));
                  let p = null;
                  try { p = teamLeaderSpot(); } catch (_) { p = null; }
                  if (p && Math.abs(p.x - spot.x) < 300 && Math.abs(p.y - spot.y) < 300) { arrived = true; break; }
                }
                if (arrived) break;
                await waitWorldReady(6000);
              }
              try { dropLoading(); } catch (_) { /* ignore */ }
              suppressLanding(3500);
            }
            box.count += 1;
            const cost = ((now() - began2) / 1000).toFixed(1);
            box.lastResult = '全灭回城复活完成（' + cost + ' 秒）';
            box.reason = box.lastResult + ' · 今日 ' + box.count + ' 次';
          })
          .catch(cause => {
            box.reason = '全灭回城失败：' + msgOf(cause);
          })
          .then(() => {
            box.busy = false;
            box.phase = '';
            box.nextTryAt = now() + 4000;
          });
        return;
      }

      let spot = null;
      Promise.resolve()
        .then(async () => {
          spot = teamLeaderSpot();
          if (!spot) throw Error('读不到队伍坐标');
          const core = window[mapConfig().holder][mapConfig().core];
          const camp = campSpot(core, reviveCfg.campTeleport);
          if (!camp) throw Error('读不到营地坐标（TCLA/CLAT）');

          const near2 = (p, t) => !!p && !!t
            && Math.abs(p.x - t.x) < 320 && Math.abs(p.y - t.y) < 320;

          // 第一跳：传营地。
          // 注意：海底这类图「人已经在营地」时再传营地会被当成传送门，会飞到别处
          const atCamp = near2(teamLeaderSpot(), camp);
          let revived = false;

          // ① 最快：只调 MEMK（结束战斗）——游戏会原地把倒地的英雄拉起来，
          //    不传送、不碰怪，实测 0.1 秒。
          reviveLog('开始：dead=' + (() => { try { return deadHeroCount(); } catch (_) { return -1; } })());
          box.reason = '正在复活…';
          leaveBattle();
          const tMemk = now();
          while (now() - tMemk < 800) {
            await new Promise(r => setTimeout(r, 40));
            let left = 1;
            try { left = deadHeroCount(); } catch (_) { left = 1; }
            if (left === 0) { revived = true; break; }
          }
          if (revived) reviveLog('MEMK 原地复活');

          if (!revived && !atCamp) {
            // ① 极速模式：传营地 → 80ms → 立刻传回（服务端按顺序处理，位置瞬间回来）
            reviveLog('开始：dead=' + (() => { try { return deadHeroCount(); } catch (_) { return -1; } })());
            box.reason = '快速复活中…';
            campTeleport(camp);
            await new Promise(r => setTimeout(r, 80));
            try { teleportNoFx(spot.x, spot.y); } catch (_) { /* ignore */ }
            const tFast = now();
            while (now() - tFast < 420) {
              await new Promise(r => setTimeout(r, 40));
              let left = 1;
              try { left = deadHeroCount(); } catch (_) { left = 1; }
              if (left === 0) { revived = true; break; }
            }
            if (revived) reviveLog('极速复活成功');
          }

          if (!revived) {
            // ② 稳妥模式：等真的复活了再回来（最多 10 秒）
            box.reason = '正在传送到营地…';
            campTeleport(camp);
            const t1 = now();
            while (now() - t1 < 10000) {
              await new Promise(r => setTimeout(r, 50));
              let left = 1;
              try { left = deadHeroCount(); } catch (_) { left = 1; }
              if (left === 0) { revived = true; break; }
              hideLanding();
            }
            if (!revived) {
              reviveLog('等复活超时');
              dropLoading();
              throw Error('到营地后英雄没有复活（这张图的复活点可能和其它图不一样）');
            }
            reviveLog('已复活');
          }

          // 第二跳：回原地，最多重试 8 秒
          box.reason = '已复活，正在回原地…';
          box.returnTo = { x: spot.x, y: spot.y, until: now() + 20000 };
          let back = false;
          const t2 = now();
          while (now() - t2 < 8000) {
            try { teleportNoFx(spot.x, spot.y); } catch (_) { teleportFire(spot.x, spot.y); }
            const t3 = now();
            while (now() - t3 < 600) {
              await new Promise(r => setTimeout(r, 40));
              let p = null;
              try { p = teamLeaderSpot(); } catch (_) { p = null; }
              if (near2(p, spot)) { back = true; break; }
            }
            if (back) break;
            // 瞬移没动就退回带加载的传送
            try { teleportFire(spot.x, spot.y); } catch (_) { /* ignore */ }
            const t4 = now();
            while (now() - t4 < 1200) {
              await new Promise(r => setTimeout(r, 100));
              let p = null;
              try { p = teamLeaderSpot(); } catch (_) { p = null; }
              if (near2(p, spot)) { back = true; break; }
            }
            if (back) break;
            await new Promise(r => setTimeout(r, 200));
          }
          dropLoading();
          suppressLanding(3500);
          reviveLog(back ? '已回到原地' : '回原地失败，转补传');
          if (back) box.returnTo = null;

          box.count += 1;
          const cost = ((now() - began) / 1000).toFixed(1);
          box.lastResult = back
            ? '快速复活完成（' + cost + ' 秒）'
            : '已复活，正在补传回原地（' + cost + ' 秒）';
          box.reason = box.lastResult + ' · 今日 ' + box.count + ' 次';
        })
        .catch(cause => {
          // 没复活成功也别把人扔在营地/奇怪的位置，先传回原来的地方
          try { if (spot) teleportFire(spot.x, spot.y); } catch (_) { /* ignore */ }
          box.reason = '传送复活失败：' + msgOf(cause);
        })
        .then(() => {
          box.busy = false;
          box.phase = '';
          box.nextTryAt = now() + 3000;
        });
      return;
    }

    // 免费回营地：和参考脚本一样，面板一出现就点「返回」，队伍回营地复活
    if (box.mode === 'free') {
      try {
        if (typeof panel[cfg.abandon] !== 'function') throw Error('全灭面板没有返回入口');
        panel[cfg.abandon]();
        box.count += 1;
        box.lastResult = '已回营地复活（免费）';
        box.reason = box.lastResult + ' · 今日 ' + box.count + ' 次';
      } catch (cause) {
        box.reason = '回营地失败：' + msgOf(cause);
      }
      box.pendingAt = 0;
      box.phase = '';
      box.nextTryAt = now() + 2000;
      state.map.deathSince = Object.create(null);
      return;
    }

    // 参考脚本那套：回营地（营地满血复活）→ 回图 → 飞回原位
    if (box.mode === 'campfly') {
      if (state.campFly.running) {
        box.reason = state.campFly.reason;
        return;
      }
      try {
        startCampFly('全灭');
        box.count += 1;
        box.reason = '已触发回营地复活：' + state.campFly.reason;
      } catch (cause) {
        box.reason = '回营地复活失败：' + msgOf(cause);
        box.nextTryAt = now() + 5000;
      }
      box.pendingAt = 0;
      box.phase = '';
      return;
    }

    box.busy = true;
    box.phase = 'acting';
    box.reason = '正在复活…';

    if (box.mode === 'camp') {
      Promise.resolve()
        .then(() => reviveByCamp(cfg, panel))
        .then(info => {
          box.count += 1;
          box.lastResult = info.how;
          box.reason = info.how + '（今日 ' + box.count + ' 次）';
          box.pendingAt = 0;
          box.phase = '';
          // 复活后留 2 秒冷却：面板可能还没关，别连着发第二次
          box.nextTryAt = now() + 2000;
          state.map.deathSince = Object.create(null);
        })
        .catch(cause => {
          box.reason = '复活失败：' + msgOf(cause);
          box.pendingAt = 0;
          box.phase = '';
          box.nextTryAt = now() + 5000;
        })
        .then(() => { box.busy = false; });
      return;
    }

    Promise.resolve()
      .then(() => callNativeRevive(cfg, box.mode === 'ad'))
      .then(result => {
        // 道具不够时自动改试广告复活（不花道具）
        if (!result.ok && box.mode !== 'ad') {
          return callNativeRevive(cfg, true).then(ad => {
            if (ad.ok) ad.viaAd = true;
            return ad;
          });
        }
        return result;
      })
      .then(result => {
        if (result.ok) {
          box.count += 1;
          const how = (box.mode === 'ad' || result.viaAd) ? '看广告复活' : '道具复活';
          box.lastResult = how;
          box.reason = '已' + how + '成功（今日 ' + box.count + ' 次）';
          box.pendingAt = 0;
          box.phase = '';
          box.nextTryAt = now() + 2000;
          state.map.deathSince = Object.create(null);
        } else {
          box.reason = '复活没成功：' + (result.error || (box.mode === 'ad'
            ? '广告复活没成功（可能没有可用广告）'
            : '道具不足或当前不让复活，可以改成「广告复活」或「回营地复活」'));
          box.pendingAt = 0;
          box.phase = '';
          box.nextTryAt = now() + 5000;
        }
      })
      .catch(cause => {
        box.reason = '复活失败：' + msgOf(cause);
        box.pendingAt = 0;
        box.phase = '';
        box.nextTryAt = now() + 5000;
      })
      .then(() => { box.busy = false; });
  }

  /* ------------------------------------------------------------------ *
   * 八、单英雄复活：我方英雄倒地 N 秒后，只把那一个原地拉起来
   *
   * 战斗内核（main 包）里的重生系统：
   *   VKXX.ZRRU.UDWZ().NOYR.LBUD(玩家编号, null, { CTXT: true, UWFS: [英雄配置号] })
   * 会遍历该玩家队伍里「是英雄 且 已死」的单位，只复活 UWFS 里列出的那些；
   * CTXT:true 把单位放回死亡时记录的位置，不会拉回出生点。
   * ------------------------------------------------------------------ */

  const heroReviveBox = { deadSince: Object.create(null) };
  let heroReviveAt = 0;

  function heroReviveConfig() {
    const build = mapping();
    if (!build) {
      state.missingMapping = true;
      throw Error('该游戏版本还没有字段映射，需要更新映射表');
    }
    const cfg = build.heroRevive;
    if (!cfg || !cfg.heroFlag || !cfg.deadFlag) {
      throw Error('该游戏版本还没有单英雄复活映射，需要更新映射表');
    }
    return cfg;
  }

  function heroReviveContext(cfg) {
    // 只需要实体世界：遍历我方英雄，判断谁倒地
    const mapCfg = mapConfig();
    const ctx = mapContext();
    return { ctx, mapCfg };
  }

  /* 本地计时复活：把英雄属性里的 SGQP（复活秒数，存储字段 HPJR）改成 N 秒，
   * 游戏自己的重生系统到点就会把英雄拉起来（原地）。
   * 注意：这是改客户端战斗模拟里的属性，服务端是否对账需要实测。 */
  const heroTimerBox = { written: [] };
  let heroTimerAt = 0;

  function heroTimerApply(cfg, seconds) {
    const mapCfg = mapConfig();
    const ctx = mapContext();
    const core = ctx.core;
    const world = ctx.holder;
    const mgr = core[mapCfg.manager]();
    const team = core[mapCfg.team](world[mapCfg.player]);
    const members = mgr[mapCfg.members](team) || [];
    let ok = 0;
    for (const id of members) {
      const unit = core[mapCfg.read](id);
      const attr = unit && mapCfg.unit.attribute ? unit[mapCfg.unit.attribute] : null;
      if (!attr || !writableNumber(attr, cfg.store)) continue;
      let entry = null;
      for (const item of heroTimerBox.written) {
        if (item.owner === attr && item.key === cfg.store) { entry = item; break; }
      }
      if (!entry) {
        entry = {
          owner: attr, key: cfg.store, original: attr[cfg.store], last: seconds,
          own: Object.getOwnPropertyDescriptor(attr, cfg.store),
        };
        heroTimerBox.written.push(entry);
      }
      if (attr[cfg.store] !== seconds) attr[cfg.store] = seconds;
      entry.last = seconds;
      ok += 1;
    }
    return ok;
  }

  function heroTimerRestore() {
    try { heroTimerBox.written = combatRestore(heroTimerBox.written); } catch (_) { /* ignore */ }
  }

  function tickHeroRevive() {
    const box = state.heroRevive;
    if (box.enabled && box.timer) {
      if (now() < heroTimerAt) return;
      heroTimerAt = now() + 1000;
      let cfg = null;
      try { cfg = heroReviveConfig(); } catch (cause) { box.reason = '未生效：' + msgOf(cause); return; }
      let ok = 0;
      try { ok = heroTimerApply(cfg, box.seconds); } catch (_) { return; }
      box.heroes = ok;
      box.reason = ok
        ? '已生效：英雄倒地后 ' + box.seconds + ' 秒自动复活（本地计时，' + ok + ' 个英雄）'
        : '当前地图没找到我方英雄';
      return;
    }
    if (!box.enabled) {
      heroReviveBox.deadSince = Object.create(null);
      box.heroes = 0;
      return;
    }
    if (box.busy || now() < box.nextTryAt) return;
    if (now() < heroReviveAt) return;
    heroReviveAt = now() + 250;

    let ctx = null;
    let cfg = null;
    let reviveCfg = null;
    try {
      cfg = heroReviveConfig();
      reviveCfg = reviveConfig();
      const view = heroReviveContext(cfg);
      ctx = view.ctx;
    } catch (cause) {
      box.reason = '未生效：' + msgOf(cause);
      return;
    }

    const nowMs = now();
    const dead = [];
    const alive = Object.create(null);
    ctx.units[ctx.cfg.each](function (_index, unit) {
      if (!unit) return false;
      if (typeof unit[cfg.heroFlag] !== 'function' || unit[cfg.heroFlag]() !== true) return false;
      const index = unit[ctx.cfg.unit.index];
      const key = String(index === undefined || index === null ? unit[ctx.cfg.unit.entity] : index);
      if (typeof unit[cfg.deadFlag] !== 'function' || unit[cfg.deadFlag]() !== true) return false;
      alive[key] = true;
      if (!heroReviveBox.deadSince[key]) heroReviveBox.deadSince[key] = nowMs;
      dead.push({ key, since: heroReviveBox.deadSince[key] });
      return false;
    });
    for (const key of Object.keys(heroReviveBox.deadSince)) {
      if (!alive[key]) delete heroReviveBox.deadSince[key];
    }
    box.heroes = dead.length;
    if (!dead.length) {
      if (state.campFly.running) {
        box.reason = state.campFly.reason;
      } else {
        box.reason = '监控中：英雄倒地后 ' + box.seconds + ' 秒'
          + (box.campfly ? '回营地复活并飞回原位' : (box.ad ? '看广告' : '用道具') + '复活');
      }
      return;
    }

    const delayMs = Math.max(0, box.seconds) * 1000;
    const ready = dead.filter(item => nowMs - item.since >= delayMs);
    if (!ready.length) {
      const wait = Math.max(0, delayMs - (nowMs - dead[0].since));
      box.reason = '检测到 ' + dead.length + ' 个英雄倒地，'
        + (wait > 0 ? (Math.ceil(wait / 100) / 10) + ' 秒后复活' : '准备复活');
      return;
    }

    // 回营地复活 + 飞回原位（参考脚本的做法）
    if (box.campfly) {
      if (state.campFly.running) {
        box.reason = state.campFly.reason;
        return;
      }
      try {
        startCampFly('英雄倒地');
        box.count += 1;
        box.reason = '已触发回营地复活：' + state.campFly.reason;
      } catch (cause) {
        box.reason = '回营地复活失败：' + msgOf(cause);
        box.nextTryAt = now() + 5000;
      }
      return;
    }

    // 用游戏自己的「复活」请求（和全灭面板点复活是同一条，服务端认这个）
    box.busy = true;
    box.reason = '正在向服务器申请复活…';
    const pendingKeys = ready.map(item => item.key);
    Promise.resolve()
      .then(() => callNativeRevive(reviveCfg, box.ad))
      .then(result => {
        if (!result.ok && !box.ad) {
          return callNativeRevive(reviveCfg, true).then(ad => {
            if (ad.ok) ad.viaAd = true;
            return ad;
          });
        }
        return result;
      })
      .then(result => {
        if (result.ok) {
          box.count += 1;
          box.reason = '已复活（倒地 ' + box.seconds + ' 秒，' + (box.ad || result.viaAd ? '广告' : '道具')
            + '）· 今日 ' + box.count + ' 次';
          for (const key of pendingKeys) delete heroReviveBox.deadSince[key];
        } else {
          box.reason = '服务器没同意复活：' + (result.error || '道具不足或当前不让复活')
            + '，可以改成「看广告」或等全灭用快速复活';
          box.nextTryAt = now() + 5000;
        }
      })
      .catch(cause => {
        box.reason = '复活失败：' + msgOf(cause);
        box.nextTryAt = now() + 5000;
      })
      .then(() => { box.busy = false; });
  }

  /* ------------------------------------------------------------------ *
   * 九、回营地复活 + 飞回原位（参考脚本的 gohome → goto → fly）
   *
   *   回营地 = 重进当前地图 XIPH(当前地图号)：队伍回到这张图的营地/篝火，
   *            阵亡英雄在营地复活（服务端认，不需要回主城）；
   *   飞回：main 包的位置接口 LFFY.DLMM / JTSZ，把队伍坐标写回出发位置。
   * 整个过程是异步的，用 250ms 一次的 tick 推进；中途可以随时被 stopAll 停掉。
   * ------------------------------------------------------------------ */

  function campFlyConfig() {
    const build = mapping();
    if (!build) {
      state.missingMapping = true;
      throw Error('该游戏版本还没有字段映射，需要更新映射表');
    }
    const cfg = build.campFly;
    if (!cfg || !cfg.fly) throw Error('该游戏版本还没有回营地 / 瞬移映射，需要更新映射表');
    return cfg;
  }

  function teamSpot(cfg) {
    const mapCfg = mapConfig();
    const world = window[mapCfg.holder];
    if (!world) throw Error('还没进入游戏');
    const core = world[mapCfg.core];
    if (!core) throw Error('实体世界还没就绪');
    const manager = core[mapCfg.manager]();
    const team = core[mapCfg.team](world[mapCfg.player]);
    const members = manager[mapCfg.members](team);
    if (!Array.isArray(members) || !members.length) throw Error('还没找到我方队伍');
    const unit = core[mapCfg.read](members[0]);
    if (!unit) throw Error('读不到队长坐标');
    return {
      members,
      x: unit[cfg.fly.x],
      y: unit[cfg.fly.y],
      mapId: world[mapCfg.state][mapCfg.mapId],
    };
  }

  /** 把整支队伍搬到 (x, y)：LFFY.DLMM 写世界坐标，JTSZ 同步表现层。 */
  function flyTo(cfg, x, y) {
    const f = cfg.fly;
    const system = window.System;
    const mod = system && typeof system.get === 'function' ? system.get(f.moduleId) : null;
    const api = mod && mod[f.export];
    if (!api || typeof api[f.set] !== 'function') {
      throw Error('游戏瞬移接口还没加载（' + f.export + '）');
    }
    const mapCfg = mapConfig();
    const world = window[mapCfg.holder];
    const core = world[mapCfg.core];
    const manager = core[mapCfg.manager]();
    const team = core[mapCfg.team](world[mapCfg.player]);
    const members = manager[mapCfg.members](team) || [];
    if (!members.length) throw Error('还没找到我方队伍');
    let ok = 0;
    // 队长站目标点，其余人围一个小圈，避免 10 个人叠在同一个坐标
    for (let i = 0; i < members.length; i += 1) {
      const id = members[i];
      const angle = (Math.PI * 2 * i) / Math.max(1, members.length);
      const radius = i === 0 ? 0 : 36;
      const px = x + Math.cos(angle) * radius;
      const py = y + Math.sin(angle) * radius;
      try {
        api[f.set](id, px, py, false);
        if (typeof api[f.move] === 'function') {
          api[f.move](id, px, py, 0, f.moveMs || 300, f.moveMode || 1);
        }
        ok += 1;
      } catch (_) { /* 单个失败不影响其它 */ }
    }
    if (!ok) throw Error('瞬移没有生效');
    return ok;
  }

  /** 开始一轮「回营地复活 + 飞回原位」；已经在跑就返回 false。 */
  let campFlyCoreRef = null;

  function startCampFly(label) {
    const cfg = campFlyConfig();
    const box = state.campFly;
    if (box.running) return false;
    const spot = teamSpot(cfg);
    const mapCfg = mapConfig();
    const world = window[mapCfg.holder];
    box.running = true;
    box.phase = 'reload';
    box.savedMapId = spot.mapId;
    box.savedX = spot.x;
    box.savedY = spot.y;
    box.startedAt = now();
    box.phaseAt = now();
    box.reason = (label || '复活') + '：正在回营地（重进 ' + box.savedMapId + ' 号图）…';
    campFlyCoreRef = world[mapCfg.core] || null;
    // 营地复活 = 重进当前地图：队伍会回到地图营地/篝火，阵亡英雄随之复活
    world[mapCfg.enter](box.savedMapId);
    return true;
  }

  function stopCampFly(reason) {
    const box = state.campFly;
    box.running = false;
    box.phase = '';
    campFlyCoreRef = null;
    if (reason) box.reason = reason;
  }

  function tickCampFly() {
    const box = state.campFly;
    if (!box.running) return;
    let cfg = null;
    let mapCfg = null;
    let world = null;
    let core = null;
    let mapId = null;
    try {
      cfg = campFlyConfig();
      mapCfg = mapConfig();
      world = window[mapCfg.holder];
      core = world[mapCfg.core];
      mapId = world[mapCfg.state][mapCfg.mapId];
    } catch (_) {
      return; // 过场/加载中，下一轮再试
    }
    const nowMs = now();

    if (box.phase === 'reload') {
      // 等实体世界换成新对象（XIPH 完成后 VKXX.ZRRU 会换）
      if (!core || core === campFlyCoreRef) {
        if (nowMs - box.phaseAt > (cfg.reloadTimeoutMs || 15000)) stopCampFly('回营地超时（地图没重进成功）');
        return;
      }
      let members = null;
      let dead = 0;
      try {
        const mgr = core[mapCfg.manager]();
        const team = core[mapCfg.team](world[mapCfg.player]);
        members = mgr[mapCfg.members](team);
        if (members && members.length) {
          for (const id of members) {
            const unit = core[mapCfg.read](id);
            if (unit && mapCfg.unit.dead && typeof unit[mapCfg.unit.dead] === 'function' && unit[mapCfg.unit.dead]()) {
              dead += 1;
            }
          }
        }
      } catch (_) { members = null; }
      if (!members || !members.length) {
        if (nowMs - box.phaseAt > (cfg.backTimeoutMs || 20000)) stopCampFly('回营地后队伍没刷出来');
        return;
      }
      // 还有倒地的就先在营地等一会（最多 5 秒）
      if (dead > 0 && nowMs - box.phaseAt < 5000) return;
      box.phase = 'fly';
      box.phaseAt = nowMs;
      box.reason = '已回营地（英雄复活），准备飞回原位…';
      return;
    }

    if (box.phase === 'fly') {
      if (nowMs - box.phaseAt < (cfg.settleMs || 600)) return;
      // 写入坐标会让客户端的战斗模拟和服务端对不上（BattleHashDiff），默认不飞。
      // 只有映射里把 flyBack 显式打开才执行，自己承担被检测的风险。
      if (cfg.flyBack === true) {
        try {
          const n = flyTo(cfg, box.savedX, box.savedY);
          box.count += 1;
          box.lastResult = '已回营地复活并飞回原位（' + n + ' 个英雄）';
          box.reason = box.lastResult + ' · 今日 ' + box.count + ' 次';
        } catch (cause) {
          box.reason = '复活成功，但飞回失败：' + msgOf(cause);
        }
      } else {
        box.count += 1;
        box.lastResult = '已回营地复活（队伍在营地）';
        box.reason = box.lastResult + ' · 今日 ' + box.count + ' 次（飞回已关闭，避免被检测）';
      }
      box.running = false;
      box.phase = '';
      campFlyCoreRef = null;
      return;
    }
  }

  function tick() {
    probe();
    // 红包领取由 RED_TICK_MS 的专用定时器跑；这里只负责跨天清零
    try { rollRedDay(); } catch (_) { /* ignore */ }

    // 祈愿不依赖红包服务是否可用，单独跑，互不影响。
    try { tickWish(); } catch (cause) { state.wish.reason = '祈愿异常：' + msgOf(cause); }
    // 地图同理：红包/祈愿都不可用时，打怪重进地图照样能跑。
    try { tickMap(); } catch (cause) {
      const text = msgOf(cause);
      // 重进地图的加载窗口里实体世界会短暂不可用，这不算失败，别把状态刷成报错
      if (!state.map.entering) {
        if (state.mob.enabled) state.mob.reason = text;
        if (state.boss.enabled) state.boss.reason = text;
      }
    }
    // 战斗参数：只做「会话是否还有效」的守护，不改数值
    try { tickCombat(); } catch (_) { /* 守护失败不影响其它功能 */ }
    // 快速复活：独立跑，不依赖红包/地图刷新
    try { tickRevive(); } catch (cause) { state.revive.reason = '复活异常：' + msgOf(cause); }
    // 单英雄复活时间
    try { tickHeroRevive(); } catch (_) { /* 每秒重试 */ }
    // 回营地复活 + 飞回原位
    try { tickCampFly(); } catch (_) { /* 下一轮继续 */ }
    // 未登录时给手机登录页补上「微信扫码登录」入口
    try { ensureWechatLogin(); } catch (_) { /* 登录页结构变化时忽略 */ }
  }

  /* ------------------------------------------------------------------ *
   * 五、状态与对外接口
   * ------------------------------------------------------------------ */

  function probe() {
    if (now() < state.nextProbeAt) return;
    state.nextProbeAt = now() + RETRY_MS;
    state.build = state.build || detectBuild();
    state.ctx = null;
    state.wishCtx = null;
    try {
      resolve();
      state.ready = true;
      state.error = '';
      state.missingMapping = false;
      state.message = '辅助已连接（' + (state.build || '未知版本') + '）';
    } catch (cause) {
      state.ready = false;
      state.error = msgOf(cause);
      // 只有「已经识别到版本」且确实缺映射，才算版本问题；
      // 还没进游戏（build 未知）时只是没加载完，不能把按钮全置灰。
      state.missingMapping = !!state.build && /字段映射|映射/.test(state.error);
    }
  }

  /** 英雄列表：拥有的（带品质）+ 未拥有的（从名字表补），名字取「·」后面的短名。 */
  function heroList() {
    try {
      const MAPPINGALL = (MAPPING && MAPPING.builds) || {};
      let g = (mapping() || {}).gacha;
      if (!g) {
        const key = Object.keys(MAPPINGALL)[0];
        g = key ? MAPPINGALL[key].gacha : null;
      }
      if (!g || !g.hero || !g.holderModule) return [];
      const system = window.System;
      const mod = system && typeof system.get === 'function' ? system.get(g.holderModule) : null;
      const holder = mod && mod[g.holder];
      const svc = holder && holder[g.hero.service];
      const t = svc && svc[g.hero.model];
      const owned = t && t[g.hero.list];
      let i18n = {};
      try {
        const m2 = system.get(g.hero.i18nModule);
        i18n = (m2 && m2[g.hero.i18nExport]) || {};
      } catch (_) { i18n = {}; }
      const prefix = g.hero.namePrefix || 'hero_name_';
      const shortOf = full => {
        const parts = String(full || '').split(/[·・－\-]/);
        return String(parts[parts.length - 1] || full || '').trim();
      };
      const qualityOf = id => {
        try {
          const e = owned && owned.get ? owned.get(id) : null;
          return e ? Number(e[g.hero.quality]) || 0 : 0;
        } catch (_) { return 0; }
      };
      const out = [];
      const seen = Object.create(null);
      if (owned && typeof owned.forEach === 'function') {
        owned.forEach(function (_entry, id) {
          const hid = Number(id);
          if (!Number.isSafeInteger(hid) || hid <= 0) return;
          const full = String(i18n[prefix + hid] || '');
          seen[hid] = true;
          out.push({ id: hid, name: shortOf(full) || String(hid), quality: qualityOf(hid), owned: true });
        });
      }
      // 未拥有的英雄：从名字表里补（皮肤编号一般 7 位以上，排除掉）
      try {
        for (const key of Object.keys(i18n)) {
          if (key.indexOf(prefix) !== 0) continue;
          const hid = Number(key.slice(prefix.length));
          if (!Number.isSafeInteger(hid) || hid <= 0 || seen[hid]) continue;
          if (String(hid).length > 6) continue;
          const full = String(i18n[key] || '');
          if (!full) continue;
          out.push({ id: hid, name: shortOf(full), quality: 0, owned: false });
        }
      } catch (_) { /* ignore */ }
      out.sort((a, b) => (b.owned - a.owned) || (b.quality - a.quality) || (a.id - b.id));
      return out;
    } catch (_) {
      return [];
    }
  }

  function snapshot() {
    try { rollRedDay(); } catch (_) { /* ignore */ }
    const red = state.red;
    return {
      ok: true,
      ready: state.ready,
      error: state.error,
      missingMapping: state.missingMapping,
      build: state.build,
      message: state.message,
      mob: { enabled: state.mob.enabled, seconds: state.mob.seconds, reason: state.mob.reason },
      boss: { enabled: state.boss.enabled, seconds: state.boss.seconds, reason: state.boss.reason },
      redPacket: {
        enabled: red.enabled,
        available: red.available,
        reason: red.reason,
        today: red.today,
        date: red.date,
        claimed: red.claimed,
        failed: red.failed,
        pending: red.pending,
        green: redGreenValue(),
        greenExact: red.exactGreen || 0,
        greenExactCount: red.exactCount || 0,
        lastAmount: red.lastAmount || 0,
        amountError: red.amountError || '',
        hooked: !!red.hookId,
        retry: red.retry ? Object.keys(red.retry).length : 0,
        lastCode: red.lastCode || 0,
        failReason: red.failReason || '',
      },
      wish: {
        enabled: state.wish.enabled,
        available: state.wish.available,
        reason: state.wish.reason,
        claimed: state.wish.claimed,
        pending: state.wish.pending,
        slots: state.wish.waiting.slice(0, 8),
      },
      gold: { today: state.gold.today, date: state.gold.date },
      revive: {
        log: (state.revive.log || []).slice(-8),
        enabled: state.revive.enabled,
        mode: state.revive.mode,
        delayMs: state.revive.delayMs,
        count: state.revive.count,
        reason: state.revive.reason,
        lastResult: state.revive.lastResult,
        confirmAt: state.revive.confirmAt || null,
      },
      heroRevive: {
        enabled: state.heroRevive.enabled,
        seconds: state.heroRevive.seconds,
        campfly: state.heroRevive.campfly,
        heroes: state.heroRevive.heroes,
        count: state.heroRevive.count,
        reason: state.heroRevive.reason,
      },
      blink: {
        busy: state.blink.busy,
        last: state.blink.last,
        reason: state.blink.reason,
      },
      campFly: {
        running: state.campFly.running,
        phase: state.campFly.phase,
        reason: state.campFly.reason,
        count: state.campFly.count,
      },
      combat: {
        damage: {
          enabled: state.combat.damage.enabled,
          value: state.combat.damage.value,
          reason: state.combat.damage.reason,
        },
        shield: {
          enabled: state.combat.shield.enabled,
          value: state.combat.shield.value,
          reason: state.combat.shield.reason,
        },
        vision: {
          enabled: state.combat.vision.enabled,
          value: state.combat.vision.value,
          reason: state.combat.vision.reason,
        },
        cooldown: {
          enabled: state.combat.cooldown.enabled,
          value: state.combat.cooldown.value,
          reason: state.combat.cooldown.reason,
        },
      },
      map: {
        sceneId: state.map.sceneId,
        units: Object.keys(state.map.seen).length,
        kills: { mob: state.map.kills.mob, boss: state.map.kills.boss },
        bossGold: state.map.bossGold,
        entering: state.map.entering,
        blocked: state.map.enterBlocked,
        lastAction: state.map.lastAction,
      },
    };
  }

  const unsupported = label => ({
    ok: false,
    message: '「' + label + '」还没接入当前版本（红包已经可用，其它功能正在做）',
  });

  function stopAllFeatures() {
    try { farmStop('已关闭'); } catch (_) { /* ignore */ }
    try { farmClearStopTimers(); } catch (_) { /* ignore */ }
    try { dailyClaimsStop(); } catch (_) { /* ignore */ }
    try { plankStop('已关闭'); } catch (_) { /* ignore */ }
    try { set('stopAll'); } catch (_) { /* ignore */ }
    return { ok: true, message: '已全部关闭' };
  }

  function set(feature, value) {
    probe();
    if (feature === 'stopAll') {
      state.red.enabled = false;
      state.red.reason = '已关闭';
      wishStop('已关闭');
      mapStop('已关闭');
      for (const name of ['damage', 'shield', 'vision', 'cooldown']) {
        try { stopCombat(name); } catch (_) { /* 单项失败不影响其它 */ }
      }
      state.revive.enabled = false;
      state.revive.reason = '已关闭';
      reviveReset();
      state.heroRevive.enabled = false;
      state.heroRevive.heroes = 0;
      state.heroRevive.reason = '已关闭';
      heroReviveBox.deadSince = Object.create(null);
      state.heroRevive.timer = false;
      heroTimerRestore();
      try { stopCampFly('已关闭'); } catch (_) { /* ignore */ }
      return { ok: true, message: '已全部关闭（战斗数值已恢复）' };
    }

    if (feature === 'clearStuck') {
      // 切图标记卡住时，游戏会静默忽略所有传送/回城（标记名按版本来）
      const mapCfg = mapConfig() || {};
      const cs = mapCfg.clearStuck || {};
      const flags = Array.isArray(cs.flags) && cs.flags.length ? cs.flags : ['XXNL', 'FZJB'];
      const loadingHolder = cs.loadingHolder || 'HOFK';
      const loadingField = mapCfg.loading || 'MAOP';
      let w = null;
      try { w = window[mapCfg.holder]; } catch (_) { w = null; }
      if (!w) return { ok: false, message: '还没进入游戏（找不到世界服务）' };
      let before = {};
      try {
        before = { loading: !!(w[loadingHolder] && w[loadingHolder][loadingField]) };
        flags.forEach((name) => { if (w[name]) before[name] = true; });
      } catch (_) { before = {}; }
      flags.forEach((name) => { try { w[name] = false; } catch (_) { /* ignore */ } });
      try { if (w[loadingHolder]) w[loadingHolder][loadingField] = false; } catch (_) { /* ignore */ }
      try {
        const st = mapCfg.state ? w[mapCfg.state] : null;
        if (st && loadingField && st[loadingField] !== undefined) st[loadingField] = false;
      } catch (_) { /* ignore */ }
      return {
        ok: true,
        message: '已清除卡住的切图标记（清除前：' + flags[0] + '=' + (before[flags[0]] ? '卡住' : '正常')
          + '，加载中=' + (before.loading ? '是' : '否') + '）',
      };
    }

    if (feature === 'blinkTo') {
      const raw = value && typeof value === 'object' ? value : {};
      try {
        return blinkToCoords(Number(raw.x) || 0, Number(raw.y) || 0, raw.mode === 'fly' ? 'fly' : 'instant');
      } catch (cause) {
        return { ok: false, message: msgOf(cause) };
      }
    }

    if (feature === 'blink') {
      try {
        return doBlink(value);
      } catch (cause) {
        state.blink.reason = '闪现失败：' + msgOf(cause);
        return { ok: false, message: msgOf(cause) };
      }
    }

    if (feature === 'reviveNow') {
      const box = state.revive;
      if (box.busy) return { ok: false, message: '上一次复活还没结束' };
      box.busy = true;
      box.reason = '手动复活中…';
      Promise.resolve()
        .then(() => manualRevive())
        .then(msg => { box.reason = String(msg || '复活完成'); })
        .catch(cause => { box.reason = '手动复活失败：' + msgOf(cause); })
        .then(() => { box.busy = false; });
      return { ok: true, message: '开始手动复活' };
    }

    if (feature === 'heroRevive') {
      try {
      const box = state.heroRevive;
      const raw = value && typeof value === 'object' ? value : { seconds: value };
      const seconds = Number(raw.seconds) || 0;
      if (raw.timer) {
        throw Error('「本地改复活倒计时」会触发战斗哈希校验（实测弹数据不一致），已停用');
      }
      if (!(seconds > 0)) {
        box.enabled = false;
        box.heroes = 0;
        box.reason = '已关闭';
        box.busy = false;
        box.timer = false;
        heroReviveBox.deadSince = Object.create(null);
        heroTimerRestore();
        return { ok: true, message: '英雄复活已关闭' };
      }
      const cfg = heroReviveConfig();
      const range = cfg.range || [1, 60];
      if (seconds < range[0] || seconds > range[1]) {
        throw Error('复活时间需要在 ' + range[0] + ' - ' + range[1] + ' 秒之间');
      }
      // 重复下发同样的设置（页面刚重载后的状态补发）不要清空倒地计时
      if (box.enabled && Number(box.seconds) === seconds
        && box.ad === !!raw.ad && box.campfly === !!raw.campfly && box.timer === !!raw.timer) {
        return { ok: true, message: '英雄复活已在运行（无需重复开启）' };
      }
      box.enabled = true;
      box.seconds = seconds;
      box.ad = !!raw.ad;
      box.campfly = !!raw.campfly;
      box.timer = !!raw.timer;
      box.heroes = 0;
      box.busy = false;
      box.nextTryAt = 0;
      heroReviveBox.deadSince = Object.create(null);
      box.reason = box.timer
        ? '监控中：英雄倒地后 ' + seconds + ' 秒自动复活（本地计时）'
        : (box.campfly
          ? '监控中：英雄倒地后 ' + seconds + ' 秒回营地复活并飞回原位'
          : ('监控中：英雄倒地后 ' + seconds + ' 秒' + (box.ad ? '看广告' : '用道具') + '复活'));
      return {
        ok: true,
        message: box.timer
          ? '英雄复活已开启：本地计时 ' + seconds + ' 秒自动复活'
          : (box.campfly
            ? '英雄复活已开启（回营地复活 + 飞回原位）'
            : '英雄复活已开启：倒地 ' + seconds + ' 秒后向服务器申请复活'),
      };
      } catch (cause) {
        state.heroRevive.enabled = false;
        state.heroRevive.reason = '未开启：' + msgOf(cause);
        return { ok: false, message: msgOf(cause) };
      }
    }

    if (feature === 'revive') {
      // value = 0 关闭；{ mode: 'item'|'ad'|'camp', delay: 秒 }
      const raw = value && typeof value === 'object' ? value : {};
      const off = value === 0 || value === false || value === null || value === undefined;
      if (off) {
        state.revive.enabled = false;
        state.revive.reason = '已关闭';
        reviveReset();
        return { ok: true, message: '快速复活已关闭' };
      }
      const box = state.revive;
      const mode = ['instant', 'local', 'item', 'ad', 'camp', 'campfly', 'free', 'tp'].indexOf(raw.mode) >= 0 ? raw.mode : 'item';
      let seconds = Number(raw.delay);
      if (!Number.isFinite(seconds) || seconds < 0) seconds = 0.5;
      seconds = Math.min(10, seconds);
      // 同样避免「每次轮询都重发」把正在进行的复活流程重置
      if (box.enabled && box.mode === mode && box.delayMs === Math.round(seconds * 1000)) {
        return { ok: true, message: '快速复活已在运行（无需重复开启）' };
      }
      if (mode === 'item' || mode === 'ad') {
        try {
          const cfg = reviveConfig();
          const yx = gameBag(cfg)[cfg.heroService];
          if (!yx || typeof yx[cfg.revive] !== 'function') {
            throw Error('英雄服务里没有复活接口（' + cfg.revive + '），该版本可能改了');
          }
        } catch (cause) {
          box.enabled = false;
          box.reason = '未开启：' + msgOf(cause);
          return { ok: false, message: msgOf(cause) };
        }
      }
      box.enabled = true;
      box.mode = mode;
      box.delayMs = Math.round(seconds * 1000);
      box.pendingAt = 0;
      box.phase = '';
      box.nextTryAt = 0;
      box.reason = '监控中：' + (
        mode === 'local' ? '英雄倒地就立刻原地复活'
          : mode === 'instant' ? '英雄倒地就原地立即复活（每次 20 绿币）'
          : '全灭后 ' + seconds + ' 秒自动' + (
        mode === 'tp' ? '传送回营地复活'
          : mode === 'free' ? '免费回营地复活'
          : mode === 'campfly' ? '回营地复活并飞回原位'
            : mode === 'camp' ? '放弃回营地复活并返回地图'
              : (mode === 'ad' ? '看广告复活' : '用道具复活')));
      const label = mode === 'local' ? '原地秒复活'
        : mode === 'instant' ? '立即复活（原地）'
        : mode === 'tp' ? '传送回营地复活'
        : mode === 'free' ? '免费回营地复活'
        : mode === 'campfly' ? '回营地复活 + 飞回原位'
          : mode === 'camp' ? '回营地复活' : (mode === 'ad' ? '广告复活' : '道具复活');
      return { ok: true, message: '快速复活已开启（' + label + '，延迟 ' + seconds + ' 秒）' };
    }

    if (feature === 'damage' || feature === 'shield' || feature === 'vision' || feature === 'cooldown') {
      const label = { damage: '英雄倍伤', shield: '英雄免伤', vision: '视野修改', cooldown: '技能冷却' }[feature];
      try {
        return setCombat(feature, value);
      } catch (cause) {
        const text = msgOf(cause);
        try { stopCombat(feature); } catch (_) { /* ignore */ }
        state.combat[feature].reason = '未开启：' + text;
        return { ok: false, message: '[' + label + '] ' + text };
      }
    }

    if (feature === 'mob' || feature === 'boss') {
      const target = feature === 'mob' ? state.mob : state.boss;
      const label = feature === 'mob' ? '小怪' : 'BOSS';
      const seconds = Number(value) || 0;
      if (!seconds) {
        target.enabled = false;
        target.reason = '已关闭';
        state.map.mobAt = feature === 'mob' ? 0 : state.map.mobAt;
        state.map.bossAt = feature === 'boss' ? 0 : state.map.bossAt;
        return { ok: true, message: '已关闭' };
      }
      // 重复下发同样的等待秒数（例如助手每次轮询补状态）不能清掉死亡计时，
      // 否则 BOSS 一死、倒计时刚走一半又被重置，永远等不到重进地图。
      if (target.enabled && Number(target.seconds) === seconds && !state.map.enterBlocked) {
        return { ok: true, message: label + '死亡后等待已是 ' + seconds + ' 秒（无需重复开启）' };
      }
      // 参考工具同样要求：改过战斗数值就先别开地图刷新（重进会把配置冲掉、数值也可能错乱）
      if (combatActive()) {
        target.enabled = false;
        target.reason = '未开启：请先关闭技能冷却、英雄伤害和视野修改';
        return { ok: false, message: '请先关闭技能冷却、英雄伤害和视野修改，再开启地图刷新' };
      }
      let ctx = null;
      try {
        ctx = mapContext();
      } catch (cause) {
        target.enabled = false;
        target.reason = '未开启：' + msgOf(cause);
        return { ok: false, message: msgOf(cause) };
      }
      target.enabled = true;
      target.seconds = seconds;
      target.reason = '监控中：' + label + '死亡后 ' + seconds + ' 秒重进地图';
      state.map.sceneId = ctx.state[ctx.cfg.sceneId];
      state.map.seen = Object.create(null);
      state.map.deathSince = Object.create(null);
      // 手动再开一次 = 允许再试一次重进
      state.map.enterBlocked = false;
      return { ok: true, message: label + '死亡后等待已开启：' + seconds + ' 秒后重进当前地图' };
    }

    if (feature === 'redPacket') {
      if (!state.ready) {
        return { ok: false, message: state.error || '辅助未就绪' };
      }
      state.red.enabled = !!value;
      try { installRedHooks(); } catch (_) { /* ignore */ }
      if (value) {
        rollRedDay();
        state.red.greenSeen = greenBalance();
      } else {
        state.red.greenUntil = 0;
      }
      state.red.reason = value
        ? '监控中：红包一进聊天立刻领（60ms 轮询兜底）'
        : '已关闭（今日 ' + state.red.today + ' 个 / ' + redGreenValue() + ' 绿钻）';
      return {
        ok: true,
        message: value
          ? '快速抢红包已开启（今日 ' + state.red.today + ' 个 / ' + redGreenValue() + ' 绿钻）'
          : '快速抢红包已关闭',
      };
    }

    if (feature === 'wish') {
      if (!value) {
        wishStop('已关闭');
        return { ok: true, message: '祈愿领取已关闭' };
      }
      try {
        wishStart();
      } catch (cause) {
        wishStop('未开启：' + msgOf(cause));
        return { ok: false, message: msgOf(cause) };
      }
      return { ok: true, message: '祈愿领取已开启：到期会自动领取并把原英雄放回原槽位' };
    }

    return { ok: false, message: '未知功能：' + String(feature) };
  }

  function diagnose() {
    state.build = state.build || detectBuild();
    const out = {
      build: state.build,
      version: VERSION,
      mappingFound: !!mapping(),
      mappingBuilds: Object.keys((MAPPING && MAPPING.builds) || {}),
      error: '',
      registry: 0,
      redPacket: null,
      chat: null,
      hub: false,
      packet: null,
    };
    try {
      const ctx = resolve();
      out.registry = ctx.reg.size;
      out.redPacket = {
        service: ctx.redCfg.service,
        methods: Object.getOwnPropertyNames(Object.getPrototypeOf(ctx.redPacket)).slice(0, 24),
      };
      out.hub = !!ctx.hub;
      out.packet = {
        wchk: ctx.redPacket.WCHK,
        ba: ctx.redPacket.BAEA,
        item: ctx.redPacket.NDSP,
        today: state.red.today,
      };
      if (ctx.chat) {
        let channels = 0;
        let messages = 0;
        let withPacket = 0;
        try {
          ctx.chat[ctx.chatCfg.messages].forEach(list => {
            channels += 1;
            if (!Array.isArray(list)) return;
            messages += list.length;
            for (const message of list) {
              const detail = message && message[ctx.chatCfg.detail];
              if (detail && detail[ctx.chatCfg.info]) withPacket += 1;
            }
          });
        } catch (cause) {
          out.chat = { error: msgOf(cause) };
        }
        out.chat = out.chat || { channels, messages, withPacket };
      }
    } catch (cause) {
      out.error = msgOf(cause);
    }
    try {
      const wish = wishContext();
      out.wish = {
        service: wish.cfg.service,
        statuses: wish.status,
        clock: wish.clock(),
        slots: wishSlots(wish).map(slot => ({ id: slot.id, hero: slot.hero, status: slot.status, end: slot.end })),
        available: state.wish.available,
        enabled: state.wish.enabled,
        reason: state.wish.reason,
      };
    } catch (cause) {
      out.wish = { error: msgOf(cause) };
    }
    try {
      const ctx = mapContext();
      const camps = {};
      const tags = {};
      const bosses = [];
      const matchers = bossMatchers(ctx.cfg);
      let total = 0;
      ctx.units[ctx.cfg.each](function (_index, unit) {
        total += 1;
        let info = null;
        try { info = unitInfo(unit, ctx, matchers); } catch (_) { info = null; }
        const camp = String(unit[ctx.cfg.unit.camp]);
        const tag = String(unit[ctx.cfg.unit.tag]);
        camps[camp] = (camps[camp] || 0) + 1;
        tags[camp + ':' + tag] = (tags[camp + ':' + tag] || 0) + 1;
        if (info && info.kind === 'boss' && bosses.length < 8) bosses.push(info.name);
        return false;
      });
      out.map = {
        sceneId: ctx.state[ctx.cfg.sceneId],
        sceneType: ctx.state[ctx.cfg.sceneType || 'ISAV'],
        units: total,
        monsters: Object.keys(state.map.seen).length,
        camps,
        tags,
        bosses,
        kills: state.map.kills,
        bossGold: state.map.bossGold,
        gold: state.map.goldLast,
        mobSeconds: state.mob.seconds,
        bossSeconds: state.boss.seconds,
      };
    } catch (cause) {
      out.map = { error: msgOf(cause) };
    }
    try {
      const cfg = combatConfig();
      const manager = combatManager(cfg);
      const monsters = manager[cfg.config.table](cfg.config.monsterTable);
      const skills = manager[cfg.config.table](cfg.config.skillTable);
      let visionRows = 0;
      let cooldownRows = 0;
      if (Array.isArray(monsters)) monsters.forEach(row => {
        if (row && (row[cfg.config.vision] === 350 || row[cfg.config.vision] === 500)) visionRows += 1;
      });
      if (Array.isArray(skills)) skills.forEach(row => {
        if (!row || !Number.isSafeInteger(row[cfg.config.skill])) return;
        const cd = row[cfg.config.cooldown];
        const ms = cd && cd[cfg.config.ms];
        if (typeof ms === 'number' && ms >= 40000 && ms <= 60000) cooldownRows += 1;
      });
      let damage = '';
      try {
        damage = damageContext(cfg).heroes().length + ' 个我方英雄';
      } catch (cause) {
        damage = msgOf(cause);
      }
      out.combat = {
        visionRows,
        cooldownRows,
        damage,
        enabled: {
          damage: state.combat.damage.enabled,
          vision: state.combat.vision.enabled,
          cooldown: state.combat.cooldown.enabled,
        },
      };
    } catch (cause) {
      out.combat = { error: msgOf(cause) };
    }
    try {
      const cfg = reviveConfig();
      const bag = gameBag(cfg);
      const yx = bag[cfg.heroService];
      out.revive = {
        panel: cfg.panel,
        panelFound: !!findDefeatPanel(cfg),
        reviveMethod: !!(yx && typeof yx[cfg.revive] === 'function'),
        abandonMethod: true,
      };
    } catch (cause) {
      out.revive = { error: msgOf(cause) };
    }
    try {
      const cfg = heroReviveConfig();
      const view = heroReviveContext(cfg);
      let heroes = 0;
      let dead = 0;
      view.ctx.units[view.ctx.cfg.each](function (_index, unit) {
        if (!unit) return false;
        if (typeof unit[cfg.heroFlag] !== 'function' || unit[cfg.heroFlag]() !== true) return false;
        heroes += 1;
        if (typeof unit[cfg.deadFlag] === 'function' && unit[cfg.deadFlag]() === true) dead += 1;
        return false;
      });
      out.heroRevive = { heroes, dead, reviveMethod: true };
    } catch (cause) {
      out.heroRevive = { error: msgOf(cause) };
    }
    return out;
  }

  // ---------- 秘境探宝（BuildPlank）自动跳 ----------
  const plankJump = {
    running: false, done: false, stop: false, view: null,
    jumps: 0, power: 0, last: '', error: '', failType: null,
    timer: 0, maxJumps: 500, empty: 0, probe: [],
  };

  function plankFindView() {
    try {
      const cc = window.cc;
      if (!cc || !cc.director || !cc.director.getScene) return null;
      const scene = cc.director.getScene();
      if (!scene) return null;
      const stack = [scene];
      while (stack.length) {
        const node = stack.pop();
        if (!node) continue;
        const comps = node.components || node._components || [];
        for (let i = 0; i < comps.length; i += 1) {
          const c = comps[i];
          if (!c) continue;
          if (typeof c.HFSI === 'function' && 'BHQS' in c && 'TLZS' in c && c.NERB) {
            if (node.activeInHierarchy) return c;
          }
        }
        const children = node.children || [];
        for (let i = 0; i < children.length; i += 1) stack.push(children[i]);
      }
      return null;
    } catch (_) { return null; }
  }

  function plankFailType(view) {
    try {
      if (!view || !view.TLZS || typeof view.TLZS.VXKA !== 'function') return null;
      const cc = window.cc;
      if (!cc || !cc.Vec3) return null;
      const far = new cc.Vec3(1e9, 1e9, 0);
      const r = view.TLZS.VXKA(far);
      return r && r.resultType !== undefined ? r.resultType : null;
    } catch (_) { return null; }
  }

  function plankChoose(view) {
    try {
      if (!view || !view.BHQS || !view.TLZS) return 0;
      const items = view.BHQS.UCLH || [];
      const max = Math.min(items.length, Number(view.BHQS.DTAG) || items.length);
      let bestPower = 0;
      let bestScore = -Infinity;
      const probe = [];
      for (let i = 1; i <= max; i += 1) {
        const item = items[i - 1];
        if (!item || !item.NSBI) continue;
        const pos = item.NSBI.worldPosition;
        if (!pos) continue;
        const r = view.TLZS.VXKA(pos);
        if (!r || r.beyond === -1 || r.resultType === undefined) {
          probe.push({ power: i, type: r && r.resultType, beyond: r && r.beyond });
          continue;
        }
        probe.push({ power: i, type: r.resultType, beyond: r.beyond });
        if (plankJump.failType !== null && r.resultType === plankJump.failType) continue;
        let score = 0;
        try {
          if (r.resultItem && typeof r.resultItem.MGTV === 'function') {
            const center = r.resultItem.MGTV();
            const dx = pos.x - center.x;
            const dy = pos.y - center.y;
            score = -(dx * dx + dy * dy);
          }
        } catch (_) { score = 0; }
        if (score > bestScore) { bestScore = score; bestPower = i; }
      }
      plankJump.probe = probe;
      return bestPower;
    } catch (_) { return 0; }
  }

  function plankStop(reason) {
    plankJump.running = false;
    plankJump.stop = false;
    plankJump.done = true;
    if (reason) plankJump.last = String(reason);
    if (plankJump.timer) { clearTimeout(plankJump.timer); plankJump.timer = 0; }
  }

  function plankFail(reason) {
    plankJump.error = String(reason || '自动跳失败');
    plankJump.last = plankJump.error;
    plankStop(plankJump.last);
  }

  function plankTick() {
    plankJump.timer = 0;
    if (!plankJump.running) return;
    if (plankJump.stop) return plankStop('已停止');
    let view = plankJump.view;
    if (!view || !view.isValid) { view = plankFindView(); plankJump.view = view; }
    if (!view) return plankFail('请先进入秘境探宝');
    if (view.VJMK) return plankStop('本局结束');
    if (!view.BHQS || !view.TLZS) {
      plankJump.timer = setTimeout(plankTick, 150);
      return;
    }
    if (view.SKBB) {
      plankJump.timer = setTimeout(plankTick, 100);
      return;
    }
    if (plankJump.jumps >= plankJump.maxJumps) return plankStop('达到最大跳数');
    if (plankJump.failType === null) {
      plankJump.failType = plankFailType(view);
      if (plankJump.failType === null) {
        plankJump.timer = setTimeout(plankTick, 150);
        return;
      }
    }
    if (view.TLZS.BVWD) {
      plankJump.timer = setTimeout(plankTick, 100);
      return;
    }
    if (!view.BHQS.UCLH || !view.BHQS.UCLH.length) {
      plankJump.timer = setTimeout(plankTick, 120);
      return;
    }
    const power = plankChoose(view);
    if (!power) {
      plankJump.empty += 1;
      if (plankJump.empty < 60) {
        plankJump.timer = setTimeout(plankTick, 150);
        return;
      }
      return plankFail('找不到能落地的力度');
    }
    plankJump.empty = 0;
    plankJump.power = power;
    plankJump.jumps += 1;
    plankJump.last = '第 ' + plankJump.jumps + ' 跳（力度 ' + power + '）';
    const before = view.TLZS;
    const startedAt = now();
    try {
      view.MFZL = power;
      view.SKBB = false;
      view.HFSI();
    } catch (cause) {
      plankFail('起跳失败：' + msgOf(cause));
      return;
    }
    (function afterJump() {
      if (!plankJump.running) return;
      if (plankJump.stop) return plankStop('已停止');
      if (view.VJMK) return plankStop('本局结束');
      const elapsed = now() - startedAt;
      if (elapsed < 300) { plankJump.timer = setTimeout(afterJump, 80); return; }
      if (view.SKBB && elapsed < 6000) { plankJump.timer = setTimeout(afterJump, 80); return; }
      if (view.TLZS === before && elapsed < 6000) { plankJump.timer = setTimeout(afterJump, 100); return; }
      if (view.TLZS === before) return plankFail('跳跃没有完成');
      plankJump.timer = setTimeout(plankTick, 180);
    })();
  }

  function plankStart(opts) {
    if (plankJump.running) return { ok: true, message: '已经在自动跳' };
    const view = plankFindView();
    if (!view || !view.BHQS || !view.TLZS) {
      return { ok: false, message: '请先进入秘境探宝，并等界面加载完' };
    }
    if (view.VJMK) {
      return { ok: false, message: '本局已结束，请重新进入秘境探宝' };
    }
    plankJump.view = view;
    plankJump.running = true;
    plankJump.stop = false;
    plankJump.done = false;
    plankJump.error = '';
    plankJump.jumps = 0;
    plankJump.power = 0;
    plankJump.last = '';
    plankJump.failType = null;
    plankJump.empty = 0;
    plankJump.probe = [];
    plankJump.maxJumps = Math.max(1, Number(opts && opts.maxJumps) || 500);
    plankTick();
    return { ok: true, message: '开始自动跳' };
  }

  function plankState() {
    return {
      running: plankJump.running,
      done: plankJump.done,
      jumps: plankJump.jumps,
      power: plankJump.power,
      last: plankJump.last,
      error: plankJump.error,
      probe: plankJump.probe || [],
    };
  }

  // ---------- 农场（收菜 / 种菜 / 偷菜 / 施肥 / 照料） ----------
  const farmAuto = {
    running: false, mode: '', stop: false, timer: 0,
    steps: 0, last: '', action: '', error: '', busyUntil: 0,
    noVisitTries: 0, noActionTries: 0, noActionStreak: 0, dryRun: false,
    farmKey: '', didSteal: false, didHelp: false,
    visited: Object.create(null), pendingKey: '', nextMisses: 0,
    stealCount: 0, helpCount: 0, nextCount: 0, openedFarm: false,
    seedMissing: false, seedMissingAt: 0, leaveTries: 0, leaveStart: 0,
    friendName: '', keyAt: 0, keyAtKey: '', enterAt: 0, stopTimers: [],
    service: null, serviceAt: 0,
    counters: { steal: 0, fertilize: 0, water: 0, pest: 0, known: false },
    phase: '', ownMature: 0, emptyPlots: 0, nextOwnAt: 0,
    lastPlantAt: 0, visitDeadline: 0, visitCooldown: 0, leaveUntil: 0,
  };

  function farmFindService(force) {
    const nowAt = Date.now();
    if (!force && farmAuto.service && farmAuto.service.HBJS
      && typeof farmAuto.service.DNRQ === 'function'
      && nowAt - farmAuto.serviceAt < 10000) {
      return farmAuto.service;
    }
    try {
      for (const pair of window.System.entries()) {
        const exp = pair && pair[1];
        if (!exp) continue;
        let keys;
        try { keys = Object.getOwnPropertyNames(exp); } catch (_) { continue; }
        for (let i = 0; i < keys.length; i += 1) {
          let value;
          try { value = exp[keys[i]]; } catch (_) { continue; }
          if (value && typeof value === 'object'
            && value.MUHI && typeof value.MUHI === 'object'
            && typeof value.MUHI.DNRQ === 'function'
            && typeof value.MUHI.DVVD === 'function') {
            farmAuto.service = value.MUHI;
            farmAuto.serviceAt = nowAt;
            return value.MUHI;
          }
        }
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  function farmFindNode(name) {
    try {
      const scene = cc.director.getScene();
      if (!scene) return null;
      const stack = [scene];
      while (stack.length) {
        const node = stack.pop();
        if (!node) continue;
        if (String(node.name || '') === name && node.activeInHierarchy) return node;
        const children = node.children || [];
        for (let i = 0; i < children.length; i += 1) stack.push(children[i]);
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  function farmNodeText(name) {
    const node = farmFindNode(name);
    if (!node) return '';
    let text = '';
    (function walk(n) {
      if (!n || text) return;
      (n.components || []).forEach(function (c) {
        try {
          if (!text && c instanceof cc.Label && c.string) text = String(c.string);
        } catch (_) { /* ignore */ }
      });
      (n.children || []).forEach(walk);
    })(node);
    return text;
  }

  function farmClickNode(node) {
    if (!node) return false;
    try {
      let comp = null;
      const comps = node.components || node._components || [];
      for (let i = 0; i < comps.length; i += 1) {
        let cls = '';
        try { cls = cc.js.getClassName(comps[i]); } catch (_) { /* ignore */ }
        if (cls === 'BPNN' || cls === 'cc.Button') { comp = comps[i]; break; }
      }
      if (farmAuto.dryRun) return true;
      if (comp) node.emit('click', comp);
      else node.emit('click');
      return true;
    } catch (_) {
      return false;
    }
  }

  function farmClick(name) {
    return farmClickNode(farmFindNode(name));
  }

  function farmCall(fn) {
    try {
      const ret = fn();
      if (ret && typeof ret.catch === 'function') ret.catch(function () { /* ignore */ });
      return ret;
    } catch (_) {
      return null;
    }
  }

  function farmFriendEntries() {
    const list = [];
    try {
      const scene = cc.director.getScene();
      if (!scene) return list;
      const items = [];
      (function walk(n) {
        if (!n) return;
        if (String(n.name || '') === 'FarmVisitItemView' && n.activeInHierarchy) items.push(n);
        (n.children || []).forEach(walk);
      })(scene);
      items.forEach(function (item) {
        let steal = false;
        let help = false;
        let button = null;
        let name = '';
        (function walk(n) {
          const nodeName = String(n.name || '');
          if (!button && nodeName === 'CommonBlueButton' && n.activeInHierarchy) button = n;
          (n.components || []).forEach(function (c) {
            try {
              if (c instanceof cc.Label && c.string) {
                const text = String(c.string);
                if (nodeName === 'UserName') name = text;
                if (n.activeInHierarchy) {
                  if (text.indexOf('可偷') >= 0) steal = true;
                  if (text.indexOf('可帮') >= 0) help = true;
                }
              }
            } catch (_) { /* ignore */ }
          });
          (n.children || []).forEach(walk);
        })(item);
        if (steal || help) list.push({ name: name, steal: steal, help: help, button: button });
      });
    } catch (_) { /* ignore */ }
    return list;
  }

  function farmEnterGrace() {
    const at = farmAuto.enterAt || 0;
    return at > 0 && Date.now() - at < 6000;
  }

  function farmClickMarkedFriend() {
    const entries = farmFriendEntries();
    if (!entries.length) return false;
    let pick = null;
    for (let i = 0; i < entries.length; i += 1) {
      if (entries[i].steal) { pick = entries[i]; break; }
    }
    if (!pick) pick = entries[0];
    if (!pick.button) return false;
    const ok = farmClickNode(pick.button);
    if (ok) {
      farmAuto.friendName = pick.name || '';
      farmAuto.enterAt = Date.now();
    }
    return ok;
  }

  function farmClickFirstFriend() {
    try {
      const scene = cc.director.getScene();
      if (!scene) return false;

      const clickVisitItem = function () {
        let button = null;
        (function walk(n) {
          if (!n || button) return;
          if (String(n.name || '') === 'FarmVisitItemView' && n.activeInHierarchy) {
            (function findButton(k) {
              if (!k || button) return;
              if (String(k.name || '') === 'CommonBlueButton' && k.activeInHierarchy) {
                const comps = k.components || k._components || [];
                for (let i = 0; i < comps.length; i += 1) {
                  let cls = '';
                  try { cls = cc.js.getClassName(comps[i]); } catch (_) { /* ignore */ }
                  if (cls === 'BPNN' || cls === 'cc.Button') {
                    button = k;
                    return;
                  }
                }
              }
              (k.children || []).forEach(findButton);
            })(n);
          }
          (n.children || []).forEach(walk);
        })(scene);
        return button ? farmClickNode(button) : false;
      };

      let hit = null;
      (function walk(n) {
        if (!n || hit) return;
        (n.components || []).forEach(function (c) {
          try {
            if (c instanceof cc.Label && String(c.string || '').trim() === '拜访') hit = n;
          } catch (_) { /* ignore */ }
        });
        (n.children || []).forEach(walk);
      })(scene);
      if (!hit) return clickVisitItem();

      let node = hit;
      for (let depth = 0; depth < 5 && node; depth += 1, node = node.parent) {
        const comps = node.components || node._components || [];
        for (let i = 0; i < comps.length; i += 1) {
          let cls = '';
          try { cls = cc.js.getClassName(comps[i]); } catch (_) { /* ignore */ }
          if ((cls === 'BPNN' || cls === 'cc.Button') && node.activeInHierarchy) {
            if (farmAuto.dryRun) return true;
            node.emit('click', comps[i]);
            return true;
          }
        }
      }
      return clickVisitItem();
    } catch (_) { /* ignore */ }
    return false;
  }

  function farmButtons() {
    const steal = farmFindNode('oneKeyStealBtn');
    const help = farmFindNode('FarmOperationButton');
    const next = farmFindNode('GoVisitNextoneBtn');
    return {
      steal: !!(steal && steal.activeInHierarchy),
      help: !!(help && help.activeInHierarchy),
      next: !!(next && next.activeInHierarchy),
      stealText: steal ? farmNodeText('oneKeyStealBtn') : '',
      helpText: help ? farmNodeText('FarmOperationButton') : '',
    };
  }

  function farmCounters(svc) {
    try {
      const znzx = svc && svc.HBJS && typeof svc.HBJS.ZNZX === 'function'
        ? svc.HBJS.ZNZX() : null;
      if (!znzx || typeof znzx !== 'object') {
        return { steal: 0, fertilize: 0, water: 0, pest: 0, known: false };
      }
      const known = Object.getOwnPropertyNames(znzx).some(function (k) {
        return ['MVSM', 'DRIF', 'XHNI', 'AIAD'].indexOf(k) >= 0;
      });
      return {
        steal: Number(znzx.MVSM || 0) || 0,
        fertilize: Number(znzx.DRIF || 0) || 0,
        water: Number(znzx.XHNI || 0) || 0,
        pest: Number(znzx.AIAD || 0) || 0,
        known: known,
      };
    } catch (_) {
      return { steal: 0, fertilize: 0, water: 0, pest: 0, known: false };
    }
  }

  function farmQuotaLeft(counters) {
    if (!counters || !counters.known) return true;
    return counters.steal > 0 || counters.fertilize > 0
      || counters.water > 0 || counters.pest > 0;
  }

  function farmVisitUiOpen() {
    return !!(farmFindNode('FarmVisitMenuView') || farmFindNode('BackHomeMenuIconView'));
  }

  function farmAtHome(svc) {
    try {
      if (!svc) return false;
      if (svc.OUDX && svc.OUDX()) return false;
      const model = svc.HBJS || svc.IYJF;
      if (model && model.BZUO) return false;
      if (farmVisitUiOpen()) return false;
      return true;
    } catch (_) {
      return false;
    }
  }

  function farmProbe() {
    const svc = farmFindService();
    const atHome = farmAtHome(svc);
    const status = atHome ? farmOwnStatus(svc) : { mature: 0, empty: 0, crops: 0 };
    return {
      ok: !!svc,
      visiting: !!(svc && svc.OUDX && svc.OUDX()),
      farmKey: svc && svc.HBJS ? String(svc.HBJS.BZUO || '') : '',
      nextMatureAt: svc && atHome ? farmNextMatureAt(svc) : 0,
      ownMature: status.mature,
      ownEmpty: status.empty,
      ownCrops: status.crops,
      counters: svc ? farmCounters(svc) : null,
      buttons: farmButtons(),
      state: farmState(),
    };
  }

  function farmState() {
    return {
      running: farmAuto.running,
      mode: farmAuto.mode,
      phase: farmAuto.phase,
      steps: farmAuto.steps,
      action: farmAuto.action,
      last: farmAuto.last,
      error: farmAuto.error,
      farmKey: farmAuto.farmKey,
      didSteal: farmAuto.didSteal,
      didHelp: farmAuto.didHelp,
      noActionStreak: farmAuto.noActionStreak,
      counters: farmAuto.counters,
      stealCount: farmAuto.stealCount || 0,
      helpCount: farmAuto.helpCount || 0,
      nextCount: farmAuto.nextCount || 0,
      leaveReason: farmAuto.leaveReason || '',
      seedMissing: !!farmAuto.seedMissing,
      ownMature: farmAuto.ownMature,
      emptyPlots: farmAuto.emptyPlots,
      nextOwnAt: farmAuto.nextOwnAt,
    };
  }

  function farmNextMatureAt(svc) {
    let soonest = 0;
    try {
      const model = svc && svc.HBJS ? svc.HBJS : null;
      if (!model) return 0;
      const nowSec = Math.floor(Date.now() / 1000);
      (model.QCMZ || []).forEach(function (crop) {
        if (!crop || !crop.IYHA || !crop.IYHA.UATU) return;
        try {
          if (svc.OLYX && svc.OLYX(crop.HTGF)) return;
        } catch (_) { /* ignore */ }
        const at = Number(crop.IYHA.LAUV || crop.IYHA.RMEQ || 0);
        if (at > nowSec && (!soonest || at < soonest)) soonest = at;
      });
    } catch (_) { /* ignore */ }
    return soonest;
  }

  function farmOwnStatus(svc) {
    const status = { mature: 0, empty: 0, crops: 0 };
    try {
      const model = svc && svc.HBJS ? svc.HBJS : null;
      if (!model) return status;
      (model.QCMZ || []).forEach(function (plot) {
        if (!plot || !plot.IYHA || !plot.IYHA.UATU) return;
        status.crops += 1;
        try {
          if (svc.OLYX && svc.OLYX(plot.HTGF)) status.mature += 1;
        } catch (_) { /* ignore */ }
      });
      try {
        status.empty = svc.QZBQ ? (svc.QZBQ() || []).length : 0;
      } catch (_) {
        status.empty = 0;
      }
    } catch (_) { /* ignore */ }
    return status;
  }

  function farmPlant(svc) {
    farmAuto.lastPlantAt = Date.now();
    let planted = false;
    try {
      const ret = svc.OPKK ? svc.OPKK() : null;
      if (ret && typeof ret.then === 'function') {
        ret.then(function (res) {
          planted = !!res;
          if (!res) {
            farmAuto.seedMissing = true;
            farmAuto.seedMissingAt = Date.now();
            farmAuto.last = '没有可用的种子，暂时不补种';
          } else {
            farmAuto.seedMissing = false;
            farmAuto.seedMissingAt = 0;
          }
        }).catch(function () { /* ignore */ });
      } else {
        planted = !!ret;
        if (!planted) {
          farmAuto.seedMissing = true;
          farmAuto.seedMissingAt = Date.now();
          farmAuto.last = '没有可用的种子，暂时不补种';
        }
      }
    } catch (_) { /* ignore */ }
    return planted;
  }

  function farmStop(reason) {
    farmAuto.running = false;
    farmAuto.stop = false;
    if (farmAuto.timer) {
      clearTimeout(farmAuto.timer);
      farmAuto.timer = 0;
    }
    if (reason) farmAuto.last = String(reason);
  }

  function farmFail(reason) {
    farmAuto.error = String(reason || '农场操作失败');
    farmAuto.last = farmAuto.error;
    farmStop(farmAuto.error);
  }

  function farmVisitDeadline() {
    const now = Date.now();
    let deadline = now + 10 * 60 * 1000;
    const nextAt = farmAuto.nextOwnAt;
    if (nextAt) {
      const matureMs = nextAt * 1000 - 20000;
      deadline = matureMs > now + 60000 ? Math.min(deadline, matureMs) : now + 60000;
    }
    return deadline;
  }

  function farmFmtLeft(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const r = total % 60;
    return (h > 0 ? h + ':' : '') + (m < 10 ? '0' + m : m) + ':' + (r < 10 ? '0' + r : r);
  }

  function farmWaitOwn() {
    const now = Date.now();
    let waitMs = 5 * 60 * 1000;
    farmAuto.last = '等待自己的作物成熟';
    if (farmAuto.nextOwnAt) {
      const seconds = Math.max(0, farmAuto.nextOwnAt - Math.floor(now / 1000));
      farmAuto.last = seconds > 0
        ? '等待自己的菜成熟（还有 ' + farmFmtLeft(seconds) + '）'
        : '自己的菜应该熟了，检查中';
      if (seconds <= 1) {
        waitMs = 3000;
      } else if (seconds <= 60) {
        // 快到点了：精确在成熟时刻醒来，收完立刻补种
        waitMs = Math.max(2000, (seconds + 1) * 1000);
      } else {
        // 平时每 5 分钟复查一次，但提前 30 秒醒
        waitMs = Math.max(10000, Math.min(5 * 60 * 1000, (seconds - 30) * 1000));
      }
    }
    if (farmAuto.visitCooldown && farmAuto.visitCooldown > now) {
      waitMs = Math.min(waitMs, Math.max(5000, farmAuto.visitCooldown - now));
      farmAuto.last = '暂时没有可偷的，等会儿再试';
    }
    farmAuto.busyUntil = now + waitMs;
    farmAuto.timer = setTimeout(farmTick, waitMs);
  }

  function farmGoHome(reason) {
    farmAuto.phase = 'leave';
    farmAuto.action = '回个人农场';
    farmAuto.last = reason || '回个人农场看看自己的菜';
    farmAuto.leaveReason = String(reason || '');
    farmAuto.leaveStart = Date.now();
    farmAuto.leaveUntil = 0;
    farmAuto.leaveTries = 0;
    farmAuto.busyUntil = Date.now() + 300;
    farmAuto.timer = setTimeout(farmTick, 300);
  }

  function farmTickOwn(svc, allowAuto) {
    if (svc.OUDX && svc.OUDX()) return farmFail('当前在好友农场，请先回到个人农场');
    if (!farmAtHome(svc)) return farmGoHome('先返回个人农场');
    const status = farmOwnStatus(svc);
    farmAuto.ownMature = status.mature;
    farmAuto.emptyPlots = status.empty;
    if (status.mature > 0) {
      farmAuto.action = '收自己的菜';
      farmAuto.last = '收自己的菜（' + status.mature + ' 块成熟）';
      farmAuto.steps += 1;
      farmCall(function () { return svc.DNRQ(); });
      farmAuto.busyUntil = Date.now() + 4000;
      farmAuto.timer = setTimeout(farmTick, 4000);
      return;
    }
    const seedWait = farmAuto.seedMissing && Date.now() - farmAuto.seedMissingAt < 2 * 60 * 1000;
    if (status.empty > 0 && !seedWait && Date.now() - farmAuto.lastPlantAt >= 20000) {
      farmAuto.action = '种菜';
      farmAuto.last = '自动种菜（' + status.empty + ' 块空地）';
      farmAuto.steps += 1;
      farmPlant(svc);
      farmAuto.busyUntil = Date.now() + 4000;
      farmAuto.timer = setTimeout(farmTick, 4000);
      return;
    }
    farmAuto.nextOwnAt = farmNextMatureAt(svc);
    if (allowAuto) return farmTickAutoHome(svc, true);
    farmWaitOwn();
  }

  function farmTickAutoHome(svc, anyQuota) {
    farmAuto.phase = 'home';
    const status = farmOwnStatus(svc);
    farmAuto.ownMature = status.mature;
    farmAuto.emptyPlots = status.empty;
    farmAuto.nextOwnAt = farmNextMatureAt(svc);

    if (status.mature > 0) {
      farmAuto.action = '收自己的菜';
      farmAuto.last = '收自己的菜（' + status.mature + ' 块成熟）';
      farmAuto.steps += 1;
      farmCall(function () { return svc.DNRQ(); });
      farmAuto.busyUntil = Date.now() + 4000;
      farmAuto.timer = setTimeout(farmTick, 4000);
      return;
    }
    const seedWait = farmAuto.seedMissing && Date.now() - farmAuto.seedMissingAt < 2 * 60 * 1000;
    if (status.empty > 0 && !seedWait && Date.now() - farmAuto.lastPlantAt >= 20000) {
      farmAuto.action = '种菜';
      farmAuto.last = '自动种菜（' + status.empty + ' 块空地）';
      farmAuto.steps += 1;
      farmPlant(svc);
      farmAuto.busyUntil = Date.now() + 4000;
      farmAuto.timer = setTimeout(farmTick, 4000);
      return;
    }
    if (anyQuota) {
      if (farmAuto.visitCooldown && Date.now() < farmAuto.visitCooldown) {
        farmWaitOwn();
        return;
      }
      farmAuto.phase = 'enter';
      farmAuto.noVisitTries = 0;
      farmAuto.visitDeadline = farmVisitDeadline();
      farmAuto.didSteal = false;
      farmAuto.didHelp = false;
      farmAuto.noActionStreak = 0;
      farmAuto.farmKey = '';
      farmAuto.visited = Object.create(null);
      farmAuto.pendingKey = '';
      farmAuto.nextMisses = 0;
      farmAuto.openedFarm = false;
      farmAuto.action = '去偷菜/施肥';
      farmAuto.last = '自己的菜还没熟，去好友农场偷菜/施肥…';
      farmAuto.busyUntil = Date.now() + 200;
      farmAuto.timer = setTimeout(farmTick, 200);
      return;
    }
    farmWaitOwn();
  }

  function farmTickAutoEnter(svc) {
    farmAuto.action = '进入好友农场';
    if (farmEnterGrace()) {
      farmAuto.last = '正在进入好友农场…';
      farmAuto.busyUntil = Date.now() + 1200;
      farmAuto.timer = setTimeout(farmTick, 1200);
      return;
    }
    if (farmClickMarkedFriend()) {
      farmAuto.noVisitTries = 0;
      farmAuto.last = '拜访有偷菜/帮忙标识的好友'
        + (farmAuto.friendName ? '：' + farmAuto.friendName : '') + '…';
      farmAuto.busyUntil = Date.now() + 3000;
      farmAuto.timer = setTimeout(farmTick, 3000);
      return;
    }
    if (!farmAuto.openedFarm && svc && typeof svc.LXAY === 'function') {
      farmAuto.openedFarm = true;
      farmAuto.last = '打开农场界面…';
      try { svc.LXAY(); } catch (_) { /* ignore */ }
      farmAuto.busyUntil = Date.now() + 2500;
      farmAuto.timer = setTimeout(farmTick, 2500);
      return;
    }
    if (farmAuto.noVisitTries < 2) {
      farmAuto.noVisitTries += 1;
      const tab = farmFindNode('VisitTab');
      if (tab) farmClickNode(tab);
      farmAuto.last = '打开「拜访好友」列表…';
      farmAuto.busyUntil = Date.now() + 2000;
      farmAuto.timer = setTimeout(farmTick, 2000);
      return;
    }
    // 列表里没有任何「可偷取/可帮忙」标识：不去空农场，等一会儿再看
    farmAuto.noVisitTries = 0;
    farmAuto.phase = '';
    farmAuto.visitCooldown = Date.now() + 3 * 60 * 1000;
    farmAuto.last = '好友暂时没有可偷/可帮忙的，3 分钟后再看';
    farmWaitOwn();
    farmAuto.last = '好友暂时没有可偷/可帮忙的，等会儿再看';
  }

  function farmTickVisit(svc, counters, allowAuto) {
    const key = String(svc.HBJS && svc.HBJS.BZUO ? svc.HBJS.BZUO : '');
    farmAuto.visited = farmAuto.visited || Object.create(null);
    if (key && key !== farmAuto.farmKey) {
      farmAuto.farmKey = key;
      farmAuto.didSteal = false;
      farmAuto.didHelp = false;
      farmAuto.pendingKey = '';
      farmAuto.nextMisses = 0;
      if (farmAuto.visited[key]) {
        farmAuto.visitCooldown = Date.now() + 5 * 60 * 1000;
        if (allowAuto) return farmGoHome('已经拜访完一轮好友');
        return farmStop('已经拜访完一轮好友');
      }
      farmAuto.visited[key] = 1;
    }
    if (key && key !== farmAuto.keyAtKey) {
      farmAuto.keyAtKey = key;
      farmAuto.keyAt = Date.now();
    }
    const stealQuota = !counters.known || counters.steal > 0;
    const helpQuota = !counters.known || counters.fertilize > 0
      || counters.water > 0 || counters.pest > 0;
    const buttons = farmButtons();

    if (stealQuota && buttons.steal && !farmAuto.didSteal) {
      farmAuto.action = '偷菜';
      farmAuto.last = buttons.stealText || '一键偷菜';
      farmClick('oneKeyStealBtn');
      farmAuto.didSteal = true;
      farmAuto.stealCount += 1;
      farmAuto.noActionStreak = 0;
      farmAuto.steps += 1;
      farmAuto.busyUntil = Date.now() + 2000;
      farmAuto.timer = setTimeout(farmTick, 2000);
      return;
    }
    if (helpQuota && buttons.help && !farmAuto.didHelp) {
      farmAuto.action = '施肥/照料';
      farmAuto.last = buttons.helpText || '一键施肥';
      farmClick('FarmOperationButton');
      farmAuto.didHelp = true;
      farmAuto.helpCount += 1;
      farmAuto.noActionStreak = 0;
      farmAuto.steps += 1;
      farmAuto.busyUntil = Date.now() + 2000;
      farmAuto.timer = setTimeout(farmTick, 2000);
      return;
    }
    if (allowAuto && Date.now() >= farmAuto.visitDeadline) {
      return farmGoHome('回个人农场看看自己的菜');
    }
    // 刚进好友农场先等按钮加载，别急着跳过
    const settled = Date.now() - (farmAuto.keyAt || 0) > 3000;
    if (!settled && !farmAuto.didSteal && !farmAuto.didHelp) {
      farmAuto.last = '看看这位好友有没有可偷/可帮忙的…';
      farmAuto.busyUntil = Date.now() + 1200;
      farmAuto.timer = setTimeout(farmTick, 1200);
      return;
    }
    // 这位好友没有「偷菜/帮忙」按钮：不点空按钮，直接跳下一位
    if (!buttons.steal && !buttons.help && buttons.next && settled) {
      farmAuto.noActionStreak += 1;
      if (allowAuto) {
        // 这位好友没有标识：回个人农场重新看列表，只去有标识的好友
        return farmGoHome('这位好友没有可偷/可帮忙的，回去找有标识的');
      }
      if (farmAuto.noActionStreak >= 3) {
        return farmStop('连续 3 位好友没有可偷/可照料作物，可能今日次数或道具已用完');
      }
      farmAuto.action = '跳过空农场';
      farmAuto.last = '这位好友没有可偷/可帮忙的，看下一位';
      if (farmClick('GoVisitNextoneBtn')) farmAuto.enterAt = Date.now();
      farmAuto.pendingKey = key;
      farmAuto.nextCount += 1;
      farmAuto.busyUntil = Date.now() + 1200;
      farmAuto.timer = setTimeout(farmTick, 1200);
      return;
    }
    if (buttons.next) {
      if (farmAuto.didSteal || farmAuto.didHelp) {
        farmAuto.noActionStreak = 0;
      } else {
        farmAuto.noActionStreak += 1;
        if (farmAuto.noActionStreak >= 5) {
          farmAuto.visitCooldown = Date.now() + 5 * 60 * 1000;
          if (allowAuto) return farmGoHome('连续 5 位好友没有可偷/可照料作物');
          return farmStop('连续 5 位好友没有可偷/可照料作物，可能今日次数或道具已用完');
        }
      }
      if (farmAuto.pendingKey) {
        if (key === farmAuto.pendingKey) {
          farmAuto.nextMisses += 1;
          if (farmAuto.nextMisses >= 3) {
            farmAuto.visitCooldown = Date.now() + 5 * 60 * 1000;
            if (allowAuto) return farmGoHome('快速拜访没有切换好友，先回个人农场');
            return farmStop('快速拜访没有切换好友，请检查游戏是否卡住或已到拜访上限');
          }
        } else {
          farmAuto.nextMisses = 0;
        }
      }
      farmAuto.action = '下一位好友';
      farmAuto.last = '快速拜访下一位好友…';
      if (farmClick('GoVisitNextoneBtn')) farmAuto.enterAt = Date.now();
      farmAuto.pendingKey = key;
      farmAuto.nextCount += 1;
      farmAuto.steps += 1;
      farmAuto.noActionTries = 0;
      farmAuto.busyUntil = Date.now() + 2500;
      farmAuto.timer = setTimeout(farmTick, 2500);
      return;
    }
    farmAuto.noActionTries += 1;
    if (farmAuto.noActionTries >= 3) {
      if (allowAuto) {
        farmAuto.visitCooldown = Date.now() + 5 * 60 * 1000;
        return farmGoHome('没有可偷/可帮忙的作物');
      }
      return farmStop('没有可偷/可帮忙的作物了');
    }
    farmAuto.last = '等待农场状态…';
    farmAuto.busyUntil = Date.now() + 1000;
    farmAuto.timer = setTimeout(farmTick, 1000);
  }

  function farmTick() {
    farmAuto.timer = 0;
    if (!farmAuto.running) return;
    if (farmAuto.stop) return farmStop('已停止');
    if (farmAuto.mode === 'visit' && farmAuto.steps >= 200) {
      return farmStop('已达到本轮上限 200 次');
    }
    if (Date.now() < farmAuto.busyUntil) {
      farmAuto.timer = setTimeout(farmTick, 300);
      return;
    }
    const svc = farmFindService(true);
    if (!svc) return farmFail('农场数据还没加载，请先打开农场');
    farmAuto.counters = farmCounters(svc);
    const counters = farmAuto.counters;
    const anyQuota = farmQuotaLeft(counters);

    if (farmAuto.mode === 'own') return farmTickOwn(svc, false);

    if (farmAuto.phase === 'leave') {
      const now = Date.now();
      if (farmAtHome(farmFindService(true) || svc)) {
        farmAuto.phase = '';
        farmAuto.leaveStart = 0;
        farmAuto.leaveTries = 0;
        farmAuto.nextOwnAt = farmNextMatureAt(svc);
        farmAuto.last = '已回到个人农场';
        farmAuto.busyUntil = now + 1200;
        farmAuto.timer = setTimeout(farmTick, 1200);
        return;
      }
      if (!farmAuto.leaveStart) farmAuto.leaveStart = now;
      if (now - farmAuto.leaveStart > 90000) {
        return farmFail('回不到个人农场：请手动点游戏里的「回城」，或关闭游戏窗口重开');
      }
      const lsvc = farmFindService(true) || svc;
      const visiting = !!(lsvc.OUDX && lsvc.OUDX());
      if (!farmAuto.dryRun && now > farmAuto.leaveUntil) {
        if (visiting) {
          farmAuto.action = '回个人农场';
          farmCall(function () { return lsvc.RUUS(); });
          farmAuto.leaveUntil = now + 5000;
        } else if (farmVisitUiOpen()) {
          // 游戏已清掉拜访状态，但好友农场界面还开着：先重新进一位好友，再正常回城
          farmAuto.leaveTries = (farmAuto.leaveTries || 0) + 1;
          farmAuto.action = '恢复返回个人农场';
          if (farmAuto.leaveTries > 8) {
            return farmFail('好友农场界面卡住了：请手动点游戏里的「回城」，或关闭游戏窗口重开');
          }
          farmClick('GoVisitNextoneBtn');
          farmAuto.leaveUntil = now + 5000;
        } else {
          farmAuto.phase = '';
          farmAuto.leaveStart = 0;
          farmAuto.busyUntil = now + 500;
          farmAuto.timer = setTimeout(farmTick, 500);
          return;
        }
      }
      farmAuto.last = visiting ? '正在返回个人农场…' : '正在恢复并返回个人农场…';
      farmAuto.busyUntil = now + 1000;
      farmAuto.timer = setTimeout(farmTick, 1000);
      return;
    }

    if (!anyQuota) {
      if (farmAuto.mode === 'visit') return farmStop('今日偷菜/施肥/照料次数已用完');
      if (svc.OUDX && svc.OUDX()) return farmGoHome('今日次数已用完，回个人农场');
      if (!farmAtHome(svc)) return farmGoHome('先回个人农场');
      return farmTickAutoHome(svc, false);
    }

    if (farmAuto.mode === 'auto') {
      if (svc.OUDX && svc.OUDX()) {
        farmAuto.phase = 'visit';
        return farmTickVisit(svc, counters, true);
      }
      if (farmAuto.phase === 'enter') return farmTickAutoEnter(svc);
      if (!farmAtHome(svc)) {
        if (farmEnterGrace()) {
          farmAuto.last = '正在进入好友农场…';
          farmAuto.busyUntil = Date.now() + 1200;
          farmAuto.timer = setTimeout(farmTick, 1200);
          return;
        }
        return farmGoHome('先回个人农场，再继续自动循环');
      }
      return farmTickAutoHome(svc, true);
    }

    if (!svc.OUDX || !svc.OUDX()) {
      if (farmEnterGrace()) {
        farmAuto.last = '正在进入好友农场…';
        farmAuto.busyUntil = Date.now() + 1200;
        farmAuto.timer = setTimeout(farmTick, 1200);
        return;
      }
      if (farmClickMarkedFriend()) {
        farmAuto.noVisitTries = 0;
        farmAuto.action = '拜访好友';
        farmAuto.last = '拜访有偷菜/帮忙标识的好友'
          + (farmAuto.friendName ? '：' + farmAuto.friendName : '') + '…';
        farmAuto.busyUntil = Date.now() + 3000;
        farmAuto.timer = setTimeout(farmTick, 3000);
        return;
      }
      if (!farmAuto.openedFarm && typeof svc.LXAY === 'function') {
        farmAuto.openedFarm = true;
        farmAuto.action = '打开农场界面';
        farmAuto.last = '打开农场界面…';
        try { svc.LXAY(); } catch (_) { /* ignore */ }
        farmAuto.busyUntil = Date.now() + 2500;
        farmAuto.timer = setTimeout(farmTick, 2500);
        return;
      }
      farmAuto.noVisitTries += 1;
      if (farmAuto.noVisitTries >= 2) {
        farmAuto.noVisitTries = 0;
        farmAuto.action = '等待可偷/可帮忙的好友';
        farmAuto.last = '好友暂时没有可偷/可帮忙的，2 分钟后再看';
        farmAuto.busyUntil = Date.now() + 2 * 60 * 1000;
        farmAuto.timer = setTimeout(farmTick, 2 * 60 * 1000);
        return;
      }
      farmAuto.action = '进入好友农场';
      farmAuto.last = '正在打开「拜访好友」列表…';
      const tab = farmFindNode('VisitTab');
      if (tab) farmClickNode(tab);
      farmAuto.busyUntil = Date.now() + 2000;
      farmAuto.timer = setTimeout(farmTick, 2000);
      return;
    }
    farmAuto.noVisitTries = 0;
    return farmTickVisit(svc, counters, false);
  }

  function farmStart(opts) {
    if (farmAuto.running) return { ok: true, message: '农场自动已在运行' };
    farmClearStopTimers();
    const svc = farmFindService();
    if (!svc) return { ok: false, message: '请先打开农场界面' };
    let mode = 'visit';
    if (opts && opts.mode === 'own') mode = 'own';
    else if (opts && opts.mode === 'auto') mode = 'auto';
    const visiting = !!(svc.OUDX && svc.OUDX());
    if (mode === 'own' && visiting) {
      return { ok: false, message: '当前在好友农场，请先回到个人农场' };
    }
    const counters = farmCounters(svc);
    if (mode === 'visit' && !farmQuotaLeft(counters)) {
      farmAuto.counters = counters;
      return { ok: false, message: '今日偷菜/施肥/照料次数已用完' };
    }
    farmAuto.counters = counters;
    farmAuto.running = true;
    farmAuto.mode = mode;
    farmAuto.stop = false;
    farmAuto.steps = 0;
    const anyQuota = farmQuotaLeft(counters);
    farmAuto.last = mode === 'own' ? '开始收自己的菜/种菜'
      : (mode === 'auto'
        ? (anyQuota ? '开始自动循环：收菜优先，没熟就去偷菜' : '今日次数已用完：只监控自己的菜')
        : '开始偷菜/施肥');
    farmAuto.action = '';
    farmAuto.error = '';
    farmAuto.busyUntil = 0;
    farmAuto.noVisitTries = 0;
    farmAuto.noActionTries = 0;
    farmAuto.noActionStreak = 0;
    farmAuto.dryRun = !!(opts && opts.dryRun);
    farmAuto.farmKey = '';
    farmAuto.didSteal = false;
    farmAuto.didHelp = false;
    farmAuto.visited = Object.create(null);
    farmAuto.pendingKey = '';
    farmAuto.nextMisses = 0;
    farmAuto.stealCount = 0;
    farmAuto.helpCount = 0;
    farmAuto.nextCount = 0;
    farmAuto.openedFarm = false;
    farmAuto.leaveStart = 0;
    farmAuto.leaveTries = 0;
    farmAuto.seedMissing = false;
    farmAuto.seedMissingAt = 0;
    farmAuto.phase = visiting ? 'visit' : '';
    farmAuto.ownMature = 0;
    farmAuto.emptyPlots = 0;
    farmAuto.nextOwnAt = 0;
    farmAuto.lastPlantAt = 0;
    farmAuto.visitDeadline = 0;
    farmAuto.visitCooldown = 0;
    farmAuto.leaveUntil = 0;
    farmTick();
    return { ok: true, message: farmAuto.last };
  }

  function farmLeaveOnce(preferNext) {
    try {
      const svc = farmFindService(true);
      if (!svc) return false;
      if (svc.OUDX && svc.OUDX() && typeof svc.RUUS === 'function') {
        farmCall(function () { return svc.RUUS(); });
        return true;
      }
      if (preferNext && farmVisitUiOpen()) {
        // 拜访状态已被游戏清掉但好友农场界面还在：先重新进一位好友，回城按钮才会生效
        farmClick('GoVisitNextoneBtn');
        return true;
      }
    } catch (_) { /* ignore */ }
    return false;
  }

  function farmClearStopTimers() {
    (farmAuto.stopTimers || []).forEach(function (id) {
      try { clearTimeout(id); } catch (_) { /* ignore */ }
    });
    farmAuto.stopTimers = [];
  }

  function farmAutoStop() {
    farmClearStopTimers();
    farmAuto.stopTimers = [];
    farmLeaveOnce(true);
    [5000, 10000, 15000, 20000, 25000].forEach(function (delay) {
      farmAuto.stopTimers.push(setTimeout(function () {
        if (!farmVisitUiOpen()) return;
        const svc = farmFindService();
        if (svc && svc.OUDX && svc.OUDX()) farmLeaveOnce(false);
        else farmLeaveOnce(true);
      }, delay));
    });
    farmStop('已停止');
    return { ok: true };
  }

  window.__zzAssist = {
    version: VERSION,
    __stamp: STAMP,
    snapshot,
    heroes: heroList,
    monsters: monsterList,
    openDailyTasks,
    claimDailyTasks,
    dailyClaimsStart,
    dailyClaimsStop,
    dailyClaimsState,
    arenaStart,
    arenaStop,
    arenaState,
    rogueStart,
    rogueStop,
    rogueState,
    marketClaimFree,
    marketState,
    stopAll: stopAllFeatures,
    claimAllDailyTabs,
    claimDailyViaService,
    completeDailyWithCards,
    shareGame,
    reloadMapNow,
    clickTaskGo,
    pickCoord,
    clickTeleport,
    clickTeleportTrack,
    autoJumpStart: plankStart,
    autoJumpState: plankState,
    autoJumpStop: () => plankStop('已停止'),
    farmAutoStart: farmStart,
    farmAutoStop: farmAutoStop,
    farmAutoState: farmState,
    farmProbe: farmProbe,
    guildIntelStart,
    guildIntelState,
    guildTeamsStart,
    guildTeamsState,
    mazeQuery,
    set,
    diagnose,
    seedDaily: seedRedDay,
    // 外部（Python 通过 CDP）直接推进红包扫描：页面定时器被浏览器节流时靠这个兜底。
    // CDP 的 Runtime.evaluate 不走页面定时器，窗口最小化也照常执行。
    __redTick: () => {
      try { tickRed(); } catch (cause) { state.red.reason = '红包异常：' + msgOf(cause); }
      return state.red.enabled ? 1 : 0;
    },
    refresh: () => { state.nextProbeAt = 0; probe(); },
    // 只给自测用：直接塞一次「刚死亡」事件，下一轮扫描就按真实死亡走
    // 「等待 N 秒 → 重进地图」。不碰游戏数据，可以在任何场景里验证整条链路。
    // 自测用：算「确认」按钮的屏幕坐标（不点）
    __findConfirm: () => findConfirmPoint(),
    __fakeDeath: kind => {
      const info = { kind: kind === 'boss' ? 'boss' : 'mob', name: '自测假怪', dead: true };
      state.map.fakeDeaths.push(info);
      return 'fake_' + kind + '_' + now();
    },
  };

  try { loadRedDay(); } catch (_) { /* ignore */ }
  state.timer = setInterval(() => {
    try { tick(); } catch (_) { /* 定时器里绝不抛出 */ }
  }, TICK_MS);
  // 红包专用定时器：主循环 250ms 太慢，这里 60ms 转一圈，开启时才有开销
  state.redTimer = setInterval(() => {
    if (!state.red.enabled || !state.ready) return;
    try { tickRed(); } catch (cause) { state.red.reason = '红包异常：' + msgOf(cause); }
  }, RED_TICK_MS);
  // 留给下一次注入：新运行时先 clearInterval 掉旧定时器，不然两套一起跑
  window.__zzAssist.__timer = state.timer;
  window.__zzAssist.__redTimer = state.redTimer;
})();
"""





