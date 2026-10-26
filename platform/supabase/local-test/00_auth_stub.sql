-- 本地裸 PostgreSQL 测试环境 — Supabase auth schema 最小桩
--
-- 用途：让 M2 跨 schema FK / M3 RLS / M5 trigger 在不依赖 Supabase 完整栈的本地 PG 上能跑。
-- 仅模拟 Supabase Auth 的最小子集（schema + auth.users 表 + auth.uid() 函数）；
-- 不涉及任何真实鉴权、密码、JWT、refresh_token 等机制。
--
-- 设计原则：
--   1. IF NOT EXISTS / OR REPLACE 全幂等，可重复跑
--   2. 字段名/类型与 Supabase 真实 auth.users 对齐（id / email / raw_user_meta_data / created_at）
--      → 让 M5 trigger 的 NEW.id / NEW.email / NEW.raw_user_meta_data 引用都直接生效
--   3. 留空 auth.uid()（返回 NULL）→ M3 的 RLS policy 在本地默认拒绝读
--      （集成测试用 service role 绕过 RLS，因此不影响）
--
-- 这个文件由 setup-local-pg.ts 在所有 migration 之前先执行。

-- pgcrypto 提供 gen_random_uuid（PG13+ builtin 但 gen_random_bytes 仍需 extension）
-- M5 trigger 内部用 gen_random_uuid 派生 user_id / platform_token / nickname 后缀
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS auth;

-- 最小化 auth.users 表：只保留 trigger 真实读取的字段
CREATE TABLE IF NOT EXISTS auth.users (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email               text UNIQUE,
  raw_user_meta_data  jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at          timestamptz NOT NULL DEFAULT now()
);

-- auth.uid() 在 Supabase 真实环境下返回当前 JWT 中的 sub（uuid）。
-- 本地裸 PG 没有 JWT 上下文，永远返回 NULL：
--   - 集成测试默认走 service role（绕过 RLS），不会触发到这里
--   - 若未来用 anon role 测 RLS，可在测试 setup 阶段
--     CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid AS $$ SELECT '...'::uuid $$ LANGUAGE sql STABLE;
--     临时覆盖
CREATE OR REPLACE FUNCTION auth.uid()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
  SELECT NULL::uuid;
$$;

-- auth.role() 在 Supabase 中返回 JWT 中的 role 字段（authenticated/anon/service_role）
-- 部分 RLS policy 会引用，预留兼容
CREATE OR REPLACE FUNCTION auth.role()
RETURNS text
LANGUAGE sql
STABLE
AS $$
  SELECT 'service_role'::text;
$$;

COMMENT ON SCHEMA auth IS '[local-test stub] 模拟 Supabase auth schema 的最小子集，仅用于本地裸 PG 测试';
COMMENT ON TABLE  auth.users IS '[local-test stub] 仅含 trigger 真实读取的字段（id/email/raw_user_meta_data/created_at）';
COMMENT ON FUNCTION auth.uid() IS '[local-test stub] 永远返回 NULL；测试时按需在 setup 阶段覆盖';
