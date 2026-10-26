CREATE INDEX IF NOT EXISTS idx_runtime_bindings_expiry_due
  ON public.runtime_bindings (expires_at, binding_id)
  WHERE expires_at IS NOT NULL
    AND status IN ('pending', 'active', 'suspended');
