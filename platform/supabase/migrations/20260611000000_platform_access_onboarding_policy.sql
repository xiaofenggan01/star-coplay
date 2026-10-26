INSERT INTO public.system_configs (
  domain, config_key, environment, status, value_type, config_value, description
) VALUES
  (
    'platform_access',
    'onboarding_policy',
    'dev',
    1,
    5,
    '{"mode":"manual_review","key_issue_policy":"approval_required","key_access_user_types":["beta_user","user","member"]}',
    'Controls onboarding platform key and resource access only.'
  ),
  (
    'platform_access',
    'onboarding_policy',
    'test',
    1,
    5,
    '{"mode":"manual_review","key_issue_policy":"approval_required","key_access_user_types":["beta_user","user","member"]}',
    'Controls onboarding platform key and resource access only.'
  ),
  (
    'platform_access',
    'onboarding_policy',
    'prod',
    1,
    5,
    '{"mode":"manual_review","key_issue_policy":"approval_required","key_access_user_types":["beta_user","user","member"]}',
    'Controls onboarding platform key and resource access only.'
  )
ON CONFLICT (domain, config_key, environment) DO NOTHING;

INSERT INTO public.system_configs (
  domain, config_key, environment, status, value_type, config_value, description
) VALUES
  (
    'invite',
    'policy',
    'dev',
    1,
    5,
    '{"enabled":true,"per_user_limit":3,"invitee_bonus_credits":20,"inviter_bonus_credits":20,"auto_issue_user_invite_code":true,"user_invite_code_format":{"kind":"readable","prefix":"SC","random_length":8}}',
    'Controls user invite limits, reward credits, and automatic invite-code issuing.'
  ),
  (
    'invite',
    'policy',
    'test',
    1,
    5,
    '{"enabled":true,"per_user_limit":3,"invitee_bonus_credits":20,"inviter_bonus_credits":20,"auto_issue_user_invite_code":true,"user_invite_code_format":{"kind":"readable","prefix":"SC","random_length":8}}',
    'Controls user invite limits, reward credits, and automatic invite-code issuing.'
  ),
  (
    'invite',
    'policy',
    'prod',
    1,
    5,
    '{"enabled":true,"per_user_limit":3,"invitee_bonus_credits":20,"inviter_bonus_credits":20,"auto_issue_user_invite_code":true,"user_invite_code_format":{"kind":"readable","prefix":"SC","random_length":8}}',
    'Controls user invite limits, reward credits, and automatic invite-code issuing.'
  )
ON CONFLICT (domain, config_key, environment) DO NOTHING;

ALTER TABLE public.invite_codes
  ALTER COLUMN max_uses DROP NOT NULL,
  ALTER COLUMN max_uses DROP DEFAULT;

UPDATE public.invite_codes
SET max_uses = NULL
WHERE type = 'user';

CREATE UNIQUE INDEX IF NOT EXISTS idx_invite_codes_user_owner_unique
  ON public.invite_codes (owner_user_id)
  WHERE type = 'user' AND owner_user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_invite_codes_available
  ON public.invite_codes (type, disabled, expires_at);

CREATE TABLE IF NOT EXISTS public.user_relations (
  user_id         text PRIMARY KEY REFERENCES public.users(user_id) ON DELETE CASCADE,
  inviter_user_id text REFERENCES public.users(user_id) ON DELETE SET NULL,
  invite_code     text REFERENCES public.invite_codes(code) ON DELETE SET NULL,
  lineage_path    text NOT NULL,
  depth           integer NOT NULL DEFAULT 0 CHECK (depth >= 0),
  created_at      timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT user_relations_not_self
    CHECK (inviter_user_id IS NULL OR inviter_user_id <> user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_relations_inviter
  ON public.user_relations (inviter_user_id);

CREATE INDEX IF NOT EXISTS idx_user_relations_invite_code
  ON public.user_relations (invite_code);

CREATE INDEX IF NOT EXISTS idx_user_relations_lineage_path
  ON public.user_relations (lineage_path);

ALTER TABLE public.user_relations ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.invite_reward_grants (
  id                      bigserial PRIMARY KEY,
  idempotency_key         text NOT NULL UNIQUE,
  invitee_user_id         text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  inviter_user_id         text REFERENCES public.users(user_id) ON DELETE SET NULL,
  invite_code             text REFERENCES public.invite_codes(code) ON DELETE SET NULL,
  reward_target           text NOT NULL CHECK (reward_target IN ('invitee', 'inviter')),
  credits                 numeric(12, 2) NOT NULL CHECK (credits > 0),
  status                  text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'succeeded', 'failed')),
  ai_relay_adjustment_key text,
  error                   text,
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invite_reward_grants_invitee
  ON public.invite_reward_grants (invitee_user_id);

CREATE INDEX IF NOT EXISTS idx_invite_reward_grants_inviter
  ON public.invite_reward_grants (inviter_user_id);

CREATE INDEX IF NOT EXISTS idx_invite_reward_grants_status
  ON public.invite_reward_grants (status, updated_at);

CREATE OR REPLACE FUNCTION public.tg_invite_reward_grants_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_invite_reward_grants_updated_at ON public.invite_reward_grants;
CREATE TRIGGER trg_invite_reward_grants_updated_at
  BEFORE UPDATE ON public.invite_reward_grants
  FOR EACH ROW EXECUTE FUNCTION public.tg_invite_reward_grants_set_updated_at();

ALTER TABLE public.invite_reward_grants ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS idx_waitlist_email_lower
  ON public.waitlist (lower(email))
  WHERE email IS NOT NULL;

WITH eligible_users AS (
  SELECT
    u.user_id,
    ic.code AS matched_invite_code,
    CASE
      WHEN ic.type = 'user'
        AND ic.owner_user_id IS NOT NULL
        AND ic.owner_user_id <> u.user_id
      THEN ic.owner_user_id
      ELSE NULL
    END AS inviter_user_id
  FROM public.users u
  JOIN public.profiles p ON p.user_id = u.user_id
  LEFT JOIN public.invite_codes ic ON ic.code = NULLIF(u.invite_code_used, '')
  WHERE p.user_type IN ('beta_user', 'user', 'member')
)
INSERT INTO public.user_relations (
  user_id,
  inviter_user_id,
  invite_code,
  lineage_path,
  depth
)
SELECT
  user_id,
  inviter_user_id,
  matched_invite_code,
  CASE
    WHEN inviter_user_id IS NULL THEN user_id
    ELSE inviter_user_id || '/' || user_id
  END AS lineage_path,
  CASE
    WHEN inviter_user_id IS NULL THEN 0
    ELSE 1
  END AS depth
FROM eligible_users
ON CONFLICT (user_id) DO NOTHING;

COMMENT ON COLUMN public.invite_codes.max_uses IS
  'Optional intrinsic use cap. NULL means the code itself has no cap; user invite limits are enforced through invite/policy and user_relations.';

COMMENT ON TABLE public.user_relations IS
  'Immutable invite attribution facts. One row per user; does not grant platform resource access.';

COMMENT ON COLUMN public.user_relations.lineage_path IS
  'Immutable slash-separated user_id lineage path. First backfill may only include direct inviter for historical data.';

COMMENT ON TABLE public.invite_reward_grants IS
  'Invite reward ledger. Actual credits are granted through AI Relay quota adjustments with idempotency keys.';

COMMENT ON COLUMN public.invite_reward_grants.ai_relay_adjustment_key IS
  'Idempotency key used when calling AI Relay quota adjustments.';
