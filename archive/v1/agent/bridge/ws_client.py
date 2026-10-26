from __future__ import annotations


class WsClient:
    def __init__(self) -> None:
        self.enabled = False

    def connect(self) -> bool:
        return False
