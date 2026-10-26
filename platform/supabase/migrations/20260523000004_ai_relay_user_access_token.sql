ALTER TABLE public.ai_relay_accounts
  ADD COLUMN IF NOT EXISTS newapi_user_access_token text;

COMMENT ON COLUMN public.ai_relay_accounts.newapi_user_access_token IS 'NewAPI user self-service access token used by backend to manage that user token records; never returned to users.';
