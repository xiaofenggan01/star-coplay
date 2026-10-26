"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";
import { FN } from "@/lib/supabase";
import type { Profile } from "@/lib/types/profile";
import { QQGroupModal } from "@/components/QQGroupModal";
import InviteShareCard from "@/components/console/InviteShareCard";
import type { InviteShareCardHandle } from "@/components/console/InviteShareCard";
import type { AppearanceSubmit } from "@/lib/types/appearance";
import { DEFAULT_APPEARANCE, hsbToHex } from "@/lib/types/appearance";
import type { CorePreferences, CustomFlavor } from "@/lib/types/agentee";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface InviteCodeStatus {
  code: string;
  used_count: number;
  max_uses: number | null;
  usage_limit: number | null;
  remaining_uses: number | null;
  is_exhausted: boolean;
  batch_number: number | null;
}

interface InviteReferralStatus {
  id: string;
  referee_user_id: string;
  referee_email: string | null;
  referee_nickname: string | null;
  invite_code: string | null;
  reward_status: string;
  inviter_reward_status: string;
  invitee_reward_status: string;
  created_at: string;
}

interface InviteStatus {
  totalBatches: number;
  maxBatches: number;
  totalCodes: number;
  usedCodes: number;
  remainingUses: number | null;
  canGenerate: boolean;
  codes: InviteCodeStatus[];
  referrals: InviteReferralStatus[];
}

interface AvatarPreset {
  id: string;
  src: string;
  label: string;
}

const FALLBACK_AVATAR_PRESETS: AvatarPreset[] = [
  { id: "the_player", src: "/assets/icons/The_Player_Icon.png", label: "Player" },
  { id: "forester", src: "/assets/icons/Forester.png", label: "Forester" },
  { id: "junimo", src: "/assets/icons/Junimo_Icon.png", label: "Junimo" },
  { id: "smapi_mascot", src: "/assets/icons/SMAPI_mascot.png", label: "SMAPI" },
];

const USER_TYPE_LABEL: Record<string, string> = {
  guest: "游客",
  beta_user: "内测用户",
  user: "正式用户",
  member: "会员",
};

/* ------------------------------------------------------------------ */
/*  Main Component                                                     */
/* ------------------------------------------------------------------ */

export default function ProfilePage() {
  const router = useRouter();
  const supabase = createClient();
  const inviteCardRef = useRef<InviteShareCardHandle>(null);

  const [profile, setProfile] = useState<Profile | null>(null);
  const [email, setEmail] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [nickname, setNickname] = useState("");

  const [showQQModal, setShowQQModal] = useState(false);
  const [claimInviteCode, setClaimInviteCode] = useState("");
  const [claimingInvite, setClaimingInvite] = useState(false);
  const [claimInviteError, setClaimInviteError] = useState("");

  const [appearance, setAppearance] = useState<AppearanceSubmit | null>(null);
  const [agenteeName, setAgenteeName] = useState<string | null>(null);
  const [customFlavor, setCustomFlavor] = useState<CustomFlavor[]>([]);
  const [corePreferences, setCorePreferences] = useState<CorePreferences | null>(null);
  const [styleSummary, setStyleSummary] = useState<string | null>(null);
  const [relationshipType, setRelationshipType] = useState<string | null>(null);

  const [platformTokens, setPlatformTokens] = useState<
    Array<{ id: string; token: string; name: string; created_at: string }>
  >([]);
  const [generatingToken, setGeneratingToken] = useState(false);
  const [deletingToken, setDeletingToken] = useState(false);
  const [copiedToken, setCopiedToken] = useState(false);

  // Invite/referral state
  const [inviteStatus, setInviteStatus] = useState<InviteStatus | null>(null);
  const [generatingBatch, setGeneratingBatch] = useState(false);
  const [copiedInviteLink, setCopiedInviteLink] = useState<string | null>(null);
  const [showInviteCard, setShowInviteCard] = useState(false);
  const [avatarPresets, setAvatarPresets] = useState<AvatarPreset[]>(FALLBACK_AVATAR_PRESETS);
  const [showAvatarPicker, setShowAvatarPicker] = useState(false);
  const [savingAvatar, setSavingAvatar] = useState(false);
  const [avatarError, setAvatarError] = useState("");

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ── Data fetching ── */

  async function fetchData() {
    setLoading(true);
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        router.push("/");
        return;
      }

      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (user?.email) setEmail(user.email);

      const avatarRes = await fetch("/api/me/avatar-presets", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (avatarRes.ok) {
        const { items } = (await avatarRes.json()) as { items?: AvatarPreset[] };
        if (items?.length) setAvatarPresets(items);
      }

      const res = await fetch(FN.meProfile, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const { profile: data } = (await res.json()) as { profile: Profile | null };
        if (data) {
          setProfile(data);
          setNickname(data.nickname ?? "");

          if (data.user_type !== "guest") {
            const tokensRes = await fetch("/api/me/tokens", {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (tokensRes.ok) {
              const { tokens: tkns } = (await tokensRes.json()) as {
                tokens: Array<{ id: string; token: string; name: string; created_at: string }>;
              };
              setPlatformTokens(tkns ?? []);
            }

            const appRes = await fetch("/api/me/appearance", {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (appRes.ok) {
              const { appearance: appData } = (await appRes.json()) as {
                appearance: (AppearanceSubmit & { updated_at?: string }) | null;
              };
              if (appData) {
                const { updated_at: _, ...submit } = appData;
                setAppearance(submit);
              }
            }

            // Fetch invite/referral status. Users generate their personal code explicitly.
            const invRes = await fetch("/api/me/invitations", {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (invRes.ok) {
              const invData = await invRes.json();
              setInviteStatus(invData);
            }

            // Fetch persona (custom_flavor + core_preferences + style_summary + relationship_type)
            const personaRes = await fetch("/api/me/persona", {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (personaRes.ok) {
              const personaData = await personaRes.json();
              if (personaData.profile?.display_name) {
                setAgenteeName(personaData.profile.display_name);
              }
              if (personaData.persona?.custom_flavor) {
                setCustomFlavor(personaData.persona.custom_flavor);
              }
              if (personaData.persona?.core_preferences) {
                setCorePreferences(personaData.persona.core_preferences);
              }
              if (personaData.persona?.style_summary) {
                setStyleSummary(personaData.persona.style_summary);
              }
              if (personaData.profile?.relationship_type) {
                setRelationshipType(personaData.profile.relationship_type);
              }
            }
          }
        }
      }
    } finally {
      setLoading(false);
    }
  }

  /* ── Handlers ── */

  async function handleClaimInviteCode() {
    setClaimInviteError("");
    const code = claimInviteCode.trim();
    if (!code) {
      setClaimInviteError("请输入邀请码");
      return;
    }
    setClaimingInvite(true);
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) return;

      const res = await fetch("/api/me/invite-code/claim", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ invite_code: code }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setClaimInviteError(
          typeof data.error === "string" ? data.error : "邀请码开通失败，请稍后重试",
        );
        return;
      }
      setClaimInviteCode("");
      await fetchData();
    } finally {
      setClaimingInvite(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) return;

      const res = await fetch(FN.meProfile, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(nickname.trim() ? { nickname: nickname.trim() } : {}),
      });
      if (res.ok) {
        const { profile: updated } = (await res.json()) as { profile: Profile };
        setProfile(updated);
        setEditing(false);
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveAvatar(avatarUrl: string) {
    setAvatarError("");
    setSavingAvatar(true);
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        setAvatarError("登录已失效，请重新登录");
        return;
      }

      const res = await fetch(FN.meProfile, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ avatar_url: avatarUrl }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setAvatarError(
          data?.reason === "invalid_avatar_preset"
            ? "当前头像不可选，请重新选择"
            : "头像保存失败，请稍后重试",
        );
        return;
      }
      const { profile: updated } = (await res.json()) as { profile: Profile };
      setProfile(updated);
      setShowAvatarPicker(false);
    } finally {
      setSavingAvatar(false);
    }
  }

  function handleCancel() {
    setNickname(profile?.nickname ?? "");
    setEditing(false);
  }

  async function handleSignOut() {
    await supabase.auth.signOut();
    sessionStorage.clear();
    router.push("/");
  }

  async function handleGenerateToken() {
    setGeneratingToken(true);
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const accessToken = session?.access_token;
      if (!accessToken) return;
      const res = await fetch("/api/me/tokens", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
        body: JSON.stringify({}),
      });
      if (res.ok) {
        const { token: newToken } = await res.json();
        setPlatformTokens((prev) => [...prev, newToken]);
      }
    } finally {
      setGeneratingToken(false);
    }
  }

  async function handleDeleteToken(id: string) {
    setDeletingToken(true);
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const accessToken = session?.access_token;
      if (!accessToken) return;
      const res = await fetch("/api/me/tokens", {
        method: "DELETE",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
        body: JSON.stringify({ id }),
      });
      if (res.ok) {
        setPlatformTokens((prev) => prev.filter((t) => t.id !== id));
      }
    } finally {
      setDeletingToken(false);
    }
  }

  async function handleGenerateInviteBatch() {
    setGeneratingBatch(true);
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const accessToken = session?.access_token;
      if (!accessToken) return;
      const res = await fetch("/api/me/invitations", {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (res.ok) {
        // Re-fetch status
        const invRes = await fetch("/api/me/invitations", {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (invRes.ok) {
          const invData = await invRes.json();
          setInviteStatus(invData);
        }
      }
    } finally {
      setGeneratingBatch(false);
    }
  }

  function handleCopyInviteLink(code: string) {
    const url = buildInviteUrl(code);
    void navigator.clipboard.writeText(url);
    setCopiedInviteLink(`link:${code}`);
    setTimeout(() => setCopiedInviteLink(null), 1500);
  }

  function handleCopyInviteCode(code: string) {
    void navigator.clipboard.writeText(code);
    setCopiedInviteLink(`code:${code}`);
    setTimeout(() => setCopiedInviteLink(null), 1500);
  }

  function handleCopyPlatformToken(token: string) {
    void navigator.clipboard.writeText(token);
    setCopiedToken(true);
    setTimeout(() => setCopiedToken(false), 1500);
  }

  async function handleDownloadInviteCard() {
    await inviteCardRef.current?.saveImage();
  }

  function formatDate(dateStr: string) {
    return new Date(dateStr).toLocaleDateString("zh-CN");
  }

  /* ── Shared values ── */

  const charAppearance = appearance ?? DEFAULT_APPEARANCE;
  const activeAvatarPresets = avatarPresets.length > 0 ? avatarPresets : FALLBACK_AVATAR_PRESETS;
  const defaultAvatarUrl = activeAvatarPresets[0]?.src ?? "/assets/icons/The_Player_Icon.png";
  const avatarIsEnabled =
    !!profile?.avatar_url && activeAvatarPresets.some((preset) => preset.src === profile.avatar_url);
  const currentAvatarUrl = avatarIsEnabled ? profile!.avatar_url! : defaultAvatarUrl;
  const personaConfigured = Boolean(
    relationshipType?.trim() ||
      styleSummary?.trim() ||
      customFlavor.length > 0 ||
      (corePreferences && Object.keys(corePreferences).length > 0),
  );
  const activeInviteCode = inviteStatus?.codes.find((code) => !code.is_exhausted) ?? null;
  const exhaustedInviteCode = inviteStatus?.codes.find((code) => code.is_exhausted) ?? null;
  const inviteCodeState: "available" | "exhausted" | "empty" =
    activeInviteCode ? "available" : exhaustedInviteCode ? "exhausted" : "empty";
  const ts = { textShadow: "2px 2px 0px #3E2723" } as const;
  const tsWhite = { textShadow: "1px 1px 0px rgba(0,0,0,0.4)" } as const;
  const sdvInput =
    "w-full px-3 py-2 border-2 border-stardew-wood/30 rounded-lg bg-stardew-cream/80 text-stardew-brown text-[13px] focus:outline-none focus:border-stardew-wood focus:ring-2 focus:ring-stardew-wood/15";
  const avatarButtonBase =
    "shrink-0 rounded-full p-1 flex items-center justify-center hover:opacity-80 transition-opacity bg-stardew-cream/70 border-2 border-stardew-wood/20";
  const avatarPickerDialog = showAvatarPicker ? (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4">
      <div className="parchment w-full max-w-sm rounded-2xl p-5">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-bold text-stardew-brown">选择头像</h3>
          <button
            type="button"
            onClick={() => setShowAvatarPicker(false)}
            className="text-lg leading-none text-stardew-brown/60 hover:text-stardew-brown"
            aria-label="关闭头像选择"
          >
            x
          </button>
        </div>
        {!avatarIsEnabled && profile?.avatar_url && (
          <p className="mb-3 rounded-lg border border-stardew-red/20 bg-stardew-red/10 px-2 py-1.5 text-[11px] font-bold text-stardew-red">
            当前头像不可用，请重新选择
          </p>
        )}
        <div className="grid max-h-[60vh] grid-cols-4 gap-2 overflow-y-auto">
          {activeAvatarPresets.map((preset) => (
            <button
              key={preset.src}
              type="button"
              disabled={savingAvatar}
              onClick={() => void handleSaveAvatar(preset.src)}
              title={preset.label}
              className={`rounded-lg border-2 p-1 transition-colors disabled:opacity-50 ${
                profile?.avatar_url === preset.src
                  ? "border-stardew-gold bg-stardew-gold/15"
                  : "border-stardew-wood/20 hover:border-stardew-wood/50"
              }`}
            >
              <img
                src={preset.src}
                alt=""
                className="mx-auto h-10 w-10 pixel-art"
                style={{ imageRendering: "pixelated" }}
              />
            </button>
          ))}
        </div>
        {avatarError && (
          <p className="mt-3 text-center text-[10px] font-bold text-stardew-red">{avatarError}</p>
        )}
        <p className="mt-3 text-center text-[10px] text-stardew-brown/50">
          只能从预设里选，不支持上传
        </p>
      </div>
    </div>
  ) : null;

  /* ================================================================ */
  /*  LOADING / ERROR                                                  */
  /* ================================================================ */

  if (loading) {
    return (
      <div className="min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] bg-stardew-cream px-4 py-10" style={{ fontFamily: "var(--font-sans)" }}>
        <div className="max-w-3xl mx-auto parchment rounded-2xl p-6 md:p-8 text-center">
          <p className="text-sm text-stardew-brown">
            加载中...
          </p>
        </div>
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] bg-stardew-cream px-4 py-10" style={{ fontFamily: "var(--font-sans)" }}>
        <div className="max-w-3xl mx-auto parchment rounded-2xl p-6 md:p-8 text-center">
          <p className="text-sm text-stardew-brown">
            无法加载用户信息
          </p>
        </div>
      </div>
    );
  }

  /* ================================================================ */
  /*  EDIT MODE                                                        */
  /* ================================================================ */

  if (editing) {
    return (
      <div className="min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] bg-stardew-cream px-4 py-6 md:py-8" style={{ fontFamily: "var(--font-sans)" }}>
        <div className="max-w-3xl mx-auto parchment rounded-2xl p-5 md:p-8 space-y-5">
          <h2
            className="text-base text-stardew-brown"
          >
            编辑资料
          </h2>

          {/* Avatar + nickname */}
          <div className="flex items-start gap-5">
            <button
              type="button"
              onClick={() => setShowAvatarPicker(true)}
              className={avatarButtonBase}
              aria-label="更换头像"
            >
              <img
                src={currentAvatarUrl}
                alt="头像"
                className="w-20 h-20 rounded-full object-cover pixel-art"
                style={{ imageRendering: "pixelated" }}
              />
            </button>
            <div className="flex-1 space-y-4">
              <div>
                <label className="text-[11px] font-bold text-stardew-brown/70 block mb-1">
                  昵称
                </label>
                <input
                  type="text"
                  value={nickname}
                  onChange={(e) => setNickname(e.target.value)}
                  placeholder="输入昵称"
                  className={sdvInput}
                />
              </div>
              <div>
                <label className="text-[11px] font-bold text-stardew-brown/70 block mb-1">
                  邮箱（不可修改）
                </label>
                <input
                  type="text"
                  value={email}
                  disabled
                  className="w-full px-3 py-2 border-2 border-stardew-wood/15 rounded-lg bg-stardew-wood/10 text-stardew-brown/60 text-[13px] cursor-not-allowed"
                />
              </div>
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-3 pt-1">
            <button
              onClick={handleSave}
              disabled={saving}
              className="stardew-btn px-5 py-2.5 rounded-xl text-[12px] font-bold disabled:opacity-50"
            >
              {saving ? "保存中..." : "保存"}
            </button>
            <button
              onClick={handleCancel}
              disabled={saving}
              className="px-5 py-2.5 rounded-xl border-2 border-stardew-wood/25 bg-stardew-cream/80 text-[12px] font-bold text-stardew-brown"
            >
              取消
            </button>
          </div>
        </div>
        {avatarPickerDialog}
      </div>
    );
  }

  /* ================================================================ */
  /*  VIEW MODE — Stardew Valley Character Info Board                  */
  /* ================================================================ */

  const userType = USER_TYPE_LABEL[profile.user_type ?? "beta_user"] ?? "用户";

  return (
    <div className="min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] bg-stardew-cream px-4 py-2 md:py-3" style={{ fontFamily: "var(--font-sans)" }}>
      <div className="max-w-3xl mx-auto space-y-3">

        {/* ═══════════════════════════════════════════════════════════ */}
        {/*  SECTION 1: Compact Header Bar                             */}
        {/* ═══════════════════════════════════════════════════════════ */}
        <div className="parchment rounded-2xl p-3 md:p-4">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-black text-stardew-brown">
            <img
              src="/assets/icons/The_Player_Icon.png"
              alt=""
              className="h-4 w-4 pixel-art"
              style={{ imageRendering: "pixelated" }}
            />
            账号信息
          </h2>
          <div className="flex flex-wrap items-start gap-3 md:flex-nowrap">

            {/* Left: User avatar */}
            <button
              type="button"
              onClick={() => setShowAvatarPicker(true)}
              className={avatarButtonBase}
              aria-label="更换头像"
            >
              <img
                src={currentAvatarUrl}
                alt="头像"
                className="w-16 h-16 rounded-full object-cover pixel-art"
                style={{ imageRendering: "pixelated" }}
              />
            </button>

            {/* Center: Name + buttons + info */}
            <div className="min-w-0 flex-1">
              {/* Row 1: Name + Ribbon */}
              <div className="flex items-center gap-2 flex-wrap">
                <h1
                  className="text-base md:text-lg text-stardew-brown leading-tight"
                >
                  {profile.nickname || "未设置用户名"}
                </h1>
                <span
                  className="inline-flex items-center px-2.5 py-0.5 rounded-full text-[10px] font-bold"
                  style={{
                    background: "linear-gradient(180deg, #f5d76e, #d4a020)",
                    border: "1.5px solid #b8860b",
                    color: "#5a3e1b",
                    boxShadow:
                      "inset 0 1px 0 rgba(255,255,255,0.35), 0 1px 2px rgba(0,0,0,0.12)",
                  }}
                >
                  {userType}
                </span>
              </div>

              {/* Row 2: Action buttons */}
              <div className="flex items-center gap-2 mt-2">
                <button
                  onClick={() => setEditing(true)}
                  className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold"
                >
                  ✏️ 编辑资料
                </button>
              </div>

              {/* Row 3: Info rows with icons */}
              <div className="mt-2 space-y-1">
                <div className="flex items-center gap-2">
                  <img src="/assets/icons/Speech_bubble.png" alt="" className="w-3.5 h-3.5 pixel-art opacity-50 shrink-0" />
                  <span className="w-14 shrink-0 text-[11px] font-bold text-stardew-brown/45">邮箱</span>
                  <span className="text-[13px] text-stardew-brown/60 truncate">{email || "未绑定"}</span>
                </div>
                <div className="flex items-center gap-2">
                  <img src="/assets/icons/Inventory_Icon.png" alt="" className="w-3.5 h-3.5 pixel-art opacity-50 shrink-0" />
                  <span className="w-14 shrink-0 text-[11px] font-bold text-stardew-brown/45">ID</span>
                  <span className="min-w-0 break-all text-[12px] text-stardew-brown/60">{profile.user_id || "—"}</span>
                </div>
                {profile.created_at && (
                  <div className="flex items-center gap-2">
                    <img src="/assets/icons/Time_Icon.png" alt="" className="w-3.5 h-3.5 pixel-art opacity-50 shrink-0" />
                    <span className="w-14 shrink-0 text-[11px] font-bold text-stardew-brown/45">注册时间</span>
                    <span className="text-[13px] text-stardew-brown/60">
                      搭子已来到鹈鹕镇 {Math.max(1, Math.ceil((Date.now() - new Date(profile.created_at).getTime()) / 86400000))} 天
                    </span>
                  </div>
                )}
              </div>
            </div>
            {profile.user_type !== "guest" && (
              <div className="mt-3 w-full border-t-2 border-stardew-wood/20 pt-3 md:mt-0 md:w-[340px] md:shrink-0 md:border-l-2 md:border-t-0 md:pl-3 md:pt-0">
                {platformTokens.length > 0 ? (
                  <div className="flex items-center gap-2">
                    <img
                      src="/assets/icons/Controls_Icon.png"
                      alt=""
                      className="h-4 w-4 shrink-0 pixel-art opacity-70"
                      style={{ imageRendering: "pixelated" }}
                    />
                    <p className="shrink-0 text-[13px] font-black leading-tight text-stardew-brown">
                      访问令牌
                    </p>
                    <button
                      type="button"
                      onClick={() => handleCopyPlatformToken(platformTokens[0].token)}
                      title="点击复制访问令牌"
                      className="min-w-0 flex-1 rounded-lg border-2 border-stardew-wood/30 bg-stardew-cream/70 px-2.5 py-1.5 text-left shadow-inner transition-colors hover:bg-white/75 focus:outline-none focus:ring-2 focus:ring-stardew-gold"
                    >
                      <span className="block truncate font-mono text-[13px] font-black leading-5 text-stardew-brown">
                        {platformTokens[0].token}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleCopyPlatformToken(platformTokens[0].token)}
                      className="stardew-btn shrink-0 rounded-lg px-3 py-1.5 text-[11px] font-black"
                    >
                      {copiedToken ? "已复制" : "复制"}
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <img
                      src="/assets/icons/Controls_Icon.png"
                      alt=""
                      className="h-4 w-4 shrink-0 pixel-art opacity-70"
                      style={{ imageRendering: "pixelated" }}
                    />
                    <p className="shrink-0 text-[13px] font-black leading-tight text-stardew-brown">
                      访问令牌
                    </p>
                    <div className="min-w-0 flex-1 rounded-lg border-2 border-dashed border-stardew-wood/30 bg-stardew-cream/55 px-2.5 py-1.5">
                      <p className="truncate text-[12px] font-bold text-stardew-brown/60">
                        还没有访问令牌
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={handleGenerateToken}
                      disabled={generatingToken}
                      className="stardew-btn shrink-0 rounded-lg px-3 py-1.5 text-[11px] font-black disabled:opacity-50"
                    >
                      {generatingToken ? "生成中..." : "生成"}
                    </button>
                  </div>
                )}
                <Link
                  href="/guide"
                  className="mt-1.5 block truncate pl-6 text-[10px] font-bold text-stardew-blue underline decoration-stardew-blue/40 underline-offset-2 transition-colors hover:text-stardew-blue/80"
                >
                  激活 AI 搭子，需要输入访问令牌
                </Link>
              </div>
            )}
          </div>
        </div>

        {/* ═══════════════════════════════════════════════════════════ */}
        {/*  SECTION 2: Guest Waitlist Panel (guest only)              */}
        {/* ═══════════════════════════════════════════════════════════ */}

        {profile.user_type === "guest" && (
          <div className="parchment rounded-2xl p-5 space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-stardew-brown">
                补充邀请码
              </h3>
              <span className="rounded-full bg-stardew-wood/10 px-2 py-0.5 text-[10px] font-bold text-stardew-brown">
                游客
              </span>
            </div>

            <div className="space-y-2">
              <p className="text-[12px] leading-relaxed text-stardew-brown/60">
                输入官方或邀请人给你的邀请码，开通后即可使用 AI 账户、Mod 连接密钥和邀请功能。
              </p>
              <div className="flex flex-col gap-2 sm:flex-row">
                <input
                  type="text"
                  value={claimInviteCode}
                  onChange={(event) => {
                    setClaimInviteCode(event.target.value);
                    if (claimInviteError) setClaimInviteError("");
                  }}
                  className={`${sdvInput} font-mono tracking-wider`}
                  placeholder="输入邀请码"
                />
                <button
                  type="button"
                  onClick={() => void handleClaimInviteCode()}
                  disabled={claimingInvite}
                  className="stardew-btn shrink-0 rounded-xl px-4 py-2 text-[12px] font-bold disabled:opacity-50"
                >
                  {claimingInvite ? "开通中..." : "提交邀请码"}
                </button>
              </div>
              {claimInviteError && (
                <p className="text-[11px] font-bold text-stardew-red">{claimInviteError}</p>
              )}
            </div>

            <div className="space-y-2 border-t border-stardew-wood/15 pt-2">
              <InfoRowLight icon="/assets/icons/Speech_bubble.png" label="邮箱" value={email || "未绑定"} />
              <InfoRowLight icon="/assets/icons/Inventory_Icon.png" label="用户 ID" value={profile.user_id ?? ""} copyable />
              <InfoRowLight icon="/assets/icons/Multiplayer_Icon.png" label="社群">
                <button
                  onClick={() => setShowQQModal(true)}
                  className="text-stardew-wood text-[12px] font-bold hover:text-stardew-brown"
                >
                  加入 QQ 群 →
                </button>
              </InfoRowLight>
            </div>
          </div>
        )}

        {/*  SECTION 3: Card Preview + Appearance (side by side)        */}
        {/* ═══════════════════════════════════════════════════════════ */}

        {profile.user_type !== "guest" && (
          <>
            <div className="flex flex-col md:flex-row gap-3 md:items-stretch">
              {/* Card preview — scaled down, stretches to match right column */}
              <div className="parchment rounded-2xl p-3 md:w-[235px] md:flex-shrink-0 flex flex-col">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-stardew-brown">
                    🃏 搭子名片
                  </h3>
                  {personaConfigured && (
                  <button
                    type="button"
                    onClick={() => void handleDownloadInviteCard()}
                    title="下载搭子名片"
                    aria-label="下载搭子名片"
                    className="inline-flex h-9 w-9 items-center justify-center rounded-lg border-2 border-stardew-gold bg-stardew-cream shadow-sm transition-transform hover:-translate-y-0.5 hover:bg-stardew-gold/15 focus:outline-none focus:ring-2 focus:ring-stardew-gold"
                  >
                    <img
                      src="/assets/icons/ShippingBox.png"
                      alt=""
                      className="h-5 w-5 pixel-art"
                      style={{ imageRendering: "pixelated" }}
                    />
                  </button>
                  )}
                </div>
                {personaConfigured ? (
                <div className="flex-1 flex items-center justify-center">
                  <button
                    type="button"
                    onClick={() => setShowInviteCard(true)}
                    title="点击查看搭子名片"
                    aria-label="查看搭子名片"
                    className="overflow-hidden rounded-xl focus:outline-none focus:ring-2 focus:ring-stardew-gold"
                    style={{ width: 209, height: 352 }}
                  >
                    <div style={{ transform: "scale(0.55)", transformOrigin: "top left" }}>
                      <InviteShareCard
                        ref={inviteCardRef}
                        agentName={agenteeName ?? "小枫"}
                        appearance={charAppearance}
                        inviteCode={activeInviteCode?.code ?? null}
                        inviteCodeState={inviteCodeState}
                        relationshipType={relationshipType ?? undefined}
                        styleSummary={styleSummary ?? undefined}
                        corePreferences={corePreferences ?? undefined}
                        customFlavor={customFlavor}
                      />
                    </div>
                  </button>
                </div>
                ) : (
                  <div className="flex min-h-[220px] flex-1 items-center justify-center rounded-xl border-2 border-dashed border-stardew-wood/25 bg-stardew-cream/45 px-4 text-center">
                    <p className="text-[13px] font-bold text-stardew-brown/60">
                      快去设置搭子的性格吧！
                    </p>
                  </div>
                )}
              </div>

              {/* Right column: Companion info (merged) + Invite code */}
              <div className="flex-1 min-w-0 space-y-3">
                {/* Companion appearance + profile — single module */}
                <div className="parchment rounded-2xl p-3 md:p-4 space-y-3">
                  {!personaConfigured ? (
                    <div className="flex min-h-[180px] items-center justify-center rounded-xl border-2 border-dashed border-stardew-wood/25 bg-stardew-cream/45 px-4 text-center">
                      <p className="text-[13px] font-bold text-stardew-brown/60">
                        快去设置搭子的性格吧！
                      </p>
                    </div>
                  ) : (
                    <>
                  {/* Profile (top) */}
                  <div className="space-y-2">
                    <h3 className="text-sm font-semibold text-stardew-brown flex items-center gap-1.5">
                      <img src="/assets/icons/Achievements_Icon.png" alt="" className="w-3.5 h-3.5 pixel-art opacity-60" />
                      搭子档案
                    </h3>

                    {/* Personality traits */}
                    {customFlavor.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {customFlavor.map((f, i) => (
                          <span
                            key={i}
                            className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold cursor-default"
                            title={f.description}
                            style={{
                              background: "linear-gradient(180deg, #e8d5b7, #c9a96e)",
                              border: "1px solid #a08040",
                              color: "#5a3e1b",
                              boxShadow: "inset 0 1px 0 rgba(255,255,255,0.3)",
                            }}
                          >
                            {f.label}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="text-[12px] text-stardew-brown/50">
                        暂无自定义标签，当前使用下方搭子风格与偏好设置。
                      </p>
                    )}

                    {/* Relationship type */}
                    {relationshipType && (
                      <div className="flex items-center gap-2 text-[12px]">
                        <img src="/assets/icons/Marriage_Icon.png" alt="" className="w-3.5 h-3.5 pixel-art opacity-50 shrink-0" />
                        <span className="text-stardew-brown/55 font-bold w-14 shrink-0">关系</span>
                        <span className="text-stardew-brown font-bold">{relationshipType}</span>
                      </div>
                    )}

                    {/* Style summary */}
                    {styleSummary && (
                      <div className="flex items-start gap-2 text-[12px]">
                        <img src="/assets/icons/Speech_bubble.png" alt="" className="w-3.5 h-3.5 pixel-art opacity-50 shrink-0 mt-0.5" />
                        <span className="text-stardew-brown/55 font-bold w-14 shrink-0">风格</span>
                        <span className="text-stardew-brown/80 leading-relaxed">{styleSummary}</span>
                      </div>
                    )}
                  </div>

                  {/* Pixel dotted divider */}
                  <div
                    className="h-0 my-1"
                    style={{
                      borderTop: "2px dotted #8b691455",
                    }}
                  />

                  {/* Appearance (2-column grid) */}
                  <div>
                    <h3 className="text-sm font-semibold text-stardew-brown mb-1">
                      🎨 搭子外观
                    </h3>
                    <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                      <AppearanceCell icon="/assets/icons/The_Player_Icon.png" label="性别" value={charAppearance.gender === "Male" ? "♂ 男孩" : "♀ 女孩"} />
                      <AppearanceCell icon="/assets/icons/Forester.png" label="发型" value={`#${charAppearance.hairstyle + 1}`} />
                      <AppearanceCell icon="/assets/icons/Inventory_Icon.png" label="上衣" value={formatAppearanceIndex(charAppearance.shirtIndex)} />
                      <AppearanceCell icon="/assets/icons/Quests_Icon.png" label="裤子" value={formatAppearanceIndex(charAppearance.pantsIndex)} />
                      {charAppearance.hatIndex != null && (
                        <AppearanceCell icon="/assets/icons/All_Seasons_Icon.png" label="帽子" value={formatAppearanceIndex(charAppearance.hatIndex)} />
                      )}
                      <AppearanceCell icon="/assets/icons/Weather_Icon.png" label="发色" value={<ColorSwatch color={charAppearance.hairColor} />} />
                      <AppearanceCell icon="/assets/icons/Secret_Note_Icon.png" label="瞳色" value={<ColorSwatch color={charAppearance.eyeColor} />} />
                      <AppearanceCell icon="/assets/icons/Cooking_Icon.png" label="裤色" value={<ColorSwatch color={charAppearance.pantsColor} />} />
                    </div>
                  </div>
                    </>
                  )}
                </div>

                {/* Invite code — always visible */}
                {inviteStatus && (() => {
                  if (activeInviteCode) {
                    const inviteUrl = buildInviteUrl(activeInviteCode.code);
                    return (
                      <div className="parchment rounded-2xl p-3 md:p-4 space-y-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <div className="flex items-center gap-2 min-w-0 flex-1">
                            <img src="/assets/icons/Multiplayer_Icon.png" alt="" className="w-4 h-4 pixel-art opacity-60 shrink-0" />
                            <h3 className="text-sm font-semibold text-stardew-brown">
                              邀请链接
                            </h3>
                          </div>
                          <span className="rounded-full bg-stardew-wood/10 px-2 py-0.5 text-[10px] font-bold text-stardew-brown">
                            {formatInviteRemaining(activeInviteCode)}
                          </span>
                        </div>
                        <div className="grid grid-cols-3 gap-2">
                          <InviteStat label="已使用" value={`${activeInviteCode.used_count}`} />
                          <InviteStat
                            label="总名额"
                            value={activeInviteCode.usage_limit === null ? "不限" : `${activeInviteCode.usage_limit}`}
                          />
                          <InviteStat
                            label="剩余"
                            value={activeInviteCode.remaining_uses === null ? "不限" : `${activeInviteCode.remaining_uses}`}
                          />
                        </div>
                        <div className="space-y-2">
                          <div className="flex items-center gap-2 rounded-lg border border-stardew-wood/15 bg-stardew-cream/35 px-2.5 py-2">
                            <span className="w-14 shrink-0 text-[10px] font-bold text-stardew-brown/45">
                              邀请码
                            </span>
                            <p className="min-w-0 flex-1 break-all font-mono text-[12px] font-bold text-stardew-brown">
                              {activeInviteCode.code}
                            </p>
                            <button
                              type="button"
                              onClick={() => handleCopyInviteCode(activeInviteCode.code)}
                              className="stardew-btn shrink-0 rounded-lg px-3 py-1.5 text-[10px] font-bold"
                            >
                              {copiedInviteLink === `code:${activeInviteCode.code}` ? "已复制" : "复制"}
                            </button>
                          </div>
                          <div className="flex items-center gap-2 rounded-lg border border-stardew-wood/15 bg-stardew-cream/35 px-2.5 py-2">
                            <span className="w-14 shrink-0 text-[10px] font-bold text-stardew-brown/45">
                              邀请链接
                            </span>
                            <p className="min-w-0 flex-1 truncate font-mono text-[12px] text-stardew-brown/60">
                              {inviteUrl}
                            </p>
                            <button
                              type="button"
                              onClick={() => handleCopyInviteLink(activeInviteCode.code)}
                              className="stardew-btn shrink-0 rounded-lg px-3 py-1.5 text-[10px] font-bold"
                            >
                              {copiedInviteLink === `link:${activeInviteCode.code}` ? "已复制" : "复制"}
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  }
                  return (
                    <div className="parchment rounded-2xl p-3 md:p-4 space-y-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="flex items-center gap-2 min-w-0 flex-1">
                          <img src="/assets/icons/Multiplayer_Icon.png" alt="" className="w-4 h-4 pixel-art opacity-60 shrink-0" />
                          <h3 className="text-sm font-semibold text-stardew-brown">
                            邀请链接
                          </h3>
                        </div>
                        <span className="rounded-full bg-stardew-red/10 px-2 py-0.5 text-[10px] font-bold text-stardew-red">
                          {exhaustedInviteCode ? "名额已用完" : "暂无邀请码"}
                        </span>
                      </div>
                      {exhaustedInviteCode ? (
                        <>
                          <div className="grid grid-cols-3 gap-2">
                            <InviteStat label="已使用" value={`${exhaustedInviteCode.used_count}`} />
                            <InviteStat
                              label="总名额"
                              value={exhaustedInviteCode.usage_limit === null ? "不限" : `${exhaustedInviteCode.usage_limit}`}
                            />
                            <InviteStat
                              label="剩余"
                              value={exhaustedInviteCode.remaining_uses === null ? "不限" : `${exhaustedInviteCode.remaining_uses}`}
                            />
                          </div>
                          {exhaustedInviteCode.code ? (
                            <p className="text-[12px] font-mono text-stardew-brown/45 break-all">
                              {exhaustedInviteCode.code}
                            </p>
                          ) : null}
                        </>
                      ) : (
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[12px] text-stardew-brown/50">暂无可用邀请码</span>
                          {inviteStatus.canGenerate && (
                            <button
                              onClick={handleGenerateInviteBatch}
                              disabled={generatingBatch}
                              className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold disabled:opacity-50"
                            >
                              {generatingBatch ? "生成中..." : "生成邀请码"}
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })()}

                {/* Invite records */}
                {inviteStatus && (
                  <div className="parchment rounded-2xl p-3 md:p-4 space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="flex items-center gap-2 min-w-0 flex-1">
                        <img src="/assets/icons/Achievements_Icon.png" alt="" className="w-4 h-4 pixel-art opacity-60 shrink-0" />
                        <h3 className="text-sm font-semibold text-stardew-brown">
                          邀请记录
                        </h3>
                      </div>
                      <span className="rounded-full bg-stardew-wood/10 px-2 py-0.5 text-[10px] font-bold text-stardew-brown">
                        {inviteStatus.referrals.length} 人
                      </span>
                    </div>
                    {inviteStatus.referrals.length === 0 ? (
                      <p className="text-[12px] text-stardew-brown/50">暂无邀请记录</p>
                    ) : (
                      <div className="space-y-2">
                        {inviteStatus.referrals.map((referral) => (
                          <div
                            key={referral.id}
                            className="rounded-lg border border-stardew-wood/15 bg-stardew-cream/35 px-2.5 py-2"
                          >
                            <div className="flex flex-col sm:flex-row sm:items-start gap-2">
                              <div className="min-w-0 flex-1">
                                <p className="truncate text-[12px] font-bold text-stardew-brown">
                                  {formatReferralName(referral)}
                                </p>
                                <p className="mt-0.5 text-[10px] text-stardew-brown/45">
                                  {formatDate(referral.created_at)}
                                  {referral.invite_code ? ` · ${referral.invite_code}` : ""}
                                </p>
                              </div>
                              <div className="flex flex-wrap gap-1.5 sm:justify-end">
                                <RewardStatusPill label="我的奖励" status={referral.inviter_reward_status} />
                                <RewardStatusPill label="对方奖励" status={referral.invitee_reward_status} />
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        )}

        {/* ═══════════════════════════════════════════════════════════ */}
        {/*  Sign out                                                       */}
        {/* ═══════════════════════════════════════════════════════════ */}

        <div className="pt-1">
          <button
            onClick={handleSignOut}
            className="px-4 py-2 rounded-xl border-2 border-stardew-red/40 bg-stardew-red/10 text-stardew-red text-[12px] font-bold hover:bg-stardew-red/20 transition-colors"
          >
            退出登录
          </button>
        </div>
      </div>

      {/* QQ 群弹窗 */}
      {showQQModal && <QQGroupModal onClose={() => setShowQQModal(false)} />}

      {showInviteCard && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-3 py-6"
          onClick={() => setShowInviteCard(false)}
        >
          <div
            className="max-h-full overflow-y-auto rounded-2xl bg-stardew-cream p-4 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-stardew-brown">搭子名片</h3>
              <button
                onClick={() => setShowInviteCard(false)}
                className="rounded-lg border-2 border-stardew-wood/25 bg-stardew-cream px-3 py-1 text-[12px] font-bold text-stardew-brown"
              >
                关闭
              </button>
            </div>
            <InviteShareCard
              agentName={agenteeName ?? "小枫"}
              appearance={charAppearance}
              inviteCode={activeInviteCode?.code ?? null}
              inviteCodeState={inviteCodeState}
              relationshipType={relationshipType ?? undefined}
              styleSummary={styleSummary ?? undefined}
              corePreferences={corePreferences ?? undefined}
              customFlavor={customFlavor}
            />
          </div>
        </div>
      )}

      {/* ── Scoped SDV styles ── */}
      {avatarPickerDialog}

      <style jsx>{`
        .sdv-scroll {
          background: linear-gradient(135deg, #2c1810 0%, #3e2723 40%, #2c1810 100%);
          box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.4), 0 1px 0 rgba(255, 255, 255, 0.05);
        }
      `}</style>
    </div>
  );
}

/* ================================================================== */
/*  Helper Components                                                  */
/* ================================================================== */

function buildInviteUrl(code: string) {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return `${origin}/register?ref=${code}`;
}

function formatInviteRemaining(code: InviteCodeStatus) {
  if (code.remaining_uses === null) return "不限名额";
  return code.remaining_uses > 0 ? `剩余 ${code.remaining_uses} 次` : "名额已用完";
}

function formatReferralName(referral: InviteReferralStatus) {
  return (
    referral.referee_nickname?.trim() ||
    referral.referee_email?.trim() ||
    referral.referee_user_id
  );
}

function rewardStatusText(status: string) {
  if (status === "succeeded") return "已到账";
  if (status === "failed") return "发放失败";
  if (status === "pending") return "待发放";
  if (!status) return "待发放";
  return status;
}

function rewardStatusClassName(status: string) {
  if (status === "succeeded") return "bg-stardew-green/10 text-stardew-green";
  if (status === "failed") return "bg-stardew-red/10 text-stardew-red";
  return "bg-stardew-wood/10 text-stardew-brown";
}

function InviteStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-stardew-wood/15 bg-stardew-cream/35 px-2 py-1.5">
      <p className="text-[10px] font-bold text-stardew-brown/45">{label}</p>
      <p className="mt-0.5 text-[13px] font-bold text-stardew-brown">{value}</p>
    </div>
  );
}

function RewardStatusPill({ label, status }: { label: string; status: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold ${rewardStatusClassName(status)}`}
    >
      {label}：{rewardStatusText(status)}
    </span>
  );
}

/** Info row inside parchment (dark text on light bg) */
function InfoRow({
  icon,
  label,
  noBorder,
  children,
}: {
  icon: string;
  label: string;
  noBorder?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`flex items-center gap-3 py-2.5 ${
        noBorder ? "" : "border-b border-stardew-wood/15"
      }`}
    >
      <img src={icon} alt="" className="w-5 h-5 pixel-art opacity-60 shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-[10px] text-stardew-brown/50 font-bold">{label}</p>
        {children}
      </div>
    </div>
  );
}

/** Info row inside parchment (dark text on light bg) */
function InfoRowLight({
  icon,
  label,
  value,
  copyable,
  children,
}: {
  icon: string;
  label: string;
  value?: string;
  copyable?: boolean;
  children?: React.ReactNode;
}) {
  const [copied, setCopied] = useState(false);

  return (
    <div
      className={`flex items-center gap-3 py-1.5 ${copyable ? "cursor-pointer" : ""}`}
      onClick={
        copyable && value
          ? () => {
              void navigator.clipboard.writeText(value);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }
          : undefined
      }
    >
      <img src={icon} alt="" className="w-4 h-4 pixel-art opacity-60 shrink-0" />
      <span className="text-[10px] text-stardew-brown/50 font-bold w-14 shrink-0">
        {label}
      </span>
      {children ?? (
        <span className="text-[12px] text-stardew-brown font-bold break-all">
          {value || "—"}
        </span>
      )}
      {copied && (
        <span className="text-[10px] text-stardew-wood font-bold">已复制</span>
      )}
    </div>
  );
}

/** Appearance cell — for 2-column grid layout, strict baseline alignment */
function AppearanceCell({
  icon,
  label,
  value,
}: {
  icon?: string;
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-1.5 py-1 px-1 rounded-md hover:bg-stardew-wood/[0.06] transition-colors">
      {icon && (
        <img
          src={icon}
          alt=""
          className="w-3.5 h-3.5 pixel-art opacity-60 shrink-0 self-center"
          style={{ imageRendering: "pixelated" }}
        />
      )}
      <span className="text-stardew-brown/55 font-bold shrink-0 w-7 self-center">{label}</span>
      <span className="text-stardew-brown font-bold truncate self-center min-w-0">{value}</span>
    </div>
  );
}

/** Color swatch */
function ColorSwatch({ color }: { color: { h: number; s: number; b: number } }) {
  return (
    <div className="flex items-center gap-1.5">
      <div
        className="w-4 h-4 rounded border-2 border-stardew-wood/30 pixel-art"
        style={{ backgroundColor: hsbToHex(color) }}
      />
      <span className="text-[11px] text-stardew-brown/50">
        {color.h}/{color.s}/{color.b}
      </span>
    </div>
  );
}

function formatAppearanceIndex(index: number) {
  return index < 0 ? "无" : `#${index + 1}`;
}
