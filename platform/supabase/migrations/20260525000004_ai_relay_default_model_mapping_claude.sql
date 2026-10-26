UPDATE public.system_configs
SET config_value = '{"balanced":["claude-sonnet-4-6"]}',
    updated_at = now()
WHERE domain = 'ai_relay'
  AND config_key = 'model_tier_mapping'
  AND environment IN ('dev', 'test', 'prod')
  AND (
    config_value = '{}'
    OR config_value = '{"balanced":["deepseek-v4-flash"]}'
    OR config_value = '{"balanced":["gpt-4o-mini"]}'
  );
