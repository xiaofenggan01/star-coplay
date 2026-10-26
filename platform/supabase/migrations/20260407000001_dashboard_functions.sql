-- Dashboard 聚合函数（内测数据看板）
-- 2026-04-07: 为内测期间提供 DAU、会话时长、留存率、漏斗、用户时间线视图

-- DAU (Daily Active Users)
CREATE OR REPLACE FUNCTION public.admin_dau(days int DEFAULT 30)
RETURNS TABLE(day date, dau bigint)
LANGUAGE sql STABLE
AS $$
  SELECT ts::date AS day, COUNT(DISTINCT user_id) AS dau
  FROM public.events
  WHERE type = 'session.start'
    AND ts >= now() - make_interval(days => days)
  GROUP BY 1
  ORDER BY 1;
$$;

-- 平均会话时长（基于心跳计数 x 5分钟）
CREATE OR REPLACE FUNCTION public.admin_avg_session_duration(days int DEFAULT 30)
RETURNS TABLE(day date, avg_duration_min numeric, total_sessions bigint)
LANGUAGE sql STABLE
AS $$
  WITH session_heartbeats AS (
    SELECT
      session_id,
      user_id,
      MIN(ts)::date AS day,
      COUNT(*) AS heartbeat_count
    FROM public.events
    WHERE type = 'session.heartbeat'
      AND ts >= now() - make_interval(days => days)
    GROUP BY session_id, user_id
  )
  SELECT
    day,
    ROUND(AVG(heartbeat_count * 5.0), 1) AS avg_duration_min,
    COUNT(*) AS total_sessions
  FROM session_heartbeats
  GROUP BY day
  ORDER BY day;
$$;

-- 留存率（D1/D3/D7）
CREATE OR REPLACE FUNCTION public.admin_retention()
RETURNS TABLE(cohort_date date, cohort_size bigint, d1 numeric, d3 numeric, d7 numeric)
LANGUAGE sql STABLE
AS $$
  WITH user_first AS (
    SELECT user_id, MIN(ts)::date AS first_day
    FROM public.events WHERE type = 'session.start'
    GROUP BY user_id
  ),
  user_active_days AS (
    SELECT DISTINCT user_id, ts::date AS active_day
    FROM public.events WHERE type = 'session.start'
  )
  SELECT
    uf.first_day AS cohort_date,
    COUNT(DISTINCT uf.user_id) AS cohort_size,
    ROUND(COUNT(DISTINCT CASE WHEN ua.active_day = uf.first_day + 1 THEN uf.user_id END)::numeric
          / NULLIF(COUNT(DISTINCT uf.user_id), 0) * 100, 1) AS d1,
    ROUND(COUNT(DISTINCT CASE WHEN ua.active_day = uf.first_day + 3 THEN uf.user_id END)::numeric
          / NULLIF(COUNT(DISTINCT uf.user_id), 0) * 100, 1) AS d3,
    ROUND(COUNT(DISTINCT CASE WHEN ua.active_day = uf.first_day + 7 THEN uf.user_id END)::numeric
          / NULLIF(COUNT(DISTINCT uf.user_id), 0) * 100, 1) AS d7
  FROM user_first uf
  LEFT JOIN user_active_days ua ON uf.user_id = ua.user_id
  GROUP BY uf.first_day
  ORDER BY uf.first_day;
$$;

-- 用户级行为时间线（内测期间最有价值的视图）
CREATE OR REPLACE FUNCTION public.admin_user_timeline(target_user_id text DEFAULT NULL)
RETURNS TABLE(user_id text, ts timestamptz, type text, session_id text, data jsonb)
LANGUAGE sql STABLE
AS $$
  SELECT user_id, ts, type, session_id, data
  FROM public.events
  WHERE (target_user_id IS NULL OR user_id = target_user_id)
  ORDER BY ts DESC
  LIMIT 500;
$$;

-- 首次体验漏斗
CREATE OR REPLACE FUNCTION public.admin_onboard_funnel()
RETURNS TABLE(step text, user_count bigint)
LANGUAGE sql STABLE
AS $$
  SELECT
    CASE type
      WHEN 'funnel.registered' THEN '1_registered'
      WHEN 'onboard.agent_connected' THEN '2_connected'
      WHEN 'onboard.first_action' THEN '3_first_action'
      WHEN 'onboard.first_conversation' THEN '4_first_conversation'
      WHEN 'onboard.first_skill_complete' THEN '5_first_skill'
    END AS step,
    COUNT(DISTINCT user_id) AS user_count
  FROM public.events
  WHERE type IN ('funnel.registered', 'onboard.agent_connected', 'onboard.first_action',
                 'onboard.first_conversation', 'onboard.first_skill_complete')
  GROUP BY 1
  ORDER BY 1;
$$;
