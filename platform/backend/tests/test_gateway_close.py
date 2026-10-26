from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.gateway.server import _ws_close_code


def test_ws_close_code_reads_normal_going_away():
    exc = SimpleNamespace(rcvd=SimpleNamespace(code=1001), sent=None)
    assert _ws_close_code(exc) == 1001
