-- Durable Hosted MCP signing and revocation authority. Secrets stay in the
-- control-plane database and never enter runtime workdirs or logs.
CREATE TABLE public.runtime_mcp_signing_keys (
  kid text PRIMARY KEY,
  secret bytea NOT NULL CHECK (octet_length(secret) >= 32),
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  active boolean NOT NULL DEFAULT true
);
CREATE UNIQUE INDEX runtime_mcp_one_active_signing_key
  ON public.runtime_mcp_signing_keys ((active)) WHERE active;

CREATE TABLE public.runtime_mcp_jti_revocations (
  jti text PRIMARY KEY,
  revoked_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.runtime_mcp_binding_revocations (
  binding_id text PRIMARY KEY REFERENCES public.runtime_bindings(binding_id) ON DELETE CASCADE,
  through_binding_version bigint NOT NULL,
  through_lease_epoch bigint NOT NULL,
  revoked_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.runtime_mcp_signing_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.runtime_mcp_jti_revocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.runtime_mcp_binding_revocations ENABLE ROW LEVEL SECURITY;
