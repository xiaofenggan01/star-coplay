# SMAPI 技术摸底总结

> 基于 3 个并行研究方向（输入控制、版本/Harmony、Starbot 源码分析）的综合结论。
> 面向 StardewAgent Adapter 层 Demo 的技术选型决策。

---

## 一、环境基线

| 项 | 值 | 备注 |
|----|-----|------|
| SMAPI | 4.5.2 (2026-03-14) | 最新稳定版 |
| 星露谷 | 1.6.14+ | String ID 系统、.NET 6 |
| .NET | 6.0 | 不要用 7/8 |
| Harmony | 2.2.x | SMAPI 内部管理，无需单独引用 |
| NuGet 包 | `Pathoschild.Stardew.ModBuildConfig 4.4.0` | 自动构建部署 |
| 游戏循环 | ~60 FPS | UpdateTicked 每帧触发 |
| 时间 | 10 游戏分钟 = 7 现实秒 | `gameTimeInterval` 累积 ms |

---

## 二、五大核心能力判定

### 1. 移动控制 — 三条路径，按需选择

| 方法 | 原理 | 可靠性 | 适用场景 |
|------|------|--------|---------|
| **PathFindController** (游戏内置) | 内置 A* + 自动移动角色 | ★★★★★ | 日常任务（采集、农耕、社交） |
| **Game1.warpFarmer()** | 跨地图传送 | ★★★★★ | 跨地图导航（需延迟 100ms） |
| **IInputSimulator 反射劫持** (Starbot) | 替换游戏输入系统 | ★★★☆☆ | 高精度任务（战斗、钓鱼） |
| **直接设 Position** | 瞬移 | ★★☆☆☆ | 调试/作弊模式（绕过碰撞） |
| **Helper.Input.Press()** | SMAPI 输入模拟 | ★★☆☆☆ | 不推荐（历史 bug 多） |

**Demo 决策**：PathFindController + warpFarmer。不碰输入模拟。

**PathFindController 用法**（来自 AutoPlaySV 验证）：
```csharp
var controller = new PathFindController(
    Game1.player,           // 角色
    Game1.currentLocation,  // 当前地图
    destination,            // 目标 Point
    -1,                     // 方向（-1=自动）
    null                    // 完成回调
);
// 每帧调用 controller.update() 即可
```

**warpFarmer 用法**：
```csharp
GameLocation loc = Utility.fuzzyLocationSearch("Farm");
DelayedAction.functionAfterDelay(() => {
    Game1.warpFarmer(
        new LocationRequest(loc.NameOrUniqueName, loc.uniqueName.Value != null, loc),
        tileX, tileY, facingDirection // 0=上 1=右 2=下 3=左
    );
}, 100); // 必须延迟
```

### 2. 状态读取 — 直接访问游戏对象

**已验证可靠的 API 路径**：

```csharp
// 全局状态
Game1.timeOfDay          // 26h 制时间 (0600-2600)
Game1.currentSeason      // "spring"/"summer"/"fall"/"winter"
Game1.dayOfMonth         // 1-28
Game1.isRaining / isSnowing / isLightning

// 玩家状态
Game1.player.Position    // Vector2 像素坐标
Game1.player.Tile        // Vector2 瓦片坐标 (1.6+ 新增)
Game1.player.Stamina     // 当前体力
Game1.player.Money       // 金钱
Game1.player.Items       // IList<Item> 背包
Game1.player.CurrentTool // 当前工具

// 地图内容
Game1.currentLocation.Objects          // 放置的物品/机器
Game1.currentLocation.terrainFeatures  // 作物/树/草
Game1.currentLocation.characters       // NPC 列表

// 上下文检查（Agent 必须用）
Context.IsWorldReady     // 存档已加载
Context.IsPlayerFree     // 无菜单、无过场
Context.CanPlayerMove    // 无工具使用中
```

### 3. 工具使用 — 两种方式

| 方式 | 实现 | 适用场景 |
|------|------|---------|
| **checkForAction()** | `object.checkForAction(Game1.player)` | 交互物品、NPC、机器 |
| **SwingTool()** | 输入模拟 Use Tool 键 + 30 帧计时 | 锄地、砍树、挖矿 |

**Demo 决策**：优先 `checkForAction()` 直接 API，工具使用后续再加。

**关键发现**：AutoPlaySV 直接调用 `animal.pet(Game1.player)` 和 `machine.checkForAction(Game1.player)` 完全可行，绕过输入系统更可靠。

### 4. 事件驱动 — Agent 的感知-执行循环

**核心事件选择**：

| 事件 | 频率 | Agent 用途 |
|------|------|-----------|
| `DayStarted` | 每天 1 次 | 每日规划：扫描农场、制定任务清单 |
| `TimeChanged` | 每 10 游戏分钟 | 时间驱动行为切换（上午→下午→晚上） |
| `OneSecondUpdateTicked` | 每秒 1 次 | 中频决策：路径检查、状态监控 |
| `UpdateTicked` | ~60 次/秒 | 高频执行：移动控制、工具使用 |
| `Warped` | 换地图时 | 上下文切换（农场→矿洞→城镇） |
| `InventoryChanged` | 背包变动时 | 物品获取/消耗追踪 |

**性能模式**：
```csharp
// 不要每 tick 做重计算
helper.Events.GameLoop.UpdateTicked += (s, e) => {
    if (!e.IsMultipleOf(60)) return; // 每秒只执行一次
    // 决策逻辑...
};
```

### 5. 数据持久化 — 三层存储

| 层 | API | 生命周期 | 用途 |
|----|-----|---------|------|
| `config.json` | `Helper.ReadConfig<T>()` | 跨存档 | Agent 配置参数 |
| Save Data | `Helper.Data.ReadSaveData<T>()` | 绑定存档 | Agent 学习记忆 |
| modData | `entity.modData["key"]` | 跟随实体 | 标记已处理的对象 |

---

## 三、关键技术发现（文档未覆盖）

### 发现 1：PathFindController 是游戏内置的 NPC 寻路系统

这是**最重要的发现**。游戏自己用来控制 NPC 移动的 `PathFindController` 类，**可以直接用来控制玩家角色**。这意味着：
- 不需要自己实现 A* 算法
- 不需要自己处理碰撞检测
- 不需要输入模拟来移动
- 游戏引擎保证移动的正确性

### 发现 2：SMAPI 4.4+ 新增 `helper.Input.Send()`

2026-01-10 发布的 SMAPI 4.4.0 新增了 `helper.Input.Send(button)` 方法，可以程序化向游戏发送按钮事件。这比旧的 `Press()` 更可靠，是官方推荐的输入模拟方式。

### 发现 3：Starbot 通过反射劫持 `Game1.inputSimulator`

Starbot 不使用 SMAPI 的输入 API，而是通过反射直接替换游戏的 `IInputSimulator` 接口实现。这提供了最底层的输入控制，但维护成本高。

### 发现 4：物品 ID 系统已迁移到字符串

星露谷 1.6 的最大 API 变化：物品 ID 从数字变为字符串。
```csharp
item.ItemId          // "Pufferfish" (新)，不再是 "128"
item.QualifiedItemId // "(O)Pufferfish" (全局唯一)
```

### 发现 5：失败恢复是 Bot 稳定性的关键

Starbot 实现了四层防御，AutoPlaySV 没有任何失败处理导致容易卡死。任何实用的 Bot 必须实现：
1. 卡住检测（300 帧 / 5 秒无移动）
2. 任务重试（最多 3 次）
3. 对话框自动关闭
4. 路径回退（四方向尝试）

---

## 四、Harmony 使用策略

**原则**：Harmony 是最后手段，SMAPI 原生 API 能解决的不用 Harmony。

| 场景 | 是否需要 Harmony | 替代方案 |
|------|-----------------|---------|
| 拦截玩家输入 | 否 | SMAPI Input 事件 + Suppress |
| 监控状态变化 | 否 | SMAPI World/Player 事件 |
| 修改游戏资源 | 否 | Content API |
| 修改移动速度 | 可能 | Harmony Postfix `getMovementSpeed()` |
| 拦截工具使用逻辑 | 可能 | Harmony Prefix `Tool.DoFunction()` |
| 修改时间流速 | 是 | Harmony `performTenMinuteClockUpdate()` |

**Demo 阶段**：不使用 Harmony。

---

## 五、Demo 技术选型决策

### 架构

```
┌─────────────────────────────────────────┐
│        StardewAgent Adapter 层           │
│                                          │
│  StateReader    ActionExecutor   Events  │
│  ├ GetTime()   ├ MoveTo()       ├ OnDay  │
│  ├ GetPlayer() ├ WarpTo()       ├ OnTime │
│  ├ GetMap()    ├ Interact()     └ OnWarp │
│  └ GetNPCs()   └ UseTool()              │
└──────────────┬──────────────────────────┘
               │ 直接调用
┌──────────────▼──────────────────────────┐
│     Game API (Game1 / Farmer / etc.)     │
│     PathFindController / warpFarmer      │
└──────────────────────────────────────────┘
```

### 不做的事

- ❌ 不用 `Helper.Input.Press()` — 历史 bug 多
- ❌ 不用 IInputSimulator 反射劫持 — 维护成本高
- ❌ 不用 Harmony — Demo 阶段不需要
- ❌ 不自己实现 A* — PathFindController 已内置
- ❌ 不做 Agent 决策层 — 仅 Adapter 以下

### 要做的事

- ✅ PathFindController 驱动移动
- ✅ warpFarmer 跨地图传送
- ✅ 直接 API 读取状态（Game1.*）
- ✅ checkForAction() 物品交互
- ✅ 事件驱动架构（DayStarted + UpdateTicked）
- ✅ 卡住检测 + 失败回退

---

## 六、待验证问题（需要实际编码验证）

| 问题 | 风险 | 验证方式 |
|------|------|---------|
| PathFindController 控制 Farmer 是否完全兼容 | 中 | Demo 实测 |
| warpFarmer 延迟 100ms 是否足够 | 低 | Demo 实测 |
| checkForAction 是否覆盖所有交互类型 | 中 | 逐个测试 |
| 1.6 String ID 在背包操作中的表现 | 低 | 代码验证 |
| UpdateTicked 中长时间操作是否阻塞游戏 | 高 | 性能测试 |

---

## 参考资料索引

| 研究报告 | 路径 | 核心内容 |
|---------|------|---------|
| 输入控制研究 | `research-input-control.md` | Press() 可靠性、直接 API vs 输入模拟、warpFarmer |
| 版本与 Harmony | `research-smapi-version-harmony.md` | SMAPI 4.5.2、.NET 6、Harmony 策略、时间机制 |
| Starbot 源码分析 | `research-starbot-analysis.md` | 三层移动、Objective 系统、失败恢复 |

*研究日期：2026-03-23*
