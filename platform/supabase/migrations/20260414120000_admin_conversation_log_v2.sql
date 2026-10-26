DROP FUNCTION IF EXISTS public.admin_conversation_log(text, int);

CREATE OR REPLACE FUNCTION public.admin_conversation_log(
  target_user_id text,
  page_size int DEFAULT 50,
  page_offset int DEFAULT 0
)
RETURNS TABLE(ts timestamptz, type text, session_id text, data jsonb, total bigint)
LANGUAGE sql STABLE
AS $$
  WITH base AS (
    SELECT e.ts, e.type, e.session_id, e.data
    FROM public.events e
    WHERE e.user_id = target_user_id
      AND e.type IN (
        'player.message',
        'agent.say',
        'ai.dialogue',
        'ai.decision'
      )
  ),
  cnt AS (
    SELECT COUNT(*)::bigint AS total FROM base
  )
  SELECT b.ts, b.type, b.session_id, b.data, cnt.total
  FROM base b
  CROSS JOIN cnt
  ORDER BY b.ts DESC
  LIMIT GREATEST(page_size, 1)
  OFFSET GREATEST(page_offset, 0);
$$;
