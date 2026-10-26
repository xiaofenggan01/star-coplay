"""Authoritative generated schemas for atomic Proto Skill wrappers."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


def published_skill_schemas(
    skill_id: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    from agent.protocol.canonical_surface import canonical_specs

    specs = canonical_specs()
    atomic_prefix = "stardew.proto."
    if skill_id.startswith(atomic_prefix):
        capability_id = skill_id[len(atomic_prefix):]
        spec = specs.get(capability_id)
        if spec is None or spec.source_surface != "protocol-2.4.5":
            return None
        return deepcopy(spec.input_schema), deepcopy(spec.output_schema)

    return None
