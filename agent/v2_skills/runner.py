"""
v2_skills.runner — SkillRunner：技能执行引擎。

负责创建 SkillContext、管理超时和异常，
将技能的 async 函数执行结果标准化为 Result dict。
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from v2_skills.context import SkillContext
from v2_skills.errors import BridgeError, SkillCancelled

log = logging.getLogger("v2_skills.runner")


class SkillRunner:
    """技能执行引擎。

    为每次技能执行创建独立的 SkillContext，
    管理超时（asyncio.wait_for）和异常捕获，
    确保返回标准化的 Result dict。

    Args:
        bridge: FileBridge 实例。
        game_state_path: game_state.json 路径。
        logger: 日志记录器（可选）。
    """

    MAX_TIMEOUT = 300.0  # 技能最大执行时间 5 分钟

    def __init__(
        self,
        bridge: Any,
        game_state_path: Path,
        logger: logging.Logger | None = None,
    ):
        self._bridge = bridge
        self._game_state_path = Path(game_state_path)
        self._logger = logger or log

    def set_bridge(self, bridge: Any) -> None:
        self._bridge = bridge

    async def execute(
        self,
        skill_entry: Any,
        params: dict,
        on_context: Callable[[SkillContext], None] | None = None,
    ) -> dict:
        """执行一个技能。

        Args:
            skill_entry: SkillEntry 实例，含 name/func/timeout 等属性。
            params: 技能参数 dict，来自 FC 解析。

        Returns:
            标准化的 Result dict：
            {
                "success": bool,
                "status": "ok" | "partial" | "failed",
                "observation": str,  # 可选
                "data": dict,        # 可选
                "error": dict,       # 失败时
            }
        """
        ctx = SkillContext(self._bridge, self._game_state_path, self._logger)
        if on_context is not None:
            on_context(ctx)
        timeout = min(skill_entry.timeout, self.MAX_TIMEOUT)

        self._logger.info(
            "[SkillRunner] 开始执行: %s, params=%s, timeout=%.0fs",
            skill_entry.name, params, timeout,
        )
        start_time = time.time()

        try:
            result = await asyncio.wait_for(
                skill_entry.func(ctx, **params),
                timeout=timeout,
            )
            elapsed = time.time() - start_time
            status = result.get("status", "ok") if isinstance(result, dict) else "ok"
            self._logger.info(
                "[SkillRunner] 完成: %s, 耗时=%.1fs, status=%s",
                skill_entry.name, elapsed, status,
            )
            if isinstance(result, dict):
                return result
            return {"success": True, "status": "ok", "data": result}

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            self._logger.warning(
                "[SkillRunner] 超时: %s, timeout=%.0fs, elapsed=%.1fs",
                skill_entry.name, timeout, elapsed,
            )
            ctx.cancel()
            return {
                "success": False,
                "status": "failed",
                "error": {
                    "code": "TIMEOUT",
                    "message": f"{skill_entry.name} 超时 ({timeout}s)",
                },
            }

        except SkillCancelled:
            elapsed = time.time() - start_time
            self._logger.info(
                "[SkillRunner] 取消: %s, elapsed=%.1fs",
                skill_entry.name, elapsed,
            )
            return {
                "success": True,
                "status": "partial",
                "observation": f"{skill_entry.name} 被取消",
                "error": {"code": "CANCELLED", "message": f"{skill_entry.name} 被取消"},
            }

        except BridgeError as e:
            elapsed = time.time() - start_time
            self._logger.warning(
                "[SkillRunner] BridgeError: %s, elapsed=%.1fs, error=%s",
                skill_entry.name, elapsed, e,
            )
            return {
                "success": False,
                "status": "failed",
                "error": e.to_dict(),
            }

        except Exception as e:
            elapsed = time.time() - start_time
            self._logger.error(
                "[SkillRunner] %s 异常 (%.1fs): %s",
                skill_entry.name, elapsed, traceback.format_exc(),
            )
            return {
                "success": False,
                "status": "failed",
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e),
                },
            }
