-- M4: public.waitlist status 规范化
-- Step 1 实测线上 463 条全部为 pending，无 contacted/invited 历史值
-- 历史值清洗 UPDATE 在生产是 no-op，但保留作为防御性写法（幂等、不影响）

-- 历史值清洗（防御性；本地 / staging 无影响）
UPDATE public.waitlist SET status = 'approved' WHERE status IN ('invited', 'contacted');

-- 加 CHECK 约束，确保后续仅出现这 5 个枚举值
ALTER TABLE public.waitlist
  DROP CONSTRAINT IF EXISTS chk_waitlist_status;

ALTER TABLE public.waitlist
  ADD CONSTRAINT chk_waitlist_status
  CHECK (status IN ('unknown', 'pending', 'approved', 'rejected', 'registered'));

COMMENT ON COLUMN public.waitlist.status IS
  '审核状态：unknown(脏数据兜底) / pending(待审) / approved(已通过待注册) / rejected(已拒绝) / registered(已完成注册)';
