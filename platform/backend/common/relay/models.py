from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NewAPIEnvelope:
    success: bool
    message: str
    data: Any = None
