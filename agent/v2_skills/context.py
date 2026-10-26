"""
v2_skills.context — SkillContext：技能与游戏引擎的唯一交互通道。

SkillContext 封装了对 FileBridge 的异步调用，
让 Python async 技能可以通过简洁的 API 编排 Mod 侧的 L1 能力。

设计决策：
    - C1: 查询方法返回裸数据（list/dict），失败时抛 BridgeError
    - C3: get_state() 每次读文件，无缓存
    - 异步适配：通过 run_in_executor 将同步的 FileBridge 调用桥接到 asyncio
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from slow_loop.action_policy import BRIDGE_POLL_INTERVAL_S, timeout_for_action
from v2_skills.errors import BridgeError, SkillCancelled
from v2_skills.models import GameState

log = logging.getLogger("v2_skills.context")


class SkillContext:
    """技能执行上下文，提供对 Mod 侧 L1 能力的异步访问。

    所有与游戏引擎的交互必须通过此对象进行，技能代码不应直接访问 FileBridge。

    Args:
        bridge: FileBridge 实例，用于 IPC 通信。
        game_state_path: game_state.json 的文件路径。
        logger: 日志记录器。
    """

    def __init__(self, bridge: Any, game_state_path: Path, logger: logging.Logger | None = None):
        self._bridge = bridge
        self._game_state_path = Path(game_state_path)
        self._logger = logger or log
        self._cancelled = False
        self._cancel_lock = threading.Lock()
        self._active_action_id: str | None = None
        self._cancelled_action_ids: set[str] = set()

    # ── L1 查询（返回裸数据，失败抛 BridgeError）──────────────────────────────

    async def find_targets(
        self,
        type: str,
        filter: dict | None = None,
        sort: str = "nearest",
        max: int = 50,
    ) -> list[dict]:
        """查找指定类型的目标。

        Args:
            type: 目标类型，如 "animal"、"npc"、"crop"。
            filter: 过滤条件 dict，如 {"ungreeted": True}。
            sort: 排序方式，默认 "nearest"。
            max: 最大返回数量，默认 50。

        Returns:
            目标列表（list[dict]），每个元素包含 name、location_name 等字段。

        Raises:
            BridgeError: Mod 侧执行失败。
        """
        params = {"target_type": type, "sort": sort, "max": max}
        # filter 参数展开到顶层（Mod 侧直接从 params 读取过滤键）
        if filter:
            params.update(filter)
        result = await self._execute_and_wait("find_targets", params)
        # find_targets 返回 data 中的列表（key 因 target_type 不同：animals/crops/trees/...）
        data = self._unwrap_query(result)
        if isinstance(data, dict):
            # 提取第一个 list 类型的值（适配 animals/crops/trees/targets 等不同 key）
            for v in data.values():
                if isinstance(v, list):
                    return v
            return []
        return data if isinstance(data, list) else []

    async def query_farm_summary(self) -> dict:
        """查询农场摘要（作物、动物、机器状态）。"""
        result = await self._execute_and_wait("query_farm_summary", {})
        return self._unwrap_query(result)

    async def query_time(self) -> dict:
        """查询当前游戏时间。"""
        result = await self._execute_and_wait("query_time", {})
        return self._unwrap_query(result)

    async def query_inventory(self) -> list[dict]:
        """查询背包物品列表。"""
        result = await self._execute_and_wait("query_inventory", {})
        return self._unwrap_query(result)

    async def query_runtime_snapshot(
        self,
        *,
        include_player: bool = True,
        include_world_time: bool = True,
        include_weather: bool = False,
        include_menu_state: bool = True,
    ) -> dict:
        result = await self.query(
            "query_runtime_snapshot",
            {
                "include_player": include_player,
                "include_world_time": include_world_time,
                "include_weather": include_weather,
                "include_menu_state": include_menu_state,
            },
        )
        return self._unwrap_protocol_payload(result, "query_runtime_snapshot")

    async def query_world_region(
        self,
        *,
        map_token: str | None = None,
        center: dict | None = None,
        region: dict | None = None,
        radius: int = 8,
        world_entity_types: list[str] | None = None,
        include_tiles: bool = False,
        include_characters: bool = False,
        include_debug: bool = False,
    ) -> dict:
        params: dict[str, Any] = {
            "radius": radius,
            "include_tiles": include_tiles,
            "include_characters": include_characters,
            "include_debug": include_debug,
        }
        if map_token:
            params["map_token"] = map_token
        if center is not None:
            params["center"] = center
        if region is not None:
            params["region"] = region
        if world_entity_types:
            params["world_entity_types"] = world_entity_types
        result = await self.query("query_world_region", params)
        return self._unwrap_protocol_payload(result, "query_world_region")

    async def query_inventory_snapshot(
        self,
        *,
        container_kind: str = "player",
        container_ref: str | None = None,
        include_empty_slots: bool = False,
        include_details: bool = True,
        include_debug: bool = False,
    ) -> dict:
        params: dict[str, Any] = {
            "container_kind": container_kind,
            "include_empty_slots": include_empty_slots,
            "include_details": include_details,
            "include_debug": include_debug,
        }
        if container_ref:
            params["container_ref"] = container_ref
        result = await self.query("query_inventory_snapshot", params)
        return self._unwrap_protocol_payload(result, "query_inventory_snapshot")

    async def query_inspect_refs(
        self,
        refs: list[str],
        *,
        include_debug: bool = False,
    ) -> dict:
        result = await self.query(
            "query_inspect_refs",
            {"refs": refs, "include_debug": include_debug},
        )
        return self._unwrap_protocol_payload(result, "query_inspect_refs")

    # ── L1 交互（返回 Result dict）────────────────────────────────────────────

    async def go_to(self, target: str, arrive_distance: int = 1) -> dict:
        """L2 语义导航：支持 NPC 名、动物名、建筑名、地图名、TargetRef 的跨地图导航。

        Args:
            target: 目标标识符（如 "Abigail"、"甜豆"、"鸡舍"、"Farm:animal_Daisy"）。
            arrive_distance: 到达判定距离，默认 1 格。
        """
        if self._protocol_transport_enabled():
            structured = _parse_structured_target(target)
            if structured is None:
                raise BridgeError(
                    "INVALID_PARAMS",
                    "protocol transport requires go_to target in 'Map:x,y' form; "
                    f"semantic target is not protocol-addressable yet: {target}",
                )
            map_id, x, y = structured
            return await self._execute_and_wait(
                "go_to",
                {"map_id": map_id, "x": x, "y": y, "arrival_mode": "exact"},
                timeout=timeout_for_action("go_to"),
            )
        return await self._execute_and_wait("go_to", {
            "target": target, "arrive_distance": arrive_distance,
        }, timeout=timeout_for_action("go_to"))

    async def move_to(self, x: int, y: int, location: str | None = None) -> dict:
        """L1 同地图坐标移动。

        Args:
            x: 目标 tile X 坐标。
            y: 目标 tile Y 坐标。
            location: 可选目标地图名。
        """
        if self._protocol_transport_enabled() and location:
            return await self._execute_and_wait(
                "go_to",
                {"map_id": location, "x": x, "y": y, "arrival_mode": "exact"},
                timeout=timeout_for_action("go_to"),
            )
        params: dict[str, Any] = {"x": x, "y": y}
        if self._protocol_transport_enabled():
            params["mode"] = "exact"
        if location:
            params["location"] = location
        return await self._execute_and_wait("move_to", params, timeout=timeout_for_action("move_to"))

    async def move_adjacent(
        self,
        x: int,
        y: int,
        adjacent_side: str = "down",
        *,
        timeout: float | None = None,
    ) -> dict:
        params = {"mode": "adjacent", "x": x, "y": y, "adjacent_side": adjacent_side}
        return await self._execute_and_wait(
            "move_to",
            params,
            timeout=timeout if timeout is not None else timeout_for_action("move_to"),
        )

    async def equip(self, *, slot: int | None = None, item_name: str | None = None) -> dict:
        params: dict[str, Any] = {}
        if slot is not None:
            params["slot"] = slot
        if item_name is not None:
            params["item_name"] = item_name
        return await self._execute_and_wait("equip", params, timeout=timeout_for_action("equip"))

    async def use_tool(self, x: int, y: int, *, charge_level: int = 0) -> dict:
        return await self._execute_and_wait(
            "use_tool",
            {"x": x, "y": y, "charge_level": charge_level},
            timeout=timeout_for_action("use_tool"),
        )

    async def interact(self, x: int, y: int) -> dict:
        return await self._execute_and_wait(
            "interact",
            {"x": x, "y": y},
            timeout=timeout_for_action("interact"),
        )

    async def go_to_bed(self, *, timeout_frames: int = 9000) -> dict:
        params: dict[str, Any] = {}
        if timeout_frames > 0:
            params["timeout_frames"] = timeout_frames
        return await self._execute_and_wait(
            "go_to_bed",
            params,
            timeout=timeout_for_action("go_to_bed"),
        )

    async def do(self, intent: str, target: str | None = None, **kwargs: Any) -> dict:
        """执行意图动作。

        Args:
            intent: 动作意图，如 "pet"、"harvest"。
            target: 可选目标标识符。
            **kwargs: 额外参数，透传给 Mod 侧。

        Returns:
            Mod 侧返回的结果 dict。
        """
        params: dict[str, Any] = {"intent": intent}
        if target:
            params["target"] = target
        params.update(kwargs)
        return await self._execute_and_wait("do", params)

    # ── L2 批量执行 ─────────────────────────────────────────────────────────

    async def execute_on_targets(
        self,
        targets: list[str],
        action: str = "do",
        *,
        tool: str | None = None,
        tool_category: str | None = None,
        intent: str | None = None,
        intent_args: dict | None = None,
        repeat_per_target: str = "once",
        max_count: int = 0,
        stamina_below: int = 0,
        time_after: int = 0,
    ) -> dict:
        """批量对目标列表执行动作（L2 通用引擎）。

        Args:
            targets: TargetRef 列表，如 ["Farm:tile_12_34", "Farm:animal_Daisy"]。
            action: "use_tool" 或 "do"。
            tool: use_tool 时必需，工具名（如 "Milk Pail"）。
            intent: do 时可选意图（如 "pet"、"harvest"）。
            intent_args: intent 额外参数（如 {"item": "Parsnip Seeds"}）。
            repeat_per_target: "once" | "until_gone" | "count:N"。
            max_count: 最大处理目标数，0=不限。
            stamina_below: 体力低于此值时停止，0=不检查。
            time_after: 游戏时间超过此值时停止（如 2200），0=不检查。
        """
        params: dict[str, Any] = {
            "targets": targets,
            "action": action,
            "repeat_per_target": repeat_per_target,
        }
        if tool:
            params["tool"] = tool
        if tool_category:
            params["tool_category"] = tool_category
        if intent:
            params["intent"] = intent
        if intent_args:
            params["intent_args"] = intent_args
        if max_count > 0:
            params["max_count"] = max_count
        stop: dict[str, int] = {}
        if stamina_below > 0:
            stop["stamina_below"] = stamina_below
        if time_after > 0:
            stop["time_after"] = time_after
        if stop:
            params["stop_conditions"] = stop
        return await self._execute_and_wait(
            "execute_on_targets", params, timeout=timeout_for_action("execute_on_targets")
        )

    async def water_crops_compound(self, *, max_count: int = 0) -> dict:
        """Run the Mod-side water_crops compound action.

        The current protocol envelope does not define a water_crops oneof branch yet.
        This method intentionally uses the existing legacy JSON command path so the
        L3 skill can reuse Mod-side navigation, per-tile watering, and refill logic.
        """
        params: dict[str, Any] = {}
        if max_count > 0:
            params["max_count"] = max_count
        return await self._execute_and_wait(
            "water_crops",
            params,
            timeout=timeout_for_action("water_crops"),
            use_legacy_command=True,
        )

    async def harvest_crops_compound(
        self,
        *,
        max_count: int = 0,
        replant_seed: str | None = None,
    ) -> dict:
        """Run the Mod-side harvest_crops compound action.

        Like water_crops, harvest_crops still lives on the legacy JSON command path.
        The L3 skill delegates here so Mod-side traversal owns the whole-map scan,
        navigation, harvesting, and optional replanting loop.
        """
        params: dict[str, Any] = {}
        if max_count > 0:
            params["max_count"] = max_count
        if replant_seed:
            params["replant_seed"] = replant_seed
        return await self._execute_and_wait(
            "harvest_crops",
            params,
            timeout=timeout_for_action("harvest_crops"),
            use_legacy_command=True,
        )

    async def menu_interact(
        self,
        menu_type: str,
        operations: list[dict],
        *,
        target: str | None = None,
        shop: str | None = None,
    ) -> dict:
        """统一菜单交互（L2 声明式事务执行器）。

        Args:
            menu_type: 菜单类型 — "Shop", "Container", "Craft", "Ship"。
            operations: 操作列表，如 [{"action": "buy", "item": "Parsnip Seeds", "qty": 10}]。
            target: 可选导航目标（NPC 名、container_id、建筑名）。
            shop: 可选商店 ID（Shop 类型时使用）。
        """
        params: dict[str, Any] = {
            "menu_type": menu_type,
            "operations": operations,
        }
        if target:
            params["target"] = target
        if shop:
            params["shop"] = shop
        return await self._execute_and_wait(
            "menu_interact", params, timeout=timeout_for_action("menu_interact")
        )

    # ── L1 表达 ──────────────────────────────────────────────────────────────

    async def say(self, message: str) -> None:
        """发送聊天消息。"""
        await self._execute_and_wait("say", {"message": message}, timeout=5.0)

    async def emote(self, type: str) -> None:
        """播放表情动作。"""
        await self._execute_and_wait("emote", {"type": type}, timeout=5.0)

    # ── observe Skill 接口适配层 ─────────────────────────────────────────────

    def get_game_state(self) -> dict:
        """
        同步读取 game_state.json，返回 raw dict（供 observe Skill 使用）。

        与 MockObserveCtx.get_game_state() 签名一致，使 observe Skill 代码可以
        用 duck typing 无缝在生产环境和测试环境中运行。

        注意：此方法同步读取文件，仅应在 observe 的采集阶段调用一次，
        不应在热循环中频繁使用（高频状态读取请用 get_state()）。

        Returns:
            game_state.json 的完整 dict，读取失败时返回空 dict。
        """
        try:
            return json.loads(self._game_state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def get_alias_map(self) -> dict:
        """
        获取地图别名映射（供 observe Skill 使用）。

        生产环境中 alias_map 由 query_buildings 动态构建（spec §7.4）。
        SkillContext 自身不缓存 alias_map（避免 GUID 失效），
        此方法返回空 dict，由 observe Skill 的 collect_layer0 在采集时动态填充。

        与 MockObserveCtx.get_alias_map() 签名一致。

        Returns:
            空 dict（生产环境由采集层从 query_buildings 填充）
        """
        return {}

    async def query(self, capability: str, params: dict | None = None) -> dict:
        """
        通用 query_* 适配器（供 observe Skill 使用）。

        将 observe Skill 的 ctx.query(capability, params) 调用适配到
        SkillContext._execute_and_wait + _unwrap_query 管线。

        与 MockObserveCtx.query() 签名一致。

        Args:
            capability: Mod 侧 capability name（如 "find_targets", "query_containers"）
            params: 能力参数 dict，None 视为空 dict

        Returns:
            query_* 返回的 data dict（经 _unwrap_query 解包）

        Raises:
            BridgeError: Mod 侧执行失败
        """
        result = await self._execute_and_wait(capability, params or {})
        return self._unwrap_query(result)

    # ── 状态 ─────────────────────────────────────────────────────────────────

    async def get_state(self) -> GameState:
        """读取 game_state.json，解析为 GameState。

        每次调用都读文件（无缓存），确保获取最新状态（决策 C3）。

        Returns:
            GameState 实例。

        Raises:
            FileNotFoundError: game_state.json 不存在。
            json.JSONDecodeError: JSON 解析失败。
        """
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None, lambda: json.loads(self._game_state_path.read_text(encoding="utf-8"))
        )
        return GameState.from_dict(raw)

    # ── 中断 ─────────────────────────────────────────────────────────────────

    def check_cancelled(self) -> None:
        """检查取消标记，已取消则抛出 SkillCancelled。

        技能实现应在循环中定期调用此方法，以支持外部取消。
        """
        if self._cancelled:
            raise SkillCancelled()

    @property
    def is_cancelled(self) -> bool:
        """是否已被标记取消。"""
        return self._cancelled

    def cancel(self) -> None:
        """标记技能为已取消。由 SkillRunner 调用。"""
        with self._cancel_lock:
            self._cancelled = True
            action_id = self._active_action_id
        if action_id:
            self._cancel_action_once(action_id)

    # ── 内部方法 ─────────────────────────────────────────────────────────────

    def _unwrap_query(self, result: dict) -> Any:
        """查询结果解包：成功返回 data，失败抛 BridgeError。

        BridgeResult 已被转为 dict（通过 _bridge_result_to_dict），
        结构为 {"success": bool, "status": str, "data": ..., "observation": str, "error": {...}}。

        Args:
            result: 从 _execute_and_wait 返回的结果 dict。

        Returns:
            查询数据（data 或 observations）。

        Raises:
            BridgeError: result.success 为 False。
        """
        if result.get("success"):
            return result.get("data", result.get("observation", ""))
        error = result.get("error", {})
        if isinstance(error, dict):
            raise BridgeError(
                error.get("code", "UNKNOWN"),
                error.get("message", str(result)),
            )
        raise BridgeError("UNKNOWN", str(result))

    def _unwrap_protocol_payload(self, result: dict, field: str) -> dict:
        if not isinstance(result, dict):
            return {}
        protocol_data = result.get("protocol_data")
        if isinstance(protocol_data, dict):
            return protocol_data
        nested = result.get(field)
        if isinstance(nested, dict):
            return nested
        return result

    async def _execute_and_wait(
        self,
        capability: str,
        params: dict,
        timeout: float | None = None,
        *,
        use_legacy_command: bool = False,
    ) -> dict:
        """发送命令到 Mod 并轮询等待结果。

        通过 run_in_executor 将同步的 FileBridge 操作桥接到 asyncio，
        避免阻塞 event loop。

        轮询逻辑参考 DeliberativeLoop._wait_for_result，
        使用 bridge.write_command + bridge.read_command_result 轮询。

        Args:
            capability: L1 能力名称（如 "move_to"、"do"）。
            params: 能力参数 dict。
            timeout: 等待超时时间（秒），默认 30。

        Returns:
            结果 dict，含 success/data/error 等字段。
        """
        loop = asyncio.get_running_loop()
        timeout_s = timeout_for_action(capability) if timeout is None else timeout
        if self._cancelled:
            raise SkillCancelled()

        # 发送命令（同步操作，在 executor 中运行）
        writer = self._bridge.write_command
        if use_legacy_command:
            legacy_writer = getattr(self._bridge, "write_legacy_command", None)
            if callable(legacy_writer):
                writer = legacy_writer
        action_id = await loop.run_in_executor(None, lambda: writer(capability, params))
        self._set_active_action_id(action_id)
        self._logger.debug(
            "SkillContext: 已发送 %s params=%s action_id=%s",
            capability, params, action_id,
        )

        try:
            if self._cancelled:
                await loop.run_in_executor(
                    None, lambda: self._cancel_action_once(action_id)
                )
                raise SkillCancelled()

            # 轮询等待结果
            deadline = time.monotonic() + timeout_s
            poll_interval = BRIDGE_POLL_INTERVAL_S

            while time.monotonic() < deadline:
                # 检查取消
                if self._cancelled:
                    # 取消 Mod 侧的命令
                    await loop.run_in_executor(
                        None, lambda: self._cancel_action_once(action_id)
                    )
                    raise SkillCancelled()

                result = await loop.run_in_executor(
                    None, lambda: self._bridge.read_command_result(action_id)
                )
                if result is not None:
                    # BridgeResult -> dict
                    return self._bridge_result_to_dict(result)

                await asyncio.sleep(poll_interval)

            # 超时：取消命令并返回超时错误
            self._logger.warning(
                "SkillContext: 等待超时 action_id=%s capability=%s timeout=%.1fs",
                action_id, capability, timeout_s,
            )
            await loop.run_in_executor(
                None, lambda: self._cancel_action_once(action_id)
            )
            return {
                "success": False,
                "data": {},
                "observation": "",
                "status": "failed",
                "error": {"code": "TIMEOUT", "message": f"{capability} 超时 ({timeout_s}s)"},
            }
        finally:
            self._clear_active_action_id(action_id)

    def _set_active_action_id(self, action_id: str) -> None:
        with self._cancel_lock:
            self._active_action_id = action_id

    def _clear_active_action_id(self, action_id: str) -> None:
        with self._cancel_lock:
            if self._active_action_id == action_id:
                self._active_action_id = None

    def _cancel_action_once(self, action_id: str) -> None:
        with self._cancel_lock:
            if action_id in self._cancelled_action_ids:
                return
            self._cancelled_action_ids.add(action_id)
        try:
            self._bridge.cancel_command(action_id)
        except Exception:
            self._logger.debug("SkillContext: cancel_command failed action_id=%s", action_id, exc_info=True)

    def _protocol_transport_enabled(self) -> bool:
        checker = getattr(self._bridge, "is_protocol_transport_enabled", None)
        if callable(checker):
            try:
                enabled = checker()
            except Exception:
                enabled = None
            if isinstance(enabled, bool):
                return enabled
        enabled = getattr(self._bridge, "_protocol_transport", False)
        return enabled if isinstance(enabled, bool) else False

    @staticmethod
    def _bridge_result_to_dict(result: Any) -> dict:
        """将 BridgeResult 转为普通 dict。

        BridgeResult 是 dataclass，技能代码不应依赖其类型，
        统一转为 dict 以解耦。

        Args:
            result: BridgeResult 实例。

        Returns:
            标准化的结果 dict。
        """
        # 如果已经是 dict（测试 mock 场景），直接返回
        if isinstance(result, dict):
            return result

        d: dict[str, Any] = {
            "success": result.success,
            "status": result.status,
            "data": result.data or {},
            "observation": result.observation or "",
            "side_effects": result.side_effects or [],
            "action_id": result.action_id,
            "seq": result.seq,
        }
        if not result.success:
            d["error"] = {
                "code": result.error_code or "UNKNOWN",
                "message": result.message or "",
            }
        return d


def _parse_structured_target(target: str) -> tuple[str, int, int] | None:
    text = (target or "").strip()
    if not text or ":" not in text:
        return None
    map_id, coords = text.split(":", 1)
    map_id = map_id.strip()
    if not map_id or "," not in coords:
        return None
    x_text, y_text = coords.split(",", 1)
    try:
        return map_id, int(x_text.strip()), int(y_text.strip())
    except ValueError:
        return None
