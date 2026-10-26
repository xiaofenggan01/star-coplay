-- Drop legacy direct LLM provider pool.
-- Runtime LLM calls now route through NewAPI-backed AI Relay.

DROP TRIGGER IF EXISTS trg_llm_providers_updated_at ON public.llm_providers;
DROP TABLE IF EXISTS public.llm_providers;
DROP FUNCTION IF EXISTS public.update_llm_providers_updated_at();
