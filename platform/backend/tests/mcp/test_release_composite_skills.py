"""Fake composition tests for the four M3 release composite Skills."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.mcp.profiles.local_stdio import local_controlled_risk_policy
from modules.mcp.capabilities.models import CapabilityContract, CapabilitySnapshot
from modules.mcp.contracts.models import SideEffect
from modules.mcp.release_manifest import default_mounted_skill_refs
from modules.mcp.skills.models import ExposureContext, RiskDecision
from modules.mcp.skills.registry import SkillRegistry
from modules.mcp.skills.runtime_models import SkillFailure


ROOT = Path(__file__).resolve().parents[4]
SKILLS = ROOT / "platform/backend/modules/mcp/skills"


def _program(relative: str):
    path = SKILLS / relative / "implementation.py"
    spec = importlib.util.spec_from_file_location("release_composite_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run


class FakeContext:
    def __init__(self, fail_at: str | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.fail_at = fail_at

    async def invoke(self, capability_id, _version, arguments):
        self.calls.append((capability_id, dict(arguments)))
        if capability_id == self.fail_at:
            raise RuntimeError("fake capability failure")
        return SimpleNamespace(output={"observation": f"observed:{capability_id}"})


@pytest.mark.asyncio
async def test_observe_current_view_only_invokes_queries_with_truthful_inputs():
    ctx = FakeContext()
    result = await _program("builtin/stardew.basic.observe_current_view")(ctx, {})
    assert [name for name, _ in ctx.calls] == [
        "query_runtime_snapshot", "query_inventory_snapshot", "query_ui_snapshot",
        "query_world_region",
    ]
    assert result["status"] == "completed"
    assert result["observations"]["world"] == "observed:query_world_region"
    assert result["observations"]["inspected_refs"] == ""


@pytest.mark.asyncio
async def test_navigate_and_interact_is_serial_and_stops_on_navigation_failure():
    arguments = {
        "destination": {"map_id": "Farm", "x": 4, "y": 5, "arrival_mode": "adjacent", "stand_side": "down"},
        "interaction": {"x": 4, "y": 5},
    }
    ok = FakeContext()
    result = await _program("builtin/stardew.basic.navigate_and_interact")(ok, arguments)
    assert [name for name, _ in ok.calls] == ["query_world_region", "go_to", "interact"]
    assert result["status"] == "completed"

    failed = FakeContext(fail_at="go_to")
    with pytest.raises(RuntimeError):
        await _program("builtin/stardew.basic.navigate_and_interact")(failed, arguments)
    assert [name for name, _ in failed.calls] == ["query_world_region", "go_to"]


@pytest.mark.asyncio
async def test_go_to_place_resolves_shop_alias_and_invokes_go_to_once():
    ctx = FakeContext()

    result = await _program("builtin/stardew.navigation.go_to_place")(
        ctx, {"place": "商店", "arrival": "counter"},
    )

    assert ctx.calls == [("go_to", {
        "map_id": "SeedShop",
        "x": 4,
        "y": 19,
        "arrival_mode": "adjacent",
        "stand_side": "down",
    })]
    assert result == {
        "status": "completed",
        "place_id": "SeedShop",
        "map_id": "SeedShop",
        "arrival": "counter",
        "observation": "observed:go_to",
    }


@pytest.mark.asyncio
async def test_go_to_place_resolves_any_standard_map_to_its_runtime_safe_point():
    ctx = FakeContext()

    result = await _program("builtin/stardew.navigation.go_to_place")(
        ctx, {"place": "温室"},
    )

    assert ctx.calls == [("go_to", {
        "map_id": "Greenhouse",
        "x": -1,
        "y": -1,
        "arrival_mode": "exact",
    })]
    assert result["place_id"] == "Greenhouse"
    assert result["map_id"] == "Greenhouse"
    assert result["arrival"] == "safe_point"


@pytest.mark.asyncio
async def test_go_to_place_defers_modded_map_validation_to_the_runtime_graph():
    ctx = FakeContext()

    result = await _program("builtin/stardew.navigation.go_to_place")(
        ctx, {"place": "Custom_Modded_Forest"},
    )

    assert ctx.calls == [("go_to", {
        "map_id": "Custom_Modded_Forest",
        "x": -1,
        "y": -1,
        "arrival_mode": "exact",
    })]
    assert result["map_id"] == "Custom_Modded_Forest"


@pytest.mark.parametrize(("alias", "map_id"), [
    ("商店", "SeedShop"),
    ("骷髅矿洞", "SkullCave"),
    ("电影院", "MovieTheater"),
    ("女巫沼泽", "WitchSwamp"),
    ("姜岛农场", "IslandWest"),
    ("火山锻造台", "Caldera"),
])
def test_go_to_place_catalog_covers_standard_chinese_aliases(alias, map_id):
    from modules.mcp.place_catalog import resolve_place

    assert resolve_place(alias).map_id == map_id


@pytest.mark.asyncio
async def test_controlled_water_crops_performs_observe_equip_navigate_and_use_tool():
    ctx = FakeContext()
    targets = [
        {"map_id": "Farm", "x": 1, "y": 2, "stand_side": "down"},
        {"map_id": "Farm", "x": 3, "y": 4, "stand_side": "down"},
    ]
    result = await _program("controlled-m4/stardew.daily.water_crops")(ctx, {"targets": targets})
    assert [name for name, _ in ctx.calls] == [
        "query_world_region", "query_inventory_snapshot", "equip",
        "go_to", "use_tool", "go_to", "use_tool",
    ]
    assert ctx.calls[2][1] == {"item_name": "Watering Can"}
    assert result["targets_completed"] == 2


@pytest.mark.asyncio
async def test_controlled_harvest_crops_performs_observe_navigate_and_interact():
    ctx = FakeContext()
    target = {"map_id": "Farm", "x": 8, "y": 9, "stand_side": "down"}
    result = await _program("controlled-m4/stardew.daily.harvest_crops")(ctx, {"targets": [target]})
    assert [name for name, _ in ctx.calls] == [
        "query_world_region", "query_inventory_snapshot", "go_to", "interact",
    ]
    assert result["targets_completed"] == 1


def test_local_controlled_policy_is_default_deny_with_explicit_exact_opt_in():
    assert local_controlled_risk_policy(False) == {}
    assert local_controlled_risk_policy(True) == {
        "changes_save": RiskDecision.ALLOW,
        "consumes_energy": RiskDecision.ALLOW,
    }
    with pytest.raises(ValueError, match="controlled_mutation_flag_invalid"):
        local_controlled_risk_policy(1)  # type: ignore[arg-type]


def test_local_controlled_packages_are_visible_only_after_explicit_risk_opt_in():
    builtin = SKILLS / "builtin"
    controlled = SKILLS / "controlled-m4"
    registry = SkillRegistry.index(builtin, controlled)
    controlled_packages = [p for p in registry.packages if p.source.value == "controlled"]
    capability_ids = sorted({
        requirement.capability_id
        for package in controlled_packages
        for requirement in package.manifest.requires
    })
    contracts = tuple(
        CapabilityContract(
            capability_id, "2.4.5", "protocol-2.4.5", {}, None,
            "game:write", SideEffect.MUTATING, 30.0, True, True, False, None,
        )
        for capability_id in capability_ids
    )
    snapshot = CapabilitySnapshot((), contracts, (), "cap-digest")
    mounts = default_mounted_skill_refs(builtin, controlled)

    def catalog(enabled: bool):
        return registry.resolve_catalog(ExposureContext(
            "stardew-valley", mounts, local_controlled_risk_policy(enabled), False,
            "mount", "policy", "binding", 1, "game",
        ), snapshot)

    denied = [s for s in catalog(False).skills if s.package.source.value == "controlled"]
    enabled = [s for s in catalog(True).skills if s.package.source.value == "controlled"]
    assert len(denied) == len(enabled) == 2
    assert not any(skill.available for skill in denied)
    assert all(skill.available for skill in enabled)


def test_builtin_risk_projection_is_default_deny_but_safe_actions_remain_visible():
    builtin = SKILLS / "builtin"
    registry = SkillRegistry.index(builtin)
    capability_ids = sorted({
        requirement.capability_id
        for package in registry.packages
        for requirement in package.manifest.requires
    })
    contracts = tuple(
        CapabilityContract(
            capability_id, "2.4.5", "protocol-2.4.5", {}, None,
            "game:write", SideEffect.MUTATING, 30.0, True, True, False, None,
        )
        for capability_id in capability_ids
    )
    snapshot = CapabilitySnapshot((), contracts, (), "cap-digest")
    mounts = default_mounted_skill_refs(builtin)

    def available(policy):
        catalog = registry.resolve_catalog(ExposureContext(
            "stardew-valley", mounts, policy, False,
            "mount", "policy", "binding", 1, "game",
        ), snapshot)
        return {skill.package.manifest.skill_id for skill in catalog.skills if skill.available}

    default = available({})
    assert "stardew.proto.say" in default
    assert "stardew.proto.go_to" in default
    assert "stardew.proto.interact" not in default
    assert "stardew.proto.use_tool" not in default
    assert "stardew.proto.go_to_bed" not in default
    assert "stardew.proto.menu_click" not in default

    explicitly_allowed = available({
        "changes_save": RiskDecision.ALLOW,
        "consumes_energy": RiskDecision.ALLOW,
    })
    assert "stardew.proto.interact" in explicitly_allowed
    assert "stardew.proto.use_tool" in explicitly_allowed
    assert "stardew.proto.go_to_bed" not in explicitly_allowed  # advances_time stays denied
    assert "stardew.proto.menu_click" not in explicitly_allowed  # item/money stay denied
