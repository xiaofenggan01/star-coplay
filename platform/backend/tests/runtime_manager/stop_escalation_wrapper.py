"""Controlled fault-injection wrapper for testing ClaudeProcessTransport stop
escalation (EOF → TERM → KILL ladder).

Spawned by the test; bridges stdio to the *real* Claude CLI (passed as
remaining argv after the mode argument).  Protocol frames flow transparently.

Uses raw ``os.read`` / ``os.write`` to avoid Python userspace buffering
interacting with the multi-layer pipe topology.  Threads for stdout/stderr
so the main thread owns stdin bridging and child lifecycle.

Modes (first positional argument):

- ``ignore-eof``: ignores stdin EOF from the transport (the adapter will
  escalate to TERM).  The real Claude child runs in the same process group,
  so ``os.killpg(pgid, SIGTERM)`` reaches it directly.
- ``ignore-eof-term``: ignores stdin EOF AND installs SIG_IGN for SIGTERM.
  The transport must escalate to SIGKILL to stop this wrapper.

Standard-library only — must run under the same Python as the test.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

MODE = sys.argv[1]  # "ignore-eof" | "ignore-eof-term"
REAL_CMD = sys.argv[2:]  # e.g. ["claude", "-p", "--model", ...]


def _bridge_raw(src_fd: int, dst_fd: int, label: str) -> None:
    """Raw fd → fd pump: one ``os.read`` / ``os.write`` per iteration.
    No Python userspace buffering."""
    try:
        while True:
            data = os.read(src_fd, 65536)
            if not data:
                break
            # Write all bytes (os.write may do a short write on some platforms;
            # loop until done.)
            written = 0
            while written < len(data):
                n = os.write(dst_fd, data[written:])
                if n <= 0:
                    return
                written += n
    except (BrokenPipeError, OSError, ValueError):
        pass


def main() -> None:
    if MODE == "ignore-eof-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    proc = subprocess.Popen(
        REAL_CMD,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Bridge child stdout/stderr → parent using raw fds (daemon threads).
    threading.Thread(
        target=_bridge_raw,
        args=(proc.stdout.fileno(), sys.stdout.buffer.fileno(), "stdout"),
        daemon=True,
    ).start()
    threading.Thread(
        target=_bridge_raw,
        args=(proc.stderr.fileno(), sys.stderr.buffer.fileno(), "stderr"),
        daemon=True,
    ).start()

    # Main thread: bridge parent stdin → child stdin.
    stdin_fd = sys.stdin.buffer.fileno()
    child_stdin_fd = proc.stdin.fileno()
    try:
        while True:
            data = os.read(stdin_fd, 65536)
            if not data:
                # EOF from parent (transport closed stdin).
                if MODE in ("ignore-eof", "ignore-eof-term"):
                    break  # stop forwarding; child stdin stays open
                else:
                    os.close(child_stdin_fd)
                    break
            written = 0
            while written < len(data):
                n = os.write(child_stdin_fd, data[written:])
                if n <= 0:
                    break
                written += n
    except (BrokenPipeError, OSError):
        pass

    # Wait for the real Claude to exit (SIGTERM from os.killpg, or natural).
    proc.wait()

    if MODE == "ignore-eof-term":
        # SIGTERM ignored; keep running until SIGKILL (kernel-enforced).
        while True:
            time.sleep(60)

    sys.exit(proc.returncode or 0)


if __name__ == "__main__":
    main()
