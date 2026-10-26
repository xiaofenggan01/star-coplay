-- Durable GatewayEvent outbox for crash-safe at-least-once delivery.
-- Producer writes the event and returns; a background worker claims and
-- advances application_stage through projection → binding → runtime so
-- that a mid-flight crash never loses the event and replay converges
-- without duplicate side-effects.

CREATE TABLE public.gateway_event_outbox (
    message_id        text PRIMARY KEY,
    event_payload     jsonb NOT NULL,
    application_stage text NOT NULL DEFAULT 'pending'
                      CHECK (application_stage IN ('pending', 'projected', 'bound', 'completed', 'failed')),
    claimed_by        text,
    claimed_at        timestamptz,
    attempt_count     integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error        text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(event_payload) = 'object')
);

-- Worker claim scan: skip completed rows and rows whose claim lease is still
-- valid (5-minute lease).  Order by created_at for approximate FIFO.
CREATE INDEX idx_gateway_event_outbox_pending
    ON public.gateway_event_outbox (application_stage, created_at)
    WHERE application_stage != 'completed';

ALTER TABLE public.gateway_event_outbox ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.gateway_event_outbox IS 'Durable GatewayEvent outbox: stage-gated projection → binding → runtime application';
