"""Spike: provider-neutral AgentRuntimeAdapter harness.

This package is intentionally isolated from ``modules.runtime`` (the legacy
Claude/Gateway-specific runtime). Nothing here may import from
``modules.runtime`` or ``modules.gateway``, and no public type may carry a
Claude CLI or Gateway-specific field (e.g. ``claude_session_id`` or
``agent_instance_id``). See
``spike/agent-runtime-adapter/implementation-spec.md`` for the authoring
contract.
"""
