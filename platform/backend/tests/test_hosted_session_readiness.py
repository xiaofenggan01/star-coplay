from types import SimpleNamespace

import pytest

from app.bootstrap import _HostedSessionReadinessProbe
from modules.mcp.release_manifest import PROTO_CAPABILITIES, V2_CAPABILITIES
from modules.runtime_manager.session_activation import HOSTED_DEFAULT_RUNTIME_GRANTS


class Registry:
    def __init__(self, entries):
        self.entries = entries
        self.route = SimpleNamespace(
            facts=SimpleNamespace(capability_digest="sha256:ready"),
        )

    async def resolve_authenticated(self, game_session_id):
        assert game_session_id == "game-1"
        return self.route

    async def capability_entries(self, game_session_id):
        assert game_session_id == "game-1"
        return self.entries


def session():
    return SimpleNamespace(
        hosted_game_session_id="game-1",
        hosted_facts=SimpleNamespace(capability_digest="sha256:ready"),
    )


def capability_entries():
    return [
        *(
            {
                "source_surface": "protocol-2.4.5",
                "capability_id": capability_id,
                "capability_contract_version": "2.4.5",
            }
            for capability_id in PROTO_CAPABILITIES
        ),
        *(
            {
                "source_surface": "v2-compat",
                "capability_id": capability_id,
                "capability_contract_version": "2.4.5",
            }
            for capability_id in V2_CAPABILITIES
        ),
    ]


@pytest.mark.asyncio
async def test_readiness_checks_mod_capabilities_but_ignores_versioned_skill_grants():
    probe = _HostedSessionReadinessProbe(Registry(capability_entries()))

    assert await probe(session(), HOSTED_DEFAULT_RUNTIME_GRANTS) is True


@pytest.mark.asyncio
async def test_readiness_fails_when_an_advanced_skill_dependency_is_missing():
    entries = capability_entries()
    entries = [entry for entry in entries if entry["capability_id"] != "water_crops"]
    probe = _HostedSessionReadinessProbe(Registry(entries))

    assert await probe(session(), HOSTED_DEFAULT_RUNTIME_GRANTS) is False
