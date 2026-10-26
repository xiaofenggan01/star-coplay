# Ref ID 设计

## 核心模型

Ref ID 是协议层对具体实体的可重新解析引用。它只负责两件事：

- 重新定位：在当前游戏状态中找到候选实体。
- 语义校验：判断候选实体是否仍是上次观察到的那类东西。

统一格式：

```text
ref_type:type:locator#identity_guard
```

Ref 字符串的结构分隔符只允许 `:` 和 `#`。各 segment 必须是 `safe_token`，允许字母、数字、下划线和连字符；空格规范化为下划线。原始游戏值如果包含括号、本地化字符、路径分隔符等其他符号，需要先映射成 safe token。原始值放在 facts/debug 中，不放进 Ref 表层。

例子：

```text
world_entity:tree:farm:41:12#wt_1
world_entity:resource_node:mine_20:34:18#rn_stone
world_entity:container:farmhouse:8:6#bc_130
world_entity:machine:farm:20:11#bc_12
world_entity:bed:farmhouse:4:7#f_bed
character:npc:robin#robin
inventory:item:player:3#o_388
ui:option:dialogue_sleep:yes#accept
quest:special_order:biome_balance#biome_balance
```

## Token 规则

`locator` 和 `identity_guard` 都必须使用 safe token。

| 来源 | Ref 中的 token | facts/debug 中保留 |
|------|----------------|--------------------|
| `GameLocation.NameOrUniqueName` | `map_token`，例如 `farm`、`coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2` | 原始 `NameOrUniqueName` |
| `Item.QualifiedItemId` / 数据 key | safe-encoded 机器键，例如 `(O)388` -> `o_388`、`(BC)130` -> `bc_130`、`(F)Bed` -> `f_bed` | `ItemId`、`QualifiedItemId`、`InternalName`、`DisplayName` |
| NPC / 动物 / 宠物 `Name` | 小写规范名；空格转下划线，例如 `mr_qi`、`black_cat` | 原始 `Name`、`displayName` |
| UI response key / component id | 协议归一化选项 ID，例如 `yes`、`cancel`、`buy` | 原始 `responseKey`、显示文本、component id |

`identity_guard` 的职责是防止 locator 被误解析，不是给人看的标签。凡是游戏对象有稳定机器键，应优先使用 safe-encoded 机器键，而不是 display name 或 internal/canonical name。原始机器键仍完整保留在 facts/debug 中。

建议的 safe encoding：

| 原始 ID | Ref guard |
|---------|-----------|
| `(O)388` | `o_388` |
| `(BC)130` | `bc_130` |
| `(F)Bed` | `f_bed` |
| Wild tree `treeType = "1"` | `wt_1` |
| Resource node class `stone` | `rn_stone` |
| Resource clump `large_stump` | `rc_large_stump` |
| Warp semantic `to_town` | `warp_to_town` |

如果 mod 数据 key 含空格、本地化字符、`:`、`/`、`.` 等非 safe 字符，Ref 层必须先做确定性 safe encoding；facts/debug 保留原始 key，解析时从 encoded token 反查或重新编码比对。即使原始值本身已经是 safe token，也应优先加类型前缀，避免 `oak`、`stone`、`bed` 这种裸词在不同 type 或 mod key 中碰撞。

## 解析状态

| 状态 | 含义 |
|------|------|
| `resolved` | locator 找到候选实体，type 与 identity_guard 均匹配 |
| `resolved_degraded` | 找到同一生命周期链上的实体，但状态退化，例如树变 stump |
| `missing` | locator 指向的位置、slot 或记录不存在 |
| `stale_type_mismatch` | locator 找到实体，但 type 不匹配 |
| `stale_guard_mismatch` | type 匹配，但 identity_guard 不匹配 |
| `stale_context_mismatch` | UI/menu 等短生命周期上下文已切换 |
| `ambiguous` | locator 合法，但解析出多个候选；或 token 映射发生碰撞，无法唯一确定实体 |
| `unsupported` | Ref 格式合法，但当前协议/实现版本不支持该 ref_type 或 type |
| `malformed_ref` | Ref 字符串不符合格式、结构分隔符错误、segment 非 safe token，或 LLM 自造了未声明结构 |

## ref_type 与 type

### `world_entity`

`world_entity` 表示地图状态中的具体实体。locator 使用：

```text
map_token:x:y
```

`map_token` 来自 `GameLocation.NameOrUniqueName` 的 safe-token 映射。建筑内部地图的原始名可能含 GUID 连字符，连字符可原样保留；facts 中仍保留原始名用于反查和调试。

| type | Ref 示例 | locator | identity_guard | 典型 State | 有效 / 失效规则 |
|------|----------|---------|----------------|------------|-----------------|
| `tree` | `world_entity:tree:farm:41:12#wt_1` | `map_token:x:y` 定位 `terrainFeatures` 中的 `Tree` | safe-encoded wild tree key；vanilla oak 是 `treeType = "1"` -> `wt_1` | `growth_stage`、`is_stump`、`is_tapped`、`has_moss`、`health` | 同 tile 仍为同一 wild tree key 时 `resolved`；变 stump 是 `resolved_degraded`；完全消失是 `missing`；树种变化是 `stale_guard_mismatch`；变 crop/object 是 `stale_type_mismatch` |
| `fruit_tree` | `world_entity:fruit_tree:farm:32:18#o_613` | `map_token:x:y` 定位 `terrainFeatures` 中的 `FruitTree` | safe-encoded fruit tree `treeId`；vanilla apple fruit item id 是 `613` -> `o_613` | `growth_stage`、`days_until_mature`、`fruit_count`、`is_stump`、`struck_by_lightning` | 成长、结果、雷劈是 state 变化；变 stump 可 `resolved_degraded`；消失是 `missing`；换品种是 `stale_guard_mismatch` |
| `crop` | `world_entity:crop:farm:12:8#o_24` | `map_token:x:y` 定位 `HoeDirt.crop` | 作物产物或作物数据的 stable key，优先 safe-encoded 机器 ID | `growth_phase`、`ready_for_harvest`、`watered`、`fertilizer`、`dead` | 成长、浇水、成熟是 state 变化；收获后 crop 消失为 `missing`；改种为 `stale_guard_mismatch`；tile 不再是 crop 容器为 `stale_type_mismatch` |
| `resource_node` | `world_entity:resource_node:mine_20:34:18#rn_stone` | `map_token:x:y` 定位 `GameLocation.objects` 中可破坏/可清理的资源节点 | safe-encoded 节点 key，如 `rn_stone`、`rn_ore_copper`、`rn_twig`、`rn_weeds`；能落到 `QualifiedItemId` 时优先用 `o_xxx` | `node_kind`、`health`、`required_tool`、`drop_preview`、`is_spawned_object` | 同 tile 仍是同类资源节点时有效；被清理为 `missing`；石头变矿点或小木头为 `stale_guard_mismatch`；变成机器/箱子/作物为 `stale_type_mismatch` |
| `loose_item` | `world_entity:loose_item:farm:64:15#o_388` | `map_token:x:y` 定位 `GameLocation.objects` 中可拾取 item stack | safe-encoded `QualifiedItemId`，如 `o_388`、`o_390` | `stack`、`quality`、`can_be_grabbed`、`is_spawned_object` | 数量变化是 state；被拾取为 `missing`；slot/tile 被其他物品复用为 `stale_guard_mismatch` |
| `machine` | `world_entity:machine:farm:20:11#bc_12` | `map_token:x:y` 定位 `GameLocation.objects`，并要求 `GetMachineData() != null` | safe-encoded `QualifiedItemId`，如 keg 的机器 ID | `held_object`、`last_input_item`、`minutes_until_ready`、`ready_for_harvest`、`machine_data_id` | 加工进度和产物是 state；机器被移走为 `missing`；同 tile 换机器为 `stale_guard_mismatch`；同 tile 变非机器为 `stale_type_mismatch` |
| `container` | `world_entity:container:farmhouse:8:6#bc_130` | `map_token:x:y` 定位 `GameLocation.objects` 中 `Chest` 或同类容器 | safe-encoded 容器 `QualifiedItemId` 或特殊容器 key，如 `bc_130`、`junimo_chest` | `capacity`、`items_count`、`special_chest_type`、`fridge`、`player_chest` | 内容变化是 state；箱子被移走为 `missing`；同 tile 换容器 ID 为 `stale_guard_mismatch`；同 tile 变非容器为 `stale_type_mismatch` |
| `bed` | `world_entity:bed:farmhouse:4:7#f_bed` | `map_token:x:y` 定位 furniture 集合中的 bed anchor tile | safe-encoded bed furniture ID，如 `f_bed` | `bed_type`、`can_sleep`、`rotation`、`bounding_tiles` | bed 是睡觉/结束一天的高价值目标，单独建 type；被拿起为 `missing`；换床为 `stale_guard_mismatch`；同 tile 变非 bed 家具为 `stale_type_mismatch` |
| `furniture` | `world_entity:furniture:farmhouse:6:7#f_chair` | `map_token:x:y` 定位 furniture 集合中的非 bed 家具 anchor tile | safe-encoded furniture qualified ID | `rotation`、`bounding_tiles`、`can_sit`、`held_object` | 旋转是 state；被拿起为 `missing`；换成其他家具是 `stale_guard_mismatch`；同 tile 变普通 object 是 `stale_type_mismatch` |
| `resource_clump` | `world_entity:resource_clump:farm:5:32#rc_large_stump` | `map_token:x:y` 定位 `resourceClumps` anchor tile | safe-encoded clump key，如 `rc_large_stump`、`rc_large_log`、`rc_boulder`、`rc_meteorite` | `health`、`width`、`height`、`required_tool` | 血量变化是 state；被砍掉/炸掉是 `missing`；换成 boulder 是 `stale_guard_mismatch` |
| `door` | `world_entity:door:farmhouse:3:12#door_default` | `map_token:x:y` 定位 `doors` 或地图 door action tile | safe-encoded door/action key；没有机器 ID 时也必须带类型前缀 | `target_map`、`target_tile`、`locked` | 门目标变化是 state 或 `stale_guard_mismatch`，取决于是否仍是同一语义门；tile 不再有门为 `missing` |
| `warp` | `world_entity:warp:farm:64:15#warp_to_town` | `map_token:x:y` 定位 warp tile | safe-encoded warp 目标 key；没有机器 ID 时也必须带类型前缀 | `target_map`、`target_x`、`target_y`、`npc_only` | 目标仍一致时有效；目标变化为 `stale_guard_mismatch`；warp 消失为 `missing` |

`world_entity:object` 不作为正式协议 type。`StardewValley.Object` 是源码实现类，覆盖 item、地图 tile object、机器、箱子、资源节点、特殊交互物等多种语义；协议层必须按可验证的解析路径和 state model 拆成更窄 type。无法归类的新 Object 第一版返回 `unsupported`，不要静默塞进 `object`。

第一批不建议把 `debris`、`projectile`、`temporary_sprite` 纳入 Ref。它们是高频短生命周期对象，适合在 observation 中作为事实出现，不适合作为后续 inspect/action 的稳定引用。

### `character`

`character` 表示角色身份，不表示“某地图某 tile 上的人”。位置、朝向、是否可交互都属于 state。

| type | Ref 示例 | locator | identity_guard | 典型 State | 有效 / 失效规则 |
|------|----------|---------|----------------|------------|-----------------|
| `npc` | `character:npc:robin#robin` | NPC 内部 `Name` 的 safe token | 通常同 locator | `current_map`、`tile`、`friendship`、`schedule_key`、`can_talk` | NPC 换地图、移动、不可交互都是 state；NPC 不存在是 `missing` |
| `farm_animal` | `character:farm_animal:123456#bessie` | `FarmAnimal.myID` | 动物当前名字 safe token，弱 guard | `animal_type`、`name`、`home_building`、`current_map`、`produce_ready`、`friendship` | ID 仍存在时有效；改名可返回 `resolved_degraded` 或 `stale_guard_mismatch`；动物被卖掉为 `missing` |
| `horse` | `character:horse:0f4a7c#tractor` | horse guid 的 safe token | 马名 safe token，弱 guard | `owner_id`、`current_map`、`tile` | guid 仍存在时有效；改名为弱 guard 变化；消失为 `missing` |
| `pet` | `character:pet:9bca21#miso` | pet guid 的 safe token | 宠物名 safe token，弱 guard | `pet_type`、`current_map`、`friendship`、`last_petted` | guid 仍存在时有效；改名为弱 guard 变化；消失为 `missing` |
| `monster` | `character:monster:mine_20:9f2e7f1a-8b4f-4f55-91a2-c5212a77d713#green_slime` | `map_token:character_guid`，guid 来自所在 `GameLocation.characters` 集合 | 怪物稳定类型 key；没有机器 ID 时才用 canonical monster key | `health`、`tile`、`is_moving`、`current_map` | 同一 location 的 `characters` 集合中 guid 仍存在且仍为同类怪物时有效；怪物死亡/离场为 `missing`；guid 指向其他角色为 `stale_type_mismatch`；同 guid 怪物类型变化为 `stale_guard_mismatch` |

`monster` 不应使用 `map_token:x:y` 作为 locator。怪物会移动，tile 只能作为 state。反编译代码里 `Monster : NPC`，怪物实例随 NPC 一样放在 `GameLocation.characters`，该容器是 `NetCollection<NPC>`，提供 `GuidOf(item)` 与 `ContainsGuid(guid)`。这个 guid 不是怪物类自身暴露的业务 ID，但它是当前 location 角色集合中的实例身份，足以作为战斗/观察期的强 locator。为避免跨 location 集合歧义，Ref 中保留 `map_token`。

### `inventory`

`inventory` 指向容器 slot 中的 item stack。slot 是 locator，`identity_guard` 防止 slot 被其他物品复用。

| type | Ref 示例 | locator | identity_guard | 典型 State | 有效 / 失效规则 |
|------|----------|---------|----------------|------------|-----------------|
| `item` | `inventory:item:player:3#o_388` | `container_locator:slot`。常见形式包括 `player:slot`、`chest:map_token:x:y:slot`、`fridge:map_token:x:y:slot`、`junimo_chest:slot` | safe-encoded `QualifiedItemId` | `stack`、`quality`、`category`、`can_stack`、`container_kind` | 同 slot 仍为同一 `QualifiedItemId` 时有效；数量变化是 state；slot 空是 `missing`；slot 换物品是 `stale_guard_mismatch`；容器不存在则 `missing` |
| `tool` | `inventory:tool:player:0#t_axe` | `player:slot` | safe-encoded tool qualified ID 或 stable tool key | `upgrade_level`、`attachments`、`enchantments` | 工具升级是 state；slot 换工具为 `stale_guard_mismatch`；slot 空为 `missing` |
| `equipment` | `inventory:equipment:boots#b_504` | equipment slot token，如 `hat`、`boots`、`left_ring` | safe-encoded equipment qualified ID | `stats`、`enchantments` | 同装备槽仍为同物品有效；摘下为 `missing`；换装为 `stale_guard_mismatch` |

不再定义单独的 `container_item` type。容器内物品仍是 `inventory:item`，差异只在 locator 的 `container_locator`：

```text
inventory:item:player:3#o_388
inventory:item:chest:farmhouse:8:6:12#o_388
inventory:item:fridge:farmhouse:0:4#o_184
inventory:item:junimo_chest:12#o_388
```

普通箱子本身没有独立实例 ID。反编译代码里 `Chest : Object`，普通放置箱子存放在 `GameLocation.objects`，该容器按 tile key 定位；箱子移动时会从旧 tile 移除并用新 tile 重新插入。因此普通箱子的容器 locator 应使用 `chest:map_token:x:y`，slot 再接在后面。`junimo_chest` 这类特殊共享容器不依赖世界 tile，应使用特殊容器 token。

### `ui`

`ui` 是当前 UI 状态中的临时可操作目标。它不应该跨菜单、跨帧长期保存。

| type | Ref 示例 | locator | identity_guard | 典型 State | 有效 / 失效规则 |
|------|----------|---------|----------------|------------|-----------------|
| `option` | `ui:option:dialogue_sleep:yes#accept` | `menu_token:option_token` | 选项语义，如 `accept`、`cancel`、`buy` | `label`、`enabled`、`index`、`menu_type` | 同菜单同选项有效；菜单关闭/切换为 `stale_context_mismatch`；选项消失为 `missing` |
| `button` | `ui:button:shop:buy#buy` | `menu_token:button_token` | 按钮语义 | `enabled`、`visible`、`label` | 同 button 仍存在有效；页面切换为 context stale |
| `tab` | `ui:tab:game_menu:inventory#inventory` | `menu_token:tab_token` | tab 语义 | `selected`、`enabled` | 菜单关闭为 context stale；tab 不存在为 `missing` |

UI 的 `option_token` 应优先来自 `Response.responseKey` 或稳定 component id，再映射为 safe token；显示文本只进 facts。

### `quest`

`quest` 指向任务系统记录，不使用地图坐标。

| type | Ref 示例 | locator | identity_guard | 典型 State | 有效 / 失效规则 |
|------|----------|---------|----------------|------------|-----------------|
| `special_order` | `quest:special_order:biome_balance#biome_balance` | `SpecialOrder.questKey` 的 safe token | 通常同 order id | `status`、`days_left`、`objectives`、`reward_claimed` | 进行中、完成、过期是 state；记录消失为 `missing` |
| `quest` | `quest:quest:introductions#introductions` | quest id/name 的 safe token | quest canonical 名 | `status`、`objectives`、`completed` | 状态变化不 stale；记录消失为 `missing` |

## State 与 Facts

Ref 表层只放解析所必需的信息。当前值、内部 ID、显示文本都放在 state/facts：

```json
{
  "ref": "world_entity:loose_item:farm:64:15#o_388",
  "status": "resolved",
  "state": {
    "map_token": "farm",
    "tile": [64, 15],
    "stack": 17,
    "quality": 0
  },
  "facts": {
    "location_name_or_unique_name": "Farm",
    "qualified_item_id": "(O)388",
    "internal_name": "Wood",
    "display_name": "Wood"
  }
}
```

Fact proto 是 Query/Inspect 的运行态事实承载结构，不是一次性冻结的完备领域模型。第一版 `query_facts.proto` 只定义当前实现需要返回和验证的字段；随着反编译源码探索、Fact extractor 落地、inspect 用例增加，可以持续补充具体 Fact 字段、oneof 分支和 debug 信息。演进时应遵守：

- RefId 的表层格式和解析语义要保持稳定，Fact 字段可以增量扩展。
- 新增字段优先追加 field number，不重用已发布字段号，不改变既有字段语义。
- 未确认稳定语义的游戏内部值先放入 facts/debug，不急于提升为强结构字段。
- Fact proto 的字段完备性以实际 Query/Inspect 实现和测试证据为准，不以当前文档罗列为上限。

## 反编译源码依据

- `GameLocation` 的可引用地图对象分布在 `objects`、`terrainFeatures`、`furniture`、`resourceClumps`、`doors`、`warps` 等容器中；因此 `world_entity` 必须先按 `type` 选容器，再按 tile 和 guard 校验。
- `GameLocation.NameOrUniqueName` 是地图/建筑内部定位的正确源值；其中 GUID 连字符可作为 segment 内容保留，facts 中仍保留原始值。
- `Item` / `ParsedItemData` 区分 `ItemId`、`QualifiedItemId`、`InternalName`、`DisplayName`；Ref 的 guard 应优先用 safe-encoded `QualifiedItemId`，facts/debug 同时保留原始机器 ID 和可读名称。
- `Monster` 继承 `NPC`，实例位于 `GameLocation.characters`；该字段是 `NetCollection<NPC>`，提供 `GuidOf(item)`、`ContainsGuid(guid)`、`Remove(guid)`，因此怪物 ref 应使用 collection guid，而不是 tile。
- `Chest` 继承 `Object`，普通放置箱子位于 `GameLocation.objects` 的 tile-keyed 字典中，未暴露独立实例 ID；箱子内容引用应使用 `chest:map_token:x:y:slot` 形式。
- `Object.GetMachineData()` 从 `Data/Machines` 按 `QualifiedItemId` 查机器定义；因此 `machine` 应从 `big_craftable` 中独立出来，不应只按 `(BC)` 判断。
- 石头、矿石节点、小木头、杂草等小型资源节点多数也落在 `GameLocation.objects`，但 Query 语义和 action/state 与可拾取物不同；协议层用 `resource_node` 表达。
- `BedFurniture` 继承 furniture，但 bed 是睡觉/结束一天的高价值目标；协议层独立为 `world_entity:bed`，避免被普通 `furniture` 查询淹没。
- `Tree` 使用 `treeType`、`growthStage`、`stump`、`tapped` 等字段；树变 stump 仍处于同一生命周期链。
- `FruitTree` 使用 `treeId`、`growthStage`、`fruit`、`stump` 等字段；品种 guard 来自 `treeId` 对应数据。
- `FarmAnimal` 有 `myID`，并可跨 location/building 移动；因此动物 ref 应以 ID 为强 locator，名字只作为弱 guard。
- UI 对话选项有 `Response.responseKey` 和本地化 `responseText`；Ref 使用 response key 的 safe token，显示文本只进 facts。
- `SpecialOrder` 使用 `questKey` 关联 `Data/SpecialOrders`；特殊订单 ref 应以 `questKey` 为 locator。
