alter table public.events
  add column if not exists client_event_id text;

alter table public.events
  drop constraint if exists uq_events_user_client_event_id;

alter table public.events
  add constraint uq_events_user_client_event_id unique (user_id, client_event_id);
