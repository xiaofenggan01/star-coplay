"""Provider-private adapter implementations for ``AgentRuntimeAdapter``.

Each subpackage (e.g. ``adapters.claude``) owns one provider's CLI flags,
wire protocol, and session concepts. Nothing under here may leak a provider
private field (Claude session id, CLI flags, MCP config) into
``modules.runtime_manager.contracts``; the public ``AgentRuntimeAdapter``
Protocol is the only surface the Harness depends on.
"""
