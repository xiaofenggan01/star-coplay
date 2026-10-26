INSERT INTO public.system_configs (
  domain, config_key, environment, status, value_type, config_value, description
) VALUES
  (
    'agent_runtime', 'hosted_rollout', 'dev', 1, 5,
    '{"enabled":true,"mode":"all_authenticated","user_ids":[],"emails":[]}',
    'Controls Hosted Claude Code + MCP rollout. Non-matching users remain on Legacy.'
  ),
  (
    'agent_runtime', 'hosted_rollout', 'test', 1, 5,
    '{"enabled":false,"mode":"allowlist","user_ids":[],"emails":[]}',
    'Controls Hosted Claude Code + MCP rollout. Non-matching users remain on Legacy.'
  ),
  (
    'agent_runtime', 'hosted_rollout', 'prod', 1, 5,
    '{"enabled":false,"mode":"allowlist","user_ids":[],"emails":[]}',
    'Controls Hosted Claude Code + MCP rollout. Non-matching users remain on Legacy.'
  )
ON CONFLICT (domain, config_key, environment) DO NOTHING;
