-- Dashboard V2: 新增聚合函数（LLM 统计、动作分布、错误排行、活跃用户、用户档案、对话记录）

-- LLM 调用统计（调用量/延迟/token/模型分布）
CREATE OR REPLACE FUNCTION public.admin_llm_stats(days int DEFAULT 30)
RETURNS TABLE(
  total_calls bigint,
  avg_latency_ms numeric,
  p95_latency_ms numeric,
  total_tokens_in bigint,
  total_tokens_out bigint,
  model_distribution jsonb
)
LANGUAGE sql STABLE
AS $$
  WITH raw AS (
    SELECT
      (data->>'latency_ms')::numeric AS latency,
      (data->>'tokens_in')::bigint AS tin,
      (data->>'tokens_out')::bigint AS tout,
      data->>'model' AS model
    FROM public.events
    WHERE type = 'ai.llm_call'
      AND ts >= now() - make_interval(days => days)
  ),
  stats AS (
    SELECT
      COUNT(*) AS total_calls,
      ROUND(AVG(latency), 0) AS avg_latency_ms,
      ROUND((PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency))::numeric, 0) AS p95_latency_ms,
      COALESCE(SUM(tin), 0) AS total_tokens_in,
      COALESCE(SUM(tout), 0) AS total_tokens_out
    FROM raw
  ),
  models AS (
    SELECT jsonb_object_agg(model, cnt) AS model_distribution
    FROM (SELECT model, COUNT(*) AS cnt FROM raw GROUP BY model ORDER BY cnt DESC) sub
  )
  SELECT s.total_calls, s.avg_latency_ms, s.p95_latency_ms,
         s.total_tokens_in, s.total_tokens_out, COALESCE(m.model_distribution, '{}'::jsonb)
  FROM stats s, models m;
$$;

-- 动作类型分布 + 成功率
CREATE OR REPLACE FUNCTION public.admin_action_stats(days int DEFAULT 30)
RETURNS TABLE(action_type text, total bigint, success_count bigint, success_rate numeric)
LANGUAGE sql STABLE
AS $$
  SELECT
    data->>'action_type' AS action_type,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE (data->>'success')::boolean = true) AS success_count,
    ROUND(
      COUNT(*) FILTER (WHERE (data->>'success')::boolean = true)::numeric
      / NULLIF(COUNT(*), 0) * 100, 1
    ) AS success_rate
  FROM public.events
  WHERE type = 'ai.action'
    AND ts >= now() - make_interval(days => days)
  GROUP BY 1
  ORDER BY total DESC;
$$;

-- 错误 Top N 排行
CREATE OR REPLACE FUNCTION public.admin_error_ranking(days int DEFAULT 30, top_n int DEFAULT 10)
RETURNS TABLE(error_type text, error_count bigint, latest_ts timestamptz, sample_msg text)
LANGUAGE sql STABLE
AS $$
  SELECT
    COALESCE(data->>'error_type', type) AS error_type,
    COUNT(*) AS error_count,
    MAX(ts) AS latest_ts,
    (array_agg(data->>'message' ORDER BY ts DESC))[1] AS sample_msg
  FROM public.events
  WHERE (type LIKE 'error.%' OR type = 'ai.error')
    AND ts >= now() - make_interval(days => days)
  GROUP BY 1
  ORDER BY error_count DESC
  LIMIT top_n;
$$;

-- 活跃用户排行（按总时长和对话数）
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
    u.nickname,
    us.hb_sessions AS total_sessions,
    ROUND((us.hb_count * 5)::numeric, 0) AS total_duration_min,
    us.msg_count AS total_messages,
    us.last_active
  FROM user_sessions us
  LEFT JOIN public.users u ON us.user_id = u.user_id
  ORDER BY us.hb_count DESC;
$$;

-- 单用户档案
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
    u.nickname,
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
  LEFT JOIN public.events e ON u.user_id = e.user_id
  WHERE u.user_id = target_user_id
  GROUP BY u.user_id, u.nickname, u.source, u.created_at;
$$;

-- 单用户对话记录
CREATE OR REPLACE FUNCTION public.admin_conversation_log(target_user_id text, max_rows int DEFAULT 200)
RETURNS TABLE(ts timestamptz, type text, session_id text, data jsonb)
LANGUAGE sql STABLE
AS $$
  SELECT ts, type, session_id, data
  FROM public.events
  WHERE user_id = target_user_id
    AND type IN ('player.message', 'agent.say', 'ai.dialogue')
  ORDER BY ts DESC
  LIMIT max_rows;
$$;
