#!/usr/bin/env python3
"""
deploy/local/test-agent.py — Agent 本地集成测试

验证 session_runner.py 的：启动、消息处理、输出格式、优雅退出。
配置了平台 LLM relay 时额外验证决策环能产出 command 帧。

用法：
    python3 deploy/local/test-agent.py
    VENV_DIR=.venv-test python3 deploy/local/test-agent.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
VENV_DIR = Path(os.environ.get("VENV_DIR", str(REPO_DIR / ".venv")))
PY = VENV_DIR / "bin" / "python"
SESSION_RUNNER = REPO_DIR / "agent" / "session_runner.py"


def _has_llm_relay() -> bool:
    return bool(os.environ.get("LLM_RELAY_BASE_URL") and os.environ.get("LLM_RELAY_API_KEY"))

# ── 标准 NDJSON 帧 ────────────────────────────────────────────────────

HELLO_MSG = {
    "type": "hello",
    "adapter_info": {
        "capabilities": [
            {"name": "move_to", "description": "Move AI to a location"},
            {"name": "say", "description": "AI says something"},
            {"name": "emote", "description": "AI shows an emote"},
            {"name": "wait", "description": "AI waits"},
        ]
    },
}

STATE_SNAPSHOT = {
    "type": "state_snapshot",
    "state": {
        "game_version": "1.6.0",
        "player": {"name": "Farmer", "energy": 270, "max_energy": 270, "location": "Farm"},
        "world": {"day": 1, "season": "spring", "year": 1, "time": "8:00", "weather": "sunny"},
        "ai_player": {"name": "AI小星", "energy": 270, "location": "Farm"},
    },
}

CHAT_EVENT = {
    "type": "game_event",
    "seq": 1,
    "event": {"type": "chat_received", "data": {"player_name": "Farmer", "message": "你好！今天有什么计划？"}, "seq": 1},
}

STOP_MSG = {"type": "__stop__"}


# ── 进程辅助 ──────────────────────────────────────────────────────────

def _spawn(instance_id: str) -> subprocess.Popen:
    """启动 session_runner.py 子进程。"""
    env = dict(os.environ)
    env.setdefault("LLM_MODEL", "balanced")
    return subprocess.Popen(
        [str(PY), str(SESSION_RUNNER), "--agent-instance-id", instance_id],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def _send(proc: subprocess.Popen, msg: dict) -> None:
    proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    proc.stdin.flush()


def _stop(proc: subprocess.Popen, timeout: float = 6.0) -> tuple[int, str]:
    """发 __stop__，等待进程退出，返回 (exit_code, stderr_text)。"""
    try:
        _send(proc, STOP_MSG)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    stderr = proc.stderr.read().decode("utf-8", errors="replace")
    return proc.returncode, stderr


# ── 测试函数 ──────────────────────────────────────────────────────────

def t1_startup_and_stop() -> None:
    """T1: 进程能启动，收到 __stop__ 后干净退出，exit code 0。"""
    if not _has_llm_relay():
        return "SKIPPED"
    proc = _spawn("test-t1")
    time.sleep(2.0)
    code, stderr = _stop(proc)
    assert code == 0, f"exit code={code}\nstderr tail:\n{stderr[-400:]}"
    assert "session_runner started" in stderr, \
        f"'session_runner started' 未见于 stderr\nstderr tail:\n{stderr[-400:]}"


def t2_message_handling() -> None:
    """T2: hello + state_snapshot + chat_event 全部处理完毕无崩溃。

    需要平台 LLM relay 配置；只验证进程干净退出（exit 0 + session_runner exiting）。
    """
    if not _has_llm_relay():
        return "SKIPPED"
    proc = _spawn("test-t2")
    for msg in [HELLO_MSG, STATE_SNAPSHOT, CHAT_EVENT]:
        _send(proc, msg)
    time.sleep(2.5)
    code, stderr = _stop(proc)

    assert "session_runner exiting" in stderr, \
        f"'session_runner exiting' 未见于 stderr（进程异常退出？）\nstderr tail:\n{stderr[-400:]}"
    assert code == 0, f"exit code={code}\nstderr tail:\n{stderr[-400:]}"


def t3_output_is_valid_ndjson() -> None:
    """T3: stdout 输出的每一行都是合法 JSON dict。"""
    if not _has_llm_relay():
        return "SKIPPED"
    proc = _spawn("test-t3")
    bad_frames: list[str] = []

    def _read() -> None:
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                assert isinstance(obj, dict), f"not a dict: {obj!r}"
            except Exception as e:
                bad_frames.append(f"{e}: {line[:120]!r}")

    t = threading.Thread(target=_read, daemon=True)
    t.start()

    for msg in [HELLO_MSG, STATE_SNAPSHOT]:
        _send(proc, msg)
    time.sleep(1.5)
    code, _ = _stop(proc)
    t.join(timeout=2.0)

    assert not bad_frames, f"无效 NDJSON 帧:\n" + "\n".join(bad_frames)


def t4_command_output(timeout: float = 10.0) -> str | None:
    """T4: chat 事件触发后，stdout 产出 command 帧（有平台 relay → 真实决策；无 relay → 跳过）。"""
    if not _has_llm_relay():
        return "SKIPPED"

    proc = _spawn("test-t4")
    commands: list[dict] = []

    def _read() -> None:
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
                if isinstance(frame, dict) and frame.get("type") == "command":
                    commands.append(frame)
            except Exception:
                pass

    t = threading.Thread(target=_read, daemon=True)
    t.start()

    for msg in [HELLO_MSG, STATE_SNAPSHOT, CHAT_EVENT]:
        _send(proc, msg)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not commands:
        time.sleep(0.1)

    code, stderr = _stop(proc)
    t.join(timeout=2.0)

    assert commands, \
        f"超时 {timeout}s 内未收到任何 command 帧\nstderr tail:\n{stderr[-400:]}"
    cmd = commands[0]
    assert "capability" in cmd, f"command 帧缺少 capability 字段: {cmd}"
    assert "action_id" in cmd, f"command 帧缺少 action_id 字段: {cmd}"
    print(f"\n        [平台 relay] capability={cmd['capability']} params={cmd.get('params')}", end="")
    return None


# ── 主流程 ────────────────────────────────────────────────────────────

TESTS = [
    ("T1 启动 + 优雅退出", t1_startup_and_stop),
    ("T2 消息处理（hello/state/chat）", t2_message_handling),
    ("T3 stdout 全为合法 NDJSON", t3_output_is_valid_ndjson),
    ("T4 chat 触发 → stdout 收到 command 帧", t4_command_output),
]


def main() -> int:
    if not PY.exists():
        print(f"[ERROR] venv 不存在: {VENV_DIR}")
        print(f"  先运行: ./deploy/local/run-agent.sh --install --agent-instance-id check")
        return 1

    print(f"Agent 集成测试  (venv={VENV_DIR.name})\n{'─' * 50}")
    passed = failed = skipped = 0

    for name, fn in TESTS:
        print(f"  [ ] {name}", end="", flush=True)
        try:
            result = fn()
            if result == "SKIPPED":
                print(f"\r  [~] {name}  (SKIPPED — 无 LLM 密钥)")
                skipped += 1
            else:
                print(f"\r  [✓] {name}")
                passed += 1
        except AssertionError as e:
            print(f"\r  [✗] {name}")
            print(f"      {e}")
            failed += 1
        except Exception as e:
            print(f"\r  [✗] {name}")
            print(f"      {type(e).__name__}: {e}")
            failed += 1

    print(f"{'─' * 50}")
    print(f"结果: {passed} 通过 / {failed} 失败 / {skipped} 跳过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
