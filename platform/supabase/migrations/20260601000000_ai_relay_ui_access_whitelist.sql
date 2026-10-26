INSERT INTO public.system_configs (
  domain, config_key, environment, status, value_type, config_value, description
) VALUES
  ('ai_relay', 'ui_access_whitelist', 'dev', 1, 5, '{"enabled":true,"allow_all":true,"emails":[],"user_ids":[]}', 'AI Relay LLM UI page/API rollout whitelist. Set allow_all=false and fill emails/user_ids for gray release.'),
  ('ai_relay', 'ui_access_whitelist', 'test', 1, 5, '{"enabled":true,"allow_all":false,"emails":["2024057531@qq.com"],"user_ids":[]}', 'AI Relay LLM UI page/API rollout whitelist. Set allow_all=false and fill emails/user_ids for gray release.'),
  ('ai_relay', 'ui_access_whitelist', 'prod', 1, 5, '{"enabled":true,"allow_all":false,"emails":["2024057531@qq.com"],"user_ids":[]}', 'AI Relay LLM UI page/API rollout whitelist. Set allow_all=false and fill emails/user_ids for gray release.')
ON CONFLICT (domain, config_key, environment) DO NOTHING;
