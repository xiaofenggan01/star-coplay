-- Backfill platform_tokens from public.users.platform_token for local and staged environments.
-- Idempotent: safe to run multiple times.

INSERT INTO public.platform_tokens (user_id, token, name, created_at)
SELECT
  u.user_id,
  u.platform_token,
  COALESCE(
    NULLIF(TRIM(u.nickname), ''),
    '默认令牌'
  ) AS name,
  u.created_at
FROM public.users u
WHERE u.platform_token IS NOT NULL
  AND u.platform_token <> ''
ON CONFLICT (token) DO NOTHING;
