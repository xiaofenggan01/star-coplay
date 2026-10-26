"use client";

import type { ReactNode } from "react";
import { Fragment, useState, useEffect, useCallback } from "react";
import { FN, fnAdminHeaders } from "@/lib/supabase";
import QrCodePanel from "@/components/QrCodePanel";
import SystemConfigTab from "./_system-config-tab";

// ─── Types ────────────────────────────────────────────────────────────────────

interface User {
  user_id: string;
  platform_token: string;
  nickname: string | null;
  phone: string | null;
  wechat: string | null;
  email: string | null;
  invite_code_used: string;
  created_at: string;
  source: string | null;
  user_type: string | null;
  invite_source: string | null;
  invite_code_summary?: UserInviteSummary;
  credit_summary?:
    | {
        status: "available";
        available_credits: number;
        used_credits: number;
        available_percent: number;
      }
    | {
        status: "not_provisioned";
        reason_code: string;
      }
    | {
        status: "unavailable";
        reason_code: string;
      }
    | null;
}

interface InvitedUser {
  user_id: string;
  email: string | null;
  nickname: string | null;
  created_at: string | null;
}

interface UserInviteCode {
  code: string;
  used_count: number;
  max_uses: number | null;
  usage_limit?: number | null;
  remaining_uses?: number | null;
  disabled: boolean;
  invited_users: InvitedUser[];
}

interface UserInviteSummary {
  codes: UserInviteCode[];
  invited_user_count: number;
  invited_users: InvitedUser[];
}

interface Stats {
  by_source: { source: string; count: number }[];
  by_day: { day: string; count: number }[];
}

interface EventTypeRow {
  event_type: string;
  cnt: number;
}

interface UserReport {
  user_id: string;
  ts: string;
  session_id: string | null;
  data: {
    message?: { text?: string };
    game_context?: {
      player_pos?: { x?: number; y?: number };
      energy?: number;
      location?: string;
      game_time?: string;
    };
  } | null;
}

interface DashboardData {
  dau: { day: string; dau: number }[];
  avg_session_duration: { day: string; avg_duration_min: number }[];
  retention: { cohort_date: string; cohort_size: number; d1: number; d3: number; d7: number }[];
  onboard_funnel: { step: string; user_count: number }[];
  event_stats: EventTypeRow[];
  freshness?: {
    status: "available" | "unavailable";
    latest_event_at: string | null;
    latest_event_type: string | null;
    events_in_window: number | null;
    window_days: number;
    server_now: string;
    error_code: string | null;
  };
}

interface LlmStats {
  total_calls: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  total_tokens_in: number;
  total_tokens_out: number;
  model_distribution: Record<string, number>;
}

interface ActionStatRow {
  action_type: string;
  total: number;
  success_count: number;
  success_rate: number;
}

interface ErrorRow {
  error_type: string;
  error_count: number;
  latest_ts: string;
  sample_msg: string | null;
}

interface ActiveUser {
  user_id: string;
  nickname: string | null;
  total_sessions: number;
  total_duration_min: number;
  total_messages: number;
  last_active: string;
}

interface UserProfile {
  user_id: string;
  nickname: string | null;
  source: string | null;
  created_at: string;
  total_sessions: number;
  total_duration_min: number;
  total_messages: number;
  total_llm_calls: number;
  total_actions: number;
  total_errors: number;
  first_session: string | null;
  last_session: string | null;
}

interface ConversationMsg {
  ts: string;
  type: string;
  session_id: string | null;
  data: Record<string, unknown> | null;
}

interface TimelineEvent {
  ts: string;
  type: string;
  session_id: string | null;
  data: Record<string, unknown> | null;
}

type WaitlistStatus = "pending" | "approved" | "rejected" | "registered" | "unknown";
type WaitlistFilter = WaitlistStatus | "all";

interface WaitlistEntry {
  id: number;
  email: string | null;
  wechat: string | null;
  qq: string | null;
  message: string | null;
  created_at: string;
  status: WaitlistStatus;
}

interface WaitlistUserDetail {
  status: string;
  email_sent: boolean;
  resend_email_id: string | null;
  password_set: boolean;
  password_set_at: string | null;
  first_login: boolean;
  first_login_at: string | null;
}

interface OfficialInviteUser {
  user_id: string;
  nickname: string | null;
  email: string | null;
  phone: string | null;
  wechat: string | null;
  user_type: string | null;
  source: string | null;
  usage_source?: "users.invite_code_used" | "user_relations";
  created_at: string;
}

interface OfficialInviteCode {
  code: string;
  type: "official";
  owner_user_id: null;
  max_uses: number | null;
  used_count: number;
  official_code_reason: string | null;
  created_at: string;
  expires_at: string | null;
  disabled: boolean;
  invited_users: OfficialInviteUser[];
  usage: {
    used_count: number;
    invited_user_count: number;
    max_uses: number | null;
    remaining: number | null;
    usage_rate: number | null;
    status: "active" | "disabled" | "expired" | "exhausted";
  };
}

interface OfficialInviteSummary {
  total_codes: number;
  active_codes: number;
  disabled_codes: number;
  exhausted_codes: number;
  total_capacity: number | null;
  total_used: number;
  total_remaining: number | null;
  invited_user_count: number;
}

interface OfficialInviteCreateForm {
  quantity: number;
  maxUses: number;
  prefix: string;
  reason: string;
}

interface AgentInstanceRow {
  agent_instance_id: string;
  user_id: string | null;
  worker_id: string | null;
  connection_id: string | null;
  target_player_id: number | null;
  state: string;
  state_changed_at: string;
  created_at: string;
  last_game_event_at: string | null;
  command_inflight_count: number;
  ended_reason: string | null;
  updated_at: string;
  stale_seconds: number | null;
}

interface RoadshowRoute {
  route_key: string;
  title: string;
  enabled: boolean;
  target_user_id: string | null;
  target_agent_instance_id: string | null;
  created_at: string;
  updated_at: string;
}

interface RoadshowInput {
  input_id: string;
  route_key: string;
  user_id: string;
  text: string;
  sanitized_text: string | null;
  received_at: string;
  agent_instance_id: string | null;
  delivery_status: string;
  error: string | null;
}

interface RoadshowDeliveryEvent {
  id: number;
  input_id: string;
  route_key: string;
  user_id: string | null;
  agent_instance_id: string | null;
  event_type: string;
  delivery_status: string;
  payload_type: string;
  sanitized_text: string | null;
  payload: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
}

interface RoadshowLookupUser {
  user_id: string;
  platform_token: string;
  nickname: string | null;
  phone: string | null;
  wechat: string | null;
  email: string | null;
  created_at: string;
}

interface RoadshowLookupData {
  user: RoadshowLookupUser;
  instances: AgentInstanceRow[];
}

type LiveMode = "alive" | "recent";

interface LiveInstancesData {
  mode: LiveMode;
  window_min: number;
  instances: AgentInstanceRow[];
  summary: {
    total: number;
    by_state: Record<string, number>;
    inflight_commands: number;
    server_now: string;
  };
}

interface HostedRuntimeHealth {
  schema_version?: number;
  observed_at?: string;
  ok?: boolean;
  component?: string;
  runtime?: { active_count?: number; owned_handle_count?: number; orphan_candidate_count?: number; quarantined_count?: number };
  provider?: { adapter_kind?: string | null; last_failure_at?: string | null; last_failure_code?: string | null };
  workers?: Record<string, { last_success_at?: string | null; last_error_at?: string | null; last_error_code?: string | null; last_batch_size?: number | null }>;
  outboxes?: Record<string, { pending_count?: number | null; claimed_count?: number | null; oldest_pending_age_seconds?: number | null; available?: boolean; error_code?: string | null }>;
  sinks?: Record<string, { configured?: boolean; writable?: boolean | null; last_success_at?: string | null; last_error_code?: string | null }>;
  hosted?: {
    gateway?: {
      summary?: { controller?: number; observer?: number; reconnecting?: number };
      sessions?: Array<{ role?: string; transport_state?: string; target_player_id?: string; control_revision?: number }>;
      last_takeover?: { observed_at?: string; outcome?: string; target_player_id?: string; revision?: number } | null;
    };
    input?: {
      queue?: { total?: number; compensation?: number; player?: number; background?: number };
      delivery?: { silent_turns?: number; compensation_delivered?: number; compensation_failed?: number; last_outcome?: string | null };
    };
  };
}

interface AIRelayAccount {
  user_id: string;
  newapi_user_id: number;
  newapi_token_id: number | null;
  has_token_key: boolean;
  has_user_access_token: boolean;
  group: string;
  preferred_models: Record<string, string>;
  credential_mode: string;
  status: string;
  last_sync_error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface AIRelayQuota {
  user_id: string;
  newapi_user_id: number;
  newapi_token_id: number | null;
  group: string;
  status: string;
  quota: number;
  used_quota: number;
  request_count: number;
  token?: {
    id: number | null;
    name?: string | null;
    unlimited_quota?: boolean;
    total_granted?: number | null;
    total_used?: number | null;
    total_available?: number | null;
  };
}

interface AIRelayLogItem {
  id?: number;
  created_at?: number;
  type?: number;
  content?: string;
  token_name?: string;
  model_name?: string;
  quota?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  use_time?: number;
  channel?: number;
  channel_name?: string;
  token_id?: number;
  group?: string;
  request_id?: string;
}

interface AIRelayLogPage {
  user_id: string;
  page: number;
  page_size: number;
  total: number;
  items: AIRelayLogItem[];
  included_log_types?: number[];
  excluded_log_types?: number[];
}

interface AIRelayUsageSummary {
  user_id: string;
  newapi_user_id?: number;
  quota: number;
  rpm: number;
  tpm: number;
  buckets: {
    created_at?: number;
    model_name?: string;
    count: number;
    quota: number;
    token_used: number;
  }[];
}

interface AIRelayHealth {
  checked_at?: number;
  newapi_status?: Record<string, unknown>;
  server_test?: { success?: boolean; message?: string; http_stats?: Record<string, unknown> };
  groups?: string[];
  models?: {
    enabled?: string[];
    channel_catalog?: unknown[];
  };
  channels?: {
    total?: number;
    sample_size?: number;
    by_status?: Record<string, number>;
    items?: {
      id?: number;
      name?: string;
      status?: number;
      group?: string;
      models?: string;
      balance?: number;
      response_time?: number;
      test_time?: number;
      is_multi_key?: boolean;
      multi_key_size?: number;
    }[];
  };
}

interface AIRelayQuotaAdjustmentForm {
  mode: string;
  quotaUsd: number;
  reason: string;
}

interface AIRelayQuotaAdjustmentResult {
  idempotency_key?: string;
  mode?: string;
  quota?: number;
  quota_usd?: number;
  quota_per_usd?: number;
  status?: string;
  duplicate?: boolean;
  quota_summary?: AIRelayQuota;
}


type TabKey = "users" | "dashboard" | "deep-dive" | "live" | "hosted-runtime" | "roadshow" | "waitlist" | "official-invites" | "ai-relay" | "system-config";

const CONVO_PAGE_SIZE = 50;

// ─── Main Component ───────────────────────────────────────────────────────────

export default function AdminPage() {
  const [secret, setSecret] = useState("");
  const [authed, setAuthed] = useState(false);
  const [activeTab, setActiveTab] = useState<TabKey>("users");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  // Tab: users
  const [users, setUsers] = useState<User[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<Stats | null>(null);
  const [userReports, setUserReports] = useState<UserReport[]>([]);
  const [usersLoaded, setUsersLoaded] = useState(false);
  const [userSearch, setUserSearch] = useState("");
  const [userSearchDraft, setUserSearchDraft] = useState("");
  const [userPage, setUserPage] = useState(1);
  const [userPageSize, setUserPageSize] = useState(50);
  const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);
  const [upgradingUserId, setUpgradingUserId] = useState<string | null>(null);
  const [grantingCreditsUserId, setGrantingCreditsUserId] = useState<string | null>(null);
  const [batchGrantingCredits, setBatchGrantingCredits] = useState(false);
  const [userUpgradeToast, setUserUpgradeToast] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  // Tab: dashboard
  const [dashData, setDashData] = useState<DashboardData | null>(null);
  const [llmStats, setLlmStats] = useState<LlmStats | null>(null);
  const [actionStats, setActionStats] = useState<ActionStatRow[]>([]);
  const [errorRanking, setErrorRanking] = useState<ErrorRow[]>([]);
  const [dashLoaded, setDashLoaded] = useState(false);

  // Tab: deep-dive
  const [activeUsers, setActiveUsers] = useState<ActiveUser[]>([]);
  const [deepDiveLoaded, setDeepDiveLoaded] = useState(false);
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [userProfile, setUserProfile] = useState<UserProfile | null>(null);
  const [conversations, setConversations] = useState<ConversationMsg[]>([]);
  const [convoPage, setConvoPage] = useState(0);
  const [convoTotal, setConvoTotal] = useState(0);
  const [convoListLoading, setConvoListLoading] = useState(false);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [userDetailLoading, setUserDetailLoading] = useState(false);
  const [searchUserId, setSearchUserId] = useState("");
  const [dashDays, setDashDays] = useState(30);

  // Tab: waitlist
  const [waitlistEntries, setWaitlistEntries] = useState<WaitlistEntry[]>([]);
  const [waitlistTotal, setWaitlistTotal] = useState(0);
  const [waitlistLoaded, setWaitlistLoaded] = useState(false);
  const [waitlistFilter, setWaitlistFilter] = useState<WaitlistFilter>("pending");
  const [waitlistMutatingId, setWaitlistMutatingId] = useState<number | null>(null);
  const [waitlistToast, setWaitlistToast] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);
  const [waitlistPage, setWaitlistPage] = useState(1);
  const [waitlistPageSize, setWaitlistPageSize] = useState(50);
  const [waitlistSearch, setWaitlistSearch] = useState("");
  const [waitlistSearchDebounced, setWaitlistSearchDebounced] = useState("");

  // Waitlist user detail modal
  const [waitlistDetailId, setWaitlistDetailId] = useState<number | null>(null);
  const [waitlistDetail, setWaitlistDetail] = useState<WaitlistUserDetail | null>(null);
  const [waitlistDetailLoading, setWaitlistDetailLoading] = useState(false);
  const [waitlistDetailErr, setWaitlistDetailErr] = useState("");

  // Tab: official invites
  const [officialInviteCodes, setOfficialInviteCodes] = useState<OfficialInviteCode[]>([]);
  const [officialInviteSummary, setOfficialInviteSummary] = useState<OfficialInviteSummary | null>(null);
  const [officialInvitesLoaded, setOfficialInvitesLoaded] = useState(false);
  const [officialInviteBusy, setOfficialInviteBusy] = useState(false);
  const [officialInviteToast, setOfficialInviteToast] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);
  const [officialInviteCreateForm, setOfficialInviteCreateForm] = useState<OfficialInviteCreateForm>({
    quantity: 5,
    maxUses: 20,
    prefix: "OFFICIAL",
    reason: "official-admin-batch",
  });

  // Tab: live
  const [liveData, setLiveData] = useState<LiveInstancesData | null>(null);
  const [liveLoaded, setLiveLoaded] = useState(false);
  const [liveAutoRefresh, setLiveAutoRefresh] = useState(true);
  const [liveWindowMin, setLiveWindowMin] = useState(30);
  const [liveMode, setLiveMode] = useState<LiveMode>("alive");
  const [liveUpdatedAt, setLiveUpdatedAt] = useState<number | null>(null);
  const [gwStatus, setGwStatus] = useState<{current_connections: number, max_connections: number, accepting: boolean} | null>(null);
  const [hostedHealth, setHostedHealth] = useState<HostedRuntimeHealth | null>(null);
  const [hostedHealthLoaded, setHostedHealthLoaded] = useState(false);

  // Tab: roadshow
  const [roadshowRoutes, setRoadshowRoutes] = useState<RoadshowRoute[]>([]);
  const [roadshowInputs, setRoadshowInputs] = useState<RoadshowInput[]>([]);
  const [roadshowDeliveryEvents, setRoadshowDeliveryEvents] = useState<RoadshowDeliveryEvent[]>([]);
  const [roadshowLoaded, setRoadshowLoaded] = useState(false);
  const [roadshowToken, setRoadshowToken] = useState("");
  const [roadshowLookup, setRoadshowLookup] = useState<RoadshowLookupData | null>(null);
  const [roadshowRouteKey, setRoadshowRouteKey] = useState("main");
  const [roadshowTitle, setRoadshowTitle] = useState("主展示页路演入口");
  const [roadshowSelectedInstance, setRoadshowSelectedInstance] = useState("");
  const [roadshowEnabled, setRoadshowEnabled] = useState(true);
  const [roadshowSaving, setRoadshowSaving] = useState(false);
  const [roadshowStatus, setRoadshowStatus] = useState("");
  const [roadshowQuery, setRoadshowQuery] = useState("");
  const [roadshowFilterRoute, setRoadshowFilterRoute] = useState("");
  const [roadshowFilterStatus, setRoadshowFilterStatus] = useState("");
  const [roadshowFrom, setRoadshowFrom] = useState("");
  const [roadshowTo, setRoadshowTo] = useState("");

  // Tab: ai-relay
  const [aiRelayUserId, setAiRelayUserId] = useState("");
  const [aiRelayAccount, setAiRelayAccount] = useState<AIRelayAccount | null>(null);
  const [aiRelayLoaded, setAiRelayLoaded] = useState(false);
  const [aiRelayBusy, setAiRelayBusy] = useState(false);
  const [aiRelayGroup, setAiRelayGroup] = useState("");
  const [aiRelayStatus, setAiRelayStatus] = useState("active");
  const [aiRelayPreferences, setAiRelayPreferences] = useState("{}");
  const [aiRelayEmail, setAiRelayEmail] = useState("");
  const [aiRelayDisplayName, setAiRelayDisplayName] = useState("");
  const [aiRelayQuota, setAiRelayQuota] = useState<AIRelayQuota | null>(null);
  const [aiRelayLedger, setAiRelayLedger] = useState<AIRelayLogPage | null>(null);
  const [aiRelayUsageLogs, setAiRelayUsageLogs] = useState<AIRelayLogPage | null>(null);
  const [aiRelayUsageSummary, setAiRelayUsageSummary] = useState<AIRelayUsageSummary | null>(null);
  const [aiRelayHealth, setAiRelayHealth] = useState<AIRelayHealth | null>(null);
  const [aiRelayQuotaAdjustment, setAiRelayQuotaAdjustment] = useState<AIRelayQuotaAdjustmentResult | null>(null);

  useEffect(() => {
    const s = sessionStorage.getItem("admin_secret");
    if (s) {
      setSecret(s);
      setAuthed(true);
    }
  }, []);

  // ─── Data fetchers ────────────────────────────────────────────────────────

  const fetchUsers = useCallback(async (searchOverride?: string, pageOverride = userPage, pageSizeOverride = userPageSize) => {
    setLoading(true);
    setErr("");
    try {
      const h = fnAdminHeaders(secret);
      const query = (searchOverride ?? userSearch).trim();
      const usersUrl = `${FN.admin}?action=users&page=${pageOverride}&pageSize=${pageSizeOverride}${query ? `&q=${encodeURIComponent(query)}` : ""}`;
      const [uRes, sRes, rRes] = await Promise.all([
        fetch(usersUrl, { headers: h }),
        fetch(`${FN.admin}?action=stats`, { headers: h }),
        fetch(`${FN.admin}?action=user_reports`, { headers: h }),
      ]);
      const uData = await uRes.json();
      const sData = await sRes.json();
      const rData = await rRes.json();
      if (!uRes.ok) throw new Error(uData.error ?? "用户列表请求失败");
      if (!sRes.ok) throw new Error(sData.error ?? "用户统计请求失败");
      setUsers(uData.users ?? []);
      setSelectedUserIds([]);
      setTotal(uData.total ?? 0);
      setStats({ by_source: sData.by_source ?? [], by_day: sData.by_day ?? [] });
      setUserReports(rData.reports ?? []);
      setUsersLoaded(true);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "请求失败");
    } finally {
      setLoading(false);
    }
  }, [secret, userSearch, userPage, userPageSize]);

  const applyUserSearch = useCallback(async () => {
    const query = userSearchDraft.trim();
    setUserSearch(query);
    setUserPage(1);
    await fetchUsers(query, 1, userPageSize);
  }, [fetchUsers, userSearchDraft]);

  const clearUserSearch = useCallback(async () => {
    setUserSearch("");
    setUserSearchDraft("");
    setUserPage(1);
    await fetchUsers("", 1, userPageSize);
  }, [fetchUsers]);

  useEffect(() => {
    setUsersLoaded(false);
  }, [userPage, userPageSize]);

  const changeUserPage = useCallback((page: number) => {
    setUserPage(page);
  }, []);

  const changeUserPageSize = useCallback((pageSize: number) => {
    setUserPage(1);
    setUserPageSize(pageSize);
  }, []);

  const upgradeGuestUser = useCallback(async (user: User) => {
    if (user.user_type !== "guest") {
      setUserUpgradeToast({ kind: "err", msg: "只有游客用户可以通过此入口升级为内测用户。" });
      return;
    }
    if (!confirm(`确定为 ${user.nickname ?? user.email ?? user.user_id} 升级为内测用户？系统会同步开通 AI Relay、个人邀请码和用户关系。`)) {
      return;
    }
    const reason = prompt("请输入本次操作原因，便于审计追踪：", "运营手动开通内测资格")?.trim();
    if (!reason) {
      setUserUpgradeToast({ kind: "err", msg: "操作已取消：必须填写操作原因。" });
      return;
    }

    setUpgradingUserId(user.user_id);
    setUserUpgradeToast(null);
    setErr("");
    try {
      const res = await fetch(`${FN.admin}?action=user_upgrade_to_beta`, {
        method: "POST",
        headers: { "x-admin-secret": secret, "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: user.user_id, reason }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "升级用户失败");

      const ownInviteCode = json.open_access?.invite_code ? `，个人邀请码 ${json.open_access.invite_code}` : "";
      const waitlistText = json.waitlist_id ? `，waitlist #${json.waitlist_id} 已标记 registered` : "";
      setUserUpgradeToast({ kind: "ok", msg: `已升级为内测用户${ownInviteCode}${waitlistText}` });
      await fetchUsers();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "升级用户失败";
      setErr(msg);
      setUserUpgradeToast({ kind: "err", msg });
    } finally {
      setUpgradingUserId(null);
    }
  }, [secret, fetchUsers]);

  const grantUserCredits = useCallback(async (user: User) => {
    const label = user.nickname ?? user.email ?? user.user_id;
    const creditsInput = prompt(`请输入要发放给 ${label} 的积分数：`, "20")?.trim();
    if (!creditsInput) {
      setUserUpgradeToast({ kind: "err", msg: "操作已取消：必须填写积分数。" });
      return;
    }
    const credits = Number(creditsInput);
    if (!Number.isFinite(credits) || credits <= 0 || credits > 100000) {
      setUserUpgradeToast({ kind: "err", msg: "积分数必须是 0 到 100000 之间的正数。" });
      return;
    }
    const reason = prompt("请输入本次积分发放原因，便于审计追踪。同一用户、积分数、原因在同一天会按同一笔发放幂等处理；确需重复发放请写不同工单或批次号。", "运营手动发放积分")?.trim();
    if (!reason) {
      setUserUpgradeToast({ kind: "err", msg: "操作已取消：必须填写发放原因。" });
      return;
    }
    if (!confirm(`确定给 ${label} 发放 ${credits} 积分？系统会通过 AI Relay 额度流水记账。`)) {
      return;
    }

    setGrantingCreditsUserId(user.user_id);
    setUserUpgradeToast(null);
    setErr("");
    try {
      const res = await fetch(`${FN.admin}?action=user_grant_credits`, {
        method: "POST",
        headers: { "x-admin-secret": secret, "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: user.user_id, credits, reason }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(readAdminError(json, "积分发放失败"));
      setUserUpgradeToast({
        kind: "ok",
        msg: `已给 ${label} 发放 ${json.credits ?? credits} 积分，Relay 原始额度 ${Number(json.quota ?? 0).toLocaleString("zh-CN")}`,
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "积分发放失败";
      setErr(msg);
      setUserUpgradeToast({ kind: "err", msg });
    } finally {
      setGrantingCreditsUserId(null);
    }
  }, [secret]);

  const grantSelectedUsersCredits = useCallback(async () => {
    if (selectedUserIds.length === 0) {
      setUserUpgradeToast({ kind: "err", msg: "请先勾选需要发放积分的用户。" });
      return;
    }
    const creditsInput = prompt(`请输入要批量发放给 ${selectedUserIds.length} 个用户的积分数：`, "20")?.trim();
    if (!creditsInput) {
      setUserUpgradeToast({ kind: "err", msg: "操作已取消：必须填写积分数。" });
      return;
    }
    const credits = Number(creditsInput);
    if (!Number.isFinite(credits) || credits <= 0 || credits > 100000) {
      setUserUpgradeToast({ kind: "err", msg: "积分数必须是 0 到 100000 之间的正数。" });
      return;
    }
    const reason = prompt("请输入本次批量积分发放原因，便于审计追踪。同一用户、积分数、原因在同一天会按同一笔发放幂等处理；确需重复发放请写不同工单或批次号。", "运营批量发放积分")?.trim();
    if (!reason) {
      setUserUpgradeToast({ kind: "err", msg: "操作已取消：必须填写发放原因。" });
      return;
    }
    if (!confirm(`确定给 ${selectedUserIds.length} 个用户各发放 ${credits} 积分？系统会逐个写入 AI Relay 额度流水。`)) {
      return;
    }

    setBatchGrantingCredits(true);
    setUserUpgradeToast(null);
    setErr("");
    try {
      const res = await fetch(`${FN.admin}?action=user_grant_credits_batch`, {
        method: "POST",
        headers: { "x-admin-secret": secret, "Content-Type": "application/json" },
        body: JSON.stringify({ user_ids: selectedUserIds, credits, reason }),
      });
      const json = await res.json();
      if (!res.ok && res.status !== 207) throw new Error(readAdminError(json, "批量积分发放失败"));
      const succeeded = Number(json.succeeded?.length ?? 0);
      const failed = Number(json.failed?.length ?? 0);
      setUserUpgradeToast({
        kind: failed > 0 ? "err" : "ok",
        msg: `批量积分发放完成：成功 ${succeeded} 个，失败 ${failed} 个。`,
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "批量积分发放失败";
      setErr(msg);
      setUserUpgradeToast({ kind: "err", msg });
    } finally {
      setBatchGrantingCredits(false);
    }
  }, [secret, selectedUserIds]);

  const fetchDashboard = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const h = fnAdminHeaders(secret);
      const d = dashDays;
      const [dashRes] = await Promise.all([
        fetch(`${FN.admin}?action=dashboard&days=${d}`, { headers: h }),
      ]);
      const dashJson = await dashRes.json();
      if (!dashRes.ok) throw new Error(dashJson.error ?? "数据看板请求失败");
      setDashData(dashJson);
      setLlmStats(null);
      setActionStats([]);
      setErrorRanking([]);
      setDashLoaded(true);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "请求失败");
    } finally {
      setLoading(false);
    }
  }, [secret, dashDays]);

  const fetchActiveUsers = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const h = fnAdminHeaders(secret);
      const res = await fetch(`${FN.admin}?action=active_users&days=${dashDays}`, { headers: h });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "活跃用户请求失败");
      setActiveUsers(json.users ?? []);
      setDeepDiveLoaded(true);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "请求失败");
    } finally {
      setLoading(false);
    }
  }, [secret, dashDays]);

  const fetchWaitlist = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const h = fnAdminHeaders(secret);
      const qs = `action=waitlist_list&status=${encodeURIComponent(waitlistFilter)}&page=${waitlistPage}&pageSize=${waitlistPageSize}${waitlistSearchDebounced ? `&q=${encodeURIComponent(waitlistSearchDebounced)}` : ""}`;
      const res = await fetch(`${FN.admin}?${qs}`, { headers: h });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "预约名单请求失败");
      setWaitlistEntries(json.waitlist ?? []);
      setWaitlistTotal(Number(json.total ?? 0));
      setWaitlistLoaded(true);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "请求失败");
    } finally {
      setLoading(false);
    }
  }, [secret, waitlistFilter, waitlistPage, waitlistPageSize, waitlistSearchDebounced]);

  const fetchWaitlistDetail = useCallback(async (waitlistId: number) => {
    setWaitlistDetailId(waitlistId);
    setWaitlistDetail(null);
    setWaitlistDetailErr("");
    setWaitlistDetailLoading(true);
    try {
      const h = fnAdminHeaders(secret);
      const res = await fetch(`${FN.admin}?action=waitlist_user_detail&waitlist_id=${waitlistId}`, { headers: h });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "查询失败");
      setWaitlistDetail(json.detail as WaitlistUserDetail);
    } catch (e: unknown) {
      setWaitlistDetailErr(e instanceof Error ? e.message : "查询失败");
    } finally {
      setWaitlistDetailLoading(false);
    }
  }, [secret]);

  const updateWaitlistStatus = useCallback(
    async (id: number, nextStatus: "pending" | "approved" | "rejected") => {
      const original = waitlistEntries;
      const target = original.find((e) => e.id === id);
      if (!target) return;
      const previousStatus = target.status;
      setWaitlistMutatingId(id);
      // 乐观更新：若当前过滤器不匹配新状态则从列表移除，否则就地改 status
      const optimistic =
        waitlistFilter === "all" || waitlistFilter === nextStatus
          ? original.map((e) => (e.id === id ? { ...e, status: nextStatus } : e))
          : original.filter((e) => e.id !== id);
      setWaitlistEntries(optimistic);
      try {
        const res = await fetch(`${FN.admin}?action=waitlist_update_status`, {
          method: "POST",
          headers: { ...fnAdminHeaders(secret), "Content-Type": "application/json" },
          body: JSON.stringify({
            id,
            status: nextStatus,
          }),
        });
        const json = await res.json();
        if (!res.ok) throw new Error(json.error ?? "更新失败");
        const actualStatus = typeof json.status === "string" ? json.status : nextStatus;
        setWaitlistEntries((rows) => {
          if (waitlistFilter !== "all" && waitlistFilter !== actualStatus) {
            return rows.filter((e) => e.id !== id);
          }
          return rows.map((e) => (e.id === id ? { ...e, status: actualStatus } : e));
        });
        setWaitlistToast({
          kind: "ok",
          msg: `#${id} 状态已更新为 ${actualStatus}`,
        });
        // 若过滤器排除了新状态，total 减 1；否则保持
        if (waitlistFilter !== "all" && waitlistFilter !== actualStatus) {
          setWaitlistTotal((t) => Math.max(0, t - 1));
          if (optimistic.length <= 1 && waitlistPage > 1) {
            setWaitlistPage((p) => p - 1);
          }
        }
      } catch (e: unknown) {
        // 回滚
        setWaitlistEntries(original.map((row) => (row.id === id ? { ...row, status: previousStatus } : row)));
        setWaitlistToast({
          kind: "err",
          msg: e instanceof Error ? e.message : "更新失败",
        });
      } finally {
        setWaitlistMutatingId(null);
      }
    },
    [secret, waitlistEntries, waitlistFilter, waitlistPage],
  );

  const fetchOfficialInvites = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await fetch(`${FN.admin}?action=invite_codes_list`, {
        headers: fnAdminHeaders(secret),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "官方邀请码请求失败");
      setOfficialInviteCodes((json.invite_codes ?? []) as OfficialInviteCode[]);
      setOfficialInviteSummary((json.summary ?? null) as OfficialInviteSummary | null);
      setOfficialInvitesLoaded(true);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "官方邀请码请求失败");
    } finally {
      setLoading(false);
    }
  }, [secret]);

  const createOfficialInvites = useCallback(async () => {
    setOfficialInviteBusy(true);
    setErr("");
    setOfficialInviteToast(null);
    try {
      const res = await fetch(`${FN.admin}?action=official_invite_batch_create`, {
        method: "POST",
        headers: { ...fnAdminHeaders(secret), "Content-Type": "application/json" },
        body: JSON.stringify({
          quantity: officialInviteCreateForm.quantity,
          max_uses: officialInviteCreateForm.maxUses,
          prefix: officialInviteCreateForm.prefix,
          official_code_reason: officialInviteCreateForm.reason,
        }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "批量创建失败");
      setOfficialInviteToast({
        kind: "ok",
        msg: `已创建 ${(json.invite_codes ?? []).length} 个官方邀请码`,
      });
      await fetchOfficialInvites();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "批量创建失败";
      setErr(msg);
      setOfficialInviteToast({ kind: "err", msg });
    } finally {
      setOfficialInviteBusy(false);
    }
  }, [secret, officialInviteCreateForm, fetchOfficialInvites]);

  const disableOfficialInvite = useCallback(async (code: string) => {
    if (!confirm(`确定禁用官方邀请码 ${code}？禁用后新注册将不能继续使用该码。`)) return;
    setOfficialInviteBusy(true);
    setErr("");
    setOfficialInviteToast(null);
    try {
      const res = await fetch(`${FN.admin}?action=official_invite_disable`, {
        method: "POST",
        headers: { ...fnAdminHeaders(secret), "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "禁用失败");
      setOfficialInviteToast({ kind: "ok", msg: `${code} 已禁用` });
      await fetchOfficialInvites();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "禁用失败";
      setErr(msg);
      setOfficialInviteToast({ kind: "err", msg });
    } finally {
      setOfficialInviteBusy(false);
    }
  }, [secret, fetchOfficialInvites]);

  const increaseOfficialInviteMaxUses = useCallback(async (code: string, additionalUses: number) => {
    setOfficialInviteBusy(true);
    setErr("");
    setOfficialInviteToast(null);
    try {
      const res = await fetch(`${FN.admin}?action=official_invite_increase_max_uses`, {
        method: "POST",
        headers: { ...fnAdminHeaders(secret), "Content-Type": "application/json" },
        body: JSON.stringify({ code, add_uses: additionalUses }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "扩容失败");
      setOfficialInviteToast({ kind: "ok", msg: `${code} 已增加 ${additionalUses} 次可用名额` });
      await fetchOfficialInvites();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "扩容失败";
      setErr(msg);
      setOfficialInviteToast({ kind: "err", msg });
    } finally {
      setOfficialInviteBusy(false);
    }
  }, [secret, fetchOfficialInvites]);

  // toast 自动消失
  useEffect(() => {
    if (!waitlistToast) return;
    const t = setTimeout(() => setWaitlistToast(null), 3000);
    return () => clearTimeout(t);
  }, [waitlistToast]);

  useEffect(() => {
    if (!officialInviteToast) return;
    const t = setTimeout(() => setOfficialInviteToast(null), 3000);
    return () => clearTimeout(t);
  }, [officialInviteToast]);

  useEffect(() => {
    const timer = setTimeout(() => setWaitlistSearchDebounced(waitlistSearch), 300);
    return () => clearTimeout(timer);
  }, [waitlistSearch]);

  useEffect(() => { setWaitlistPage(1); setWaitlistLoaded(false); }, [waitlistSearchDebounced, waitlistPageSize]);

  const fetchLive = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setErr("");
    try {
      const h = fnAdminHeaders(secret);
      const qs = liveMode === "recent"
        ? `action=live_instances&mode=recent&window_min=${liveWindowMin}`
        : `action=live_instances&mode=alive`;
      const res = await fetch(`${FN.admin}?${qs}`, { headers: h });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "实时实例请求失败");
      setLiveData(json);
      setLiveLoaded(true);
      setLiveUpdatedAt(Date.now());
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "请求失败");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [secret, liveWindowMin, liveMode]);

  const fetchGwStatus = useCallback(async () => {
    try {
      const res = await fetch(`${FN.admin}?action=gateway_status`, {
        headers: fnAdminHeaders(secret),
      });
      if (res.ok) setGwStatus(await res.json());
    } catch { /* ignore */ }
  }, [secret]);

  const fetchHostedHealth = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await fetch(`${FN.admin}?action=hosted_runtime_health`, {
        headers: fnAdminHeaders(secret),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error?.message ?? json?.error ?? "Hosted Runtime 健康检查失败");
      setHostedHealth(json as HostedRuntimeHealth);
      setHostedHealthLoaded(true);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Hosted Runtime 健康检查失败");
    } finally {
      setLoading(false);
    }
  }, [secret]);

  function applyAIRelayAccount(account: AIRelayAccount | null) {
    setAiRelayAccount(account);
    setAiRelayLoaded(true);
    if (!account) return;
    setAiRelayUserId(account.user_id);
    setAiRelayGroup(account.group);
    setAiRelayStatus(account.status);
    setAiRelayPreferences(JSON.stringify(account.preferred_models ?? {}, null, 2));
  }

  const fetchAIRelayQuota = useCallback(async (userId?: string) => {
    const targetUserId = (userId ?? aiRelayUserId).trim();
    if (!targetUserId) {
      setErr("请输入平台用户 ID（user_id）");
      return;
    }
    setAiRelayBusy(true);
    setErr("");
    try {
      const res = await fetch(
        `${FN.admin}?action=ai_relay_quota&user_id=${encodeURIComponent(targetUserId)}`,
        { headers: fnAdminHeaders(secret) },
      );
      const json = await res.json();
      if (!res.ok) throw new Error(readAdminError(json, "AI 中转额度查询失败"));
      setAiRelayQuota((json.data ?? null) as AIRelayQuota | null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "AI 中转额度查询失败");
    } finally {
      setAiRelayBusy(false);
    }
  }, [secret, aiRelayUserId]);

  const fetchAIRelayLedger = useCallback(async (userId?: string) => {
    const targetUserId = (userId ?? aiRelayUserId).trim();
    if (!targetUserId) {
      setErr("请输入平台用户 ID（user_id）");
      return;
    }
    setAiRelayBusy(true);
    setErr("");
    try {
      const params = new URLSearchParams({
        action: "ai_relay_quota_ledger",
        user_id: targetUserId,
        page: "1",
        page_size: "20",
      });
      const res = await fetch(`${FN.admin}?${params.toString()}`, {
        headers: fnAdminHeaders(secret),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(readAdminError(json, "AI 中转额度流水查询失败"));
      setAiRelayLedger((json.data ?? null) as AIRelayLogPage | null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "AI 中转额度流水查询失败");
    } finally {
      setAiRelayBusy(false);
    }
  }, [secret, aiRelayUserId]);

  const fetchAIRelayUsage = useCallback(async (userId?: string) => {
    const targetUserId = (userId ?? aiRelayUserId).trim();
    if (!targetUserId) {
      setErr("请输入平台用户 ID（user_id）");
      return;
    }
    setAiRelayBusy(true);
    setErr("");
    try {
      const logsParams = new URLSearchParams({
        action: "ai_relay_usage_logs",
        user_id: targetUserId,
        page: "1",
        page_size: "20",
        include_errors: "true",
      });
      const summaryParams = new URLSearchParams({
        action: "ai_relay_usage_summary",
        user_id: targetUserId,
      });
      const [logsRes, summaryRes] = await Promise.all([
        fetch(`${FN.admin}?${logsParams.toString()}`, { headers: fnAdminHeaders(secret) }),
        fetch(`${FN.admin}?${summaryParams.toString()}`, { headers: fnAdminHeaders(secret) }),
      ]);
      const logsJson = await logsRes.json();
      const summaryJson = await summaryRes.json();
      if (!logsRes.ok) throw new Error(readAdminError(logsJson, "AI 中转调用日志查询失败"));
      if (!summaryRes.ok) throw new Error(readAdminError(summaryJson, "AI 中转调用汇总查询失败"));
      setAiRelayUsageLogs((logsJson.data ?? null) as AIRelayLogPage | null);
      setAiRelayUsageSummary((summaryJson.data ?? null) as AIRelayUsageSummary | null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "AI 中转调用查询失败");
    } finally {
      setAiRelayBusy(false);
    }
  }, [secret, aiRelayUserId]);

  const fetchAIRelayHealth = useCallback(async () => {
    setAiRelayBusy(true);
    setErr("");
    try {
      const group = aiRelayGroup.trim();
      const params = new URLSearchParams({ action: "ai_relay_health" });
      if (group) params.set("group", group);
      const res = await fetch(`${FN.admin}?${params.toString()}`, {
        headers: fnAdminHeaders(secret),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(readAdminError(json, "AI 中转健康状态查询失败"));
      setAiRelayHealth((json.data ?? null) as AIRelayHealth | null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "AI 中转健康状态查询失败");
    } finally {
      setAiRelayBusy(false);
    }
  }, [secret, aiRelayGroup]);

  const fetchAIRelayOverview = useCallback(async () => {
    const targetUserId = aiRelayUserId.trim();
    if (!targetUserId) {
      setErr("请输入平台用户 ID（user_id）");
      return;
    }
    await Promise.all([
      fetchAIRelayQuota(targetUserId),
      fetchAIRelayLedger(targetUserId),
      fetchAIRelayUsage(targetUserId),
      fetchAIRelayHealth(),
    ]);
  }, [aiRelayUserId, fetchAIRelayQuota, fetchAIRelayLedger, fetchAIRelayUsage, fetchAIRelayHealth]);

  const fetchAIRelayAccount = useCallback(async (userId?: string) => {
    const targetUserId = (userId ?? aiRelayUserId).trim();
    if (!targetUserId) {
      setErr("请输入 user_id");
      return;
    }
    setLoading(true);
    setErr("");
    try {
      const res = await fetch(
        `${FN.admin}?action=ai_relay_account&user_id=${encodeURIComponent(targetUserId)}`,
        { headers: fnAdminHeaders(secret) },
      );
      const json = await res.json();
      if (!res.ok) throw new Error(readAdminError(json, "AI 中转账号查询失败"));
      applyAIRelayAccount((json.data ?? null) as AIRelayAccount | null);
    } catch (e: unknown) {
      setAiRelayLoaded(true);
      setAiRelayAccount(null);
      setErr(e instanceof Error ? e.message : "AI 中转账号查询失败");
    } finally {
      setLoading(false);
    }
  }, [secret, aiRelayUserId]);

  const ensureAIRelayAccount = useCallback(async () => {
    const targetUserId = aiRelayUserId.trim();
    if (!targetUserId) {
      setErr("请输入 user_id");
      return;
    }
    setAiRelayBusy(true);
    setErr("");
    try {
      const preferredModels = parseJsonObject(aiRelayPreferences);
      const res = await fetch(`${FN.admin}?action=ai_relay_ensure_account`, {
        method: "POST",
        headers: { ...fnAdminHeaders(secret), "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: targetUserId,
          email: aiRelayEmail.trim() || undefined,
          display_name: aiRelayDisplayName.trim() || undefined,
          default_group: aiRelayGroup.trim() || undefined,
          preferred_models: preferredModels,
        }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(readAdminError(json, "AI 中转账号创建或修复失败"));
      applyAIRelayAccount((json.data ?? null) as AIRelayAccount | null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "AI 中转账号创建或修复失败");
    } finally {
      setAiRelayBusy(false);
    }
  }, [secret, aiRelayUserId, aiRelayEmail, aiRelayDisplayName, aiRelayGroup, aiRelayPreferences]);

  const updateAIRelayAccount = useCallback(async () => {
    const targetUserId = aiRelayUserId.trim();
    if (!targetUserId) {
      setErr("请输入 user_id");
      return;
    }
    setAiRelayBusy(true);
    setErr("");
    try {
      const preferredModels = parseJsonObject(aiRelayPreferences);
      const res = await fetch(`${FN.admin}?action=ai_relay_update_account`, {
        method: "POST",
        headers: { ...fnAdminHeaders(secret), "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: targetUserId,
          group: aiRelayGroup.trim() || undefined,
          status: aiRelayStatus,
          preferred_models: preferredModels,
        }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(readAdminError(json, "AI 中转账号更新失败"));
      applyAIRelayAccount((json.data ?? null) as AIRelayAccount | null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "AI 中转账号更新失败");
    } finally {
      setAiRelayBusy(false);
    }
  }, [secret, aiRelayUserId, aiRelayGroup, aiRelayStatus, aiRelayPreferences]);

  const disableAIRelayAccount = useCallback(async () => {
    const targetUserId = aiRelayUserId.trim();
    if (!targetUserId) {
      setErr("请输入 user_id");
      return;
    }
    if (!confirm(`确定禁用用户 ${targetUserId} 的 AI 中转账号？`)) return;
    setAiRelayBusy(true);
    setErr("");
    try {
      const res = await fetch(`${FN.admin}?action=ai_relay_disable_account`, {
        method: "POST",
        headers: { ...fnAdminHeaders(secret), "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: targetUserId }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(readAdminError(json, "AI 中转账号禁用失败"));
      applyAIRelayAccount((json.data ?? null) as AIRelayAccount | null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "AI 中转账号禁用失败");
    } finally {
      setAiRelayBusy(false);
    }
  }, [secret, aiRelayUserId]);

  const adjustAIRelayQuota = useCallback(async (form: AIRelayQuotaAdjustmentForm) => {
    const targetUserId = aiRelayUserId.trim();
    if (!targetUserId) {
      setErr("请输入平台用户 ID（user_id）");
      return;
    }
    setAiRelayBusy(true);
    setErr("");
    try {
      const res = await fetch(`${FN.admin}?action=ai_relay_quota_adjustment`, {
        method: "POST",
        headers: { ...fnAdminHeaders(secret), "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: targetUserId,
          mode: form.mode,
          quota_usd: form.quotaUsd,
          reason: form.reason,
        }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(readAdminError(json, "AI 中转额度调整失败"));
      setAiRelayQuotaAdjustment((json.data ?? null) as AIRelayQuotaAdjustmentResult | null);
      await Promise.all([fetchAIRelayQuota(targetUserId), fetchAIRelayLedger(targetUserId)]);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "AI 中转额度调整失败");
    } finally {
      setAiRelayBusy(false);
    }
  }, [secret, aiRelayUserId, fetchAIRelayQuota, fetchAIRelayLedger]);

  const fetchRoadshow = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const h = fnAdminHeaders(secret);
      const params = new URLSearchParams({ action: "roadshow_routes" });
      if (roadshowQuery.trim()) params.set("q", roadshowQuery.trim());
      if (roadshowFilterRoute.trim()) params.set("route_key", roadshowFilterRoute.trim());
      if (roadshowFilterStatus) params.set("status", roadshowFilterStatus);
      if (roadshowFrom) params.set("from", new Date(roadshowFrom).toISOString());
      if (roadshowTo) params.set("to", new Date(roadshowTo).toISOString());
      const res = await fetch(`${FN.admin}?${params.toString()}`, { headers: h });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "路演入口请求失败");
      setRoadshowRoutes(json.routes ?? []);
      setRoadshowInputs(json.recent_inputs ?? []);
      setRoadshowDeliveryEvents(json.delivery_events ?? []);
      setRoadshowLoaded(true);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "请求失败");
    } finally {
      setLoading(false);
    }
  }, [secret, roadshowQuery, roadshowFilterRoute, roadshowFilterStatus, roadshowFrom, roadshowTo]);

  const lookupRoadshowToken = useCallback(async () => {
    const t = roadshowToken.trim();
    if (!t) {
      setErr("请输入平台 Token（Platform Token）");
      return;
    }
    setLoading(true);
    setErr("");
    setRoadshowStatus("");
    try {
      const h = { ...fnAdminHeaders(secret), "Content-Type": "application/json" };
      const res = await fetch(`${FN.admin}?action=roadshow_lookup_token`, {
        method: "POST",
        headers: h,
        body: JSON.stringify({ platform_token: t }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "用户 Token 查询失败");
      setRoadshowLookup(json as RoadshowLookupData);
      const running = ((json.instances ?? []) as AgentInstanceRow[]).find((i) => i.state === "running");
      setRoadshowSelectedInstance(running?.agent_instance_id ?? (json.instances?.[0]?.agent_instance_id ?? ""));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "查询失败");
      setRoadshowLookup(null);
      setRoadshowSelectedInstance("");
    } finally {
      setLoading(false);
    }
  }, [secret, roadshowToken]);

  const saveRoadshowRoute = useCallback(async () => {
    if (!roadshowLookup?.user.user_id || !roadshowSelectedInstance) {
      setErr("请先查询平台 Token 并选择智能体实例");
      return;
    }
    setRoadshowSaving(true);
    setErr("");
    setRoadshowStatus("");
    try {
      const h = { ...fnAdminHeaders(secret), "Content-Type": "application/json" };
      const res = await fetch(`${FN.admin}?action=roadshow_save_route`, {
        method: "POST",
        headers: h,
        body: JSON.stringify({
          route_key: roadshowRouteKey.trim(),
          title: roadshowTitle.trim(),
          enabled: roadshowEnabled,
          target_user_id: roadshowLookup.user.user_id,
          target_agent_instance_id: roadshowSelectedInstance,
        }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "路演入口保存失败");
      setRoadshowStatus("配置已保存");
      await fetchRoadshow();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setRoadshowSaving(false);
    }
  }, [secret, roadshowLookup, roadshowSelectedInstance, roadshowRouteKey, roadshowTitle, roadshowEnabled, fetchRoadshow]);

  const kickInstance = useCallback(async (instanceId: string) => {
    if (!confirm(`确定踢出实例 ${instanceId}？`)) return;
    try {
      await fetch(`${FN.admin}?action=kick_instance&instance_id=${instanceId}`, {
        method: "POST",
        headers: { ...fnAdminHeaders(secret), "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "admin_manual_kick" }),
      });
      void fetchLive(false);
      void fetchGwStatus();
    } catch { /* ignore */ }
  }, [secret, fetchLive, fetchGwStatus]);

  const toggleMaintenance = useCallback(async () => {
    if (!gwStatus) return;
    const enabling = gwStatus.accepting;
    if (enabling && !confirm("确定开启维护模式？新连接将被拒绝。")) return;
    try {
      await fetch(`${FN.admin}?action=set_maintenance`, {
        method: "POST",
        headers: { ...fnAdminHeaders(secret), "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: enabling }),
      });
      void fetchGwStatus();
    } catch { /* ignore */ }
  }, [secret, gwStatus, fetchGwStatus]);

  const fetchConvoPage = useCallback(async (userId: string, page: number) => {
    setConvoListLoading(true);
    setErr("");
    try {
      const h = fnAdminHeaders(secret);
      const res = await fetch(
        `${FN.admin}?action=conversations&user_id=${encodeURIComponent(userId)}&page=${page}&page_size=${CONVO_PAGE_SIZE}`,
        { headers: h },
      );
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "会话记录请求失败");
      setConversations(json.messages ?? []);
      setConvoTotal(Number(json.total ?? 0));
      setConvoPage(page);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "请求失败");
    } finally {
      setConvoListLoading(false);
    }
  }, [secret]);

  const fetchUserDetail = useCallback(async (userId: string) => {
    setUserDetailLoading(true);
    setSelectedUserId(userId);
    setUserProfile(null);
    setConversations([]);
    setTimeline([]);
    setConvoPage(0);
    setConvoTotal(0);
    try {
      const h = fnAdminHeaders(secret);
      const [pRes, cRes, tRes] = await Promise.all([
        fetch(`${FN.admin}?action=user_profile&user_id=${encodeURIComponent(userId)}`, { headers: h }),
        fetch(
          `${FN.admin}?action=conversations&user_id=${encodeURIComponent(userId)}&page=0&page_size=${CONVO_PAGE_SIZE}`,
          { headers: h },
        ),
        fetch(`${FN.admin}?action=user_timeline&user_id=${encodeURIComponent(userId)}`, { headers: h }),
      ]);
      const pJson = await pRes.json();
      const cJson = await cRes.json();
      const tJson = await tRes.json();
      setUserProfile(pJson);
      setConversations(cJson.messages ?? []);
      setConvoTotal(Number(cJson.total ?? 0));
      setConvoPage(0);
      setTimeline(tJson.timeline ?? []);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "请求失败");
    } finally {
      setUserDetailLoading(false);
    }
  }, [secret]);

  // ─── Tab switch logic ─────────────────────────────────────────────────────

  useEffect(() => {
    if (!authed || !secret) return;
    if (activeTab === "users" && !usersLoaded) fetchUsers();
    if (activeTab === "dashboard" && !dashLoaded) fetchDashboard();
    if (activeTab === "deep-dive" && !deepDiveLoaded) fetchActiveUsers();
    if (activeTab === "live" && !liveLoaded) { fetchLive(); fetchGwStatus(); }
    if (activeTab === "hosted-runtime" && !hostedHealthLoaded) fetchHostedHealth();
    if (activeTab === "roadshow" && !roadshowLoaded) fetchRoadshow();
    if (activeTab === "waitlist" && !waitlistLoaded) fetchWaitlist();
    if (activeTab === "official-invites" && !officialInvitesLoaded) fetchOfficialInvites();
  }, [authed, secret, activeTab, usersLoaded, dashLoaded, deepDiveLoaded, liveLoaded, hostedHealthLoaded, roadshowLoaded, waitlistLoaded, officialInvitesLoaded, fetchUsers, fetchDashboard, fetchActiveUsers, fetchLive, fetchGwStatus, fetchHostedHealth, fetchRoadshow, fetchWaitlist, fetchOfficialInvites]);

  useEffect(() => {
    if (!authed || !secret) return;
    if (activeTab !== "live" || !liveAutoRefresh) return;
    const id = setInterval(() => { void fetchLive(false); void fetchGwStatus(); }, 5000);
    return () => clearInterval(id);
  }, [authed, secret, activeTab, liveAutoRefresh, fetchLive, fetchGwStatus]);

  function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    sessionStorage.setItem("admin_secret", secret);
    setAuthed(true);
  }

  // ─── Login screen ─────────────────────────────────────────────────────────

  if (!authed) {
    return (
      <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] flex items-center justify-center px-4">
        <form onSubmit={handleLogin} className="parchment rounded-2xl p-8 max-w-sm w-full text-center">
          <div className="text-3xl mb-4">&#x1f512;</div>
          <h1 className="font-[family-name:var(--font-pixel)] text-sm mb-4">管理后台</h1>
          <input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="请输入管理后台密钥"
            className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-sm focus:border-stardew-wood focus:outline-none mb-4"
            required
          />
          <button type="submit" className="stardew-btn w-full py-3 rounded-lg font-bold text-sm">
            进入
          </button>
        </form>
      </div>
    );
  }

  // ─── Tabs ─────────────────────────────────────────────────────────────────

  const tabs: { key: TabKey; label: string }[] = [
    { key: "users", label: "用户列表" },
    { key: "dashboard", label: "数据看板" },
    { key: "deep-dive", label: "用户深潜" },
    { key: "live", label: "实时实例" },
    { key: "hosted-runtime", label: "Hosted Runtime" },
    { key: "roadshow", label: "路演入口" },
    { key: "waitlist", label: "预约名单" },
    { key: "official-invites", label: "官方邀请码" },
    { key: "ai-relay", label: "AI 中转账户" },
    { key: "system-config", label: "系统配置" },
  ];

  const todayCount = users.filter(
    (u) => u.created_at.slice(0, 10) === new Date().toISOString().slice(0, 10)
  ).length;

  return (
    <div className="min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] bg-stardew-cream flex flex-col md:flex-row">
      <aside className="wood-panel text-white md:w-48 shrink-0 flex flex-row md:flex-col gap-1 p-3 md:p-4 md:min-h-[calc(100dvh-4rem)] overflow-x-auto">
        <h1 className="font-[family-name:var(--font-pixel)] text-[10px] md:text-xs mb-2 hidden md:block">
          StarCoPlay 管理后台
        </h1>
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setActiveTab(t.key)}
            className={`text-left px-3 py-2 rounded-lg text-[10px] md:text-xs font-bold whitespace-nowrap ${
              activeTab === t.key ? "bg-white/20" : "hover:bg-white/10"
            }`}
          >
            {t.label}
          </button>
        ))}
      </aside>

      <div className="flex-1 max-w-6xl w-full mx-auto px-4 py-4">
        {err && <p className="text-red-600 text-xs font-bold bg-red-50 p-2 rounded mb-4">{err}</p>}

        {activeTab === "users" && (
          <div className="flex justify-end mb-3">
            <button
              type="button"
              onClick={() => void fetchUsers()}
              disabled={loading}
              className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold disabled:opacity-50"
            >
              刷新
            </button>
          </div>
        )}
        {activeTab === "dashboard" && (
          <div className="flex flex-wrap items-center gap-3 justify-end mb-3">
            <label className="text-[10px] text-stardew-brown/70 flex items-center gap-1">
              统计窗口
              <select
                value={dashDays}
                onChange={(e) => {
                  setDashDays(Number(e.target.value));
                  setDashLoaded(false);
                }}
                className="rounded border border-stardew-wood/30 bg-white/80 text-[10px] px-1 py-0.5"
              >
                {[7, 14, 30].map((n) => (
                  <option key={n} value={n}>
                    {n} 天
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => void fetchDashboard()}
              disabled={loading}
              className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold disabled:opacity-50"
            >
              刷新
            </button>
          </div>
        )}
        {activeTab === "deep-dive" && (
          <div className="flex justify-end mb-3">
            <button
              type="button"
              onClick={() => void fetchActiveUsers()}
              disabled={loading}
              className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold disabled:opacity-50"
            >
              刷新
            </button>
          </div>
        )}
        {activeTab === "live" && (
          <div className="flex flex-wrap items-center gap-3 justify-end mb-3">
            <label className="text-[10px] text-stardew-brown/70 flex items-center gap-1">
              视图
              <select
                value={liveMode}
                onChange={(e) => {
                  setLiveMode(e.target.value as LiveMode);
                  setLiveLoaded(false);
                }}
                className="rounded border border-stardew-wood/30 bg-white/80 text-[10px] px-1 py-0.5"
              >
                <option value="alive">存活中</option>
                <option value="recent">最近 N 分钟</option>
              </select>
            </label>
            {liveMode === "recent" && (
              <label className="text-[10px] text-stardew-brown/70 flex items-center gap-1">
                时间窗
                <select
                  value={liveWindowMin}
                  onChange={(e) => {
                    setLiveWindowMin(Number(e.target.value));
                    setLiveLoaded(false);
                  }}
                  className="rounded border border-stardew-wood/30 bg-white/80 text-[10px] px-1 py-0.5"
                >
                  {[5, 15, 30, 60, 180, 720, 1440].map((n) => (
                    <option key={n} value={n}>{n} 分钟</option>
                  ))}
                </select>
              </label>
            )}
            <label className="text-[10px] text-stardew-brown/70 flex items-center gap-1">
              <input
                type="checkbox"
                checked={liveAutoRefresh}
                onChange={(e) => setLiveAutoRefresh(e.target.checked)}
              />
              自动刷新 5s
            </label>
            {liveUpdatedAt && (
              <span className="text-[10px] text-stardew-brown/50">
                上次更新：{new Date(liveUpdatedAt).toLocaleTimeString("zh-CN")}
              </span>
            )}
            <button
              type="button"
              onClick={() => void fetchLive()}
              disabled={loading}
              className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold disabled:opacity-50"
            >
              刷新
            </button>
          </div>
        )}
        {activeTab === "hosted-runtime" && (
          <div className="flex justify-end mb-3">
            <button type="button" onClick={() => void fetchHostedHealth()} disabled={loading} className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold disabled:opacity-50">
              刷新健康状态
            </button>
          </div>
        )}
        {activeTab === "roadshow" && (
          <div className="flex justify-end mb-3">
            <button
              type="button"
              onClick={() => void fetchRoadshow()}
              disabled={loading}
              className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold disabled:opacity-50"
            >
              刷新
            </button>
          </div>
        )}
        {activeTab === "waitlist" && (
          <div className="flex flex-wrap items-center gap-3 justify-end mb-3">
            <label className="text-[10px] text-stardew-brown/70 flex items-center gap-1">
              状态过滤
              <select
                value={waitlistFilter}
                onChange={(e) => {
                  setWaitlistFilter(e.target.value as WaitlistFilter);
                  setWaitlistPage(1);
                  setWaitlistLoaded(false);
                }}
                className="rounded border border-stardew-wood/30 bg-white/80 text-[10px] px-1 py-0.5"
              >
                <option value="pending">待审核 (pending)</option>
                <option value="approved">已通过 (approved)</option>
                <option value="rejected">已驳回 (rejected)</option>
                <option value="registered">已注册 (registered)</option>
                <option value="all">全部</option>
              </select>
            </label>
            <button
              type="button"
              onClick={() => void fetchWaitlist()}
              disabled={loading}
              className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold disabled:opacity-50"
            >
              刷新
            </button>
          </div>
        )}
        {activeTab === "ai-relay" && (
          <div className="flex justify-end mb-3">
            <button
              type="button"
              onClick={() => void fetchAIRelayAccount()}
              disabled={loading || !aiRelayUserId.trim()}
              className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold disabled:opacity-50"
            >
              刷新
            </button>
          </div>
        )}
        {activeTab === "official-invites" && (
          <div className="flex justify-end mb-3">
            <button
              type="button"
              onClick={() => void fetchOfficialInvites()}
              disabled={loading || officialInviteBusy}
              className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold disabled:opacity-50"
            >
              刷新
            </button>
          </div>
        )}

        {loading ? (
          <p className="text-center text-sm text-stardew-brown/60 py-12">加载中...</p>
        ) : (
          <>
            {activeTab === "users" && (
              <UsersTab
                users={users}
                total={total}
                todayCount={todayCount}
                stats={stats}
                userReports={userReports}
                upgradingUserId={upgradingUserId}
                grantingCreditsUserId={grantingCreditsUserId}
                batchGrantingCredits={batchGrantingCredits}
                upgradeToast={userUpgradeToast}
                searchDraft={userSearchDraft}
                appliedSearch={userSearch}
                page={userPage}
                pageSize={userPageSize}
                selectedUserIds={selectedUserIds}
                onSearchDraftChange={setUserSearchDraft}
                onApplySearch={applyUserSearch}
                onClearSearch={clearUserSearch}
                onPageChange={changeUserPage}
                onPageSizeChange={changeUserPageSize}
                onSelectedUserIdsChange={setSelectedUserIds}
                onUpgradeGuestUser={upgradeGuestUser}
                onGrantUserCredits={grantUserCredits}
                onGrantSelectedUsersCredits={grantSelectedUsersCredits}
              />
            )}
            {activeTab === "dashboard" && (
              <DashboardTab dashData={dashData} llmStats={llmStats} actionStats={actionStats} errorRanking={errorRanking} />
            )}
            {activeTab === "deep-dive" && (
              <DeepDiveTab
                activeUsers={activeUsers}
                selectedUserId={selectedUserId}
                userProfile={userProfile}
                conversations={conversations}
                convoPage={convoPage}
                convoTotal={convoTotal}
                convoPageSize={CONVO_PAGE_SIZE}
                convoListLoading={convoListLoading}
                onConvoPageChange={(page) => {
                  if (selectedUserId) void fetchConvoPage(selectedUserId, page);
                }}
                timeline={timeline}
                userDetailLoading={userDetailLoading}
                searchUserId={searchUserId}
                onSearchChange={setSearchUserId}
                onSelectUser={fetchUserDetail}
                dashDays={dashDays}
              />
            )}
            {activeTab === "live" && (
              <LiveTab
                data={liveData}
                gwStatus={gwStatus}
                onToggleMaintenance={toggleMaintenance}
                onKickInstance={kickInstance}
              />
            )}
            {activeTab === "hosted-runtime" && <HostedRuntimeTab health={hostedHealth} />}
            {activeTab === "roadshow" && (
              <RoadshowTab
                routes={roadshowRoutes}
                recentInputs={roadshowInputs}
                deliveryEvents={roadshowDeliveryEvents}
                platformToken={roadshowToken}
                onPlatformTokenChange={setRoadshowToken}
                onLookupToken={lookupRoadshowToken}
                lookup={roadshowLookup}
                routeKey={roadshowRouteKey}
                onRouteKeyChange={setRoadshowRouteKey}
                title={roadshowTitle}
                onTitleChange={setRoadshowTitle}
                selectedInstance={roadshowSelectedInstance}
                onSelectedInstanceChange={setRoadshowSelectedInstance}
                enabled={roadshowEnabled}
                onEnabledChange={setRoadshowEnabled}
                onSave={saveRoadshowRoute}
                saving={roadshowSaving}
                status={roadshowStatus}
                query={roadshowQuery}
                onQueryChange={setRoadshowQuery}
                filterRoute={roadshowFilterRoute}
                onFilterRouteChange={setRoadshowFilterRoute}
                filterStatus={roadshowFilterStatus}
                onFilterStatusChange={setRoadshowFilterStatus}
                from={roadshowFrom}
                onFromChange={setRoadshowFrom}
                to={roadshowTo}
                onToChange={setRoadshowTo}
                onRefresh={() => void fetchRoadshow()}
              />
            )}
            {activeTab === "waitlist" && (
              <WaitlistTab
                entries={waitlistEntries}
                total={waitlistTotal}
                filter={waitlistFilter}
                mutatingId={waitlistMutatingId}
                onUpdateStatus={updateWaitlistStatus}
                toast={waitlistToast}
                page={waitlistPage}
                pageSize={waitlistPageSize}
                search={waitlistSearch}
                onSearchChange={setWaitlistSearch}
                onPageChange={(p) => { setWaitlistPage(p); setWaitlistLoaded(false); }}
                onPageSizeChange={(s) => { setWaitlistPageSize(s); setWaitlistLoaded(false); }}
                loading={loading}
                onViewDetail={fetchWaitlistDetail}
              />
            )}
            {activeTab === "official-invites" && (
              <OfficialInvitesTab
                codes={officialInviteCodes}
                summary={officialInviteSummary}
                form={officialInviteCreateForm}
                onFormChange={(patch) => {
                  setOfficialInviteCreateForm((current) => ({ ...current, ...patch }));
                }}
                busy={officialInviteBusy}
                toast={officialInviteToast}
                onCreate={() => void createOfficialInvites()}
                onDisable={(code) => void disableOfficialInvite(code)}
                onIncreaseMaxUses={(code, additionalUses) => void increaseOfficialInviteMaxUses(code, additionalUses)}
              />
            )}

            {/* Waitlist user detail modal */}
            {waitlistDetailId !== null && (
              <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 px-4">
                <div className="parchment rounded-2xl p-6 max-w-md w-full">
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="font-[family-name:var(--font-pixel)] text-sm">用户进度 #{waitlistDetailId}</h2>
                    <button
                      type="button"
                      onClick={() => { setWaitlistDetailId(null); setWaitlistDetail(null); setWaitlistDetailErr(""); }}
                      className="text-stardew-brown/50 hover:text-stardew-brown text-lg leading-none"
                      aria-label="关闭"
                    >
                      ✕
                    </button>
                  </div>
                  {waitlistDetailLoading && (
                    <p className="text-center text-sm text-stardew-brown/60 py-8">加载中...</p>
                  )}
                  {waitlistDetailErr && (
                    <p className="text-red-600 text-xs font-bold bg-red-50 p-2 rounded">{waitlistDetailErr}</p>
                  )}
                  {waitlistDetail && !waitlistDetailLoading && (
                    <WaitlistDetailSteps detail={waitlistDetail} />
                  )}
                </div>
              </div>
            )}
            {activeTab === "ai-relay" && (
              <AIRelayTab
                userId={aiRelayUserId}
                onUserIdChange={setAiRelayUserId}
                account={aiRelayAccount}
                loaded={aiRelayLoaded}
                group={aiRelayGroup}
                onGroupChange={setAiRelayGroup}
                status={aiRelayStatus}
                onStatusChange={setAiRelayStatus}
                preferences={aiRelayPreferences}
                onPreferencesChange={setAiRelayPreferences}
                email={aiRelayEmail}
                onEmailChange={setAiRelayEmail}
                displayName={aiRelayDisplayName}
                onDisplayNameChange={setAiRelayDisplayName}
                busy={aiRelayBusy}
                quota={aiRelayQuota}
                ledger={aiRelayLedger}
                usageLogs={aiRelayUsageLogs}
                usageSummary={aiRelayUsageSummary}
                health={aiRelayHealth}
                quotaAdjustment={aiRelayQuotaAdjustment}
                onFetch={() => void fetchAIRelayAccount()}
                onEnsure={() => void ensureAIRelayAccount()}
                onUpdate={() => void updateAIRelayAccount()}
                onDisable={() => void disableAIRelayAccount()}
                onAdjustQuota={(form) => void adjustAIRelayQuota(form)}
                onFetchQuota={() => void fetchAIRelayQuota()}
                onFetchLedger={() => void fetchAIRelayLedger()}
                onFetchUsage={() => void fetchAIRelayUsage()}
                onFetchHealth={() => void fetchAIRelayHealth()}
                onFetchOverview={() => void fetchAIRelayOverview()}
              />
            )}
            {activeTab === "system-config" && (
              <SystemConfigTab secret={secret} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ─── StatCard ─────────────────────────────────────────────────────────────────

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="parchment rounded-xl p-4">
      <p className="text-[10px] text-stardew-brown/60 mb-1">{label}</p>
      <p className="text-lg font-bold">{value}</p>
    </div>
  );
}

function readAdminError(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "error" in payload) {
    const error = (payload as { error?: unknown }).error;
    if (typeof error === "string") return error;
    if (error && typeof error === "object" && "message" in error) {
      const message = (error as { message?: unknown }).message;
      if (typeof message === "string" && message) return message;
    }
  }
  return fallback;
}

function parseJsonObject(raw: string): Record<string, string> {
  const value = raw.trim();
  if (!value) return {};
  const parsed = JSON.parse(value) as unknown;
  if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("偏好模型（preferred_models）必须是 JSON 对象");
  }
  const result: Record<string, string> = {};
  for (const [key, item] of Object.entries(parsed)) {
    if (typeof item !== "string") throw new Error("偏好模型（preferred_models）的每个值都必须是字符串");
    if (key.trim() && item.trim()) result[key.trim()] = item.trim();
  }
  return result;
}

function formatUnixSeconds(value?: number): string {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleString("zh-CN");
}

const AI_RELAY_STATUS_LABELS: Record<string, string> = {
  active: "正常（active）",
  disabled: "已禁用（disabled）",
  syncing: "同步中（syncing）",
  sync_failed: "同步失败（sync_failed）",
};

const AI_RELAY_QUOTA_MODE_LABELS: Record<string, string> = {
  add: "增加额度（add）",
  subtract: "扣减额度（subtract）",
  override: "覆盖额度（override）",
};

const AI_RELAY_LOG_TYPE_LABELS: Record<number, string> = {
  1: "充值/加点（topup）",
  2: "消耗（consume）",
  3: "管理操作（manage）",
  4: "系统操作（system）",
  5: "异常记录（error）",
  6: "退款/回退（refund）",
};

function aiRelayStatusLabel(status?: string): string {
  if (!status) return "-";
  return AI_RELAY_STATUS_LABELS[status] ?? `未知状态（${status}）`;
}

function aiRelayQuotaModeLabel(mode?: string): string {
  if (!mode) return "-";
  return AI_RELAY_QUOTA_MODE_LABELS[mode] ?? `未知方式（${mode}）`;
}

function aiRelayBoundLabel(value: boolean): string {
  return value ? "已绑定" : "缺失";
}

function logTypeName(type?: number): string {
  return type ? AI_RELAY_LOG_TYPE_LABELS[type] ?? `类型 ${type}` : "-";
}

function positiveNumber(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : Number.NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function newApiQuotaPerUsdFromHealth(health: AIRelayHealth | null): number | null {
  return positiveNumber(health?.newapi_status?.quota_per_unit);
}

function newApiQuotaPerUsdFromAdjustment(adjustment: AIRelayQuotaAdjustmentResult | null): number | null {
  return positiveNumber(adjustment?.quota_per_usd);
}

function newApiQuotaFromUsd(value: number, quotaPerUsd: number | null): number | null {
  if (!Number.isFinite(value) || value <= 0 || quotaPerUsd == null) return null;
  return Math.round(value * quotaPerUsd);
}

function formatNewApiQuota(value?: number | null, quotaPerUsd?: number | null): string {
  if (value == null || !Number.isFinite(value)) return "-";
  if (quotaPerUsd == null || quotaPerUsd <= 0) {
    return `${value.toLocaleString("zh-CN")} 原始额度（未查询到美元换算规则）`;
  }
  const usd = value / quotaPerUsd;
  return `$${usd.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}（${value.toLocaleString("zh-CN")} 原始额度）`;
}

function AIRelayStatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    active: "bg-green-100 text-green-800",
    disabled: "bg-gray-100 text-gray-700",
    syncing: "bg-amber-100 text-amber-800",
    sync_failed: "bg-red-100 text-red-800",
  };
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold ${styles[status] ?? "bg-stardew-wood/10 text-stardew-brown"}`}>
      {aiRelayStatusLabel(status)}
    </span>
  );
}

function AIRelaySection({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="font-[family-name:var(--font-pixel)] text-xs text-stardew-brown">{title}</h3>
          <p className="mt-1 text-[10px] leading-relaxed text-stardew-brown/55">{description}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

function AIRelayLogTable({
  title,
  description,
  page,
  quotaPerUsd,
}: {
  title: string;
  description: string;
  page: AIRelayLogPage | null;
  quotaPerUsd: number | null;
}) {
  return (
    <div className="parchment rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-stardew-wood/10 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-bold">{title}</h3>
          <p className="text-[10px] text-stardew-brown/55 mt-1">{description}</p>
        </div>
        <span className="text-[10px] text-stardew-brown/60">共 {page?.total ?? 0} 条</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs min-w-[720px]">
          <thead className="bg-stardew-wood/10">
            <tr>
              <th className="px-3 py-2 text-left">时间</th>
              <th className="px-3 py-2 text-left">类型</th>
              <th className="px-3 py-2 text-left">模型</th>
              <th className="px-3 py-2 text-left">额度变化</th>
              <th className="px-3 py-2 text-left">Token 数</th>
              <th className="px-3 py-2 text-left">内容/备注</th>
            </tr>
          </thead>
          <tbody>
            {(page?.items ?? []).map((item, idx) => (
              <tr key={`${item.id ?? idx}-${item.created_at ?? 0}`} className="border-b border-stardew-wood/10">
                <td className="px-3 py-2 whitespace-nowrap">{formatUnixSeconds(item.created_at)}</td>
                <td className="px-3 py-2">{logTypeName(item.type)}</td>
                <td className="px-3 py-2 font-mono">{item.model_name || "-"}</td>
                <td className="px-3 py-2 font-mono">{formatNewApiQuota(item.quota ?? 0, quotaPerUsd)}</td>
                <td className="px-3 py-2 font-mono">{(item.prompt_tokens ?? 0) + (item.completion_tokens ?? 0)}</td>
                <td className="px-3 py-2 max-w-[320px] truncate" title={item.content ?? ""}>{item.content || "-"}</td>
              </tr>
            ))}
            {(!page || page.items.length === 0) && (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-stardew-brown/50">暂无数据。请先点击上方查询按钮加载对应记录。</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AIRelayHealthPanel({ health }: { health: AIRelayHealth | null }) {
  const channels = health?.channels?.items ?? [];
  const quotaPerUsd = newApiQuotaPerUsdFromHealth(health);
  return (
    <div className="parchment rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-bold">NewAPI 健康状态</h3>
          <p className="text-[10px] text-stardew-brown/55 mt-1">
            用于确认平台能否连到 NewAPI、当前分组是否存在、模型和渠道是否可见。渠道的 API Key 和模型池仍在 NewAPI 后台维护。
          </p>
        </div>
        <span className="text-[10px] text-stardew-brown/60 whitespace-nowrap">{formatUnixSeconds(health?.checked_at)}</span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
        <MiniCard label="服务连通" value={health?.server_test?.success ? "正常" : "未知/未查询"} />
        <MiniCard label="可用分组" value={(health?.groups ?? []).join(", ") || "-"} />
        <MiniCard label="启用模型数" value={health?.models?.enabled?.length ?? 0} />
        <MiniCard label="渠道样本数" value={health?.channels?.total ?? 0} />
        <MiniCard label="额度换算" value={quotaPerUsd == null ? "未返回" : `${quotaPerUsd.toLocaleString("zh-CN")} 原始额度 = $1`} />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs min-w-[640px]">
          <thead className="bg-stardew-wood/10">
            <tr>
              <th className="px-3 py-2 text-left">渠道 ID</th>
              <th className="px-3 py-2 text-left">渠道名称</th>
              <th className="px-3 py-2 text-left">原始状态值</th>
              <th className="px-3 py-2 text-left">分组</th>
              <th className="px-3 py-2 text-left">余额</th>
              <th className="px-3 py-2 text-left">响应耗时</th>
            </tr>
          </thead>
          <tbody>
            {channels.slice(0, 12).map((channel) => (
              <tr key={channel.id ?? channel.name} className="border-b border-stardew-wood/10">
                <td className="px-3 py-2 font-mono">{channel.id ?? "-"}</td>
                <td className="px-3 py-2">{channel.name ?? "-"}</td>
                <td className="px-3 py-2 font-mono">{channel.status ?? "-"}</td>
                <td className="px-3 py-2">{channel.group ?? "-"}</td>
                <td className="px-3 py-2 font-mono">{channel.balance ?? "-"}</td>
                <td className="px-3 py-2 font-mono">{channel.response_time ?? "-"}</td>
              </tr>
            ))}
            {channels.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-stardew-brown/50">暂无渠道样本。点击“查询 NewAPI 健康”后仍为空时，请到 NewAPI 后台检查渠道配置。</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AIRelayTab({
  userId,
  onUserIdChange,
  account,
  loaded,
  group,
  onGroupChange,
  status,
  onStatusChange,
  preferences,
  onPreferencesChange,
  email,
  onEmailChange,
  displayName,
  onDisplayNameChange,
  busy,
  quota,
  ledger,
  usageLogs,
  usageSummary,
  health,
  quotaAdjustment,
  onFetch,
  onEnsure,
  onUpdate,
  onDisable,
  onAdjustQuota,
  onFetchQuota,
  onFetchLedger,
  onFetchUsage,
  onFetchHealth,
  onFetchOverview,
}: {
  userId: string;
  onUserIdChange: (value: string) => void;
  account: AIRelayAccount | null;
  loaded: boolean;
  group: string;
  onGroupChange: (value: string) => void;
  status: string;
  onStatusChange: (value: string) => void;
  preferences: string;
  onPreferencesChange: (value: string) => void;
  email: string;
  onEmailChange: (value: string) => void;
  displayName: string;
  onDisplayNameChange: (value: string) => void;
  busy: boolean;
  quota: AIRelayQuota | null;
  ledger: AIRelayLogPage | null;
  usageLogs: AIRelayLogPage | null;
  usageSummary: AIRelayUsageSummary | null;
  health: AIRelayHealth | null;
  quotaAdjustment: AIRelayQuotaAdjustmentResult | null;
  onFetch: () => void;
  onEnsure: () => void;
  onUpdate: () => void;
  onDisable: () => void;
  onAdjustQuota: (form: AIRelayQuotaAdjustmentForm) => void;
  onFetchQuota: () => void;
  onFetchLedger: () => void;
  onFetchUsage: () => void;
  onFetchHealth: () => void;
  onFetchOverview: () => void;
}) {
  const [quotaForm, setQuotaForm] = useState<AIRelayQuotaAdjustmentForm>({
    mode: "add",
    quotaUsd: 100,
    reason: "",
  });

  function updateQuotaForm(patch: Partial<AIRelayQuotaAdjustmentForm>) {
    setQuotaForm((current) => ({ ...current, ...patch }));
  }

  const quotaUsdInvalid =
    !Number.isFinite(quotaForm.quotaUsd) ||
    quotaForm.quotaUsd < 0 ||
    ((quotaForm.mode === "add" || quotaForm.mode === "subtract") && quotaForm.quotaUsd <= 0);
  const quotaPerUsd = newApiQuotaPerUsdFromAdjustment(quotaAdjustment) ?? newApiQuotaPerUsdFromHealth(health);
  const quotaRawPreview = newApiQuotaFromUsd(
    Number.isFinite(quotaForm.quotaUsd) ? quotaForm.quotaUsd : 0,
    quotaPerUsd,
  );

  return (
    <div className="space-y-4">
      <div className="parchment rounded-xl p-4">
        <h2 className="text-sm font-bold mb-2">AI 中转账户维护</h2>
        <p className="mb-3 text-[10px] leading-relaxed text-stardew-brown/55">
          按技术方案固定为“账户概览、账务流水、调用明细、NewAPI 健康”四块。管理后台只做统一入口和查询聚合，账务事实仍以 NewAPI 为准。
        </p>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 text-[11px] text-stardew-brown/65 leading-relaxed">
          <div className="bg-white/35 border border-stardew-wood/15 rounded-lg p-3">
            <p className="font-bold text-stardew-brown mb-1">这个页面管什么</p>
            <p>这里维护平台用户和 NewAPI 用户/令牌的绑定关系。平台侧负责按 user_id 找到对应 NewAPI 账号，实际模型渠道、上游 API Key、渠道倍率和模型池仍在 NewAPI 后台维护。</p>
          </div>
          <div className="bg-white/35 border border-stardew-wood/15 rounded-lg p-3">
            <p className="font-bold text-stardew-brown mb-1">常用操作顺序</p>
            <p>先输入平台用户 ID，点“查询账号绑定”。如果没有绑定或绑定异常，点“创建或修复绑定”。需要排查时点“加载维护概览”，会同时拉取额度、流水、调用日志和 NewAPI 健康状态。</p>
          </div>
          <div className="bg-white/35 border border-stardew-wood/15 rounded-lg p-3">
            <p className="font-bold text-stardew-brown mb-1">维护注意事项</p>
            <p>禁用账号会阻断该用户继续通过平台调用模型。额度调整会写入 NewAPI 额度流水；后台会自动生成幂等键和业务来源，原因可按需要补充。</p>
          </div>
        </div>
      </div>

      <div className="parchment rounded-xl p-4">
        <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_auto_auto] gap-3 items-end">
          <label className="block text-[10px] text-stardew-brown/70">
            平台用户 ID（public.users.user_id）
            <input
              type="text"
              value={userId}
              onChange={(e) => onUserIdChange(e.target.value)}
              placeholder="public.users.user_id"
              className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1 font-mono"
            />
            <span className="block mt-1 text-[10px] text-stardew-brown/45">必须填平台自己的 user_id，不是 NewAPI 的用户 ID。</span>
          </label>
          <button type="button" onClick={onFetch} disabled={busy || !userId.trim()} className="stardew-btn px-4 py-2 rounded-lg text-xs font-bold disabled:opacity-50">查询账号绑定</button>
          <button type="button" onClick={onFetchOverview} disabled={busy || !userId.trim()} className="px-4 py-2 rounded-lg text-xs font-bold border-2 border-stardew-wood/30 hover:bg-white/40 disabled:opacity-50">加载维护概览</button>
          <button type="button" onClick={onEnsure} disabled={busy || !userId.trim()} className="px-4 py-2 rounded-lg text-xs font-bold border-2 border-stardew-wood/30 hover:bg-white/40 disabled:opacity-50">创建或修复绑定</button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-3">
          <label className="block text-[10px] text-stardew-brown/70">
            邮箱（可选）
            <input type="email" value={email} onChange={(e) => onEmailChange(e.target.value)} className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1" />
            <span className="block mt-1 text-[10px] text-stardew-brown/45">创建 NewAPI 用户时用于备注，已有绑定不会反查覆盖平台用户资料。</span>
          </label>
          <label className="block text-[10px] text-stardew-brown/70">
            显示名（可选）
            <input type="text" value={displayName} onChange={(e) => onDisplayNameChange(e.target.value)} className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1" />
            <span className="block mt-1 text-[10px] text-stardew-brown/45">便于在 NewAPI 后台识别来源用户。</span>
          </label>
          <label className="block text-[10px] text-stardew-brown/70">
            NewAPI 分组（group）
            <input type="text" value={group} onChange={(e) => onGroupChange(e.target.value)} placeholder="default" className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1" />
            <span className="block mt-1 text-[10px] text-stardew-brown/45">必须和 NewAPI 后台配置的分组一致，例如 default。</span>
          </label>
        </div>
      </div>

      {account ? (
        <>
          <AIRelaySection
            title="账户概览"
            description="来自 profiles、ai_relay_accounts 与 NewAPI quota，用于确认用户状态、默认路由分组、绑定关系和可用额度。"
          >
            <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
              <StatCard label="NewAPI 用户 ID" value={account.newapi_user_id} />
              <StatCard label="NewAPI Token ID" value={account.newapi_token_id ?? "-"} />
              <StatCard label="模型调用 Token" value={aiRelayBoundLabel(account.has_token_key)} />
              <StatCard label="用户管理 Access Token" value={aiRelayBoundLabel(account.has_user_access_token)} />
              <div className="parchment rounded-xl p-4">
                <p className="text-[10px] text-stardew-brown/60 mb-2">账号状态</p>
                <AIRelayStatusBadge status={account.status} />
              </div>
            </div>

            {quota && (
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <StatCard label="用户剩余额度" value={formatNewApiQuota(quota.quota, quotaPerUsd)} />
                <StatCard label="已用额度" value={formatNewApiQuota(quota.used_quota, quotaPerUsd)} />
                <StatCard label="请求次数" value={quota.request_count} />
                <StatCard label="Token 可用额度" value={quota.token?.total_available == null ? "不限额" : formatNewApiQuota(quota.token.total_available, quotaPerUsd)} />
              </div>
            )}

            <div className="parchment rounded-xl p-4">
              <h3 className="text-sm font-bold mb-1">账号绑定设置</h3>
              <p className="text-[10px] text-stardew-brown/55 mb-4">
                修改分组或状态只影响平台记录和后续路由选择；NewAPI 渠道的可用模型、价格和上游密钥请在 NewAPI 后台维护。保存前建议确认用户当前是否有正在进行的模型调用。
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <label className="block text-[10px] text-stardew-brown/70">
                  NewAPI 分组（group）
                  <input type="text" value={group} onChange={(e) => onGroupChange(e.target.value)} className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1" />
                </label>
                <label className="block text-[10px] text-stardew-brown/70">
                  账号状态（status）
                  <select value={status} onChange={(e) => onStatusChange(e.target.value)} className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1">
                    <option value="active">{AI_RELAY_STATUS_LABELS.active}</option>
                    <option value="disabled">{AI_RELAY_STATUS_LABELS.disabled}</option>
                    <option value="syncing">{AI_RELAY_STATUS_LABELS.syncing}</option>
                    <option value="sync_failed">{AI_RELAY_STATUS_LABELS.sync_failed}</option>
                  </select>
                </label>
              </div>
              <label className="block text-[10px] text-stardew-brown/70 mt-4">
                偏好模型 JSON（preferred_models）
                <textarea value={preferences} onChange={(e) => onPreferencesChange(e.target.value)} rows={6} placeholder={'{\n  "balanced": "claude-sonnet-4-6"\n}'} className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1 font-mono" />
                <span className="block mt-1 text-[10px] text-stardew-brown/45">
                  这里按平台模型档位指定首选 NewAPI 模型。示例：{"{"}"balanced": "claude-sonnet-4-6"{"}"}。留空或空对象表示使用系统默认映射。
                </span>
              </label>
              <div className="flex flex-wrap gap-2 mt-4">
                <button type="button" onClick={onUpdate} disabled={busy} className="stardew-btn px-4 py-2 rounded-lg text-xs font-bold disabled:opacity-50">保存账号设置</button>
                <button type="button" onClick={onDisable} disabled={busy || account.status === "disabled"} className="px-4 py-2 rounded-lg text-xs font-bold bg-red-100 text-red-700 hover:bg-red-200 disabled:opacity-50">禁用账号</button>
                <button type="button" onClick={onFetchQuota} disabled={busy} className="px-4 py-2 rounded-lg text-xs font-bold border-2 border-stardew-wood/30 hover:bg-white/40 disabled:opacity-50">查询额度</button>
                <button type="button" onClick={onFetchLedger} disabled={busy} className="px-4 py-2 rounded-lg text-xs font-bold border-2 border-stardew-wood/30 hover:bg-white/40 disabled:opacity-50">查询额度流水</button>
                <button type="button" onClick={onFetchUsage} disabled={busy} className="px-4 py-2 rounded-lg text-xs font-bold border-2 border-stardew-wood/30 hover:bg-white/40 disabled:opacity-50">查询模型调用</button>
                <button type="button" onClick={onFetchHealth} disabled={busy} className="px-4 py-2 rounded-lg text-xs font-bold border-2 border-stardew-wood/30 hover:bg-white/40 disabled:opacity-50">查询 NewAPI 健康</button>
              </div>
            </div>
          </AIRelaySection>

          <AIRelaySection
            title="账务流水"
            description="账务事实只读 NewAPI logs。后台额度调整会写入 NewAPI 流水，platform 只保留必要操作来源。"
          >
            <div className="parchment rounded-xl p-4">
              <h3 className="text-sm font-bold mb-1">额度调整</h3>
              <p className="text-[10px] text-stardew-brown/55 mb-4">
                这里填写 NewAPI 后台展示的美元额度，不需要再手算原始额度值。提交后端会实时读取 NewAPI 状态里的 quota_per_unit 做换算，并自动生成幂等键和后台业务来源；实际可调用次数仍取决于模型倍率、分组倍率和渠道计费规则。
              </p>
            <div className="grid grid-cols-1 md:grid-cols-[160px_180px_1fr] gap-3">
              <label className="block text-[10px] text-stardew-brown/70">
                调整方式*
                <select
                  value={quotaForm.mode}
                  onChange={(e) => updateQuotaForm({ mode: e.target.value })}
                  className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1"
                >
                  <option value="add">{AI_RELAY_QUOTA_MODE_LABELS.add}</option>
                  <option value="subtract">{AI_RELAY_QUOTA_MODE_LABELS.subtract}</option>
                  <option value="override">{AI_RELAY_QUOTA_MODE_LABELS.override}</option>
                </select>
              </label>
              <label className="block text-[10px] text-stardew-brown/70">
                额度数值（美元）*
                <input
                  type="number"
                  min={0}
                  step="0.01"
                  value={quotaForm.quotaUsd}
                  onChange={(e) => updateQuotaForm({ quotaUsd: Number(e.target.value) })}
                  className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1"
                />
                <span className="block mt-1 text-[10px] text-stardew-brown/45">
                  例如填 100 表示 NewAPI 后台显示的 $100；
                  {quotaRawPreview == null
                    ? "当前页面尚未查询到换算规则，提交时由后端向 NewAPI 实时读取。"
                    : `按当前已查询到的规则会写入 ${quotaRawPreview.toLocaleString("zh-CN")} 原始额度值。`}
                </span>
              </label>
              <label className="block text-[10px] text-stardew-brown/70">
                调整原因（可选）
                <input
                  type="text"
                  value={quotaForm.reason}
                  onChange={(e) => updateQuotaForm({ reason: e.target.value })}
                  className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1"
                />
                <span className="block mt-1 text-[10px] text-stardew-brown/45">
                  留空时平台会自动写入“后台额度调整：方式 + 金额”；幂等键和业务来源也会自动生成，后续仍可在额度流水和平台审计记录里回溯。
                </span>
              </label>
            </div>
            <div className="flex flex-wrap gap-2 mt-4 items-center">
              <button
                type="button"
                onClick={() => onAdjustQuota(quotaForm)}
                disabled={busy || quotaUsdInvalid}
                className="stardew-btn px-4 py-2 rounded-lg text-xs font-bold disabled:opacity-50"
              >
                执行额度调整
              </button>
              {quotaAdjustment && (
                <span className="text-[10px] text-stardew-brown/70">
                  最近调整：{quotaAdjustment.status ?? "-"} / {aiRelayQuotaModeLabel(quotaAdjustment.mode)} {formatNewApiQuota(quotaAdjustment.quota, quotaPerUsd)}
                  {quotaAdjustment.duplicate ? " / 重复请求已拦截" : ""}
                </span>
              )}
            </div>
            </div>
            <AIRelayLogTable title="额度流水" description="展示 NewAPI 侧额度增减、系统调整、消费、退款等记录，用于查账和回溯人工操作。" page={ledger} quotaPerUsd={quotaPerUsd} />
          </AIRelaySection>

          <AIRelaySection
            title="调用明细"
            description="来自 NewAPI usage logs / summary，用于查看模型、group、request_id、Token 与消耗，不作为 platform 本地账务副本。"
          >
            {usageSummary && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <StatCard label="调用消耗额度" value={formatNewApiQuota(usageSummary.quota, quotaPerUsd)} />
                <StatCard label="每分钟请求数（RPM）" value={usageSummary.rpm} />
                <StatCard label="每分钟 Token 数（TPM）" value={usageSummary.tpm} />
              </div>
            )}
            <AIRelayLogTable title="模型调用日志" description="展示用户通过中转发起的模型调用记录，包含模型名、消耗额度、Token 数和 NewAPI 返回内容摘要。" page={usageLogs} quotaPerUsd={quotaPerUsd} />
          </AIRelaySection>

          <AIRelaySection
            title="NewAPI 健康"
            description="用于确认平台到 NewAPI、group、模型池和渠道样本是否可用；渠道密钥与倍率仍在 NewAPI 后台维护。"
          >
            <AIRelayHealthPanel health={health} />
          </AIRelaySection>

          <div className="parchment rounded-xl overflow-hidden">
            <table className="w-full text-xs">
              <tbody>
                {[
                  ["平台用户 ID（user_id）", account.user_id],
                  ["NewAPI 分组（group）", account.group],
                  ["凭证模式（credential_mode）", account.credential_mode],
                  ["创建时间（created_at）", account.created_at ?? "-"],
                  ["更新时间（updated_at）", account.updated_at ?? "-"],
                  ["最近同步错误（last_sync_error）", account.last_sync_error ?? "-"],
                ].map(([label, value]) => (
                  <tr key={label} className="border-b border-stardew-wood/10">
                    <td className="px-3 py-2 font-bold w-56">{label}</td>
                    <td className="px-3 py-2 font-mono break-all">{value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="parchment rounded-xl p-8 text-center text-sm text-stardew-brown/60">
          {loaded ? "没有找到 AI 中转绑定。确认 user_id 无误后，可点击“创建或修复绑定”自动创建 NewAPI 用户和 Token。" : "请输入平台用户 ID，然后查询账号绑定；如果用户还没有绑定，可直接创建或修复绑定。"}
        </div>
      )}
    </div>
  );
}

// --- Tab: Users ---

function UsersTab({
  users,
  total,
  todayCount,
  stats,
  userReports,
  upgradingUserId,
  grantingCreditsUserId,
  batchGrantingCredits,
  upgradeToast,
  searchDraft,
  appliedSearch,
  page,
  pageSize,
  selectedUserIds,
  onSearchDraftChange,
  onApplySearch,
  onClearSearch,
  onPageChange,
  onPageSizeChange,
  onSelectedUserIdsChange,
  onUpgradeGuestUser,
  onGrantUserCredits,
  onGrantSelectedUsersCredits,
}: {
  users: User[];
  total: number;
  todayCount: number;
  stats: Stats | null;
  userReports: UserReport[];
  upgradingUserId: string | null;
  grantingCreditsUserId: string | null;
  batchGrantingCredits: boolean;
  upgradeToast: { kind: "ok" | "err"; msg: string } | null;
  searchDraft: string;
  appliedSearch: string;
  page: number;
  pageSize: number;
  selectedUserIds: string[];
  onSearchDraftChange: (value: string) => void;
  onApplySearch: () => void;
  onClearSearch: () => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  onSelectedUserIdsChange: (value: string[]) => void;
  onUpgradeGuestUser: (user: User) => void;
  onGrantUserCredits: (user: User) => void;
  onGrantSelectedUsersCredits: () => void;
}) {
  const selectedSet = new Set(selectedUserIds);
  const allVisibleSelected = users.length > 0 && users.every((user) => selectedSet.has(user.user_id));

  function toggleUser(userId: string) {
    if (selectedSet.has(userId)) {
      onSelectedUserIdsChange(selectedUserIds.filter((id) => id !== userId));
      return;
    }
    onSelectedUserIdsChange([...selectedUserIds, userId]);
  }

  function toggleAllVisible() {
    if (allVisibleSelected) {
      const visible = new Set(users.map((user) => user.user_id));
      onSelectedUserIdsChange(selectedUserIds.filter((id) => !visible.has(id)));
      return;
    }
    onSelectedUserIdsChange([...new Set([...selectedUserIds, ...users.map((user) => user.user_id)])]);
  }

  return (
    <>
      <form
        className="mb-4 flex flex-col gap-2 md:flex-row md:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          onApplySearch();
        }}
      >
        <label className="flex-1 text-[10px] font-bold text-stardew-brown/70">
          按 UID / 邮箱搜索
          <input
            value={searchDraft}
            onChange={(e) => onSearchDraftChange(e.target.value)}
            placeholder="UID / 邮箱"
            className="mt-1 w-full rounded-lg border border-stardew-wood/30 bg-white/70 px-3 py-2 text-xs text-stardew-brown outline-none focus:border-stardew-green"
          />
        </label>
        <div className="flex gap-2">
          <button type="submit" className="stardew-btn px-3 py-2 rounded-lg text-[10px] font-bold">
            搜索
          </button>
          {appliedSearch && (
            <button
              type="button"
              onClick={onClearSearch}
              className="px-3 py-2 rounded-lg text-[10px] font-bold border border-stardew-wood/30 text-stardew-brown hover:bg-white/50"
            >
              清除
            </button>
          )}
        </div>
      </form>
      {appliedSearch && (
        <p className="mb-4 text-[10px] text-stardew-brown/60">
          当前搜索：{appliedSearch}
        </p>
      )}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <StatCard label={appliedSearch ? "匹配用户" : "总用户"} value={total} />
        <StatCard label={appliedSearch ? "匹配今日新增" : "今日新增"} value={todayCount} />
        <StatCard
          label="来源分布"
          value={stats?.by_source.map((s) => `${s.source}: ${s.count}`).join(", ") ?? "-"}
        />
      </div>
      {upgradeToast && (
        <div
          className={`mb-4 rounded-lg p-3 text-xs font-bold ${
            upgradeToast.kind === "ok"
              ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
              : "bg-red-50 text-red-700 border border-red-200"
          }`}
        >
          {upgradeToast.msg}
        </div>
      )}

      {/* User reports */}
      <div className="parchment rounded-xl overflow-hidden mb-6">
        <p className="text-[10px] text-stardew-brown/60 px-3 pt-3">用户反馈（最近 100 条）</p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b-2 border-stardew-wood/20">
                {["时间", "用户", "反馈内容", "位置", "体力", "游戏时间"].map((h) => (
                  <th key={h} className="px-3 py-3 text-left font-bold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {userReports.map((r, i) => {
                const gc = r.data?.game_context;
                return (
                  <tr key={`${r.user_id}-${i}`} className="border-b border-stardew-wood/10 hover:bg-white/30">
                    <td className="px-3 py-2 whitespace-nowrap">{new Date(r.ts).toLocaleString("zh-CN")}</td>
                    <td className="px-3 py-2 max-w-[100px]">
                      <CopyableId id={r.user_id ?? "-"} />
                    </td>
                    <td className="px-3 py-2 max-w-[300px]">{r.data?.message?.text ?? "-"}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{gc?.location ?? "-"}</td>
                    <td className="px-3 py-2">{gc?.energy ?? "-"}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{gc?.game_time ?? "-"}</td>
                  </tr>
                );
              })}
              {userReports.length === 0 && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-stardew-brown/50">暂无反馈</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="parchment rounded-xl overflow-hidden mb-6">
        <div className="px-3 pt-3">
          <p className="text-[10px] text-stardew-brown/60">用户邀请码使用总览</p>
          <p className="text-[10px] text-stardew-brown/45 mt-1">
            汇总当前用户列表中的所有个人邀请码，方便管理员整体查看邀请码使用情况和被邀请用户明细。
          </p>
        </div>
        <UserInviteOverviewTable users={users} />
      </div>

      {/* Users table */}
      <div className="parchment rounded-xl overflow-hidden">
        <div className="flex flex-col gap-2 border-b border-stardew-wood/15 px-3 py-3 md:flex-row md:items-center md:justify-between">
          <div className="text-[10px] text-stardew-brown/60">
            已选择 {selectedUserIds.length} 个用户
          </div>
          <button
            type="button"
            onClick={onGrantSelectedUsersCredits}
            disabled={selectedUserIds.length === 0 || batchGrantingCredits}
            className="stardew-btn px-3 py-2 rounded-lg text-[10px] font-bold disabled:opacity-50"
          >
            {batchGrantingCredits ? "批量发放中..." : "批量加积分"}
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b-2 border-stardew-wood/20">
                <th className="px-3 py-3 text-left font-bold">
                  <input
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={toggleAllVisible}
                    aria-label="选择当前列表全部用户"
                  />
                </th>
                {["昵称", "User ID", "类型", "邀请来源", "个人邀请码", "联系方式", "邮箱", "使用邀请码", "注册时间", "Token", "操作"].map((h) => (
                  <th key={h} className="px-3 py-3 text-left font-bold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.user_id} className="border-b border-stardew-wood/10 hover:bg-white/30">
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={selectedSet.has(u.user_id)}
                      onChange={() => toggleUser(u.user_id)}
                      aria-label={`选择用户 ${u.user_id}`}
                    />
                  </td>
                  <td className="px-3 py-2 min-w-[180px]">
                    <p className="font-bold text-stardew-brown">{u.nickname ?? "-"}</p>
                    <ResolvedUserCreditStatus summary={u.credit_summary} />
                  </td>
                  <td className="px-3 py-2 max-w-[100px]">
                    <CopyableId id={u.user_id} />
                  </td>
                  <td className="px-3 py-2">{({ guest: "游客", beta_user: "内测用户", user: "正式用户", member: "会员" } as Record<string, string>)[u.user_type ?? ""] ?? "-"}</td>
                  <td className="px-3 py-2">{u.invite_source ?? "-"}</td>
                  <td className="px-3 py-2 min-w-[150px]">
                    <UserInviteSummaryCell summary={u.invite_code_summary} />
                  </td>
                  <td className="px-3 py-2">
                    <ContactButton user={u} />
                  </td>
                  <td className="px-3 py-2 min-w-[220px] whitespace-nowrap">{u.email ?? "-"}</td>
                  <td className="px-3 py-2">{u.invite_code_used}</td>
                  <td className="px-3 py-2">{new Date(u.created_at).toLocaleString("zh-CN")}</td>
                  <td className="px-3 py-2 max-w-[140px]">
                    <TokenCell token={u.platform_token} />
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <div className="flex flex-col gap-1">
                    {u.user_type === "guest" ? (
                      <button
                        type="button"
                        onClick={() => onUpgradeGuestUser(u)}
                        disabled={upgradingUserId === u.user_id}
                        className="stardew-btn px-2 py-1 rounded text-[10px] font-bold disabled:opacity-50"
                        title="开通 AI Relay、个人邀请码、用户关系，并升级为内测用户"
                      >
                        {upgradingUserId === u.user_id ? "处理中..." : "升级内测"}
                      </button>
                    ) : (
                      <span className="text-stardew-brown/40">-</span>
                    )}
                      <button
                        type="button"
                        onClick={() => onGrantUserCredits(u)}
                        disabled={batchGrantingCredits || grantingCreditsUserId === u.user_id}
                        className="px-2 py-1 rounded text-[10px] font-bold border border-stardew-wood/30 text-stardew-brown hover:bg-white/50 disabled:opacity-50"
                        title="通过 AI Relay 额度流水给该用户发放积分"
                      >
                        {grantingCreditsUserId === u.user_id ? "发放中..." : "发积分"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {users.length === 0 && (
                <tr><td colSpan={12} className="px-3 py-6 text-center text-stardew-brown/50">暂无用户</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      <div className="mt-4 flex items-center justify-between text-[11px]">
        <span className="text-stardew-brown/60">共 {total} 条</span>
        <div className="flex items-center gap-2">
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            className="rounded border border-stardew-wood/20 bg-white/50 px-2 py-1 text-[11px]"
          >
            <option value={20}>20 条/页</option>
            <option value={50}>50 条/页</option>
            <option value={100}>100 条/页</option>
          </select>
          <button type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)} className="stardew-btn rounded px-2 py-1 disabled:opacity-40">上一页</button>
          <span className="text-stardew-brown/70">{page} / {Math.max(1, Math.ceil(total / pageSize))}</span>
          <button type="button" disabled={page >= Math.ceil(total / pageSize)} onClick={() => onPageChange(page + 1)} className="stardew-btn rounded px-2 py-1 disabled:opacity-40">下一页</button>
        </div>
      </div>
    </>
  );
}

function ResolvedUserCreditStatus({ summary }: { summary?: User["credit_summary"] }) {
  if (!summary) {
    return <span className="mt-1 inline-flex rounded-full bg-slate-100 px-2 py-1 text-[10px] font-bold text-slate-500">积分状态未知</span>;
  }

  if (summary.status === "not_provisioned") {
    return <span className="mt-1 inline-flex rounded-full bg-slate-100 px-2 py-1 text-[10px] font-bold text-slate-500">未开通积分账户</span>;
  }

  if (summary.status === "unavailable") {
    return (
      <span
        className="mt-1 inline-flex rounded-full bg-amber-100 px-2 py-1 text-[10px] font-bold text-amber-700"
        title={`积分查询暂时不可用：${summary.reason_code}`}
      >
        积分暂时不可用
      </span>
    );
  }

  return <UserCreditStatus summary={summary} />;
}

function UserCreditStatus({ summary }: { summary: Extract<NonNullable<User["credit_summary"]>, { status: "available" }> }) {
  const percent = Math.max(0, Math.min(100, summary.available_percent));
  const isFull = percent >= 99.995;
  const isLow = percent < 10;
  const tone = isFull
    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
    : isLow
      ? "border-red-200 bg-red-50 text-red-700"
      : "border-amber-200 bg-amber-50 text-amber-800";

  return (
    <div className={`mt-1 rounded-lg border px-2 py-1.5 ${tone}`} title={`已使用 ${summary.used_credits.toFixed(2)} 积分`}>
      <div className="flex items-center justify-between gap-2 text-[10px] font-bold">
        <span>{summary.available_credits.toFixed(2)} 积分可用</span>
        <span>{percent.toFixed(0)}%</span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-black/10">
        <div className={`h-full rounded-full ${isFull ? "bg-emerald-500" : isLow ? "bg-red-500" : "bg-amber-500"}`} style={{ width: `${percent}%` }} />
      </div>
      {isLow && <p className="mt-1 text-[9px] font-bold">积分不足，建议补充</p>}
    </div>
  );
}

function UserInviteOverviewTable({ users }: { users: User[] }) {
  const rows = users
    .flatMap((user) => {
      const codes = user.invite_code_summary?.codes ?? [];
      return codes.map((code) => ({
        owner_user_id: user.user_id,
        owner_nickname: user.nickname,
        owner_email: user.email,
        code: code.code,
        used_count: code.used_count,
        max_uses: code.max_uses,
        usage_limit: code.usage_limit ?? null,
        remaining_uses: code.remaining_uses ?? null,
        disabled: code.disabled,
        invited_user_count: code.invited_users.length,
        invited_users: code.invited_users,
      }));
    })
    .sort((a, b) => {
      if (b.invited_user_count !== a.invited_user_count) return b.invited_user_count - a.invited_user_count;
      if (b.used_count !== a.used_count) return b.used_count - a.used_count;
      return a.code.localeCompare(b.code);
    });

  if (rows.length === 0) {
    return <p className="px-3 py-6 text-center text-xs text-stardew-brown/50">当前暂无用户邀请码使用记录</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b-2 border-stardew-wood/20">
            {["邀请码拥有者", "用户 ID", "邀请码", "使用情况", "已邀请人数", "被邀请用户"].map((h) => (
              <th key={h} className="px-3 py-3 text-left font-bold">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.owner_user_id}-${row.code}`} className="border-b border-stardew-wood/10 hover:bg-white/30">
              <td className="px-3 py-2">
                <div className="font-bold">{row.owner_nickname ?? "-"}</div>
                <div className="text-[10px] text-stardew-brown/50">{row.owner_email ?? "-"}</div>
              </td>
              <td className="px-3 py-2 max-w-[100px]">
                <CopyableId id={row.owner_user_id} />
              </td>
              <td className="px-3 py-2 font-mono text-[10px]">{row.code}</td>
              <td className="px-3 py-2 whitespace-nowrap">
                {row.used_count}
                {row.usage_limit == null ? " / 不限" : ` / ${row.usage_limit}`}
                {row.disabled && (
                  <span className="ml-2 rounded bg-stardew-brown/10 px-1.5 py-0.5 text-[10px] text-stardew-brown/60">
                    已禁用
                  </span>
                )}
              </td>
              <td className="px-3 py-2">{row.invited_user_count}</td>
              <td className="px-3 py-2 min-w-[280px]">
                {row.invited_users.length === 0 ? (
                  <span className="text-stardew-brown/40">暂无</span>
                ) : (
                  <div className="space-y-1">
                    {row.invited_users.map((invited) => (
                      <div key={invited.user_id} className="rounded bg-white/35 px-2 py-1">
                        <div className="font-medium">{invited.nickname ?? invited.email ?? invited.user_id}</div>
                        <div className="text-[10px] text-stardew-brown/50">
                          {invited.email ?? invited.user_id}
                          {invited.created_at ? ` · ${new Date(invited.created_at).toLocaleString("zh-CN")}` : ""}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UserInviteSummaryCell({ summary }: { summary?: UserInviteSummary }) {
  const codes = summary?.codes ?? [];
  if (codes.length === 0) {
    return <span className="text-stardew-brown/40">-</span>;
  }
  const invitedUsers = summary?.invited_users ?? [];
  const inviteeLines = invitedUsers.length === 0
    ? "暂无被邀请用户"
    : invitedUsers
        .map((user) => `${user.nickname || user.email || user.user_id}${user.email ? ` <${user.email}>` : ""}`)
        .join("\n");
  const primaryCode = codes[0];
  const extraCount = codes.length - 1;

  return (
    <div className="space-y-1" title={`已邀请 ${summary?.invited_user_count ?? 0} 人\n${inviteeLines}`}>
      <div className="font-mono text-[10px] text-stardew-brown">
        {primaryCode.code}
        {extraCount > 0 && <span className="ml-1 text-stardew-brown/50">+{extraCount}</span>}
      </div>
      <div className="text-[10px] text-stardew-brown/55">
        已邀 {summary?.invited_user_count ?? 0} 人
      </div>
      {invitedUsers.length > 0 && (
        <div className="max-w-[180px] truncate text-[10px] text-stardew-brown/45">
          {invitedUsers.map((user) => user.email ?? user.nickname ?? user.user_id).join(", ")}
        </div>
      )}
    </div>
  );
}

function ContactButton({ user }: { user: User }) {
  const items = [
    ["手机", user.phone],
    ["微信", user.wechat],
  ].filter(([, value]) => value?.trim());
  const disabled = items.length === 0;

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => {
        if (disabled) return;
        alert(items.map(([label, value]) => `${label}：${value}`).join("\n"));
      }}
      className="px-2 py-1 rounded text-[10px] font-bold border border-stardew-wood/30 text-stardew-brown hover:bg-white/50 disabled:opacity-40"
      title={disabled ? "没有手机或微信" : items.map(([label, value]) => `${label}: ${value}`).join("\n")}
    >
      查看联系方式
    </button>
  );
}

// ─── Tab: Dashboard ───────────────────────────────────────────────────────────

function DashboardTab({
  dashData,
  llmStats,
  actionStats,
  errorRanking,
}: {
  dashData: DashboardData | null;
  llmStats: LlmStats | null;
  actionStats: ActionStatRow[];
  errorRanking: ErrorRow[];
}) {
  if (!dashData) return <p className="text-center text-sm text-stardew-brown/50 py-8">暂无数据</p>;

  const latestDau = dashData.dau.length > 0 ? dashData.dau[dashData.dau.length - 1].dau : 0;
  const latestDuration =
    dashData.avg_session_duration.length > 0
      ? dashData.avg_session_duration[dashData.avg_session_duration.length - 1].avg_duration_min
      : 0;

  const funnelMap: Record<string, number> = {};
  dashData.onboard_funnel.forEach((f) => (funnelMap[f.step] = f.user_count));
  const activated = funnelMap["3_first_action"] ?? 0;
  const firstConvo = funnelMap["4_first_conversation"] ?? 0;
  const convoRate = activated > 0 ? Math.round((firstConvo / activated) * 100) : 0;

  const dauBars = dashData.dau.slice(-14);
  const durBars = dashData.avg_session_duration.slice(-14);
  const latestDay = dashData.dau.at(-1)?.day ?? null;
  const freshnessStatus = dashData.freshness?.status ?? "unavailable";
  const latestEventAt = dashData.freshness?.latest_event_at ?? null;
  const freshnessHours = latestEventAt
    ? Math.max(0, (Date.now() - new Date(latestEventAt).getTime()) / 3600000)
    : null;
  const isFresh = freshnessStatus === "available" && freshnessHours != null && freshnessHours <= 24;

  return (
    <>
      <div className="mb-5 flex flex-col gap-3 rounded-2xl border border-stardew-wood/15 bg-white/75 p-4 shadow-sm md:flex-row md:items-center md:justify-between">
        <div>
          <p className="text-sm font-bold text-stardew-brown">运营数据概览</p>
          <p className="mt-1 text-[11px] text-stardew-brown/60">按实际持久化的产品活动事件统计，AI 调用与账务请在 AI 中转账户中查看。</p>
        </div>
        <div className={`rounded-xl border px-3 py-2 text-[11px] ${isFresh ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
          <p className="font-bold">{freshnessStatus === "unavailable" ? "数据状态暂不可用" : isFresh ? "数据更新正常" : "数据可能延迟"}</p>
          <p>最新事件：{latestEventAt ? new Date(latestEventAt).toLocaleString("zh-CN") : freshnessStatus === "unavailable" ? "查询失败" : "暂无"}</p>
          <p className="opacity-70">{freshnessStatus === "unavailable" ? dashData.freshness?.error_code ?? "-" : dashData.freshness?.latest_event_type ?? "-"}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <StatCard label={`最新 DAU${latestDay ? ` · ${latestDay}` : ""}`} value={latestDau} />
        <StatCard label="平均会话时长" value={`${Number(latestDuration).toFixed(1)} 分钟`} />
        <StatCard label="主动对话转化" value={`${convoRate}%`} />
        <StatCard
          label={`${dashData.freshness?.window_days ?? 30} 天事件量`}
          value={dashData.freshness?.events_in_window == null ? "不可用" : formatNumber(dashData.freshness.events_in_window)}
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        <div className="parchment rounded-xl p-4">
          <p className="text-[10px] text-stardew-brown/60 mb-2">日活用户（DAU）趋势（最近 14 个点）</p>
          <MiniBars
            rows={dauBars}
            val={(r) => r.dau}
            label={(r) => String(r.day).slice(5)}
          />
        </div>
        <div className="parchment rounded-xl p-4">
          <p className="text-[10px] text-stardew-brown/60 mb-2">日均会话时长（分钟，末 14 点）</p>
          <MiniBars
            rows={durBars}
            val={(r) => Number(r.avg_duration_min)}
            label={(r) => String(r.day).slice(5)}
          />
        </div>
      </div>

      {/* Overview cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatCard label="今日日活用户（DAU）" value={latestDau} />
        <StatCard label="平均时长（分钟）" value={Number(latestDuration).toFixed(1)} />
        <StatCard label="主动对话率" value={`${convoRate}%`} />
        <StatCard label="LLM 运行统计" value="已迁移" />
      </div>

      {/* Onboard funnel */}
      <div className="parchment rounded-xl p-4 mb-6">
        <p className="text-[10px] text-stardew-brown/60 mb-3">转化漏斗</p>
        <FunnelChart funnel={dashData.onboard_funnel} />
      </div>

      {/* Retention */}
      <div className="parchment rounded-xl overflow-hidden mb-6">
        <p className="text-[10px] text-stardew-brown/60 px-3 pt-3">留存（Cohort）</p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b-2 border-stardew-wood/20">
                {["分群日期", "人数", "次日留存 D1%", "3 日留存 D3%", "7 日留存 D7%"].map((h) => (
                  <th key={h} className="px-3 py-3 text-left font-bold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dashData.retention.map((r) => (
                <tr key={r.cohort_date} className="border-b border-stardew-wood/10 hover:bg-white/30">
                  <td className="px-3 py-2">{r.cohort_date}</td>
                  <td className="px-3 py-2">{r.cohort_size}</td>
                  <td className="px-3 py-2"><RetentionCell value={r.d1} /></td>
                  <td className="px-3 py-2"><RetentionCell value={r.d3} /></td>
                  <td className="px-3 py-2"><RetentionCell value={r.d7} /></td>
                </tr>
              ))}
              {dashData.retention.length === 0 && (
                <tr><td colSpan={5} className="px-3 py-6 text-center text-stardew-brown/50">暂无留存数据</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* LLM Performance */}
      {llmStats && (
        <div className="parchment rounded-xl p-4 mb-6">
          <p className="text-[10px] text-stardew-brown/60 mb-3">大模型（LLM）性能</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <MiniCard label="总调用" value={llmStats.total_calls} />
            <MiniCard label="平均延迟" value={`${llmStats.avg_latency_ms ?? 0}ms`} />
            <MiniCard label="95 分位延迟（P95）" value={`${llmStats.p95_latency_ms ?? 0}ms`} />
            <MiniCard label="输入 Token 数" value={formatNumber(llmStats.total_tokens_in)} />
            <MiniCard label="输出 Token 数" value={formatNumber(llmStats.total_tokens_out)} />
          </div>
          <ul className="mt-3 flex flex-wrap gap-2">
            {Object.entries(llmStats.model_distribution || {}).length === 0 ? (
              <li className="text-[10px] text-stardew-brown/50">暂无模型分布</li>
            ) : (
              Object.entries(llmStats.model_distribution || {})
                .sort((a, b) => Number(b[1]) - Number(a[1]))
                .map(([m, c]) => (
                  <li
                    key={m}
                    className="text-[10px] px-2 py-1 rounded-lg bg-white/50 font-mono border border-stardew-wood/15"
                  >
                    {m}: <strong>{String(c)}</strong>
                  </li>
                ))
            )}
          </ul>
        </div>
      )}

      <div className="parchment rounded-xl p-4 mb-6">
        <p className="text-[10px] text-stardew-brown/60 mb-2">AI Relay data source</p>
        <p className="text-xs text-stardew-brown/80 leading-5">
          LLM usage, billing ledger, and route troubleshooting are no longer read from public.events.
          Use the AI relay account tab for NewAPI account and billing facts, and use structured ECS logs
          for runtime route troubleshooting. This dashboard only keeps product/business event summaries.
        </p>
      </div>

      {/* Action stats + Error ranking side by side */}
      {(actionStats.length > 0 || errorRanking.length > 0) && (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {/* Action distribution */}
        <div className="parchment rounded-xl overflow-hidden">
          <p className="text-[10px] text-stardew-brown/60 px-3 pt-3">动作分布 + 成功率</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b-2 border-stardew-wood/20">
                  {["动作", "总数", "成功", "成功率"].map((h) => (
                    <th key={h} className="px-3 py-3 text-left font-bold">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {actionStats.map((a) => (
                  <tr key={a.action_type} className="border-b border-stardew-wood/10 hover:bg-white/30">
                    <td className="px-3 py-2 font-mono">{a.action_type}</td>
                    <td className="px-3 py-2">{Number(a.total)}</td>
                    <td className="px-3 py-2">{Number(a.success_count)}</td>
                    <td className="px-3 py-2">{Number(a.success_rate)}%</td>
                  </tr>
                ))}
                {actionStats.length === 0 && (
                  <tr><td colSpan={4} className="px-3 py-6 text-center text-stardew-brown/50">暂无动作数据</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Error ranking */}
        <div className="parchment rounded-xl overflow-hidden">
          <p className="text-[10px] text-stardew-brown/60 px-3 pt-3">错误 Top 5</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b-2 border-stardew-wood/20">
                  {["错误类型", "次数", "最近发生", "示例"].map((h) => (
                    <th key={h} className="px-3 py-3 text-left font-bold">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {errorRanking.map((e) => (
                  <tr key={e.error_type} className="border-b border-stardew-wood/10 hover:bg-white/30">
                    <td className="px-3 py-2 font-mono max-w-[120px] truncate">{e.error_type}</td>
                    <td className="px-3 py-2">{Number(e.error_count)}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{new Date(e.latest_ts).toLocaleString("zh-CN")}</td>
                    <td className="px-3 py-2 max-w-[200px] truncate" title={e.sample_msg ?? ""}>
                      {e.sample_msg ?? "-"}
                    </td>
                  </tr>
                ))}
                {errorRanking.length === 0 && (
                  <tr><td colSpan={4} className="px-3 py-6 text-center text-stardew-brown/50">暂无错误</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
      )}

      {/* Event type distribution */}
      <div className="parchment rounded-xl overflow-hidden">
        <p className="text-[10px] text-stardew-brown/60 px-3 pt-3">事件类型分布</p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b-2 border-stardew-wood/20">
                <th className="px-3 py-3 text-left font-bold">类型</th>
                <th className="px-3 py-3 text-left font-bold">条数</th>
              </tr>
            </thead>
            <tbody>
              {dashData.event_stats.map((r) => (
                <tr key={r.event_type} className="border-b border-stardew-wood/10 hover:bg-white/30">
                  <td className="px-3 py-2 font-mono">{r.event_type}</td>
                  <td className="px-3 py-2">{Number(r.cnt)}</td>
                </tr>
              ))}
              {dashData.event_stats.length === 0 && (
                <tr><td colSpan={2} className="px-3 py-6 text-center text-stardew-brown/50">暂无事件</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

// ─── Tab: Deep Dive ───────────────────────────────────────────────────────────

const TEXT_PREVIEW = 200;

function ExpandableText({ text, className }: { text: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const t = text.length ? text : "-";
  const needTruncate = t.length > TEXT_PREVIEW;
  const shown = open || !needTruncate ? t : `${t.slice(0, TEXT_PREVIEW)}…`;
  return (
    <div className={className}>
      <span className="whitespace-pre-wrap break-words">{shown}</span>
      {needTruncate ? (
        <button
          type="button"
          className="text-[9px] text-stardew-blue underline ml-1 align-top"
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "收起" : "全文"}
        </button>
      ) : null}
    </div>
  );
}

function conversationBubbleBody(msg: ConversationMsg): string {
  const d = msg.data;
  if (msg.type === "ai.decision" && d && typeof d === "object") {
    const lines: string[] = [];
    lines.push(`[FC] trigger=${String(d.trigger ?? "")}`);
    if (d.fc_rounds != null) lines.push(`rounds=${String(d.fc_rounds)}`);
    if (Array.isArray(d.executed_actions)) {
      lines.push(`actions: ${d.executed_actions.map(String).join(", ")}`);
    }
    if (d.had_error === true) lines.push("(had_error)");
    if (Array.isArray(d.tool_calls)) {
      for (const tc of d.tool_calls as { name?: unknown; arguments?: unknown }[]) {
        const nm = tc.name != null ? String(tc.name) : "?";
        const arg = tc.arguments != null ? String(tc.arguments) : "";
        lines.push(`  · ${nm} ${arg}`);
      }
    }
    return lines.join("\n");
  }
  if (msg.type === "ai.dialogue" && d && typeof d === "object") {
    return `latency_ms=${String(d.response_latency_ms ?? "-")} is_player_initiated=${String(d.is_player_initiated ?? "-")}`;
  }
  if (d && typeof d === "object") {
    const text =
      (typeof d.text === "string" && d.text) ||
      (typeof d.message === "string" && d.message) ||
      (typeof d.length === "number" ? `[长度: ${d.length}]` : "");
    if (text) return text;
    try {
      return JSON.stringify(d);
    } catch {
      return "[无内容]";
    }
  }
  return "[无内容]";
}

function convoBubbleClasses(msg: ConversationMsg): { row: string; bubble: string; meta: string } {
  if (msg.type === "player.message") {
    return {
      row: "justify-end",
      bubble: "max-w-[85%] px-3 py-2 rounded-xl text-xs bg-green-100 text-green-900 rounded-br-sm",
      meta: "text-green-600",
    };
  }
  if (msg.type === "agent.say") {
    return {
      row: "justify-start",
      bubble: "max-w-[85%] px-3 py-2 rounded-xl text-xs bg-blue-100 text-blue-900 rounded-bl-sm",
      meta: "text-blue-600",
    };
  }
  return {
    row: "justify-start",
    bubble:
      "max-w-[85%] px-3 py-2 rounded-xl text-xs bg-stone-100 text-stone-800 rounded-bl-sm border border-stardew-wood/20",
    meta: "text-stone-600",
  };
}

function DeepDiveTab({
  activeUsers,
  selectedUserId,
  userProfile,
  conversations,
  convoPage,
  convoTotal,
  convoPageSize,
  convoListLoading,
  onConvoPageChange,
  timeline,
  userDetailLoading,
  searchUserId,
  onSearchChange,
  onSelectUser,
  dashDays,
}: {
  activeUsers: ActiveUser[];
  selectedUserId: string | null;
  userProfile: UserProfile | null;
  conversations: ConversationMsg[];
  convoPage: number;
  convoTotal: number;
  convoPageSize: number;
  convoListLoading: boolean;
  onConvoPageChange: (page: number) => void;
  timeline: TimelineEvent[];
  userDetailLoading: boolean;
  searchUserId: string;
  onSearchChange: (v: string) => void;
  onSelectUser: (userId: string) => void;
  dashDays: number;
}) {
  return (
    <>
      {/* Search */}
      <div className="parchment rounded-xl p-4 mb-6">
        <div className="flex gap-2 items-center">
          <input
            type="text"
            value={searchUserId}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="按 user_id 搜索..."
            className="flex-1 px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none"
            onKeyDown={(e) => {
              if (e.key === "Enter" && searchUserId.trim()) onSelectUser(searchUserId.trim());
            }}
          />
          <button
            onClick={() => searchUserId.trim() && onSelectUser(searchUserId.trim())}
            className="stardew-btn px-4 py-2 rounded-lg text-xs font-bold"
          >
            查询
          </button>
        </div>
      </div>

      {/* Active users ranking */}
      <div className="parchment rounded-xl overflow-hidden mb-6">
        <p className="text-[10px] text-stardew-brown/60 px-3 pt-3">活跃用户排行（近 {dashDays} 天）</p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b-2 border-stardew-wood/20">
                {["昵称", "用户 ID", "会话数", "时长(分)", "消息数", "最近活跃"].map((h) => (
                  <th key={h} className="px-3 py-3 text-left font-bold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {activeUsers.map((u) => (
                <tr
                  key={u.user_id}
                  className={`border-b border-stardew-wood/10 hover:bg-white/30 cursor-pointer ${
                    selectedUserId === u.user_id ? "bg-stardew-wood/10" : ""
                  }`}
                  onClick={() => onSelectUser(u.user_id)}
                >
                  <td className="px-3 py-2">{u.nickname ?? "-"}</td>
                  <td className="px-3 py-2 max-w-[120px]">
                    <CopyableId id={u.user_id} maxLen={12} />
                  </td>
                  <td className="px-3 py-2">{Number(u.total_sessions)}</td>
                  <td className="px-3 py-2">{Number(u.total_duration_min)}</td>
                  <td className="px-3 py-2">{Number(u.total_messages)}</td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    {u.last_active ? new Date(u.last_active).toLocaleString("zh-CN") : "-"}
                  </td>
                </tr>
              ))}
              {activeUsers.length === 0 && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-stardew-brown/50">暂无活跃用户</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* User detail panel */}
      {selectedUserId && (
        <div className="space-y-4">
          {userDetailLoading ? (
            <p className="text-center text-sm text-stardew-brown/60 py-8">加载用户数据中...</p>
          ) : (
            <>
              {/* Profile card */}
              {userProfile && (
                <div className="parchment rounded-xl p-4">
                  <p className="text-[10px] text-stardew-brown/60 mb-3">用户档案 - {userProfile.nickname ?? userProfile.user_id}</p>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <MiniCard label="注册时间" value={userProfile.created_at ? new Date(userProfile.created_at).toLocaleDateString("zh-CN") : "-"} />
                    <MiniCard label="来源" value={userProfile.source ?? "-"} />
                    <MiniCard label="总会话数" value={Number(userProfile.total_sessions)} />
                    <MiniCard label="总时长(分)" value={Number(userProfile.total_duration_min)} />
                    <MiniCard label="消息数" value={Number(userProfile.total_messages)} />
                    <MiniCard label="LLM 调用" value={Number(userProfile.total_llm_calls)} />
                    <MiniCard label="动作数" value={Number(userProfile.total_actions)} />
                    <MiniCard label="错误数" value={Number(userProfile.total_errors)} />
                  </div>
                  <div className="mt-3 text-[10px] text-stardew-brown/50">
                    首次会话: {userProfile.first_session ? new Date(userProfile.first_session).toLocaleString("zh-CN") : "-"}
                    {" | "}
                    最近会话: {userProfile.last_session ? new Date(userProfile.last_session).toLocaleString("zh-CN") : "-"}
                  </div>
                </div>
              )}

              {/* Conversation bubbles */}
              <div className="parchment rounded-xl p-4">
                <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                  <p className="text-[10px] text-stardew-brown/60">
                    对话记录（共 {convoTotal} 条，每页 {convoPageSize}）
                  </p>
                  <div className="flex items-center gap-2 text-[10px]">
                    <button
                      type="button"
                      disabled={convoPage <= 0 || convoListLoading}
                      onClick={() => onConvoPageChange(convoPage - 1)}
                      className="stardew-btn px-2 py-1 rounded disabled:opacity-40"
                    >
                      上一页
                    </button>
                    <span className="text-stardew-brown/70">
                      {convoPage + 1} / {Math.max(1, Math.ceil(convoTotal / convoPageSize) || 1)}
                    </span>
                    <button
                      type="button"
                      disabled={(convoPage + 1) * convoPageSize >= convoTotal || convoListLoading}
                      onClick={() => onConvoPageChange(convoPage + 1)}
                      className="stardew-btn px-2 py-1 rounded disabled:opacity-40"
                    >
                      下一页
                    </button>
                  </div>
                </div>
                <div className="relative max-h-[500px] overflow-y-auto space-y-2">
                  {[...conversations].reverse().map((msg, i) => {
                    const st = convoBubbleClasses(msg);
                    const body = conversationBubbleBody(msg);
                    return (
                      <div key={`${msg.ts}-${msg.type}-${i}`} className={`flex ${st.row}`}>
                        <div className={st.bubble}>
                          <p className="text-[9px] font-mono text-stardew-brown/55 mb-0.5">{msg.type}</p>
                          <ExpandableText text={body} className="text-xs" />
                          <p className={`text-[9px] mt-1 ${st.meta}`}>
                            {new Date(msg.ts).toLocaleString("zh-CN")}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                  {!convoListLoading && conversations.length === 0 ? (
                    <p className="text-center text-stardew-brown/50 text-xs py-4">暂无对话记录</p>
                  ) : null}
                </div>
              </div>

              {/* Event timeline */}
              <div className="parchment rounded-xl overflow-hidden">
                <p className="text-[10px] text-stardew-brown/60 px-3 pt-3">事件时间线（最近 100 条）</p>
                <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-stardew-cream">
                      <tr className="border-b-2 border-stardew-wood/20">
                        {["时间", "类型", "Session", "数据"].map((h) => (
                          <th key={h} className="px-3 py-3 text-left font-bold">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {timeline.map((ev, i) => (
                        <tr key={i} className="border-b border-stardew-wood/10 hover:bg-white/30">
                          <td className="px-3 py-2 whitespace-nowrap">{new Date(ev.ts).toLocaleString("zh-CN")}</td>
                          <td className="px-3 py-2 font-mono">{ev.type}</td>
                          <td className="px-3 py-2 font-mono max-w-[80px] truncate">{ev.session_id?.slice(0, 8) ?? "-"}</td>
                          <td className="px-3 py-2 max-w-[420px] font-mono text-[10px] align-top">
                            <ExpandableText text={ev.data ? JSON.stringify(ev.data) : "-"} />
                          </td>
                        </tr>
                      ))}
                      {timeline.length === 0 && (
                        <tr><td colSpan={4} className="px-3 py-6 text-center text-stardew-brown/50">暂无事件</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}

// ─── Shared sub-components ────────────────────────────────────────────────────

function MiniCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-white/40 rounded-lg p-2">
      <p className="text-[9px] text-stardew-brown/50">{label}</p>
      <p className="text-sm font-bold truncate">{value}</p>
    </div>
  );
}

function RetentionCell({ value }: { value: number }) {
  const n = Number(value);
  const color = n >= 50 ? "text-green-700" : n >= 30 ? "text-stardew-brown" : "text-red-600";
  return <span className={`font-bold ${color}`}>{n}%</span>;
}

const FUNNEL_ORDER = [
  "1_registered",
  "2_connected",
  "3_first_action",
  "4_first_conversation",
  "5_first_skill",
];

function FunnelChart({ funnel }: { funnel: { step: string; user_count: number }[] }) {
  if (funnel.length === 0) return <p className="text-xs text-stardew-brown/50">暂无漏斗数据</p>;
  const sorted = [...funnel].sort(
    (a, b) => FUNNEL_ORDER.indexOf(a.step) - FUNNEL_ORDER.indexOf(b.step),
  );
  const max = Math.max(...sorted.map((f) => f.user_count), 1);
  const stepLabels: Record<string, string> = {
    "1_registered": "注册",
    "2_connected": "Agent 已连接",
    "3_first_action": "首动作",
    "4_first_conversation": "首对话",
    "5_first_skill": "首技能完成",
  };
  return (
    <div className="space-y-2">
      {sorted.map((f, i) => {
        const pct = Math.round((f.user_count / max) * 100);
        const convPct = i > 0 ? Math.round((f.user_count / (sorted[i - 1]?.user_count || 1)) * 100) : 100;
        return (
          <div key={f.step}>
            <div className="flex justify-between text-[10px] text-stardew-brown/70 mb-0.5">
              <span>{stepLabels[f.step] ?? f.step}</span>
              <span>
                {f.user_count} 人 ({convPct}%)
              </span>
            </div>
            <div className="w-full bg-stardew-wood/10 rounded-full h-4">
              <div
                className="bg-stardew-wood/60 h-4 rounded-full transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function MiniBars<T extends Record<string, unknown>>({
  rows,
  val,
  label,
}: {
  rows: T[];
  val: (r: T) => number;
  label: (r: T) => string;
}) {
  if (rows.length === 0) return <p className="text-[10px] text-stardew-brown/50">暂无序列数据</p>;
  const max = Math.max(...rows.map(val), 1);
  return (
    <div className="flex items-end gap-0.5 h-28 border-b border-stardew-wood/15 pb-1">
      {rows.map((r, i) => {
        const v = val(r);
        const barH = Math.max(4, Math.round((v / max) * 72));
        return (
          <div key={i} className="flex-1 flex flex-col items-center justify-end min-w-0">
            <div
              className="w-full max-w-[14px] mx-auto rounded-t bg-stardew-wood/55"
              style={{ height: `${barH}px` }}
              title={`${label(r)}: ${v}`}
            />
            <span className="text-[7px] text-stardew-brown/45 truncate w-full text-center mt-0.5">
              {label(r)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function TokenCell({ token }: { token: string }) {
  const [open, setOpen] = useState(false);
  const mask =
    token.length > 14 ? `${token.slice(0, 6)}…${token.slice(-4)}` : "••••••";
  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-[10px] break-all">{open ? token : mask}</span>
      <div className="flex gap-1">
        <button
          type="button"
          className="text-[9px] text-stardew-blue underline"
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "隐藏" : "显示"}
        </button>
        <button
          type="button"
          className="text-[9px] text-stardew-blue underline"
          onClick={() => void navigator.clipboard.writeText(token)}
        >
          复制
        </button>
      </div>
    </div>
  );
}

function CopyableId({ id, maxLen = 8 }: { id: string; maxLen?: number }) {
  const [copied, setCopied] = useState(false);
  const display = id.length > maxLen ? `${id.slice(0, maxLen)}…` : id;
  const handleCopy = () => {
    void navigator.clipboard.writeText(id);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <span
      className="font-mono cursor-pointer hover:text-stardew-blue transition-colors relative group"
      onClick={handleCopy}
      title={`点击复制: ${id}`}
    >
      {display}
      {copied && (
        <span className="absolute -top-6 left-1/2 -translate-x-1/2 px-2 py-0.5 rounded bg-stardew-brown text-white text-[9px] whitespace-nowrap z-10">
          已复制
        </span>
      )}
    </span>
  );
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatShortDuration(iso: string | null): string {
  if (!iso) return "-";
  const delta = Date.now() - new Date(iso).getTime();
  if (delta < 0) return "刚刚";
  const s = Math.floor(delta / 1000);
  if (s < 60) return `${s} 秒前`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  return `${Math.floor(h / 24)} 天前`;
}

// ─── Tab: Live ───────────────────────────────────────────────────────────────

const STATE_COLOR: Record<string, string> = {
  running: "bg-green-500/80 text-white",
  starting: "bg-blue-500/80 text-white",
  authenticating: "bg-blue-400/80 text-white",
  connecting: "bg-blue-300/80 text-stardew-brown",
  degraded: "bg-yellow-500/80 text-white",
  suspended: "bg-yellow-400/80 text-stardew-brown",
  draining: "bg-orange-500/80 text-white",
  failed: "bg-red-600/80 text-white",
  destroyed: "bg-stardew-brown/50 text-white",
};

const STATE_LABELS: Record<string, string> = {
  running: "运行中（running）",
  starting: "启动中（starting）",
  authenticating: "认证中（authenticating）",
  connecting: "连接中（connecting）",
  degraded: "降级（degraded）",
  suspended: "挂起（suspended）",
  draining: "下线中（draining）",
  failed: "失败（failed）",
  destroyed: "已销毁（destroyed）",
};

function StateBadge({ state }: { state: string }) {
  const cls = STATE_COLOR[state] ?? "bg-stardew-brown/40 text-white";
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[10px] ${cls}`}>
      {STATE_LABELS[state] ?? `未知状态（${state}）`}
    </span>
  );
}

function relTime(iso: string | null): string {
  return formatShortDuration(iso);
}

function HostedRuntimeTab({ health }: { health: HostedRuntimeHealth | null }) {
  if (!health) return <p className="text-sm text-stardew-brown/60">暂无健康数据，请刷新。</p>;
  const runtime = health.runtime ?? {};
  const provider = health.provider ?? {};
  const workers = Object.entries(health.workers ?? {});
  const outboxes = Object.entries(health.outboxes ?? {});
  const sinks = Object.entries(health.sinks ?? {});
  const hostedGateway = health.hosted?.gateway;
  const hostedInput = health.hosted?.input;
  const hostedSummary = hostedGateway?.summary ?? {};
  const hostedQueue = hostedInput?.queue ?? {};
  const hostedDelivery = hostedInput?.delivery ?? {};
  const hostedSessions = hostedGateway?.sessions ?? [];
  const lastTakeover = hostedGateway?.last_takeover;
  const value = (item: unknown) => item == null ? "-" : String(item);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="font-bold text-base text-stardew-brown">Hosted Runtime 运维状态</h2>
          <p className="text-[11px] text-stardew-brown/60">只读诊断，不触发恢复动作；更新时间 {health.observed_at ? new Date(health.observed_at).toLocaleString("zh-CN") : "-"}</p>
        </div>
        <span className={`text-xs font-bold ${health.ok ? "text-green-700" : "text-red-700"}`}>{health.ok ? "正常" : "异常"}</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[["活动 Runtime", runtime.active_count], ["进程句柄", runtime.owned_handle_count], ["孤儿候选", runtime.orphan_candidate_count], ["隔离数量", runtime.quarantined_count]].map(([label, item]) => (
          <div key={String(label)} className="parchment p-3"><div className="text-[10px] text-stardew-brown/60">{label}</div><div className="text-xl font-bold text-stardew-brown">{value(item)}</div></div>
        ))}
      </div>
      <div className="parchment p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-bold text-sm text-stardew-brown">Hosted 连接与消息交付</h3>
          <span className="text-[10px] text-stardew-brown/60">
            最近补偿：{value(hostedDelivery.last_outcome)}
          </span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-2 text-xs">
          {[
            ["Controller", hostedSummary.controller],
            ["Observer", hostedSummary.observer],
            ["恢复中", hostedSummary.reconnecting],
            ["排队输入", hostedQueue.total],
            ["静默 Turn", hostedDelivery.silent_turns],
            ["补偿成功", hostedDelivery.compensation_delivered],
            ["补偿失败", hostedDelivery.compensation_failed],
          ].map(([label, item]) => (
            <div key={String(label)} className="border border-stardew-wood/15 p-2">
              <div className="text-[10px] text-stardew-brown/60">{label}</div>
              <div className="font-bold text-base text-stardew-brown">{value(item)}</div>
            </div>
          ))}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs text-stardew-brown/80">
          <span>队列：补偿 {value(hostedQueue.compensation)} / 玩家 {value(hostedQueue.player)} / 后台 {value(hostedQueue.background)}</span>
          <span>最近接管：{lastTakeover ? `${value(lastTakeover.outcome)} · 玩家 ${value(lastTakeover.target_player_id)} · rev ${value(lastTakeover.revision)}` : "-"}</span>
        </div>
        {hostedSessions.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="border-b border-stardew-wood/20">
                <th className="py-2 text-left">角色</th>
                <th className="py-2 text-left">Transport</th>
                <th className="py-2 text-left">目标玩家</th>
                <th className="py-2 text-left">Revision</th>
              </tr></thead>
              <tbody>{hostedSessions.map((item, index) => (
                <tr key={`${item.role}-${item.target_player_id}-${item.control_revision}-${index}`} className="border-b border-stardew-wood/10">
                  <td className="py-2">{value(item.role)}</td>
                  <td className={`py-2 ${item.transport_state === "reconnecting" ? "text-amber-700 font-bold" : ""}`}>{value(item.transport_state)}</td>
                  <td className="py-2 font-mono">{value(item.target_player_id)}</td>
                  <td className="py-2 font-mono">{value(item.control_revision)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </div>
      <div className="parchment p-4 space-y-3">
        <h3 className="font-bold text-sm text-stardew-brown">Provider</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-xs"><span>适配器：{value(provider.adapter_kind)}</span><span>最近失败：{provider.last_failure_at ? new Date(provider.last_failure_at).toLocaleString("zh-CN") : "无"}</span><span>错误码：{value(provider.last_failure_code)}</span></div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="parchment p-4"><h3 className="font-bold text-sm text-stardew-brown mb-2">后台 Worker</h3><div className="space-y-2">{workers.map(([name, item]) => <div key={name} className="flex justify-between border-b border-stardew-wood/10 pb-1 text-xs"><span>{name}</span><span className={item.last_error_code ? "text-red-700" : "text-stardew-brown/70"}>{item.last_error_code ?? (item.last_success_at ? "最近成功" : "暂无记录")}</span></div>)}</div></div>
        <div className="parchment p-4"><h3 className="font-bold text-sm text-stardew-brown mb-2">Outbox</h3><div className="space-y-2">{outboxes.map(([name, item]) => <div key={name} className="flex justify-between border-b border-stardew-wood/10 pb-1 text-xs"><span>{name}</span><span>{item.available === false ? (item.error_code ?? "不可用") : `待处理 ${value(item.pending_count)} · 认领 ${value(item.claimed_count)}`}</span></div>)}</div></div>
      </div>
      <div className="parchment p-4"><h3 className="font-bold text-sm text-stardew-brown mb-2">日志 Sink</h3><div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-xs">{sinks.map(([name, item]) => <div key={name}><span className="font-bold">{name}</span>：{item.writable === false ? "写入异常" : item.last_error_code ?? "可写"}</div>)}</div></div>
    </div>
  );
}

function LiveTab({
  data,
  gwStatus,
  onToggleMaintenance,
  onKickInstance,
}: {
  data: LiveInstancesData | null;
  gwStatus: {current_connections: number; max_connections: number; accepting: boolean} | null;
  onToggleMaintenance: () => void;
  onKickInstance: (instanceId: string) => void;
}) {
  if (!data) return <p className="text-center text-sm text-stardew-brown/50 py-8">暂无数据</p>;
  const running = data.summary.by_state["running"] ?? 0;
  const draining = data.summary.by_state["draining"] ?? 0;
  const failed = data.summary.by_state["failed"] ?? 0;
  const stateEntries = Object.entries(data.summary.by_state)
    .sort((a, b) => Number(b[1]) - Number(a[1]));

  return (
    <>
      {gwStatus && (
        <div className="flex items-center gap-4 mb-4 p-3 rounded-lg bg-neutral-50 dark:bg-neutral-800">
          <span className="text-sm">
            在线 <strong>{gwStatus.current_connections}</strong> / {gwStatus.max_connections}
          </span>
          <span className={`text-sm ${gwStatus.accepting ? 'text-green-600' : 'text-red-600'}`}>
            {gwStatus.accepting ? '🟢 接受连接' : '🔴 维护中'}
          </span>
          <button
            onClick={onToggleMaintenance}
            className={`ml-auto px-3 py-1 text-xs rounded ${
              gwStatus.accepting
                ? 'bg-yellow-100 text-yellow-800 hover:bg-yellow-200'
                : 'bg-green-100 text-green-800 hover:bg-green-200'
            }`}
          >
            {gwStatus.accepting ? '开启维护模式' : '恢复接受连接'}
          </button>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        <StatCard
          label={data.mode === "recent" ? `窗口内实例（${data.window_min}m）` : "存活中实例"}
          value={data.summary.total}
        />
        <StatCard label="运行中" value={running} />
        <StatCard label="下线中" value={draining} />
        <StatCard label="失败" value={failed} />
        <StatCard label="命令在飞" value={data.summary.inflight_commands} />
      </div>

      <div className="parchment rounded-xl p-4 mb-6">
        <p className="text-[10px] text-stardew-brown/60 mb-2">状态分布</p>
        {stateEntries.length === 0 ? (
          <p className="text-[10px] text-stardew-brown/50">无实例</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {stateEntries.map(([s, n]) => (
              <span key={s} className="flex items-center gap-1 text-[10px]">
                <StateBadge state={s} />
                <strong>{n}</strong>
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="parchment rounded-xl overflow-hidden">
        <p className="text-[10px] text-stardew-brown/60 px-3 pt-3">
          实例列表（{data.instances.length}）
        </p>
        <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-stardew-cream">
              <tr className="border-b-2 border-stardew-wood/20">
                {[
                  "实例 ID",
                  "用户",
                  "状态",
                  "工作进程",
                  "目标玩家",
                  "在飞",
                  "心跳前",
                  "最后事件",
                  "状态变更",
                  "结束原因",
                  "操作",
                ].map((h) => (
                  <th key={h} className="px-3 py-3 text-left font-bold whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.instances.map((r) => {
                const stale = r.stale_seconds;
                const staleWarn = stale !== null && stale > 60;
                return (
                <tr
                  key={r.agent_instance_id}
                  className={`border-b border-stardew-wood/10 ${staleWarn ? "bg-red-50/60 hover:bg-red-50" : "hover:bg-white/30"}`}
                >
                  <td className="px-3 py-2 font-mono text-[10px]" title={r.agent_instance_id}>
                    {r.agent_instance_id.slice(0, 14)}…
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px]" title={r.user_id ?? ""}>
                    {r.user_id ? <CopyableId id={r.user_id} /> : "-"}
                  </td>
                  <td className="px-3 py-2"><StateBadge state={r.state} /></td>
                  <td className="px-3 py-2 font-mono text-[10px]">{r.worker_id ?? "-"}</td>
                  <td className="px-3 py-2 font-mono text-[10px]">{r.target_player_id ?? "-"}</td>
                  <td className="px-3 py-2">{r.command_inflight_count}</td>
                  <td
                    className={`px-3 py-2 whitespace-nowrap ${staleWarn ? "text-red-600 font-bold" : ""}`}
                    title={r.updated_at}
                  >
                    {stale === null ? "-" : `${stale}s`}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap" title={r.last_game_event_at ?? ""}>
                    {relTime(r.last_game_event_at)}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap" title={r.state_changed_at}>
                    {relTime(r.state_changed_at)}
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px]">{r.ended_reason ?? "-"}</td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => onKickInstance(r.agent_instance_id)}
                      className="px-2 py-1 text-xs bg-red-100 text-red-700 rounded hover:bg-red-200"
                    >
                      踢出
                    </button>
                  </td>
                </tr>
                );
              })}
              {data.instances.length === 0 && (
                <tr><td colSpan={11} className="px-3 py-6 text-center text-stardew-brown/50">暂无实例</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

// ─── Tab: Roadshow ────────────────────────────────────────────────────────────

function roadshowUrl(routeKey: string): string {
  if (typeof window === "undefined") return `/roadshow?r=${encodeURIComponent(routeKey)}`;
  return `${window.location.origin}/roadshow?r=${encodeURIComponent(routeKey)}`;
}

function RoadshowTab({
  routes,
  recentInputs,
  deliveryEvents,
  platformToken,
  onPlatformTokenChange,
  onLookupToken,
  lookup,
  routeKey,
  onRouteKeyChange,
  title,
  onTitleChange,
  selectedInstance,
  onSelectedInstanceChange,
  enabled,
  onEnabledChange,
  onSave,
  saving,
  status,
  query,
  onQueryChange,
  filterRoute,
  onFilterRouteChange,
  filterStatus,
  onFilterStatusChange,
  from,
  onFromChange,
  to,
  onToChange,
  onRefresh,
}: {
  routes: RoadshowRoute[];
  recentInputs: RoadshowInput[];
  deliveryEvents: RoadshowDeliveryEvent[];
  platformToken: string;
  onPlatformTokenChange: (v: string) => void;
  onLookupToken: () => void;
  lookup: RoadshowLookupData | null;
  routeKey: string;
  onRouteKeyChange: (v: string) => void;
  title: string;
  onTitleChange: (v: string) => void;
  selectedInstance: string;
  onSelectedInstanceChange: (v: string) => void;
  enabled: boolean;
  onEnabledChange: (v: boolean) => void;
  onSave: () => void;
  saving: boolean;
  status: string;
  query: string;
  onQueryChange: (v: string) => void;
  filterRoute: string;
  onFilterRouteChange: (v: string) => void;
  filterStatus: string;
  onFilterStatusChange: (v: string) => void;
  from: string;
  onFromChange: (v: string) => void;
  to: string;
  onToChange: (v: string) => void;
  onRefresh: () => void;
}) {
  const currentUrl = roadshowUrl(routeKey.trim() || "main");
  const runningCount = lookup?.instances.filter((i) => i.state === "running").length ?? 0;
  const deliveredCount = recentInputs.filter((i) => i.delivery_status === "delivered").length;
  const failedCount = recentInputs.filter((i) => i.delivery_status === "delivery_failed").length;
  const deliveryStatusLabel = (value: string) => {
    const labels: Record<string, string> = {
      received: "已接收（received）",
      sending: "投递中（sending）",
      delivered: "已投递（delivered）",
      delivery_failed: "投递失败（delivery_failed）",
    };
    return labels[value] ?? `未知状态（${value}）`;
  };

  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        <StatCard label="入口数量" value={routes.length} />
        <StatCard label="筛选输入" value={recentInputs.length} />
        <StatCard label="已投递" value={deliveredCount} />
        <StatCard label="失败" value={failedCount} />
        <StatCard label="可选运行中实例" value={runningCount} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_0.9fr] gap-4 mb-6">
        <div className="parchment rounded-xl p-4">
          <p className="text-[10px] text-stardew-brown/60 mb-3">配置主展示页路演入口</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
            <label className="block text-[10px] text-stardew-brown/70">
              入口 Key
              <input
                value={routeKey}
                onChange={(e) => onRouteKeyChange(e.target.value)}
                className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1 font-mono"
              />
            </label>
            <label className="block text-[10px] text-stardew-brown/70">
              展示名称
              <input
                value={title}
                onChange={(e) => onTitleChange(e.target.value)}
                className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1"
              />
            </label>
          </div>

          <div className="flex gap-2 mb-4">
            <input
              type="password"
              value={platformToken}
              onChange={(e) => onPlatformTokenChange(e.target.value)}
              placeholder="输入目标用户的平台 Token（Platform Token）"
              className="flex-1 px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none"
              onKeyDown={(e) => {
                if (e.key === "Enter") onLookupToken();
              }}
            />
            <button
              type="button"
              onClick={onLookupToken}
              className="stardew-btn px-4 py-2 rounded-lg text-xs font-bold"
            >
              查询
            </button>
          </div>

          {lookup && (
            <div className="bg-white/35 border border-stardew-wood/15 rounded-xl p-3 mb-4">
              <div className="flex flex-wrap gap-2 items-center mb-3">
                <span className="text-xs font-bold">{lookup.user.nickname ?? "未命名用户"}</span>
                <span className="text-[10px] font-mono text-stardew-brown/55">{lookup.user.user_id}</span>
              </div>
              <label className="block text-[10px] text-stardew-brown/70 mb-2">
                目标智能体实例（Agent Instance）
                <select
                  value={selectedInstance}
                  onChange={(e) => onSelectedInstanceChange(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/70 text-xs focus:border-stardew-wood focus:outline-none mt-1"
                >
                  {lookup.instances.map((i) => (
                    <option key={i.agent_instance_id} value={i.agent_instance_id}>
                      {STATE_LABELS[i.state] ?? i.state} · {i.agent_instance_id} · 目标玩家={i.target_player_id ?? "-"} · 更新于 {relTime(i.updated_at)}
                    </option>
                  ))}
                </select>
              </label>
              {lookup.instances.length === 0 && (
                <p className="text-[10px] text-red-600">该用户暂无 agent_instances，请先让游戏连接 Gateway。</p>
              )}
            </div>
          )}

          <label className="flex items-center gap-2 text-[10px] text-stardew-brown/70 mb-4">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => onEnabledChange(e.target.checked)}
            />
            启用该入口
          </label>

          <div className="flex items-center gap-3">
            <button
              type="button"
              disabled={saving || !lookup || !selectedInstance}
              onClick={onSave}
              className="stardew-btn px-4 py-2 rounded-lg text-xs font-bold disabled:opacity-50"
            >
              {saving ? "保存中..." : "保存配置"}
            </button>
            {status && <span className="text-[10px] font-bold text-stardew-green">{status}</span>}
          </div>
        </div>

        <div className="parchment rounded-xl p-4">
          <p className="text-[10px] text-stardew-brown/60 mb-3">观众扫码链接</p>
          <QrCodePanel value={currentUrl} size={220} className="mb-4" />
          <div className="bg-white/45 rounded-xl border border-stardew-wood/15 p-3 mb-3">
            <p className="font-mono text-[10px] break-all">{currentUrl}</p>
          </div>
          <button
            type="button"
            onClick={() => navigator.clipboard?.writeText(currentUrl)}
            className="px-3 py-2 rounded-lg text-xs font-bold border-2 border-stardew-wood/30 hover:bg-white/40"
          >
            复制链接
          </button>
          <p className="text-[10px] text-stardew-brown/55 mt-4">
            二维码只包含入口 Key，不包含用户 Token。观众提交后由平台按这里保存的目标智能体（Agent）投递。
          </p>
        </div>
      </div>

      <div className="parchment rounded-xl p-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-[1.4fr_0.8fr_0.8fr_0.9fr_0.9fr_auto] gap-3 items-end">
          <label className="block text-[10px] text-stardew-brown/70">
            搜索内容 / 输入 ID（input_id）
            <input
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              placeholder="输入观众文本、清洗文本或输入 ID（input_id）"
              className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1"
            />
          </label>
          <label className="block text-[10px] text-stardew-brown/70">
            入口
            <select
              value={filterRoute}
              onChange={(e) => onFilterRouteChange(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1"
            >
              <option value="">全部</option>
              {routes.map((r) => (
                <option key={r.route_key} value={r.route_key}>{r.route_key}</option>
              ))}
            </select>
          </label>
          <label className="block text-[10px] text-stardew-brown/70">
            状态
            <select
              value={filterStatus}
              onChange={(e) => onFilterStatusChange(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1"
            >
              <option value="">全部</option>
              <option value="received">已接收（received）</option>
              <option value="sending">投递中（sending）</option>
              <option value="delivered">已投递（delivered）</option>
              <option value="delivery_failed">投递失败（delivery_failed）</option>
            </select>
          </label>
          <label className="block text-[10px] text-stardew-brown/70">
            开始时间
            <input
              type="datetime-local"
              value={from}
              onChange={(e) => onFromChange(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1"
            />
          </label>
          <label className="block text-[10px] text-stardew-brown/70">
            结束时间
            <input
              type="datetime-local"
              value={to}
              onChange={(e) => onToChange(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1"
            />
          </label>
          <button
            type="button"
            onClick={onRefresh}
            className="stardew-btn px-4 py-2 rounded-lg text-xs font-bold"
          >
            查询
          </button>
        </div>
      </div>

      <div className="parchment rounded-xl overflow-hidden mb-6">
        <p className="text-[10px] text-stardew-brown/60 px-3 pt-3">已配置入口</p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b-2 border-stardew-wood/20">
                {["入口 Key", "名称", "状态", "目标用户", "目标实例", "更新时间"].map((h) => (
                  <th key={h} className="px-3 py-3 text-left font-bold whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {routes.map((r) => (
                <tr key={r.route_key} className="border-b border-stardew-wood/10 hover:bg-white/30">
                  <td className="px-3 py-2 font-mono">{r.route_key}</td>
                  <td className="px-3 py-2">{r.title}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold ${r.enabled ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600"}`}>
                      {r.enabled ? "启用" : "停用"}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px]">{r.target_user_id ? `${r.target_user_id.slice(0, 10)}…` : "-"}</td>
                  <td className="px-3 py-2 font-mono text-[10px]">{r.target_agent_instance_id ? `${r.target_agent_instance_id.slice(0, 16)}…` : "-"}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{new Date(r.updated_at).toLocaleString("zh-CN")}</td>
                </tr>
              ))}
              {routes.length === 0 && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-stardew-brown/50">暂无入口配置</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="parchment rounded-xl overflow-hidden mb-6">
        <p className="text-[10px] text-stardew-brown/60 px-3 pt-3">观众输入记录</p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b-2 border-stardew-wood/20">
                {["时间", "入口", "状态", "原文", "清洗后", "目标实例", "错误"].map((h) => (
                  <th key={h} className="px-3 py-3 text-left font-bold whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {recentInputs.map((i) => (
                <tr key={i.input_id} className="border-b border-stardew-wood/10 hover:bg-white/30">
                  <td className="px-3 py-2 whitespace-nowrap">{new Date(i.received_at).toLocaleString("zh-CN")}</td>
                  <td className="px-3 py-2 font-mono">{i.route_key}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold ${i.delivery_status === "delivered" ? "bg-green-100 text-green-800" : i.delivery_status === "delivery_failed" ? "bg-red-100 text-red-800" : "bg-amber-100 text-amber-800"}`}>
                      {deliveryStatusLabel(i.delivery_status)}
                    </span>
                  </td>
                  <td className="px-3 py-2 max-w-[360px]">
                    <span className="whitespace-pre-wrap break-words">{i.text}</span>
                  </td>
                  <td className="px-3 py-2 max-w-[360px]">
                    <span className="whitespace-pre-wrap break-words">{i.sanitized_text ?? i.text}</span>
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px]">{i.agent_instance_id ? `${i.agent_instance_id.slice(0, 16)}…` : "-"}</td>
                  <td className="px-3 py-2 text-red-600 max-w-[180px] truncate" title={i.error ?? ""}>{i.error ?? "-"}</td>
                </tr>
              ))}
              {recentInputs.length === 0 && (
                <tr><td colSpan={7} className="px-3 py-6 text-center text-stardew-brown/50">暂无输入</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="parchment rounded-xl overflow-hidden">
        <p className="text-[10px] text-stardew-brown/60 px-3 pt-3">智能体（Agent）投递事件流</p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b-2 border-stardew-wood/20">
                {["时间", "事件", "状态", "入口", "实际投递文本", "目标实例", "输入 ID", "错误"].map((h) => (
                  <th key={h} className="px-3 py-3 text-left font-bold whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {deliveryEvents.map((e) => (
                <tr key={e.id} className="border-b border-stardew-wood/10 hover:bg-white/30">
                  <td className="px-3 py-2 whitespace-nowrap">{new Date(e.created_at).toLocaleString("zh-CN")}</td>
                  <td className="px-3 py-2 font-mono">{e.event_type}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold ${e.delivery_status === "delivered" ? "bg-green-100 text-green-800" : e.delivery_status === "delivery_failed" ? "bg-red-100 text-red-800" : "bg-amber-100 text-amber-800"}`}>
                      {deliveryStatusLabel(e.delivery_status)}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono">{e.route_key}</td>
                  <td className="px-3 py-2 max-w-[420px]">
                    <span className="whitespace-pre-wrap break-words">{e.sanitized_text ?? "-"}</span>
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px]">{e.agent_instance_id ? `${e.agent_instance_id.slice(0, 16)}…` : "-"}</td>
                  <td className="px-3 py-2 font-mono text-[10px] max-w-[180px] truncate" title={e.input_id}>{e.input_id}</td>
                  <td className="px-3 py-2 text-red-600 max-w-[200px] truncate" title={e.error ?? ""}>{e.error ?? "-"}</td>
                </tr>
              ))}
              {deliveryEvents.length === 0 && (
                <tr><td colSpan={8} className="px-3 py-6 text-center text-stardew-brown/50">暂无投递事件</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

// ─── Tab: Official Invites ───────────────────────────────────────────────────

const OFFICIAL_INVITE_STATUS_LABEL: Record<OfficialInviteCode["usage"]["status"], string> = {
  active: "可用",
  disabled: "已禁用",
  expired: "已过期",
  exhausted: "已用尽",
};

const OFFICIAL_INVITE_STATUS_BADGE: Record<OfficialInviteCode["usage"]["status"], string> = {
  active: "bg-green-100 text-green-800",
  disabled: "bg-gray-100 text-gray-600",
  expired: "bg-amber-100 text-amber-800",
  exhausted: "bg-red-100 text-red-800",
};

function formatInviteCapacity(value: number | null): string {
  return value == null ? "不限" : value.toLocaleString("zh-CN");
}

function officialInviteRemainingText(code: OfficialInviteCode): string {
  if (code.max_uses == null) return "不限";
  return Math.max(0, Number(code.max_uses) - Number(code.used_count)).toLocaleString("zh-CN");
}

function OfficialInviteStatusBadge({ status }: { status: OfficialInviteCode["usage"]["status"] }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold ${OFFICIAL_INVITE_STATUS_BADGE[status]}`}>
      {OFFICIAL_INVITE_STATUS_LABEL[status]}
    </span>
  );
}

function OfficialInvitesTab({
  codes,
  summary,
  form,
  onFormChange,
  busy,
  toast,
  onCreate,
  onDisable,
  onIncreaseMaxUses,
}: {
  codes: OfficialInviteCode[];
  summary: OfficialInviteSummary | null;
  form: OfficialInviteCreateForm;
  onFormChange: (patch: Partial<OfficialInviteCreateForm>) => void;
  busy: boolean;
  toast: { kind: "ok" | "err"; msg: string } | null;
  onCreate: () => void;
  onDisable: (code: string) => void;
  onIncreaseMaxUses: (code: string, maxUses: number) => void;
}) {
  const [openCode, setOpenCode] = useState<string | null>(null);
  const [addUseInputs, setAddUseInputs] = useState<Record<string, string>>({});
  const createDisabled =
    busy ||
    !Number.isInteger(form.quantity) ||
    form.quantity <= 0 ||
    form.quantity > 100 ||
    !Number.isInteger(form.maxUses) ||
    form.maxUses <= 0;

  function addInputValueFor(code: OfficialInviteCode): string {
    if (addUseInputs[code.code] != null) return addUseInputs[code.code];
    if (code.max_uses == null) return "";
    return "1";
  }

  return (
    <>
      {toast && (
        <div
          className={`mb-3 px-3 py-2 rounded text-[11px] font-bold ${
            toast.kind === "ok"
              ? "bg-green-50 text-green-800 border border-green-200"
              : "bg-red-50 text-red-800 border border-red-200"
          }`}
        >
          {toast.msg}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatCard label="官方码总数" value={summary?.total_codes ?? codes.length} />
        <StatCard label="可用官方码" value={summary?.active_codes ?? codes.filter((c) => c.usage.status === "active").length} />
        <StatCard label="已使用次数" value={summary?.total_used ?? codes.reduce((sum, c) => sum + Number(c.used_count), 0)} />
        <StatCard label="剩余容量" value={formatInviteCapacity(summary?.total_remaining ?? null)} />
      </div>

      <div className="parchment rounded-xl p-4 mb-6">
        <p className="text-[10px] text-stardew-brown/60 mb-3">批量创建官方邀请码</p>
        <div className="grid grid-cols-1 md:grid-cols-[120px_140px_160px_1fr_auto] gap-3 items-end">
          <label className="block text-[10px] text-stardew-brown/70">
            数量
            <input
              type="number"
              min={1}
              max={100}
              value={form.quantity}
              onChange={(e) => onFormChange({ quantity: Number(e.target.value) })}
              className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1"
            />
          </label>
          <label className="block text-[10px] text-stardew-brown/70">
            单码容量
            <input
              type="number"
              min={1}
              value={form.maxUses}
              onChange={(e) => onFormChange({ maxUses: Number(e.target.value) })}
              className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1"
            />
          </label>
          <label className="block text-[10px] text-stardew-brown/70">
            前缀
            <input
              value={form.prefix}
              onChange={(e) => onFormChange({ prefix: e.target.value.toUpperCase() })}
              className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1 font-mono"
            />
          </label>
          <label className="block text-[10px] text-stardew-brown/70">
            备注 / 投放来源
            <input
              value={form.reason}
              onChange={(e) => onFormChange({ reason: e.target.value })}
              placeholder="例如 summer-roadshow-2026"
              className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none mt-1"
            />
          </label>
          <button
            type="button"
            disabled={createDisabled}
            onClick={onCreate}
            className="stardew-btn px-4 py-2 rounded-lg text-xs font-bold disabled:opacity-50"
          >
            批量创建
          </button>
        </div>
      </div>

      <div className="parchment rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs min-w-[1040px]">
            <thead>
              <tr className="border-b-2 border-stardew-wood/20">
                {["邀请码", "状态", "备注", "容量", "已用", "剩余", "受邀用户", "创建时间", "操作"].map((h) => (
                  <th key={h} className="px-3 py-3 text-left font-bold whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {codes.map((code) => {
                const additionalUses = Number(addInputValueFor(code));
                const canIncrease =
                  code.max_uses != null &&
                  Number.isInteger(additionalUses) &&
                  additionalUses > 0;
                return (
                  <Fragment key={code.code}>
                    <tr className="border-b border-stardew-wood/10 hover:bg-white/30 align-top">
                      <td className="px-3 py-2 font-mono">
                        <CopyableId id={code.code} maxLen={22} />
                      </td>
                      <td className="px-3 py-2"><OfficialInviteStatusBadge status={code.usage.status} /></td>
                      <td className="px-3 py-2 max-w-[220px]">
                        <span className="line-clamp-2" title={code.official_code_reason ?? ""}>{code.official_code_reason ?? "-"}</span>
                      </td>
                      <td className="px-3 py-2 font-mono">{formatInviteCapacity(code.max_uses)}</td>
                      <td className="px-3 py-2 font-mono">{Number(code.used_count).toLocaleString("zh-CN")}</td>
                      <td className="px-3 py-2 font-mono">{officialInviteRemainingText(code)}</td>
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => setOpenCode((current) => (current === code.code ? null : code.code))}
                          className="text-stardew-blue underline"
                        >
                          {code.invited_users.length} 人
                        </button>
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">{new Date(code.created_at).toLocaleString("zh-CN")}</td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-2 items-center">
                          <input
                            type="number"
                            min={1}
                            value={addInputValueFor(code)}
                            disabled={busy || code.max_uses == null}
                            onChange={(e) => setAddUseInputs((current) => ({ ...current, [code.code]: e.target.value }))}
                            className="w-24 px-2 py-1 rounded border border-stardew-wood/30 bg-white/60 text-[10px] font-mono disabled:opacity-50"
                            placeholder={code.max_uses == null ? "不限" : "增加次数"}
                          />
                          <button
                            type="button"
                            disabled={busy || !canIncrease}
                            onClick={() => onIncreaseMaxUses(code.code, additionalUses)}
                            className="px-2 py-1 rounded text-[10px] font-bold border border-stardew-wood/30 hover:bg-white/40 disabled:opacity-50"
                          >
                            增加次数
                          </button>
                          <button
                            type="button"
                            disabled={busy || code.disabled}
                            onClick={() => onDisable(code.code)}
                            className="px-2 py-1 rounded text-[10px] font-bold bg-red-100 text-red-700 hover:bg-red-200 disabled:opacity-50"
                          >
                            禁用
                          </button>
                        </div>
                      </td>
                    </tr>
                    {openCode === code.code && (
                      <tr className="border-b border-stardew-wood/10 bg-white/25">
                        <td colSpan={9} className="px-3 py-3">
                          <OfficialInviteUsersTable users={code.invited_users} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
              {codes.length === 0 && (
                <tr><td colSpan={9} className="px-3 py-8 text-center text-stardew-brown/50">暂无官方邀请码</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function OfficialInviteUsersTable({ users }: { users: OfficialInviteUser[] }) {
  if (users.length === 0) {
    return <p className="text-[11px] text-stardew-brown/50">这个官方码还没有邀请到用户。</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="border-b border-stardew-wood/15">
            {["用户", "邮箱", "微信", "身份", "业务来源", "记录来源", "注册/归因时间"].map((h) => (
              <th key={h} className="px-2 py-2 text-left font-bold whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.user_id} className="border-b border-stardew-wood/10">
              <td className="px-2 py-2">
                <div className="font-bold">{user.nickname ?? "-"}</div>
                <CopyableId id={user.user_id} maxLen={14} />
              </td>
              <td className="px-2 py-2 font-mono">{user.email ?? "-"}</td>
              <td className="px-2 py-2">{user.wechat ?? "-"}</td>
              <td className="px-2 py-2">{user.user_type ?? "-"}</td>
              <td className="px-2 py-2">{user.source ?? "-"}</td>
              <td className="px-2 py-2 font-mono">{user.usage_source ?? "-"}</td>
              <td className="px-2 py-2 whitespace-nowrap">{new Date(user.created_at).toLocaleString("zh-CN")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Tab: Waitlist ───────────────────────────────────────────────────────────

const WAITLIST_STATUS_LABEL: Record<WaitlistStatus, string> = {
  pending: "待审核",
  approved: "已通过",
  rejected: "已驳回",
  registered: "已注册",
  unknown: "未知",
};

const WAITLIST_STATUS_BADGE: Record<WaitlistStatus, string> = {
  pending: "bg-amber-100 text-amber-800",
  approved: "bg-green-100 text-green-800",
  rejected: "bg-red-100 text-red-800",
  registered: "bg-blue-100 text-blue-800",
  unknown: "bg-gray-100 text-gray-600",
};

function ContactList({ entry }: { entry: WaitlistEntry }) {
  const items: { type: string; value: string }[] = [];
  if (entry.email) items.push({ type: "邮箱", value: entry.email });
  if (entry.wechat) items.push({ type: "微信", value: entry.wechat });
  if (entry.qq) items.push({ type: "QQ", value: entry.qq });
  if (items.length === 0) {
    return <span className="text-stardew-brown/30">未填写</span>;
  }
  return (
    <div className="flex flex-col gap-0.5">
      {items.map((it) => (
        <div key={it.type} className="flex items-center gap-1">
          <span className="inline-block px-1 rounded bg-stardew-wood/10 text-[9px] text-stardew-brown/60">
            {it.type}
          </span>
          <span className="font-mono text-[11px]">{it.value}</span>
        </div>
      ))}
    </div>
  );
}

function WaitlistTab({
  entries,
  total,
  filter,
  mutatingId,
  onUpdateStatus,
  toast,
  page,
  pageSize,
  search,
  onSearchChange,
  onPageChange,
  onPageSizeChange,
  loading,
  onViewDetail,
}: {
  entries: WaitlistEntry[];
  total: number;
  filter: WaitlistFilter;
  mutatingId: number | null;
  onUpdateStatus: (id: number, status: "pending" | "approved" | "rejected") => void | Promise<void>;
  toast: { kind: "ok" | "err"; msg: string } | null;
  page: number;
  pageSize: number;
  search: string;
  onSearchChange: (v: string) => void;
  onPageChange: (p: number) => void;
  onPageSizeChange: (s: number) => void;
  loading: boolean;
  onViewDetail: (id: number) => void;
}) {
  return (
    <>
      {toast && (
        <div
          className={`mb-3 px-3 py-2 rounded text-[11px] font-bold ${
            toast.kind === "ok"
              ? "bg-green-50 text-green-800 border border-green-200"
              : "bg-red-50 text-red-800 border border-red-200"
          }`}
        >
          {toast.msg}
        </div>
      )}

      <div className="mb-4 flex flex-col md:flex-row md:items-center gap-3">
        <input
          type="text"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="搜索 email / 微信 / QQ…"
          className="w-full md:w-80 px-3 py-2 rounded-lg border border-stardew-wood/20 bg-white/50 text-sm placeholder:text-stardew-brown/40 focus:outline-none focus:ring-2 focus:ring-stardew-green/30"
        />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
        <StatCard
          label={`当前过滤：${filter === "all" ? "全部" : WAITLIST_STATUS_LABEL[filter as WaitlistStatus] ?? filter}`}
          value={total}
        />
        <StatCard label="本页条数" value={entries.length} />
        <StatCard label="可操作" value={entries.filter((e) => e.status === "pending" || e.status === "approved" || e.status === "rejected").length} />
      </div>

      <div className="parchment rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b-2 border-stardew-wood/20">
                {["#", "联系方式", "留言", "状态", "申请时间", "操作"].map((h) => (
                  <th key={h} className="px-3 py-3 text-left font-bold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entries.map((e, i) => {
                const canActPending = e.status === "pending" || e.status === "approved" || e.status === "rejected";
                const isMutating = mutatingId === e.id;
                return (
                  <tr key={e.id} className="border-b border-stardew-wood/10 hover:bg-white/30 align-top">
                    <td className="px-3 py-2 text-stardew-brown/40">#{e.id}<span className="text-stardew-brown/30 ml-1">({entries.length - i})</span></td>
                    <td className="px-3 py-2"><ContactList entry={e} /></td>
                    <td className="px-3 py-2 max-w-[300px]">
                      {e.message ? (
                        <span className="whitespace-pre-wrap break-words">{e.message}</span>
                      ) : (
                        <span className="text-stardew-brown/30">未填写</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold ${
                          WAITLIST_STATUS_BADGE[e.status] ?? "bg-gray-100 text-gray-600"
                        }`}
                      >
                        {WAITLIST_STATUS_LABEL[e.status] ?? e.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">{new Date(e.created_at).toLocaleString("zh-CN")}</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <div className="flex flex-col gap-1.5">
                        {(e.status === "approved" || e.status === "registered") && (
                          <button
                            type="button"
                            onClick={() => onViewDetail(e.id)}
                            className="px-2 py-1 rounded text-[10px] font-bold bg-blue-100 text-blue-800 hover:bg-blue-200"
                          >
                            查看
                          </button>
                        )}
                        {e.status === "registered" ? (
                          <span className="text-stardew-brown/40 text-[10px]">已完成注册</span>
                        ) : canActPending ? (
                          <div className="flex gap-1.5">
                            {e.status !== "approved" && (
                              <button
                                type="button"
                                disabled={isMutating}
                                onClick={() => void onUpdateStatus(e.id, "approved")}
                                className="px-2 py-1 rounded text-[10px] font-bold bg-green-100 text-green-800 hover:bg-green-200 disabled:opacity-50 disabled:cursor-not-allowed"
                              >
                                {isMutating ? "..." : "开通"}
                              </button>
                            )}
                            {e.status !== "rejected" && (
                              <button
                                type="button"
                                disabled={isMutating}
                                onClick={() => void onUpdateStatus(e.id, "rejected")}
                                className="px-2 py-1 rounded text-[10px] font-bold bg-red-100 text-red-800 hover:bg-red-200 disabled:opacity-50"
                              >
                                {isMutating ? "..." : "驳回"}
                              </button>
                            )}
                            {e.status !== "pending" && (
                              <button
                                type="button"
                                disabled={isMutating}
                                onClick={() => void onUpdateStatus(e.id, "pending")}
                                className="px-2 py-1 rounded text-[10px] font-bold bg-amber-100 text-amber-800 hover:bg-amber-200 disabled:opacity-50"
                              >
                                {isMutating ? "..." : "改回待审"}
                              </button>
                            )}
                          </div>
                        ) : (
                          <span className="text-stardew-brown/40 text-[10px]">—</span>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
              {entries.length === 0 && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-stardew-brown/50">暂无预约申请</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex items-center justify-between mt-4 text-[11px]">
        <span className="text-stardew-brown/60">共 {total} 条</span>
        <div className="flex items-center gap-2">
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            className="px-2 py-1 rounded border border-stardew-wood/20 bg-white/50 text-[11px]"
          >
            <option value={20}>20 条/页</option>
            <option value={50}>50 条/页</option>
            <option value={100}>100 条/页</option>
          </select>
          <button type="button" disabled={page <= 1 || loading} onClick={() => onPageChange(page - 1)} className="stardew-btn px-2 py-1 rounded disabled:opacity-40">上一页</button>
          <span className="text-stardew-brown/70">{page} / {Math.max(1, Math.ceil(total / pageSize))}</span>
          <button type="button" disabled={page >= Math.ceil(total / pageSize) || loading} onClick={() => onPageChange(page + 1)} className="stardew-btn px-2 py-1 rounded disabled:opacity-40">下一页</button>
        </div>
      </div>
    </>
  );
}

// ─── Waitlist Detail Steps ────────────────────────────────────────────────────

function WaitlistDetailSteps({ detail }: { detail: WaitlistUserDetail }) {
  const steps: {
    label: string;
    done: boolean;
    time: string | null;
    sub?: string | null;
  }[] = [
    {
      label: "审核通过",
      done: detail.status === "approved" || detail.status === "registered",
      time: null,
    },
    {
      label: "邮件发送",
      done: detail.email_sent,
      time: null,
      sub: detail.resend_email_id ? `Resend ID: ${detail.resend_email_id}` : null,
    },
    {
      label: "密码设置",
      done: detail.password_set,
      time: detail.password_set_at,
    },
    {
      label: "首次登录",
      done: detail.first_login,
      time: detail.first_login_at,
    },
  ];

  return (
    <div className="space-y-3">
      {steps.map((step, i) => (
        <div key={step.label} className="flex items-start gap-3">
          {/* connector line + icon */}
          <div className="flex flex-col items-center shrink-0">
            <div
              className={`w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold border-2 ${
                step.done
                  ? "bg-green-100 border-green-400 text-green-700"
                  : "bg-gray-100 border-gray-300 text-gray-400"
              }`}
            >
              {step.done ? "✓" : String(i + 1)}
            </div>
            {i < steps.length - 1 && (
              <div className={`w-0.5 h-6 mt-1 ${step.done ? "bg-green-300" : "bg-gray-200"}`} />
            )}
          </div>
          {/* content */}
          <div className="pt-0.5 min-w-0">
            <p
              className={`text-sm font-bold ${
                step.done ? "text-green-800" : "text-stardew-brown/40"
              }`}
            >
              {step.label}
            </p>
            {step.done && step.time && (
              <p className="text-[10px] text-stardew-brown/60 mt-0.5">
                {new Date(step.time).toLocaleString("zh-CN")}
              </p>
            )}
            {step.done && step.sub && (
              <p className="text-[10px] font-mono text-stardew-brown/50 mt-0.5 break-all">
                {step.sub}
              </p>
            )}
            {!step.done && (
              <p className="text-[10px] text-stardew-brown/35 mt-0.5">未完成</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
