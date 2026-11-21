# 待办 / 交接（传送与采集）

## 已完成

- PC：红包、祈愿、复活、刷新、闪现、抽卡、日常、战斗参数、运行指令（等待/点击/滚轮/移动/回城）、采集已适配 4758a
- 安卓：同样的界面与功能，抽卡、运行指令（除传送/采集）、悬浮球面板、夜间/白天
- 采集：坐标改从实体 `point` 组件取 `x/y`；已采判定兼容 `AINJ` / `KHGB` / `HCQJ` / `UMMW`

## 传送（PC + 安卓都还没通）

### 已排除的路子

1. `world` 上找传送服务实例 —— 没有
2. `BIMP` 类 —— 只有类，`GCOA` 上没有实例
3. 模块命名空间 `System.entries()` —— 导出不可枚举
4. `TeleportSelectView` 控制器的 `HXDC.YXTI` —— 只是 UI 选中态（字符串数组）

### 已确认的接口

```
传送调用：GCOA.LJCS(mapId, pointId, callback)        （map.teleport = "LJCS"）
地图状态：GCOA.QESF.EJPP = 当前地图 id
```
`automation_config.json → teleport_mappings` 里的 `map_id / teleport_id` 是旧版本编号，4758a 不认。

### 下一步（推荐）

**面板点击式传送**（不依赖 id）：

1. 打开传送面板后，场景里有两个节点族：
   - `TeleportTypePageItemView-001 / -002 / …` ← 章节页签
   - `TeleportSelectItemView`（多个）← 地图/传送点条目
2. 模拟点击：找到目标 `TeleportTypePageItemView-XXX`，调用其组件 `BPNN._onTouchBegan/_onTouchEnded` + `node.emit('click')`
3. 再点对应的 `TeleportSelectItemView`
4. 等 `QESF.EJPP` 变成目标地图 id

参考实现：`__zzAssist.clickTeleportTrack()` 里已经有一模一样的点击套路（找 Label → 往上找 `BPNN` 组件 → 触发 touch）。

## 开宝箱（未做）

字段在 `_js_chest_targets` / `_js_chest_claim` 里写死旧名，要按 4758a 的 `map.unit` / 宝箱服务重新对。

## 安卓同步

等 PC 的传送和采集确认无误后，把 `js/runner.js` 里的传送/采集按同一套逻辑更新（`tools/gen_android_runner.py` 重新生成）。
