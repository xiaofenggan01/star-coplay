"""Claude CLI provider-private adapter package.

Wraps the real ``claude`` CLI (stream-json stdin/stdout, one OS process per
``(runtime_id, generation)``) behind the provider-neutral
``AgentRuntimeAdapter`` Protocol from ``modules.runtime_manager.contracts``.

Hosted MCP is injected through a thin stdio proxy; the adapter persists only
the endpoint and 0600 credential path, never credential content.
"""
