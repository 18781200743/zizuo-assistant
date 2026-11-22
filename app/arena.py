"""荣耀竞技场：按 game_storage_sync 的流程跑免费挑战。

流程：
1. 刷新竞技场状态，检查开放/阵容/对手/主城；
2. 选积分最高的对手；
3. 调原生挑战接口，进入竞技场世界；
4. 等结算页出现后关闭，回到主城；
5. 免费次数用完为止。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, Optional


def _build(mapping: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    data = dict(mapping or {})
    builds = data.get("builds") or {}
    current = str(data.get("current") or "")
    if not current and builds:
        current = sorted(builds.keys())[0]
        data["current"] = current
    build = builds.get(current) or {}
    return build.get("arena") or {}, build.get("map") or {}


_ARENA_TEMPLATE = r"""
(function () {
  try {
    var A = %s;
    var MC = %s;
    var MAX_COUNT = %s;
    var FAST = %s;
    var ALLOW_PAID = %s;
    var ALLOW_BATTLE = %s;
    var KEY = '__zzArena';
    var previous = window[KEY];
    if (previous && previous.running) return 'busy';

    var world = window[MC.holder || 'GCOA'];
    if (!world) return 'ERR 游戏还没进地图';

    var pairs = [...System.entries()];
    var bag = null;
    for (var i = 0; i < pairs.length; i += 1) {
      var ex = pairs[i][1];
      if (ex && ex.DYCU && ex.DYCU[MC.holder || 'GCOA'] === world) { bag = ex.DYCU; break; }
    }
    if (!bag) return 'ERR 找不到游戏服务';
    var svc = bag[A.service];
    var model = svc && svc[A.model];
    if (!svc || !model) return 'ERR 竞技场服务未就绪';

    var S = window[KEY] = {
      running: true, stop: false, done: false, error: '', action: '',
      challenges: 0, free: 0, totalFree: 0, paidLogged: false, log: []
    };
    function log(msg) {
      S.log.push(String(msg));
      if (S.log.length > 50) S.log.shift();
    }
    function sleep(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }
    function check() { if (S.stop) throw new Error('STOP'); }

    var viewCls = null, forms = null, setupCls = null;
    try { viewCls = (System.get('chunks:///_virtual/' + A.viewModule) || {})[A.viewManager]; } catch (e) {}
    try { forms = (System.get('chunks:///_virtual/' + A.configModule) || {})[A.viewConfig]; } catch (e) {}
    try { setupCls = (System.get('chunks:///_virtual/' + A.setupModule) || {})[A.battleSetup]; } catch (e) {}
    var views = viewCls && viewCls[A.viewSingleton];

    function isMainWorld() {
      try { return !!world[MC.scene || 'STDU'] && world[MC.scene || 'STDU'][A.worldTypeField] === A.mainWorldType; }
      catch (e) { return false; }
    }
    function form(name) { return forms && forms[name]; }
    function isOpen(name) {
      try { return !!(views && views[A.isOpen] && views[A.isOpen](form(name))); } catch (e) { return false; }
    }
    function isTransitioning() { return isOpen(A.transitionView); }
    function resultOpen() { return isOpen(A.resultView); }
    function getView() {
      try { return views && views[A.getView] ? views[A.getView](form(A.resultView)) : null; } catch (e) { return null; }
    }
    function findResultComp() {
      var found = null;
      try {
        var scene = cc.director.getScene();
        (function walk(node, depth) {
          if (!node || found || depth > 40) return;
          if (String(node.name || '') === A.resultView) {
            (node._components || []).forEach(function (comp) {
              if (comp && typeof comp[A.resultClose] === 'function') found = comp;
            });
            if (found) return;
          }
          (node.children || []).forEach(function (kid) { walk(kid, depth + 1); });
        })(scene, 0);
      } catch (e) {}
      return found;
    }
    function arenaClosed() { try { return !!model[A.closed](false); } catch (e) { return false; } }
    function freeCount() { try { return Number(model[A.free]()) || 0; } catch (e) { return 0; } }
    function ticketCount() {
      try {
        var inv = bag[A.inventoryService];
        if (inv && typeof inv[A.ticketCount] === 'function') {
          return Number(inv[A.ticketCount](model[A.ticketId]())) || 0;
        }
      } catch (e) {}
      return 0;
    }

    var LAST_ERROR = '';
    var origSend = svc[A.sendRequest];
    if (typeof origSend === 'function') {
      svc[A.sendRequest] = function (req) {
        var pending = origSend.apply(this, arguments);
        try {
          pending.then(function (res) {
            var err = res && res[A.responseError];
            if (err && Number(err[A.responseCode])) {
              LAST_ERROR = String(err[A.responseMessage] || ('错误码 ' + err[A.responseCode]));
            }
          }, function () {});
        } catch (e) {}
        return pending;
      };
    }
    function takeError() { var e = LAST_ERROR; LAST_ERROR = ''; return e; }
    function friendlyError(text) {
      var s = String(text || '');
      if (s.indexOf('no auto challenge num') >= 0) return '没有可用的自动挑战次数（免费次数为 0，或未开启自动挑战）';
      if (s.indexOf('manual jump') >= 0) return '游戏未开启手动跳转特权';
      if (s.indexOf('no open privilege') >= 0) return '游戏未开启对应特权';
      if (s.indexOf('free refresh count') >= 0) return '免费刷新次数不足';
      return s;
    }
    function restoreHooks() {
      try { if (typeof origSend === 'function') svc[A.sendRequest] = origSend; } catch (e) {}
    }

    async function directChallenge() {
      if (!FAST) return { ok: false, error: '没有开启极速模式' };
      var auto = null;
      try { auto = model[A.auto](); } catch (e) {}
      if (!auto) return { ok: false, error: '自动竞技状态不可用' };
      var opponents = model[A.opponents]() || [];
      var robots = opponents.filter(function (o) {
        try { return !!o[A.robot](); } catch (e) { return false; }
      });
      var pool = robots.length ? robots : opponents;
      if (!pool.length) return { ok: false, error: '对手列表为空，请先在游戏内刷新对手' };
      var enemy = pool.slice().sort(function (a, b) {
        return (b[A.points]() || 0) - (a[A.points]() || 0);
      })[0];
      var player = enemy[A.player] ? enemy[A.player]() : null;
      var prevOn = false, prevRem = 0, prevTotal = 0, prevCount = 0;
      try { prevOn = !!auto[A.autoOn](); } catch (e) {}
      try { prevRem = Number(auto[A.autoRemaining]()) || 0; } catch (e) {}
      try { prevTotal = Number(auto[A.autoTotal]()) || 0; } catch (e) {}
      try { prevCount = Number(auto[A.autoPrev]()) || 0; } catch (e) {}
      var beforeFree = freeCount();
      try {
        auto[A.autoOn](true);
        if (typeof auto[A.autoCount] === 'function') auto[A.autoCount](1);
        takeError();
        S.action = '极速竞技 ' + ((player && player[A.playerName]) || '') + '（' + enemy[A.points]() + '分）';
        var result = await svc[A.challenge](
          model[A.battleType](), model[A.battleMode](),
          player, enemy[A.points](), enemy[A.power](),
          true, true, false
        );
        var error = friendlyError(takeError());
        if (!result) {
          var reason = error || '游戏未返回结果';
          log('极速竞技未成功：' + reason);
          return { ok: false, error: reason };
        }
        S.action = '极速结算';
        try {
          if (typeof auto[A.autoUpdate] === 'function' && result[A.autoRemainingField] !== undefined) {
            auto[A.autoUpdate](Number(result[A.autoRemainingField]) || 0);
          }
        } catch (e) {}
        try {
          if (typeof auto[A.autoCount] === 'function' && typeof auto[A.autoPrev] === 'function') {
            auto[A.autoCount](Math.max(0, Number(auto[A.autoPrev]()) - 1));
          }
        } catch (e) {}
        await svc[A.settle](true, null, null, true);
        var deadline = Date.now() + 15000;
        while (Date.now() < deadline) {
          if (resultOpen()) {
            var comp = getView() || findResultComp();
            if (comp) {
              try {
                if (typeof comp[A.resultTip] === 'function') comp[A.resultTip]();
                comp[A.resultClose]();
                log('已关闭结算页');
              } catch (e) {
                log('关闭结算页失败：' + ((e && e.message) || e));
              }
            }
          }
          if (!resultOpen() && !isTransitioning() && isMainWorld()) break;
          await sleep(300);
        }
        if (!isMainWorld()) throw new Error('极速结算后没有回到主城，请检查游戏画面');
        await sleep(300);
        if (freeCount() >= beforeFree) {
          log('注意：本次没有扣除免费次数，已停止以免重复挑战');
          return { ok: false, error: '免费次数未扣除' };
        }
        return { ok: true, error: '' };
      } finally {
        try {
          if (!prevOn) auto[A.autoOn](false);
          if (typeof auto[A.autoUpdate] === 'function') {
            auto[A.autoUpdate](prevRem, prevTotal);
          }
          if (typeof auto[A.autoCount] === 'function') auto[A.autoCount](prevCount);
        } catch (e) {
          log('恢复自动竞技失败：' + ((e && e.message) || e));
        }
      }
    }

    async function challengeOnce(allowPaid) {
      S.action = '刷新竞技场';
      await svc[A.refresh]();
      check();
      if (arenaClosed()) throw new Error('荣耀竞技场已结束');
      var paymentFree = freeCount() > 0;
      if (!paymentFree && !allowPaid) return false;
      if (!model[A.lineup]().length) throw new Error('请先在游戏内设置竞技场阵容');
      var opponents = model[A.opponents]() || [];
      if (!opponents.length) throw new Error('对手列表为空，请先在游戏内刷新对手');
      if (!isMainWorld()) throw new Error('当前不在主城，请返回主城后再挑战');
      if (resultOpen()) throw new Error('竞技场结算页面尚未关闭');
      if (isTransitioning()) throw new Error('游戏过场尚未结束');

      var enemy = opponents.slice().sort(function (a, b) {
        return (b[A.points]() || 0) - (a[A.points]() || 0);
      })[0];
      if (paymentFree && FAST) {
        var direct = await directChallenge();
        if (direct.ok) return true;
        check();
        log('直接结算失败：' + direct.error);
        if (!ALLOW_BATTLE) {
          throw new Error('直接结算失败：' + direct.error + '。已停止，没有进入战斗');
        }
        log('按设置改走正常战斗');
      }
      if (!paymentFree && !S.paidLogged) {
        S.paidLogged = true;
        log('免费次数已用完，之后使用门票/绿钻正常战斗');
      }

      var battle = model[A.battle]();
      try {
        if (setupCls && setupCls[A.viewSingleton] && typeof setupCls[A.viewSingleton][A.battleSetupMethod] === 'function') {
          setupCls[A.viewSingleton][A.battleSetupMethod](svc, battle, null);
        }
      } catch (e) {
        log('竞技初始化失败：' + ((e && e.message) || e));
      }

      var player = enemy[A.player] ? enemy[A.player]() : null;
      S.action = '挑战 ' + ((player && player[A.playerName]) || '') + '（' + enemy[A.points]() + '分）';
      var result = await svc[A.challenge](
        model[A.battleType](), model[A.battleMode](),
        player, enemy[A.points](), enemy[A.power](),
        !!paymentFree, false, false
      );
      check();
      if (!result) throw new Error('挑战未开始：免费次数/门票/绿钻不足，或竞技场状态已变化');
      var destination = result[A.resultDestination];
      if (!destination) throw new Error('未收到竞技开始回执');

      S.action = '进入竞技场';
      await world[A.enterWorld](destination);
      S.action = '等待战斗结束';
      var deadline = Date.now() + 180000;
      var closeTried = false;
      while (Date.now() < deadline) {
        if (arenaClosed()) return false;
        if (resultOpen() && !closeTried) {
          var comp = getView() || findResultComp();
          if (comp) {
            try {
              if (typeof comp[A.resultTip] === 'function') comp[A.resultTip]();
              comp[A.resultClose]();
              closeTried = true;
              log('已关闭结算页');
            } catch (e) {
              log('关闭结算页失败：' + ((e && e.message) || e));
            }
          }
        }
        if (!resultOpen() && !isTransitioning() && isMainWorld()) return true;
        await sleep(400);
      }
      throw new Error('等待竞技场战斗结束超时');
    }

    (async function () {
      try {
        S.totalFree = freeCount();
        S.free = S.totalFree;
        log('免费次数：' + S.free + (ALLOW_PAID ? '（允许门票/绿钻，付费走正常战斗）' : '（只用免费次数）'));
        while (!S.stop) {
          check();
          if (MAX_COUNT && S.challenges >= MAX_COUNT) {
            log('已到设定次数 ' + MAX_COUNT + ' 次');
            break;
          }
          var freeNow = freeCount();
          if (!(freeNow > 0) && !ALLOW_PAID) { log('免费次数已用尽'); break; }
          if (!(freeNow > 0) && ALLOW_PAID && !MAX_COUNT) {
            log('免费次数为 0，付费挑战请先设置挑战次数');
            break;
          }
          var ok = await challengeOnce(ALLOW_PAID);
          if (ok === false) break;
          S.challenges += 1;
          S.free = freeCount();
          log('已挑战 ' + S.challenges + ' 次，剩余免费 ' + S.free);
          await sleep(600);
        }
      } catch (e) {
        var msg = String((e && e.message) || e);
        if (msg !== 'STOP') { S.error = msg; log('失败：' + msg); }
      } finally {
        restoreHooks();
        S.running = false;
        S.done = true;
        S.action = '';
      }
    })();
    return 'started';
  } catch (e) {
    return 'ERR ' + ((e && e.stack) || e);
  }
})()
"""

_PROBE_TEMPLATE = r"""
(function () {
  try {
    var A = %s;
    var MC = %s;
    var world = window[MC.holder || 'GCOA'];
    if (!world) return JSON.stringify({ error: '游戏还没进地图' });
    var pairs = [...System.entries()];
    var bag = null;
    for (var i = 0; i < pairs.length; i += 1) {
      var ex = pairs[i][1];
      if (ex && ex.DYCU && ex.DYCU[MC.holder || 'GCOA'] === world) { bag = ex.DYCU; break; }
    }
    var svc = bag && bag[A.service];
    var model = svc && svc[A.model];
    if (!model) return JSON.stringify({ error: '竞技场服务未就绪' });
    var opponents = [];
    try { opponents = model[A.opponents]() || []; } catch (e) {}
    var points = opponents.map(function (o) { try { return o[A.points](); } catch (e) { return 0; } });
    var rank = null;
    try { rank = model[A.rank](); } catch (e) {}
    var tickets = null, ticketCost = null;
    try {
      var inv = bag[A.inventoryService];
      ticketCost = Number(model[A.ticketCost]()) || 0;
      if (inv && typeof inv[A.ticketCount] === 'function') {
        tickets = Number(inv[A.ticketCount](model[A.ticketId]()));
      }
    } catch (e) { tickets = null; }
    var autoRemaining = null, autoTotal = null;
    try {
      var auto = model[A.auto]();
      autoRemaining = Number(auto[A.autoRemaining]()) || 0;
      autoTotal = Number(auto[A.autoTotal]()) || 0;
    } catch (e) {}
    var views = null;
    try {
      var vc = (System.get('chunks:///_virtual/' + A.viewModule) || {})[A.viewManager];
      views = vc && vc[A.viewSingleton];
    } catch (e) {}
    var forms = null;
    try { forms = (System.get('chunks:///_virtual/' + A.configModule) || {})[A.viewConfig]; } catch (e) {}
    function open(name) { try { return !!(views && forms && views[A.isOpen](forms[name])); } catch (e) { return false; } }
    var main = false;
    try { main = world[MC.scene || 'STDU'][A.worldTypeField] === A.mainWorldType; } catch (e) {}
    return JSON.stringify({
      free: (function () { try { return Number(model[A.free]()) || 0; } catch (e) { return 0; } })(),
      closed: (function () { try { return !!model[A.closed](false); } catch (e) { return false; } })(),
      lineup: (function () { try { return (model[A.lineup]() || []).length; } catch (e) { return 0; } })(),
      opponents: opponents.length,
      maxPoints: points.length ? Math.max.apply(null, points) : 0,
      minPoints: points.length ? Math.min.apply(null, points) : 0,
      rank: '',
      tickets: tickets,
      ticketCost: ticketCost,
      autoRemaining: autoRemaining,
      autoTotal: autoTotal,
      main: main,
      transitioning: open(A.transitionView),
      resultOpen: open(A.resultView)
    });
  } catch (e) {
    return JSON.stringify({ error: String((e && e.message) || e) });
  }
})()
"""


class ArenaRunner:
    """在后台跑竞技场的免费挑战；状态放在页面 window.__zzArena。"""

    def __init__(
        self,
        bridge_provider: Callable[[], Any],
        log: Optional[Callable[[str], None]] = None,
        on_finished: Optional[Callable[[bool, str], None]] = None,
        on_state: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._bridge_provider = bridge_provider
        self._log = log or (lambda _msg: None)
        self._on_finished = on_finished or (lambda _ok, _msg: None)
        self._on_state = on_state or (lambda _state: None)
        self._thread: Optional[threading.Thread] = None
        self.running = False

    def _eval(self, js: str) -> Any:
        bridge = self._bridge_provider()
        if bridge is None:
            raise RuntimeError("还没连上游戏窗口")
        return bridge.eval_js(js)

    def probe(self, mapping: Dict[str, Any]) -> Dict[str, Any]:
        arena, mc = _build(mapping)
        raw = self._eval(
            _PROBE_TEMPLATE % (
                json.dumps(arena, ensure_ascii=False),
                json.dumps(mc, ensure_ascii=False),
            )
        )
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def start(self, mapping: Dict[str, Any], count: int = 0, fast: bool = True, allow_paid: bool = False, allow_battle: bool = False) -> None:
        if self.running:
            raise RuntimeError("竞技场还在跑")
        arena, mc = _build(mapping)
        result = str(
            self._eval(
                _ARENA_TEMPLATE % (
                    json.dumps(arena, ensure_ascii=False),
                    json.dumps(mc, ensure_ascii=False),
                    int(count or 0),
                    int(1 if fast else 0),
                    int(1 if allow_paid else 0),
                    int(1 if allow_battle else 0),
                )
            )
            or ""
        )
        if result == "busy":
            raise RuntimeError("竞技场还在运行")
        if result not in ("started", "ok"):
            raise RuntimeError(result or "竞技场启动失败")
        self.running = True
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        try:
            self._eval("(function(){var s=window.__zzArena;if(s)s.stop=true;return 'ok'})()")
        except Exception:  # noqa: BLE001
            pass

    def _watch(self) -> None:
        ok = False
        message = "已完成"
        last_logs = 0
        last_action = ""
        try:
            while True:
                raw = self._eval("JSON.stringify(window.__zzArena||null)")
                try:
                    state = json.loads(raw) if isinstance(raw, str) else raw
                except (TypeError, ValueError):
                    state = None
                if not isinstance(state, dict):
                    message = "竞技场状态丢失（游戏页可能已刷新）"
                    break
                logs = state.get("log") or []
                if len(logs) < last_logs:
                    last_logs = 0
                for line in logs[last_logs:]:
                    self._log(str(line))
                last_logs = len(logs)
                try:
                    self._on_state(state)
                except Exception:  # noqa: BLE001
                    pass
                action = str(state.get("action") or "")
                if action and action != last_action:
                    last_action = action
                    self._log("    " + action)
                if state.get("running") is False:
                    error = str(state.get("error") or "")
                    ok = not error
                    message = error or ("已挑战 %d 次" % int(state.get("challenges") or 0))
                    break
                time.sleep(0.5)
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or exc.__class__.__name__
        finally:
            self.running = False
            self._on_finished(ok, message)
