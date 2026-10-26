-- 用户类型体系：区分游客/内测用户/正式用户/会员
ALTER TABLE public.profiles
  ADD COLUMN user_type text NOT NULL DEFAULT 'guest'
  CHECK (user_type IN ('guest', 'beta_user', 'user', 'member'));

-- 存量用户全部为内测用户
UPDATE public.profiles SET user_type = 'beta_user';
