# StardewAgent Adapter 层能力扩展设计

> 版本: v1.0 | 日期: 2026-03-24
> 基于现有 Demo 代码,面向黑客松 MVP 场景的能力扩展方案

---

## 一、能力缺口分析

### MVP Demo 场景与当前支持度对比

| 场景 | 必需能力 | StateReader 现状 | ActionExecutor 现状 | 缺口 |
|------|---------|----------------|-------------------|------|
| **出门浇水** | 检测未浇水作物、批量浇水、水壶装水 | ✅ GetNearbyObjects 可查作物<br>✅ GetFarmSummary 有统计 | ❌ 无批量浇水<br>❌ 无装水功能 | P0 缺失 |
| **主动找话题** | 检测环境变化、获取时间/NPC 位置 | ✅ GetWorldState<br>✅ GetNearbyNpcs | ✅ 已有移动能力 | P1 轻微 |
| **手抖卖宝石 → AI 记住** | 背包变化事件、物品价值查询 | ✅ GetInventory | ❌ 无卖出 API | P0 缺失 |
| **挖到钻石 → AI 庆祝** | 背包变化事件、物品价值判断 | ✅ GetInventory<br>❌ 无事件推送 | — | P0 缺失事件 |
| **做收集包 → AI 提醒** | 收集包进度、缺失物料清单 | ❌ 无收集包查询 | — | P1 缺失 |

### 核心结论

**P0 级别缺口(MVP 必须)**:
1. **事件感知层** — 背包变化、时间变化、NPC 位置变化的主动推送
2. **ActionExecutor 工具使用** — SwingTool(浇水/锄地/砍树)
3. **ActionExecutor 物品操作** — 丢弃物品、出售物品
4. **StateReader 增强** — 物品价值查询、水壶状态、环境变化检测

**P1 级别缺口(加分项)**:
5. **收集包系统** — 进度查询、物料需求
6. **NPC 动态寻路** — 基于 NPC 当前位置的自动导航

---

## 二、StateReader 扩展设计

### 2.1 新增方法清单(按优先级)

#### P0-1: GetToolState()
```csharp
public ToolState GetToolState(string toolName)
```

**返回类型**:
```csharp
public class ToolState
{
    public string ToolName { get; set; }
    public bool InInventory { get; set; }
    public int SlotIndex { get; set; }  // -1 表示不在快捷栏
    public int UpgradeLevel { get; set; }  // 0=基础, 1=铜, 2=铁, 3=金, 4=铱

    // 浇水壶专用
    public int? WaterLevel { get; set; }
    public int? WaterCapacity { get; set; }
}
```

**实现路径**:
```csharp
// 查找背包中的工具
var tool = Game1.player.Items
    .OfType<Tool>()
    .FirstOrDefault(t => t.DisplayName == toolName);

if (tool is WateringCan can)
{
    return new ToolState {
        WaterLevel = can.WaterLeft,
        WaterCapacity = can.waterCanMax
    };
}

// 升级等级
tool.UpgradeLevel  // 0-4
```

**数据来源**: `Game1.player.Items` → `Tool.WaterLeft`, `Tool.UpgradeLevel`

---

#### P0-2: GetItemValue()
```csharp
public ItemValue GetItemValue(string itemName, int quality = 0)
```

**返回类型**:
```csharp
public class ItemValue
{
    public string ItemName { get; set; }
    public string ItemId { get; set; }
    public int BasePrice { get; set; }
    public int QualityMultiplier { get; set; }  // 1.0/1.25/1.5/2.0
    public int FinalPrice { get; set; }
    public string Category { get; set; }  // "Gem", "Crop", "Fish", etc.
    public bool IsValuable { get; set; }  // BasePrice >= 500
}
```

**实现路径**:
```csharp
// 通过 ItemId 查找物品数据
var itemData = ItemRegistry.GetData(itemId);
int basePrice = itemData.GetSellPrice();

// 品质乘数
float qualityMult = quality switch {
    1 => 1.25f,  // 银星
    2 => 1.5f,   // 金星
    4 => 2.0f,   // 铱星
    _ => 1.0f
};

// 职业加成(如果玩家有 Artisan 职业,加工品 +40%)
if (player.professions.Contains(/* Artisan ID */))
    finalPrice *= 1.4f;
```

**数据来源**: `ItemRegistry.GetData()`, `Item.salePrice()`

---

#### P0-3: GetEnvironmentChanges()
```csharp
public EnvironmentChanges GetEnvironmentChanges()
```

**返回类型**:
```csharp
public class EnvironmentChanges
{
    public List<InventoryChange> InventoryChanges { get; set; }
    public List<NpcMovement> NpcMovements { get; set; }
    public WeatherChange? WeatherChange { get; set; }
    public TimeChange? TimeChange { get; set; }
}

public class InventoryChange
{
    public ChangeType Type { get; set; }  // Added / Removed
    public string ItemName { get; set; }
    public int Quantity { get; set; }
    public int Quality { get; set; }
    public int Timestamp { get; set; }  // 游戏时间
}

public class NpcMovement
{
    public string NpcName { get; set; }
    public string FromLocation { get; set; }
    public string ToLocation { get; set; }
    public Point NewTile { get; set; }
}
```

**实现思路**:
```csharp
// 需要在 ModEntry 中维护历史快照
private List<InventorySlot> _lastInventorySnapshot;
private Dictionary<string, Point> _lastNpcPositions;

public EnvironmentChanges GetEnvironmentChanges()
{
    var changes = new EnvironmentChanges();

    // 比对背包变化
    var currentInv = GetInventory().Items;
    changes.InventoryChanges = CompareInventories(_lastInventorySnapshot, currentInv);
    _lastInventorySnapshot = currentInv;

    // 比对 NPC 位置
    var currentNpcs = GetNearbyNpcs(radius: 50);
    changes.NpcMovements = CompareNpcPositions(_lastNpcPositions, currentNpcs);
    // ...

    return changes;
}
```

**数据来源**: 差分当前状态与上一次快照

**注意**: 此方法需要在 ModEntry 的 OneSecondUpdateTicked 中定期更新快照

---

#### P1-1: GetCommunityCenterProgress()
```csharp
public CommunityCenterProgress GetCommunityCenterProgress()
```

**返回类型**:
```csharp
public class CommunityCenterProgress
{
    public bool IsComplete { get; set; }
    public List<BundleInfo> Bundles { get; set; }
}

public class BundleInfo
{
    public string RoomName { get; set; }  // "Pantry", "Crafts Room", etc.
    public string BundleName { get; set; }
    public bool IsComplete { get; set; }
    public List<BundleItem> RequiredItems { get; set; }
}

public class BundleItem
{
    public string ItemName { get; set; }
    public string ItemId { get; set; }
    public int RequiredQuantity { get; set; }
    public int CurrentQuantity { get; set; }  // 玩家已提交数量
    public int MinQuality { get; set; }
    public bool IsComplete { get; set; }
}
```

**实现路径**:
```csharp
var cc = Game1.getLocationFromName("CommunityCenter") as CommunityCenter;
if (cc == null) return null;

var bundles = new List<BundleInfo>();

// 遍历所有收集包
foreach (var bundle in cc.bundles.Pairs)
{
    int bundleId = bundle.Key;
    bool[] completed = bundle.Value;  // 每个物品的完成状态

    // 获取收集包数据
    var bundleData = Game1.netWorldState.Value
        .BundleData[bundleId.ToString()];

    // 解析收集包物品要求
    // bundleData 格式: "ItemId Quantity Quality/ItemId Quantity Quality/..."
    var items = ParseBundleData(bundleData, completed);

    bundles.Add(new BundleInfo {
        BundleName = GetBundleName(bundleId),
        RequiredItems = items,
        IsComplete = completed.All(c => c)
    });
}
```

**数据来源**: `CommunityCenter.bundles`, `Game1.netWorldState.Value.BundleData`

**难度**: ⭐⭐⭐ 中等(需要解析收集包数据格式)

---

#### P1-2: GetNearbyNpcs() 增强版
```csharp
public List<NpcInfo> GetNearbyNpcs(int radius = 20, bool includeSchedule = false)
```

**新增字段**:
```csharp
public class NpcInfo
{
    // ... 原有字段 ...

    // 新增
    public string CurrentActivity { get; set; }  // "walking", "standing", "working"
    public int FacingDirection { get; set; }  // 0=上 1=右 2=下 3=左
    public bool IsMoving { get; set; }
    public Point? NextScheduleLocation { get; set; }  // 下一个日程位置
    public int? NextScheduleTime { get; set; }  // 下一个日程时间(10 分钟制)
}
```

**实现路径**:
```csharp
foreach (var npc in location.characters)
{
    // 获取日程
    if (includeSchedule && npc.Schedule != null)
    {
        var currentTime = Game1.timeOfDay;
        var nextSchedule = npc.Schedule
            .Where(s => s.Key > currentTime)
            .OrderBy(s => s.Key)
            .FirstOrDefault();

        if (nextSchedule.Value != null)
        {
            info.NextScheduleLocation = nextSchedule.Value.targetTile;
            info.NextScheduleTime = nextSchedule.Key;
        }
    }

    info.IsMoving = npc.isMoving();
    info.FacingDirection = npc.FacingDirection;
}
```

**数据来源**: `NPC.Schedule`, `NPC.isMoving()`, `NPC.FacingDirection`

---

### 2.2 StateReader 扩展优先级总结

| 方法 | 优先级 | 估算代码量 | MVP 场景覆盖 |
|------|--------|----------|------------|
| `GetToolState()` | P0 | 30 行 | 出门浇水 |
| `GetItemValue()` | P0 | 40 行 | 手抖卖宝石 |
| `GetEnvironmentChanges()` | P0 | 80 行 | 挖到钻石、AI 庆祝 |
| `GetCommunityCenterProgress()` | P1 | 100 行 | 收集包提醒 |
| `GetNearbyNpcs()` 增强 | P1 | 20 行 | 主动找话题 |

---

## 三、ActionExecutor 扩展设计

### 3.1 新增方法清单(按优先级)

#### P0-1: SwingTool()
```csharp
public bool SwingTool(int tileX, int tileY)
```

**功能**: 在指定瓦片上挥动当前装备的工具(浇水/锄地/砍树/挖矿)

**实现思路**:
```csharp
public bool SwingTool(int tileX, int tileY)
{
    if (!Context.IsPlayerFree) return false;

    var player = Game1.player;
    var tool = player.CurrentTool;
    if (tool == null) return false;

    // 1. 检查是否相邻(工具使用距离限制)
    var playerTile = player.Tile;
    var distance = Vector2.Distance(playerTile, new Vector2(tileX, tileY));
    if (distance > 1.5f)
    {
        _monitor.Log($"SwingTool 失败: 距离过远({distance:F1})", LogLevel.Warn);
        return false;
    }

    // 2. 面向目标瓦片
    FaceTowardTile(tileX, tileY);

    // 3. 检查体力
    if (player.Stamina < 2)
    {
        _monitor.Log("SwingTool 失败: 体力不足", LogLevel.Warn);
        return false;
    }

    // 4. 调用工具的 DoFunction 方法
    tool.DoFunction(
        Game1.currentLocation,
        tileX * 64,  // 像素坐标 X
        tileY * 64,  // 像素坐标 Y
        1,           // power level
        player
    );

    _monitor.Log($"SwingTool: {tool.DisplayName} @ ({tileX},{tileY})", LogLevel.Info);
    return true;
}

private void FaceTowardTile(int tileX, int tileY)
{
    var player = Game1.player;
    int deltaX = tileX - (int)player.Tile.X;
    int deltaY = tileY - (int)player.Tile.Y;

    if (Math.Abs(deltaX) > Math.Abs(deltaY))
        player.FacingDirection = deltaX > 0 ? 1 : 3;  // 右:左
    else
        player.FacingDirection = deltaY > 0 ? 2 : 0;  // 下:上
}
```

**SMAPI API 依据**:
- `Tool.DoFunction(location, x, y, power, farmer)` — 公开方法,稳定 ⭐⭐⭐⭐
- `Farmer.Stamina` — 公开属性
- `Farmer.FacingDirection` — 公开属性

**返回增强**(可选):
```csharp
public class ToolUseResult
{
    public bool Success { get; set; }
    public string FailReason { get; set; }  // "TOO_FAR" / "NO_TOOL" / "NO_ENERGY"
    public int EnergyConsumed { get; set; }
    public TileEffect Effect { get; set; }  // "WATERED" / "TILLED" / "CHOPPED"
}
```

---

#### P0-2: WaterCrops()
```csharp
public WaterResult WaterCrops(List<Point> tiles = null, int maxCount = 999)
```

**功能**: 批量浇水,自动处理水壶容量和体力限制

**返回类型**:
```csharp
public class WaterResult
{
    public bool Success { get; set; }
    public int WateredCount { get; set; }
    public int SkippedAlreadyWatered { get; set; }
    public int SkippedNoCrop { get; set; }
    public string StopReason { get; set; }  // null / "NO_WATER" / "NO_ENERGY" / "MAX_COUNT"
    public int EnergyUsed { get; set; }
    public int WaterUsed { get; set; }
    public int WaterRemaining { get; set; }
}
```

**实现思路**:
```csharp
public WaterResult WaterCrops(List<Point> tiles = null, int maxCount = 999)
{
    var result = new WaterResult();
    var player = Game1.player;
    var location = Game1.currentLocation;

    // 1. 检查水壶
    var wateringCan = player.CurrentTool as WateringCan;
    if (wateringCan == null)
    {
        wateringCan = player.Items.OfType<WateringCan>().FirstOrDefault();
        if (wateringCan == null)
        {
            result.Success = false;
            result.StopReason = "NO_WATERING_CAN";
            return result;
        }
        EquipItem(player.Items.IndexOf(wateringCan));
    }

    // 2. 如果未指定瓦片,扫描农场所有未浇水耕地
    if (tiles == null)
    {
        tiles = ScanUnwateredCrops(location);
    }

    // 3. 按距离排序(最近的优先)
    tiles = tiles.OrderBy(t =>
        Vector2.Distance(player.Tile, new Vector2(t.X, t.Y))
    ).ToList();

    // 4. 逐个浇水
    foreach (var tile in tiles)
    {
        if (result.WateredCount >= maxCount)
        {
            result.StopReason = "MAX_COUNT";
            break;
        }

        // 检查水壶水量
        if (wateringCan.WaterLeft <= 0)
        {
            result.StopReason = "NO_WATER";
            break;
        }

        // 检查体力
        if (player.Stamina < 2)
        {
            result.StopReason = "NO_ENERGY";
            break;
        }

        // 检查是否已浇水
        if (location.terrainFeatures.TryGetValue(new Vector2(tile.X, tile.Y), out var feature))
        {
            if (feature is HoeDirt dirt)
            {
                if (dirt.state.Value == 1)  // 已浇水
                {
                    result.SkippedAlreadyWatered++;
                    continue;
                }

                if (dirt.crop == null)
                {
                    result.SkippedNoCrop++;
                    continue;
                }

                // 导航到相邻瓦片(如果不在范围内)
                if (Vector2.Distance(player.Tile, new Vector2(tile.X, tile.Y)) > 1.5f)
                {
                    if (!NavigateTo(tile))
                    {
                        continue;  // 无法到达,跳过
                    }
                    // 等待导航完成...
                    return result;  // 下一帧继续
                }

                // 挥动水壶
                if (SwingTool(tile.X, tile.Y))
                {
                    result.WateredCount++;
                    result.WaterUsed++;
                    result.EnergyUsed += 2;
                }
            }
        }
    }

    result.Success = true;
    result.WaterRemaining = wateringCan.WaterLeft;
    return result;
}

private List<Point> ScanUnwateredCrops(GameLocation location)
{
    var tiles = new List<Point>();
    foreach (var pair in location.terrainFeatures.Pairs)
    {
        if (pair.Value is HoeDirt dirt
            && dirt.crop != null
            && dirt.state.Value == 0)  // 干燥
        {
            tiles.Add(new Point((int)pair.Key.X, (int)pair.Key.Y));
        }
    }
    return tiles;
}
```

**SMAPI API 依据**:
- `WateringCan.WaterLeft` — 公开属性
- `HoeDirt.state.Value` — 0=干燥, 1=浇水
- `Tool.DoFunction()` — 浇水逻辑

**难度**: ⭐⭐⭐⭐ 中高(需处理移动状态机)

---

#### P0-3: RefillWateringCan()
```csharp
public bool RefillWateringCan()
```

**功能**: 自动寻找最近水源并装水

**实现思路**:
```csharp
public bool RefillWateringCan()
{
    var player = Game1.player;
    var location = Game1.currentLocation;

    // 1. 检查是否装备水壶
    var can = player.CurrentTool as WateringCan;
    if (can == null)
    {
        _monitor.Log("RefillWateringCan 失败: 未装备水壶", LogLevel.Warn);
        return false;
    }

    // 2. 查找最近的水源瓦片
    Point? waterSource = FindNearestWaterSource(location);
    if (waterSource == null)
    {
        _monitor.Log("RefillWateringCan 失败: 找不到水源", LogLevel.Warn);
        return false;
    }

    // 3. 导航到水源旁边
    if (Vector2.Distance(player.Tile, new Vector2(waterSource.Value.X, waterSource.Value.Y)) > 1.5f)
    {
        NavigateTo(waterSource.Value);
        return false;  // 下一帧继续
    }

    // 4. 面向水源
    FaceTowardTile(waterSource.Value.X, waterSource.Value.Y);

    // 5. 使用工具(自动装水)
    can.DoFunction(location,
        waterSource.Value.X * 64,
        waterSource.Value.Y * 64,
        1, player);

    _monitor.Log($"RefillWateringCan: 已装满水({can.WaterLeft}/{can.waterCanMax})", LogLevel.Info);
    return true;
}

private Point? FindNearestWaterSource(GameLocation location)
{
    var playerTile = Game1.player.Tile;
    Point? nearest = null;
    float minDist = float.MaxValue;

    // 扫描地图寻找水体瓦片
    for (int x = 0; x < location.Map.Layers[0].LayerWidth; x++)
    {
        for (int y = 0; y < location.Map.Layers[0].LayerHeight; y++)
        {
            if (location.isOpenWater(x, y))
            {
                float dist = Vector2.Distance(playerTile, new Vector2(x, y));
                if (dist < minDist)
                {
                    minDist = dist;
                    nearest = new Point(x, y);
                }
            }
        }
    }

    return nearest;
}
```

**SMAPI API 依据**:
- `GameLocation.isOpenWater(x, y)` — 检测水体瓦片
- `WateringCan.DoFunction()` — 靠近水源时自动装水

**难度**: ⭐⭐⭐ 中等

---

#### P0-4: DropItem()
```csharp
public bool DropItem(string itemName, int quantity = 1)
```

**功能**: 丢弃背包中的物品到地面

**实现思路**:
```csharp
public bool DropItem(string itemName, int quantity = 1)
{
    var player = Game1.player;
    var location = Game1.currentLocation;

    // 1. 在背包中查找物品
    var item = player.Items.FirstOrDefault(i =>
        i != null && i.DisplayName == itemName);

    if (item == null)
    {
        _monitor.Log($"DropItem 失败: 背包中无 '{itemName}'", LogLevel.Warn);
        return false;
    }

    // 2. 减少背包数量
    if (item.Stack > quantity)
    {
        item.Stack -= quantity;
    }
    else
    {
        quantity = item.Stack;
        player.Items[player.Items.IndexOf(item)] = null;  // 移除物品
    }

    // 3. 在玩家脚下生成掉落物
    var droppedItem = item.getOne();  // 克隆物品
    droppedItem.Stack = quantity;

    Game1.createItemDebris(
        droppedItem,
        player.getStandingPosition(),
        player.FacingDirection,
        location
    );

    _monitor.Log($"DropItem: 丢弃 {itemName} x{quantity}", LogLevel.Info);
    return true;
}
```

**SMAPI API 依据**:
- `Game1.createItemDebris()` — 生成掉落物
- `Item.getOne()` — 克隆物品实例

**难度**: ⭐⭐⭐ 中等

---

#### P0-5: SellItem()
```csharp
public bool SellItem(string itemName, int quantity = 1)
```

**功能**: 将物品放入出货箱(夜间自动出售)

**实现思路**:
```csharp
public bool SellItem(string itemName, int quantity = 1)
{
    var player = Game1.player;
    var farm = Game1.getFarm();

    // 1. 在背包中查找物品
    var item = player.Items.FirstOrDefault(i =>
        i != null && i.DisplayName == itemName);

    if (item == null)
    {
        _monitor.Log($"SellItem 失败: 背包中无 '{itemName}'", LogLevel.Warn);
        return false;
    }

    // 2. 查找出货箱
    var shippingBin = farm.objects.Values
        .FirstOrDefault(obj => obj.Name == "Shipping Bin");

    if (shippingBin == null)
    {
        _monitor.Log("SellItem 失败: 找不到出货箱", LogLevel.Warn);
        return false;
    }

    // 3. 导航到出货箱
    Point binTile = new Point((int)shippingBin.TileLocation.X,
                               (int)shippingBin.TileLocation.Y);
    if (Vector2.Distance(player.Tile, new Vector2(binTile.X, binTile.Y)) > 1.5f)
    {
        NavigateTo(binTile);
        return false;  // 下一帧继续
    }

    // 4. 将物品添加到出货箱
    var itemToShip = item.getOne();
    itemToShip.Stack = Math.Min(quantity, item.Stack);

    farm.getShippingBin(player).Add(itemToShip);

    // 5. 从背包移除
    if (item.Stack > quantity)
    {
        item.Stack -= quantity;
    }
    else
    {
        player.Items[player.Items.IndexOf(item)] = null;
    }

    _monitor.Log($"SellItem: 出售 {itemName} x{itemToShip.Stack}", LogLevel.Info);
    return true;
}
```

**SMAPI API 依据**:
- `Farm.getShippingBin(player)` — 获取出货箱集合
- `List<Item>.Add()` — 添加物品到出货箱

**难度**: ⭐⭐⭐⭐ 中高(需要导航逻辑)

---

#### P1-1: NavigateToNpc()
```csharp
public bool NavigateToNpc(string npcName)
```

**功能**: 自动导航到 NPC 当前位置

**实现思路**:
```csharp
public bool NavigateToNpc(string npcName)
{
    // 1. 查找 NPC
    var npc = Game1.getCharacterFromName(npcName);
    if (npc == null)
    {
        _monitor.Log($"NavigateToNpc 失败: 找不到 NPC '{npcName}'", LogLevel.Warn);
        return false;
    }

    // 2. 检查是否同一地图
    if (npc.currentLocation != Game1.currentLocation)
    {
        // 需要先跨地图传送
        WarpTo(npc.currentLocation.Name, (int)npc.Tile.X, (int)npc.Tile.Y);
        return false;
    }

    // 3. 导航到 NPC 相邻瓦片
    Point npcTile = new Point((int)npc.Tile.X, (int)npc.Tile.Y);

    // 尝试四个方向(随机顺序)
    var directions = new[] {
        new Point(0, 1), new Point(0, -1),
        new Point(1, 0), new Point(-1, 0)
    }.OrderBy(_ => new Random().Next());

    foreach (var dir in directions)
    {
        Point targetTile = new Point(npcTile.X + dir.X, npcTile.Y + dir.Y);
        if (NavigateTo(targetTile))
        {
            _monitor.Log($"NavigateToNpc: 前往 {npcName} @ ({targetTile.X},{targetTile.Y})", LogLevel.Info);
            return true;
        }
    }

    _monitor.Log($"NavigateToNpc 失败: 无法到达 {npcName} 附近", LogLevel.Warn);
    return false;
}
```

**SMAPI API 依据**:
- `Game1.getCharacterFromName(name)` — 全局 NPC 查找
- `NPC.currentLocation` — NPC 当前位置
- `NPC.Tile` — NPC 瓦片坐标

**难度**: ⭐⭐⭐⭐ 中高(NPC 可能移动)

---

### 3.2 ActionExecutor 扩展优先级总结

| 方法 | 优先级 | 估算代码量 | SMAPI 稳定性 | MVP 场景 |
|------|--------|----------|------------|---------|
| `SwingTool()` | P0 | 40 行 | ⭐⭐⭐⭐ | 出门浇水 |
| `WaterCrops()` | P0 | 80 行 | ⭐⭐⭐⭐ | 出门浇水 |
| `RefillWateringCan()` | P0 | 60 行 | ⭐⭐⭐⭐ | 出门浇水 |
| `DropItem()` | P0 | 30 行 | ⭐⭐⭐⭐ | 手抖卖宝石 |
| `SellItem()` | P0 | 50 行 | ⭐⭐⭐⭐ | 手抖卖宝石 |
| `NavigateToNpc()` | P1 | 40 行 | ⭐⭐⭐ | 主动找话题 |

---

## 四、事件感知层设计(新模块)

### 4.1 设计必要性

当前 Demo 采用**轮询模式**(每秒/每帧查询状态),无法支持 "AI 主动庆祝" 和 "AI 提醒物料" 场景。需要新增 **事件推送机制**。

### 4.2 EventWatcher.cs 模块设计

```csharp
namespace StardewAgentDemo
{
    /// <summary>
    /// 事件监听器 — 监控游戏状态变化并推送事件通知。
    /// </summary>
    public class EventWatcher
    {
        private readonly IMonitor _monitor;

        /*── 历史快照 ──*/
        private List<InventorySlot> _lastInventory;
        private int _lastTime;
        private string _lastWeather;
        private Dictionary<string, Point> _lastNpcPositions;

        /*── 事件回调 ──*/
        public event EventHandler<InventoryChangedEvent> OnInventoryChanged;
        public event EventHandler<TimeChangedEvent> OnTimeChanged;
        public event EventHandler<WeatherChangedEvent> OnWeatherChanged;
        public event EventHandler<NpcMovedEvent> OnNpcMoved;

        public EventWatcher(IMonitor monitor)
        {
            _monitor = monitor;
            _lastInventory = new List<InventorySlot>();
            _lastNpcPositions = new Dictionary<string, Point>();
        }

        /// <summary>
        /// 每秒调用一次,检测状态变化并触发事件。
        /// </summary>
        public void Update(StateReader state)
        {
            CheckInventoryChanges(state);
            CheckTimeChanges(state);
            CheckWeatherChanges(state);
            CheckNpcMovements(state);
        }

        private void CheckInventoryChanges(StateReader state)
        {
            var currentInv = state.GetInventory().Items;

            // 检测新增物品
            foreach (var item in currentInv)
            {
                var prev = _lastInventory.FirstOrDefault(i => i.ItemId == item.ItemId);
                if (prev == null)
                {
                    // 全新物品
                    OnInventoryChanged?.Invoke(this, new InventoryChangedEvent
                    {
                        ChangeType = ChangeType.Added,
                        ItemName = item.Name,
                        Quantity = item.Stack,
                        Quality = item.Quality
                    });
                }
                else if (item.Stack > prev.Stack)
                {
                    // 数量增加
                    OnInventoryChanged?.Invoke(this, new InventoryChangedEvent
                    {
                        ChangeType = ChangeType.Increased,
                        ItemName = item.Name,
                        Quantity = item.Stack - prev.Stack,
                        Quality = item.Quality
                    });
                }
            }

            // 检测移除物品
            foreach (var prev in _lastInventory)
            {
                var current = currentInv.FirstOrDefault(i => i.ItemId == prev.ItemId);
                if (current == null)
                {
                    OnInventoryChanged?.Invoke(this, new InventoryChangedEvent
                    {
                        ChangeType = ChangeType.Removed,
                        ItemName = prev.Name,
                        Quantity = prev.Stack,
                        Quality = prev.Quality
                    });
                }
            }

            _lastInventory = currentInv;
        }

        private void CheckTimeChanges(StateReader state)
        {
            var world = state.GetWorldState();
            if (world.Time != _lastTime)
            {
                OnTimeChanged?.Invoke(this, new TimeChangedEvent
                {
                    OldTime = _lastTime,
                    NewTime = world.Time,
                    TimeOfDay = GetTimeOfDay(world.Time)
                });
                _lastTime = world.Time;
            }
        }

        private void CheckWeatherChanges(StateReader state)
        {
            var world = state.GetWorldState();
            if (world.Weather != _lastWeather && _lastWeather != null)
            {
                OnWeatherChanged?.Invoke(this, new WeatherChangedEvent
                {
                    OldWeather = _lastWeather,
                    NewWeather = world.Weather
                });
            }
            _lastWeather = world.Weather;
        }

        private void CheckNpcMovements(StateReader state)
        {
            var npcs = state.GetNearbyNpcs(radius: 50);

            foreach (var npc in npcs)
            {
                var key = npc.Name;
                if (_lastNpcPositions.TryGetValue(key, out var oldPos))
                {
                    var newPos = new Point(npc.TileX, npc.TileY);
                    if (oldPos != newPos)
                    {
                        OnNpcMoved?.Invoke(this, new NpcMovedEvent
                        {
                            NpcName = npc.Name,
                            OldTile = oldPos,
                            NewTile = newPos
                        });
                        _lastNpcPositions[key] = newPos;
                    }
                }
                else
                {
                    _lastNpcPositions[key] = new Point(npc.TileX, npc.TileY);
                }
            }
        }

        private string GetTimeOfDay(int time)
        {
            if (time < 1200) return "morning";
            if (time < 1800) return "afternoon";
            return "evening";
        }
    }

    /*────────────────────────────────────────────
     *  事件数据模型
     *────────────────────────────────────────────*/

    public enum ChangeType { Added, Removed, Increased, Decreased }

    public class InventoryChangedEvent : EventArgs
    {
        public ChangeType ChangeType { get; set; }
        public string ItemName { get; set; }
        public int Quantity { get; set; }
        public int Quality { get; set; }
    }

    public class TimeChangedEvent : EventArgs
    {
        public int OldTime { get; set; }
        public int NewTime { get; set; }
        public string TimeOfDay { get; set; }  // "morning" / "afternoon" / "evening"
    }

    public class WeatherChangedEvent : EventArgs
    {
        public string OldWeather { get; set; }
        public string NewWeather { get; set; }
    }

    public class NpcMovedEvent : EventArgs
    {
        public string NpcName { get; set; }
        public Point OldTile { get; set; }
        public Point NewTile { get; set; }
    }
}
```

### 4.3 事件通知机制选择

| 方案 | 实现 | 优点 | 缺点 |
|------|------|------|------|
| **C# 事件回调** | `event EventHandler<T>` | 简单,类型安全 | 仅限 Mod 内部 |
| **队列缓冲** | `Queue<Event>` | 可批量处理,可序列化 | 需要轮询队列 |
| **外部推送** | HTTP Webhook | 可跨进程 | 需要网络服务 |

**推荐**: P0 阶段使用 **C# 事件回调**,后续接入 LLM 时通过队列序列化为 JSON。

### 4.4 EventWatcher vs GetEnvironmentChanges 关系

**EventWatcher** = 推送模型（C# 事件回调，实时通知变化）
**GetEnvironmentChanges()** = 拉取模型（按需查询，返回差分快照）

MVP 阶段 **只需 EventWatcher**。理由：
- 推送模型更适合 "AI 主动庆祝" 和 "AI 提醒物料" 场景（需要即时响应）
- `GetEnvironmentChanges()` 可在接入 LLM 后再实现（LLM 需要结构化查询接口）
- 将 `GetEnvironmentChanges()` 从 P0 **降级为 P1**

### 4.5 与 DebugReporter 联动

EventWatcher 的所有事件应自动记录到 DebugReporter（见 `design-测试基础设施.md` §八），确保测试报告包含完整的事件日志。

---

## 五、与现有架构的集成

### 5.1 ModEntry.cs 完整集成视图

> 合并测试基础设施（`design-测试基础设施.md`）和本方案的所有改动。

```csharp
public class ModEntry : Mod
{
    /*── 核心模块 ──*/
    private StateReader _state = null!;
    private ActionExecutor _actions = null!;
    private StuckDetector _stuckDetector = null!;

    /*── Adapter 扩展 ──*/
    private EventWatcher _eventWatcher = null!;

    /*── 测试基础设施 ──*/
    private DebugReporter _reporter = null!;
    private TestCommands _testCommands = null!;
    private HudOverlay _hudOverlay = null!;

    public override void Entry(IModHelper helper)
    {
        _config = helper.ReadConfig<ModConfig>();

        // 1. 核心模块
        _state = new StateReader(Monitor);
        _actions = new ActionExecutor(Monitor);
        _stuckDetector = new StuckDetector(Monitor, _config.StuckThresholdTicks);

        // 2. Adapter 扩展
        _eventWatcher = new EventWatcher(Monitor);

        // 3. 测试基础设施（依赖 1+2）
        _reporter = new DebugReporter(Monitor, helper);
        _testCommands = new TestCommands(Monitor, _state, _actions, _reporter);
        _testCommands.Register(helper.ConsoleCommands);
        _hudOverlay = new HudOverlay(Monitor, _state, _actions, _stuckDetector);
        _hudOverlay.Register(helper);

        // 4. 事件订阅
        _eventWatcher.OnInventoryChanged += OnInventoryChanged;
        _eventWatcher.OnTimeChanged += OnTimeChanged;

        // 5. EventWatcher → DebugReporter 联动
        _eventWatcher.OnInventoryChanged += (s, e) =>
            _reporter?.RecordAction($"Event.Inventory.{e.ChangeType}",
                new { item = e.ItemName, qty = e.Quantity }, "event", DateTime.Now);

        // 6. SMAPI 事件
        helper.Events.GameLoop.UpdateTicked += OnUpdateTicked;
        helper.Events.GameLoop.OneSecondUpdateTicked += OnOneSecondTicked;
        helper.Events.GameLoop.DayStarted += OnDayStarted;
        helper.Events.GameLoop.TimeChanged += OnTimeChangedSMAPI;
        helper.Events.GameLoop.ReturnedToTitle += OnReturnedToTitle;
        helper.Events.Player.Warped += OnWarped;
        helper.Events.Player.InventoryChanged += OnInventoryChangedSMAPI;
        helper.Events.GameLoop.SaveLoaded += OnSaveLoaded;

        Monitor.Log("StardewAgent Demo 已加载", LogLevel.Info);
    }

    private void OnOneSecondTicked(object? sender, OneSecondUpdateTickedEventArgs e)
    {
        if (!Context.IsWorldReady || !_demoRunning) return;

        // 新增: 更新事件监听器
        _eventWatcher.Update(_state);

        // 卡住检测
        if (_stuckDetector.IsStuck)
        {
            Monitor.Log("检测到卡住!尝试恢复...", LogLevel.Warn);
            _actions.CancelCurrent();
            _stuckDetector.Reset();
        }
    }

    // 事件处理器
    private void OnInventoryChanged(object? sender, InventoryChangedEvent e)
    {
        if (e.ChangeType == ChangeType.Added)
        {
            // 检测是否是贵重物品
            var value = _state.GetItemValue(e.ItemName, e.Quality);
            if (value.IsValuable)
            {
                Monitor.Log($"[事件] 获得贵重物品: {e.ItemName} (价值 {value.FinalPrice}g)", LogLevel.Alert);
                // → 通知 AI 层
            }
        }
    }

    private void OnTimeChanged(object? sender, TimeChangedEvent e)
    {
        Monitor.Log($"[事件] 时间变化: {e.OldTime} → {e.NewTime} ({e.TimeOfDay})", LogLevel.Trace);
    }
}
```

### 5.2 StuckDetector 联动

当前 `StuckDetector` 已实现卡住检测(300 帧),可直接复用。在 ActionExecutor 的长时间操作(如 WaterCrops)中,每帧检查卡住状态:

```csharp
// ActionExecutor.cs
public void Tick()
{
    switch (_currentState)
    {
        case ActionState.Watering:
            // 检查是否卡住(通过外部注入的 StuckDetector)
            if (_stuckDetector?.IsStuck == true)
            {
                _monitor.Log("Watering 被卡住,取消操作", LogLevel.Warn);
                CancelCurrent();
                return;
            }
            TickWatering();
            break;
    }
}
```

### 5.3 命名空间和文件组织

```
StardewAgentDemo/
├── ModEntry.cs            # 入口,事件挂载,总初始化
├── StateReader.cs         # 状态查询(+5 个方法,含原有数据模型)
├── ActionExecutor.cs      # 动作执行(+6 个方法)
├── StuckDetector.cs       # 卡住检测(无改动)
├── EventWatcher.cs        # 新增: 事件监听器 + 事件数据模型
├── DisplayHelper.cs       # 新增: 共享格式化工具(FormatTime, GetDirectionName)
├── TestCommands.cs        # 新增: 控制台测试命令
├── HudOverlay.cs          # 新增: HUD 状态面板
├── DebugReporter.cs       # 新增: 调试报告生成器 + 报告数据模型
└── manifest.json
```

**数据模型位置约定**：
- StateReader 的查询返回类型（WorldState, PlayerState, InventorySlot, NpcInfo, ToolState, ItemValue 等）→ 放在 `StateReader.cs` 同文件底部（已有惯例）
- ActionExecutor 的结果类型（WaterResult 等）→ 放在 `ActionExecutor.cs` 同文件底部
- EventWatcher 的事件类型（InventoryChangedEvent, TimeChangedEvent 等）→ 放在 `EventWatcher.cs` 同文件底部
- DebugReporter 的报告类型（TestReport, ActionLogEntry 等）→ 放在 `DebugReporter.cs` 同文件底部

> 保持单文件内聚，不拆 Models/ 目录。理由：Mod 总代码量 < 2000 行，拆目录增加导航成本。

---

## 六、实现优先级与工作量估算

### 6.1 分阶段实施计划

| 阶段 | 模块 | 功能 | 代码量 | 时间估算 |
|------|------|------|--------|---------|
| **Phase 1** (P0 核心) | StateReader | GetToolState、GetItemValue | 70 行 | 1h |
| | ActionExecutor | SwingTool、RefillWateringCan | 100 行 | 2h |
| | ActionExecutor | DropItem、SellItem | 80 行 | 1.5h |
| **Phase 2** (P0 批量) | ActionExecutor | WaterCrops(完整版) | 120 行 | 3h |
| | EventWatcher | 背包/时间事件监听 | 150 行 | 2h |
| | ModEntry | 事件集成 | 50 行 | 1h |
| **Phase 3** (P1 增强) | StateReader | GetEnvironmentChanges | 100 行 | 2h |
| | StateReader | GetCommunityCenterProgress | 120 行 | 3h |
| | ActionExecutor | NavigateToNpc | 50 行 | 1h |

### 6.2 P0 MVP 最小集(15 小时内完成)

**必须实现** (支持 5 个 Demo 场景):
1. StateReader: `GetToolState()`, `GetItemValue()`
2. ActionExecutor: `SwingTool()`, `WaterCrops()`, `RefillWateringCan()`, `DropItem()`, `SellItem()`
3. EventWatcher: 背包变化事件

**可暂缓**:
- `GetCommunityCenterProgress()` — 收集包场景为 P1
- `NavigateToNpc()` — 可用 WarpTo 替代
- `GetEnvironmentChanges()` — 可用 EventWatcher 替代

### 6.3 技术债务与后续优化

| 技术债务 | 影响 | 优化方案 |
|---------|------|---------|
| WaterCrops 使用同步导航 | 性能差,阻塞游戏 | 改用异步状态机(类似 Starbot) |
| EventWatcher 每秒轮询 | CPU 开销 | 改用 SMAPI 原生事件订阅 |
| 缺少失败重试机制 | 容错能力弱 | 参考 Starbot 的 3 次重试 + 回退 |
| 无路径缓存 | 重复寻路计算 | 添加 `Dictionary<(Point,Point), List<Point>>` |

---

## 七、风险与约束

### 7.1 SMAPI API 已知限制

| 限制 | 影响 | 缓解方案 |
|------|------|---------|
| PathFindController 无回调 | 无法精确判断到达时间 | 每帧检查 `player.controller == null` |
| Tool.DoFunction 无返回值 | 无法确认工具使用成功 | 通过后续状态查询验证(如检查 `HoeDirt.state`) |
| 收集包数据格式复杂 | 解析困难 | 先硬编码 Spring 季节的前 3 个收集包 |
| NPC.Schedule 可能为 null | 崩溃风险 | 加 null 检查,日程缺失时返回空列表 |

### 7.2 多人模式行为差异

| 场景 | 单人模式 | 多人模式 | Adapter 层策略 |
|------|---------|---------|---------------|
| 出货箱 | 全局唯一 | 每个农场一个 | 查找 `Game1.getFarm()` |
| 收集包 | 全局共享 | 房主农场独有 | 检查 `Game1.IsMasterGame` |
| NPC 位置 | 固定日程 | 可能被其他玩家影响 | 实时查询,不缓存 |
| 工具使用 | 同步执行 | 需要网络同步 | 无需特殊处理(SMAPI 自动同步) |

### 7.3 性能影响

| 操作 | 每帧开销 | 优化建议 |
|------|---------|---------|
| ScanUnwateredCrops | O(n) 遍历所有 terrainFeatures | 仅在 DayStarted 时扫描一次,缓存结果 |
| FindNearestWaterSource | O(w×h) 遍历整个地图 | 限制搜索半径为 20 瓦片,或预计算农场水源位置 |
| GetCommunityCenterProgress | O(bundles × items) | 仅在需要时调用,不放在 Tick 中 |
| EventWatcher.Update | O(items + npcs) | 已优化(每秒一次),可接受 |

---

## 八、验证与测试策略

### 8.1 单元测试思路

虽然 SMAPI Mod 难以单元测试,但可以通过 **隔离纯逻辑** 来测试:

```csharp
// 可测试: 纯函数逻辑
public static class InventoryDiffer
{
    public static List<InventoryChange> Compare(
        List<InventorySlot> old,
        List<InventorySlot> current)
    {
        // ... 差分逻辑 ...
    }
}

// 难以测试: 依赖 Game1 的方法
public bool SwingTool(int x, int y)
{
    var tool = Game1.player.CurrentTool;  // 依赖全局状态
    // ...
}
```

**测试策略**:
1. **纯函数部分** → 单元测试(xUnit)
2. **游戏集成部分** → 手动测试 + 日志验证

### 8.2 手动测试清单

| 功能 | 测试步骤 | 预期结果 |
|------|---------|---------|
| **SwingTool** | 装备水壶,面向干燥耕地,调用 SwingTool | 耕地变湿,水壶水量 -1,体力 -2 |
| **WaterCrops** | 农场有 10 块未浇水耕地,调用 WaterCrops | 全部浇水,返回 WateredCount=10 |
| **RefillWateringCan** | 水壶空,靠近池塘,调用 RefillWateringCan | 自动装满水,WaterLeft = WaterCapacity |
| **DropItem** | 背包有 5 个 Parsnip,调用 DropItem("Parsnip", 2) | 地上出现 2 个 Parsnip,背包剩 3 个 |
| **SellItem** | 背包有钻石,调用 SellItem("Diamond", 1) | 出货箱增加 1 个钻石,第二天收到钱 |
| **EventWatcher** | 采集 1 个野生浆果 | 触发 OnInventoryChanged 事件,日志输出 "获得: Wild Horseradish" |

### 8.3 失败案例准备

**预期失败场景**(必须优雅处理):
1. **SwingTool 距离过远** → 返回 false,日志 "距离过远"
2. **WaterCrops 水壶没水** → 返回 StopReason="NO_WATER",已浇部分计入 WateredCount
3. **NavigateToNpc 时 NPC 移动** → 重新查找 NPC 位置,最多重试 3 次
4. **DropItem 背包已满** → 无需处理(丢弃始终成功)
5. **SellItem 找不到出货箱** → 返回 false,日志 "找不到出货箱"

---

## 九、与设计规格的一致性检查

### 9.1 对照 Adapter 层设计规格

| 设计规格要求 | 本方案实现 | 符合度 |
|------------|-----------|--------|
| **Read Tools 覆盖** | +5 个方法(工具/物品/环境/收集包/NPC) | ✅ 完全符合 |
| **Exec Tools 覆盖** | +6 个方法(工具/浇水/装水/丢弃/出售/NPC) | ✅ 完全符合 |
| **Event 覆盖** | EventWatcher(背包/时间/天气/NPC) | ✅ 完全符合 |
| **稳定性分级** | 所有方法标注 SMAPI API 来源 | ✅ 完全符合 |
| **错误码定义** | 每个方法返回失败原因 | ✅ 完全符合 |
| **语义化命名** | 方法名对 LLM 友好 | ✅ 完全符合 |

### 9.2 对照技术摸底结论

| 摸底结论 | 本方案采纳 | 偏离点 |
|---------|-----------|--------|
| **PathFindController 优先** | ✅ NavigateTo 已使用 | 无 |
| **不用 Input.Press()** | ✅ 未使用输入模拟 | 无 |
| **checkForAction() 交互** | ✅ InteractWithTile 已使用 | 无 |
| **Tool.DoFunction() 工具** | ✅ SwingTool 使用 | 无 |
| **卡住检测必须** | ✅ StuckDetector 已有 | 无 |
| **失败恢复机制** | ⚠️ P0 未实现 3 次重试 | 技术债务 |

---

## 十、后续扩展路径

### 10.1 Phase 4 — 接入 LLM 决策层

**当前 Adapter 层 → LLM 的接口设计**:

```json
{
  "tool": "water_crops",
  "params": {
    "max_count": 20
  }
}
```

**LLM 返回 → Adapter 层执行**:

```csharp
// 伪代码
var result = JsonSerializer.Deserialize<ActionRequest>(llmResponse);

switch (result.Tool)
{
    case "water_crops":
        var waterResult = _actions.WaterCrops(
            maxCount: result.Params.GetInt("max_count")
        );
        return JsonSerializer.Serialize(waterResult);
}
```

### 10.2 Phase 5 — 高级能力扩展

**P2 级别功能**(黑客松后):
1. **采矿系统** — 进入矿洞、战斗、采集矿石
2. **钓鱼系统** — 自动钓鱼(需要精确时序控制)
3. **社交系统** — 送礼、对话、婚姻
4. **商店购买** — 自动导航商店、购买种子
5. **建筑系统** — 委托 Robin 建造

### 10.3 架构演进建议

**当前架构 → 目标架构**:

```
现在:
ModEntry → StateReader + ActionExecutor + EventWatcher
    ↓
目标(接入 LLM 后):
ModEntry → AgentController
    ├── StateReader (查询)
    ├── ActionExecutor (执行)
    ├── EventWatcher (感知)
    ├── LLMBridge (决策)
    └── MemoryManager (记忆)
```

**新增组件**:
- **AgentController** — 总控制器,协调各模块
- **LLMBridge** — 与 LLM 通信(HTTP/WebSocket)
- **MemoryManager** — 持久化记忆(玩家偏好、历史决策)

---

## 附录 A:完整方法签名速查

### StateReader 扩展

```csharp
// P0
ToolState GetToolState(string toolName);
ItemValue GetItemValue(string itemName, int quality = 0);
EnvironmentChanges GetEnvironmentChanges();

// P1
CommunityCenterProgress GetCommunityCenterProgress();
List<NpcInfo> GetNearbyNpcs(int radius = 20, bool includeSchedule = false);
```

### ActionExecutor 扩展

```csharp
// P0
bool SwingTool(int tileX, int tileY);
WaterResult WaterCrops(List<Point> tiles = null, int maxCount = 999);
bool RefillWateringCan();
bool DropItem(string itemName, int quantity = 1);
bool SellItem(string itemName, int quantity = 1);

// P1
bool NavigateToNpc(string npcName);
```

### EventWatcher

```csharp
// 事件订阅
event EventHandler<InventoryChangedEvent> OnInventoryChanged;
event EventHandler<TimeChangedEvent> OnTimeChanged;
event EventHandler<WeatherChangedEvent> OnWeatherChanged;
event EventHandler<NpcMovedEvent> OnNpcMoved;

// 核心方法
void Update(StateReader state);
```

---

## 附录 B:错误码完整列表

| 错误码 | 含义 | 返回场景 |
|--------|------|---------|
| `TOO_FAR` | 距离过远 | SwingTool、InteractWithTile |
| `NO_TOOL` | 未装备工具 | SwingTool |
| `NO_ENERGY` | 体力不足 | SwingTool、WaterCrops |
| `NO_WATER` | 水壶没水 | WaterCrops |
| `NO_WATERING_CAN` | 背包无水壶 | WaterCrops、RefillWateringCan |
| `NO_WATER_SOURCE` | 找不到水源 | RefillWateringCan |
| `ITEM_NOT_FOUND` | 物品不在背包 | DropItem、SellItem |
| `SHIPPING_BIN_NOT_FOUND` | 找不到出货箱 | SellItem |
| `NPC_NOT_FOUND` | NPC 不存在 | NavigateToNpc |
| `PATH_BLOCKED` | 路径被阻挡 | NavigateTo、NavigateToNpc |

---

**文档版本**: v1.0
**完成时间**: 2026-03-24
**基于**: 现有 Demo 代码 + 技术摸底 + 设计规格
**面向**: 黑客松 MVP (3/28-29)
