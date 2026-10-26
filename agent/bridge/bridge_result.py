"""
bridge.bridge_result — V2 命令执行结果数据类。

BridgeResult 封装 Mod 侧通过 results.jsonl 返回的 V2 格式结果，
提供类型安全访问，取代裸 dict 访问。

V2 results.jsonl 行格式（Mod 实际输出）：
    {
        "action_id": "act_0042_a1b2",
        "seq": 42,
        "success": true,
        "status": "ok",
        "data": {...},
        "observation": "玩家位于农场; 背包已满",
        "side_effects": [{"type": "inventory_changed", ...}],
        "error": null
    }

    失败时 error 为嵌套对象：
    {
        "success": false,
        "status": "failed",
        "error": {"code": "INVALID_PARAMS", "message": "Missing param"}
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BridgeResult:
    """V2 命令执行结果。

    Attributes:
        success:      命令是否执行成功。
        status:       三值状态："ok" | "partial" | "failed"。
        data:         执行结果数据（V2 result.data）。
        observation:  Mod 侧返回的观察字符串（V2 result.observation）。
        side_effects: 结构化副作用列表（V2 result.side_effects）。
        error_code:   失败时的错误码，成功时为 None。
        message:      失败时的人类可读信息，成功时为 None。
        action_id:    回传的 action_id，用于与发送命令校验。
        seq:          回传的 seq，用于调试；超时结果为 -1。
    """

    success: bool
    status: str
    data: dict[str, Any]
    observation: str
    side_effects: list[dict[str, Any]]
    error_code: str | None
    message: str | None
    action_id: str
    seq: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BridgeResult":
        """从 V2 results.jsonl 行解析 BridgeResult。

        容错规则：
        - 缺失字段使用默认值（data={}, observation="", side_effects=[]）
        - 缺少 success 字段视为失败，error_code 设为 "PARSE_ERROR"
        - action_id / seq 缺失时使用空字符串 / -1

        Args:
            d: 从 JSON 解析的 dict。

        Returns:
            BridgeResult 实例。
        """
        try:
            if "success" not in d:
                return cls(
                    success=False,
                    status="failed",
                    data={},
                    observation="",
                    side_effects=[],
                    error_code="PARSE_ERROR",
                    message=f"Missing 'success' field in result: {list(d.keys())}",
                    action_id=str(d.get("action_id", "")),
                    seq=int(d.get("seq", -1)),
                )

            # 解析 error：Mod 使用嵌套 {"error": {"code": ..., "message": ...}}
            # 同时兼容旧的扁平 error_code/message 格式
            error_obj = d.get("error")
            if isinstance(error_obj, dict):
                error_code = error_obj.get("code")
                error_message = error_obj.get("message")
            else:
                error_code = d.get("error_code")
                error_message = d.get("message")

            # 兼容旧版 observations 列表格式：合并为单字符串
            obs_raw = d.get("observation") or d.get("observations")
            if isinstance(obs_raw, list):
                obs_str = "; ".join(obs_raw)
            else:
                obs_str = str(obs_raw) if obs_raw else ""

            return cls(
                success=bool(d["success"]),
                status=str(d.get("status", "ok" if d["success"] else "failed")),
                data=d.get("data") or {},
                observation=obs_str,
                side_effects=list(d.get("side_effects") or []),
                error_code=error_code,
                message=error_message,
                action_id=str(d.get("action_id", "")),
                seq=int(d.get("seq", -1)),
            )
        except (TypeError, ValueError, AttributeError) as e:
            return cls(
                success=False,
                status="failed",
                data={},
                observation="",
                side_effects=[],
                error_code="PARSE_ERROR",
                message=f"Failed to parse result: {e}",
                action_id=str(d.get("action_id", "")),
                seq=-1,
            )

    @classmethod
    def timeout(cls, action_id: str) -> "BridgeResult":
        """生成超时失败结果。

        Args:
            action_id: 超时命令的 action_id。

        Returns:
            success=False、error_code="TIMEOUT" 的 BridgeResult。
        """
        return cls(
            success=False,
            status="failed",
            data={},
            observation="",
            side_effects=[],
            error_code="TIMEOUT",
            message="Command timed out",
            action_id=action_id,
            seq=-1,
        )

    def __repr__(self) -> str:
        if self.success:
            return (
                f"BridgeResult(success=True, action_id={self.action_id!r}, "
                f"seq={self.seq}, data_keys={list(self.data.keys())})"
            )
        return (
            f"BridgeResult(success=False, action_id={self.action_id!r}, "
            f"seq={self.seq}, error_code={self.error_code!r}, "
            f"message={self.message!r})"
        )
