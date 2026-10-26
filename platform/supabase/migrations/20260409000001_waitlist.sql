-- Waitlist 表：收集内测申请
CREATE TABLE IF NOT EXISTS public.waitlist (
  id          BIGSERIAL PRIMARY KEY,
  wechat      TEXT NOT NULL,
  message     TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  status      TEXT NOT NULL DEFAULT 'pending'  -- pending / contacted / invited
);

CREATE INDEX IF NOT EXISTS idx_waitlist_status ON public.waitlist (status);
CREATE INDEX IF NOT EXISTS idx_waitlist_created ON public.waitlist (created_at);
