from __future__ import annotations

import random
from typing import Any

from agent.debug.runtime import TurnTrace
from agent.execution.tool_base import ToolDecision
from agent.execution.tool_registry import ToolRegistry
from agent.interface.perceiver import GameContext
from agent.knowledge.router import KnowledgeRouter
from agent.llm.client import LLMClient, LLMDecision, LLMResponse
from agent.llm.prompt_builder import build_plan_prompt
from agent.memory.manager import MemoryManager
from agent.planning.goal import Goal, GoalManager, SubGoal
from agent.planning.plan_queue import PlanQueue
from agent.planning.task import Task


class Planner:
    def __init__(self, knowledge_router: KnowledgeRouter, llm_client: LLMClient) -> None:
        self.knowledge_router = knowledge_router
        self.llm_client = llm_client

    def plan_task(
        self,
        context: GameContext,
        memory: MemoryManager,
        registry: ToolRegistry,
        day_key: str,
        goal_manager: GoalManager,
        plan_queue: PlanQueue,
        trace: TurnTrace | None = None,
    ) -> tuple[Task | None, LLMResponse | None]:
        player_cmds = memory.working.pop_player_commands()
        player_mood = memory.emotion.get_mood()
        intents = self.knowledge_router.suggest_intents(context, player_mood)
        knowledge_cards = self.knowledge_router.get_cards(intents)
        knowledge_ids = [c.card_id for c in knowledge_cards]
        context_tags = self.knowledge_router.context_tags(context)
        matched_rules = memory.matched_rules(context_tags)
        working_mem = memory.working_recent(10)
        layered_memory = memory.planner_memory(day_key)
        force_switch = memory.force_switch
        prompt_budget = self.knowledge_router.build_prompt_budget(context, player_mood)

        if trace is not None:
            trace.record(
                "ceo_context",
                {
                    "context_tags": context_tags,
                    "knowledge_ids": knowledge_ids,
                    "matched_rule_count": len(matched_rules),
                    "working_memory_count": len(working_mem),
                    "short_term_counts": {k: len(v) for k, v in layered_memory["short_term"].items()},
                    "long_term_counts": {k: len(v) for k, v in layered_memory["long_term"].items()},
                    "force_switch": force_switch,
                },
            )

        if context.time_of_day >= 2200:
            memory.working.restore_player_commands(player_cmds)
            task = Task(description="go_to_bed", timeout_turns=3)
            self._set_goal_single(goal_manager, "go_to_bed", {}, "go_to_bed", "角色回到床上睡觉，进入第二天")
            return task, None

        goal_info = goal_manager.goal_info_for_prompt() if goal_manager else None
        queue_remaining = len(plan_queue) if plan_queue else 0

        emotion_ctx = layered_memory.get("emotion")
        if emotion_ctx and emotion_ctx.get("current_mood") == "unhappy":
            behavior_hints = memory.emotion.get_behavior_hints(context_tags)
            if behavior_hints:
                emotion_ctx["avoid_actions"] = [
                    h["action"] for h in behavior_hints if h["emotion_effect"] == "negative"
                ]

        if self.llm_client.enabled:
            prompt = build_plan_prompt(
                context,
                working_mem,
                layered_memory["short_term"],
                layered_memory["long_term"],
                matched_rules,
                knowledge_cards,
                force_switch=force_switch,
                goal_info=goal_info,
                plan_queue_remaining=queue_remaining,
                last_failure=memory.working.last_failure,
                past_days=layered_memory.get("past_days"),
                standing_orders=memory.working.standing_orders or None,
                player_commands=player_cmds or None,
                prompt_budget=prompt_budget,
                emotion_context=emotion_ctx,
            )
            if force_switch:
                memory.reset_force_switch()
                memory.working.clear()

            decisions, llm_response = self.llm_client.plan_multi(
                prompt, registry.schemas_for_planner(), trace=trace,
            )
            sanitized_list: list[dict[str, Any]] = []
            for d in decisions:
                s = self._sanitize_decision(context, registry, d.tool_name, d.arguments)
                if s is not None:
                    s["reason"] = d.reason
                    s["expectation"] = d.expectation or s["tool_name"]
                    sanitized_list.append(s)

            if sanitized_list:
                first = sanitized_list[0]
                description = first.get("reason", "llm_task")
                task = Task(
                    description=description[:120],
                    timeout_turns=max(10, len(sanitized_list) * 10),
                )
                if trace is not None:
                    trace.record("ceo_task_created", {
                        "source": "llm",
                        "task_id": task.task_id[:8],
                        "description": task.description,
                        "steps": len(sanitized_list),
                    })
                self._build_goal_from_decisions(sanitized_list, goal_manager)
                return task, llm_response

            if trace is not None and decisions:
                trace.record("ceo_invalid_llm", {"tool_name": decisions[0].tool_name, "arguments": decisions[0].arguments})
            elif trace is not None:
                trace.record("ceo_llm_failed", llm_response.as_dict())

            task, _ = self._fallback_task(context, goal_manager, trace)
            self._record_unhandled_player_commands(memory, player_cmds, day_key, context)
            return task, llm_response

        task, _ = self._fallback_task(context, goal_manager, trace)
        self._record_unhandled_player_commands(memory, player_cmds, day_key, context)
        return task, None

    def _fallback_task(
        self,
        context: GameContext,
        goal_manager: GoalManager,
        trace: TurnTrace | None = None,
    ) -> tuple[Task | None, None]:
        fb = self._fallback_plan(context)
        if fb is None:
            return None, None
        task = Task(description=fb["reason"], timeout_turns=5)
        self._set_goal_single(
            goal_manager,
            fb["tool_name"],
            fb["arguments"],
            fb["reason"],
            fb["expectation"],
        )
        if trace is not None:
            trace.record("ceo_task_created", {
                "source": "heuristic",
                "task_id": task.task_id[:8],
                "description": task.description,
            })
        return task, None

    def _set_goal_single(
        self,
        goal_manager: GoalManager,
        tool_name: str,
        arguments: dict[str, Any],
        description: str,
        expectation: str,
    ) -> None:
        sg = SubGoal(
            id="step_0",
            description=expectation,
            tool_name=tool_name,
            arguments=arguments,
        )
        goal = Goal.create(description=description, sub_goals=[sg])
        goal_manager.set_goal(goal)
        sg.status = "active"

    def _build_goal_from_decisions(
        self,
        sanitized: list[dict[str, Any]],
        goal_manager: GoalManager,
    ) -> None:
        sub_goals: list[SubGoal] = []
        for i, s in enumerate(sanitized):
            sub_goals.append(SubGoal(
                id=f"step_{i}",
                description=s.get("expectation", s["tool_name"]),
                tool_name=s["tool_name"],
                arguments=s["arguments"],
            ))
        goal = Goal.create(
            description=sanitized[0].get("reason", "llm_multi_step"),
            sub_goals=sub_goals,
        )
        goal_manager.set_goal(goal)
        sub_goals[0].status = "active"

    def _record_unhandled_player_commands(
        self,
        memory: MemoryManager,
        player_cmds: list[str],
        day_key: str,
        context: GameContext,
    ) -> None:
        if not player_cmds or self.llm_client.enabled:
            return
        for cmd in player_cmds:
            memory.record_event(
                category="player_dialogue",
                summary=f"player_command_no_llm | {cmd}",
                payload={"command": cmd},
                day_key=day_key,
                game_time=context.time_of_day,
                turn_id="",
                priority=3,
            )

    def _fallback_plan(self, context: GameContext) -> dict[str, Any] | None:
        if context.to_water and not context.block_water:
            return {
                "tool_name": "water_crops",
                "arguments": {"max_count": min(len(context.to_water), 30)},
                "reason": "dry_tiles_detected",
                "expectation": "所有未浇水的作物被浇灌",
            }
        if context.harvest_tiles:
            return {
                "tool_name": "harvest_crops",
                "arguments": {},
                "reason": "harvestable_tiles_detected",
                "expectation": "所有成熟作物被收割",
            }
        if (context.is_indoor
                and context.location_name == "FarmHouse"
                and 600 <= context.time_of_day < 2200):
            return {
                "tool_name": "navigate_to",
                "arguments": {"x": 64, "y": 15, "location": "Farm"},
                "reason": "indoor_exit_to_farm",
                "expectation": "从FarmHouse出门到达Farm，以便执行农场任务",
            }
        if context.nearby_npcs:
            for npc in context.nearby_npcs:
                if not npc.get("talked_today", False):
                    return {
                        "tool_name": "talk_to_npc",
                        "arguments": {"npc_name": npc["name"]},
                        "reason": "leisure_npc_chat",
                        "expectation": f"和{npc['name']}聊天增进好感",
                    }
        return self._leisure_wander(context)

    _LEISURE_SPOTS = [
        ("Town", 29, 67, "去镇上广场逛逛"),
        ("Beach", 30, 5, "去海边散步"),
        ("Forest", 50, 50, "去森林走走"),
        ("Mountain", 30, 15, "去山区转转"),
        ("Farm", 50, 20, "在农场附近走走"),
    ]

    def _is_leisure(self, context: GameContext) -> bool:
        return (not context.to_water or context.block_water) and not context.harvest_tiles

    def _leisure_wander(self, context: GameContext) -> dict[str, Any] | None:
        if context.time_of_day >= 2200 or context.time_of_day < 600:
            return None
        candidates = [s for s in self._LEISURE_SPOTS if s[0] != context.location_name]
        if not candidates:
            candidates = self._LEISURE_SPOTS
        loc, x, y, desc = random.choice(candidates)
        nav_args: dict[str, Any] = {"x": x, "y": y}
        if loc != context.location_name:
            nav_args["location"] = loc
        return {
            "tool_name": "navigate_to",
            "arguments": nav_args,
            "reason": "leisure_wander",
            "expectation": desc,
        }

    _COMPLEX_LOCATIONS = {"Mine", "VolcanoDungeon", "Caldera", "SkullCave", "CommunityCenter"}

    def _sanitize_decision(
        self,
        context: GameContext,
        registry: ToolRegistry,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not registry.has(tool_name):
            return None

        if tool_name == "water_crops":
            if context.block_water:
                return None
            mc = min(int(arguments.get("max_count") or 999), 999)
            return {"tool_name": tool_name, "arguments": {"max_count": mc}}

        if tool_name == "harvest_crops":
            return {"tool_name": tool_name, "arguments": {}}

        if tool_name == "go_to_bed":
            return {"tool_name": tool_name, "arguments": {}}

        if tool_name == "navigate_to":
            out: dict[str, Any] = {}
            try:
                out["x"] = int(arguments["x"])
                out["y"] = int(arguments["y"])
            except (KeyError, TypeError, ValueError):
                pass
            loc = arguments.get("location") or arguments.get("target_location")
            if isinstance(loc, str) and loc.strip():
                loc = loc.strip()
                if self._is_leisure(context) and loc in self._COMPLEX_LOCATIONS:
                    return None
                if loc != context.location_name:
                    out["location"] = loc
            if "x" not in out and "location" not in out:
                return None
            return {"tool_name": tool_name, "arguments": out}

        if tool_name == "talk_to_npc":
            npc = arguments.get("npc_name")
            if not isinstance(npc, str) or not npc.strip():
                return None
            return {"tool_name": tool_name, "arguments": {"npc_name": npc.strip()}}

        if tool_name == "give_gift":
            npc = arguments.get("npc_name")
            item = arguments.get("item_name")
            if not (isinstance(npc, str) and isinstance(item, str)):
                return None
            return {"tool_name": tool_name, "arguments": {"npc_name": npc.strip(), "item_name": item.strip()}}

        if tool_name == "send_chat":
            msg = arguments.get("message")
            if not isinstance(msg, str) or not msg.strip():
                return None
            return {"tool_name": tool_name, "arguments": {"message": msg.strip()}}

        if tool_name == "equip_item":
            item = arguments.get("item_name")
            if not isinstance(item, str) or not item.strip():
                return None
            return {"tool_name": tool_name, "arguments": {"item_name": item.strip()}}

        if tool_name == "query":
            tool_arg = arguments.get("tool")
            if not isinstance(tool_arg, str) or not tool_arg.strip():
                return None
            out2: dict[str, Any] = {"tool": tool_arg.strip()}
            args = arguments.get("args")
            if isinstance(args, dict):
                out2["args"] = args
            return {"tool_name": tool_name, "arguments": out2}

        if tool_name in ("use_tool_on_tile", "interact_with_tile", "place_item"):
            return {"tool_name": tool_name, "arguments": arguments}

        if tool_name == "drop_item":
            item = arguments.get("item_name")
            if not isinstance(item, str) or not item.strip():
                return None
            out3: dict[str, Any] = {"item_name": item.strip()}
            count = arguments.get("count")
            if count is not None:
                try:
                    out3["count"] = max(1, int(count))
                except (TypeError, ValueError):
                    pass
            return {"tool_name": tool_name, "arguments": out3}

        if tool_name == "ship_item":
            item = arguments.get("item_name")
            if not isinstance(item, str) or not item.strip():
                return None
            out4: dict[str, Any] = {"item_name": item.strip()}
            count = arguments.get("count")
            if count is not None:
                try:
                    out4["count"] = max(1, int(count))
                except (TypeError, ValueError):
                    pass
            return {"tool_name": tool_name, "arguments": out4}

        if tool_name == "plant_seeds":
            seed = arguments.get("seed_name")
            tiles = arguments.get("tiles")
            if not isinstance(seed, str) or not seed.strip():
                return None
            if not isinstance(tiles, list) or not tiles:
                return None
            valid = []
            for t in tiles[:30]:
                try:
                    valid.append({"x": int(t["x"]), "y": int(t["y"])})
                except (KeyError, TypeError, ValueError):
                    continue
            if not valid:
                return None
            return {"tool_name": tool_name, "arguments": {"seed_name": seed.strip(), "tiles": valid}}

        if tool_name == "buy_from_shop":
            shop = arguments.get("shop")
            item = arguments.get("item_name")
            if not (isinstance(shop, str) and isinstance(item, str)):
                return None
            qty = max(1, int(arguments.get("quantity") or 1))
            return {"tool_name": tool_name, "arguments": {"shop": shop.strip(), "item_name": item.strip(), "quantity": qty}}

        return {"tool_name": tool_name, "arguments": arguments}
