-- PAT（Personal Access Token）独立表
-- 从 public.users.platform_token 解耦，支持用户主动生成、后续多 token

CREATE TABLE public.platform_tokens (
  id            text PRIMARY KEY DEFAULT gen_random_uuid()::text,
  user_id       text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  token         text NOT NULL UNIQUE DEFAULT gen_random_uuid()::text,
  name          text NOT NULL DEFAULT '默认令牌',
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_used_at  timestamptz
);

CREATE INDEX idx_platform_tokens_token ON public.platform_tokens (token);
CREATE INDEX idx_platform_tokens_user_id ON public.platform_tokens (user_id);

-- 迁移旧数据：把 public.users.platform_token 搬到新表
INSERT INTO public.platform_tokens (user_id, token, name, created_at)
SELECT user_id, platform_token, '默认令牌', created_at
FROM public.users
WHERE platform_token IS NOT NULL AND platform_token != '';
