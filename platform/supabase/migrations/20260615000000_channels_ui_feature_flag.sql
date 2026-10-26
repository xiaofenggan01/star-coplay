INSERT INTO public.system_configs (
  domain, config_key, environment, status, value_type, config_value, description
) VALUES
  ('channels', 'ui_enabled', 'dev', 1, 1, 'false', 'Controls whether the console chat/contact channels UI is visible. Keep false until the feature is implemented.'),
  ('channels', 'ui_enabled', 'test', 1, 1, 'false', 'Controls whether the console chat/contact channels UI is visible. Keep false until the feature is implemented.'),
  ('channels', 'ui_enabled', 'prod', 1, 1, 'false', 'Controls whether the console chat/contact channels UI is visible. Keep false until the feature is implemented.')
ON CONFLICT (domain, config_key, environment) DO NOTHING;
