UPDATE public.system_configs
SET config_value = '{"balanced":["gpt-4o-mini"]}',
    updated_at = now()
WHERE domain = 'ai_relay'
  AND config_key = 'model_tier_mapping'
  AND environment = 'dev'
  AND config_value = '{}';
