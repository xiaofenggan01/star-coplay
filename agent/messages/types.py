"""
messages.types — 两环间传递的所有消息 dataclass 定义。

消息流向：
    快环 → 慢环：EscalateRequest, ReportEvent, EpisodeBoundaryReport, TaskCompleted
    慢环 → 快环：ReplaceBehaviorTree, DirectAction, UpdateBlackboard
    玩家 → 慢环：PlayerInput
    系统：SystemCommand
    慢环通用响应：SlowLoopResponse
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EscalateRequest:
    """快环→慢环：BT 执行到 EscalateLeaf 时请求慢环决策。

    用于快环无法自主解决的情形（如当前行为树无合适节点、
    关键资源不足需要重新规划等）。
    """

    reason: str
    """请求升级的原因描述。"""

    correlation_id: str
    """用于将慢环响应与本次请求配对的唯一 ID。"""

    game_state: dict = field(default_factory=dict)
    """请求时刻的游戏状态快照（来自 GameStateCache）。"""

    current_bt_node: str = ""
    """发起升级时快环正在执行的 BT 节点 ID。"""

    sent_at: float = field(default_factory=time.time)
    """消息发送时间戳（Unix epoch，秒）。"""


@dataclass
class ReportEvent:
    """快环→慢环：向慢环汇报游戏内值得关注的事件。

    不要求慢环立即响应，仅用于丰富慢环的上下文记忆。
    """

    event_type: str
    """事件类型，如 'npc_birthday', 'crop_ready', 'energy_low'。"""

    data: dict = field(default_factory=dict)
    """事件的附加数据。"""

    significance: int = 5
    """重要程度，0（可忽略）到 10（非常重要），用于慢环优先级排序。"""

    source_node: str = ""
    """产生此事件的 BT 节点 ID，与设计文档 Report.source_node 对齐。"""

    sent_at: float = field(default_factory=time.time)
    """消息发送时间戳（Unix epoch，秒）。"""


@dataclass
class ReplaceBehaviorTree:
    """慢环→快环：下发新的行为树替换当前运行中的 BT。

    慢环每次决策后生成新 BT JSON，快环在安全时机（当前 tick 末）
    热替换行为树。
    """

    bt_json: dict = field(default_factory=dict)
    """行为树 JSON 描述，符合 GSP（Game Script Protocol）规范。"""

    priority: int = 0
    """替换优先级，数值越高越优先（用于多消息并发时排序）。"""

    ttl_seconds: float = 30.0
    """行为树有效期（秒），超时后快环应请求新树或回退到默认树。"""

    correlation_id: str | None = None
    """对应的 EscalateRequest.correlation_id（若有）。"""

    preload_bb: dict | None = None
    """预填充黑板键值对，在 BT 开始执行前注入。"""

    sent_at: float = field(default_factory=time.time)
    """消息创建时间戳（用于 TTL 过期检查）。"""


@dataclass
class PlayerInput:
    """玩家→慢环：玩家通过游戏内聊天或外部入口输入的文字指令。

    游戏内聊天默认 source=game_chat；路演等云侧输入使用独立 source。
    """

    text: str = ""
    """玩家输入的原始文本。"""

    source: str = "game_chat"
    """输入来源，如 game_chat / roadshow。"""

    input_id: str | None = None
    """外部输入幂等键；游戏内聊天可为空。"""

    sender_label: str | None = None
    """外部输入展示名；阶段一路演可为空。"""

    route_key: str | None = None
    """外部入口路由键；路演输入用于把公开消息回流到对应扫码页。"""

    sent_at: float = field(default_factory=time.time)
    """消息发送时间戳（Unix epoch，秒）。"""


@dataclass
class EpisodeBoundaryReport:
    """快环→慢环：Episode 边界事件（日结、睡觉、关闭）。

    用于触发慢环的日志总结与长期记忆更新。
    """

    trigger: str = "day_end"
    """触发原因：'day_end'（天结束）/ 'sleep'（玩家入睡）/ 'shutdown'（游戏关闭）。"""

    game_context: dict = field(default_factory=dict)
    """当前游戏上下文快照，如游戏日期、位置、关键指标等。"""

    summary_hint: str | None = None
    """给慢环的总结提示文本（可选），辅助生成日志摘要。"""

    sent_at: float = field(default_factory=time.time)
    """消息发送时间戳（Unix epoch，秒）。"""


@dataclass
class SystemCommand:
    """系统命令：控制 Agent 整体运行状态。

    通常由外部管理脚本或玩家特殊指令触发。
    """

    command: str = "shutdown"
    """命令类型：'shutdown'（关闭）/ 'restart'（重启）/ 'pause'（暂停）。"""

    sent_at: float = field(default_factory=time.time)
    """消息发送时间戳（Unix epoch，秒）。"""


@dataclass
class TaskCompleted:
    """快环→慢环：BT 子任务执行完毕的结果汇报。

    快环完成一个明确子任务（如浇水、出行）后主动上报，
    供慢环更新长期记忆和规划进度。
    """

    node_id: str = ""
    """完成任务的 BT 节点 ID。"""

    result: dict = field(default_factory=dict)
    """任务执行结果，键值对形式，具体内容由节点类型决定。"""

    observation: str = ""
    """执行过程中收集到的观察文本。"""

    route_key: str | None = None
    """若任务由外部路演输入触发，用于后续 follow-up 回复回流到对应扫码页。"""

    duration_s: float = 0.0
    """任务耗时（秒）。"""

    sent_at: float = field(default_factory=time.time)
    """消息发送时间戳（Unix epoch，秒）。"""


@dataclass
class DirectAction:
    """慢环→快环：慢环直接下发一组动作序列，绕过 BT 立即执行。

    用于紧急或一次性操作场景（如即时对话回复、紧急移动），
    不替换当前行为树。
    """

    action_sequence: list[dict] = field(default_factory=list)
    """动作序列，每个元素为符合 GSP 规范的动作描述字典。"""

    ttl_seconds: float = 10.0
    """动作序列的有效期（秒），超时后快环应丢弃未执行的剩余动作。"""

    correlation_id: str | None = None
    """对应的 EscalateRequest.correlation_id（若有）。"""

    sent_at: float = field(default_factory=time.time)
    """消息发送时间戳（Unix epoch，秒）。"""


@dataclass
class UpdateBlackboard:
    """慢环→快环：慢环主动更新快环黑板中的指定键值对。

    用于慢环异步推送上下文信息（如 NPC 好感度变化、目标优先级调整），
    无需替换整棵行为树。
    """

    updates: dict[str, Any] = field(default_factory=dict)
    """需要写入黑板的键值对，已有键会被覆盖，新键会被创建。"""

    ttl_seconds: float = 30.0
    """这批更新的有效期（秒），快环可在超期后忽略过期条目。"""

    sent_at: float = field(default_factory=time.time)
    """消息发送时间戳（Unix epoch，秒）。"""


@dataclass
class SlowLoopResponse:
    """慢环通用响应包装器。

    慢环处理完消息后，将结果封装为此类型写回消息总线。
    快环根据 response_type 分发处理。
    """

    payload: Any = None
    """响应载荷，具体类型由 response_type 决定。"""

    correlation_id: str | None = None
    """对应请求的 correlation_id（若有）。"""

    response_type: str = "bt_replace"
    """响应类型：'bt_replace'（替换 BT）/ 'chat'（聊天回复）/ 'noop'（无操作）。"""
