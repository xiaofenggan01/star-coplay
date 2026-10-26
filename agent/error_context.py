"""向后兼容代理 -- 实际实现已移至 report/error_context.py"""
from report.error_context import *  # noqa: F401,F403
from report.error_context import capture_error_context  # noqa: F401
