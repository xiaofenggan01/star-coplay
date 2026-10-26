-- Local / staging seed
-- 仅用于本地与 staging 环境的开发与测试，不进入生产数据
-- 使用 ON CONFLICT DO NOTHING 保证可重复 reset 幂等

-- ===== 测试邀请码 =====
INSERT INTO public.invite_codes (code, type, max_uses, used_count, official_code_reason, expires_at, disabled)
VALUES
  ('test-code-once',     'official', 1,  0, '一次性测试码', NULL,                       false),
  ('test-code-multi',    'official', 3,  0, '多次使用测试码（max=3）', NULL,            false),
  ('test-code-disabled', 'official', 1,  0, '已禁用测试码', NULL,                       true),
  ('test-code-expired',  'official', 1,  0, '已过期测试码', '2020-01-01T00:00:00Z',     false),
  ('test-code-pool',     'official', 10, 0, '池化测试码（max=10）', NULL,               false)
ON CONFLICT (code) DO NOTHING;

-- ===== 测试 waitlist =====
INSERT INTO public.waitlist (email, wechat, message, status, created_at)
VALUES
  ('pending@test.com',  'wx_pending',  '测试 pending 状态',  'pending',  now()),
  ('approved@test.com', 'wx_approved', '测试 approved 状态', 'approved', now()),
  ('rejected@test.com', 'wx_rejected', '测试 rejected 状态', 'rejected', now())
ON CONFLICT DO NOTHING;
