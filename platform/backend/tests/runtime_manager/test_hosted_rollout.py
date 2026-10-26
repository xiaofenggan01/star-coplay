import pytest

from modules.config.system_config_client import SystemConfigClient
from modules.runtime_manager.admission import RuntimeAdmissionPolicy
from modules.runtime_manager.domain import HostedRuntimeNotAllowed
from modules.runtime_manager.rollout import HostedRolloutProvider


class Repository:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    async def fetch_config(self, domain, config_key, environment):
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return {
            "domain": domain,
            "config_key": config_key,
            "environment": environment,
            "value_type": "json",
            "parsed_value": self.value,
            "updated_at": "revision-1",
        }


@pytest.mark.asyncio
async def test_allowlist_matches_user_id_and_normalized_email():
    repository = Repository({
        "enabled": True,
        "mode": "allowlist",
        "user_ids": ["user-1"],
        "emails": ["Tester@Example.com"],
    })
    provider = HostedRolloutProvider(
        SystemConfigClient(repository, default_environment="prod"),
        environment="prod",
    )

    assert await provider.is_allowed("user-1") is True
    assert await provider.is_allowed("user-2", "tester@example.com") is True
    assert await provider.is_allowed("user-3", "other@example.com") is False
    assert repository.calls == 1

    policy = RuntimeAdmissionPolicy(rollout_provider=provider)
    policy.require_allowed("user-2")
    with pytest.raises(HostedRuntimeNotAllowed):
        policy.require_allowed("user-3")


@pytest.mark.asyncio
async def test_disabled_or_invalid_config_fails_closed_to_legacy():
    repository = Repository({
        "enabled": "yes",
        "mode": "all_authenticated",
        "user_ids": [],
        "emails": [],
    })
    provider = HostedRolloutProvider(
        SystemConfigClient(repository, default_environment="prod"),
        environment="prod",
    )

    assert await provider.is_allowed("user-1") is False
    assert provider.is_allowed_cached("user-1") is False


@pytest.mark.asyncio
async def test_all_authenticated_mode_admits_every_authenticated_user():
    repository = Repository({
        "enabled": True,
        "mode": "all_authenticated",
        "user_ids": [],
        "emails": [],
    })
    provider = HostedRolloutProvider(
        SystemConfigClient(repository, default_environment="dev"),
        environment="dev",
    )

    assert await provider.is_allowed("any-user") is True
    RuntimeAdmissionPolicy(rollout_provider=provider).require_allowed("another-user")
