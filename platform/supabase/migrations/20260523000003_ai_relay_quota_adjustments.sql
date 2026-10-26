CREATE TABLE IF NOT EXISTS public.ai_relay_quota_adjustments (
  idempotency_key text PRIMARY KEY,
  user_id          text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  newapi_user_id   integer,
  mode             text NOT NULL,
  quota            integer NOT NULL,
  reason           text NOT NULL DEFAULT '',
  biz_ref_type     text,
  biz_ref_id       text,
  status           text NOT NULL DEFAULT 'pending',
  newapi_response  jsonb NOT NULL DEFAULT '{}'::jsonb,
  quota_summary    jsonb NOT NULL DEFAULT '{}'::jsonb,
  error            text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT ai_relay_quota_adjustments_mode_check
    CHECK (mode IN ('add', 'subtract', 'override')),
  CONSTRAINT ai_relay_quota_adjustments_quota_check
    CHECK (quota >= 0),
  CONSTRAINT ai_relay_quota_adjustments_status_check
    CHECK (status IN ('pending', 'succeeded', 'failed')),
  CONSTRAINT ai_relay_quota_adjustments_idem_not_blank
    CHECK (length(trim(idempotency_key)) > 0 AND length(idempotency_key) <= 128)
);

CREATE INDEX IF NOT EXISTS idx_ai_relay_quota_adjustments_user_created
  ON public.ai_relay_quota_adjustments (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_relay_quota_adjustments_biz_ref
  ON public.ai_relay_quota_adjustments (biz_ref_type, biz_ref_id)
  WHERE biz_ref_type IS NOT NULL AND biz_ref_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ai_relay_quota_adjustments_status
  ON public.ai_relay_quota_adjustments (status, updated_at DESC);

CREATE OR REPLACE FUNCTION public.tg_ai_relay_quota_adjustments_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_ai_relay_quota_adjustments_updated_at
  ON public.ai_relay_quota_adjustments;
CREATE TRIGGER trg_ai_relay_quota_adjustments_updated_at
  BEFORE UPDATE ON public.ai_relay_quota_adjustments
  FOR EACH ROW EXECUTE FUNCTION public.tg_ai_relay_quota_adjustments_set_updated_at();

ALTER TABLE public.ai_relay_quota_adjustments ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.ai_relay_quota_adjustments IS 'Idempotency and audit records for platform-triggered NewAPI quota adjustments; NewAPI logs remain the quota ledger source of truth.';
