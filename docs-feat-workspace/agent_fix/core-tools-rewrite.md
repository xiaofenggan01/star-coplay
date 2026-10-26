# core_tools.py 重写方案

## 背景

### 当前状态

`agent/slow_loop/core_tools.py` 定义了 9 个手写的 LLM 工具（OpenAI FC 格式）：

```
say, interact, use_tool, equip, go_to, observe, search_wiki, read_wiki, remember
```

问题：
1. 参数结构与 proto 定义不一致（如 `go_to` 接受 `target: "Lewis"` 而非坐标）
2. 工具面不完整（proto 定义了 `emote`、`face`、`move_to`、`menu_*`、`query_*` 等，当前未暴露）
3. Agent runtime 内有意图解析层（把 "Lewis" 解析为坐标），增加了不可靠性

### 改造目标

- 参数结构 1:1 对齐 `capability_params.proto` 定义
- 工具面覆盖 proto 中所有 LLM 可见能力
- 砍掉意图解析层，LLM tool_call params 直接透传给 Mod
- description 保持 LLM 友好的中文（不从 proto 注释自动生成）

## 新工具面

### 执行原子（本次改动，走 Mod）

| 工具名 | proto 对应 | 层级 | 说明 |
|--------|-----------|------|------|
| `say` | SayParams | L1 | 发送聊天文本 |
| `emote` | EmoteParams | L1 | 显示表情气泡（从 say 的 emote 参数独立出来） |
| `face` | FaceParams | L1 | 转向（不移动） |
| `move_to` | MoveToParams | L1 | 同地图寻路（新增暴露） |
| `go_to` | GoToParams | L2 | 跨地图移动（参数从 target 改为坐标） |
| `go_to_bed` | GoToBedParams | L2 | 睡觉进入下一天 |
| `interact` | InteractParams | L1 | 按 X 交互 |
| `use_tool` | UseToolParams | L1 | 按 C 使用工具 |
| `equip` | EquipParams | L1 | 切换手持物品 |
| `open_menu` | OpenMenuParams | L1 | 打开 GameMenu |
| `query_menu` | QueryMenuParams | L1 | 查询当前菜单快照 |
| `menu_click` | MenuClickParams | L1 | 点击菜单组件 |
| `menu_close` | MenuCloseParams | L1 | 关闭菜单 |
| `query_inventory` | QueryInventoryParams | L1 | 查询背包 |
| `query_location` | QueryLocationParams | L1 | 查询当前位置 |
| `query_map_exits` | QueryMapExitsParams | L1 | 查询地图出口 |

### 纯 Agent 侧（不动）

| 工具名 | 说明 |
|--------|------|
| `observe` | 感知入口（后续单独处理） |
| `search_wiki` | 搜索百科 |
| `read_wiki` | 阅读百科页面 |
| `remember` | 记忆持久化 |

## 参数结构对比（关键变化）

### say

```
Before: {message: str, emote?: enum}
After:  {content: str}              ← emote 独立为单独工具
```

Proto 对应：`SayParams { string content = 1; }`

### emote（新增）

```
{emote_type: enum}
```

Proto 对应：`EmoteParams { EmoteType emote_type = 1; }`

枚举值：`happy, sad, angry, heart, exclamation, question, laugh, thinking`

### face（新增）

```
{direction: enum}
```

Proto 对应：`FaceParams { Direction direction = 1; }`

枚举值：`up, down, left, right`

### move_to（新增暴露）

```
{mode: enum, x: int, y: int, direction?: enum, face_direction?: enum, adjacent_side?: enum, timeout_frames?: int}
```

Proto 对应：`MoveToParams`

mode 枚举：`exact, edge_warp, adjacent`

### go_to（参数重构）

```
Before: {target: str}                    ← "Lewis"、"Beach" 等自然语言
After:  {map_id: str, x: int, y: int, arrival_mode: enum, stand_side?: enum, face_direction?: enum, timeout_frames?: int}
```

Proto 对应：`GoToParams`

arrival_mode 枚举：`exact, adjacent`

**重大变化**：LLM 不再能说 "去找 Lewis"，必须先通过 `query_location` 或 `observe` 获取目标坐标，再调用 `go_to`。

### go_to_bed（新增）

```
{timeout_frames?: int}
```

Proto 对应：`GoToBedParams`

### interact

```
Before: {x?: int, y?: int}              ← 可选，不传则对面前 tile
After:  {target_tile: {x: int, y: int}} ← 必填
```

Proto 对应：`InteractParams { TilePoint target_tile = 1; }`

**变化**：target_tile 变为必填。LLM 必须知道要交互的 tile 坐标。

### use_tool

```
Before: {x?: int, y?: int, charge_level?: int}
After:  {target_tile: {x: int, y: int}, charge_level?: int}
```

Proto 对应：`UseToolParams { TilePoint target_tile = 1; int32 charge_level = 2; }`

**变化**：target_tile 变为必填。

### equip

```
Before: {slot?: int, item_name?: str}
After:  {slot?: int, item_name?: str, item_category?: enum}
```

Proto 对应：`EquipParams { optional int32 slot = 1; optional string item_name = 2; ItemCategory item_category = 3; }`

新增 `item_category` 选择器（如 "装备一把斧类工具"）。三种选择器优先级：slot > item_name > item_category。

### 菜单操作（新增）

```
open_menu:   {}                          ← 无参数
query_menu:  {}                          ← 无参数
menu_click:  {component_id: int, source: enum}
menu_close:  {}                          ← 无参数
```

source 枚举：`page, tab`

### 查询（新增）

```
query_inventory: {include_details?: bool}
query_location:  {}                      ← 无参数
query_map_exits: {}                      ← 无参数
```

## _process_trigger 透传改造

### 当前逻辑（简化）

```python
def _process_trigger(self, tool_calls):
    for call in tool_calls:
        name = call.function.name
        args = json.loads(call.function.arguments)

        if name == "go_to":
            # 意图解析：target="Lewis" → 查找 NPC 位置 → 转为坐标
            resolved = self._resolve_go_to_target(args["target"])
            dispatcher.submit("go_to", resolved, wait_result=True)

        elif name == "interact":
            # 如果没传 x/y，用玩家面前的 tile
            if "x" not in args:
                args = self._get_facing_tile()
            dispatcher.submit("interact", args, ...)
```

### 改造后

```python
def _process_trigger(self, tool_calls):
    for call in tool_calls:
        name = call.function.name
        args = json.loads(call.function.arguments)

        if name in AGENT_LOCAL_TOOLS:
            # observe, search_wiki, read_wiki, remember → 本地处理
            self._handle_local_tool(name, args)
        elif name in self._bridge.effective_capabilities:
            # 所有 proto 定义的能力 → 直接透传
            result = dispatcher.submit(name, args, wait_result=(name in LONG_ACTIONS))
            # result 是 proto result dict，直接返回给 LLM
        else:
            # 未知工具
            yield tool_error(call.id, f"Unknown tool: {name}")
```

核心变化：
1. 砍掉 `_resolve_go_to_target`（意图解析）
2. 砍掉 `_get_facing_tile`（默认值填充）
3. params dict 直接透传给 `bridge.write_command(capability, params)`

### LONG_ACTIONS vs INSTANT_ACTIONS

```python
LONG_ACTIONS = {"move_to", "go_to", "go_to_bed", "interact", "use_tool"}
INSTANT_ACTIONS = {"say", "emote", "face", "equip", "open_menu", "menu_click", "menu_close"}
QUERY_ACTIONS = {"query_menu", "query_inventory", "query_location", "query_map_exits"}
AGENT_LOCAL_TOOLS = {"observe", "search_wiki", "read_wiki", "remember"}
```

LONG_ACTIONS 需要 `wait_result=True`（阻塞等待 Mod 执行完成）。
INSTANT_ACTIONS 和 QUERY_ACTIONS 也等结果，但预期很快返回。

## LLM Description 编写原则

1. **中文为主**，面向 LLM 决策（不是面向开发者）
2. **说明何时用**：给出典型场景
3. **说明前提条件**：如 "必须先 query_location 获取坐标"
4. **说明与其他工具的边界**：如 interact vs use_tool
5. **不暴露实现细节**：不提 proto、不提 Envelope、不提 Mod
6. **枚举值用英文小写**：与 proto enum 名对齐（去掉前缀，小写化）

## 枚举值映射规则

Proto 枚举 → LLM schema enum 值：

| Proto | LLM schema |
|-------|-----------|
| `DIRECTION_UP` | `"up"` |
| `DIRECTION_DOWN` | `"down"` |
| `MOVE_TO_EXACT` | `"exact"` |
| `MOVE_TO_EDGE_WARP` | `"edge_warp"` |
| `MOVE_TO_ADJACENT` | `"adjacent"` |
| `ARRIVAL_EXACT` | `"exact"` |
| `ARRIVAL_ADJACENT` | `"adjacent"` |
| `EMOTE_HAPPY` | `"happy"` |
| `EMOTE_SAD` | `"sad"` |
| `MENU_COMPONENT_SOURCE_PAGE` | `"page"` |
| `MENU_COMPONENT_SOURCE_TAB` | `"tab"` |

Bridge 在 `_set_capability_params` 中需要将这些字符串映射回 proto enum int 值。`ParseDict` 默认支持 enum 名称字符串解析，但需要传完整的 proto enum 名（如 `"DIRECTION_UP"`）。

**方案**：在 `_set_capability_params` 之前做一次 enum 值预处理，将 LLM 传入的简写（`"up"`）转为 proto 全名（`"DIRECTION_UP"`）。或者在 proto 层面配置 `json_name` 使 `ParseDict` 直接接受简写。

推荐前者（显式映射），因为不修改 proto 定义。

## 文件变更

| 文件 | 变更 |
|------|------|
| `agent/slow_loop/core_tools.py` | 完全重写 |
| `agent/slow_loop/deliberative_loop.py` | `_process_trigger` 砍意图解析，透传 |
| `agent/slow_loop/deliberative_loop.py` | `_build_tool_schemas` 用 effective_capabilities 过滤 |
