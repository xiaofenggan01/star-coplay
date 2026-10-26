"""Agentee profile/persona data access layer."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import asyncpg


@dataclass(frozen=True)
class AgenteeRuntimeData:
    """Minimal data needed at session spawn time."""

    display_name: str
    user_call_name: str
    persona_card: Optional[str]


class AgenteeRepository(Protocol):
    async def get_default_runtime_data(self, user_id: str) -> Optional[AgenteeRuntimeData]:
        """Fetch the default agentee's runtime-relevant fields for a user."""
        ...


class DbAgenteeRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_default_runtime_data(self, user_id: str) -> Optional[AgenteeRuntimeData]:
        query = """
            SELECT p.display_name, p.user_call_name, ps.persona_card
            FROM public.agentee_profiles p
            LEFT JOIN public.agentee_personas ps ON ps.agentee_id = p.agentee_id
            WHERE p.user_id = $1 AND p.is_default = true
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id)
        if row is None:
            return None
        return AgenteeRuntimeData(
            display_name=row["display_name"],
            user_call_name=row["user_call_name"],
            persona_card=row["persona_card"],
        )
