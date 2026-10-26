-- Runtime Manager product persistence.  This is deliberately additive: the
-- legacy public.agent_instances table remains an independent Gateway mirror.

CREATE TABLE public.runtime_instances (
  runtime_id            text NOT NULL,
  generation            integer NOT NULL CHECK (generation >= 1),
  activation_key        text NOT NULL,
  user_id               text NOT NULL,
  agentee_id             text NOT NULL,
  adapter_kind          text NOT NULL,
  adapter_config_ref    text NOT NULL,
  persona_artifact_ref  text NOT NULL,
  resource_budget       jsonb NOT NULL DEFAULT '{}'::jsonb,
  idle_ttl_seconds      integer NOT NULL DEFAULT 600 CHECK (idle_ttl_seconds > 0),
  allowed_capabilities  jsonb NOT NULL DEFAULT '[]'::jsonb,
  activation_intent_digest text NOT NULL CHECK (length(activation_intent_digest) = 64),
  contract_version      text NOT NULL CHECK (contract_version = 'starcoplay.runtime.v1'),
  desired_state         text NOT NULL CHECK (desired_state IN ('running', 'stopped')),
  actual_state          text NOT NULL CHECK (actual_state IN (
    'stopped', 'provisioning', 'starting', 'ready_unbound', 'running_bound',
    'idle', 'draining', 'stopping', 'degraded', 'failed'
  )),
  status_version        bigint NOT NULL DEFAULT 1 CHECK (status_version >= 1),
  ready                 boolean NOT NULL DEFAULT false,
  last_effective_activity_at timestamptz,
  started_at            timestamptz,
  exit_reason           text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (runtime_id, generation),
  UNIQUE (user_id, agentee_id, activation_key, generation),
  CHECK (jsonb_typeof(resource_budget) = 'object'),
  CHECK (jsonb_typeof(allowed_capabilities) = 'array')
);

-- Exactly one non-terminal generation may own a logical activation/runtime.
CREATE UNIQUE INDEX uniq_runtime_active_activation
  ON public.runtime_instances (user_id, agentee_id, activation_key)
  WHERE actual_state <> 'stopped';
CREATE UNIQUE INDEX uniq_runtime_active_identity
  ON public.runtime_instances (runtime_id)
  WHERE actual_state <> 'stopped';
CREATE INDEX idx_runtime_activation_history
  ON public.runtime_instances (user_id, agentee_id, activation_key, generation DESC);

CREATE TABLE public.runtime_bindings (
  binding_id          text PRIMARY KEY,
  runtime_id          text NOT NULL,
  generation          integer NOT NULL,
  user_id             text NOT NULL,
  agentee_id           text NOT NULL,
  binding_version     bigint NOT NULL DEFAULT 1 CHECK (binding_version >= 1),
  lease_epoch         bigint NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
  status              text NOT NULL CHECK (status IN ('pending', 'active', 'suspended', 'revoked', 'expired')),
  game_session_id     text,
  game_instance_nonce text,
  capability_digest   text,
  resume_identity     text,
  bound_at            timestamptz,
  expires_at          timestamptz,
  suspended_at        timestamptz,
  revoked_at          timestamptz,
  contract_version    text NOT NULL CHECK (contract_version = 'starcoplay.binding.v1'),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (runtime_id, generation),
  FOREIGN KEY (runtime_id, generation)
    REFERENCES public.runtime_instances(runtime_id, generation) ON DELETE CASCADE,
  CHECK (status <> 'active' OR (
    game_session_id IS NOT NULL AND game_instance_nonce IS NOT NULL
    AND capability_digest IS NOT NULL AND resume_identity IS NOT NULL
    AND lease_epoch >= 1
  )),
  CHECK (status <> 'revoked' OR revoked_at IS NOT NULL),
  CHECK (status <> 'suspended' OR suspended_at IS NOT NULL)
);

CREATE TABLE public.runtime_operations (
  owner_ref       text NOT NULL,
  operation_id    text NOT NULL,
  fingerprint     text NOT NULL,
  result_kind     text NOT NULL CHECK (result_kind IN ('runtime', 'binding')),
  result_payload  jsonb NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_ref, operation_id),
  CHECK (length(fingerprint) = 64),
  CHECK (jsonb_typeof(result_payload) = 'object')
);

CREATE TABLE public.runtime_authority_outbox (
  intent_id                 text PRIMARY KEY,
  intent_kind               text NOT NULL CHECK (intent_kind IN ('issue', 'revoke')),
  binding_id                text NOT NULL REFERENCES public.runtime_bindings(binding_id) ON DELETE CASCADE,
  binding_version           bigint NOT NULL CHECK (binding_version >= 1),
  lease_epoch               bigint NOT NULL CHECK (lease_epoch >= 0),
  payload                   jsonb NOT NULL,
  attempt_count             integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  available_at              timestamptz NOT NULL DEFAULT now(),
  claimed_at                timestamptz,
  processed_at              timestamptz,
  last_error                text,
  created_at                timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_typeof(payload) = 'object')
);
CREATE INDEX idx_runtime_authority_outbox_pending
  ON public.runtime_authority_outbox (available_at, created_at)
  WHERE processed_at IS NULL;

CREATE TABLE public.gateway_session_projection (
  game_session_id      text PRIMARY KEY,
  game_instance_nonce text NOT NULL,
  owner_user_id        text NOT NULL,
  connection_id        text,
  session_version      bigint NOT NULL CHECK (session_version >= 1),
  capability_digest    text NOT NULL,
  resume_identity      text,
  available            boolean NOT NULL,
  resumable            boolean NOT NULL,
  updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.gateway_event_dedupe (
  message_id       text PRIMARY KEY,
  game_session_id  text NOT NULL,
  aggregate_version bigint NOT NULL CHECK (aggregate_version >= 1),
  payload_digest    text NOT NULL CHECK (length(payload_digest) = 64),
  consumed_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_gateway_event_dedupe_session
  ON public.gateway_event_dedupe (game_session_id, aggregate_version DESC);

-- These tables are service-role control-plane state, never browser-facing.
ALTER TABLE public.runtime_instances ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.runtime_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.runtime_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.runtime_authority_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gateway_session_projection ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gateway_event_dedupe ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.runtime_instances IS 'Runtime Manager-owned product runtime generations; independent from legacy agent_instances';
COMMENT ON TABLE public.runtime_bindings IS 'Versioned and fenced Runtime-to-GameSession binding snapshots';
COMMENT ON TABLE public.runtime_authority_outbox IS 'Retryable authority issue/revoke intents; contains no credential material';
