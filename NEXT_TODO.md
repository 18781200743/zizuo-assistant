# 当前状态 / 待办

## 已完成

- PC：红包、祈愿、复活、刷新、闪现、抽卡、战斗参数、运行命令（等待/点击/滚轮/移动/传送/回城/采集/开宝箱/多槽位/跳转/录制）已适配 4758a。
- 安卓：抽卡、运行命令（同上，含多槽位/跳转/录制）、悬浮球面板、夜间/白天。
- 采集：和参考工具一样是**事件等待**，不寻路、不瞬移；监听采集入口/采完事件，按实际掉落物品名匹配。PC 和安卓共用同一套映射字段。
- 4758a 开宝箱：PC 走 `app/chest_4758a.py`，安卓走 `runner.js` 里的同一套字段。
- 4758a 回城：优先主城地图传送 `LJCS(1,1)`，没反应再退回营地坐标。
- 功能玩法：跳一跳、农场（收菜/偷菜/施肥）已实现；日常任务/护符升级待实现。
- 死代码清理：
  - 旧指令库列表 UI、日常任务 UI/后端
  - 完整宝箱控制器（`_chest_controller` / `chest_bundle` / `app/chest_controller.js`）
  - 隐藏的旧「存档/录制」页、`TaskRunner` / `ClickRecorder` / `slots_store` / `script_codec`
- 打包：`tools/make_release.py` 已带上 `commands_presets.json`、`chest_aliases_4758a.json`，并验证 exe 内数据文件齐全。

## 传送（已通 4758a）

已确认的接口：

```
传送调用：GCOA.LJCS(mapId, pointId, callback)        （map.teleport = "LJCS"）
坐标瞬移：GCOA.JWDT(x, y, false, callback)            （revive.campTeleport.api = "JWDT"）
地图状态：GCOA.QESF.EJPP = 当前地图 id
```

- `automation_config.json → teleport_mappings` 里的 `map_id / teleport_id` 是旧版本编号，4758a 不认。
- PC 传送优先用面板点击（章节/地图名），失败再退回 `LJCS`；安卓目前按 `teleport_mappings` 调 `LJCS`。
- 新版本如果 `LJCS` / `JWDT` 改名，更新 `assist_mapping.json` 的 `map.teleport` / `revive.campTeleport.api` 即可。

## 开宝箱

- 4758a：PC 和安卓都按 `game_storage_sync` 的 boss_chest 流程做了**轻量版**：
  - 查 DNTD 服务次数 XMQR
  - 用 DVPV/LFWE/LHUO/NNPY/POBP 找 BOSS 掉落
  - 用 SARG 打开、WZMM 找面板、NMMA/KAQL 领取
  - 不在游戏页注入 40KB 控制器，避免主线程扫描导致 CDP 超时。
  - `app/chest_aliases_4758a.json` 保留作字段映射。
- f61cf：仍走旧的服务映射（`chest` 里的 HQQD/HDPD/DELV/KMYW/IVTK），未再验证。

## 新版本适配流程（每次游戏发版）

1. 识别新 build：`assets/mainScripts/index.<build>.js` 的哈希。
2. 对比旧/新客户端包，更新 `app/assist_mapping.json`：
   - 语义字段：`map` / `revive` / `combat` / `collect` / `chest` 等
   - `builds.<build>.runtimeNames`：运行时 JS 里旧名 → 新名
   - 安卓同步更新 `android_app/js/mapping.js` / `renames.js`
3. 跑检查：
   ```bash
   python tools/check_game_names.py --strict
   ```
   有未归类的全大写名字就会列出来，按提示收进映射或 `LOCAL_NAMES` / `DEAD_NAMES`。
4. 启动保护：
   - PC：`GameAssist.ensure_runtime()` 和运行命令遇到未知 build 会直接提示，不会拿旧名字硬跑。
   - 安卓：`doInject()` 和运行命令同样会检查 build 是否在 `ZZ_MAPPING.builds` 里。
5. 本次已收进映射的硬编码名：`chest`、`collect.entityId/collectible/removedFlags`、`clearStuck.flags/loadingHolder`、`confirmPopup.className/confirmMethod`、`map.unit.x/y/skillId`、`combat.damage.zeroField`。

## 安卓同步

- 安卓 `开宝箱()` 已按同一套 4758a 字段接入（`DNTD/XMQR/SUZK/WZMM/NMMA/KAQL`），支持数量和双倍。
- 安卓 `回城()` 先试主城 `LJCS(1,1)`，不行再退回营地。
- 安卓 `采集()` 和 PC 一样监听 `YHST.KCQB.PZQH/IPDY` 事件，按掉落名匹配；不寻路、不瞬移，5 秒没新动作就继续。
- PC/安卓采集都走 `assist_mapping.json` 的 `collect.*` 字段，不再写死旧版本名。
- 改完 `tools/gen_android_runner.py` 后重新生成 `runner.js`。

## 未完成 / 限制

- `game_storage_sync` 的公共指令库是服务器接口，目前只导入了本机自定义脚本 + 项目内置 45 条。
- 运行命令现在支持 10 个槽位、`跳转(槽位)`、鼠标/摇杆录制；不做分享码。
- 首页不再放「运行命令」按钮，入口在「辅助工具」页第 5 个按钮。
- f61cf 的开宝箱旧逻辑未重新验证。
