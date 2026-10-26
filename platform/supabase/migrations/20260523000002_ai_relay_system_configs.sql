INSERT INTO public.system_configs (
  domain, config_key, environment, status, value_type, config_value, description
)
VALUES
  ('ai_relay', 'default_group', 'dev', 1, 4, 'default', 'Default NewAPI group for AI relay accounts'),
  ('ai_relay', 'default_group', 'test', 1, 4, 'default', 'Default NewAPI group for AI relay accounts'),
  ('ai_relay', 'default_group', 'prod', 1, 4, 'default', 'Default NewAPI group for AI relay accounts'),
  ('ai_relay', 'allowed_groups', 'dev', 1, 5, '["default"]', 'NewAPI groups users may select through platform'),
  ('ai_relay', 'allowed_groups', 'test', 1, 5, '["default"]', 'NewAPI groups users may select through platform'),
  ('ai_relay', 'allowed_groups', 'prod', 1, 5, '["default"]', 'NewAPI groups users may select through platform'),
  ('ai_relay', 'default_preferences', 'dev', 1, 5, '{}', 'Default model preferences merged into relay accounts'),
  ('ai_relay', 'default_preferences', 'test', 1, 5, '{}', 'Default model preferences merged into relay accounts'),
  ('ai_relay', 'default_preferences', 'prod', 1, 5, '{}', 'Default model preferences merged into relay accounts'),
  ('ai_relay', 'model_tier_mapping', 'dev', 1, 5, '{}', 'Allowed model tiers and candidate models for System LLM Router'),
  ('ai_relay', 'model_tier_mapping', 'test', 1, 5, '{}', 'Allowed model tiers and candidate models for System LLM Router'),
  ('ai_relay', 'model_tier_mapping', 'prod', 1, 5, '{}', 'Allowed model tiers and candidate models for System LLM Router')
ON CONFLICT (domain, config_key, environment) DO NOTHING;
