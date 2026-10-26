"""Constants for Agent-side Protocol 2.4.5 construction."""

PROTOCOL_VERSION = "2.4.5"

DEFAULT_TIMEOUT_MS = 15_000
LONG_TIMEOUT_MS = 60_000
QUERY_TIMEOUT_MS = 5_000

INTERNAL_ONLY_CAPABILITIES = frozenset({"tp"})
