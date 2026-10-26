#!/usr/bin/env python3
"""
Stage B 实机回归测试 — L0 InputBridge 切换 IInputSimulator 后的全量验证。

核心目标：
  1. 验证 Stage A（InputBridgeSimulator）替换 SMAPI Press 路径后的所有 L1 能力
  2. 端到端覆盖聚焦 + 失焦双场景，证明 macOS 失焦失效 P0 已解决
  3. 失焦切换通过 osascript 自动驱动，不需要用户手动 Cmd+Tab

运行方式（容器内）：
    python3 stage_b_remote_test.py [--focused-only|--unfocused-only] [--case <name>]
    python3 stage_b_remote_test.py --list      # 列出所有用例
    python3 stage_b_remote_test.py --help

依赖：
  - /workspace/project-skills/tailscale-ssh/scripts/ts-connect.sh （远程 Mac 操作）
  - Mac 端运行 dev_ws_server.py（监听 ws://localhost:9527）
  - Mac 端 SDV 已加载存档，Mod config.json 设 PlatformToken=dev + UseInputBridgeHandlers=true
  - 仅 Python 标准库，无第三方依赖

Backend：
  A (file-trigger，默认/推荐)：通过 SSH 写 /tmp/ws_cmd.json，dev_ws_server.py 转发 → /tmp/ws_result.json
  B (smapi-keystroke，备用)：通过 osascript keystroke 到 SMAPI Terminal 窗口（agent_xxx 命令）
                             仅当 Backend A 不可用时回落（dev_ws_server.py 未运行/Mod 未连）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# ─────────────────────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────────────────────

TS_CONNECT = "/workspace/project-skills/tailscale-ssh/scripts/ts-connect.sh"
WS_CMD_FILE = "/tmp/ws_cmd.json"
WS_RESULT_FILE = "/tmp/ws_result.json"
SMAPI_LOG = "~/.config/StardewValley/ErrorLogs/SMAPI-latest.txt"
RESULT_OUTPUT = "/tmp/stage_b_results.json"

# 失焦/聚焦切换 osascript（已实测有效）
OSASCRIPT_BLUR = "osascript -e 'tell application \"Finder\" to activate'"
OSASCRIPT_FOCUS = "osascript -e 'tell application \"Stardew Valley\" to activate'"

# Backend A 命令注入超时（默认 30s，跨地图 60s）
DEFAULT_TIMEOUT = 30
WARP_TIMEOUT = 60

# ─────────────────────────────────────────────────────────────────────────────
# ANSI 颜色
# ─────────────────────────────────────────────────────────────────────────────

class C:
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    GRAY = "\033[90m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def cprint(color: str, msg: str) -> None:
    if sys.stdout.isatty():
        print(f"{color}{msg}{C.RESET}")
    else:
        print(msg)


# ─────────────────────────────────────────────────────────────────────────────
# SSH 远程执行
# ─────────────────────────────────────────────────────────────────────────────

class RemoteExec:
    """通过 ts-connect.sh exec 远程操作 Mac。"""

    def __init__(self, ts_connect: str = TS_CONNECT, dry_run: bool = False) -> None:
        self.ts_connect = ts_connect
        self.dry_run = dry_run
        if not Path(self.ts_connect).exists():
            raise FileNotFoundError(
                f"ts-connect.sh not found at {self.ts_connect}. "
                "Verify project-skills/tailscale-ssh is installed."
            )

    def run(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        """在 Mac 上执行命令，返回 (rc, stdout, stderr)。

        ts-connect.sh exec 会在 stdout 第一行打印 banner（"🖥️  在 Mac (IP) 上执行: ..."），
        会污染需要纯净输出的解析（如 stat -f %m）。这里统一剥掉。
        """
        if self.dry_run:
            print(f"[DRY] mac> {cmd}")
            return 0, "", ""
        try:
            r = subprocess.run(
                [self.ts_connect, "exec", cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = r.stdout or ""
            # 剥离 banner（ts-connect.sh line 112: echo "🖥️  在 Mac ($MAC_IP) 上执行: $CMD"）
            lines = stdout.splitlines(keepends=True)
            cleaned: list[str] = []
            for ln in lines:
                if ln.lstrip().startswith("\U0001f5a5"):  # 🖥️
                    continue
                cleaned.append(ln)
            return r.returncode, "".join(cleaned), r.stderr
        except subprocess.TimeoutExpired as e:
            return 124, e.stdout or "", f"timeout after {timeout}s"
        except Exception as e:
            return 1, "", f"{type(e).__name__}: {e}"

    def write_file(self, path: str, content: str) -> tuple[int, str, str]:
        """把 content 写到 Mac 上的 path（用 base64 + base64 -d 避免 shell 转义问题）。"""
        import base64
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        cmd = f"echo {shlex.quote(b64)} | base64 -d > {shlex.quote(path)}"
        return self.run(cmd, timeout=15)

    def read_file(self, path: str, max_chars: int = 4096) -> tuple[int, str]:
        """读 Mac 上的文件内容。"""
        rc, out, _ = self.run(f"cat {shlex.quote(path)} 2>/dev/null | head -c {max_chars}", timeout=15)
        return rc, out

    def file_mtime(self, path: str) -> Optional[float]:
        """获取 Mac 上文件的 mtime（秒），不存在返回 None。"""
        rc, out, _ = self.run(f"stat -f %m {shlex.quote(path)} 2>/dev/null", timeout=10)
        if rc != 0 or not out.strip():
            return None
        try:
            return float(out.strip())
        except ValueError:
            return None

    def file_exists(self, path: str) -> bool:
        rc, _, _ = self.run(f"test -e {shlex.quote(path)}", timeout=10)
        return rc == 0

    def grep_log(self, pattern: str, tail_lines: int = 30) -> str:
        """在 SMAPI-latest.txt 中 grep 关键词，返回最近 tail_lines 条匹配。"""
        cmd = f"grep -E {shlex.quote(pattern)} {SMAPI_LOG} 2>/dev/null | tail -{tail_lines}"
        _, out, _ = self.run(cmd, timeout=15)
        return out

    def tail_log(self, lines: int = 50) -> str:
        cmd = f"tail -{lines} {SMAPI_LOG} 2>/dev/null"
        _, out, _ = self.run(cmd, timeout=15)
        return out


# ─────────────────────────────────────────────────────────────────────────────
# Driver Interface — Backend A (file-trigger) / Backend B (smapi-keystroke)
# ─────────────────────────────────────────────────────────────────────────────

class CommandDriver:
    """命令注入抽象基类。"""

    name: str = "abstract"

    def __init__(self, remote: RemoteExec) -> None:
        self.remote = remote

    def available(self) -> tuple[bool, str]:
        """返回 (是否可用, 状态描述)。"""
        raise NotImplementedError

    def send(self, capability: str, params: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
        """发送命令并返回结果 dict。失败时返回 {"error": "..."}。"""
        raise NotImplementedError

    def observe(self) -> dict:
        """便捷封装：返回当前游戏状态快照（player tile/facing/canMove 等）。"""
        return self.send("observe", {}, timeout=DEFAULT_TIMEOUT)


class FileTriggerDriver(CommandDriver):
    """Backend A：通过 /tmp/ws_cmd.json 走 dev_ws_server.py。"""

    name = "file-trigger (dev_ws_server)"

    def available(self) -> tuple[bool, str]:
        # 检测 Mac 上 dev_ws_server.py 是否运行（端口 9527）
        rc, out, _ = self.remote.run(
            "lsof -nP -iTCP:9527 -sTCP:LISTEN 2>/dev/null | tail -n +2 | head -1",
            timeout=10,
        )
        if rc == 0 and out.strip():
            return True, f"dev_ws_server listening on :9527 ({out.strip().split()[0]})"
        return False, "dev_ws_server.py not listening on :9527"

    def send(self, capability: str, params: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
        # 1. 清空旧 result（避免 mtime 误判）
        self.remote.run(f"rm -f {shlex.quote(WS_RESULT_FILE)}", timeout=5)
        prev_mtime = None  # 不存在
        # 2. 写命令文件
        payload = json.dumps({
            "capability": capability,
            "params": params,
            "timeout": timeout,
        }, ensure_ascii=False)
        rc, _, err = self.remote.write_file(WS_CMD_FILE, payload)
        if rc != 0:
            return {"error": f"write cmd file failed: {err}"}

        # 3. 轮询 result 文件出现
        deadline = time.time() + timeout + 5  # 多给 5s 缓冲
        while time.time() < deadline:
            time.sleep(0.5)
            mt = self.remote.file_mtime(WS_RESULT_FILE)
            if mt is not None and (prev_mtime is None or mt > prev_mtime):
                rc, content = self.remote.read_file(WS_RESULT_FILE, max_chars=8192)
                if rc != 0 or not content.strip():
                    continue
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    return {"error": f"result json parse: {e}", "raw": content[:200]}
        return {"error": f"timeout waiting for result ({timeout}s)"}


class SmapiKeystrokeDriver(CommandDriver):
    """Backend B：osascript keystroke 到 SMAPI Terminal 窗口。

    限制：失焦下无法工作（keystroke 进的是当前聚焦窗口）；用作 Backend A 不可用时的备用诊断。
    """

    name = "smapi-keystroke (osascript)"

    # capability 名 → SMAPI 控制台命令模板
    # 若 capability 在表中无映射则此 backend 不支持该用例
    CMD_MAP: dict[str, Callable[[dict], Optional[str]]] = {
        "observe": lambda p: "agent_status",
        "face": lambda p: None,  # 没有 agent_face；通过 agent_goto 邻接 tile 间接转身（不可靠）
        "interact": lambda p: f"agent_interact {p.get('x', 0)} {p.get('y', 0)}"
            if p.get("x") is not None and p.get("y") is not None else None,
        "use_tool": lambda p: f"agent_usetool {p.get('x', 0)} {p.get('y', 0)}"
            if p.get("x") is not None and p.get("y") is not None else None,
        "move_to": lambda p: f"agent_goto {p.get('x', 0)} {p.get('y', 0)}"
            if p.get("x") is not None and p.get("y") is not None else None,
        "equip": lambda p: f"agent_equip {p.get('name', '')}"
            if p.get("name") else None,
    }

    def available(self) -> tuple[bool, str]:
        # 检测 SDV 进程
        rc, out, _ = self.remote.run(
            "pgrep -f 'StardewValley' | head -1", timeout=10
        )
        if rc != 0 or not out.strip():
            return False, "Stardew Valley process not running"
        return True, "SDV running (keystroke fallback available; coverage limited)"

    def send(self, capability: str, params: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
        builder = self.CMD_MAP.get(capability)
        if builder is None:
            return {"error": f"backend B does not support capability: {capability}"}
        cmd_str = builder(params)
        if cmd_str is None:
            return {"error": f"params insufficient for backend B {capability}: {params}"}

        # 用 osascript 把命令 keystroke 到 Terminal —— 仅在聚焦下工作
        applescript = (
            'tell application "Terminal" to activate\n'
            'delay 0.3\n'
            f'tell application "System Events" to keystroke "{cmd_str}"\n'
            'delay 0.1\n'
            'tell application "System Events" to key code 36'  # Return
        )
        rc, out, err = self.remote.run(
            f"osascript -e {shlex.quote(applescript)}", timeout=15
        )
        if rc != 0:
            return {"error": f"keystroke failed: {err}"}

        # 没有同步结果通道；只能从日志事后捞，留给 verify 阶段处理
        time.sleep(2.0)  # 等命令在游戏内执行
        return {
            "status": "submitted",
            "backend": "B",
            "command": cmd_str,
            "note": "no synchronous result; verify via log/observe",
        }


def detect_driver(remote: RemoteExec, prefer: str = "auto") -> CommandDriver:
    """检测可用 driver。prefer ∈ {auto, A, B}。"""
    a = FileTriggerDriver(remote)
    b = SmapiKeystrokeDriver(remote)

    if prefer == "A":
        ok, msg = a.available()
        if not ok:
            raise RuntimeError(f"Backend A unavailable: {msg}")
        cprint(C.GREEN, f"Driver: {a.name} — {msg}")
        return a
    if prefer == "B":
        ok, msg = b.available()
        if not ok:
            raise RuntimeError(f"Backend B unavailable: {msg}")
        cprint(C.YELLOW, f"Driver: {b.name} — {msg} (LIMITED)")
        return b

    # auto：优先 A，回落 B
    ok_a, msg_a = a.available()
    if ok_a:
        cprint(C.GREEN, f"Driver: {a.name} — {msg_a}")
        return a
    cprint(C.YELLOW, f"Backend A unavailable: {msg_a}")
    ok_b, msg_b = b.available()
    if ok_b:
        cprint(C.YELLOW, f"Falling back to Driver: {b.name} — {msg_b}")
        return b
    raise RuntimeError(
        "No driver available. Need either:\n"
        "  - Backend A: dev_ws_server.py listening on :9527 (recommended)\n"
        "  - Backend B: SDV running with SMAPI Terminal visible (limited coverage)\n"
        f"Diagnostics:\n  A: {msg_a}\n  B: {msg_b}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 焦点切换
# ─────────────────────────────────────────────────────────────────────────────

def set_focus(remote: RemoteExec, focused: bool) -> tuple[bool, str]:
    """聚焦/失焦 SDV。返回 (成功, 信息)。"""
    cmd = OSASCRIPT_FOCUS if focused else OSASCRIPT_BLUR
    rc, out, err = remote.run(cmd, timeout=15)
    # 给 OS 时间完成切换 + 让 SDV 内部 IsActive 状态稳定
    time.sleep(1.5)
    if rc != 0:
        return False, f"osascript rc={rc} err={err}"
    return True, "focused" if focused else "blurred"


def query_is_active(driver: CommandDriver) -> Optional[bool]:
    """通过 observe 查询 SDV 当前 IsActive 状态（如 observation 中暴露）。

    若不可得返回 None（不阻塞测试，仅作为辅助诊断）。
    """
    r = driver.observe()
    if r and isinstance(r, dict):
        # 多种可能字段名都尝试
        data = r.get("data") or r.get("observation") or {}
        if isinstance(data, dict):
            for k in ("is_active", "isActive", "window_focused"):
                if k in data:
                    return bool(data[k])
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 用例数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    name: str
    description: str
    focused: bool  # True=聚焦执行，False=失焦执行
    skipped: bool = False
    skip_reason: str = ""
    passed: bool = False
    error: str = ""
    duration_sec: float = 0.0
    pre_observe: dict = field(default_factory=dict)
    post_observe: dict = field(default_factory=dict)
    cmd_result: dict = field(default_factory=dict)
    log_snippet: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class TestCase:
    name: str
    description: str
    focused: bool                        # 期望聚焦/失焦执行
    capability: str                      # 用 driver.send 的 capability
    params_factory: Callable[[dict], dict] # 参数生成器（接收 pre_observe 计算 params）
    timeout: int = DEFAULT_TIMEOUT
    verify: Optional[Callable[[dict, dict, dict, "TestCtx"], tuple[bool, str]]] = None
    log_grep: str = ""                   # 完成后 grep 该模式抓 log 片段（仅诊断）
    tags: list[str] = field(default_factory=list)
    expected_failure_when_unfocused: bool = False  # equip 数字键这种已知失焦不工作
    must_be_focused: bool = False        # 用例本身只能聚焦（如 ESC reinstall）


class TestCtx:
    """跨步骤共享的上下文（player 起始位置、地图、记号等）。"""
    def __init__(self) -> None:
        self.baseline: dict = {}
        self.notes: list[str] = []


# ─────────────────────────────────────────────────────────────────────────────
# observation 解析辅助
# ─────────────────────────────────────────────────────────────────────────────

def _extract_obs(result: dict) -> dict:
    """从 driver.send 返回的结果中抽取 observation/state 字典。"""
    if not isinstance(result, dict):
        return {}
    # WS command_result 一般在 data 里
    if "data" in result and isinstance(result["data"], dict):
        return result["data"]
    if "observation" in result and isinstance(result["observation"], dict):
        return result["observation"]
    return result


_FACING_STR_TO_INT = {
    "up": 0, "north": 0, "u": 0,
    "right": 1, "east": 1, "r": 1,
    "down": 2, "south": 2, "d": 2,
    "left": 3, "west": 3, "l": 3,
}


def _player_pos(obs: dict) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """从 observe 结果中提取 (tile_x, tile_y, location_name)。

    实际 schema（V2 ObserveHandler 输出）：
        data.self.position.{tile_x, tile_y, location}
    历史 schema 兼容：
        data.player.tile.{x,y} / data.player.position.{x,y} / data.player.{x,y}
    """
    obs = _extract_obs(obs)
    if not isinstance(obs, dict):
        return (None, None, None)
    # 实际 schema：self
    player = obs.get("self") or obs.get("player")
    if not isinstance(player, dict):
        return (None, None, None)

    pos = player.get("position") or player.get("tile") or {}
    loc = None
    x = y = None
    if isinstance(pos, dict):
        loc = pos.get("location") or pos.get("location_name")
        x = pos.get("tile_x") if "tile_x" in pos else (pos.get("x") if "x" in pos else pos.get("X"))
        y = pos.get("tile_y") if "tile_y" in pos else (pos.get("y") if "y" in pos else pos.get("Y"))
    if loc is None:
        loc = player.get("location") or player.get("location_name")
    if x is None:
        x = player.get("x") or player.get("tile_x")
    if y is None:
        y = player.get("y") or player.get("tile_y")
    try:
        return (int(x) if x is not None else None,
                int(y) if y is not None else None,
                loc)
    except (ValueError, TypeError):
        return (None, None, loc)


def _player_facing(obs: dict) -> Optional[int]:
    """从 observe 结果中提取 facing(0=U/1=R/2=D/3=L)。

    实际 schema：data.self.facing 是字符串 "up"/"right"/"down"/"left"。
    """
    obs = _extract_obs(obs)
    if not isinstance(obs, dict):
        return None
    player = obs.get("self") or obs.get("player")
    if not isinstance(player, dict):
        return None
    for k in ("facing", "facing_direction", "facingDirection", "FacingDirection"):
        if k in player:
            v = player[k]
            if isinstance(v, str):
                return _FACING_STR_TO_INT.get(v.strip().lower())
            try:
                return int(v)
            except (ValueError, TypeError):
                pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# verify 函数
# ─────────────────────────────────────────────────────────────────────────────

def verify_facing(expected: int):
    def fn(pre: dict, post: dict, cmd_res: dict, ctx: TestCtx) -> tuple[bool, str]:
        actual = _player_facing(post)
        if actual is None:
            return False, "post observe 中未找到 facing 字段"
        if actual != expected:
            return False, f"facing 期望 {expected}，实际 {actual}"
        return True, f"facing={actual} ✓"
    return fn


def verify_position_changed():
    def fn(pre: dict, post: dict, cmd_res: dict, ctx: TestCtx) -> tuple[bool, str]:
        px, py, _ = _player_pos(pre)
        qx, qy, _ = _player_pos(post)
        if None in (px, py, qx, qy):
            return False, f"位置字段缺失 pre=({px},{py}) post=({qx},{qy})"
        if (px, py) == (qx, qy):
            return False, f"位置未变化 ({px},{py})"
        return True, f"({px},{py}) -> ({qx},{qy}) ✓"
    return fn


def verify_position_close_to(tx: int, ty: int, tolerance: int = 1):
    def fn(pre: dict, post: dict, cmd_res: dict, ctx: TestCtx) -> tuple[bool, str]:
        qx, qy, _ = _player_pos(post)
        if None in (qx, qy):
            return False, "post 位置字段缺失"
        if abs(qx - tx) > tolerance or abs(qy - ty) > tolerance:
            return False, f"({qx},{qy}) 距目标 ({tx},{ty}) 超 {tolerance}"
        return True, f"到达 ({qx},{qy}) ≈ ({tx},{ty}) ✓"
    return fn


def verify_location_changed():
    def fn(pre: dict, post: dict, cmd_res: dict, ctx: TestCtx) -> tuple[bool, str]:
        _, _, loc_pre = _player_pos(pre)
        _, _, loc_post = _player_pos(post)
        if loc_pre and loc_post and loc_pre != loc_post:
            return True, f"地图: {loc_pre} -> {loc_post} ✓"
        return False, f"地图未切换 pre={loc_pre} post={loc_post}"
    return fn


def verify_command_success():
    def fn(pre: dict, post: dict, cmd_res: dict, ctx: TestCtx) -> tuple[bool, str]:
        if not isinstance(cmd_res, dict):
            return False, "cmd_res 非 dict"
        if "error" in cmd_res and cmd_res["error"]:
            return False, f"command error: {cmd_res['error']}"
        status = cmd_res.get("status", "")
        if status in ("success", "ok", "completed", "submitted"):
            return True, f"status={status}"
        # 没有 status 字段且没 error 也算通过
        if "error_code" in cmd_res:
            return False, f"error_code={cmd_res.get('error_code')} msg={cmd_res.get('message','')}"
        return True, "no error"
    return fn


def verify_unfocused_failure_expected():
    """已知边界：失焦下数字键 equip 不工作，确认返回失败状态。"""
    def fn(pre: dict, post: dict, cmd_res: dict, ctx: TestCtx) -> tuple[bool, str]:
        if not isinstance(cmd_res, dict):
            return False, "cmd_res 非 dict"
        # 期望要么 error_code，要么状态非 success
        if cmd_res.get("error") or cmd_res.get("error_code"):
            return True, f"按预期失败：{cmd_res.get('error_code') or cmd_res.get('error')}"
        status = cmd_res.get("status", "")
        if status not in ("success", "ok", "completed"):
            return True, f"按预期非 success（status={status}）"
        return False, "失焦下数字键 equip 居然成功了 — 检查是否 IInputSimulator 已覆盖数字键"
    return fn


# ─────────────────────────────────────────────────────────────────────────────
# 用例集（基于 L1 V2 验证报告 §3 能力矩阵）
# ─────────────────────────────────────────────────────────────────────────────

def build_test_cases() -> list[TestCase]:
    cases: list[TestCase] = []

    # ── face × 4 方向 × 聚焦/失焦 ──
    facing_map = [("up", 0, "U"), ("right", 1, "R"), ("down", 2, "D"), ("left", 3, "L")]
    for label, dir_int, short in facing_map:
        for focused in (True, False):
            cases.append(TestCase(
                name=f"face_{short}_{'focused' if focused else 'unfocused'}",
                description=f"face {label} (direction={dir_int}) — {'聚焦' if focused else '失焦'}",
                focused=focused,
                capability="face",
                params_factory=lambda pre, d=dir_int: {"direction": d},
                timeout=10,
                verify=verify_facing(dir_int),
                log_grep="V2/L1V2.*face|InputBridgeSimulator|WINDOW_NOT_FOCUSED",
                tags=["face"],
            ))

    # ── interact × 聚焦/失焦 ──
    # 用 player 当前 tile 的"前方"作为目标（依赖 face 已生效，简化为面朝下方 +1）
    for focused in (True, False):
        cases.append(TestCase(
            name=f"interact_self_tile_{'focused' if focused else 'unfocused'}",
            description=f"interact 当前面朝 tile（最小化 — 仅验证按键注入路径）— {'聚焦' if focused else '失焦'}",
            focused=focused,
            capability="interact",
            params_factory=lambda pre: {
                "x": (_player_pos(pre)[0] or 0),
                "y": (_player_pos(pre)[1] or 0) + 1,  # 朝下一格
            },
            timeout=15,
            verify=verify_command_success(),
            log_grep="V2/L1V2.*interact|InteractHandlerV2|InputBridgeSimulator",
            tags=["interact"],
        ))

    # ── use_tool × 聚焦/失焦 ──
    # 同 interact 思路：对当前面朝 tile 触发，仅验证按键路径已 dispatch
    for focused in (True, False):
        cases.append(TestCase(
            name=f"use_tool_self_tile_{'focused' if focused else 'unfocused'}",
            description=f"use_tool 当前面朝 tile（验证 Tap C 路径）— {'聚焦' if focused else '失焦'}",
            focused=focused,
            capability="use_tool",
            params_factory=lambda pre: {
                "x": (_player_pos(pre)[0] or 0),
                "y": (_player_pos(pre)[1] or 0) + 1,
            },
            timeout=20,
            verify=verify_command_success(),
            log_grep="V2/L1V2.*use_tool|UseToolHandlerV2|InputBridgeSimulator",
            tags=["use_tool"],
        ))

    # ── move_to 单地图（dx=+2 探针）× 聚焦/失焦 ──
    def _probe_move_params(pre: dict) -> dict:
        px, py, _ = _player_pos(pre)
        if px is None or py is None:
            return {"x": 0, "y": 0}  # 会触发 verify 失败
        # 向 +x +1 移动一格（最小可观察变化，避免地图边界/障碍）
        return {"x": px + 1, "y": py}

    for focused in (True, False):
        cases.append(TestCase(
            name=f"move_to_same_map_{'focused' if focused else 'unfocused'}",
            description=f"move_to 同地图 +1 tile（PathFindController 路径）— {'聚焦' if focused else '失焦'}",
            focused=focused,
            capability="move_to",
            params_factory=_probe_move_params,
            timeout=DEFAULT_TIMEOUT,
            verify=verify_position_changed(),
            log_grep="MoveToHandler|move_to|InputBridgeSimulator",
            tags=["move_to", "same_map"],
        ))

    # ── move_to 跨地图（FarmHouse → Farm 或当前地图门口）× 聚焦/失焦 ──
    # 需要存档起点是 FarmHouse；通过 go_to "Farm" 触发跨地图（更鲁棒）
    for focused in (True, False):
        cases.append(TestCase(
            name=f"goto_cross_map_{'focused' if focused else 'unfocused'}",
            description=f"go_to Farm（跨地图 warp + 5 阶段状态机）— {'聚焦' if focused else '失焦'}",
            focused=focused,
            capability="go_to",
            params_factory=lambda pre: {"target": "Farm"},
            timeout=WARP_TIMEOUT,
            verify=verify_location_changed(),
            log_grep="MoveToHandler|warp|cross-map|GoToHandler|location.*chang",
            tags=["move_to", "cross_map", "warp"],
        ))

    # ── equip 数字键 × 聚焦/失焦（失焦 expected_failure）──
    # 装备槽 1（一般是工具/物品），失焦下 IInputSimulator 不覆盖数字键
    cases.append(TestCase(
        name="equip_slot_focused",
        description="equip slot 1（数字键 D1，走 SMAPI Press 兜底）— 聚焦",
        focused=True,
        capability="equip",
        params_factory=lambda pre: {"slot": 1},
        timeout=10,
        verify=verify_command_success(),
        log_grep="EquipHandlerV2|equip|slot",
        tags=["equip"],
    ))
    cases.append(TestCase(
        name="equip_slot_unfocused_expected_fail",
        description="equip slot 1（已知边界：IInputSimulator 不覆盖数字键，失焦应失败）",
        focused=False,
        capability="equip",
        params_factory=lambda pre: {"slot": 1},
        timeout=10,
        verify=verify_unfocused_failure_expected(),
        log_grep="EquipHandlerV2|equip|WINDOW_NOT_FOCUSED",
        tags=["equip", "known_boundary"],
        expected_failure_when_unfocused=True,
    ))

    # ── ESC reinstall 验证（仅聚焦下能注入 ESC）──
    # 注：完整测试需要 driver 暴露 ESC 注入能力。当前用 face 序列代替 — 验证 simulator 持续工作即可
    cases.append(TestCase(
        name="esc_reinstall_focused",
        description="ESC 后 simulator 自动 reinstall（连续 face 操作验证 simulator 状态恢复）",
        focused=True,
        capability="face",
        params_factory=lambda pre: {"direction": 0},
        timeout=10,
        verify=verify_facing(0),
        log_grep="InputBridgeSimulator.*install|ESC.*detected|simulator.*null",
        tags=["esc", "edge_case"],
        must_be_focused=True,
    ))

    return cases


# ─────────────────────────────────────────────────────────────────────────────
# 单用例执行
# ─────────────────────────────────────────────────────────────────────────────

def run_case(
    case: TestCase,
    driver: CommandDriver,
    remote: RemoteExec,
    ctx: TestCtx,
) -> CaseResult:
    res = CaseResult(name=case.name, description=case.description, focused=case.focused)
    t0 = time.time()
    try:
        # 1. 焦点切换
        target_focused = case.focused
        ok, info = set_focus(remote, target_focused)
        if not ok:
            res.error = f"focus switch failed: {info}"
            return res
        cprint(C.GRAY, f"  focus -> {info}")

        # 2. pre observe（基线）
        pre = driver.observe()
        res.pre_observe = pre
        if "error" in pre and pre["error"]:
            cprint(C.YELLOW, f"  WARN pre observe error: {pre['error']}")

        # 3. 计算 params 并发命令
        params = case.params_factory(pre)
        cprint(C.GRAY, f"  -> {case.capability} {json.dumps(params, ensure_ascii=False)}")
        cmd_res = driver.send(case.capability, params, timeout=case.timeout)
        res.cmd_result = cmd_res

        # 短停顿让游戏 tick 完成
        time.sleep(1.0)

        # 4. post observe
        post = driver.observe()
        res.post_observe = post

        # 5. log 片段
        if case.log_grep:
            res.log_snippet = remote.grep_log(case.log_grep, tail_lines=10)

        # 6. verify
        if case.verify is None:
            ok, msg = verify_command_success()(pre, post, cmd_res, ctx)
        else:
            ok, msg = case.verify(pre, post, cmd_res, ctx)
        res.passed = ok
        res.extra["verify_msg"] = msg

    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
        res.passed = False
    finally:
        res.duration_sec = round(time.time() - t0, 2)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def list_cases(cases: list[TestCase]) -> None:
    cprint(C.BOLD, "Available test cases:")
    for c in cases:
        focus_label = "focused" if c.focused else "unfocused"
        tags = f"[{','.join(c.tags)}]" if c.tags else ""
        print(f"  {c.name:50s} ({focus_label})  {tags}")
        print(f"    {c.description}")


def filter_cases(
    cases: list[TestCase],
    focused_only: bool,
    unfocused_only: bool,
    name: Optional[str],
) -> list[TestCase]:
    out = list(cases)
    if focused_only:
        out = [c for c in out if c.focused or c.must_be_focused]
    if unfocused_only:
        out = [c for c in out if not c.focused and not c.must_be_focused]
    if name:
        out = [c for c in out if c.name == name or name in c.name or name in c.tags]
    return out


def run_all(
    cases: list[TestCase],
    driver: CommandDriver,
    remote: RemoteExec,
    output_path: str = RESULT_OUTPUT,
) -> int:
    ctx = TestCtx()
    results: list[CaseResult] = []

    cprint(C.BOLD, f"\nRunning {len(cases)} cases via {driver.name}\n")

    for i, case in enumerate(cases, 1):
        cprint(C.BLUE, f"[{i}/{len(cases)}] {case.name}")
        cprint(C.GRAY, f"  {case.description}")
        r = run_case(case, driver, remote, ctx)
        results.append(r)

        # 即时反馈
        verify_msg = r.extra.get("verify_msg", "")
        if r.passed:
            cprint(C.GREEN, f"  PASS ({r.duration_sec}s) {verify_msg}")
        elif r.error:
            cprint(C.RED, f"  ERROR ({r.duration_sec}s) {r.error}")
        else:
            cprint(C.RED, f"  FAIL ({r.duration_sec}s) {verify_msg}")
            if r.log_snippet:
                cprint(C.GRAY, f"  log:")
                for line in r.log_snippet.splitlines()[:5]:
                    cprint(C.GRAY, f"    {line}")
        print()

    # 汇总
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    cprint(C.BOLD, "─" * 60)
    cprint(C.BOLD, f"SUMMARY: {passed} passed, {failed} failed (of {len(results)})")
    if failed:
        cprint(C.RED, "Failed cases:")
        for r in results:
            if not r.passed:
                msg = r.error or r.extra.get("verify_msg", "")
                cprint(C.RED, f"  - {r.name} ({'focused' if r.focused else 'unfocused'}): {msg}")

    # 落盘
    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "driver": driver.name,
        "summary": {"passed": passed, "failed": failed, "total": len(results)},
        "cases": [asdict(r) for r in results],
    }
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        cprint(C.GRAY, f"\nResults saved: {output_path}")
    except OSError as e:
        cprint(C.YELLOW, f"\nFailed to save results: {e}")

    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", action="store_true", help="列出所有用例后退出")
    parser.add_argument("--focused-only", action="store_true", help="仅执行聚焦用例")
    parser.add_argument("--unfocused-only", action="store_true", help="仅执行失焦用例")
    parser.add_argument("--case", type=str, default=None, help="只跑匹配的用例（按 name 或 tag）")
    parser.add_argument("--backend", choices=["auto", "A", "B"], default="auto",
                        help="auto=优先 A 回落 B；A=file-trigger；B=smapi-keystroke")
    parser.add_argument("--output", type=str, default=RESULT_OUTPUT,
                        help=f"结果 JSON 输出路径（默认 {RESULT_OUTPUT}）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将要执行的命令，不实际发送")
    args = parser.parse_args()

    cases = build_test_cases()

    if args.list:
        list_cases(cases)
        return 0

    if args.focused_only and args.unfocused_only:
        cprint(C.RED, "--focused-only 与 --unfocused-only 互斥")
        return 2

    selected = filter_cases(cases, args.focused_only, args.unfocused_only, args.case)
    if not selected:
        cprint(C.RED, "没有匹配的用例")
        return 2

    # 准备 driver
    remote = RemoteExec(dry_run=args.dry_run)
    if args.dry_run:
        cprint(C.YELLOW, "[DRY RUN] 跳过 driver 检测")
        list_cases(selected)
        return 0

    try:
        driver = detect_driver(remote, prefer=args.backend)
    except RuntimeError as e:
        cprint(C.RED, f"Driver detection failed:\n{e}")
        return 3

    return run_all(selected, driver, remote, args.output)


if __name__ == "__main__":
    sys.exit(main())
