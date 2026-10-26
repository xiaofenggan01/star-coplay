alter table public.users add column if not exists email text;

create or replace function public.admin_event_stats()
returns table(event_type text, cnt bigint)
language sql stable
as $$
  select type, count(*)::bigint
  from public.events
  where received_at >= now() - interval '7 days'
  group by type
  order by 2 desc;
$$;
