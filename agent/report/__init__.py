"""report — 统一埋点上报模块。

公共 API 导出，调用方可以：
    from report import events
    from report.telemetry import Telemetry, track, set_current, ...
    from report.error_context import capture_error_context
"""

from . import events  # noqa: F401
from .error_context import capture_error_context  # noqa: F401
from .telemetry import (  # noqa: F401
    Telemetry,
    bump_action,
    bump_llm_call,
    clear_current,
    from_env,
    get_include_chat,
    llm_call_data,
    reset_session_counters,
    session_end_payload,
    set_current,
    track,
    track_onboard,
)
