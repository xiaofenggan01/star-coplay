-- Admin views should reflect the latest editable nickname from public.profiles.

CREATE OR REPLACE FUNCTION public.admin_active_users(days int DEFAULT 30)
RETURNS TABLE(
  user_id text,
  nickname text,
  total_sessions bigint,
  total_duration_min numeric,
  total_messages bigint,
  last_active timestamptz
)
LANGUAGE sql STABLE
AS $$
  WITH user_sessions AS (
    SELECT
      e.user_id,
      COUNT(DISTINCT e.session_id) FILTER (WHERE e.type = 'session.heartbeat') AS hb_sessions,
      COUNT(*) FILTER (WHERE e.type = 'session.heartbeat') AS hb_count,
      COUNT(*) FILTER (WHERE e.type = 'player.message') AS msg_count,
      MAX(e.ts) AS last_active
    FROM public.events e
    WHERE e.ts >= now() - make_interval(days => days)
    GROUP BY e.user_id
  )
  SELECT
    us.user_id,
    COALESCE(NULLIF(BTRIM(p.nickname), ''), u.nickname) AS nickname,
    us.hb_sessions AS total_sessions,
    ROUND((us.hb_count * 5)::numeric, 0) AS total_duration_min,
    us.msg_count AS total_messages,
    us.last_active
  FROM user_sessions us
  LEFT JOIN public.users u ON us.user_id = u.user_id
  LEFT JOIN public.profiles p ON us.user_id = p.user_id
  ORDER BY us.hb_count DESC;
$$;

CREATE OR REPLACE FUNCTION public.admin_user_profile(target_user_id text)
RETURNS TABLE(
  user_id text,
  nickname text,
  source text,
  created_at timestamptz,
  total_sessions bigint,
  total_duration_min numeric,
  total_messages bigint,
  total_llm_calls bigint,
  total_actions bigint,
  total_errors bigint,
  first_session timestamptz,
  last_session timestamptz
)
LANGUAGE sql STABLE
AS $$
  SELECT
    u.user_id,
    COALESCE(NULLIF(BTRIM(p.nickname), ''), u.nickname) AS nickname,
    u.source,
    u.created_at,
    COUNT(DISTINCT e.session_id) FILTER (WHERE e.type = 'session.start') AS total_sessions,
    ROUND((COUNT(*) FILTER (WHERE e.type = 'session.heartbeat') * 5)::numeric, 0) AS total_duration_min,
    COUNT(*) FILTER (WHERE e.type = 'player.message') AS total_messages,
    COUNT(*) FILTER (WHERE e.type = 'ai.llm_call') AS total_llm_calls,
    COUNT(*) FILTER (WHERE e.type = 'ai.action') AS total_actions,
    COUNT(*) FILTER (WHERE e.type LIKE 'error.%' OR e.type = 'ai.error') AS total_errors,
    MIN(e.ts) FILTER (WHERE e.type = 'session.start') AS first_session,
    MAX(e.ts) FILTER (WHERE e.type = 'session.start') AS last_session
  FROM public.users u
  LEFT JOIN public.profiles p ON u.user_id = p.user_id
  LEFT JOIN public.events e ON u.user_id = e.user_id
  WHERE u.user_id = target_user_id
  GROUP BY u.user_id, p.nickname, u.nickname, u.source, u.created_at;
$$;
