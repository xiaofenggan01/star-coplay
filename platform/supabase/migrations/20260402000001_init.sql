create table public.users (
  user_id        text primary key default gen_random_uuid()::text,
  platform_token text not null unique default gen_random_uuid()::text,
  nickname       text,
  phone          text,
  wechat         text,
  invite_code_used text not null,
  created_at     timestamptz not null default now(),
  source         text
);

create index idx_users_token on public.users (platform_token);

create table public.events (
  id          bigserial primary key,
  user_id     text not null references public.users(user_id),
  type        text not null,
  ts          timestamptz not null,
  session_id  text not null,
  data        jsonb,
  received_at timestamptz not null default now()
);

create index idx_events_user   on public.events (user_id);
create index idx_events_type   on public.events (type);
create index idx_events_ts     on public.events (ts);
create index idx_events_session on public.events (session_id);

create or replace function public.admin_source_stats()
returns table(source text, count bigint)
language sql stable
as $$
  select coalesce(source, 'unknown'), count(*) from public.users group by 1 order by 2 desc;
$$;
