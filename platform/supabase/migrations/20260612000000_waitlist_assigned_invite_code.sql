ALTER TABLE public.waitlist
  ADD COLUMN IF NOT EXISTS assigned_invite_code text REFERENCES public.invite_codes(code) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_waitlist_assigned_invite_code
  ON public.waitlist (assigned_invite_code)
  WHERE assigned_invite_code IS NOT NULL;

COMMENT ON COLUMN public.waitlist.assigned_invite_code IS
  'Optional invite code assigned by admins for approved waitlist activation.';
