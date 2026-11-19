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

RUNTIME_VERSION = 73
"""运行时代码版本：改 RUNTIME_JS 的行为时 +1。

页面里的旧运行时不会被自动覆盖（`window.__zzAssist` 已存在就直接复用），
所以只要改了注入代码或映射表结构，就必须 +1，否则窗口里跑的还是旧的。
"""

RUNTIME_JS = r"""
(() => {
  const VERSION = 73;

  // 同一页里可能还留着旧版本注入的运行时（改了代码但没关窗口），
  // 那套状态机会继续报旧错误，这里先把它停掉再重装。
  const previous = window.__zzAssist;
  if (previous && previous.version === VERSION) return;
  if (previous) {
    try { if (previous.__timer) clearInterval(previous.__timer); } catch (_) { /* ignore */ }
    try { if (typeof previous.stopAll === 'function') previous.stopAll(); } catch (_) { /* ignore */ }
    try { delete window.__zzAssist; } catch (_) { window.__zzAssist = undefined; }
  }
  const TICK_MS = 250;        // 红包轮询间隔：聊天里的红包一出现就领
  const RETRY_MS = 3000;      // 解析失败的重试间隔
  const CLAIM_GAP_MS = 150;   // 两次领取之间的最小间隔，避免瞬间刷请求

  const MAPPING = window.__zzAssistMapping || { builds: {} };

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
          for (let i = list.length - 1; i >= 0; i -= 1) {
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

  function packetId(info) {
    const key = (state.ctx && state.ctx.redCfg.fields && state.ctx.redCfg.fields.id) || '';
    if (!key) return '';
    const value = info[key];
    return value === undefined || value === null ? '' : String(value);
  }

  function claim(info) {
    const ctx = state.ctx;
    const fields = ctx.redCfg.fields || {};
    const id = packetId(info);
    if (!id) return;
    state.red.pending += 1;
    const failed = () => {
      state.red.pending = Math.max(0, state.red.pending - 1);
      state.red.failed += 1;
      state.red.seen[id] = -1;
    };
    try {
      const done = ctx.redPacket[ctx.redCfg.claim](id, info[fields.channel], info[fields.type], failed);
      if (done && typeof done.then === 'function') {
        done.then(code => {
          state.red.pending = Math.max(0, state.red.pending - 1);
          if (Number(code) === 1) {
            state.red.claimed += 1;
            state.red.today += 1;
            state.red.date = today();
            state.red.reason = '今日已领 ' + state.red.today + ' 个红包';
          } else {
            state.red.seen[id] = -1;
          }
        }, () => failed());
      } else {
        state.red.pending = Math.max(0, state.red.pending - 1);
      }
    } catch (cause) {
      failed();
      state.red.reason = '领取调用失败：' + msgOf(cause);
    }
  }

  function tickRed() {
    if (!state.red.enabled || !state.ready) return;
    if (now() - state.red.lastAt < CLAIM_GAP_MS) return;
    let list = [];
    try { list = packetSources(); } catch (_) { return; }
    state.red.available = list.length > 0;
    if (!list.length) return;

    const claimable = (state.ctx.map.statusCodes || {}).claimable;
    let picks = 0;
    for (const info of list) {
      const id = packetId(info);
      if (!id || state.red.seen[id] !== undefined) continue;
      let status = -1;
      try { status = state.ctx.redPacket[state.ctx.redCfg.status](info); } catch (_) { continue; }
      if (Number(status) !== Number(claimable)) {
        state.red.seen[id] = Number(status);
        continue;
      }
      state.red.seen[id] = 9;  // 先占位，防同一 tick 重复
      state.red.lastAt = now();
      claim(info);
      picks += 1;
      if (picks >= 3) break;
    }
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
    const cfg = ctx.cfg;
    const world = ctx.holder;
    if (typeof world[cfg.enter] !== 'function') throw Error('游戏里没有地图重进接口，需要更新映射');
    const normal = cfg.state ? world[cfg.state] : null;
    const mapId = normal && cfg.mapId ? normal[cfg.mapId] : null;
    if (mapId === undefined || mapId === null) throw Error('拿不到当前地图编号，请先进入地图');

    state.map.entering = true;
    state.map.lastEnterAt = now();
    state.map.seen = Object.create(null);
    const kind = state.map.pendingKind;
    const label = kind === 'boss' ? 'BOSS' : '小怪';

    return Promise.resolve(fastEnterMap(world, cfg, mapId)).then(result => {
      if (result !== true) throw Error('地图重进没有成功（游戏返回 ' + result + '）');
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
        continue;
      }
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
      const due = nowMs + 1;
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
          void mapEnter(ctx).catch(cause => {
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
    damage: { hook: null, context: null, seen: null, value: 1, enabled: false, reason: '' },
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

  function setDamage(cfg, value) {
    const box = combat.damage;
    const range = (cfg.ranges && cfg.ranges.damage) || [1, 70000];
    if (!(value >= range[0] && value <= range[1])) {
      throw Error('倍率需要在 ' + range[0] + ' - ' + range[1] + ' 之间');
    }
    let context = box.context;
    if (!context || context.owner[cfg.damage.receive] !== context.descriptor.value) {
      context = damageContext(cfg);
    }
    const current = context.owner[cfg.damage.receive];
    if (current !== (box.hook ? box.hook.wrapper : context.descriptor.value)) {
      throw Error('伤害结算入口已被替换，请重新打开游戏窗口');
    }
    if (!box.hook) {
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
        // 辅助自己出错也照常执行游戏原来的结算流程
        return Reflect.apply(context.descriptor.value, this, arguments);
      };
      Object.defineProperty(context.owner, cfg.damage.receive, { ...context.descriptor, value: wrapper });
      box.hook = { owner: context.owner, key: cfg.damage.receive, descriptor: context.descriptor, wrapper };
    }
    box.context = context;
    box.value = value;
    box.enabled = true;
    return { multiplier: value, heroes: context.heroes().length };
  }

  function stopDamage() {
    const box = combat.damage;
    box.enabled = false;
    const hook = box.hook;
    if (hook && hook.owner[hook.key] === hook.wrapper) {
      try { Object.defineProperty(hook.owner, hook.key, hook.descriptor); } catch (_) { /* 外部锁定就等换页恢复 */ }
    }
    box.hook = null;
    box.context = null;
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

  function cooldownRows(cfg) {
    const manager = combatManager(cfg);
    const skills = manager[cfg.config.table](cfg.config.skillTable);
    if (!Array.isArray(skills)) throw Error('技能配置表（' + cfg.config.skillTable + '）读取失败');
    const rows = [];
    for (const row of skills) {
      if (!row) continue;
      const id = row[cfg.config.skill];
      if (!Number.isSafeInteger(id)) continue;
      const cd = row[cfg.config.cooldown];
      if (!cd || typeof cd !== 'object') continue;
      const ms = cd[cfg.config.ms];
      if (typeof ms !== 'number' || !Number.isFinite(ms)) continue;
      // 参考工具只改长冷却技能（40~60 秒），这里保持一致
      if (ms < 40000 || ms > 60000) continue;
      rows.push({ id, row, cd });
    }
    if (!rows.length) throw Error('技能配置里没找到可改的冷却（版本可能变了）');
    return { manager, skills, rows };
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
    try { box.written = combatRestore(box.written); } catch (_) { /* 留到下一轮再试 */ }
    if (name === 'vision') box.core = null;
    else box.config = null;
    item.enabled = false;
    item.reason = box.written.length
      ? '已关闭（有 ' + box.written.length + ' 项没还原，重进游戏后会自然恢复）'
      : '已关闭';
  }

  function setCombat(name, value) {
    const cfg = combatConfig();
    const item = state.combat[name];
    if (state.mob.enabled || state.boss.enabled) {
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

    if (state.combat.damage.enabled) {
      const box = combat.damage;
      if (!box.hook || box.hook.owner[box.hook.key] !== box.hook.wrapper) {
        stopDamage();
        state.combat.damage.enabled = false;
        state.combat.damage.reason = '已停止：游戏重新加载了伤害结算入口，请重新开启';
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

  /** 重进地图时反复压掉加载页：XIPH 一调用就开始清 MAOP / 动画。 */
  function fastEnterMap(holder, cfg, mapId) {
    const name = (cfg && cfg.enter) || 'XIPH';
    if (!holder || typeof holder[name] !== 'function') {
      throw Error('游戏里没有地图重进接口，需要更新映射');
    }
    const until = now() + 3500;
    const tick = () => {
      try { dropLoading(); } catch (_) { /* ignore */ }
      try { hideLanding(); } catch (_) { /* ignore */ }
      if (now() < until) setTimeout(tick, 60);
    };
    const result = holder[name](mapId);
    tick();
    return result;
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
    if (box.busy || nowMs < box.nextTryAt) return;
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
    if (state.ready) {
      try { tickRed(); } catch (cause) { state.red.reason = '红包异常：' + msgOf(cause); }
    }
    // 祈愿不依赖红包服务是否可用，单独跑，互不影响。
    try { tickWish(); } catch (cause) { state.wish.reason = '祈愿异常：' + msgOf(cause); }
    // 地图同理：红包/祈愿都不可用时，打怪重进地图照样能跑。
    try { tickMap(); } catch (cause) {
      const text = msgOf(cause);
      if (state.mob.enabled) state.mob.reason = text;
      if (state.boss.enabled) state.boss.reason = text;
    }
    // 战斗参数：只做「会话是否还有效」的守护，不改数值
    try { tickCombat(); } catch (_) { /* 守护失败不影响其它功能 */ }
    // 快速复活：独立跑，不依赖红包/地图刷新
    try { tickRevive(); } catch (cause) { state.revive.reason = '复活异常：' + msgOf(cause); }
    // 单英雄复活时间
    try { tickHeroRevive(); } catch (_) { /* 每秒重试 */ }
    // 回营地复活 + 飞回原位
    try { tickCampFly(); } catch (_) { /* 下一轮继续 */ }
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

  function snapshot() {
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

  function set(feature, value) {
    probe();
    if (feature === 'stopAll') {
      state.red.enabled = false;
      state.red.reason = '已关闭';
      wishStop('已关闭');
      mapStop('已关闭');
      for (const name of ['damage', 'vision', 'cooldown']) {
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

    if (feature === 'damage' || feature === 'vision' || feature === 'cooldown') {
      const label = { damage: '英雄倍伤', vision: '视野修改', cooldown: '技能冷却' }[feature];
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
      state.red.reason = value ? '监控中：聊天里出现可领红包就自动领' : '已关闭';
      return {
        ok: true,
        message: value ? '快速抢红包已开启（今日已领 ' + state.red.today + ' 个）' : '快速抢红包已关闭',
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

  window.__zzAssist = {
    version: VERSION,
    snapshot,
    set,
    diagnose,
    refresh: () => { state.nextProbeAt = 0; probe(); },
    // 只给自测用：直接塞一次「刚死亡」事件，下一轮扫描就按真实死亡走
    // 「等待 N 秒 → 重进地图」。不碰游戏数据，可以在任何场景里验证整条链路。
    __fakeDeath: kind => {
      const info = { kind: kind === 'boss' ? 'boss' : 'mob', name: '自测假怪', dead: true };
      state.map.fakeDeaths.push(info);
      return 'fake_' + kind + '_' + now();
    },
  };

  state.timer = setInterval(() => {
    try { tick(); } catch (_) { /* 定时器里绝不抛出 */ }
  }, TICK_MS);
  // 留给下一次注入：新运行时先 clearInterval 掉旧定时器，不然两套一起跑
  window.__zzAssist.__timer = state.timer;
})();
"""





