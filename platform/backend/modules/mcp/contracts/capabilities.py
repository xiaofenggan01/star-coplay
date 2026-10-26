from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ModCapabilityManifest:
    capability_id: str
    source_surface: str
    source_version: str
    declared: bool
    registered: bool
    registration_condition: str
    product_classification: str
    schema_version: str
    side_effect_level: str
    execution_model: str
    cancellable: bool
    idempotency_policy: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class McpCatalogEntry:
    manifest_id: str
    source_manifest_ids: tuple[str, ...]
    canonical_name: str
    exposure_kind: str
    exposure_metadata: Mapping[str, Any]
    mapping_type: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    required_scope: str
    profile_support: Mapping[str, str]
    error_mapping: str
    lifecycle_support: tuple[str, ...]
    contract_tests: tuple[str, ...]
    parity_status: str
    exception_decision: str | None = None
    decision_id: str | None = None
    decision_owner: str | None = None
    decision_reason: str | None = None
    expires_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_manifest_ids"] = list(self.source_manifest_ids)
        value["lifecycle_support"] = list(self.lifecycle_support)
        value["contract_tests"] = list(self.contract_tests)
        return value
