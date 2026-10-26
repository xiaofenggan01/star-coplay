-- M2: public.users 增加 auth_id 字段桥接 Supabase Auth
-- 老用户 auth_id 初始为 null，由后续补绑邮箱流程逐步回填
-- 跨 schema FK（public.users.auth_id → auth.users.id）：实测可行（见 runbook Step 4），启用

ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS auth_id uuid UNIQUE;

-- 跨 schema FK：auth.users 删除时把 public.users.auth_id 置 null，保留业务侧记录
-- 实测：Supabase 本地 CLI 环境下 postgres superuser 可直接创建该约束
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_users_auth_id'
  ) THEN
    ALTER TABLE public.users
      ADD CONSTRAINT fk_users_auth_id
      FOREIGN KEY (auth_id) REFERENCES auth.users(id) ON DELETE SET NULL;
  END IF;
END $$;

-- 注意：CREATE INDEX CONCURRENTLY 不能在 transaction block 中执行
-- supabase migration 默认在事务中跑，因此此处使用普通 CREATE INDEX
-- 表当前体量很小（内测期），加锁影响可忽略；上线大表时 dba 手工以 CONCURRENTLY 重建
CREATE INDEX IF NOT EXISTS idx_users_auth_id ON public.users (auth_id);

COMMENT ON COLUMN public.users.auth_id IS 'Supabase Auth users.id 外链；老用户为 null，新用户由 trigger 回填';
COMMENT ON CONSTRAINT fk_users_auth_id ON public.users IS '跨 schema FK，auth.users 删除时置 null';
