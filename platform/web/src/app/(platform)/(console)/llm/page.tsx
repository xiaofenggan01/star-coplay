"use client";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";

type SummaryGranularity = "all" | "hour" | "day" | "month";

const SUMMARY_RANGE_OPTIONS = [
  { label: "7 天", days: 7 },
  { label: "30 天", days: 30 },
  { label: "90 天", days: 90 },
];

const SUMMARY_GRANULARITY_OPTIONS: Array<{ label: string; value: SummaryGranularity }> = [
  { label: "汇总", value: "all" },
  { label: "小时", value: "hour" },
  { label: "天", value: "day" },
  { label: "月", value: "month" },
];

const SUMMARY_PAGE_SIZE = 8;

interface RelayAccount {
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

interface RelayQuota {
  quota: number;
  used_quota: number;
  request_count: number;
  token: {
    id: number | null;
    name: string | null;
    unlimited_quota: boolean;
    total_granted: number | null;
    total_used: number | null;
    total_available: number | null;
    expires_at: unknown;
    model_limits_enabled: boolean;
    model_limits: unknown;
  };
  display: {
    balance_points: number;
    used_points: number;
    token_available_points: number | null;
    balance_credits?: number | null;
    used_credits?: number | null;
    token_available_credits?: number | null;
  };
}

interface UsageBucket {
  created_at: unknown;
  model_name: string | null;
  count: number;
  quota: number;
  token_used: number;
  display?: {
    quota_credits?: number | null;
  };
}

interface UsageChartSegment {
  model_name: string | null;
  count: number;
  quota: number;
  token_used: number;
  quota_percent: number;
  display?: {
    quota_credits?: number | null;
  };
}

interface UsageChartBucket {
  key: string;
  label: string;
  created_at: number | null;
  quota: number;
  count: number;
  token_used: number;
  segments: UsageChartSegment[];
  display?: {
    quota_credits?: number | null;
  };
}

interface ModelUsageSummaryItem {
  model_name: string | null;
  count: number;
  quota: number;
  token_used: number;
  quota_share?: number;
  quota_percent?: number;
  display?: {
    quota_credits?: number | null;
  };
}

interface ModelUsageSummaries {
  page: number;
  page_size: number;
  total: number;
  total_quota?: number;
  items: ModelUsageSummaryItem[];
}

interface UsageSummary {
  range?: {
    start_timestamp: number | null;
    end_timestamp: number | null;
  };
  granularity?: SummaryGranularity;
  quota: number;
  rpm: number;
  tpm: number;
  buckets: UsageBucket[];
  model_summaries?: ModelUsageSummaries;
  display?: {
    quota_credits?: number | null;
  };
}

interface UsageLog {
  id?: number | string;
  created_at?: number | string;
  type?: number;
  content?: string;
  other?: unknown;
  token_name?: string;
  model_name?: string;
  quota?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  use_time?: number;
  is_stream?: boolean;
  channel_name?: string;
  group?: string;
  request_id?: string;
  display?: {
    quota_credits?: number | null;
  };
}

interface UsageLogs {
  page: number;
  page_size: number;
  total: number;
  items: UsageLog[];
}

interface QuotaLedger {
  page: number;
  page_size: number;
  total: number;
  items: UsageLog[];
  included_log_types: number[];
  excluded_log_types: number[];
}

interface ModelTierOption {
  tier: string;
  model_count: number;
  selected: boolean;
}

interface BackendIssue {
  label: string;
  status: number;
  code: string;
  message: string;
}

interface AIRelayPayload {
  user_id: string;
  account: RelayAccount | null;
  quota: RelayQuota | null;
  usage_summary: UsageSummary | null;
  usage_logs: UsageLogs | null;
  quota_ledger: QuotaLedger | null;
  enabled_models: string[];
  model_tiers: ModelTierOption[];
  default_model_tier: string | null;
  setup_required: boolean;
  checked_at: number;
  ranges?: {
    usage_summary_days: number;
    usage_logs_days: number;
    quota_ledger_days: number;
  };
  errors: BackendIssue[];
}

const TIER_LABELS: Record<string, string> = {
  fast: "快速响应",
  balanced: "日常均衡",
  best: "效果优先",
  quality: "质量优先",
  reasoning: "分析规划",
};

const TIER_HELP: Record<string, string> = {
  fast: "反应最快，适合日常闲聊和简单任务，体力值消耗最低。",
  balanced: "聪明程度和体力消耗比较均衡，适合作为日常默认选择。",
  best: "搭子会更认真地组织答案，效果更好，体力值消耗更高。",
  quality: "偏向高质量表达和整理，适合创作任务，体力值消耗较高。",
  reasoning: "搭子会花更多精力思考和规划，最聪明，也最消耗体力值。",
};

export default function LlmPage() {
  const router = useRouter();
  const supabase = useMemo(() => createClient(), []);

  const [data, setData] = useState<AIRelayPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [ensuring, setEnsuring] = useState(false);
  const [loadingMoreUsage, setLoadingMoreUsage] = useState(false);
  const [loadingMoreLedger, setLoadingMoreLedger] = useState(false);
  const [detailPanel, setDetailPanel] = useState<"ledger" | "usage" | null>(null);
  const [showRechargeDialog, setShowRechargeDialog] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [draftTier, setDraftTier] = useState("");
  const [summaryRangeDays, setSummaryRangeDays] = useState(30);
  const [summaryGranularity, setSummaryGranularity] = useState<SummaryGranularity>("all");

  useEffect(() => {
    void loadRelay();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function getToken(): Promise<string | null> {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session?.access_token) {
      router.push("/login");
      return null;
    }
    return session.access_token;
  }

  function buildRelayQuery(options: { summaryPage?: number; rangeDays?: number; granularity?: SummaryGranularity } = {}): string {
    const now = Math.floor(Date.now() / 1000);
    const rangeDays = options.rangeDays ?? summaryRangeDays;
    const granularity = options.granularity ?? summaryGranularity;
    const summaryStart = now - rangeDays * 24 * 60 * 60;
    const params = new URLSearchParams();
    params.set("summary_start_timestamp", String(summaryStart));
    params.set("summary_end_timestamp", String(now));
    params.set("summary_granularity", granularity);
    params.set("summary_page", String(options.summaryPage ?? 1));
    params.set("summary_page_size", String(SUMMARY_PAGE_SIZE));
    return params.toString();
  }

  async function loadRelay(
    options: {
      silent?: boolean;
      summaryPage?: number;
      appendSummary?: boolean;
      rangeDays?: number;
      granularity?: SummaryGranularity;
    } = {},
  ) {
    if (options.silent) setRefreshing(true);
    else setLoading(true);
    setMessage(null);
    try {
      const token = await getToken();
      if (!token) return;

      const query = buildRelayQuery({
        summaryPage: options.summaryPage,
        rangeDays: options.rangeDays,
        granularity: options.granularity,
      });
      const res = await fetch(`/api/me/ai-relay?${query}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const body = (await res.json().catch(() => null)) as AIRelayPayload | { error?: string; reason?: string } | null;
      if (!res.ok) {
        const denied = body && "reason" in body && body.reason === "llm_ui_access_denied";
        setMessage({
          type: "error",
          text: denied ? "当前账号暂未开放 AI 用量功能" : "读取 AI 用量失败，请稍后重试。",
        });
        return;
      }

      const payload = body as AIRelayPayload;
      setData((prev) => {
        if (!options.appendSummary || !prev) return payload;
        return {
          ...prev,
          usage_summary: mergeUsageSummary(prev.usage_summary, payload.usage_summary),
          errors: payload.errors,
        };
      });
      setDraftTier(payload.default_model_tier || payload.model_tiers[0]?.tier || "");
      if (payload.errors.some((item) => item.label === "quota" || item.label === "usage_summary")) {
        setMessage({ type: "error", text: "部分用量数据暂时不可用，已显示可读取的信息。" });
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  async function ensureRelayAccount() {
    setEnsuring(true);
    setMessage(null);
    try {
      const token = await getToken();
      if (!token) return;

      const res = await fetch("/api/me/ai-relay", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        await res.json().catch(() => null);
        setMessage({ type: "error", text: "准备 AI 服务失败，请稍后重试。" });
        return;
      }
      setMessage({ type: "success", text: "AI 服务已准备好。" });
      await loadRelay({ silent: true });
    } finally {
      setEnsuring(false);
    }
  }

  async function saveTier() {
    if (!data?.account || !draftTier) return;
    setSaving(true);
    setMessage(null);
    try {
      const token = await getToken();
      if (!token) return;

      const res = await fetch("/api/me/ai-relay", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ default_model_tier: draftTier }),
      });
      await res.json().catch(() => null);
      if (!res.ok) {
        setMessage({ type: "error", text: "保存使用模式失败，请稍后重试。" });
        return;
      }
      setMessage({ type: "success", text: "默认使用模式已保存。" });
      await loadRelay({ silent: true });
    } finally {
      setSaving(false);
    }
  }

  async function loadMore(kind: "usage" | "ledger") {
    if (!data) return;
    const currentPage = kind === "usage" ? data.usage_logs?.page ?? 1 : data.quota_ledger?.page ?? 1;
    if (kind === "usage") setLoadingMoreUsage(true);
    else setLoadingMoreLedger(true);
    setMessage(null);
    try {
      const token = await getToken();
      if (!token) return;
      const params = new URLSearchParams();
      params.set(kind === "usage" ? "usage_page" : "ledger_page", String(currentPage + 1));
      const res = await fetch(`/api/me/ai-relay?${params.toString()}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const body = (await res.json().catch(() => null)) as AIRelayPayload | { error?: string } | null;
      if (!res.ok) {
        setMessage({ type: "error", text: "加载更多失败，请稍后重试。" });
        return;
      }
      const payload = body as AIRelayPayload;
      setData((prev) => {
        if (!prev) return payload;
        if (kind === "usage") {
          return {
            ...prev,
            usage_logs: mergePagedLogs(prev.usage_logs, payload.usage_logs),
            errors: payload.errors,
          };
        }
        return {
          ...prev,
          quota_ledger: mergePagedLogs(prev.quota_ledger, payload.quota_ledger),
          errors: payload.errors,
        };
      });
    } finally {
      if (kind === "usage") setLoadingMoreUsage(false);
      else setLoadingMoreLedger(false);
    }
  }

  const dirty = !!data?.account && !!draftTier && draftTier !== (data.default_model_tier || "");

  if (loading) {
    return (
      <main className="mx-auto max-w-6xl px-2 py-4 md:px-0">
        <div className="rounded-lg border border-[#e8dcc8] bg-white px-5 py-4 text-[13px] text-[#8b7355]">
          加载 AI 用量中...
        </div>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="mx-auto max-w-6xl px-2 py-4 md:px-0">
        <div className="rounded-lg border border-[#e8dcc8] bg-white p-5">
          <p className="text-[13px] text-[#4a3728]">无法读取 AI 用量信息。</p>
          {message && <p className="mt-2 text-[12px] text-[#D64045]">{message.text}</p>}
          <button
            type="button"
            onClick={() => loadRelay()}
            className="mt-4 rounded-lg bg-[#f5c842] px-4 py-2 text-[12px] font-semibold text-[#4a3728]"
          >
            重新加载
          </button>
        </div>
      </main>
    );
  }

  const quota = data.quota;
  const usage = data.usage_summary;
  const logs = data.usage_logs?.items ?? [];
  const ledger = data.quota_ledger?.items ?? [];
  const chartBuckets = buildUsageChartBuckets(usage?.buckets, summaryGranularity);
  const maxChartQuota = maxBucketQuota(chartBuckets);
  const hasMoreUsage = !!data.usage_logs && data.usage_logs.items.length < data.usage_logs.total;
  const hasMoreLedger = !!data.quota_ledger && data.quota_ledger.items.length < data.quota_ledger.total;
  const usagePercent = quota ? percent(quota.used_quota, quota.quota) : 0;
  const visibleIssues = data.errors.filter((issue) => issue.label !== "health");

  return (
    <main className="mx-auto max-w-6xl px-2 py-4 md:px-0">
      <header className="mb-5 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[#4a3728]">AI 用量</h1>
          <p className="mt-1 text-[12px] text-[#8b7355]">
            搭子的余额看这里。余额不够时，搭子会暂时下线休息。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => loadRelay({ silent: true })}
            disabled={refreshing}
            className="rounded-lg border border-[#e8dcc8] bg-white px-4 py-2 text-[12px] font-medium text-[#4a3728] disabled:opacity-50"
          >
            {refreshing ? "刷新中..." : "刷新"}
          </button>
          {data.setup_required && (
            <button
              type="button"
              onClick={ensureRelayAccount}
              disabled={ensuring}
              className="rounded-lg bg-[#f5c842] px-4 py-2 text-[12px] font-semibold text-[#4a3728] disabled:opacity-50"
            >
              {ensuring ? "准备中..." : "准备 AI 服务"}
            </button>
          )}
        </div>
      </header>

      {message && (
        <div
          className={`mb-4 rounded-lg border px-4 py-3 text-[12px] ${
            message.type === "success"
              ? "border-[#4a7c59]/30 bg-[#4a7c59]/10 text-[#2f6d42]"
              : "border-[#D64045]/30 bg-[#D64045]/10 text-[#D64045]"
          }`}
        >
          {message.text}
        </div>
      )}

      {!data.account ? (
        <section className="rounded-lg border border-[#e8dcc8] bg-white p-5">
          <h2 className="text-[15px] font-semibold text-[#4a3728]">AI 服务尚未就绪</h2>
          <p className="mt-2 text-[12px] leading-6 text-[#8b7355]">
            当前账号还没有可用的 AI 服务。点击下方按钮后，系统会自动准备好需要的使用环境。
          </p>
          <button
            type="button"
            onClick={ensureRelayAccount}
            disabled={ensuring}
            className="mt-4 rounded-lg bg-[#f5c842] px-4 py-2 text-[12px] font-semibold text-[#4a3728] disabled:opacity-50"
          >
            {ensuring ? "准备中..." : "准备 AI 服务"}
          </button>
        </section>
      ) : (
        <>
          <section className="mb-4 rounded-lg border border-[#e8dcc8] bg-white p-5">
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <div>
                <h2 className="text-[15px] font-semibold text-[#4a3728]">AI 使用占比</h2>
                <p className="mt-1 text-[12px] text-[#8b7355]">
                  看看最近是哪类 AI 能力消耗了搭子的余额。
                </p>
              </div>
              <div className="flex flex-col gap-2 md:items-end">
                <div className="flex flex-wrap gap-1">
                  {SUMMARY_RANGE_OPTIONS.map((option) => (
                    <button
                      key={option.days}
                      type="button"
                      onClick={() => {
                        setSummaryRangeDays(option.days);
                        void loadRelay({ silent: true, rangeDays: option.days });
                      }}
                      className={`rounded-md border px-3 py-1 text-[12px] ${
                        summaryRangeDays === option.days
                          ? "border-[#4a7c59] bg-[#4a7c59]/10 text-[#2f6d42]"
                          : "border-[#e8dcc8] bg-white text-[#8b7355]"
                      }`}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                <div className="flex flex-wrap gap-1">
                  {SUMMARY_GRANULARITY_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => {
                        setSummaryGranularity(option.value);
                        void loadRelay({ silent: true, granularity: option.value });
                      }}
                      className={`rounded-md border px-3 py-1 text-[12px] ${
                        summaryGranularity === option.value
                          ? "border-[#c4a35a] bg-[#c4a35a]/10 text-[#4a3728]"
                          : "border-[#e8dcc8] bg-white text-[#8b7355]"
                      }`}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <UsageStackedBarChart
              buckets={chartBuckets}
              maxQuota={maxChartQuota}
              granularity={summaryGranularity}
            />
          </section>

          <section className="mb-4 rounded-lg border border-[#e8dcc8] bg-white p-5">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="min-w-0">
                <h2 className="text-[15px] font-semibold text-[#4a3728]">搭子余额</h2>
                <p className="mt-2 text-[24px] font-semibold leading-none text-[#4a3728]">
                  {formatCredits(quota?.display.balance_credits)}
                </p>
              </div>
              <div className="grid grid-cols-3 gap-2 lg:min-w-[360px]">
                <button
                  type="button"
                  onClick={() => setDetailPanel("ledger")}
                  className="min-h-9 rounded-lg border border-[#e8dcc8] bg-white px-3 py-2 text-[12px] font-medium text-[#4a3728] hover:border-[#c4a35a]"
                >
                  查看明细
                </button>
                <button
                  type="button"
                  onClick={() => setDetailPanel("usage")}
                  className="min-h-9 rounded-lg border border-[#e8dcc8] bg-white px-3 py-2 text-[12px] font-medium text-[#4a3728] hover:border-[#c4a35a]"
                >
                  消耗记录
                </button>
                <button
                  type="button"
                  onClick={() => setShowRechargeDialog(true)}
                  className="min-h-9 rounded-lg bg-[#f5c842] px-3 py-2 text-[12px] font-semibold text-[#4a3728]"
                >
                  补充余额
                </button>
              </div>
            </div>
            <p className="mt-3 text-[12px] leading-6 text-[#8b7355]">
              余额会随着 AI 回复和任务处理逐步消耗。没有可用余额时，搭子会暂时下线，补充后就能继续陪你。
            </p>
            <div className="mt-5">
              <div className="mb-2 flex items-center justify-between gap-3 text-[12px]">
                <span className="font-medium text-[#4a3728]">目前积分使用情况</span>
                <span className="text-[#8b7355]">{usagePercent.toFixed(1)}%</span>
              </div>
              <div className="h-3 overflow-hidden rounded-full bg-[#f5f0e8]">
                <div className="h-full rounded-full bg-[#4a7c59]" style={{ width: `${Math.min(100, usagePercent)}%` }} />
              </div>
            </div>
            {data.account.last_sync_error && (
              <p className="mt-3 rounded-lg bg-[#D64045]/10 px-3 py-2 text-[12px] text-[#D64045]">
                部分信息暂时没有更新，请稍后刷新。
              </p>
            )}
          </section>

          <section className="rounded-lg border border-[#e8dcc8] bg-white p-5">
            <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <h2 className="text-[15px] font-semibold text-[#4a3728]">AI 回复模式</h2>
                <p className="mt-1 text-[12px] text-[#8b7355]">
                  回复模式决定搭子的聪明程度。越聪明的模式越会认真思考，效果更好，余额消耗也会相应更高。
                </p>
              </div>
              <button
                type="button"
                onClick={saveTier}
                disabled={!dirty || saving}
                className="rounded-lg bg-[#f5c842] px-4 py-2 text-[12px] font-semibold text-[#4a3728] disabled:opacity-45"
              >
                {saving ? "保存中..." : dirty ? "保存模式" : "已保存"}
              </button>
            </div>

            {data.model_tiers.length === 0 ? (
              <div className="rounded-lg border border-[#e8dcc8] bg-[#f5f0e8] px-4 py-3 text-[12px] text-[#8b7355]">
                当前暂时无法选择回复模式，请稍后再试。
              </div>
            ) : (
              <div className="grid gap-3 md:grid-cols-3">
                {data.model_tiers.map((tier) => {
                  const selected = draftTier === tier.tier;
                  return (
                    <button
                      key={tier.tier}
                      type="button"
                      onClick={() => setDraftTier(tier.tier)}
                      className={`min-h-[132px] rounded-lg border p-4 text-left transition ${
                        selected
                          ? "border-[#4a7c59] bg-[#4a7c59]/10 ring-2 ring-[#4a7c59]/15"
                          : "border-[#e8dcc8] bg-[#faf7f1] hover:border-[#c4a35a]"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <h3 className="text-[15px] font-semibold text-[#4a3728]">{tierTitle(tier.tier)}</h3>
                          <p className="mt-2 text-[12px] leading-5 text-[#8b7355]">{TIER_HELP[tier.tier] ?? "系统会根据这个模式自动选择合适能力。"}</p>
                        </div>
                        <span
                          className={`h-4 w-4 shrink-0 rounded-full border ${
                            selected ? "border-[#4a7c59] bg-[#4a7c59]" : "border-[#c4a35a] bg-white"
                          }`}
                          aria-hidden="true"
                        />
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </section>

          {detailPanel && (
            <DetailDialog
              title={detailPanel === "ledger" ? "余额明细" : "最近消耗记录"}
              description={detailPanel === "ledger" ? "最近 30 天的余额变化。" : "最近 7 天搭子的 AI 使用记录。"}
              onClose={() => setDetailPanel(null)}
            >
              {detailPanel === "ledger" ? (
                <>
                  <div className="max-h-[420px] overflow-y-auto rounded-lg border border-[#e8dcc8]">
                    {ledger.length > 0 ? (
                      <div className="divide-y divide-[#e8dcc8]">
                        {ledger.map((item, index) => (
                          <QuotaLedgerRow key={`${String(item.id ?? index)}-${String(item.created_at ?? "")}`} item={item} />
                        ))}
                      </div>
                    ) : (
                      <p className="px-4 py-3 text-[12px] text-[#8b7355]">最近 30 天暂无余额变化。</p>
                    )}
                  </div>
                  {hasMoreLedger && (
                    <button
                      type="button"
                      onClick={() => loadMore("ledger")}
                      disabled={loadingMoreLedger}
                      className="mt-3 w-full rounded-lg border border-[#e8dcc8] bg-white px-4 py-2 text-[12px] font-medium text-[#4a3728] disabled:opacity-50"
                    >
                      {loadingMoreLedger ? "加载中..." : "加载更多"}
                    </button>
                  )}
                </>
              ) : (
                <>
                  <div className="max-h-[420px] overflow-y-auto rounded-lg border border-[#e8dcc8]">
                    {logs.length > 0 ? (
                      <div className="divide-y divide-[#e8dcc8]">
                        {logs.map((log, index) => (
                          <UsageLogRow key={`${String(log.id ?? index)}-${String(log.created_at ?? "")}`} log={log} />
                        ))}
                      </div>
                    ) : (
                      <p className="px-4 py-3 text-[12px] text-[#8b7355]">暂无使用记录。</p>
                    )}
                  </div>
                  {hasMoreUsage && (
                    <button
                      type="button"
                      onClick={() => loadMore("usage")}
                      disabled={loadingMoreUsage}
                      className="mt-3 w-full rounded-lg border border-[#e8dcc8] bg-white px-4 py-2 text-[12px] font-medium text-[#4a3728] disabled:opacity-50"
                    >
                      {loadingMoreUsage ? "加载中..." : "加载更多"}
                    </button>
                  )}
                </>
              )}
            </DetailDialog>
          )}

          {showRechargeDialog && (
            <DetailDialog
              title="补充余额"
              description="联系管理员“核桃”进行积分添加，内测期间不需要金额哦。"
              onClose={() => setShowRechargeDialog(false)}
            >
              <div className="rounded-lg bg-[#f5f0e8] px-4 py-3 text-[12px] leading-6 text-[#4a3728]">
                联系管理员“核桃”进行积分添加，内测期间不需要金额哦。
              </div>
            </DetailDialog>
          )}

          {visibleIssues.length > 0 && (
            <section className="mt-4 rounded-lg border border-[#e8dcc8] bg-white p-5">
              <h2 className="text-[15px] font-semibold text-[#4a3728]">提示</h2>
              <p className="mt-3 rounded-lg bg-[#f5f0e8] px-3 py-2 text-[12px] text-[#8b7355]">
                部分信息暂时不可用，请稍后刷新。
              </p>
            </section>
          )}
        </>
      )}
    </main>
  );
}

function DetailDialog({
  title,
  description,
  onClose,
  children,
}: {
  title: string;
  description: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#2b2118]/35 px-4 py-6">
      <div className="w-full max-w-2xl rounded-lg border border-[#e8dcc8] bg-white p-5 shadow-xl">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-[15px] font-semibold text-[#4a3728]">{title}</h2>
            <p className="mt-1 text-[12px] text-[#8b7355]">{description}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-[#e8dcc8] bg-white px-3 py-1 text-[12px] font-medium text-[#4a3728]"
          >
            关闭
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function mergePagedLogs<T extends UsageLogs | QuotaLedger>(
  current: T | null | undefined,
  next: T | null | undefined,
): T | null {
  if (!next) return current ?? null;
  if (!current) return next;
  const seen = new Set<string>();
  const items: UsageLog[] = [];
  for (const item of [...current.items, ...next.items]) {
    const key = `${String(item.id ?? "")}-${String(item.created_at ?? "")}-${String(item.model_name ?? "")}-${String(item.content ?? "")}`;
    if (seen.has(key)) continue;
    seen.add(key);
    items.push(item);
  }
  return { ...next, items } as T;
}

function mergeUsageSummary(
  current: UsageSummary | null | undefined,
  next: UsageSummary | null | undefined,
): UsageSummary | null {
  if (!next) return current ?? null;
  if (!current?.model_summaries || !next.model_summaries) return next;
  const seen = new Set<string>();
  const items: ModelUsageSummaryItem[] = [];
  for (const item of [...current.model_summaries.items, ...next.model_summaries.items]) {
    const key = item.model_name || "__unknown__";
    if (seen.has(key)) continue;
    seen.add(key);
    items.push(item);
  }
  return {
    ...next,
    model_summaries: {
      ...next.model_summaries,
      items,
    },
  };
}

function buildUsageChartBuckets(
  buckets: UsageBucket[] | undefined,
  granularity: SummaryGranularity,
): UsageChartBucket[] {
  const byUnit = new Map<string, UsageChartBucket>();
  for (const bucket of buckets ?? []) {
    const createdAt = normalizeTimestamp(bucket.created_at);
    const key = granularity === "all" ? "all" : String(createdAt ?? "unknown");
    const current = byUnit.get(key) ?? {
      key,
      label: formatChartBucketLabel(createdAt, granularity),
      created_at: granularity === "all" ? null : createdAt,
      quota: 0,
      count: 0,
      token_used: 0,
      segments: [],
    };
    const quota = Number(bucket.quota) || 0;
    const count = Number(bucket.count) || 0;
    const tokenUsed = Number(bucket.token_used) || 0;
    current.quota += quota;
    current.count += count;
    current.token_used += tokenUsed;
    current.segments.push({
      model_name: bucket.model_name,
      count,
      quota,
      token_used: tokenUsed,
      quota_percent: 0,
      display: bucket.display,
    });
    byUnit.set(key, current);
  }

  const result = [...byUnit.values()].map((bucket) => {
    const segments = bucket.segments
      .map((segment) => ({
        ...segment,
        quota_percent: bucket.quota > 0 ? (segment.quota / bucket.quota) * 100 : 0,
      }))
      .sort((a, b) => b.quota - a.quota);
    return {
      ...bucket,
      segments,
      display: { quota_credits: bucket.quota / 500000 },
    };
  });

  return result.sort((a, b) => {
    if (granularity === "all") return b.quota - a.quota;
    return (a.created_at ?? 0) - (b.created_at ?? 0);
  });
}

function UsageStackedBarChart({
  buckets,
  maxQuota,
  granularity,
}: {
  buckets: UsageChartBucket[];
  maxQuota: number;
  granularity: SummaryGranularity;
}) {
  if (buckets.length === 0) {
    return (
      <p className="mt-4 rounded-lg bg-[#f5f0e8] px-4 py-3 text-[12px] text-[#8b7355]">
        当前范围暂无可视化数据。
      </p>
    );
  }

  return (
    <div className="mt-4 rounded-lg border border-[#e8dcc8] bg-[#faf7f1] p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-[12px] font-semibold text-[#4a3728]">{granularityChartTitle(granularity)}</p>
        <p className="text-[12px] text-[#8b7355]">把鼠标移上去查看各 AI 占比</p>
      </div>
      <div className="space-y-3">
        {buckets.map((bucket) => (
          <div key={bucket.key} className="grid gap-2 md:grid-cols-[84px_1fr] md:items-center">
            <div className="min-w-0">
              <p className="truncate text-[12px] font-medium text-[#4a3728]">{bucket.label}</p>
              <p className="text-[12px] text-[#8b7355]">{bucket.count} 次</p>
            </div>
            <div className="min-w-0">
              <div className="mb-1 flex items-center justify-between gap-2 text-[12px]">
                <span className="text-[#8b7355]">使用积分</span>
                <span className="font-semibold text-[#4a3728]">{formatCredits(bucket.display?.quota_credits)}</span>
              </div>
              <div className="h-8 rounded-md bg-white p-1 shadow-inner">
                <div
                  className="flex h-full overflow-visible rounded-[4px]"
                  style={{ width: `${Math.max(4, percent(bucket.quota, maxQuota))}%` }}
                >
                  {bucket.segments.map((segment, index) => (
                    <UsageStackedBarSegment
                      key={`${bucket.key}-${segment.model_name ?? "unknown"}-${index}`}
                      segment={segment}
                      index={index}
                    />
                  ))}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function UsageStackedBarSegment({
  segment,
  index,
}: {
  segment: UsageChartSegment;
  index: number;
}) {
  const width = Math.max(2, segment.quota_percent);
  const modelName = segment.model_name || "未知 AI";
  const title = `${modelName}\n${formatCredits(segment.display?.quota_credits)}\n占比 ${segment.quota_percent.toFixed(1)}%\n${segment.count} 次使用`;
  return (
    <div
      className="group relative h-full min-w-[2px]"
      style={{
        width: `${width}%`,
        backgroundColor: modelColor(modelName, index),
      }}
      title={title}
    >
      <div className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-2 hidden w-max max-w-[260px] -translate-x-1/2 rounded-md border border-[#e8dcc8] bg-white px-3 py-2 text-[12px] leading-5 text-[#4a3728] shadow-lg group-hover:block">
        <p className="max-w-[220px] truncate font-semibold">{modelName}</p>
        <p>{formatCredits(segment.display?.quota_credits)}</p>
        <p>占比 {segment.quota_percent.toFixed(1)}%</p>
        <p>{segment.count} 次使用</p>
      </div>
    </div>
  );
}

function QuotaLedgerRow({ item }: { item: UsageLog }) {
  const amount = Number(item.quota) || 0;
  const note = quotaLogNote(item);
  const typeText = quotaLogTypeText(item.type);
  return (
    <div className="grid gap-2 px-4 py-3 text-[12px] md:grid-cols-[1fr_auto] md:items-center">
      <div className="min-w-0">
        <p className="truncate font-medium text-[#4a3728]" title={note}>{note}</p>
        <p className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-[#8b7355]">
          <span>{typeText}</span>
          <span className="text-[#c4a35a]">·</span>
          <span>{formatTime(item.created_at)}</span>
        </p>
      </div>
      <span className={`shrink-0 font-semibold ${amount < 0 ? "text-[#D64045]" : "text-[#2f6d42]"}`}>
        {amount > 0 ? "+" : ""}{formatCredits(item.display?.quota_credits)}
      </span>
    </div>
  );
}

function UsageLogRow({ log }: { log: UsageLog }) {
  return (
    <div className="grid gap-2 px-4 py-3 text-[12px] md:grid-cols-[1fr_auto] md:items-center">
      <div className="min-w-0">
        <p className="truncate font-medium text-[#4a3728]">{log.model_name || "未知 AI"}</p>
        <p className="mt-1 truncate text-[12px] text-[#8b7355]">
          {formatTime(log.created_at)}
        </p>
      </div>
      <div className="flex flex-wrap gap-2 text-[12px] text-[#8b7355] md:justify-end">
        <span>{formatCredits(log.display?.quota_credits)}</span>
      </div>
    </div>
  );
}

function tierTitle(tier: string): string {
  return TIER_LABELS[tier] ?? "自定义模式";
}

function maxBucketQuota(buckets: Array<Pick<UsageBucket, "quota">> | undefined): number {
  return Math.max(0, ...(buckets ?? []).map((bucket) => Number(bucket.quota) || 0));
}

function normalizeTimestamp(value: unknown): number | null {
  const raw = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  if (!Number.isFinite(raw)) return null;
  return raw > 10_000_000_000 ? Math.floor(raw / 1000) : Math.floor(raw);
}

function formatChartBucketLabel(value: number | null, granularity: SummaryGranularity): string {
  if (granularity === "all") return "汇总";
  if (value === null) return "未知时间";
  const date = new Date(value * 1000);
  if (granularity === "hour") {
    return date.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  if (granularity === "day") {
    return date.toLocaleDateString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
    });
  }
  return date.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
  });
}

function granularityLabel(value: SummaryGranularity): string {
  const labels: Record<SummaryGranularity, string> = {
    all: "汇总",
    hour: "小时",
    day: "天",
    month: "月",
  };
  return labels[value];
}

function granularityChartTitle(value: SummaryGranularity): string {
  if (value === "all") return "全部汇总";
  return `按${granularityLabel(value)}查看`;
}

function modelColor(modelName: string, index: number): string {
  const palette = [
    "#4a7c59",
    "#c4a35a",
    "#3b82f6",
    "#d64045",
    "#7c3aed",
    "#0891b2",
    "#db7c26",
    "#64748b",
  ];
  let hash = index;
  for (let i = 0; i < modelName.length; i += 1) {
    hash = (hash * 31 + modelName.charCodeAt(i)) >>> 0;
  }
  return palette[hash % palette.length];
}

function percent(value: number | undefined | null, total: number | undefined | null): number {
  const v = Number(value) || 0;
  const t = Number(total) || 0;
  if (t <= 0) return 0;
  return (v / t) * 100;
}

function formatCredits(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const credits = Number(value);
  const formatted = new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: credits === 0 ? 0 : 2,
    maximumFractionDigits: 4,
  }).format(credits);
  return `${formatted} 积分`;
}

function formatTime(value: number | string | undefined): string {
  if (value === undefined || value === null || value === "") return "-";
  const raw = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(raw)) return String(value);
  const ms = raw > 10_000_000_000 ? raw : raw * 1000;
  return new Date(ms).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function quotaLogTypeText(type: number | undefined): string {
  const labels: Record<number, string> = {
    1: "余额补充",
    3: "余额调整",
    4: "系统变更",
    6: "余额退回",
  };
  return typeof type === "number" ? labels[type] ?? "余额变化" : "余额变化";
}

function quotaLogNote(item: UsageLog): string {
  const content = typeof item.content === "string" ? item.content.trim() : "";
  if (content) return compactQuotaLogNote(content);
  const adminInfo = quotaLogAdminInfo(item.other);
  const reason = stringField(adminInfo, "reason");
  if (reason) return compactQuotaLogNote(reason);
  const bizRefType = stringField(adminInfo, "biz_ref_type");
  if (bizRefType === "invite_reward") return "邀请奖励";
  if (bizRefType === "admin_quota_adjustment" || bizRefType === "admin_adjustment") return "后台额度调整";
  return quotaLogTypeText(item.type);
}

function compactQuotaLogNote(value: string): string {
  let text = value.trim();
  text = text.replace(/后发放\s*\d+(?:\.\d+)?\s*积分$/, "").replace(/[：:，,]\s*$/, "");
  text = text.replace(/\s*[＄$]\s*\d+(?:\.\d+)?\s*额度?$/, "").replace(/[：:，,]\s*$/, "");
  if (text.includes("：")) {
    const [source, detail] = text.split("：", 2);
    if (/^管理员(增加|减少|覆盖)用户额度/.test(detail.trim())) return source.trim();
  }
  const action = text.match(/^管理员(?:增加|减少|覆盖)用户额度/);
  return action?.[0] ?? text;
}

function quotaLogAdminInfo(value: unknown): Record<string, unknown> | null {
  let parsed: unknown = value;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return null;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      return null;
    }
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const adminInfo = (parsed as Record<string, unknown>).admin_info;
  if (!adminInfo || typeof adminInfo !== "object" || Array.isArray(adminInfo)) return null;
  return adminInfo as Record<string, unknown>;
}

function stringField(record: Record<string, unknown> | null, key: string): string {
  const value = record?.[key];
  return typeof value === "string" ? value.trim() : "";
}
