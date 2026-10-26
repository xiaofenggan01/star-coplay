-- M1: 新建 public.invite_codes 表
-- 替代 INVITE_CODES 环境变量；支持官方码（一码多用）与用户邀请码（一码一用）
-- 使用次数原子管控（CAS UPDATE），运营/客服可吊销

CREATE TABLE IF NOT EXISTS public.invite_codes (
  code                 text PRIMARY KEY,
  type                 text NOT NULL CHECK (type IN ('official', 'user')),
  owner_user_id        text REFERENCES public.users(user_id),
  max_uses             int  NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
  used_count           int  NOT NULL DEFAULT 0 CHECK (used_count >= 0),
  official_code_reason text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  expires_at           timestamptz,
  disabled             boolean NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_invite_codes_owner ON public.invite_codes (owner_user_id);
CREATE INDEX IF NOT EXISTS idx_invite_codes_type  ON public.invite_codes (type);

-- 部署后需手动 INSERT 当前 INVITE_CODES 环境变量中的官方码到本表
-- 示例：INSERT INTO public.invite_codes (code, type, max_uses, official_code_reason)
--       VALUES ('LEGACY-OFFICIAL-CODE', 'official', 9999, '从 INVITE_CODES env 迁移');

COMMENT ON TABLE  public.invite_codes IS '邀请码管理表，替代 INVITE_CODES 环境变量';
COMMENT ON COLUMN public.invite_codes.type IS 'official=官方运营码（一码多用）/ user=用户邀请码（默认一码一用）';
COMMENT ON COLUMN public.invite_codes.max_uses IS '允许使用次数上限，user 类型默认 1';
COMMENT ON COLUMN public.invite_codes.used_count IS '已使用次数，原子递增；signUp 失败需 -1 补偿';
COMMENT ON COLUMN public.invite_codes.disabled IS '运营吊销开关，true 则注册接口拒绝';
