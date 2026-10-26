create table if not exists public.agent_instances (
  agent_instance_id       text primary key,
  user_id                 text references public.users(user_id),
  worker_id               text,
  connection_id           text,
  farm_session_id         text,
  target_player_id        bigint,
  state                   text not null,
  state_changed_at        timestamptz not null default now(),
  created_at              timestamptz not null default now(),
  last_game_event_at      timestamptz,
  command_inflight_count  integer not null default 0,
  ended_reason            text,
  resume_token            text,
  meta                    jsonb,
  updated_at              timestamptz not null default now()
);

create index if not exists idx_agent_instances_user    on public.agent_instances (user_id);
create index if not exists idx_agent_instances_state   on public.agent_instances (state);
create index if not exists idx_agent_instances_worker  on public.agent_instances (worker_id);
create index if not exists idx_agent_instances_changed on public.agent_instances (state_changed_at);

create or replace function public.agent_instances_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists trg_agent_instances_updated_at on public.agent_instances;
create trigger trg_agent_instances_updated_at
before update on public.agent_instances
for each row execute function public.agent_instances_touch_updated_at();
