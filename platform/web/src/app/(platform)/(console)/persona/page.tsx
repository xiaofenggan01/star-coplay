"use client";

import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";
import type {
  AgenteeConfigResponse,
  AgenteePersona,
  AgenteeProfile,
  CorePreferences,
  CustomFlavor,
  PersonaGenerateOutput,
  PreferenceDimension,
} from "@/lib/types/agentee";
import {
  AGENTEE_CONSTRAINTS,
  DEFAULT_CORE_PREFERENCES,
  PREFERENCE_DIMENSIONS,
} from "@/lib/types/agentee";

const DIMENSION_LABELS: Record<PreferenceDimension, { name: string; low: string; high: string }> = {
  warmth: { name: "温暖度", low: "疏离冷淡", high: "温暖关怀" },
  expressiveness: { name: "表达欲", low: "沉默寡言", high: "话多情绪化" },
  playfulness: { name: "活泼度", low: "正经沉稳", high: "爱开玩笑" },
  initiative: { name: "主动性", low: "被动等待", high: "主动建议" },
  carefulness: { name: "谨慎度", low: "大胆冒险", high: "谨慎安全" },
  goal_focus: { name: "目标感", low: "沉浸体验", high: "效率优先" },
};

type InspirationSeed = {
  label: string;
  description: string;
  preferences: CorePreferences;
};

const INSPIRATION_SEEDS: InspirationSeed[] = [
  {
    label: "硬核包工头",
    description: "需要一个极其正经的农场包工头。眼里只有种地和赚钱，我不干活他就会一直催，毫无闲聊的心思，效率就是一切。",
    preferences: { warmth: 2, expressiveness: 2, playfulness: 1, initiative: 5, carefulness: 4, goal_focus: 5 },
  },
  {
    label: "活泼钓鱼佬",
    description: "想要一个超级活泼不羁的钓鱼佬！看到水就要去钓鱼，整天叽叽喳喳找我聊天，对农活和进度什么的一点也不上心，开心最重要。",
    preferences: { warmth: 5, expressiveness: 5, playfulness: 5, initiative: 4, carefulness: 2, goal_focus: 1 },
  },
  {
    label: "高冷下矿机器",
    description: "话少、高冷的下矿机器。不用主动找我聊天，只要能高效率陪我下矿冲层数就行，对其他事情漠不关心。",
    preferences: { warmth: 1, expressiveness: 1, playfulness: 1, initiative: 3, carefulness: 1, goal_focus: 5 },
  },
  {
    label: "温柔风景党",
    description: "希望有个非常有礼貌的风景党陪我闲逛。他可以是个路痴，也不用主动找话题，遇到好看的地方就停下来，我们慢慢悠悠建农场就好。",
    preferences: { warmth: 5, expressiveness: 2, playfulness: 2, initiative: 2, carefulness: 4, goal_focus: 1 },
  },
  {
    label: "交际花夜猫子",
    description: "想要一个随性爱玩的夜猫子，特别爱给村民送礼串门。白天懒洋洋的，到了晚上就精神百倍地拉着我全村跑。",
    preferences: { warmth: 4, expressiveness: 4, playfulness: 4, initiative: 4, carefulness: 2, goal_focus: 2 },
  },
  {
    label: "傲娇的赚钱狂",
    description: "来个满嘴跑火车但极其靠谱的搞钱搭子。嘴上总是损我，但每天都会主动拉我去干活酿酒，绝不把时间浪费在无效社交上。",
    preferences: { warmth: 4, expressiveness: 4, playfulness: 5, initiative: 5, carefulness: 3, goal_focus: 5 },
  },
  {
    label: "佛系小跟班",
    description: "安安静静的陪玩就行。不用太主动，我干嘛他干嘛。脾气温和一点，我们佛系游玩，没什么通关压力。",
    preferences: { warmth: 4, expressiveness: 1, playfulness: 1, initiative: 1, carefulness: 4, goal_focus: 1 },
  },
  {
    label: "混沌整活大师",
    description: "来个极其跳脱、天天不务正业的搭子。毫无游戏目标，不仅是个路痴老带错路，还总能主动整出点离谱的乐子来找我搭话。",
    preferences: { warmth: 3, expressiveness: 5, playfulness: 5, initiative: 5, carefulness: 1, goal_focus: 1 },
  },
  {
    label: "硬核熬夜冠军",
    description: "要一个极度正经、眼里只有农活的夜猫子。绝不浪费一分钟游戏时间，就算熬夜到凌晨也要把地种完，会疯狂催促我跟上进度。",
    preferences: { warmth: 2, expressiveness: 3, playfulness: 1, initiative: 5, carefulness: 2, goal_focus: 5 },
  },
  {
    label: "随性生活家",
    description: "想要一个平时说话很有网感的朋友。干农活时很认真，闲下来时也很会找话题陪我到处瞎逛，偶尔还会自己跑去装修农场。",
    preferences: { warmth: 4, expressiveness: 4, playfulness: 4, initiative: 4, carefulness: 3, goal_focus: 3 },
  },
];

const fieldClass =
  "w-full px-4 py-3 border-2 border-stardew-wood/25 rounded-xl text-[13px] bg-stardew-cream/90 text-stardew-brown placeholder:text-stardew-brown/30 focus:outline-none focus:border-stardew-wood focus:ring-2 focus:ring-stardew-wood/15";

function StepBadge({ active, index, label }: { active: boolean; index: number; label: string }) {
  return (
    <div
      className={`inline-flex items-center gap-2 px-3 py-2 rounded-xl border-2 text-[12px] font-bold ${
        active ? "bg-stardew-wood-light/25 border-stardew-wood text-stardew-brown" : "bg-stardew-cream/60 border-stardew-wood/20 text-stardew-brown/70"
      }`}
    >
      <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] ${active ? "bg-stardew-wood text-white" : "bg-stardew-wood/15 text-stardew-brown"}`}>
        {index}
      </span>
      <span>{label}</span>
    </div>
  );
}

type PageStep = 0 | 1 | 2 | 3;

export default function PersonaPage() {
  const router = useRouter();
  const supabase = createClient();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [step, setStep] = useState<PageStep>(1);
  const [profileRecord, setProfileRecord] = useState<AgenteeProfile | null>(null);
  const [personaRecord, setPersonaRecord] = useState<AgenteePersona | null>(null);

  const [displayName, setDisplayName] = useState("");
  const [userCallName, setUserCallName] = useState("");
  const [relationshipType, setRelationshipType] = useState("");
  const [customDescription, setCustomDescription] = useState("");
  const [preferences, setPreferences] = useState<CorePreferences>({ ...DEFAULT_CORE_PREFERENCES });
  const [flavors, setFlavors] = useState<CustomFlavor[]>([]);
  const [selectedSeed, setSelectedSeed] = useState<InspirationSeed | null>(null);

  const hasBasicInfo = useMemo(
    () => !!displayName.trim() && !!userCallName.trim() && !!relationshipType.trim(),
    [displayName, relationshipType, userCallName],
  );
  const hasInvalidFlavor = useMemo(
    () => flavors.some((flavor) => {
      const hasAny = !!flavor.label.trim() || !!flavor.description.trim();
      const complete = !!flavor.label.trim() && !!flavor.description.trim();
      return hasAny && !complete;
    }),
    [flavors],
  );

  useEffect(() => {
    void fetchConfig();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function getToken(): Promise<string | null> {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session?.access_token) {
      router.push("/");
      return null;
    }
    return session.access_token;
  }

  async function fetchConfig() {
    setLoading(true);
    setLoadFailed(false);
    try {
      const token = await getToken();
      if (!token) return;

      const res = await fetch("/api/me/persona", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const err = (await res.json().catch(() => null)) as { error?: string; reason?: string } | null;
        setLoadFailed(true);
        setMessage({
          type: "error",
          text: err?.reason === "persona_access_denied" ? "当前账号暂未开放性格定制" : "读取搭子信息失败，请稍后重试",
        });
        return;
      }

      const data = (await res.json()) as AgenteeConfigResponse;
      if (data.profile) {
        setProfileRecord(data.profile);
        setDisplayName(data.profile.display_name ?? "");
        setUserCallName(data.profile.user_call_name ?? "");
        setRelationshipType(data.profile.relationship_type ?? "");
      } else {
        setProfileRecord(null);
        setDisplayName("");
        setUserCallName("");
        setRelationshipType("");
      }
      if (data.persona) {
        setPersonaRecord(data.persona);
        setCustomDescription(data.persona.custom_description ?? "");
        setPreferences(data.persona.core_preferences);
        setFlavors(data.persona.custom_flavor ?? []);
      } else {
        setPersonaRecord(null);
        setCustomDescription("");
        setPreferences({ ...DEFAULT_CORE_PREFERENCES });
        setFlavors([]);
      }
      if (data.profile) {
        setStep(0);
      } else {
        setStep(1);
      }
    } finally {
      setLoading(false);
    }
  }

  function rollInspiration() {
    const seed = INSPIRATION_SEEDS[Math.floor(Math.random() * INSPIRATION_SEEDS.length)];
    if (!seed) return;
    setSelectedSeed(seed);
    setCustomDescription(seed.description);
    setMessage({ type: "success", text: `已掷出「${seed.label}」灵感` });
  }

  async function handleGenerate() {
    setGenerating(true);
    setMessage(null);
    try {
      const token = await getToken();
      if (!token) return;

      const res = await fetch("/api/me/persona/generate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          custom_description: customDescription.trim() || undefined,
        }),
      });
      if (!res.ok) {
        const err = (await res.json().catch(() => null)) as { error?: string } | null;
        setMessage({ type: "error", text: err?.error ?? "生成失败，请稍后重试" });
        return;
      }

      const result = (await res.json()) as PersonaGenerateOutput;
      setPreferences(result.core_preferences);
      setFlavors(result.custom_flavor);
      setStep(3);
      if (result.generation_status === "fallback") {
        setMessage({
          type: "error",
          text: result.generation_error
            ? `LLM 生成失败，已使用兜底值：${result.generation_error}`
            : "LLM 生成失败，已使用兜底值",
        });
      } else {
        setMessage({ type: "success", text: "已生成性格建议，可以继续微调" });
      }
    } finally {
      setGenerating(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setMessage(null);
    try {
      const token = await getToken();
      if (!token) return;
      const cleanedFlavors = flavors
        .map((flavor) => ({ label: flavor.label.trim(), description: flavor.description.trim() }))
        .filter((flavor) => flavor.label || flavor.description);
      if (cleanedFlavors.some((flavor) => !flavor.label || !flavor.description)) {
        setMessage({ type: "error", text: "个性细节需要同时填写名称和说明" });
        return;
      }

      const res = await fetch("/api/me/persona", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          profile: {
            display_name: displayName.trim(),
            user_call_name: userCallName.trim(),
            relationship_type: relationshipType.trim(),
          },
          persona: {
            custom_description: customDescription.trim() || null,
            core_preferences: preferences,
            custom_flavor: cleanedFlavors,
          },
        }),
      });

      if (res.ok) {
        setMessage({ type: "success", text: "保存成功" });
        const data = (await res.json().catch(() => null)) as AgenteeConfigResponse | null;
        if (data?.profile) {
          setProfileRecord(data.profile);
          setDisplayName(data.profile.display_name ?? "");
          setUserCallName(data.profile.user_call_name ?? "");
          setRelationshipType(data.profile.relationship_type ?? "");
        }
        if (data?.persona) {
          setPersonaRecord(data.persona);
          setCustomDescription(data.persona.custom_description ?? "");
          setPreferences(data.persona.core_preferences);
          setFlavors(data.persona.custom_flavor ?? []);
        }
        setStep(0);
        return;
      }

      const err = (await res.json().catch(() => null)) as { error?: string } | null;
      setMessage({ type: "error", text: err?.error ?? "保存失败" });
    } finally {
      setSaving(false);
    }
  }

  function handlePreferenceChange(dim: PreferenceDimension, value: number) {
    setPreferences((prev) => ({ ...prev, [dim]: value }));
  }

  function handleAddFlavor() {
    if (flavors.length >= AGENTEE_CONSTRAINTS.CUSTOM_FLAVOR_MAX) return;
    setFlavors((prev) => [...prev, { label: "", description: "" }]);
  }

  function handleRemoveFlavor(index: number) {
    setFlavors((prev) => prev.filter((_, i) => i !== index));
  }

  function handleFlavorChange(index: number, field: "label" | "description", value: string) {
    setFlavors((prev) => prev.map((f, i) => (i === index ? { ...f, [field]: value } : f)));
  }

  if (loading) {
    return (
      <div className="min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] bg-stardew-cream px-4 py-10">
        <div className="max-w-3xl mx-auto parchment rounded-2xl p-6 md:p-8 text-center">
          <p className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown">加载中...</p>
        </div>
      </div>
    );
  }

  if (loadFailed) {
    return (
      <div className="min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] bg-stardew-cream px-4 py-10">
        <div className="max-w-3xl mx-auto parchment rounded-2xl p-6 md:p-8 space-y-4">
          <h1 className="font-[family-name:var(--font-pixel)] text-base md:text-lg text-stardew-brown">搭子性格</h1>
          {message && (
            <div className="rounded-xl border-2 border-stardew-red/50 bg-stardew-red/10 px-4 py-3 text-[12px] md:text-[13px] text-stardew-red">
              {message.text}
            </div>
          )}
          <button
            type="button"
            onClick={fetchConfig}
            className="stardew-btn inline-flex items-center justify-center px-5 py-3 rounded-xl text-[12px] font-bold"
          >
            重新加载
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] bg-stardew-cream px-4 py-6 md:py-8">
      <div className="max-w-3xl mx-auto">
        <main className="parchment rounded-2xl p-5 md:p-8 space-y-5">
          <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <h1 className="font-[family-name:var(--font-pixel)] text-base md:text-lg text-stardew-brown">
              搭子性格
            </h1>
          </header>

          {message && (
            <div
              className={`rounded-xl border-2 px-4 py-3 text-[12px] md:text-[13px] ${
                message.type === "success"
                  ? "border-stardew-green/50 bg-stardew-green/10 text-stardew-green"
                  : "border-stardew-red/50 bg-stardew-red/10 text-stardew-red"
              }`}
            >
              {message.text}
            </div>
          )}

          {step > 0 && (
            <div className="flex flex-wrap gap-2">
              <StepBadge active={step === 1} index={1} label="基本信息" />
              <StepBadge active={step === 2} index={2} label="填写描述" />
              <StepBadge active={step === 3} index={3} label="微调保存" />
            </div>
          )}

          {step === 0 ? (
            <section className="space-y-5">
              <div className="grid gap-3 md:grid-cols-3">
                <div className="rounded-xl border-2 border-stardew-wood/20 bg-stardew-cream/60 px-4 py-3">
                  <p className="text-[10px] font-bold text-stardew-brown/55">搭子昵称</p>
                  <p className="mt-1 text-[13px] font-bold text-stardew-brown">{displayName || "-"}</p>
                </div>
                <div className="rounded-xl border-2 border-stardew-wood/20 bg-stardew-cream/60 px-4 py-3">
                  <p className="text-[10px] font-bold text-stardew-brown/55">对你的称呼</p>
                  <p className="mt-1 text-[13px] font-bold text-stardew-brown">{userCallName || "-"}</p>
                </div>
                <div className="rounded-xl border-2 border-stardew-wood/20 bg-stardew-cream/60 px-4 py-3">
                  <p className="text-[10px] font-bold text-stardew-brown/55">关系定位</p>
                  <p className="mt-1 text-[13px] font-bold text-stardew-brown">{relationshipType || "-"}</p>
                </div>
              </div>

              {personaRecord ? (
                <div className="space-y-4">
                  {personaRecord.style_summary && (
                    <div className="rounded-xl border-2 border-stardew-wood/20 bg-white/35 px-4 py-3">
                      <p className="text-[10px] font-bold text-stardew-brown/55">性格摘要</p>
                      <p className="mt-2 text-[13px] leading-6 text-stardew-brown/85">{personaRecord.style_summary}</p>
                    </div>
                  )}
                  {customDescription && (
                    <div className="rounded-xl border-2 border-stardew-wood/20 bg-white/35 px-4 py-3">
                      <p className="text-[10px] font-bold text-stardew-brown/55">期待描述</p>
                      <p className="mt-2 text-[13px] leading-6 text-stardew-brown/85">{customDescription}</p>
                    </div>
                  )}
                  <div className="rounded-xl border-2 border-stardew-wood/20 bg-white/35 px-4 py-3">
                    <p className="text-[10px] font-bold text-stardew-brown/55">性格维度</p>
                    <div className="mt-3 grid gap-2 md:grid-cols-2">
                      {PREFERENCE_DIMENSIONS.map((dim) => (
                        <div key={dim} className="flex items-center justify-between gap-3 text-[12px] text-stardew-brown/80">
                          <span className="font-bold">{DIMENSION_LABELS[dim].name}</span>
                          <span className="font-[family-name:var(--font-pixel)] text-[10px]">{preferences[dim]}/5</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  {flavors.length > 0 && (
                    <div className="rounded-xl border-2 border-stardew-wood/20 bg-white/35 px-4 py-3">
                      <p className="text-[10px] font-bold text-stardew-brown/55">个性细节</p>
                      <div className="mt-3 space-y-2">
                        {flavors.map((flavor, index) => (
                          <p key={index} className="text-[12px] leading-5 text-stardew-brown/80">
                            <span className="font-bold">{flavor.label}</span>：{flavor.description}
                          </p>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="rounded-xl border-2 border-stardew-wood/20 bg-white/35 px-4 py-3">
                  <p className="text-[13px] leading-6 text-stardew-brown/75">还没有生成性格。</p>
                </div>
              )}

              <div className="flex flex-col gap-3 sm:flex-row">
                <button
                  type="button"
                  onClick={() => setStep(personaRecord ? 3 : 2)}
                  className="stardew-btn inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl text-[12px] font-bold"
                >
                  {personaRecord ? "编辑性格" : "生成性格"} <span aria-hidden="true">→</span>
                </button>
                <button
                  type="button"
                  onClick={() => setStep(1)}
                  className="inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl border-2 border-stardew-wood/25 bg-stardew-cream/80 text-[12px] font-bold text-stardew-brown"
                >
                  编辑信息
                </button>
              </div>
            </section>
          ) : step === 1 ? (
            <section className="space-y-5">
              <div className="space-y-1">
                <h2 className="font-[family-name:var(--font-pixel)] text-xs text-stardew-brown">基础信息</h2>
              </div>

              <div className="grid gap-4">
                <div>
                  <label className="block mb-1 text-[11px] text-stardew-brown/70 font-bold">搭子昵称</label>
                  <input
                    type="text"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    maxLength={AGENTEE_CONSTRAINTS.DISPLAY_NAME_MAX}
                    placeholder="例如：小枫"
                    className={fieldClass}
                  />
                </div>
                <div>
                  <label className="block mb-1 text-[11px] text-stardew-brown/70 font-bold">对你的称呼</label>
                  <input
                    type="text"
                    value={userCallName}
                    onChange={(e) => setUserCallName(e.target.value)}
                    maxLength={AGENTEE_CONSTRAINTS.USER_CALL_NAME_MAX}
                    placeholder="例如：主人"
                    className={fieldClass}
                  />
                </div>
                <div>
                  <label className="block mb-1 text-[11px] text-stardew-brown/70 font-bold">关系定位</label>
                  <input
                    type="text"
                    value={relationshipType}
                    onChange={(e) => setRelationshipType(e.target.value)}
                    maxLength={AGENTEE_CONSTRAINTS.RELATIONSHIP_TYPE_MAX}
                    placeholder="例如：游戏搭子"
                    className={fieldClass}
                  />
                </div>
              </div>

              <div className="flex justify-end pt-1">
                {profileRecord && (
                  <button
                    type="button"
                    onClick={() => setStep(0)}
                    className="mr-3 inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl border-2 border-stardew-wood/25 bg-stardew-cream/80 text-[12px] font-bold text-stardew-brown"
                  >
                    取消
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setStep(2)}
                  disabled={!hasBasicInfo}
                  className="stardew-btn inline-flex items-center gap-2 px-5 py-3 rounded-xl text-[12px] font-bold disabled:opacity-50"
                >
                  下一步 <span aria-hidden="true">→</span>
                </button>
              </div>
            </section>
          ) : step === 2 ? (
            <section className="space-y-5">
              <div className="space-y-1">
                <h2 className="font-[family-name:var(--font-pixel)] text-xs text-stardew-brown">填写描述</h2>
              </div>

              <div className="relative">
                <textarea
                  value={customDescription}
                  onChange={(e) => setCustomDescription(e.target.value)}
                  maxLength={AGENTEE_CONSTRAINTS.CUSTOM_DESCRIPTION_MAX}
                  placeholder="例如：希望 TA 平时安静一点，但在我迷茫时能主动给建议，偶尔也能开点玩笑缓和气氛。"
                  rows={7}
                  className={`${fieldClass} min-h-[170px] resize-y pr-24`}
                />
                <button
                  type="button"
                  onClick={rollInspiration}
                  aria-label="随机灵感"
                  title="随机灵感"
                  className="absolute right-3 bottom-3 inline-flex h-10 w-10 items-center justify-center rounded-xl border-2 border-stardew-wood/30 bg-stardew-cream/95 text-[17px] font-bold text-stardew-brown shadow-sm hover:bg-white"
                >
                  <span aria-hidden="true">🎲</span>
                </button>
              </div>

              <div className="flex items-center justify-between gap-3">
                <span className={`text-[11px] ${customDescription.length < AGENTEE_CONSTRAINTS.CUSTOM_DESCRIPTION_MAX ? "text-stardew-brown/60" : "text-stardew-red"}`}>
                  {customDescription.length}/{AGENTEE_CONSTRAINTS.CUSTOM_DESCRIPTION_MAX}
                </span>
                {selectedSeed && <span className="text-[11px] text-stardew-brown/60">已填入：{selectedSeed.label}</span>}
              </div>

              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <button
                  type="button"
                  onClick={() => setStep(profileRecord ? 0 : 1)}
                  className="inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl border-2 border-stardew-wood/25 bg-stardew-cream/80 text-[12px] font-bold text-stardew-brown"
                >
                  <span aria-hidden="true">←</span> 上一步
                </button>
                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={handleGenerate}
                    disabled={generating || !customDescription.trim()}
                    className="stardew-btn inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl text-[12px] font-bold disabled:opacity-50"
                  >
                    {generating ? "生成中..." : "生成性格"} <span aria-hidden="true">→</span>
                  </button>
                </div>
              </div>
            </section>
          ) : (
            <section className="space-y-5">
              <div className="space-y-1">
                <h2 className="font-[family-name:var(--font-pixel)] text-xs text-stardew-brown">微调保存</h2>
              </div>

              <div className="grid gap-5">
                <div className="space-y-4">
                  {PREFERENCE_DIMENSIONS.map((dim) => {
                    const label = DIMENSION_LABELS[dim];
                    return (
                      <div key={dim} className="parchment rounded-2xl p-4 bg-stardew-cream/60">
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-[12px] font-bold text-stardew-brown">{label.name}</span>
                          <span className="font-[family-name:var(--font-pixel)] text-[10px] text-stardew-brown/70">{preferences[dim]}/5</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-[10px] text-stardew-brown/60 w-14 text-right font-bold">{label.low}</span>
                          <input
                            type="range"
                            min={1}
                            max={5}
                            step={1}
                            value={preferences[dim]}
                            onChange={(e) => handlePreferenceChange(dim, Number(e.target.value))}
                            className="persona-range flex-1"
                            style={{ "--value": `${((preferences[dim] - 1) / 4) * 100}%` } as CSSProperties}
                          />
                          <span className="text-[10px] text-stardew-brown/60 w-14 font-bold">{label.high}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>

                <div className="parchment rounded-2xl p-4 bg-stardew-cream/60 space-y-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h3 className="font-[family-name:var(--font-pixel)] text-[10px] text-stardew-brown">个性细节</h3>
                    </div>
                  </div>

                  <div className="space-y-3">
                    {flavors.map((flavor, index) => (
                      <div key={index} className="grid gap-2 md:grid-cols-[1fr_auto] md:items-start">
                        <div className="grid gap-2">
                          <input
                            type="text"
                            value={flavor.label}
                            onChange={(e) => handleFlavorChange(index, "label", e.target.value)}
                            maxLength={AGENTEE_CONSTRAINTS.CUSTOM_FLAVOR_LABEL_MAX}
                            placeholder="特质名称"
                            className={fieldClass}
                          />
                          <input
                            type="text"
                            value={flavor.description}
                            onChange={(e) => handleFlavorChange(index, "description", e.target.value)}
                            maxLength={AGENTEE_CONSTRAINTS.CUSTOM_FLAVOR_DESC_MAX}
                            placeholder="简要说明"
                            className={fieldClass}
                          />
                        </div>
                        <button
                          type="button"
                          onClick={() => handleRemoveFlavor(index)}
                          className="stardew-btn inline-flex items-center justify-center px-4 py-2.5 rounded-xl text-[11px] font-bold md:mt-1"
                        >
                          删除
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={handleAddFlavor}
                      disabled={flavors.length >= AGENTEE_CONSTRAINTS.CUSTOM_FLAVOR_MAX}
                      className="inline-flex items-center gap-2 text-[12px] font-bold text-stardew-brown/75 hover:text-stardew-brown disabled:opacity-40"
                    >
                      <span aria-hidden="true">+</span>
                      添加细节
                      {flavors.length > 0 && ` (${flavors.length}/${AGENTEE_CONSTRAINTS.CUSTOM_FLAVOR_MAX})`}
                    </button>
                  </div>
                </div>
              </div>

              <div className="flex flex-col sm:flex-row gap-3">
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={saving || !hasBasicInfo || hasInvalidFlavor}
                  className="stardew-btn inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl text-[12px] font-bold disabled:opacity-50"
                >
                  {saving ? "保存中..." : "保存性格"} <span aria-hidden="true">→</span>
                </button>
                <button
                  type="button"
                  onClick={() => setStep(2)}
                  className="inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl border-2 border-stardew-wood/25 bg-stardew-cream/80 text-[12px] font-bold text-stardew-brown"
                >
                  <span aria-hidden="true">←</span> 返回描述
                </button>
              </div>
            </section>
          )}
        </main>
      </div>

      <style jsx>{`
        .persona-range {
          appearance: none;
          height: 10px;
          border-radius: 999px;
          background:
            linear-gradient(90deg, var(--color-stardew-wood-light) 0%, var(--color-stardew-wood-light) 100%),
            #eadfca;
          background-repeat: no-repeat;
          background-size: var(--value, 50%) 100%;
          outline: none;
        }
        .persona-range::-webkit-slider-thumb {
          appearance: none;
          width: 18px;
          height: 18px;
          border-radius: 999px;
          border: 2px solid var(--color-stardew-wood-dark);
          background: #fff7e3;
          box-shadow: 0 1px 2px rgba(0, 0, 0, 0.25);
        }
        .persona-range::-moz-range-thumb {
          width: 18px;
          height: 18px;
          border-radius: 999px;
          border: 2px solid var(--color-stardew-wood-dark);
          background: #fff7e3;
          box-shadow: 0 1px 2px rgba(0, 0, 0, 0.25);
        }
      `}</style>
    </div>
  );
}
