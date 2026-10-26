from __future__ import annotations

import time

import pytest

from modules.mcp.contracts.models import ExecutionContext, SideEffect
from modules.mcp.core.catalog import ToolCatalog, ToolDefinition
from modules.mcp.fakes import FakeContextAuthority, FakeGameSessionRouter
from modules.mcp.fakes.router import RouteFence


def make_context(*, principal="principal-a", session="game-a", nonce="nonce-a", generation=1,
                 binding_version=1, lease_epoch=1, digest="digest-a", tools=None):
    return ExecutionContext(
        profile="fake", principal_id=principal, runtime_ref=f"runtime-{principal}", generation=generation,
        binding_ref=f"binding-{principal}", binding_version=binding_version,
        game_session_ref=session, game_instance_nonce=nonce, lease_epoch=lease_epoch,
        scopes=frozenset({"game:read", "game:write"}),
        allowed_tools=frozenset(tools or {"observe", "say"}), capability_digest=digest,
        issued_at=time.time(), trace_context={"trace_id": f"trace-{principal}"},
    )


@pytest.fixture
def catalog():
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    say = {"type": "object", "properties": {"message": {"type": "string"}},
           "required": ["message"], "additionalProperties": False}
    return ToolCatalog([
        ToolDefinition("observe", "observe", "canonical.v1", "game:read", SideEffect.READ_ONLY, empty),
        ToolDefinition("say", "say", "canonical.v1", "game:write", SideEffect.MUTATING, say),
    ])


@pytest.fixture
def stack(catalog):
    context = make_context()
    authority = FakeContextAuthority({"credential-a": context})
    router = FakeGameSessionRouter(RouteFence("game-a", "nonce-a", 1, 1, 1, "digest-a"))
    return context, authority, router, catalog
