-- Additive extension: runtime_operations remains the sole idempotency authority.
ALTER TABLE public.runtime_operations
  DROP CONSTRAINT IF EXISTS runtime_operations_result_kind_check;

ALTER TABLE public.runtime_operations
  ADD CONSTRAINT runtime_operations_result_kind_check
  CHECK (result_kind IN ('runtime', 'binding', 'input'));
