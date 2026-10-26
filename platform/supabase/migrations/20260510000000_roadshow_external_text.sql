create table if not exists public.roadshow_routes (
  route_key                text primary key,
  title                    text not null default '路演入口',
  enabled                  boolean not null default true,
  target_user_id           text references public.users(user_id),
  target_agent_instance_id text,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

create index if not exists idx_roadshow_routes_enabled on public.roadshow_routes (enabled);
create index if not exists idx_roadshow_routes_target_user on public.roadshow_routes (target_user_id);

create table if not exists public.external_text_inputs (
  id                bigserial primary key,
  input_id          text not null,
  source            text not null default 'roadshow',
  route_key         text not null references public.roadshow_routes(route_key),
  user_id           text not null references public.users(user_id),
  text              text not null,
  sender_label      text,
  received_at       timestamptz not null default now(),
  agent_instance_id text,
  delivery_status   text not null default 'received',
  error             text,
  delivered_at      timestamptz,
  raw_metadata      jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  constraint uq_external_text_inputs_user_source_input unique (user_id, source, input_id)
);

create index if not exists idx_external_text_inputs_route on public.external_text_inputs (route_key);
create index if not exists idx_external_text_inputs_user on public.external_text_inputs (user_id);
create index if not exists idx_external_text_inputs_status on public.external_text_inputs (delivery_status);
create index if not exists idx_external_text_inputs_received on public.external_text_inputs (received_at);

create or replace function public.roadshow_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists trg_roadshow_routes_updated_at on public.roadshow_routes;
create trigger trg_roadshow_routes_updated_at
before update on public.roadshow_routes
for each row execute function public.roadshow_touch_updated_at();

drop trigger if exists trg_external_text_inputs_updated_at on public.external_text_inputs;
create trigger trg_external_text_inputs_updated_at
before update on public.external_text_inputs
for each row execute function public.roadshow_touch_updated_at();
