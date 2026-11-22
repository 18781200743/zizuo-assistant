"""4758a 开宝箱：参考 game_storage_sync 的 boss_chest 流程，但只保留最小步骤。

不注入它那份 40KB 控制器，避免在游戏主线程里扫描大量模块/场景导致 CDP 超时。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class _Names:
    def __init__(self) -> None:
        path = Path(__file__).resolve().parent / "chest_aliases_4758a.json"
        try:
            self.aliases: Dict[str, str] = json.loads(path.read_text(encoding="utf-8")).get("aliases") or {}
        except (OSError, ValueError):
            self.aliases = {}

    def __call__(self, alias: str) -> str:
        return str(self.aliases.get(alias) or alias)


N = _Names()


def _service_js() -> str:
    template = r"""
(function(){try{
  function isFull(s){return !!s&&typeof s[__Q__]==='function'&&typeof s[__C__]==='function'
    &&typeof s[__D__]==='function'&&isFinite(s[__R__])&&isFinite(s[__T__]);}
  function isLoose(s){return !!s&&typeof s[__Q__]==='function'&&typeof s[__C__]==='function';}
  function pick(v){
    if(!v)return null;
    if(isFull(v)||isLoose(v))return v;
    try{var i=v.IZBA; if(i&&(isFull(i)||isLoose(i)))return i;}catch(e){}
    return null;
  }
  function finish(svc,where){
    window.__zzChestSvc=svc;window.__zzChestQuota=null;
    svc[__Q__]().then(function(r){var info=r&&r[__INFO__];
      window.__zzChestQuota={ok:true,total:info?info[__TOTAL__]:0,used:info?info[__USED__]:0,
        remaining:info?(info[__TOTAL__]-info[__USED__]):0,
        code:r&&r[__RESULT__]&&r[__RESULT__][__CODE__]};},
      function(e){window.__zzChestQuota={ok:false,error:String((e&&e.message)||e)};});
    window.__zzChestWhere=where;
  }
  function scanModule(m){
    if(!m)return false;
    var keys=['DNTD','IZBA'];
    for(var i=0;i<keys.length;i++){
      var v=null;try{v=m[keys[i]];}catch(e){}
      var s=pick(v); if(s){finish(s,'module/'+keys[i]);return true;}
    }
    var names=[];try{names=Object.getOwnPropertyNames(m);}catch(e){}
    for(var n=0;n<names.length;n++){
      var v2=null;try{v2=m[names[n]];}catch(e){}
      var s2=pick(v2); if(s2){finish(s2,'module/'+names[n]);return true;}
    }
    return false;
  }
  function scanEntries(){
    var hints=[];
    var pairs=[];try{pairs=[...System.entries()];}catch(e){pairs=[];}
    for(var pi=0;pi<pairs.length;pi++){
      var pair=pairs[pi],ex=pair[1];
      var bags=[[ex,'ex'],[ex&&ex.fw_gen,'fw']];
      for(var bi=0;bi<bags.length;bi++){
        var bag=bags[bi][0];if(!bag)continue;
        var direct=null;try{direct=bag[__F__];}catch(e){}
        var ds=pick(direct); if(ds){finish(ds,pair[0]+'/'+bags[bi][1]);return true;}
        var names=[];try{names=Object.getOwnPropertyNames(bag);}catch(e){}
        for(var ni=0;ni<names.length;ni++){
          var v=null;try{v=bag[names[ni]];}catch(e){}
          var vs=pick(v); if(vs){finish(vs,pair[0]+'/'+bags[bi][1]+'/'+names[ni]);return true;}
          if(v&&(typeof v[__Q__]==='function'||typeof v[__C__]==='function'||v.IZBA)){
            hints.push(pair[0]+':'+names[ni]);
          }
        }
      }
    }
    window.__zzChestQuota={ok:false,error:'宝箱服务未就绪｜候选 '+JSON.stringify(hints.slice(0,4))};
    return false;
  }
  var done=false;
  function finishScan(m){
    if(done)return; done=true;
    if(m)window.__zzChestModule=m;
    if(m&&scanModule(m))return;
    scanEntries();
  }
  if(window.__zzChestModule && scanModule(window.__zzChestModule)) return 'ok';
  if(window.System && typeof System.import==='function'){
    System.import('chunks:///_virtual/'+__MOD__+'.ts').then(finishScan,function(){finishScan(null);});
    setTimeout(function(){finishScan(null);},1500);
  }else{
    finishScan(null);
  }
  return 'ok';
}catch(e){return 'ERR '+((e&&e.message)||e)}})()
"""
    replacements = {
        "__Q__": _js(N("bx_chaCishu")),
        "__C__": _js(N("bx_lingqu")),
        "__D__": _js(N("bx_chaXiangqing")),
        "__R__": _js(N("bx_shengyu")),
        "__T__": _js(N("bx_shangxian")),
        "__INFO__": _js(N("bx_cishuXinxi")),
        "__TOTAL__": _js(N("bx_cishuZong")),
        "__USED__": _js(N("bx_yiLing")),
        "__RESULT__": _js(N("jg_xinxi")),
        "__CODE__": _js(N("jg_jieguoMa")),
        "__F__": _js(N("bx_fuwu")),
        "__MOD__": _js(N("bx_fuwu")),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def _scan_js() -> str:
    return (
        "(function(){try{"
        "var G=window[%s],V=G&&G[%s],M=G&&G[%s],C=M&&M[%s],U=C&&C[%s];"
        "if(!V||!U)return 'ERR 地图实体未就绪';"
        "var comp=null,types=null;"
        "try{var cm=System.get('chunks:///_virtual/%s.ts');if(cm)comp=cm[%s];}catch(e){}"
        "try{var tm=System.get('chunks:///_virtual/%s.ts');if(tm)types=tm[%s];}catch(e){}"
        "if(!comp)for(var p1 of System.entries()){var e1=p1[1];if(e1&&e1[%s]){comp=e1[%s];break;}}"
        "if(!types)for(var p2 of System.entries()){var e2=p2[1];if(e2&&e2[%s]){types=e2[%s];break;}}"
        "if(!comp||!types)return 'ERR 宝箱组件未就绪';"
        "var data=V[%s](comp)||{},bossType=types[%s],rows=[];"
        "var iter=typeof U.values==='function'?U.values():U.entries();"
        "for(var item of iter){var unit=Array.isArray(item)?item[1]:item;"
        "var id=unit[%s]&&unit[%s][%s];"
        "if(!isFinite(id)||id<=0)continue;"
        "if(data[%s][id]!==bossType)continue;"
        "if(!V[%s](id,comp))continue;"
        "if(!(data[%s][id]>0))continue;"
        "rows.push({id:id,boss:data[%s][id],src:data[%s][id],idx:data[%s][id]});"
        "}"
        "return JSON.stringify(rows);"
        "}catch(e){return 'ERR '+((e&&e.message)||e)}})()"
    ) % (
        _js(N("dt_fuwu")), _js(N("dt_shitiShijie")), _js(N("dt_moxing")),
        _js(N("dt_danweiShituRongqi")), _js(N("dt_danweiShituBiao")),
        N("bx_shitiZujianGen"), _js(N("bx_shitiZujian")),
        N("bx_shitiLeixingGen"), _js(N("bx_shitiLeixingMeiju")),
        _js(N("bx_shitiZujian")), _js(N("bx_shitiZujian")),
        _js(N("bx_shitiLeixingMeiju")), _js(N("bx_shitiLeixingMeiju")),
        _js(N("dt_chaShitiZujian")), _js(N("bx_shitiShoulingLeixing")),
        _js(N("dt_danweiShiti")), _js(N("dt_danweiShiti")), _js(N("dt_shitiBianhao")),
        _js(N("bx_shitiLeixing")),
        _js(N("dt_shitiHanZujian")),
        _js(N("bx_shitiShoulingId")),
        _js(N("bx_shitiShoulingId")), _js(N("bx_shitiDiaoluoShili")),
        _js(N("bx_shitiDiaoluoSuoyin")),
    )


def _open_js(drop: Dict[str, Any]) -> str:
    return (
        "(function(){try{"
        "var G=window[%s],V=G&&G[%s],M=G&&G[%s],C=M&&M[%s],U=C&&C[%s];"
        "var want=%d;"
        "var iter=typeof U.values==='function'?U.values():U.entries();"
        "for(var item of iter){var unit=Array.isArray(item)?item[1]:item;var id=unit[%s]&&unit[%s][%s];"
        "if(id===want){if(typeof unit[%s]==='function'){unit[%s]();return 'ok';}return 'ERR 交互入口不可用';}}"
        "return 'ERR 单位不存在';"
        "}catch(e){return 'ERR '+((e&&e.message)||e)}})()"
    ) % (
        _js(N("dt_fuwu")), _js(N("dt_shitiShijie")), _js(N("dt_moxing")),
        _js(N("dt_danweiShituRongqi")), _js(N("dt_danweiShituBiao")),
        int(drop.get("id") or 0),
        _js(N("dt_danweiShiti")), _js(N("dt_danweiShiti")), _js(N("dt_shitiBianhao")),
        _js(N("dt_danweiJiaohu")), _js(N("dt_danweiJiaohu")),
    )


def _find_view_js(drop: Dict[str, Any]) -> str:
    return (
        "(function(){try{"
        "var scene=cc.director.getScene(),found=null;"
        "var boss=%s,src=%s,idx=%s;"
        "(function walk(n){if(!n||found)return;"
        "var cs=n.components||n._components||[];"
        "for(var i=0;i<cs.length;i++){var c=cs[i],info=c&&c[%s];"
        "if(info&&info[%s]===true&&info[%s]===boss"
        "&&(!src||info[%s]===src)&&(!idx||info[%s]===idx)){found=c;break;}}"
        "(n.children||[]).forEach(walk);})(scene);"
        "window.__zzChestView=found;return found?'ok':'none';"
        "}catch(e){return 'ERR '+((e&&e.message)||e)}})()"
    ) % (
        _js(int(drop.get("boss") or 0)), _js(drop.get("src")), _js(drop.get("idx")),
        _js(N("bx_yulanXinxi")), _js(N("bx_shifouBoss")), _js(N("bx_laiyuanBoss")),
        _js(N("bx_yulanDiaoluoShili")), _js(N("bx_yulanDiaoluoSuoyin")),
    )


def _claim_js(double: bool) -> str:
    return (
        "(function(){try{"
        "var v=window.__zzChestView;if(!v)return 'ERR 宝箱面板不存在';"
        "window.__zzChestClaim={status:'pending'};"
        "var useDouble=%s&&v[%s]&&v[%s].node&&v[%s].node.active===true;"
        "var p=useDouble&&typeof v[%s]==='function'?v[%s]():v[%s]();"
        "Promise.resolve(p).then(function(r){window.__zzChestClaim={status:'ok',raw:JSON.stringify(r||{}).slice(0,200)};},"
        "function(e){window.__zzChestClaim={status:'error',error:String((e&&e.message)||e)};});"
        "return useDouble?'double':'normal';"
        "}catch(e){return 'ERR '+((e&&e.message)||e)}})()"
    ) % (
        "true" if double else "false",
        _js(N("bx_shuangbeiAnniu")), _js(N("bx_shuangbeiAnniu")), _js(N("bx_shuangbeiAnniu")),
        _js(N("bx_shuangbeiLingqu")), _js(N("bx_shuangbeiLingqu")), _js(N("bx_putongLingqu")),
    )


def _wait_json(runner, expr: str, timeout: float) -> Dict[str, Any]:
    deadline = time.monotonic() + max(0.5, timeout)
    last: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        if runner._stop.is_set():
            raise RuntimeError("已停止")
        raw = runner._eval(expr)
        try:
            data = json.loads(str(raw) or "null")
        except ValueError:
            data = None
        if isinstance(data, dict):
            last = data
            if data.get("ok") is not None or data.get("status") in ("ok", "error"):
                return data
        time.sleep(0.2)
    return last


def run_chest_4758a(runner, limit: int, mode: str) -> None:
    runner._log_line("    宝箱：连接服务…")
    first = str(runner._eval(_service_js()) or "")
    if first.startswith("ERR"):
        raise RuntimeError("开宝箱失败：%s" % first[4:].strip())

    quota = _wait_json(runner, "JSON.stringify(window.__zzChestQuota||null)", 6.0)
    if not quota:
        raise RuntimeError("开宝箱失败：读取宝箱次数超时")
    if quota.get("ok") is False:
        raise RuntimeError("开宝箱失败：%s" % (quota.get("error") or "读取宝箱次数失败"))
    remaining = int(quota.get("remaining") or 0)
    total = int(quota.get("total") or 0)
    runner._log_line("    宝箱：剩余 %d/%d" % (remaining, total))
    if remaining <= 0:
        runner._log_line("    宝箱次数已用完")
        return

    raw = str(runner._eval(_scan_js()) or "")
    if raw.startswith("ERR"):
        raise RuntimeError("开宝箱失败：%s" % raw[4:].strip())
    try:
        drops: List[Dict[str, Any]] = json.loads(raw)
    except ValueError:
        drops = []
    if not drops:
        runner._log_line("    当前地图没有可开的 BOSS 掉落宝箱")
        return
    runner._log_line("    发现 %d 个 BOSS 掉落宝箱" % len(drops))

    double = mode in ("double", "auto")
    done = 0
    failures = 0
    for drop in drops:
        if limit and done >= limit:
            break
        if remaining <= 0:
            break
        if runner._stop.is_set():
            raise RuntimeError("已停止")
        opened = str(runner._eval(_open_js(drop)) or "")
        if opened.startswith("ERR"):
            runner._log_line("    跳过宝箱 %s：%s" % (drop.get("id"), opened[4:].strip()))
            failures += 1
            if failures >= 3:
                break
            continue
        view = ""
        for _ in range(16):
            time.sleep(0.25)
            view = str(runner._eval(_find_view_js(drop)) or "")
            if view == "ok":
                break
        if view != "ok":
            runner._log_line("    宝箱 %s 没有出现预览面板" % drop.get("id"))
            failures += 1
            if failures >= 3:
                break
            continue
        result = str(runner._eval(_claim_js(double)) or "")
        if result.startswith("ERR"):
            runner._log_line("    领取失败：%s" % result[4:].strip())
            failures += 1
            if failures >= 3:
                break
            continue
        claim = _wait_json(runner, "JSON.stringify(window.__zzChestClaim||null)", 6.0)
        if claim.get("status") == "ok":
            done += 1
            remaining = max(0, remaining - 1)
            runner._log_line("    开箱成功 %d 个（剩余 %d/%d）" % (done, remaining, total))
            runner._sleep(0.8)
        else:
            runner._log_line("    领取失败：%s" % (claim.get("error") or "未知原因"))
            failures += 1
            if failures >= 3:
                break
    runner._log_line("    共开箱 %d 个" % done)
