CREATE TABLE IF NOT EXISTS public.verification_tokens (
  token        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email        TEXT NOT NULL,
  type         TEXT NOT NULL CHECK (type IN ('waitlist_invite')),
  waitlist_id  INTEGER REFERENCES public.waitlist(id) ON DELETE SET NULL,
  expires_at   TIMESTAMPTZ NOT NULL,
  used_at      TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_verification_tokens_email
  ON public.verification_tokens (email, used_at);
