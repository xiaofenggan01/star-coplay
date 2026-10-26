-- M3: 新建 public.profiles 表 + RLS
-- 与 public.users 1:1，存放可由用户自己修改的扩展字段（昵称/头像/偏好/配置）
-- 写入路径：后端 service role；读取路径：登录用户读自己的 + service role 读全部

CREATE TABLE IF NOT EXISTS public.profiles (
  user_id              text PRIMARY KEY REFERENCES public.users(user_id) ON DELETE CASCADE,
  nickname             text,
  avatar_url           text,
  preferences          jsonb NOT NULL DEFAULT '{}'::jsonb,
  config               jsonb NOT NULL DEFAULT '{}'::jsonb,
  referrer_user_id     text REFERENCES public.users(user_id),
  official_invite_note text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_profiles_referrer ON public.profiles (referrer_user_id);

-- updated_at 自动维护
CREATE OR REPLACE FUNCTION public.tg_profiles_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_profiles_updated_at ON public.profiles;
CREATE TRIGGER trg_profiles_updated_at
  BEFORE UPDATE ON public.profiles
  FOR EACH ROW EXECUTE FUNCTION public.tg_profiles_set_updated_at();

-- RLS
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- SELECT：已登录用户可读自己的 profile（按 auth_id 反查 user_id）
DROP POLICY IF EXISTS "profiles_select_own" ON public.profiles;
CREATE POLICY "profiles_select_own" ON public.profiles
  FOR SELECT
  USING (
    user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = auth.uid() LIMIT 1)
  );

-- INSERT/UPDATE/DELETE：不开放给前端，全走 service role（默认 RLS 拒绝）
-- 不创建对应 policy 即可

COMMENT ON TABLE  public.profiles IS '用户画像表，与 public.users 1:1';
COMMENT ON COLUMN public.profiles.preferences IS '用户偏好（语言/通知/主题等），用户可改';
COMMENT ON COLUMN public.profiles.config       IS '业务配置（agent 配置等），由后端写入';
COMMENT ON COLUMN public.profiles.referrer_user_id     IS '推荐人 user_id（来自邀请码 owner_user_id），仅 service role 写';
COMMENT ON COLUMN public.profiles.official_invite_note IS '官方邀请备注（type=official 时记录原因），仅 service role 写';
