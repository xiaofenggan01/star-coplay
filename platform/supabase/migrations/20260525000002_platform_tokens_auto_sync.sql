-- Keep public.platform_tokens in sync for users created after the PAT table migration.
-- New users still receive public.users.platform_token from handle_new_auth_user();
-- this trigger mirrors that token into platform_tokens so WS/game auth can use
-- the same token lookup path as old backfilled users.

CREATE OR REPLACE FUNCTION public.tg_users_mirror_platform_token()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF NEW.platform_token IS NOT NULL AND NEW.platform_token <> '' THEN
    INSERT INTO public.platform_tokens (user_id, token, name, created_at)
    VALUES (
      NEW.user_id,
      NEW.platform_token,
      COALESCE(NULLIF(TRIM(NEW.nickname), ''), '默认令牌'),
      COALESCE(NEW.created_at, now())
    )
    ON CONFLICT (token) DO UPDATE SET
      user_id = EXCLUDED.user_id,
      name = EXCLUDED.name;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_users_mirror_platform_token ON public.users;
CREATE TRIGGER trg_users_mirror_platform_token
  AFTER INSERT ON public.users
  FOR EACH ROW EXECUTE FUNCTION public.tg_users_mirror_platform_token();

COMMENT ON FUNCTION public.tg_users_mirror_platform_token() IS
  'Mirrors public.users.platform_token into public.platform_tokens for newly created users.';
