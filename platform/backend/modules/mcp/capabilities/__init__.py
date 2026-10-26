"""M3.2 Capability Runtime — Proto-only Router-backed execution."""
from modules.mcp.capabilities.models import (
    CapabilityActivityReporter,
    CapabilityAdapter,
    CapabilityAuditSink,
    CapabilityContract,
    CapabilityContractCatalog,
    CapabilityDiagnostic,
    CapabilityErrorCode,
    CapabilityInvocationHandle,
    CapabilityManifestProvider,
    CapabilityRef,
    CapabilityRuntime,
    CapabilityRuntimeError,
    CapabilitySnapshot,
    PreparedCapabilityCall,
    ProtocolCapabilityEntry,
)
from modules.mcp.capabilities.contracts import (
    ProtoCapabilityAdapter,
    build_capability_contract_catalog,
    validate_json_schema,
)
from modules.mcp.capabilities.snapshot import (
    build_capability_snapshot,
    compute_capability_snapshot_digest,
)
from modules.mcp.capabilities.runtime import RouterBackedCapabilityRuntime

__all__ = [
    "CapabilityActivityReporter",
    "CapabilityAdapter",
    "CapabilityAuditSink",
    "CapabilityContract",
    "CapabilityContractCatalog",
    "CapabilityDiagnostic",
    "CapabilityErrorCode",
    "CapabilityInvocationHandle",
    "CapabilityManifestProvider",
    "CapabilityRef",
    "CapabilityRuntime",
    "CapabilityRuntimeError",
    "CapabilitySnapshot",
    "PreparedCapabilityCall",
    "ProtocolCapabilityEntry",
    "ProtoCapabilityAdapter",
    "RouterBackedCapabilityRuntime",
    "build_capability_contract_catalog",
    "build_capability_snapshot",
    "compute_capability_snapshot_digest",
    "validate_json_schema",
]
