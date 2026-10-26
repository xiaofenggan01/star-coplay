CREATE TABLE IF NOT EXISTS public.hosted_control_selections (
    owner_user_id text PRIMARY KEY REFERENCES public.users(user_id) ON DELETE CASCADE,
    revision bigint NOT NULL CHECK (revision >= 0),
    enabled boolean NOT NULL,
    target_player_id text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT hosted_control_selection_target_check CHECK (
        (enabled AND target_player_id IS NOT NULL AND target_player_id ~ '^[0-9]+$')
        OR (NOT enabled AND target_player_id IS NULL)
    )
);

ALTER TABLE public.hosted_control_selections ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.hosted_control_selections IS
    'Gateway-owned durable desired-control snapshot; not Runtime or MCP authority.';
