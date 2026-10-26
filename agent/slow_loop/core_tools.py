"""LLM-facing default tool schemas.

The default core tools are backed by Protocol 2.4.5 capability specs so LLM
tool calls match the command mapper used by the Mod transport.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from agent.protocol.capability_registry import iter_llm_exposed_specs
from agent.protocol.schemas import get_static_protocol_tool_schemas
from l3_query.schema import supported_fact_types

# ---------------------------------------------------------------------------
# Protocol tools: 中文 LLM-facing 描述（只覆盖 description，不改 schema）
# ---------------------------------------------------------------------------

PROTOCOL_TOOL_DESCRIPTIONS: dict[str, str] = {
    "say": (
        "对玩家说话。玩家看不到你的普通文本回复；需要让玩家听见时必须调用 say。\n"
        "必填：message。可选：emote，用 happy/sad/angry/heart/exclamation/question/laugh。\n"
        "示例：say(message=\"我来帮你浇水\", emote=\"happy\")。\n"
        "不要把行动计划写进 message 里代替真正行动；需要移动或操作时继续调用对应工具。"
    ),
    "emote": (
        "只显示表情气泡，不说话。用于补充情绪反馈。\n"
        "必填：emote_type，取值 happy/sad/angry/heart/exclamation/question/laugh。\n"
        "示例：emote(emote_type=\"question\")。如果要同时说话，优先用 say 的 emote 参数。"
    ),
    "face": (
        "原地转向，不移动、不交互。用于让角色面朝目标方向后再 interact 或 use_tool。\n"
        "必填：direction，取值 up/down/left/right。\n"
        "示例：face(direction=\"down\")。需要走到某格时用 move_to 或 go_to。"
    ),
    "move_to": (
        "在当前地图内移动到指定 tile。跨地图不要用 move_to，跨地图必须用 go_to。\n"
        "必填：mode、x、y。mode=\"exact\" 到指定格；mode=\"adjacent\" 站到目标旁边并必须传 adjacent_side；"
        "mode=\"edge_warp\" 走到地图边缘并必须传 direction。\n"
        "可选：face_direction 只适用于 mode=\"exact\"；timeout_frames 为正整数。\n"
        "示例：move_to(mode=\"exact\", x=10, y=12)。不要传 target 或 map_id。"
    ),
    "go_to": (
        "跨地图或指定地图坐标移动。必须已经知道目标地图和坐标。\n"
        "必填：map_id、x、y、arrival_mode。arrival_mode=\"exact\" 到指定格；arrival_mode=\"adjacent\" 站到目标旁边并必须传 stand_side。\n"
        "可选：face_direction 只适用于 exact；timeout_frames 为正整数。\n"
        "示例：go_to(map_id=\"Farm\", x=64, y=15, arrival_mode=\"exact\")。\n"
        "不要使用旧参数 target；不要写 go_to(target=\"Abigail\") 或 go_to(target=\"Farm:64,15\")。"
    ),
    "go_to_bed": (
        "通过正常寻路回房间床上睡觉并推进到下一天。用于一天结束、玩家明确要求睡觉或体力/时间需要收尾时。\n"
        "内部会扫描可睡床、调用 go_to 寻路到床的触发格、与床交互并确认睡觉；禁止使用 tp/传送。\n"
        "可选：timeout_frames，正整数。\n"
        "示例：go_to_bed(timeout_frames=1200)。不要用 go_to 去猜床坐标。"
    ),
    "interact": (
        "对相邻目标格执行交互键 X，例如对话、开门、开箱、收割、抚摸动物。\n"
        "必填：x、y，目标 tile 坐标；角色通常需要已经站在目标相邻格。\n"
        "示例：interact(x=10, y=11)。使用手持工具请用 use_tool，不要用 interact。"
    ),
    "use_tool": (
        "对目标 tile 使用当前手持工具 C，例如浇水、砍树、敲石头、锄地、割草。\n"
        "必填：x、y。可选：charge_level，0-4；普通点击用 0。\n"
        "示例：use_tool(x=10, y=11, charge_level=0)。需要先切换工具时先调用 equip。交互 NPC/箱子请用 interact。"
    ),
    "equip": (
        "切换当前手持物品。slot 和 item_name 二选一，必须且只能提供一个。\n"
        "slot 是工具栏 1-12；item_name 是精确物品名。\n"
        "示例：equip(slot=1) 或 equip(item_name=\"Axe\")。不要同时传 slot 和 item_name；不要传 item_category。"
    ),
    "query_inventory": (
        "兼容旧协议的低层背包查询。普通决策优先使用 observe(detail=\"inventory\") 或 search_facts(fact_type=\"inventory_item\")。\n"
        "可选：include_details，true 返回更详细信息。\n"
        "示例：query_inventory(include_details=true)。这是只读查询，不会装备物品；不要作为新 L3 查询主入口。"
    ),
    "query_location": (
        "兼容旧协议的低层位置查询。普通观察优先使用 observe() 或 query_runtime_snapshot(include_player=true)。\n"
        "不需要参数。示例：query_location()。这是只读查询；不要作为新 L3 查询主入口。"
    ),
    "query_map_exits": (
        "兼容旧协议的低层出口查询。普通观察优先使用 observe(detail=\"exits\") 或 query_world_region 的 door/warp facts。\n"
        "不需要参数。示例：query_map_exits()。这是只读查询，不会移动；不要作为新 L3 查询主入口。"
    ),
    "query_runtime_snapshot": (
        "查询 L1 运行时事实快照：玩家、游戏时间、天气、菜单状态等。只读，不会移动或操作。\n"
        "可选：include_player/include_world_time/include_weather/include_menu_state；不传时使用 Mod 默认范围。\n"
        "示例：query_runtime_snapshot(include_player=true, include_world_time=true)。"
    ),
    "query_world_region": (
        "查询地图区域内的 L1 World Fact，例如作物、树、地物、角色和 tile 信息。只读。\n"
        "可选：map_token；region={x,y,width,height,map_id?}；或 center={x,y,map_id?}+radius；"
        "world_entity_types 可过滤类型；include_tiles/include_characters/include_debug 控制返回内容。\n"
        "示例：query_world_region(map_token=\"Farm\", center={\"x\":64,\"y\":15}, radius=8, include_characters=true)。"
    ),
    "query_inventory_snapshot": (
        "查询 L1 库存 Fact。默认查询玩家背包；需要箱子/冰箱等容器时应先通过 query_world_region 拿到 RefId。\n"
        "可选：container_kind（默认 player）、container_ref（RefId 字符串）、include_empty_slots、include_details、include_debug。\n"
        "示例：query_inventory_snapshot(container_kind=\"player\", include_details=true)。不要臆造 container_ref。"
    ),
    "query_ui_snapshot": (
        "查询当前 UI/Menu Fact。用于确认当前是否开着菜单、菜单元素、可 inspect 的 UI RefId。只读。\n"
        "可选：include_elements（默认由 Mod 决定）、include_debug。\n"
        "示例：query_ui_snapshot(include_elements=true)。"
    ),
    "query_inspect_refs": (
        "批量 inspect 前序 Query Fact 返回的 RefId，获取更具体的 typed fact。只读。\n"
        "必填：refs，字符串数组；可选 include_debug。\n"
        "示例：query_inspect_refs(refs=[\"world_entity:tree:farm:41:12#wt_1\"])。refs 必须来自前序 query 结果，不要凭空编造。"
    ),
    "open_menu": (
        "打开游戏菜单。用于需要查看或操作菜单前。\n"
        "不需要参数。示例：open_menu()。打开后通常用 query_menu 获取组件，再用 menu_click。"
    ),
    "query_menu": (
        "查询当前菜单快照，包括菜单类型、标签页和可点击组件。\n"
        "不需要参数。示例：query_menu()。这是只读查询，不会点击。"
    ),
    "menu_click": (
        "点击当前菜单中的组件。\n"
        "必填：component_id 和 component_source。component_source 只能是 page 或 tab，值来自 query_menu 返回结果。\n"
        "示例：menu_click(component_id=1001, component_source=\"page\")。不要凭空猜 component_id。"
    ),
    "menu_close": (
        "关闭当前菜单。\n"
        "不需要参数。示例：menu_close()。如果没有菜单打开，结果可能表示已关闭或无操作。"
    ),
}

# ---------------------------------------------------------------------------
# Agent-local tools: retained for callers that explicitly need them.
# They are intentionally not included in get_core_tool_schemas().
# ---------------------------------------------------------------------------

LOCAL_TOOLS: list[dict[str, Any]] = [
    {
        "name": "observe",
        "description": (
            "感知周围环境的唯一入口，返回语义化文字描述（非原始 JSON）。支持三层渐进式查询：\n\n"
            "【层 0 — 概览】observe()\n"
            "不传参数，返回：当前位置/时间/天气、体力、手持工具、农场作物/动物聚合统计、附近 NPC 名单、地面可清理物数量。\n"
            "适用于：开始新决策前的整体感知，每次规划前的第一步。\n"
            "约 300-400 字，可支持 80% 的日常决策。\n\n"
            "【层 1 — 类别详情】observe(detail=\"类别\")\n"
            "返回指定类别的当前视野列表，每个实例附带可继续查询的 opaque ref_id。\n"
            "可选值：\n"
            "  - crops      — 所有作物（位置、品种、浇水状态、距收割天数）\n"
            "  - animals    — 所有动物（名字、类型、好感度、是否已抚摸）\n"
            "  - npcs       — 当前地图 NPC（名字、好感度、今日交互状态）\n"
            "  - containers — 箱子/冰箱/发货箱列表（位置、容量）\n"
            "  - inventory  — 背包物品（名字、数量、槽位）\n"
            "  - objects    — 地面物品（杂草、石头、树枝，按类型分组）\n"
            "  - exits      — 当前地图出口（目标地图名）\n\n"
            "【层 2 — 单实例详情】inspect(ref_id=...)\n"
            "查看某个具体实例的内部状态时，优先使用层 1 返回的 ref_id 调用 inspect。observe(detail=\"<opaque ref_id>\") 也会兼容转发到 inspect。\n"
            "不要主动构造或解析任何 id；只能原样使用前序 observe/search/inspect 返回的 opaque ref_id。\n\n"
            "【调用示例】\n"
            "- 『我们今天要做什么？』→ observe()，看整体状态再决策\n"
            "- 『附近哪些作物没浇水？』→ observe(detail=\"crops\")，只查看当前视野\n"
            "- 『帮我浇水』→ 直接调用匹配的浇水 skill/tool，让它自行查询、执行和复查\n"
            "- 『看看箱子里有什么』→ observe(detail=\"containers\")，再 inspect(ref_id=<容器 ref_id>)\n"
            "- 『Penny 今天在哪』→ observe(detail=\"npcs\")，必要时 inspect(ref_id=<NPC ref_id>)\n\n"
            "【与其他工具的边界】\n"
            "- observe 只读，不修改任何游戏状态\n"
            "- 玩家提出明确行动请求且已有匹配 skill/tool 时，优先调用该 skill/tool；observe/search_facts 的 0 结果只代表当前视野/查询范围，不代表全地图或所有可达目标\n"
            "- 需要游戏规则知识（NPC 喜好、作物季节）→ 用 search_wiki\n"
            "- 需要移动 → 用 go_to；需要交互 → 用 interact\n"
            "- ref_id 是观察输出中嵌入的不透明引用，只能原样传给 inspect 或相关工具，不要自行生成或解析"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "detail": {
                    # 7 固定枚举 + 6 动态格式（chest/fridge/shipping_bin/crop/animal/npc），
                    "type": "string",
                    "description": "查询目标。空=概览；类别名（crops/animals/npcs/containers/inventory/objects/exits）=列表；前序结果中的 opaque ref_id=单实例详情。不要构造或解析 ref_id。",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_wiki",
        "description": (
            "搜索星露谷百科知识库。\n\n"
            "当你不确定游戏事实时使用：NPC 喜好、作物季节、配方、矿洞信息等。\n"
            "用英文关键词搜索，如 search_wiki(query=\"Abigail loved gifts\")。\n"
            "找到页面后用 read_wiki 获取详情。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（建议使用英文）",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "inspect",
        "description": (
            "按 observe 或其他 Query 返回的 RefId 查看对象详情。inspect 只接受已有 ref，"
            "不会解析、生成或猜测 RefId；RefId 必须来自前序 observe/query 结果。\n"
            "适用于：从 observe 的代表 refs 中挑选一棵树、一株作物、一台机器或一批对象，"
            "查看具体 typed fact。支持批量 refs。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "refs": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "description": "来自前序 observe/query 结果的 RefId 字符串数组。",
                },
                "ref_id": {
                    "type": "string",
                    "description": "单个 RefId。若提供 refs，则优先使用 refs。",
                },
                "include_debug": {
                    "type": "boolean",
                    "description": "是否包含 debug 信息，默认 false。",
                },
            },
            "required": [],
        },
    },
    {
        "name": "find_interactions",
        "description": (
            "查询当前视野中的交互候选项。它基于当前 L1 Fact 和 L3 Query Core 返回可行动入口，"
            "例如可收获作物、待浇水作物、可收取机器、容器、NPC、UI 元素等。\n"
            "这个工具不接收 intent，不做任务规划，不执行动作；只返回候选及其 opaque ref。"
            "每个候选会尽量给出 tool_args 与 recommended_steps，但调用动作前必须先检查 execution_gate："
            "只有 status=ready 且前置条件满足时，才可按 recommended_steps 调用下一步工具；"
            "若 status=requires_resolution，必须先处理 execution_warnings，不要批量照做 recommended_steps。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "radius": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "当前视野查询半径，默认 8。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "最多返回多少个候选，默认 20。",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "分页偏移，默认 0；若返回 has_more=true，用 data.next_query 或 data.next_offset 继续查询下一页候选。",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_facts",
        "description": (
            "在当前视野的 L1 Facts 上执行 L3 filter/sort 查询。只支持 L3 schema 声明过的字段，"
            "例如 crop.watered、crop.ready_for_harvest、tree.has_moss、machine.ready_for_harvest、"
            "bed.can_sleep、furniture.furniture_type、exit.target_map、inventory_item.display_name 等。"
            "若不确定字段，先用不带 filters 的 search_facts 获取返回 data.schema。\n"
            "用于查找一组对象并拿到 opaque ref_id；对象详情继续用 inspect(ref_id=...)。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fact_type": {
                    "type": "string",
                    "enum": supported_fact_types(),
                    "description": "Fact 类型，例如 crop/tree/fruit_tree/machine/container/bed/furniture/exit/door/warp/resource_node/npc/farm_animal/inventory_item；exit/exits 会联合查询 door 与 warp。",
                },
                "filters": {
                    "type": "object",
                    "description": "简单 KV filter，例如 {\"has_moss\": true}、{\"has_moss\": {\"eq\": true}} 或 {\"growth_stage\": {\"min\": 3}}。",
                },
                "sort": {
                    "type": "string",
                    "description": "排序字段；留空时使用该 fact_type 的默认排序。用 -field 表示降序。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "最多返回多少个对象，默认 20。",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "分页偏移，默认 0；若返回 has_more=true，用 data.next_offset 继续查询下一页。",
                },
                "radius": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "当前视野查询半径，默认 8。",
                },
            },
            "required": ["fact_type"],
        },
    },
    {
        "name": "read_wiki",
        "description": (
            "阅读百科页面详情。\n\n"
            "search_wiki 找到页面后，用 read_wiki 获取完整内容。\n"
            "可指定 section 只读特定章节，减少信息量。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "description": "页面名称（从 search_wiki 结果获取）",
                },
                "section": {
                    "type": "string",
                    "description": "可选，只读指定章节名称",
                },
            },
            "required": ["page"],
        },
    },
    {
        "name": "remember",
        "description": (
            "记住重要信息，跨会话持久化。\n\n"
            "类型：\n"
            "- player_preference: 玩家偏好（例：玩家喜欢钓鱼）\n"
            "- relationship_insight: 关系洞察（例：和 Abigail 关系很好）\n"
            "- world_discovery: 世界发现（例：矿洞第 80 层有很多金矿）\n"
            "- strategy_lesson: 策略经验（例：春天种草莓最赚钱）"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "记忆类型",
                    "enum": [
                        "player_preference",
                        "relationship_insight",
                        "world_discovery",
                        "strategy_lesson",
                    ],
                },
                "content": {
                    "type": "string",
                    "description": "记忆内容",
                },
            },
            "required": ["type", "content"],
        },
    },
]

# ---------------------------------------------------------------------------
# 对外工具函数
# ---------------------------------------------------------------------------


def get_core_tool_names() -> list[str]:
    """返回默认暴露给 LLM 的 Protocol tool 名称列表。"""
    return [spec.tool_name for spec in iter_llm_exposed_specs()]


def get_core_tool_schemas() -> list[dict[str, Any]]:
    """返回默认 OpenAI Function Calling tools schema。

    参数 schema 来自 Protocol 2.4.5 registry，description 只做 LLM-facing
    中文增强，避免模型生成 legacy 参数导致 protocol mapper 拒绝。
    """
    schemas = deepcopy(get_static_protocol_tool_schemas())
    for schema in schemas:
        function = schema.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "")
        if name in PROTOCOL_TOOL_DESCRIPTIONS:
            function["description"] = PROTOCOL_TOOL_DESCRIPTIONS[name]
    return schemas


def get_core_tool_descriptions() -> dict[str, str]:
    """返回默认 Protocol tools 的 {name: description} 映射。"""
    descriptions: dict[str, str] = {}
    for spec in iter_llm_exposed_specs():
        descriptions[spec.tool_name] = PROTOCOL_TOOL_DESCRIPTIONS.get(
            spec.tool_name,
            spec.description,
        )
    return descriptions


def get_local_tool_names() -> list[str]:
    """返回明确的 Agent-local tool 名称；默认 core tools 不包含它们。"""
    return [tool["name"] for tool in LOCAL_TOOLS]


def get_local_tool_schemas() -> list[dict[str, Any]]:
    """返回 Agent-local tools 的 OpenAI Function Calling schema。"""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in deepcopy(LOCAL_TOOLS)
    ]


def get_local_tool_descriptions() -> dict[str, str]:
    """返回 Agent-local tools 的 {name: description} 映射。"""
    return {tool["name"]: tool["description"] for tool in LOCAL_TOOLS}
