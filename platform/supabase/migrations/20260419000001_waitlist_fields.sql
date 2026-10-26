-- Add email and qq fields to waitlist table
ALTER TABLE public.waitlist ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE public.waitlist ADD COLUMN IF NOT EXISTS qq TEXT;
-- Make wechat optional (drop NOT NULL constraint if present)
ALTER TABLE public.waitlist ALTER COLUMN wechat DROP NOT NULL;
