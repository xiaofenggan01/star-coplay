"""Source-derived Mod capability inventory and conservative M2 catalog baseline."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from agent.protocol.canonical_surface import canonical_specs
from modules.mcp.contracts.capabilities import McpCatalogEntry, ModCapabilityManifest
from modules.mcp.contracts.models import SideEffect
from modules.mcp.core.catalog import ToolCatalog, ToolDefinition
from modules.mcp.evidence import reduce_profile_support
from modules.mcp.mapping_authority import mapper_authority

_READ_ONLY = {
    "query_menu", "query_world_region", "query_runtime_snapshot",
    "query_inventory_snapshot", "query_ui_snapshot", "query_inspect_refs",
    "query_inventory", "query_location", "query_map_exits", "observe",
}
_COMPOUND = {
    "water_crops", "harvest_crops", "ship_items", "buy_items", "plant_crops",
    "chop_trees", "mine_rocks", "execute_on_targets", "menu_interact", "go_to",
    "go_bed",
}
_V2_ASYNC = {
    "move_to", "face", "interact", "do", "use_tool", "open_menu", "menu_click", "menu_close",
    "go_to", "go_bed", "water_crops", "harvest_crops", "buy_items",
    "plant_crops", "chop_trees", "mine_rocks", "execute_on_targets", "menu_interact",
}
_PROTO_ASYNC = {
    "face", "move_to", "go_to", "go_to_bed", "interact", "use_tool", "equip",
    "open_menu", "menu_click", "menu_close",
}
_V2_PRODUCTION = {
    "water_crops", "harvest_crops", "ship_items", "buy_items", "plant_crops",
    "chop_trees", "mine_rocks", "execute_on_targets", "menu_interact",
}
_ALIASES = {"observe": "observe_current_view", "go_bed": "go_to_bed"}
_INTERNAL = {"tp"}
_NOT_EXPOSED = {"test_input", "dev_eval"}
_V2_COMPATIBILITY = {
    "observe", "move_to", "face", "interact", "do", "use_tool", "equip",
    "say", "emote", "open_menu", "menu_click", "menu_close", "go_to", "go_bed",
}


def _protocol_facts(repo_root: Path) -> tuple[str, str]:
    proto_dir = repo_root / "protocol/proto"
    capability = (proto_dir / "capability.proto").read_text(encoding="utf-8")
    package = re.search(r"package\s+starcoplay\.protocol\.v(\d+);", capability)
    if package is None:
        raise ValueError("protocol package version is missing")
    digits = package.group(1)
    version = ".".join(digits) if len(digits) == 3 else digits
    schema_bytes = b"".join(
        path.name.encode() + b"\0" + path.read_bytes().replace(b"\r\n", b"\n")
        for path in sorted(proto_dir.glob("*.proto"))
    )
    return version, "proto-sha256:" + hashlib.sha256(schema_bytes).hexdigest()


def _proto_declared(repo_root: Path) -> set[str]:
    text = (repo_root / "protocol/proto/capability.proto").read_text(encoding="utf-8")
    block = text.split("oneof params {", 1)[1].split("\n  }", 1)[0]
    return set(re.findall(r"^\s*\w+\s+(\w+)\s*=", block, re.MULTILINE))


def _handler_capabilities(repo_root: Path) -> dict[str, str]:
    handlers = repo_root / "mod/StarCoPlay/ProtocolCore/Handlers"
    result: dict[str, str] = {}
    for path in handlers.glob("*.cs"):
        text = path.read_text(encoding="utf-8")
        classes = [(match.start(), match.group(1)) for match in re.finditer(r"class\s+(\w+)", text)]
        for match in re.finditer(r'Capability\s*=>\s*"([a-z0-9_]+)"', text):
            class_name = max((item for item in classes if item[0] < match.start()), default=(0, ""))[1]
            if class_name:
                result[class_name] = match.group(1)
    return result


def _matching_brace(text: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return len(text)


def _enclosing_conditions(text: str, position: int) -> tuple[str, ...]:
    conditions: list[str] = []
    for match in re.finditer(r"if\s*\(([^\n)]+)\)\s*\{", text):
        opening = text.find("{", match.start())
        if opening < position < _matching_brace(text, opening):
            conditions.append(re.sub(r"\s+", " ", match.group(1)).strip())
    return tuple(conditions)


def _registration_facts(repo_root: Path, registry: str) -> dict[str, set[str]]:
    """Return handler expression -> source-derived enclosing conditions."""
    text = (repo_root / "mod/StarCoPlay/ModEntry.cs").read_text(encoding="utf-8")
    facts: dict[str, set[str]] = {}
    pattern = re.compile(rf"{re.escape(registry)}\.Register\(\s*(?:new\s+)?([A-Za-z0-9_.]+)")
    for match in pattern.finditer(text):
        conditions = _enclosing_conditions(text, match.start())
        facts.setdefault(match.group(1), set()).add(" && ".join(conditions) if conditions else "unconditional")
    return facts


def _proto_registration(repo_root: Path) -> dict[str, str]:
    entry = (repo_root / "mod/StarCoPlay/ModEntry.cs").read_text(encoding="utf-8")
    handlers = _handler_capabilities(repo_root)
    variables = dict(re.findall(r"(?:var|\w+)\s+(\w+)\s*=\s*new\s+([A-Za-z0-9_.]+)", entry))
    facts = _registration_facts(repo_root, "_protoRegistry")
    result: dict[str, set[str]] = {}
    for expression, conditions in facts.items():
        class_name = variables.get(expression, expression).split(".")[-1]
        capability = handlers.get(class_name)
        if capability:
            result.setdefault(capability, set()).update(conditions)
    return {name: " | ".join(sorted(conditions)) for name, conditions in result.items()}


def _v2_constants(repo_root: Path) -> dict[str, str]:
    text = (repo_root / "mod/StarCoPlay/Legacy/AdapterRuntime/AdapterV2.cs").read_text(encoding="utf-8")
    return dict(re.findall(r'public const string (Capability\w+)\s*=\s*"([a-z0-9_]+)"', text))


def _v2_registration(repo_root: Path) -> dict[str, str]:
    text = (repo_root / "mod/StarCoPlay/ModEntry.cs").read_text(encoding="utf-8")
    constants = _v2_constants(repo_root)
    facts = _registration_facts(repo_root, "_adapterV2")
    result: dict[str, set[str]] = {}
    for expression, conditions in facts.items():
        if expression.startswith("AdapterV2."):
            name = constants.get(expression.split(".")[-1])
        elif expression.startswith('"'):
            name = expression.strip('"')
        else:
            name = expression if re.fullmatch(r"[a-z0-9_]+", expression) else None
        if name:
            result.setdefault(name, set()).update(conditions)
    # String literals are not captured by the identifier-oriented parser.
    for match in re.finditer(r'_adapterV2\.Register\(\s*"([a-z0-9_]+)"', text):
        conditions = _enclosing_conditions(text, match.start())
        result.setdefault(match.group(1), set()).add(" && ".join(conditions) if conditions else "unconditional")
    return {name: " | ".join(sorted(conditions)) for name, conditions in result.items()}


def build_manifest(repo_root: Path) -> list[ModCapabilityManifest]:
    protocol_version, proto_schema = _protocol_facts(repo_root)
    declared = _proto_declared(repo_root)
    proto_registration = _proto_registration(repo_root)
    v2_registration = _v2_registration(repo_root)
    v2_root = repo_root / "mod/StarCoPlay/Legacy"
    v2_schema_paths = sorted({
        v2_root / "AdapterRuntime/AdapterV2.cs",
        v2_root / "AdapterRuntime/Models/V2Command.cs",
        v2_root / "AdapterRuntime/Models/V2Result.cs",
        v2_root / "Observation/ObservationResult.cs",
        *v2_root.glob("AtomicHandlers/*.cs"),
        *v2_root.glob("CompoundActions/Handlers/Compound/*.cs"),
    })
    v2_schema_bytes = b"".join(
        path.relative_to(repo_root).as_posix().encode() + b"\0"
        + path.read_bytes().replace(b"\r\n", b"\n")
        for path in v2_schema_paths if path.exists()
    )
    v2_schema = "v2-json-sha256:" + hashlib.sha256(v2_schema_bytes).hexdigest()
    rows: list[ModCapabilityManifest] = []
    for name in sorted(declared | set(proto_registration)):
        registered = name in proto_registration
        classification = "internal-control" if name in _INTERNAL else "production"
        rows.append(ModCapabilityManifest(
            capability_id=f"proto:{name}", source_surface=f"protocol-{protocol_version}", source_version=protocol_version,
            declared=name in declared, registered=registered,
            registration_condition=proto_registration.get(name, "not-registered"),
            product_classification=classification, schema_version=proto_schema,
            side_effect_level="read_only" if name in _READ_ONLY else "mutating",
            execution_model="async" if name in _PROTO_ASYNC else "request_result",
            cancellable=name in _PROTO_ASYNC,
            idempotency_policy="game-command-id",
        ))
    for name in sorted(v2_registration):
        classification = "not-exposed" if name in _NOT_EXPOSED else (
            "internal-control" if name in _INTERNAL else (
                "production" if name in _V2_PRODUCTION else (
                    "compatibility" if name in _V2_COMPATIBILITY else "unclassified"
                )
            )
        )
        rows.append(ModCapabilityManifest(
            capability_id=f"v2:{name}", source_surface="v2-compat", source_version=protocol_version,
            declared=name in _v2_constants(repo_root).values(), registered=True,
            registration_condition=v2_registration[name],
            product_classification=classification, schema_version=v2_schema,
            side_effect_level="read_only" if name in _READ_ONLY else "mutating",
            execution_model="async" if name in _V2_ASYNC else "request_result",
            cancellable=name in _V2_ASYNC, idempotency_policy="legacy-adapter-command-id",
        ))
    return rows


_RENAMED_V2 = {
    "do": "act_on_target",
    "go_to": "navigate_to_target",
    "move_to": "move_to_target",
    "use_tool": "use_tool_at_target",
    "equip": "equip_matching_item",
    "observe": "observe_current_view",
    "go_bed": "sleep_for_night",
}
_PROVEN_V2_ALIASES = {
    "say", "emote", "face", "interact", "open_menu", "menu_click", "menu_close",
}
_COMPATIBILITY_DECISIONS = {
    "observe": "source alias reserved for observe_current_view; L3 result adapter evidence pending",
    "go_bed": "source alias reserved for go_to_bed; equivalence adapter evidence pending",
    "move_to": "same source name is not merged until V2 semantic-target behavior proves Proto equivalence",
    "face": "same source name is not merged until input/result equivalence is proven",
    "interact": "same source name is not merged until input/result equivalence is proven",
    "use_tool": "same source name is not merged because V2 permits implicit faced-tile targeting",
    "equip": "same source name is not merged because V2 item/zero-based slot differs from Proto selectors",
    "say": "same source name is retained as a compatibility source pending result equivalence",
    "emote": "same source name is not merged because V2 and Proto enum/schema fields differ",
    "open_menu": "same source name is retained as a compatibility source pending schema equivalence",
    "menu_click": "same source name is retained as a compatibility source pending schema equivalence",
    "menu_close": "same source name is retained as a compatibility source pending schema equivalence",
}


def _entry(
    items: list[ModCapabilityManifest], *, canonical_name: str, exposure: str,
    mapping: str, parity: str, exception: str | None = None,
) -> McpCatalogEntry:
    specs = canonical_specs()
    spec = specs.get(canonical_name)
    if exposure == "tool" and spec is None:
        raise ValueError(f"registered public capability has no canonical schema: {canonical_name}")
    first = items[0]
    if spec is None:
        input_schema: dict[str, object] = {}
        output_schema: dict[str, object] = {}
    else:
        input_schema = dict(spec.input_schema)
        output_schema = dict(spec.output_schema)
    read_only = all(item.side_effect_level == "read_only" for item in items)
    async_execution = any(item.execution_model == "async" for item in items)
    decision_id = None
    decision_owner = None
    decision_reason = None
    expires_at = None
    if exception is not None:
        decision_id = "mcp-capability-" + first.capability_id.replace(":", "-")
        decision_owner = "system-architecture-contract-owner"
        decision_reason = exception
        expires_at = "2027-07-13T00:00:00Z"
    elif canonical_name == "buy_items":
        decision_id = "mcp-canonical-buy-items-v1"
        decision_owner = "system-architecture-contract-owner"
        decision_reason = (
            "canonical v1 uses a typed item array instead of the legacy JSON-string form; "
            "the V2 handler accepts the deserialized array without semantic loss"
        )
        expires_at = "2027-07-13T00:00:00Z"
    return McpCatalogEntry(
        manifest_id=first.capability_id,
        source_manifest_ids=tuple(item.capability_id for item in items),
        canonical_name=canonical_name,
        exposure_kind=exposure,
        exposure_metadata={
            "kinds": [exposure],
            "resource": "not-exposed",
            "prompt": "not-exposed",
        },
        mapping_type=mapping,
        input_schema=input_schema,
        output_schema=output_schema,
        required_scope="game:read" if read_only else "game:write",
        profile_support={
            "hosted": "not-yet-adapter-verified",
            "local_direct": "not-yet-adapter-verified",
            "local_gateway": "deferred",
        },
        error_mapping="starcoplay.mcp.stable-errors.v1",
        lifecycle_support=("dispatch", "result", "status", "cancel") if async_execution else ("dispatch", "result"),
        contract_tests=("manifest-drift", "schema", "mapper", "command-identity"),
        parity_status=parity,
        exception_decision=exception,
        decision_id=decision_id,
        decision_owner=decision_owner,
        decision_reason=decision_reason,
        expires_at=expires_at,
    )


def build_catalog(manifest: list[ModCapabilityManifest], repo_root: Path | None = None) -> list[McpCatalogEntry]:
    """Build one public entry per canonical semantic contract.

    Every manifest row is consumed exactly once. Compatibility source rows that
    have not proven equivalence remain explicit non-public exceptions rather
    than widening a public schema or overwriting a same-named Proto contract.
    """
    public: dict[str, list[ModCapabilityManifest]] = {}
    exceptions: list[McpCatalogEntry] = []
    for item in manifest:
        source, name = item.capability_id.split(":", 1)
        if not item.registered:
            exceptions.append(_entry(
                [item], canonical_name=name, exposure="exception",
                mapping="declared-not-registered", parity="not-applicable",
                exception="declared by protobuf but no Mod handler is registered",
            ))
        elif item.product_classification == "not-exposed":
            exceptions.append(_entry(
                [item], canonical_name=name, exposure="exception",
                mapping="dev-only", parity="not-applicable", exception="test/dev-only source",
            ))
        elif item.product_classification == "internal-control":
            exceptions.append(_entry(
                [item], canonical_name=name, exposure="internal-control",
                mapping="internal-control", parity="contract-verified",
                exception="authority- and lease-gated internal recovery capability",
            ))
        elif item.product_classification == "unclassified":
            raise ValueError(f"registered capability requires product classification: {item.capability_id}")
        elif source == "v2" and name in _RENAMED_V2:
            public.setdefault(_RENAMED_V2[name], []).append(item)
        elif source == "v2" and name in _PROVEN_V2_ALIASES:
            public.setdefault(_ALIASES.get(name, name), []).append(item)
        elif source == "v2" and item.product_classification == "compatibility":
            exceptions.append(_entry(
                [item], canonical_name=_ALIASES.get(name, name), exposure="exception",
                mapping="compatibility-source", parity="explicit-exception",
                exception=_COMPATIBILITY_DECISIONS.get(name, "compatibility source equivalence is not proven"),
            ))
        else:
            public.setdefault(name, []).append(item)

    entries = [
        _entry(
            items,
            canonical_name=name,
            exposure="tool",
            mapping=(
                "renamed-compatibility-adapter" if name in _RENAMED_V2.values()
                else "compatibility-alias" if len(items) > 1 else "direct"
            ),
            parity="schema-mapper-verified",
        )
        for name, items in sorted(public.items())
    ]
    entries.extend(sorted(exceptions, key=lambda entry: entry.manifest_id))
    root = repo_root or Path(__file__).resolve().parents[4]
    protocol_version, protocol_schema = _protocol_facts(root)
    public_entries = [entry for entry in entries if entry.exposure_kind == "tool"]
    mapping_authority = mapper_authority(root)
    evidence_contracts = {
        entry.canonical_name: {
            "source_manifest_ids": list(entry.source_manifest_ids),
            "mapping_type": entry.mapping_type,
            "capability_version": "canonical.v1",
            "required_scope": entry.required_scope,
            "side_effect": "read_only" if entry.required_scope == "game:read" else "mutating",
            "input_schema": entry.input_schema,
            "output_schema": entry.output_schema,
            "error_mapping": entry.error_mapping,
            "lifecycle_support": list(entry.lifecycle_support),
            "exposure_kind": entry.exposure_kind,
            "mapper_authority": mapping_authority,
        }
        for entry in public_entries
    }
    support = reduce_profile_support(
        evidence_contracts, protocol_version, protocol_schema,
    )
    entries = [
        McpCatalogEntry(
            **{
                **entry.__dict__,
                "profile_support": {
                    "hosted": support["hosted"].get(entry.canonical_name, "not-yet-adapter-verified"),
                    "local_direct": support["local_direct"].get(entry.canonical_name, "not-yet-adapter-verified"),
                    "local_gateway": "deferred",
                },
            }
        )
        for entry in entries
    ]
    return entries


def build_tool_catalog(manifest: list[ModCapabilityManifest]) -> ToolCatalog:
    """Construct the callable ToolCatalog from the same immutable entries used by list."""
    specs = canonical_specs()
    tools: list[ToolDefinition] = []
    for entry in build_catalog(manifest):
        if entry.exposure_kind != "tool":
            continue
        spec = specs[entry.canonical_name]
        tools.append(ToolDefinition(
            name=entry.canonical_name,
            capability_id=entry.canonical_name,
            capability_version="canonical.v1",
            required_scope=entry.required_scope,
            side_effect=SideEffect.READ_ONLY if entry.required_scope == "game:read" else SideEffect.MUTATING,
            input_schema=entry.input_schema,
            output_schema=entry.output_schema,
            exposure_kind=entry.exposure_kind,
            timeout_seconds=spec.timeout_ms / 1000,
        ))
    return ToolCatalog(tools)


def build_proto_capability_entries(repo_root: Path) -> list[dict[str, str]]:
    """Return canonical sorted unique dict triples for registered Proto capabilities.

    Uses the same source facts already produced by this module.  Only rows
    whose capability_id has the ``proto:`` prefix are included; V2 entries are
    never returned.  The result is the authoritative Local expected snapshot.
    """
    protocol_version, _ = _protocol_facts(repo_root)
    proto_registration = _proto_registration(repo_root)
    # _proto_registration is keyed by capability id, so sorting its keys is
    # both the uniqueness boundary and the canonical digest order.
    return [
        {
            "source_surface": f"protocol-{protocol_version}",
            "capability_id": capability_id,
            "capability_contract_version": protocol_version,
        }
        for capability_id in sorted(proto_registration)
    ]


def baseline_document(repo_root: Path) -> dict[str, object]:
    protocol_version, protocol_schema = _protocol_facts(repo_root)
    manifest = build_manifest(repo_root)
    catalog = build_catalog(manifest, repo_root)
    payload = {
        "schema_version": "starcoplay.mcp-capability-baseline.v2",
        "source": {"mod_protocol": protocol_version, "protocol_schema": protocol_schema},
        "manifest": [row.as_dict() for row in manifest],
        "catalog": [row.as_dict() for row in catalog],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["baseline_digest"] = hashlib.sha256(canonical).hexdigest()
    return payload
