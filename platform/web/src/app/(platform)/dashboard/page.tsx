"use client";

/**
 * 实时看板页（双入口）。
 *
 * 阶段四 A4e 改造：
 *   - 启动时调用 supabase.auth.getSession()
 *     · 有 Session → 自动用 access_token 加载（JWT 路径）
 *     · 无 Session → 保留老的 Platform Token 输入 UI（兼容老用户）
 *   - 不显示补绑邮箱 Banner（dashboard 是数据页，账号管理在 /me）
 *   - onAuthStateChange 监听：登录/登出实时刷新
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { FN } from "@/lib/supabase";
import { createClient as createSupabaseBrowser } from "@/lib/supabase-browser";

type WindowAgg = {
  event_count: number;
  active_days_with_session: number;
  estimated_online_minutes: number;
  llm_metrics_migrated: boolean;
  llm_calls: number;
  tokens_in: number;
  tokens_out: number;
};

type Summary = {
  user_id: string;
  nickname: string | null;
  created_at: string;
  windows: { days_7: WindowAgg; days_30: WindowAgg };
};

type SessionInfo = {
  online: boolean;
  agent_instance_id?: string;
  state?: string;
  state_changed_at?: number;
  connected_at?: number;
  target_player_id?: number | null;
  command_inflight_count?: number;
  last_game_event_at?: number | null;
  error?: string;
};

type StreamItem = {
  ts: number;
  text: string;
};

type AuthMode = "jwt" | "platform_token";

const MAX_STREAM = 200;

function fmtTime(sec?: number | null): string {
  if (!sec) return "—";
  return new Date(sec * 1000).toLocaleTimeString();
}

export default function DashboardPage() {
  const [token, setToken] = useState("");
  const [authMode, setAuthMode] = useState<AuthMode | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [events, setEvents] = useState<StreamItem[]>([]);
  const [logs, setLogs] = useState<StreamItem[]>([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(true);
  const eventsRef = useRef<HTMLDivElement>(null);
  const logsRef = useRef<HTMLDivElement>(null);
  const eventsSrcRef = useRef<EventSource | null>(null);
  const logsSrcRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (eventsRef.current) eventsRef.current.scrollTop = eventsRef.current.scrollHeight;
  }, [events.length]);
  useEffect(() => {
    if (logsRef.current) logsRef.current.scrollTop = logsRef.current.scrollHeight;
  }, [logs.length]);

  const closeStreams = useCallback(() => {
    eventsSrcRef.current?.close();
    eventsSrcRef.current = null;
    logsSrcRef.current?.close();
    logsSrcRef.current = null;
  }, []);

  useEffect(() => () => closeStreams(), [closeStreams]);

  const refreshSession = useCallback(async (t: string) => {
    const res = await fetch(FN.meSessionCurrent, {
      headers: { Authorization: `Bearer ${t}` },
    });
    const data = (await res.json().catch(() => ({}))) as SessionInfo;
    setSession({ ...data, online: !!data.agent_instance_id });
    return data;
  }, []);

  const startStreams = useCallback(
    (t: string) => {
      closeStreams();

      const esEvents = new EventSource(`${FN.meSessionStream}?kind=session&token=${encodeURIComponent(t)}`);
      esEvents.onmessage = (ev) => {
        try {
          const payload = JSON.parse(ev.data) as Record<string, unknown>;
          setEvents((prev) => {
            const next = [
              ...prev,
              { ts: Date.now() / 1000, text: JSON.stringify(payload) },
            ];
            return next.slice(-MAX_STREAM);
          });
        } catch {
          /* ignore */
        }
      };
      esEvents.addEventListener("closed", () => {
        setEvents((prev) => [...prev, { ts: Date.now() / 1000, text: "[session closed]" }].slice(-MAX_STREAM));
        esEvents.close();
      });
      esEvents.onerror = () => {
        /* EventSource 会自动重连，不手动处理 */
      };
      eventsSrcRef.current = esEvents;

      const esLogs = new EventSource(`${FN.meSessionStream}?kind=logs&token=${encodeURIComponent(t)}`);
      esLogs.onmessage = (ev) => {
        try {
          const payload = JSON.parse(ev.data) as { line: string; ts: number };
          setLogs((prev) => [...prev, { ts: payload.ts, text: payload.line }].slice(-MAX_STREAM));
        } catch {
          /* ignore */
        }
      };
      esLogs.onerror = () => {
        /* ignore */
      };
      logsSrcRef.current = esLogs;
    },
    [closeStreams],
  );

  const loadWithToken = useCallback(
    async (t: string, mode: AuthMode) => {
      setLoading(true);
      setErr("");
      setSummary(null);
      setEvents([]);
      setLogs([]);
      try {
        const [sumRes] = await Promise.all([
          fetch(FN.meSummary, { headers: { Authorization: `Bearer ${t}` } }),
          refreshSession(t),
        ]);
        const sumData = await sumRes.json();
        if (!sumRes.ok) throw new Error(sumData.error ?? "加载失败");
        setSummary(sumData as Summary);
        setAuthMode(mode);
        if (mode === "platform_token") {
          try {
            sessionStorage.setItem("platform_token_for_me", t);
          } catch {
            /* ignore */
          }
        }
        startStreams(t);
      } catch (e: unknown) {
        setErr(e instanceof Error ? e.message : "请求失败");
        setSummary(null);
        setAuthMode(null);
      } finally {
        setLoading(false);
      }
    },
    [refreshSession, startStreams],
  );

  /**
   * 启动序列：JWT 优先；无 Session 则回退到 platform_token 输入 UI。
   */
  useEffect(() => {
    let active = true;
    let supabase: ReturnType<typeof createSupabaseBrowser> | null = null;

    try {
      supabase = createSupabaseBrowser();
    } catch {
      try {
        const s = sessionStorage.getItem("platform_token_for_me");
        if (s) setToken(s);
      } catch {
        /* ignore */
      }
      setBootstrapping(false);
      return () => {
        active = false;
      };
    }

    const initialBoot = async () => {
      try {
        const { data } = await supabase!.auth.getSession();
        if (!active) return;
        const accessToken = data.session?.access_token;
        if (accessToken) {
          await loadWithToken(accessToken, "jwt");
        } else {
          try {
            const s = sessionStorage.getItem("platform_token_for_me");
            if (s) setToken(s);
          } catch {
            /* ignore */
          }
        }
      } catch {
        /* ignore */
      } finally {
        if (active) setBootstrapping(false);
      }
    };

    void initialBoot();

    // onAuthStateChange 闭包内不能依赖 authMode（依赖数组 [] 永远捕获初始 null）。
    // 登出时无脑清理 UI，共享设备防隐私泄漏。
    const { data: sub } = supabase.auth.onAuthStateChange(async (_event, sessionState) => {
      if (!active) return;
      const accessToken = sessionState?.access_token;
      if (accessToken) {
        await loadWithToken(accessToken, "jwt");
      } else {
        closeStreams();
        setSummary(null);
        setAuthMode(null);
        setEvents([]);
        setLogs([]);
      }
    });

    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadFromInput = useCallback(async () => {
    const t = token.trim();
    if (!t) {
      setErr("请填写 Platform Token");
      return;
    }
    await loadWithToken(t, "platform_token");
  }, [token, loadWithToken]);

  // 定期刷新当前会话
  useEffect(() => {
    if (!summary) return;
    const timer = setInterval(async () => {
      if (authMode === "jwt") {
        try {
          const supabase = createSupabaseBrowser();
          const { data } = await supabase.auth.getSession();
          const at = data.session?.access_token;
          if (at) await refreshSession(at).catch(() => undefined);
        } catch {
          /* ignore */
        }
      } else {
        const t = token.trim();
        if (t) await refreshSession(t).catch(() => undefined);
      }
    }, 10000);
    return () => clearInterval(timer);
  }, [summary, authMode, token, refreshSession]);

  const last7 = summary?.windows.days_7;
  const last30 = summary?.windows.days_30;

  return (
    <section className="min-h-[calc(100vh-4rem)] py-10 px-4">
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="bg-white/80 rounded-2xl p-6 shadow">
          <h1 className="text-2xl font-bold mb-4">实时看板</h1>
          <p className="text-sm text-stardew-brown/70 mb-4">
            已登录用户自动加载；老用户可继续使用 Platform Token 查询。
          </p>

          {bootstrapping ? (
            <p className="text-xs text-stardew-brown/60">正在检查登录状态…</p>
          ) : authMode === "jwt" ? (
            <p className="text-xs text-stardew-brown/60">已用账号 Session 登录，自动刷新中。</p>
          ) : (
            <div className="flex gap-2 flex-wrap">
              <input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="粘贴你的 Platform Token"
                className="flex-1 min-w-[240px] rounded-lg border px-3 py-2 text-sm"
              />
              <button
                onClick={loadFromInput}
                disabled={loading}
                className="rounded-lg px-4 py-2 bg-stardew-brown text-stardew-cream text-sm font-bold hover:bg-stardew-wood-dark disabled:opacity-50"
              >
                {loading ? "加载中..." : "加载"}
              </button>
            </div>
          )}
          {err && <p className="text-red-600 text-sm mt-3">{err}</p>}
        </div>

        <div className="bg-white/80 rounded-2xl p-6 shadow">
          <h2 className="text-lg font-bold mb-3">当前会话</h2>
          {!session ? (
            <p className="text-sm text-stardew-brown/60">尚未加载</p>
          ) : !session.online ? (
            <p className="text-sm text-stardew-brown/60">
              离线 {session.error ? `（${session.error}）` : "— 请确认游戏 Mod 已连接"}
            </p>
          ) : (
            <dl className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
              <Field label="状态" value={session.state ?? "—"} />
              <Field label="Agent 实例" value={session.agent_instance_id ?? "—"} />
              <Field label="目标玩家 ID" value={String(session.target_player_id ?? "本机")} />
              <Field label="在途命令" value={String(session.command_inflight_count ?? 0)} />
              <Field label="连接时间" value={fmtTime(session.connected_at)} />
              <Field label="状态更新" value={fmtTime(session.state_changed_at)} />
              <Field label="最近游戏事件" value={fmtTime(session.last_game_event_at)} />
            </dl>
          )}
        </div>

        <div className="grid md:grid-cols-2 gap-6">
          <AggCard title="最近 7 日" agg={last7} />
          <AggCard title="最近 30 日" agg={last30} />
        </div>

        <div className="bg-white/80 rounded-2xl p-6 shadow">
          <h2 className="text-lg font-bold mb-3">实时会话消息流</h2>
          <div
            ref={eventsRef}
            className="h-[240px] overflow-y-auto bg-black/90 text-green-200 rounded-lg p-3 font-mono text-xs space-y-0.5"
          >
            {events.length === 0 ? (
              <p className="text-green-500/60">等待 game_event / state_snapshot / command ...</p>
            ) : (
              events.map((e, i) => (
                <div key={i} className="break-all">
                  <span className="text-green-500/50 mr-2">{new Date(e.ts * 1000).toLocaleTimeString()}</span>
                  {e.text}
                </div>
              ))
            )}
          </div>
        </div>

        <div className="bg-white/80 rounded-2xl p-6 shadow">
          <h2 className="text-lg font-bold mb-3">Agent 日志</h2>
          <div
            ref={logsRef}
            className="h-[240px] overflow-y-auto bg-black/90 text-amber-200 rounded-lg p-3 font-mono text-xs space-y-0.5"
          >
            {logs.length === 0 ? (
              <p className="text-amber-500/60">无日志（Gateway 未配置 STARCOPLAY_AGENT_LOG 时不可用）</p>
            ) : (
              logs.map((e, i) => (
                <div key={i} className="break-all">
                  <span className="text-amber-500/50 mr-2">{new Date(e.ts * 1000).toLocaleTimeString()}</span>
                  {e.text}
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-stardew-brown/60">{label}</dt>
      <dd className="font-semibold break-all">{value}</dd>
    </div>
  );
}

function AggCard({ title, agg }: { title: string; agg: WindowAgg | undefined }) {
  return (
    <div className="bg-white/80 rounded-2xl p-6 shadow">
      <h2 className="text-lg font-bold mb-3">{title}</h2>
      {!agg ? (
        <p className="text-sm text-stardew-brown/60">—</p>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <Field label="事件数" value={String(agg.event_count)} />
            <Field label="活跃天数" value={String(agg.active_days_with_session)} />
            <Field label="估算在线分钟" value={String(agg.estimated_online_minutes)} />
            <Field label="LLM 调用" value={agg.llm_metrics_migrated ? "migrated" : String(agg.llm_calls)} />
            <Field label="输入 tokens" value={agg.llm_metrics_migrated ? "migrated" : String(agg.tokens_in)} />
            <Field label="输出 tokens" value={agg.llm_metrics_migrated ? "migrated" : String(agg.tokens_out)} />
          </dl>
          {agg.llm_metrics_migrated && (
            <p className="mt-3 text-xs text-stardew-brown/60">
              LLM metrics migrated off public.events.
            </p>
          )}
        </>
      )}
    </div>
  );
}
