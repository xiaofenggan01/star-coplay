"""向后兼容代理 -- 实际实现已移至 report/telemetry.py"""
from report.telemetry import *  # noqa: F401,F403
from report.telemetry import (  # noqa: F401  # explicit re-exports
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
