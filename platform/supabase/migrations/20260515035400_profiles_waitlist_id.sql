-- 为 profiles 表新增 waitlist_id 字段，关联 waitlist 表
-- 用途：记录用户来自哪条 waitlist 申请

ALTER TABLE public.profiles
  ADD COLUMN waitlist_id bigint REFERENCES public.waitlist(id) ON DELETE SET NULL;

CREATE INDEX idx_profiles_waitlist_id ON public.profiles (waitlist_id);
