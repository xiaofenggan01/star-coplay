# query_shop + buy_items 设计方案

## 概览

拆分为两个独立 Tool：
- `query_shop`：纯查询（同步，无导航），利用 SDV 1.6 Data/Shops API
- `buy_items`：实际购买（compound，需导航+菜单交互）

## Tool 1: query_shop

### 定位

**Query capability**（同 query_inventory、query_farm_summary），不是 compound handler。
同步返回，加入 INSTANT_ACTIONS。

### 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `shop` | string | 是 | 商店 ID |

### 商店 ID 映射

| ID | 商店名 | NPC | 位置 |
|----|--------|-----|------|
| `SeedShop` | Pierre's General Store | Pierre | Town |
| `FishShop` | Willy's Fish Shop | Willy | Beach |
| `Saloon` | Gus's Saloon | Gus | Town |
| `Blacksmith` | Clint's Blacksmith | Clint | Town |
| `AnimalShop` | Marnie's Ranch | Marnie | Forest |
| `HatMouse` | Hat Mouse | - | Forest |
| `Carpenter` | Robin's Carpenter Shop | Robin | Mountain |
| `Hospital` | Harvey's Clinic | Harvey | Town |
| `JojaMart` | Joja Mart | - | Town |
| `DesertTrader` | Desert Trader | - | Desert |
| `TravelingMerchant` | Traveling Cart | - | Forest (Fri/Sun) |

### 实现方式

**不使用 Utility.GetShopStock**（需要验证 1.6 中是否可用）。
**使用 ShopBuilder.GetShopStock 或直接读 Data/Shops**：

```csharp
// 方案 A：ShopBuilder（如果 1.6 有此 API）
var stock = ShopBuilder.GetShopStock(shopId);

// 方案 B：直接读 Data/Shops JSON（最安全，不依赖运行时 API）
var shopData = Game1.content.Load<Dictionary<string, ShopData>>("Data/Shops");
if (shopData.TryGetValue(shopId, out var shop)) { ... }

// 方案 C：构造临时 ShopMenu 读取库存（保证和玩家看到的一致）
// 注意：不实际打开 UI，只读取数据
var tempStock = Utility.GetShopStock(shopId);
```

**建议方案 C**：最准确，因为 Utility.GetShopStock 会应用所有条件（季节、好感度、解锁进度等）。需要验证此 API 在不打开 ShopMenu 的情况下是否可调用。

**fallback 方案**：如果 Utility.GetShopStock 不存在或不可直接调用，则通过反编译确认 ShopMenu 构造函数，在不显示 UI 的情况下构造 ShopMenu 读取 forSale。

### 返回值

```json
{
  "shop": "SeedShop",
  "shop_name": "Pierre's General Store",
  "currency": "gold",
  "item_count": 24,
  "items": [
    {
      "name": "Parsnip Seeds",
      "price": 20,
      "stock": -1,
      "category": "Seeds",
      "description": "Plant in spring. Takes 4 days to mature."
    },
    {
      "name": "Bean Starter",
      "price": 60,
      "stock": -1,
      "category": "Seeds",
      "description": "Plant in spring."
    }
  ]
}
```

`stock: -1` = 无限库存，`stock: N` = 限量。

### 注册

- AdapterV2: `CapabilityQueryShop = "query_shop"`，category = "query"
- 不需要 CompoundDispatcher
- Handler 直接在 AdapterV2.Register 中注册（同 query_inventory）
- Agent 侧：加入 INSTANT_ACTIONS（不是 LONG_ACTIONS）

---

## Tool 2: buy_items

### 定位

**Compound handler**（需导航 + 菜单交互 + 多步状态机）

### 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `shop` | string | 是 | 商店 ID（同 query_shop） |
| `items` | object[] | 是 | `[{"name": "Parsnip Seeds", "count": 20}]` |

### 状态机

```
ResolveShop → NavigateToShop → WaitArrival → InteractNPC → WaitForMenu → PurchaseItems → CloseMenu → Complete
```

#### Phase 1: ResolveShop

从商店 ID 解析出：
- NPC 名字（用于定位和交互）
- 默认位置（NPC 可能不在固定位置）

```csharp
static readonly Dictionary<string, (string npcName, string defaultLocation)> ShopNpcMap = new()
{
    ["SeedShop"]       = ("Pierre", "Town"),
    ["FishShop"]       = ("Willy", "Beach"),
    ["Saloon"]         = ("Gus", "Town"),
    ["Blacksmith"]     = ("Clint", "Town"),
    ["AnimalShop"]     = ("Marnie", "Forest"),
    ["Carpenter"]      = ("Robin", "Mountain"),
    ["Hospital"]       = ("Harvey", "Town"),
};
```

对于无 NPC 的商店（JojaMart、TravelingMerchant）：
- JojaMart：导航到 JojaMart 位置，与收银台交互
- TravelingMerchant：导航到 Forest，与猪车交互

#### Phase 2: NavigateToShop

1. 用 `Game1.getCharacterFromName(npcName)` 获取 NPC 当前位置
2. 如果 NPC 在当前地图：`NavigateTo(npc.TilePoint)` 附近
3. 如果 NPC 不在当前地图：先 `move_to(location)` 跨地图，再本地导航
4. NavigateTo 失败 → WarpTo fallback

#### Phase 3: WaitArrival

等待 `!_executor.IsBusy` + 距离检查

#### Phase 4: InteractNPC

面向 NPC → `InteractWithTile(npc.X, npc.Y)` → 触发 ShopMenu

#### Phase 5: WaitForMenu

每帧检查 `Game1.activeClickableMenu is ShopMenu`
- 超时 60 帧（1 秒）→ 重试 Interact
- 超时 180 帧（3 秒）→ 失败

注意：有些 NPC 会先说话再打开商店（DialogueBox → ShopMenu），需要先处理 DialogueBox：
```csharp
if (Game1.activeClickableMenu is DialogueBox dialogue)
{
    // 自动点击对话推进
    dialogue.receiveLeftClick(0, 0);
}
```

#### Phase 6: PurchaseItems

```csharp
var shopMenu = Game1.activeClickableMenu as ShopMenu;

foreach (var itemSpec in _items)
{
    // 在 forSale 中查找物品
    var salable = shopMenu.forSale.FirstOrDefault(i =>
        i.DisplayName.Equals(itemSpec.Name, StringComparison.OrdinalIgnoreCase) ||
        i.Name.Equals(itemSpec.Name, StringComparison.OrdinalIgnoreCase));

    if (salable == null)
    {
        _failed.Add(new { name = itemSpec.Name, reason = "not_in_stock" });
        continue;
    }

    // 购买
    for (int i = 0; i < itemSpec.Count; i++)
    {
        bool ok = shopMenu.tryToPurchaseItem(salable, shopMenu.heldItem, 0, 0, 1);
        if (!ok)
        {
            _failed.Add(new { name = itemSpec.Name, reason = "purchase_failed", bought = i });
            break;
        }
        _boughtCount++;
        _totalCost += GetItemPrice(shopMenu, salable);
    }
}
```

**注意**：`tryToPurchaseItem` 的方法签名需要从反编译代码确认。参数可能是：
- `tryToPurchaseItem(ISalable item, bool playSound)` — 简化版
- 或者需要模拟点击坐标

**安全检查**：
- 购买前检查 `Game1.player.Money >= price`
- 购买前检查背包空间
- 每次购买后验证 Money 减少了

#### Phase 7: CloseMenu

```csharp
Game1.activeClickableMenu?.exitThisMenu();
```

等待菜单关闭（`Game1.activeClickableMenu == null`）。

### 返回值

```json
{
  "shop": "SeedShop",
  "bought": [
    {"name": "Parsnip Seeds", "count": 20, "unit_price": 20, "total": 400}
  ],
  "total_cost": 400,
  "gold_remaining": 1600,
  "failed": [],
  "message": "从 Pierre's General Store 购买了 20 件物品，花费 400g"
}
```

### 错误处理

| 场景 | 处理 |
|------|------|
| NPC 不在（休息日） | 返回失败 + NPC 作息提示 |
| 金币不足 | 购买到金币不足为止，返回已购部分 |
| 背包满 | 停止购买，返回已购部分 |
| ShopMenu 打不开 | NPC 可能在对话中，等待后重试 |
| 物品不在商店 | 单项 failed，其他继续 |

### 注册

- AdapterV2: `CapabilityBuyItems = "buy_items"`
- CompoundDispatcher 注册
- Agent 侧：LONG_ACTIONS + 60s timeout

---

## 风险点

### query_shop 风险

1. **Utility.GetShopStock API 可能不存在**
   - SDV 1.6 数据驱动重构可能改变了 API
   - 需要在 Mac 上反编译确认
   - Fallback：直接读 `Data/Shops` JSON + 手动应用条件

2. **条件评估准确性**
   - 有些商品依赖好感度、任务进度、季节
   - `Utility.GetShopStock` 应该自动处理，但需验证

### buy_items 风险

1. **tryToPurchaseItem 方法签名不确定**
   - 需要反编译确认参数
   - Fallback：使用 `receiveLeftClick` 模拟点击

2. **NPC 对话拦截**
   - 有些 NPC 交互先弹 DialogueBox 再打开 ShopMenu
   - 需要自动推进 DialogueBox

3. **商店营业时间**
   - Pierre: 9am-5pm（周三休息）
   - 需要在 ResolveShop 阶段检查

4. **跨地图导航**
   - 当前 compound handler 的导航只支持同地图 NavigateTo + 跨地图 WarpTo
   - 需要先 WarpTo 目标地图，再本地 NavigateTo NPC

---

## 实现顺序

1. **先实现 query_shop**（风险低，同步返回，不依赖导航）
   - 作为 QueryHandler 的一个分支，注册到 AdapterV2
   - 验证 Utility.GetShopStock 是否可用
   - 如不可用，走 Data/Shops fallback

2. **再实现 buy_items**（依赖 query_shop 验证的 API 可行性）
   - 复用 query_shop 中验证过的商店数据获取方式
   - 状态机 + tryToPurchaseItem 购买

---

## 实现完成总结（2026-04-10）

### query_shop 实现（QueryHandler.cs）
- 使用 SDV 1.6 的 `DataLoader.Shops()` 读取商店数据
- 使用 `ShopBuilder.GetShopStock()` 获取实时库存
- 返回商店 ID、名字、货币、物品列表（包括价格、库存、分类）
- 注册方式：ModEntry 通过 `RegisterQueryCapability` 注册
- Agent 侧：加入 `INSTANT_ACTIONS`（同步查询，无导航开销）

### buy_items 实现（BuyItemsHandler.cs）

**CRITICAL: 实现遵循用户约束 - 只使用 ShopMenu 公开购买 API，禁止手动扣款**

状态机流程：
```
ResolveShop
  ↓ 从商店 ID 解析 NPC 名字和位置
NavigateToShop
  ↓ 跨地图 WarpTo + 本地 NavigateTo NPC
WaitArrival
  ↓ 等待玩家到达，检查距离
InteractNPC
  ↓ 面向 NPC，调用 InteractWithTile 触发
WaitForMenu
  ↓ 等待 ShopMenu 打开（支持 DialogueBox 自动推进）
PurchaseItems
  ↓ **使用 ShopMenu.tryToPurchaseItem() 完成购买**（关键）
CloseMenu
  ↓ 关闭菜单
Complete
```

**购买逻辑保证安全性**：
1. 先检查 `player.Money >= price`，失败则返回 insufficient_gold
2. 检查背包空间 `player.Items.Count >= maxItems`
3. **调用 `shopMenu.tryToPurchaseItem(salable, null, 0, 0, 1)` 进行购买**
   - 此方法完全由 Stardew Valley 内部处理金币扣减、背包增加、统计更新
   - 绝不存在手动操作 `Game1.player.Money` 的代码
4. 返回已购物品明细、总金额、剩余金币、失败原因

**错误处理**：
- NPC 不存在（休息日）→ 返回 NPC 不存在错误
- 金币不足 → 停止购买，返回已购部分
- 背包满 → 停止购买，返回已购部分
- 商品缺货 → 单项失败，其他继续
- ShopMenu 打不开 → 超时失败

**注册方式**：
- ModEntry 在 CompoundDispatcher 注册，同时向 AdapterV2 注册
- Agent 侧：加入 `LONG_ACTIONS`，60s timeout

### Files Modified
- `v2/mod/StarCoPlay/V2/QueryHandler.cs` — 添加 HandleQueryShop 方法
- `v2/mod/StarCoPlay/V2/Compound/BuyItemsHandler.cs` — 新建 compound handler
- `v2/mod/StarCoPlay/V2/AdapterV2.cs` — 添加两个 capability 元数据
- `v2/mod/StarCoPlay/ModEntry.cs` — 注册两个 capability
- `v2/agent/slow_loop/deliberative_loop.py` — 更新 INSTANT_ACTIONS 和 LONG_ACTIONS
