INSERT INTO public.system_configs (
  domain, config_key, environment, status, value_type, config_value, description
)
VALUES
  ('ai_relay', 'initial_user_quota_usd', 'dev', 1, 3, '100', 'Default NewAPI display quota in USD granted when a relay user is first created'),
  ('ai_relay', 'initial_user_quota_usd', 'test', 1, 3, '100', 'Default NewAPI display quota in USD granted when a relay user is first created'),
  ('ai_relay', 'initial_user_quota_usd', 'prod', 1, 3, '100', 'Default NewAPI display quota in USD granted when a relay user is first created')
ON CONFLICT (domain, config_key, environment) DO NOTHING;
