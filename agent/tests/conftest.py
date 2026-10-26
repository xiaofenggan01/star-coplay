"""
tests.conftest — 共享 fixtures。

导入策略：项目子包使用相对导入（如 from ..bt_engine import ...），
这要求所有子包拥有共同的父包。我们通过创建虚拟父包 "agent" 来解决。
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ── 导入路径修复 ───────────────────────────────────────────────────────
# agent/ 目录下的子包使用 from ..xxx 的相对导入，需要有共同的父包。
# 解决方案：创建虚拟父包，将所有子包注册为它的子模块。

AGENT_DIR = Path(__file__).resolve().parent.parent
AGENT_PARENT = AGENT_DIR.parent  # v2/

# 确保 agent 目录本身可以被当作包的子目录
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))
if str(AGENT_PARENT) not in sys.path:
    sys.path.insert(0, str(AGENT_PARENT))

# 创建虚拟父包 "agent"，使子包的相对导入（from ..xxx）能正确解析
_PARENT_PKG = "agent"
if _PARENT_PKG not in sys.modules:
    _pkg = types.ModuleType(_PARENT_PKG)
    _pkg.__path__ = [str(AGENT_DIR)]
    _pkg.__package__ = _PARENT_PKG
    sys.modules[_PARENT_PKG] = _pkg

# 注册所有子包为 agent.xxx（这样 from ..memory 就能找到 agent.memory）
_SUB_PACKAGES = ["bridge", "bt_engine", "memory", "messages", "slow_loop", "report"]
for _sub in _SUB_PACKAGES:
    _fqn = f"{_PARENT_PKG}.{_sub}"
    if _fqn not in sys.modules:
        _sub_dir = AGENT_DIR / _sub
        _sub_pkg = types.ModuleType(_fqn)
        _sub_pkg.__path__ = [str(_sub_dir)]
        _sub_pkg.__package__ = _fqn
        sys.modules[_fqn] = _sub_pkg

# 现在通过父包路径导入子模块
# 先导入不依赖其他子包的模块

# messages.types
_msg_types = importlib.import_module("agent.messages.types")
sys.modules["messages.types"] = _msg_types
if "messages" not in sys.modules:
    sys.modules["messages"] = sys.modules["agent.messages"]

# memory.working_memory
_wm = importlib.import_module("agent.memory.working_memory")
sys.modules["memory.working_memory"] = _wm
if "memory" not in sys.modules:
    sys.modules["memory"] = sys.modules["agent.memory"]

# bridge.bridge_result (生产路径 WSBridge 用它返回结果)
_br = importlib.import_module("agent.bridge.bridge_result")
sys.modules["bridge.bridge_result"] = _br

# 注：FileBridge 已在 R1 清理中删除，相关 fixture 仅用 MagicMock 对象不依赖真实模块
if "bridge" not in sys.modules:
    sys.modules["bridge"] = sys.modules["agent.bridge"]

# bt_engine.message_bus — 直接导入，跳过 bt_engine/__init__.py 中重的导入
_mb = importlib.import_module("agent.bt_engine.message_bus")
sys.modules["bt_engine.message_bus"] = _mb
if "bt_engine" not in sys.modules:
    sys.modules["bt_engine"] = sys.modules["agent.bt_engine"]

# slow_loop 子模块 — 直接导入具体文件，跳过 __init__.py
_bv = importlib.import_module("agent.slow_loop.bt_validator")
sys.modules["slow_loop.bt_validator"] = _bv

_pl = importlib.import_module("agent.slow_loop.planner")
sys.modules["slow_loop.planner"] = _pl

_pa = importlib.import_module("agent.slow_loop.prompt_assembler")
sys.modules["slow_loop.prompt_assembler"] = _pa

_ad = importlib.import_module("agent.slow_loop.action_dispatcher")
sys.modules["slow_loop.action_dispatcher"] = _ad

_dl = importlib.import_module("agent.slow_loop.deliberative_loop")
sys.modules["slow_loop.deliberative_loop"] = _dl

if "slow_loop" not in sys.modules:
    sys.modules["slow_loop"] = sys.modules["agent.slow_loop"]

# report 子模块
_report_init = importlib.import_module("agent.report")
sys.modules["report"] = _report_init

_report_telemetry = importlib.import_module("agent.report.telemetry")
sys.modules["report.telemetry"] = _report_telemetry

_report_events = importlib.import_module("agent.report.events")
sys.modules["report.events"] = _report_events

_report_ec = importlib.import_module("agent.report.error_context")
sys.modules["report.error_context"] = _report_ec

# 兼容旧 import 路径：telemetry / error_context 直接映射
if "telemetry" not in sys.modules:
    sys.modules["telemetry"] = _report_telemetry
if "error_context" not in sys.modules:
    sys.modules["error_context"] = _report_ec

# game_monitor (EventRelay)
_gm = importlib.import_module("agent.game_monitor")
sys.modules["game_monitor"] = _gm


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def working_memory():
    """空的 WorkingMemory 实例。"""
    return _wm.WorkingMemory(max_events=50)


@pytest.fixture
def working_memory_with_events():
    """预填充事件的 WorkingMemory。"""
    wm = _wm.WorkingMemory(max_events=50)
    wm.remember(
        {"time": "9:00", "player": {"location": "Farm", "energy": 100}},
        [
            {"type": "wake_up", "content": "起床了", "ts": 1000.0},
            {"type": "water_crop", "content": "浇了南瓜", "ts": 1001.0},
            {"type": "talk_npc", "content": "和 Abigail 聊天", "ts": 1002.0},
        ],
    )
    return wm


@pytest.fixture
def mock_game_state():
    """标准游戏状态 dict。"""
    return {
        "time": "14:30",
        "weather": "sunny",
        "player": {
            "location": "Farm",
            "energy": 80,
            "max_energy": 270,
        },
    }


@pytest.fixture
def mock_capabilities():
    """模拟 FileBridge.capabilities() 的返回值。"""
    return [
        {"name": "move_to", "description": "移动到指定位置"},
        {"name": "say", "description": "说话"},
        {"name": "observe", "description": "观察周围"},
        {"name": "do", "description": "执行动作"},
        {"name": "use_tool", "description": "使用工具"},
        {"name": "equip", "description": "装备物品"},
        {"name": "face", "description": "面向"},
        {"name": "emote", "description": "表情"},
    ]


@pytest.fixture
def mock_capability_names():
    """模拟 FileBridge.capability_names() 的返回值。"""
    return frozenset({"move_to", "say", "observe", "do", "use_tool", "equip", "face", "emote"})


@pytest.fixture
def mock_bridge(mock_game_state, mock_capabilities, mock_capability_names):
    """Mock FileBridge，返回固定 game_state 和 capabilities。"""
    bridge = MagicMock()
    bridge.read_game_state.return_value = mock_game_state
    bridge.capabilities.return_value = mock_capabilities
    bridge.capability_names.return_value = mock_capability_names
    ok = MagicMock()
    ok.success = True
    ok.data = {}
    ok.observation = ""
    ok.message = ""
    ok.error_code = None
    bridge.read_command_result.return_value = ok
    return bridge
