alter table public.external_text_inputs
  add column if not exists sanitized_text text;

create table if not exists public.roadshow_delivery_events (
  id                bigserial primary key,
  input_id          text not null,
  route_key         text not null,
  user_id           text,
  agent_instance_id text,
  event_type        text not null,
  delivery_status   text not null,
  payload_type      text not null default 'external_text_input',
  sanitized_text    text,
  payload           jsonb not null default '{}'::jsonb,
  error             text,
  created_at        timestamptz not null default now()
);

create index if not exists idx_roadshow_delivery_events_input on public.roadshow_delivery_events (input_id);
create index if not exists idx_roadshow_delivery_events_route on public.roadshow_delivery_events (route_key);
create index if not exists idx_roadshow_delivery_events_user on public.roadshow_delivery_events (user_id);
create index if not exists idx_roadshow_delivery_events_agent on public.roadshow_delivery_events (agent_instance_id);
create index if not exists idx_roadshow_delivery_events_status on public.roadshow_delivery_events (delivery_status);
create index if not exists idx_roadshow_delivery_events_type on public.roadshow_delivery_events (event_type);
create index if not exists idx_roadshow_delivery_events_created on public.roadshow_delivery_events (created_at desc);
