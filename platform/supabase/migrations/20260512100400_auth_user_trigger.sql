-- M5: auth.users INSERT 触发器，自动创建 public.users 记录
-- 依赖：M2 已添加 public.users.auth_id 字段
-- 异常策略：任何失败都 RAISE 阻塞 signUp（tech-spec 决策：不降级，确保 auth 与 public 一致性）
--
-- ⚠️ 安全前提：本 trigger 信任 raw_user_meta_data 中的 invite_code，不做校验
-- 因此必须由应用层保证：
--   1. Supabase Dashboard 关闭 "Allow new users to sign up"（Auth → Providers → Email）
--   2. 注册流程统一走 /api/register（服务端 service role + auth.admin.createUser）
--   3. /api/register 在 admin.createUser 之前已执行 consumeInviteCode CAS 校验
-- 否则任何持 anon key 的客户端可直接调 /auth/v1/signup 绕过邀请码，
-- trigger 会按 metadata 写入 invite_code_used='任意串' 创建有效用户。

CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_user_id        text;
  v_platform_token text;
  v_invite_code    text;
  v_nickname       text;
BEGIN
  -- 从 raw_user_meta_data 中读取注册时附带的元数据
  v_invite_code := NEW.raw_user_meta_data->>'invite_code';
  -- 注意：gen_random_bytes 在 Supabase 的 extensions schema，trigger SET search_path=public 后不可见
  -- 改用 gen_random_uuid() 派生随机后缀（始终可用，且天然唯一性足够）
  v_nickname    := COALESCE(
    NULLIF(TRIM(NEW.raw_user_meta_data->>'nickname'), ''),
    'user_' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 8)
  );

  -- platform_token 兜底生成（保留老接口的鉴权能力，新用户也可获得 token）
  v_user_id        := gen_random_uuid()::text;
  v_platform_token := gen_random_uuid()::text;

  INSERT INTO public.users (
    user_id,
    platform_token,
    auth_id,
    nickname,
    email,
    invite_code_used,
    created_at
  )
  VALUES (
    v_user_id,
    v_platform_token,
    NEW.id,
    v_nickname,
    NEW.email,
    COALESCE(v_invite_code, ''),
    now()
  );

  RETURN NEW;

EXCEPTION WHEN OTHERS THEN
  -- 关键决策：异常 RAISE 阻塞 signUp，避免出现 auth.users 有记录但 public.users 缺失的不一致
  RAISE WARNING 'handle_new_auth_user failed for auth_id=%: %', NEW.id, SQLERRM;
  RAISE;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_auth_user();

COMMENT ON FUNCTION public.handle_new_auth_user() IS
  'Supabase Auth 注册后自动创建 public.users 记录，异常 RAISE 阻塞 signUp';
