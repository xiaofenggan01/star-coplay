-- Agentee Persona 独立表：身份 + 人格配置
-- 替代原 profiles.config.persona jsonb 方案，支持多 agentee、独立 RLS、结构化查询

/* ------------------------------------------------------------------ */
/*  Table: agentee_profiles (Identity facts)                           */
/* ------------------------------------------------------------------ */

CREATE TABLE public.agentee_profiles (
  agentee_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  is_default        boolean NOT NULL DEFAULT true,
  display_name      text NOT NULL DEFAULT '小枫',
  avatar_url        text,
  user_call_name    text NOT NULL DEFAULT '主人',
  relationship_type text NOT NULL DEFAULT '游戏搭子',
  short_bio         text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_agentee_profiles_user_id
  ON public.agentee_profiles(user_id);

CREATE UNIQUE INDEX uniq_agentee_profiles_default
  ON public.agentee_profiles(user_id)
  WHERE is_default;

-- RLS
ALTER TABLE public.agentee_profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own agentee profiles" ON public.agentee_profiles
  FOR SELECT USING (
    user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = auth.uid() LIMIT 1)
  );

/* ------------------------------------------------------------------ */
/*  Table: agentee_personas (Personality config & compiled output)      */
/* ------------------------------------------------------------------ */

CREATE TABLE public.agentee_personas (
  agentee_id             uuid PRIMARY KEY REFERENCES public.agentee_profiles(agentee_id) ON DELETE CASCADE,
  user_id                text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  schema_version         integer NOT NULL DEFAULT 1,
  custom_description     text,
  core_preferences       jsonb NOT NULL DEFAULT '{"warmth":3,"expressiveness":3,"playfulness":3,"initiative":3,"carefulness":3,"goal_focus":3}',
  custom_flavor          jsonb NOT NULL DEFAULT '[]'::jsonb,
  style_summary          text,
  speech_guidelines      jsonb,
  collaboration_guidelines jsonb,
  gameplay_guidelines    jsonb,
  persona_card           text,
  generation_status      text NOT NULL DEFAULT 'pending'
    CHECK (generation_status IN ('pending','generated','partial_fallback','fallback')),
  generation_error       text,
  generated_at           timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_agentee_personas_user_id
  ON public.agentee_personas(user_id);

-- RLS
ALTER TABLE public.agentee_personas ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own agentee personas" ON public.agentee_personas
  FOR SELECT USING (
    user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = auth.uid() LIMIT 1)
  );

/* ------------------------------------------------------------------ */
/*  Triggers: updated_at auto-maintenance                              */
/* ------------------------------------------------------------------ */

CREATE OR REPLACE FUNCTION public.tg_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_agentee_profiles_updated_at ON public.agentee_profiles;
CREATE TRIGGER trg_agentee_profiles_updated_at
  BEFORE UPDATE ON public.agentee_profiles
  FOR EACH ROW EXECUTE FUNCTION public.tg_set_updated_at();

DROP TRIGGER IF EXISTS trg_agentee_personas_updated_at ON public.agentee_personas;
CREATE TRIGGER trg_agentee_personas_updated_at
  BEFORE UPDATE ON public.agentee_personas
  FOR EACH ROW EXECUTE FUNCTION public.tg_set_updated_at();

/* ------------------------------------------------------------------ */
/*  Comments                                                           */
/* ------------------------------------------------------------------ */

COMMENT ON TABLE public.agentee_profiles IS 'AI 搭子身份信息（名称/称呼/关系）';
COMMENT ON TABLE public.agentee_personas IS 'AI 搭子人格配置与生成产物（偏好轴/风味/persona card）';
