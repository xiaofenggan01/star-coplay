CREATE TABLE IF NOT EXISTS public.system_configs (
  id            text PRIMARY KEY DEFAULT gen_random_uuid()::text,
  domain        text NOT NULL,
  config_key    text NOT NULL,
  environment   text NOT NULL,
  status        integer NOT NULL DEFAULT 0,
  value_type    integer NOT NULL DEFAULT 0,
  config_value  text NOT NULL,
  description   text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT system_configs_domain_format
    CHECK (domain ~ '^[a-z][a-z0-9_-]{1,63}$'),
  CONSTRAINT system_configs_config_key_format
    CHECK (config_key ~ '^[a-z][a-z0-9_-]{1,63}$'),
  CONSTRAINT system_configs_environment_check
    CHECK (environment IN ('dev', 'test', 'prod')),
  CONSTRAINT system_configs_status_check
    CHECK (status IN (0, 1)),
  CONSTRAINT system_configs_value_type_check
    CHECK (value_type IN (0, 1, 2, 3, 4, 5)),
  CONSTRAINT system_configs_unique_key
    UNIQUE (domain, config_key, environment)
);

CREATE INDEX IF NOT EXISTS idx_system_configs_lookup
  ON public.system_configs (domain, config_key, environment);

CREATE INDEX IF NOT EXISTS idx_system_configs_list
  ON public.system_configs (environment, domain, status, updated_at DESC);

CREATE OR REPLACE FUNCTION update_system_configs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_system_configs_updated_at ON public.system_configs;
CREATE TRIGGER trg_system_configs_updated_at
  BEFORE UPDATE ON public.system_configs
  FOR EACH ROW
  EXECUTE FUNCTION update_system_configs_updated_at();

ALTER TABLE public.system_configs ENABLE ROW LEVEL SECURITY;
