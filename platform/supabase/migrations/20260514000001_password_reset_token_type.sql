ALTER TABLE public.verification_tokens
  DROP CONSTRAINT IF EXISTS verification_tokens_type_check;
ALTER TABLE public.verification_tokens
  ADD CONSTRAINT verification_tokens_type_check
  CHECK (type IN ('waitlist_invite', 'password_reset'));
