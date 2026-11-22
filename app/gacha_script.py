"""抽卡（英雄招募）运行时：直连游戏酒馆招募服务，按参考工具的流程抽/放弃。

设计（与参考工具一致）：
    * 招募服务 zm_fuwu：zm_zhaomu(池, 倍率, 0) 抽一次，zm_fangqi(1, 池, 0) 放弃当前候选。
    * 招募面板模型 zm_mianbanMoxing：zm_dangqianChi=池、zm_beilv=倍率、
      zm_houxuanLieBiao=候选列表（每项 zm_houxuanYingxiong=英雄编号、zm_yizhanyong=是否占用）。
    * 抽到的英雄会被标记为「占用」：是目标就保留结束，不是就放弃并刷新候选再抽。
    * 勾了「抽中目标也放弃」时，所有候选一律放弃、继续抽（只攒碎片，不停止）。

页面里运行，不依赖截图匹配。映射来自 assist_mapping.json 的 gacha 段。
"""

from __future__ import annotations

# 运行时代码版本：改本文件行为时 +1
GACHA_VERSION = 14

GACHA_JS = r"""
(() => {
  const VERSION = %(version)d;
  if (window.__zzGacha && window.__zzGacha.version === VERSION) return;
  const MAPPING = window.__zzGachaMapping || { builds: {} };
  const now = () => Date.now();
  const msgOf = e => (e && e.message) || String(e);

  const state = {
    ok: true,
    running: false,
    refreshing: false,
    action: '',
    error: '',
    stopReason: '',
    lastHero: null,
    stats: { draws: 0, kept: 0, abandoned: 0, refreshes: 0, greenSpent: 0, redAppear: 0 },
    // 抽中记录：英雄编号 -> { id, name, quality, count }
    records: Object.create(null),
    // 目标英雄计数：编号 -> { appear 出现次数, hit 抽中次数 }
    counters: Object.create(null),
    // 上一次看到的候选池指纹，用来判断候选池有没有换
    poolKey: '',
    opts: {
      targets: [], keepQuality: 6, maxDraws: 0, delayMs: 1200,
      refresh: false, maxGreen: 0, maxBet: true, alwaysAbandon: false,
    },
    timer: 0,
    startedAt: 0,
  };

  function detectBuild() {
    try {
      if (window.__zzAssist && window.__zzAssist.build) return window.__zzAssist.build;
      const perf = performance.getEntriesByType('resource');
      for (let i = perf.length - 1; i >= 0; i--) {
        const m = /mainScripts\/index\.([0-9a-z]+)\.js/.exec(perf[i].name);
        if (m) return m[1];
      }
    } catch (_) {}
    return '';
  }

  function cfg() {
    const build = detectBuild();
    const entry = (MAPPING.builds || {})[build];
    const g = entry && entry.gacha;
    if (!g) throw Error('当前版本还没有抽卡映射，需要更新映射表');
    g.__build = build;
    return g;
  }

  function holder(g) {
    // 注意：System.get 必须带着 this 调用，解构出来单调用会因为 this 丢失而报错
    const system = window.System;
    const mod = system && typeof system.get === 'function' ? system.get(g.holderModule) : null;
    const H = mod && mod[g.holder];
    if (!H) throw Error('游戏模块尚未就绪，请重新打开招募界面');
    return H;
  }

  function service(g) {
    const svc = holder(g)[g.service];
    if (!svc) throw Error('招募服务尚未就绪，请重新打开招募界面');
    return svc;
  }

  function busy(svc, g) {
    return (g.busy || []).some(k => svc[k]);
  }

  function findView(g) {
    const cc = window.cc;
    const scene = cc && cc.director && cc.director.getScene && cc.director.getScene();
    if (!scene) return null;
    const stack = [scene];
    while (stack.length) {
      const n = stack.pop();
      if (!n || n.activeInHierarchy === false) continue;
      const comps = n.components || n._components || [];
      for (let i = 0; i < comps.length; i++) {
        const c = comps[i];
        if (c && c[g.view.model] && c[g.view.candidates]) return c;
      }
      const ch = n.children || [];
      for (let j = 0; j < ch.length; j++) stack.push(ch[j]);
    }
    return null;
  }

  /** 英雄目录：编号 -> 品级（游戏配置 yx_mulu）。 */
  function heroCatalog(g) {
    const out = new Map();
    try {
      const H = holder(g);
      const h = g.hero;
      const yx = H[h.service];
      const model = yx && yx[h.model];
      const list = model && model[h.list];
      if (list && typeof list.forEach === 'function') {
        list.forEach((m, k) => {
          const id = Number(k);
          const quality = Number(m && m[h.quality]);
          if (Number.isSafeInteger(id) && id > 0 && Number.isSafeInteger(quality)) out.set(id, quality);
        });
      }
    } catch (_) {}
    return out;
  }

  /** 中文名表：hero_name_<id> = "称号·名字"。 */
  function heroNames(g) {
    if (g.__names) return g.__names;
    let table = {};
    try {
      const system = window.System;
      const mod = system && typeof system.get === 'function' ? system.get(g.hero.i18nModule) : null;
      table = (mod && mod[g.hero.i18nExport]) || {};
    } catch (_) { table = {}; }
    g.__names = table;
    return table;
  }

  function heroName(id, g) {
    const table = heroNames(g);
    const full = table[g.hero.namePrefix + id];
    if (typeof full !== 'string' || !full) return '';
    const parts = full.split(/[·・－\-]/);
    return String((parts[parts.length - 1] || full)).trim();
  }

  /** 英雄编号 -> { id, name, quality }；名称/品级读不到时给可读兜底。 */
  function heroInfo(id, g) {
    const heroId = Number(id) || 0;
    let quality = heroCatalog(g).get(heroId) || 0;
    const name = heroName(heroId, g);
    return { id: heroId, name: name || ('英雄 ' + heroId), quality };
  }

  /** 目标可以填编号，也可以填英雄名（支持「牧师·莉莉」「莉莉」这种写法）。 */
  function resolveTargets(raw, g) {
    const ids = [];
    const names = [];
    for (const item of raw || []) {
      const text = String(item === undefined || item === null ? '' : item).trim();
      if (!text) continue;
      if (/^\d+$/.test(text)) ids.push(Number(text));
      else names.push(text);
    }
    if (names.length) {
      const table = heroNames(g);
      const prefix = g.hero.namePrefix;
      for (const [id] of heroCatalog(g)) {
        const full = String(table[prefix + id] || '');
        const short = heroName(id, g);
        if (!full) continue;
        if (names.some(n => n === full || n === short || full.endsWith(n) || short === n)) ids.push(id);
      }
    }
    return [...new Set(ids.filter(n => Number.isSafeInteger(n) && n > 0))];
  }

  /** 判断候选池里是否已经有目标（用于「先刷新候选，出目标再抽」）。 */
  function poolHasTarget(candidates, g) {
    return candidates.some(item => {
      if (item.selected) return false;
      const hero = heroInfo(item.heroId, g);
      return isTarget(hero, state.opts);
    });
  }

  /**
   * 决定这次用哪种刷新方式（对应游戏 zm_fangqi 的 mode）：
   *   3 = 免费刷新（若需要看广告会先播广告；平台不支持就返回 0）
   *   4 = 绿钻刷新（受 opts.maxGreen 预算限制，0 表示不允许付费）
   * 返回 0 表示这次不刷新，退回正常抽。
   */
  function refreshMode(g, svc, contextId) {
    const r = g.refresh;
    try {
      const H = holder(g);
      const enums = H[r.enums];
      const freeRoot = enums && enums[r.freeRoot];
      const sceneId = freeRoot
        ? (contextId === 0 ? freeRoot[r.normalScene] : freeRoot[r.activityScene])
        : null;
      const adService = H[r.adService];
      const scene = adService && typeof adService[r.query] === 'function' ? adService[r.query](sceneId) : null;
      if (scene) {
        const canFree = typeof scene[r.available] === 'function' && scene[r.available]();
        if (canFree) {
          let watched = false;
          try { watched = typeof scene[r.watchedFlag] === 'function' && !!scene[r.watchedFlag](); } catch (_) {}
          try {
            if (!watched && H[r.alreadyService] && enums && enums[r.trie]
              && typeof H[r.alreadyService][r.alreadyMethod] === 'function') {
              watched = !!H[r.alreadyService][r.alreadyMethod](enums[r.trie][r.trieValue]);
            }
          } catch (_) {}
          try {
            if (!watched && H[r.alreadyService2] && typeof H[r.alreadyService2][r.alreadyMethod2] === 'function') {
              watched = !!H[r.alreadyService2][r.alreadyMethod2]();
            }
          } catch (_) {}
          if (watched) return r.modes.free;
          // 需要看广告：平台能播就播完再免费刷新，否则放弃免费路线
          try {
            if (typeof scene[r.adKeyong] === 'function' && scene[r.adKeyong]()
              && typeof scene[r.adPlay] === 'function') {
              scene[r.adPlay]();
              return r.modes.free;
            }
          } catch (_) {}
        }
      }
    } catch (_) { /* 免费刷新读不到就尝试付费 */ }

    if (!(state.opts.maxGreen > 0)) return 0;
    try {
      const price = svc[r.price](contextId);
      const amount = Number(price && price.num);
      if (Number(price && price.pid) !== Number(r.greenPid) || !Number.isSafeInteger(amount) || amount < 0) return 0;
      if (state.stats.greenSpent + amount > state.opts.maxGreen) return 0;
      return r.modes.paid;
    } catch (_) {
      return 0;
    }
  }

  /** 完整英雄图鉴（红/橙），只在没有面板时作参考。 */
  function heroes() {
    const g = cfg();
    const out = [];
    heroCatalog(g).forEach((quality, id) => {
      if (quality < 5) return;
      out.push({ id, name: heroName(id, g) || String(id), quality });
    });
    return out.sort((a, b) => b.quality - a.quality || a.id - b.id);
  }

  /** 当前招募面板里的候选英雄（真正能抽到的），界面目标就从这里选。 */
  function pool() {
    let g = null;
    try {
      g = cfg();
      const view = findView(g);
      if (!view) throw Error('请先打开酒馆/招募界面');
      const model = view[g.view.model];
      if (!model) throw Error('招募面板数据尚未就绪');
      const list = model[g.model.list] || [];
      const heroesOut = list.map(item => {
        const id = Number(item[g.model.candidateHero]);
        const hero = heroInfo(id, g);
        return {
          id,
          name: hero.name,
          quality: hero.quality,
          selected: item[g.model.selected] === true,
        };
      });
      return { ok: true, contextId: model[g.model.contextId], bet: model[g.model.bet], heroes: heroesOut };
    } catch (cause) {
      return { ok: false, reason: msgOf(cause), heroes: [] };
    }
  }

  function isTarget(hero, opts) {
    if (!hero) return false;
    if (opts.keepQuality > 0 && hero.quality >= opts.keepQuality) return true;
    return (opts.targets || []).indexOf(hero.id) >= 0;
  }

  function refreshView(view, g) {
    try {
      const c = view[g.view.candidates];
      const m = g.viewMethods;
      if (c && typeof c[m.refreshList] === 'function') c[m.refreshList]();
      if (c && typeof c[m.refreshPanel] === 'function') {
        const p = c[m.refreshPanel]();
        if (p && typeof p.then === 'function') p.catch(() => {});
      }
      if (c && typeof c[m.refreshPity] === 'function') c[m.refreshPity](view[g.view.model]);
      if (typeof view[m.updatePanel] === 'function') view[m.updatePanel]();
      const model = view[g.view.model];
      if (typeof view[m.available] === 'function' && model) {
        const ok = view[m.available]();
        if (typeof view[m.btnA] === 'function') view[m.btnA](ok);
        if (typeof view[m.btnB] === 'function') view[m.btnB](ok);
        if (typeof view[m.btnC] === 'function') view[m.btnC](ok);
      }
    } catch (_) { /* 刷新失败不致命，下一轮还会再读 */ }
  }

  function stop(reason) {
    state.running = false;
    state.stopReason = reason || '';
    state.action = reason || '已停止';
    if (state.timer) { clearTimeout(state.timer); state.timer = 0; }
  }

  function schedule(delay) {
    if (!state.running) return;
    if (state.timer) clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      state.timer = 0;
      tick();
    }, Math.max(200, delay));
  }

  async function step() {
    const g = cfg();
    const svc = service(g);
    if (busy(svc, g)) { state.action = '等待游戏执行其他招募…'; return; }
    const view = findView(g);
    if (!view) throw Error('请先打开酒馆/招募界面');
    const model = view[g.view.model];
    if (!model) throw Error('招募面板数据尚未就绪');
    const contextId = model[g.model.contextId];
    const bet = model[g.model.bet];
    if (!Number.isSafeInteger(contextId)) throw Error('招募池编号无效');
    if (!Number.isSafeInteger(bet) || bet <= 0) throw Error('招募倍率无效');
    const list = model[g.model.list];
    if (!Array.isArray(list)) throw Error('招募候选列表尚未就绪');

    const candidates = list.map(item => ({
      heroId: Number(item[g.model.candidateHero]),
      selected: item[g.model.selected] === true,
    }));
    const picked = candidates.find(item => item.selected);

    // 候选池每换一批，统计一次「目标英雄出现次数」
    const poolKey = contextId + ':' + candidates.map(c => c.heroId).join(',');
    if (poolKey !== state.poolKey) {
      state.poolKey = poolKey;
      for (const item of candidates) {
        if (state.opts.targets.indexOf(item.heroId) < 0) continue;
        const ctr = state.counters[item.heroId] || (state.counters[item.heroId] = { appear: 0, hit: 0 });
        ctr.appear += 1;
      }
      // 「红出现」= 候选池里出现过的红（品级 6 以上）数量
      try {
        for (const item of candidates) {
          if (heroInfo(item.heroId, g).quality >= 6) state.stats.redAppear += 1;
        }
      } catch (_) {}
    }

    if (picked) {
      const hero = heroInfo(picked.heroId, g);
      state.lastHero = hero;
      if (isTarget(hero, state.opts) && !state.opts.alwaysAbandon) {
        state.stats.kept += 1;
        stop('已抽到目标：' + hero.name + '（品级 ' + hero.quality + '）');
        return;
      }
      const result = await svc[g.abandon](1, contextId, 0);
      const rsp = result && (result.rsp || result);
      const code = rsp && rsp[g.response.wrapper] && rsp[g.response.wrapper][g.response.code];
      if (code !== g.response.success) throw Error('放弃候选失败（回执 ' + (code === undefined ? '空' : code) + '）');
      state.stats.abandoned += 1;
      refreshView(view, g);
      state.action = '放弃 ' + hero.name + '，继续抽';
      return;
    }

    // 「先刷新候选池，出目标再抽」：池里没目标就刷新，有目标才抽
    if (state.opts.refresh && !poolHasTarget(candidates, g)) {
      const mode = refreshMode(g, svc, contextId);
      if (mode) {
        let price = 0;
        if (mode === g.refresh.modes.paid) {
          try { price = Number(svc[g.refresh.price](contextId).num) || 0; } catch (_) { price = 0; }
        }
        const rr = await svc[g.refresh.call](mode, contextId, 0);
        const rrsp = rr && (rr.rsp || rr);
        const rcode = rrsp && rrsp[g.response.wrapper] && rrsp[g.response.wrapper][g.response.code];
        if (rcode !== g.response.success) throw Error('刷新候选失败（回执 ' + (rcode === undefined ? '空' : rcode) + '）');
        state.stats.refreshes += 1;
        state.stats.greenSpent += price;
        refreshView(view, g);
        state.action = (mode === g.refresh.modes.free ? '免费' : '绿钻') + '刷新候选，继续找目标…';
        return;
      }
      // 免费不可用且没给绿钻预算，退回正常抽
    }

    if (state.opts.maxDraws > 0 && state.stats.draws >= state.opts.maxDraws) {
      stop('已达到设置的 ' + state.opts.maxDraws + ' 次抽卡上限');
      return;
    }

    // 候选里有目标 → 切「最大倍率」再抽；没有 → 1 倍（和参考工具一致）
    let drawBet = bet;
    if (state.opts.maxBet && typeof svc[g.setBet] === 'function') {
      const wantMax = candidates.some(item => isTarget(heroInfo(item.heroId, g), state.opts));
      svc[g.setBet](contextId, wantMax ? Number.MAX_VALUE : 1);
      drawBet = model[g.model.bet];
      if (!Number.isSafeInteger(drawBet) || drawBet <= 0) throw Error('招募倍率无效');
    }

    const result = await svc[g.draw](contextId, drawBet, 0);
    const rsp = result && (result.rsp || result);
    const code = rsp && rsp[g.response.wrapper] && rsp[g.response.wrapper][g.response.code];
    if (code !== g.response.success) throw Error('抽卡失败（回执 ' + (code === undefined ? '空' : code) + '）');
    state.stats.draws += 1;
    let hitId = 0;
    try {
      hitId = Number(rsp[g.response.hitInfo][g.response.hitModel][g.response.hitHero]) || 0;
    } catch (_) { hitId = 0; }
    if (hitId) {
      const hero = heroInfo(hitId, g);
      state.lastHero = hero;
      const prev = state.records[hitId];
      state.records[hitId] = {
        id: hitId,
        name: hero.name,
        quality: hero.quality,
        count: (prev ? prev.count : 0) + 1,
      };
      if (state.opts.targets.indexOf(hitId) >= 0) {
        const ctr = state.counters[hitId] || (state.counters[hitId] = { appear: 0, hit: 0 });
        ctr.hit += 1;
      }
      state.action = '抽到 ' + hero.name + '（品级 ' + hero.quality + '，' + drawBet + ' 倍）';
    } else {
      state.action = '抽卡成功，等待候选刷新';
    }
  }

  function tick() {
    if (!state.running) return;
    if (state.refreshing) { schedule(state.opts.delayMs); return; }
    state.refreshing = true;
    Promise.resolve()
      .then(step)
      .catch(cause => {
        state.error = msgOf(cause);
        // 金币不足、接口失败等异常：直接停掉，避免继续空转/刷请求
        stop('抽卡异常：' + state.error);
      })
      .then(() => {
        state.refreshing = false;
        if (state.running) schedule(state.opts.delayMs);
      });
  }

  // 英雄碎片：游戏里每个英雄的碎片就是一件道具，编号 = 700000 + 英雄编号
  let fragCache = { at: 0, data: {} };
  function allFragments(g) {
    const nowMs = Date.now();
    if (nowMs - fragCache.at < 3000) return fragCache.data;
    const out = {};
    try {
      const H = holder(g);
      const svc = H && H[g.refresh.balanceService];
      const readFn = g.refresh.balance;
      const base = Number((g.hero && g.hero.fragmentItemBase) || 700000);
      const heroSvc = H && H[g.hero.service];
      const map = heroSvc && heroSvc[g.hero.model] && heroSvc[g.hero.model][g.hero.list];
      if (svc && typeof svc[readFn] === 'function' && map && typeof map.forEach === 'function') {
        map.forEach(function (_entry, id) {
          try {
            const n = Number(svc[readFn](base + Number(id)));
            if (Number.isFinite(n)) out[id] = n;
          } catch (_) { /* 单个英雄失败就算了 */ }
        });
      }
    } catch (_) { /* 没进游戏就读不到 */ }
    fragCache = { at: nowMs, data: out };
    return out;
  }

  function snapshot() {
    let ready = false;
    let stateReason = '';
    let candidates = [];
    let contextId = null;
    let bet = null;
    let drawCount = null;
    let fragments = {};
    let gold = null;
    let green = null;
    try {
      const g = cfg();
      const view = findView(g);
      if (!view) throw Error('请先打开酒馆/招募界面');
      const model = view[g.view.model];
      if (!model) throw Error('招募面板数据尚未就绪');
      contextId = model[g.model.contextId];
      bet = model[g.model.bet];
      drawCount = Number(model[g.model.drawCount]);
      const list = model[g.model.list] || [];
      candidates = list.map(item => ({
        heroId: Number(item[g.model.candidateHero]),
        selected: item[g.model.selected] === true,
      }));
      ready = true;
      try { fragments = allFragments(g); } catch (_) { fragments = {}; }
      try {
        const H = holder(g);
        const bal = H && H[g.refresh.balanceService];
        if (bal && typeof bal[g.refresh.balance] === 'function') {
          gold = Number(bal[g.refresh.balance](g.refresh.goldPid || 1));
          green = Number(bal[g.refresh.balance](g.refresh.greenPid || 88));
        }
      } catch (_) { gold = null; green = null; }
    } catch (cause) {
      stateReason = msgOf(cause);
    }
    return {
      ok: state.ok,
      ready,
      stateReason,
      running: state.running,
      action: state.action,
      error: state.error,
      stopReason: state.stopReason,
      lastHero: state.lastHero,
      stats: {
        draws: state.stats.draws,
        kept: state.stats.kept,
        abandoned: state.stats.abandoned,
        refreshes: state.stats.refreshes,
        greenSpent: state.stats.greenSpent,
        redAppear: state.stats.redAppear,
      },
      candidates,
      records: Object.keys(state.records).map(id => state.records[id]),
      counters: state.counters,
      fragments,
      gold,
      green,
      contextId,
      bet,
      drawCount,
      build: detectBuild(),
    };
  }

  function start(opts) {
    const g = cfg();
    const o = opts || {};
    state.opts = {
      targets: resolveTargets(o.targets || [], g),
      keepQuality: Number(o.keepQuality) || 0,
      maxDraws: Math.max(0, Number(o.maxDraws) || 0),
      delayMs: Math.max(300, Number(o.delayMs) || 1200),
      refresh: !!o.refresh,
      maxGreen: Math.max(0, Number(o.maxGreen) || 0),
      maxBet: o.maxBet !== false,
      alwaysAbandon: !!o.alwaysAbandon,
    };
    if (!state.opts.targets.length && !state.opts.keepQuality) {
      throw Error('请至少填目标英雄编号，或勾选「只保留红色」');
    }
    const view = findView(g);
    if (!view) throw Error('请先打开酒馆/招募界面再开始');
    state.stats = { draws: 0, kept: 0, abandoned: 0, refreshes: 0, greenSpent: 0, redAppear: 0 };
    state.records = Object.create(null);
    state.counters = Object.create(null);
    state.poolKey = '';
    state.error = '';
    state.stopReason = '';
    state.action = '开始抽卡…';
    state.lastHero = null;
    state.startedAt = now();
    state.running = true;
    tick();
    return snapshot();
  }

  window.__zzGacha = {
    version: VERSION,
    snapshot,
    heroes,
    pool,
    start,
    stop: reason => { stop(reason || '已手动停止'); return snapshot(); },
    debug: () => {
      const out = {};
      let g = null;
      try { g = cfg(); } catch (cause) { out.cfgErr = msgOf(cause); }
      if (g) {
        out.hero = g.hero;
        try { out.hasHolder = !!holder(g); } catch (cause) { out.holderErr = msgOf(cause); }
        try { out.catalog = heroCatalog(g).size; } catch (cause) { out.catalogErr = msgOf(cause); }
        try { const names = heroNames(g); out.names = names ? Object.keys(names).length : -1; } catch (cause) { out.namesErr = msgOf(cause); }
      }
      return JSON.stringify(out);
    },
  };
})();
"""


def build_runtime() -> str:
    return GACHA_JS % {"version": GACHA_VERSION}
