-- Align admin dashboard metrics with the event types that are still persisted.
create or replace function public.admin_dau(days int default 30)
returns table(day date, dau bigint)
language sql stable
as $$
  select e.ts::date, count(distinct e.user_id)
  from public.events e
  where e.ts >= now() - make_interval(days => greatest(days, 1))
    and e.user_id is not null
    and e.type in ('session.start', 'session.heartbeat', 'session.end', 'player.message',
      'user_report', 'external_text.input_merged', 'external_text.agent_visible_output')
  group by 1 order by 1;
$$;

create or replace function public.admin_avg_session_duration(days int default 30)
returns table(day date, avg_duration_min numeric, total_sessions bigint)
language sql stable
as $$
  with session_activity as (
    select e.session_id, min(e.ts)::date as day, min(e.ts) as first_seen, max(e.ts) as last_seen,
      count(*) filter (where e.type = 'session.heartbeat') as heartbeat_count
    from public.events e
    where e.ts >= now() - make_interval(days => greatest(days, 1))
      and e.session_id is not null
      and e.type in ('session.start', 'session.heartbeat', 'session.end', 'player.message')
    group by e.session_id
  )
  select day,
    round(avg(greatest(extract(epoch from (last_seen - first_seen)) / 60.0,
      heartbeat_count * 5.0, 1.0)), 1), count(*)
  from session_activity group by day order by day;
$$;

create or replace function public.admin_retention()
returns table(cohort_date date, cohort_size bigint, d1 numeric, d3 numeric, d7 numeric)
language sql stable
as $$
  with active_days as (
    select distinct e.user_id, e.ts::date as active_day
    from public.events e
    where e.user_id is not null
      and e.type in ('session.start', 'session.heartbeat', 'session.end', 'player.message',
        'user_report', 'external_text.input_merged')
  ), user_first as (
    select user_id, min(active_day) as first_day from active_days group by user_id
  )
  select uf.first_day, count(distinct uf.user_id),
    case
      when current_date <= uf.first_day + 1 then null
      else round(count(distinct case when ad.active_day = uf.first_day + 1 then uf.user_id end)::numeric / nullif(count(distinct uf.user_id), 0) * 100, 1)
    end,
    case
      when current_date <= uf.first_day + 3 then null
      else round(count(distinct case when ad.active_day = uf.first_day + 3 then uf.user_id end)::numeric / nullif(count(distinct uf.user_id), 0) * 100, 1)
    end,
    case
      when current_date <= uf.first_day + 7 then null
      else round(count(distinct case when ad.active_day = uf.first_day + 7 then uf.user_id end)::numeric / nullif(count(distinct uf.user_id), 0) * 100, 1)
    end
  from user_first uf left join active_days ad on ad.user_id = uf.user_id
  group by uf.first_day order by uf.first_day desc limit 30;
$$;
