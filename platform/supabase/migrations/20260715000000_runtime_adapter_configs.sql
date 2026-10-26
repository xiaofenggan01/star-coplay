CREATE TABLE public.runtime_adapter_configs (
  config_id text NOT NULL,
  revision text NOT NULL,
  runtime_id text NOT NULL,
  user_id text NOT NULL,
  agentee_id text NOT NULL,
  provider text NOT NULL,
  model text NOT NULL,
  permission_mode text NOT NULL,
  allowed_tools jsonb NOT NULL DEFAULT '[]'::jsonb,
  provider_settings jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(config_id,revision),
  CHECK(jsonb_typeof(allowed_tools)='array'),
  CHECK(jsonb_typeof(provider_settings)='object')
);
ALTER TABLE public.runtime_instances ADD COLUMN adapter_config_hash text;
ALTER TABLE public.runtime_adapter_configs ENABLE ROW LEVEL SECURITY;

CREATE FUNCTION public.reject_runtime_adapter_config_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'runtime adapter config revisions are immutable; insert a new revision';
END;
$$;
CREATE TRIGGER runtime_adapter_configs_immutable
BEFORE UPDATE OR DELETE ON public.runtime_adapter_configs
FOR EACH ROW EXECUTE FUNCTION public.reject_runtime_adapter_config_mutation();

CREATE TABLE public.runtime_config_roll_leases (
  runtime_id text PRIMARY KEY,
  operation_id text NOT NULL,
  holder_token text NOT NULL,
  fencing_epoch bigint NOT NULL CHECK(fencing_epoch >= 1),
  expires_at timestamptz NOT NULL,
  operation_fingerprint text NOT NULL,
  expected_generation bigint NOT NULL,
  target_generation bigint NOT NULL,
  target_config_ref text NOT NULL,
  target_config_hash text NOT NULL
);
ALTER TABLE public.runtime_config_roll_leases ENABLE ROW LEVEL SECURITY;
CREATE TABLE public.runtime_control_claim_nonces (
  jti text PRIMARY KEY,
  service_id text NOT NULL,
  expires_at timestamptz NOT NULL
);
ALTER TABLE public.runtime_control_claim_nonces ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
    EXECUTE 'CREATE POLICY runtime_config_roll_leases_service_role '
      'ON public.runtime_config_roll_leases FOR ALL TO service_role '
      'USING (true) WITH CHECK (true)';
  END IF;
END $$;
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
    EXECUTE 'CREATE POLICY runtime_control_claim_nonces_service_role '
      'ON public.runtime_control_claim_nonces FOR ALL TO service_role USING (true) WITH CHECK (true)';
  END IF;
END $$;
