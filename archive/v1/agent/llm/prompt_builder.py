from __future__ import annotations

import json
from typing import Any

from agent.interface.perceiver import GameContext
from agent.knowledge.router import KnowledgeCard

_CACHED_SYSTEM: str | None = None


def _get_system_prompt() -> str:
    global _CACHED_SYSTEM
    if _CACHED_SYSTEM is not None:
        return _CACHED_SYSTEM
    _CACHED_SYSTEM = (
        "你是星露谷agent的规划器，也是玩家的同伴（Player 2）。"
        "【重要】你必须且只能通过tool_call输出动作。不要输出纯文本回复——玩家看不到你的文字，"
        "所有想说的话必须通过send_chat工具发送到游戏聊天框。"
        "你可以输出一个或多个tool_call来组成多步计划。"
        "每个tool_call必须包含expectation字段描述预期结果。"
        "【重要】只使用工具列表中提供的工具。如果某个动作不在工具列表中，不要尝试调用它。"
        "如果有learned_rules与当前场景匹配，优先遵守。"
        "短期记忆优先看player_dialogues，再看emotion_responses，再看special_events，再看action_results。"
        "长期记忆是按天压缩后的摘要，只在确有帮助时参考。"
        "past_days_highlights包含前几天的重要对话和事件，用于延续跨天意图。"
        "standing_orders是玩家的长期指令，每天都必须优先执行。"
        "你有人格：热情友好、偶尔搞笑、关心玩家。"
        "【情绪感知】你必须关注emotion字段中的玩家情绪状态。"
        "当玩家高兴时，多互动、聊天、做让玩家开心的事。"
        "当玩家不满时，立即停止当前行为，用send_chat道歉或安抚，然后做玩家想要的事。"
        "behavior_patterns记录了历史上哪些行为让玩家开心/不开心，务必参考。"
        "当nearby_npcs不为空且未交谈时，考虑talk_to_npc增进好感。"
        "【休闲模式】当water_tiles=0且harvest_tiles=0（无农活）时，你处于休闲状态。"
        "休闲时只做简单轻松的事：talk_to_npc、give_gift、send_chat闲聊、do_emote表情、"
        "或navigate_to去Town/Beach/Forest等地闲逛散步。"
        "休闲时严禁规划复杂任务：不要去Mine矿洞、不要尝试献祭/社区中心、不要做多步购买种植流程。"
        "休闲就是享受生活，随便走走、和人聊聊天就好。"
        "如果current_goal存在且plan_queue_remaining>0，说明已有计划在执行，不要重复规划。"
        "equipment字段显示当前装备和水壶水量，请据此判断是否需要先装备或灌水。"
        "如果last_failure存在，说明上一步失败了，必须避免重复相同的失败操作，换一个策略。"
        "如果某工具连续失败2次以上，立即停止使用它，换其他方式解决。"
        "【自然语言指令】玩家在聊天框输入 /agent 空格 再加文字（如 /agent 去海滩）会进入PLAYER_COMMANDS；"
        "本机普通聊天不会进入指令通道，主机请用 /agent 。"
        "【移动美观】当前地图内移动：navigate_to 只传 x 和 y，不要传 location，这样会走路寻路。"
        "跨地图移动才可传 location（语义为传送到目标图安全点）。"
        "跨图前可用 query 的 get_map_exits、get_location_info 看出口再规划。"
        "当indoor=true时你在室内（如FarmHouse），室外坐标不可直接到达。"
        "必须先用navigate_to并指定location参数传送到目标地图，再在该地图内仅用x、y移动。"
        "例如：从FarmHouse去Farm的(69,14)，应调用navigate_to(x=69,y=14,location='Farm')。"
        "常用地图名：Farm, Town, Beach, Mountain, Forest, Mine, FarmHouse, BusStop。"
        "【室内决策】当location是FarmHouse且indoor=true且时间在600~2100之间，"
        "你应该首先调用navigate_to(x=64,y=15,location='Farm')出门到农场。"
        "不要在室内反复idle或query——室内看不到农场作物数据，必须先出门。"
        "如果PLAYER_COMMANDS存在，这是玩家通过 /agent 或 / 前缀发出的即时指令，必须最优先响应！"
        "先用send_chat确认收到指令，然后立即执行玩家要求的动作。"
    )
    return _CACHED_SYSTEM


def build_plan_prompt(
    context: GameContext,
    working_memory: list[dict[str, Any]],
    short_term_memory: dict[str, list[dict[str, Any]]],
    long_term_memory: dict[str, list[dict[str, Any]]],
    rules: list[dict[str, Any]],
    knowledge_cards: list[KnowledgeCard],
    force_switch: bool = False,
    goal_info: dict[str, Any] | None = None,
    plan_queue_remaining: int = 0,
    last_failure: dict[str, Any] | None = None,
    past_days: dict[str, list[dict[str, Any]]] | None = None,
    standing_orders: list[str] | None = None,
    player_commands: list[str] | None = None,
    prompt_budget: dict[str, int] | None = None,
    emotion_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    compact_working = [
        {
            "action": m.get("action"),
            "expectation": m.get("expectation"),
            "outcome": m.get("outcome"),
            "lesson": m.get("lesson"),
        }
        for m in working_memory[-10:]
    ]
    _b = prompt_budget or {}
    compact_short_term = {
        "player_dialogues": _compact_memory_items(short_term_memory.get("player_dialogue") or [])[:_b.get("player_dialogues", 8)],
        "special_events": _compact_memory_items(short_term_memory.get("special_event") or [])[:_b.get("special_events", 8)],
        "action_results": _compact_memory_items(short_term_memory.get("action_result") or [])[:_b.get("action_results", 4)],
        "decision_trace": _compact_memory_items(short_term_memory.get("decision_trace") or [])[:_b.get("decision_trace", 3)],
        "emotion_learnings": _compact_memory_items(short_term_memory.get("emotion_response") or [])[:_b.get("emotion_learnings", 5)],
    }
    compact_long_term = {
        "player_dialogues": _compact_memory_items(long_term_memory.get("player_dialogues") or []),
        "special_events": _compact_memory_items(long_term_memory.get("special_events") or []),
        "action_results": _compact_memory_items(long_term_memory.get("action_results") or []),
        "emotion_responses": _compact_memory_items(long_term_memory.get("emotion_responses") or []),
    }
    compact_cards = [
        {"id": c.card_id, "content": _compact_card(c.content)}
        for c in knowledge_cards[:5]
    ]
    compact_rules = [
        {"condition": r.get("condition"), "action": r.get("action"), "confidence": r.get("confidence")}
        for r in rules[:5]
    ]
    nearby_npcs = []
    for npc in (context.nearby_npcs or [])[:3]:
        nearby_npcs.append({
            "name": npc.get("name"),
            "distance": npc.get("distance"),
            "talked": npc.get("talked_today", False),
        })
    inv_items = (context.inventory.get("items") or [])
    tools = [{"name": i.get("name"), "slot": i.get("slot")} for i in inv_items if i.get("type") == "tool" and i.get("name")]
    valuables = sorted(
        [i for i in inv_items if i.get("name") and i.get("type") != "tool"],
        key=lambda i: i.get("sell_price", 0) or 0,
        reverse=True,
    )[:3]
    compact_inv = [{"name": i.get("name"), "stack": i.get("stack", 1)} for i in valuables]
    farm_crops = (context.farm_summary.get("crops") or {})
    farm_brief = {
        "unwatered": farm_crops.get("unwatered_count", 0),
        "harvestable": farm_crops.get("harvestable_count", 0),
    }

    equip_info: dict[str, Any] = {"equipped": context.equipped_item_name or "none"}
    if context.water_level >= 0:
        equip_info["water"] = f"{context.water_level}/{context.water_capacity}"

    user_payload: dict[str, Any] = {
        "state": context.state_summary(),
        "ctx": {
            "location": context.location_name,
            "time": context.time_of_day,
            "season": context.season,
            "day": context.day_of_month,
            "energy": f"{context.energy_current}/{context.energy_max}",
            "rain": context.block_water,
            "indoor": context.is_indoor,
            "water_tiles": len(context.to_water),
            "harvest_tiles": len(context.harvest_tiles),
            "nearby_npcs": nearby_npcs,
            "equipment": equip_info,
            "tools_in_bag": tools,
            "inventory_highlights": compact_inv,
            "farm": farm_brief,
        },
        "working_memory": compact_working,
        "short_term_memory": compact_short_term,
        "long_term_memory": compact_long_term,
        "rules": compact_rules,
        "knowledge": compact_cards,
    }
    if emotion_context:
        user_payload["emotion"] = emotion_context
    if goal_info:
        user_payload["current_goal"] = goal_info
    if plan_queue_remaining > 0:
        user_payload["plan_queue_remaining"] = plan_queue_remaining
    if last_failure:
        user_payload["last_failure"] = last_failure
    if past_days:
        compact_past = {}
        for k, v in past_days.items():
            compact_past[k] = _compact_memory_items(v) if v else []
        if any(compact_past.values()):
            user_payload["past_days_highlights"] = compact_past
    if standing_orders:
        user_payload["standing_orders"] = standing_orders
    if player_commands:
        user_payload["PLAYER_COMMANDS"] = player_commands
        user_payload["PLAYER_COMMANDS_HINT"] = (
            "玩家刚刚通过 /agent … 或 /指令 下达，必须最优先响应！"
        )
    if force_switch:
        user_payload["FORCE_SWITCH"] = True
        user_payload["hint"] = "连续失败3次，必须换一个完全不同的策略"
    return {
        "messages": [
            {"role": "system", "content": _get_system_prompt()},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    }


def build_reflect_prompt(
    expectation: str,
    actual_result: Any,
    context_summary: str,
    recent_reflections: list[dict[str, Any]],
) -> dict[str, Any]:
    user_payload = {
        "expectation": expectation,
        "actual_result": _compact_result(actual_result),
        "context": context_summary,
        "recent": [
            {"action": r.get("action"), "outcome": r.get("outcome"), "lesson": r.get("lesson")}
            for r in recent_reflections[-5:]
        ],
    }
    system = (
        "你是星露谷agent的反思模块。"
        "对比expectation和actual_result，输出严格JSON：\n"
        '{"outcome":"success|partial|fail","lesson":"一句话教训","should_distill":true/false,'
        '"suggested_rule":{"condition":"...","action":"..."} 或 null}\n'
        "只输出JSON，不要其他内容。"
    )
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    }


def build_distill_prompt(source: Any) -> dict[str, Any]:
    content = source if isinstance(source, str) else json.dumps(source, ensure_ascii=False)
    system = (
        "你是星露谷agent的经验蒸馏器。"
        "从以下按类别整理后的经验中提取可复用的规则，输出JSON数组：\n"
        '[{"condition":"触发条件描述","action":"应执行的动作","confidence":0.7},...]\n'
        "特别注意情绪相关的规则：当玩家表达某种情绪时应该怎么做。\n"
        "只输出JSON数组，不要其他内容。最多5条规则。"
    )
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    }


def _compact_result(result: Any) -> Any:
    if isinstance(result, list):
        out = []
        for item in result[:3]:
            if isinstance(item, dict):
                out.append({
                    "success": item.get("success"),
                    "message": item.get("message"),
                    "error_code": item.get("error_code"),
                })
        return out
    return result


def _compact_card(content: str) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return "\n".join(lines[:4])


def _compact_memory_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        entry: dict[str, Any] = {
            "summary": item.get("summary"),
            "game_time": item.get("game_time") or item.get("latest_time"),
            "count": item.get("count", 1),
        }
        emotion = (item.get("details") or {}).get("emotion")
        if emotion:
            entry["emotion"] = emotion
        out.append(entry)
    return out
