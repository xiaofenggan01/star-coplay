CREATE TABLE IF NOT EXISTS public.ai_relay_accounts (
  user_id           text PRIMARY KEY REFERENCES public.users(user_id) ON DELETE CASCADE,
  newapi_user_id    integer NOT NULL,
  newapi_token_id   integer,
  newapi_token_key  text,
  "group"           text NOT NULL DEFAULT 'default',
  preferred_models  jsonb NOT NULL DEFAULT '{}'::jsonb,
  credential_mode   text NOT NULL DEFAULT 'newapi_bound_token',
  status            text NOT NULL DEFAULT 'active',
  last_sync_error   text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT ai_relay_accounts_group_not_blank
    CHECK (length(trim("group")) > 0 AND length("group") <= 64),
  CONSTRAINT ai_relay_accounts_credential_mode_check
    CHECK (credential_mode IN ('newapi_bound_token')),
  CONSTRAINT ai_relay_accounts_status_check
    CHECK (status IN ('active', 'disabled', 'syncing', 'sync_failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_relay_accounts_newapi_user_id
  ON public.ai_relay_accounts (newapi_user_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_relay_accounts_newapi_token_id
  ON public.ai_relay_accounts (newapi_token_id)
  WHERE newapi_token_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ai_relay_accounts_status
  ON public.ai_relay_accounts (status, updated_at DESC);

CREATE OR REPLACE FUNCTION public.tg_ai_relay_accounts_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_ai_relay_accounts_updated_at ON public.ai_relay_accounts;
CREATE TRIGGER trg_ai_relay_accounts_updated_at
  BEFORE UPDATE ON public.ai_relay_accounts
  FOR EACH ROW EXECUTE FUNCTION public.tg_ai_relay_accounts_set_updated_at();

ALTER TABLE public.ai_relay_accounts ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.ai_relay_accounts IS 'Platform user to NewAPI relay user/token binding. NewAPI remains the source of truth for quota and usage logs.';
COMMENT ON COLUMN public.ai_relay_accounts.newapi_token_key IS 'Runtime-only NewAPI token key for backend LLM Router; never returned to users.';
