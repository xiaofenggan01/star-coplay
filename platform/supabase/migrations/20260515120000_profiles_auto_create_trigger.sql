-- 兜底 trigger：当 public.users 插入时自动创建 profiles 记录
-- 确保无论哪条路径创建用户，profiles 都不会缺失
-- 语义：ON CONFLICT (user_id) DO NOTHING — 不覆盖已有 profile

CREATE OR REPLACE FUNCTION public.handle_new_user_profile()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.profiles (user_id, user_type)
  VALUES (NEW.user_id, 'guest')
  ON CONFLICT (user_id) DO NOTHING;

  RETURN NEW;
EXCEPTION WHEN OTHERS THEN
  -- 非致命：profile 创建失败不应阻塞用户创建
  RAISE WARNING 'handle_new_user_profile failed for user_id=%: %', NEW.user_id, SQLERRM;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_user_created_ensure_profile ON public.users;
CREATE TRIGGER on_user_created_ensure_profile
  AFTER INSERT ON public.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user_profile();

COMMENT ON FUNCTION public.handle_new_user_profile() IS
  '兜底：public.users 插入后自动创建 profiles 记录（ON CONFLICT DO NOTHING）';
