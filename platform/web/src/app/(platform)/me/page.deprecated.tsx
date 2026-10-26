"use client";

/**
 * 我的数据页（双入口 + 老用户补绑 Banner）。
 *
 * 阶段四 A4e 改造：
 *   1. 启动时调用 supabase.auth.getSession()
 *      - 有 Session → 自动用 access_token 作为 Bearer 加载（JWT 路径）
 *      - 无 Session → fallback 到老的 Platform Token 输入 UI（保留老用户体验）
 *   2. JWT 用户**不显示** Banner；platform_token 用户顶部显示
 *      "完善账号：补绑邮箱可使用密码登录" Banner，引导跳 /link-email
 *   3. onAuthStateChange 监听：登录/登出实时刷新
 *
 * 来源判定（authMode）：
 *   - 'jwt'：通过 supabase.auth.getSession() 拿到 session
 *   - 'platform_token'：用户手输 platform_token 或从 sessionStorage 读到
 *   - 同一会话内允许只存其一；JWT 优先（若两者都有，按 JWT 处理）
 *
 * 兼容性：
 *   保留所有原有 UI（输入框、卡片、运行日志），仅新增"自动登录"和 Banner 两块。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { FN } from "@/lib/supabase";
import { createClient as createSupabaseBrowser } from "@/lib/supabase-browser";

type WindowAgg = {
  event_count: number;
  active_days_with_session: number;
  estimated_online_minutes: number;
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
  online?: boolean;
  agent_instance_id?: string;
  state?: string;
  state_changed_at?: number;
  connected_at?: number;
  target_player_id?: number | null;
  command_inflight_count?: number;
  last_game_event_at?: number | null;
  error?: string;
};

type LogEntry = {
  id: number;
  time: string;
  type: "think" | "action" | "chat" | "system";
  text: string;
  from?: string;
  icon?: string;
};

type AuthMode = "jwt" | "platform_token";

const MAX_LOGS = 80;

function nowStr() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

function pct(n: number, max: number): number {
  if (max <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((n / max) * 100)));
}

function classify(raw: Record<string, unknown>): LogEntry {
  const t = String(raw.type ?? "event");
  const time = nowStr();
  const id = Date.now() + Math.random();
  if (t === "command" || t.startsWith("command.")) {
    const name = String((raw as { name?: string }).name ?? t);
    return { id, time, type: "action", text: `下发指令：${name}` };
  }
  if (t === "agent.status") {
    const s = (raw as { data?: { status?: string } }).data?.status ?? "";
    return { id, time, type: "think", text: `Agent 状态：${s || JSON.stringify(raw).slice(0, 80)}` };
  }
  if (t === "state_snapshot") {
    return { id, time, type: "think", text: "收到游戏状态快照" };
  }
  if (t === "game_event") {
    const ev = (raw as { event?: string; data?: { event?: string } }).event
      ?? (raw as { data?: { event?: string } }).data?.event
      ?? "event";
    return { id, time, type: "action", text: `游戏事件：${ev}` };
  }
  if (t === "hello_ok") {
    return { id, time, type: "system", text: "握手完成，会话建立" };
  }
  if (t === "session.end") {
    return { id, time, type: "system", text: "会话结束" };
  }
  const summary = JSON.stringify(raw).slice(0, 100);
  return { id, time, type: "system", text: `${t} · ${summary}` };
}

export default function MePage() {
  const [token, setToken] = useState("");
  const [authMode, setAuthMode] = useState<AuthMode | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(true);
  const logRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs.length]);

  const closeStream = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  useEffect(() => () => closeStream(), [closeStream]);

  const refreshSession = useCallback(async (t: string) => {
    try {
      const res = await fetch(FN.meSessionCurrent, {
        headers: { Authorization: `Bearer ${t}` },
      });
      const data = (await res.json().catch(() => ({}))) as SessionInfo;
      setSession({ ...data, online: !!data.agent_instance_id && !!data.online });
    } catch {
      setSession({ online: false, error: "网络错误" });
    }
  }, []);

  const startStream = useCallback(
    (t: string) => {
      closeStream();
      const es = new EventSource(`${FN.meSessionStream}?kind=session&token=${encodeURIComponent(t)}`);
      es.onmessage = (ev) => {
        try {
          const payload = JSON.parse(ev.data) as Record<string, unknown>;
          setLogs((prev) => [...prev, classify(payload)].slice(-MAX_LOGS));
        } catch {}
      };
      es.addEventListener("closed", () => {
        setLogs((prev) => [...prev, { id: Date.now(), time: nowStr(), type: "system" as const, text: "会话通道已关闭" }].slice(-MAX_LOGS));
        es.close();
      });
      es.onerror = () => {};
      esRef.current = es;
    },
    [closeStream],
  );

  /**
   * 用给定 Bearer token 加载 summary + session + 启动事件流。
   * 由 JWT bootstrap 与手动「查询」共用。
   */
  const loadWithToken = useCallback(
    async (t: string, mode: AuthMode) => {
      setLoading(true);
      setErr("");
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
        // 仅 platform_token 模式下回写 sessionStorage（JWT 走 cookie，不需要持久化 token）
        if (mode === "platform_token") {
          try {
            sessionStorage.setItem("platform_token_for_me", t);
          } catch {
            // ignore
          }
        }
        startStream(t);
      } catch (e: unknown) {
        setErr(e instanceof Error ? e.message : "请求失败");
        // 加载失败不要假装登录成功
        setSummary(null);
        setAuthMode(null);
      } finally {
        setLoading(false);
      }
    },
    [refreshSession, startStream],
  );

  /**
   * 启动序列：
   *   1. 尝试 supabase.auth.getSession() → 有则用 JWT 自动加载
   *   2. 无 JWT 时回到老路：从 sessionStorage 恢复 platform_token 到输入框，
   *      但**不**自动加载（让用户主动点"查询"，避免无效 token 时干扰）
   *   3. 订阅 onAuthStateChange，登录/登出实时刷新
   */
  useEffect(() => {
    let active = true;
    let supabase: ReturnType<typeof createSupabaseBrowser> | null = null;

    try {
      supabase = createSupabaseBrowser();
    } catch {
      // env 缺失，直接走 platform_token 兜底
      try {
        const s = sessionStorage.getItem("platform_token_for_me");
        if (s) setToken(s);
      } catch {}
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
          // 回退到 platform_token：仅恢复输入框值，不自动 fetch
          try {
            const s = sessionStorage.getItem("platform_token_for_me");
            if (s) setToken(s);
          } catch {}
        }
      } catch {
        // ignore
      } finally {
        if (active) setBootstrapping(false);
      }
    };

    void initialBoot();

    // onAuthStateChange：登录后立刻加载；登出统一清理。
    // 注意：本回调闭包内不能依赖 authMode（依赖数组为 [] 时永远捕获初始值 null）。
    // 登出时无脑清理 UI（不区分 jwt/platform_token），共享设备防隐私泄漏。
    const { data: sub } = supabase.auth.onAuthStateChange(async (_event, sessionState) => {
      if (!active) return;
      const accessToken = sessionState?.access_token;
      if (accessToken) {
        await loadWithToken(accessToken, "jwt");
      } else {
        // 登出 / 无 session：清空 UI 残留数据；platform_token 输入框值不动（用户主动输入的）
        closeStream();
        setSummary(null);
        setAuthMode(null);
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

  // 定期刷新 session（仅在已加载 summary 时）
  useEffect(() => {
    if (!summary) return;
    const tokenForSession = authMode === "jwt" ? null : token.trim();
    // JWT 模式下 access_token 在 supabase client 中，定期刷新需要重新 getSession
    const timer = setInterval(async () => {
      if (authMode === "jwt") {
        try {
          const supabase = createSupabaseBrowser();
          const { data } = await supabase.auth.getSession();
          const at = data.session?.access_token;
          if (at) await refreshSession(at);
        } catch {
          // ignore
        }
      } else if (tokenForSession) {
        await refreshSession(tokenForSession);
      }
    }, 10000);
    return () => clearInterval(timer);
  }, [summary, authMode, token, refreshSession]);

  const last7 = summary?.windows.days_7;
  const last30 = summary?.windows.days_30;

  const companionStats = useMemo(() => {
    const w = last30 ?? { llm_calls: 0, estimated_online_minutes: 0, event_count: 0, tokens_in: 0, tokens_out: 0, active_days_with_session: 0 };
    return [
      { label: "在线时长", value: pct(w.estimated_online_minutes, 60 * 24), color: "bg-stardew-green", raw: `${w.estimated_online_minutes} 分钟` },
      { label: "LLM 调用", value: pct(w.llm_calls, 500), color: "bg-stardew-gold", raw: `${w.llm_calls} 次` },
      { label: "事件数", value: pct(w.event_count, 2000), color: "bg-stardew-blue", raw: `${w.event_count}` },
      { label: "活跃天", value: pct(w.active_days_with_session, 30), color: "bg-stardew-green-light", raw: `${w.active_days_with_session}/30` },
    ];
  }, [last30]);

  const memoryCards = useMemo(() => {
    const w7 = last7;
    const w30 = last30;
    if (!w7 || !w30) return [];
    return [
      { icon: "/icons/Clock_Icon.png", title: "在线时长", desc: `7日 ${w7.estimated_online_minutes}m · 30日 ${w30.estimated_online_minutes}m` },
      { icon: "/icons/Book_of_Mysteries.png", title: "LLM 调用", desc: `7日 ${w7.llm_calls} · 30日 ${w30.llm_calls}` },
      { icon: "/icons/Speech_bubble.png", title: "事件总数", desc: `7日 ${w7.event_count} · 30日 ${w30.event_count}` },
      { icon: "/icons/Achievements_Icon.png", title: "活跃天数", desc: `7日 ${w7.active_days_with_session} · 30日 ${w30.active_days_with_session}` },
      { icon: "/icons/Copper_Node.png", title: "Tokens 输入", desc: `7日 ${w7.tokens_in} · 30日 ${w30.tokens_in}` },
      { icon: "/icons/Golden_Walnut.png", title: "Tokens 输出", desc: `7日 ${w7.tokens_out} · 30日 ${w30.tokens_out}` },
    ];
  }, [last7, last30]);

  const profileCards = useMemo(() => {
    if (!summary) return [];
    return [
      { icon: "/icons/The_Player_Icon.png", label: "昵称", value: summary.nickname ?? "-" },
      { icon: "/icons/Time_Icon.png", label: "注册时间", value: new Date(summary.created_at).toLocaleDateString("zh-CN") },
      { icon: "/icons/Secret_Note_Icon.png", label: "用户 ID", value: summary.user_id.slice(0, 8) + "…" },
      { icon: "/icons/Standard_Farm_Map_Icon.png", label: "目标玩家", value: session?.target_player_id != null ? String(session.target_player_id) : "本机" },
      { icon: "/icons/Multiplayer_Icon.png", label: "实例状态", value: session?.state ?? (session?.online ? "online" : "离线") },
      { icon: "/icons/Controls_Icon.png", label: "在途命令", value: String(session?.command_inflight_count ?? 0) },
    ];
  }, [summary, session]);

  const online = !!session?.online;
  const agentShort = session?.agent_instance_id ? session.agent_instance_id.slice(0, 10) + "…" : "—";

  // 老用户补绑邮箱 Banner（仅 platform_token 模式显示）
  const showLinkEmailBanner = summary !== null && authMode === "platform_token";

  // 未加载完成且仍在引导阶段：显示加载占位（避免闪 token 输入框）
  if (!summary && bootstrapping) {
    return (
      <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] px-4 py-10">
        <div className="max-w-lg mx-auto parchment rounded-2xl p-6 text-center">
          <p className="text-xs text-stardew-brown/60">正在检查登录状态…</p>
        </div>
      </div>
    );
  }

  if (!summary) {
    return (
      <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] px-4 py-10">
        <div className="max-w-lg mx-auto parchment rounded-2xl p-6">
          <h1 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-1">
            我的数据
          </h1>
          <p className="text-[10px] text-stardew-brown/60 mb-4">
            已注册新账号？请用邮箱密码{" "}
            <Link href="/login" className="text-stardew-blue hover:underline">
              登录
            </Link>
            。
            <br />
            老用户可继续用 Platform Token 查询本人数据。
          </p>
          <div className="flex gap-2 mb-4">
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Platform Token"
              className="flex-1 px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs font-mono focus:border-stardew-wood focus:outline-none"
            />
            <button
              type="button"
              onClick={loadFromInput}
              disabled={loading}
              className="stardew-btn px-4 py-2 rounded-lg text-xs font-bold shrink-0 disabled:opacity-50"
            >
              {loading ? "…" : "查询"}
            </button>
          </div>
          {err && <p className="text-red-600 text-xs mb-3">{err}</p>}
          <p className="mt-4 text-center">
            <Link href="/register" className="text-[10px] text-stardew-blue hover:underline">
              没有账号？去注册
            </Link>
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      {showLinkEmailBanner && (
        <div className="bg-stardew-gold/20 border-b-2 border-stardew-gold/60">
          <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between gap-3 flex-wrap">
            <p className="text-xs text-stardew-brown">
              <span className="font-bold mr-2">完善账号</span>
              补绑邮箱后即可使用密码登录，无需再粘贴 Platform Token。
            </p>
            <Link
              href="/link-email"
              className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold whitespace-nowrap"
            >
              去补绑 →
            </Link>
          </div>
        </div>
      )}

      <section className="bg-stardew-cream py-14">
        <div className="max-w-6xl mx-auto px-4">
          <div className="parchment rounded-2xl p-8">
            <div className="flex items-center gap-4 mb-8">
              <div className="w-16 h-16 rounded-full bg-stardew-gold/20 border-2 border-stardew-gold flex items-center justify-center flex-shrink-0 animate-float">
                <Image src="/icons/Abigail_Icon.png" alt="Companion" width={40} height={40} className="pixel-art" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-3 mb-2 flex-wrap">
                  <h2 className="font-bold text-stardew-brown text-lg truncate max-w-[180px]">
                    {summary.nickname ?? "旅行者"}
                  </h2>
                  <span className="bg-stardew-gold/20 text-stardew-wood-dark text-xs font-bold px-2 py-0.5 rounded">
                    30d:{last30?.active_days_with_session ?? 0}/30
                  </span>
                  <span
                    className={`text-xs font-bold px-2 py-0.5 rounded-full ${
                      online
                        ? "bg-stardew-green/20 text-stardew-green"
                        : "bg-stardew-brown/10 text-stardew-brown/60"
                    }`}
                  >
                    {online ? "在线" : "离线"}
                  </span>
                  <span className="text-[10px] font-mono text-stardew-brown/50">
                    {agentShort}
                  </span>
                </div>
                <p className="text-[10px] font-mono text-stardew-brown/50 break-all">
                  user_id: {summary.user_id}
                </p>
              </div>
              <button
                type="button"
                onClick={loadFromInput}
                disabled={authMode === "jwt"}
                title={authMode === "jwt" ? "JWT 已登录，自动刷新" : "刷新"}
                className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold shrink-0 disabled:opacity-50"
              >
                刷新
              </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
              {companionStats.map((stat) => (
                <div key={stat.label}>
                  <div className="flex justify-between text-sm mb-2">
                    <span className="text-stardew-brown/70">{stat.label}</span>
                    <span className="font-bold text-stardew-brown">{stat.raw}</span>
                  </div>
                  <div className="w-full h-3 bg-stardew-brown/10 rounded-full overflow-hidden">
                    <div
                      className={`h-full ${stat.color} rounded-full transition-all duration-1000`}
                      style={{ width: `${stat.value}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="bg-stardew-green/5 py-14">
        <div className="max-w-6xl mx-auto px-4">
          <h2 className="font-[family-name:var(--font-pixel)] text-xl text-center text-stardew-brown mb-4">运行日志</h2>
          <p className="text-center text-stardew-brown/70 mb-8 text-xs">
            {online ? "订阅实时会话事件流" : "会话离线 · 连接游戏 Mod 后自动刷新"}
          </p>
          <div className="max-w-3xl mx-auto">
            <div
              ref={logRef}
              className="parchment rounded-2xl p-4 md:p-6 h-[420px] overflow-y-auto space-y-2"
            >
              {logs.length === 0 ? (
                <p className="text-center text-stardew-brown/50 text-xs py-10">
                  {online ? "等待事件…" : "离线中，暂无事件"}
                </p>
              ) : (
                logs.map((log) => (
                  <div
                    key={log.id}
                    className={`rounded-lg px-3 py-2 text-sm leading-relaxed ${
                      log.type === "think"
                        ? "bg-stardew-purple/10 text-stardew-brown/70"
                        : log.type === "action"
                        ? "bg-stardew-green/10 text-stardew-brown/80"
                        : log.type === "chat"
                        ? "bg-stardew-blue/10 text-stardew-brown/80"
                        : "bg-stardew-brown/5 text-stardew-brown/60"
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      <span className="text-stardew-brown/40 flex-shrink-0 font-mono text-xs">[{log.time}]</span>
                      {log.type === "think" && <span className="flex-shrink-0">💭</span>}
                      {log.type === "action" && <span className="flex-shrink-0">🎯</span>}
                      {log.type === "chat" && log.icon && (
                        <Image src={log.icon} alt="" width={16} height={16} className="pixel-art flex-shrink-0 mt-0.5" />
                      )}
                      {log.type === "system" && <span className="flex-shrink-0">⚙️</span>}
                      <div className="flex-1 min-w-0 break-all">
                        {log.type === "think" && <span className="font-bold text-stardew-purple text-xs mr-1.5">思考:</span>}
                        {log.type === "action" && <span className="font-bold text-stardew-green text-xs mr-1.5">行为:</span>}
                        {log.type === "system" && <span className="font-bold text-stardew-brown/70 text-xs mr-1.5">系统:</span>}
                        <span>{log.text}</span>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </section>

      <section className="bg-stardew-cream py-14">
        <div className="max-w-6xl mx-auto px-4">
          <h2 className="font-[family-name:var(--font-pixel)] text-xl text-center text-stardew-brown mb-4">数据概览</h2>
          <p className="text-center text-stardew-brown/70 mb-10 text-xs">最近 7 日 · 最近 30 日</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {memoryCards.map((mem) => (
              <div key={mem.title} className="parchment rounded-xl p-4 flex items-start gap-3">
                <Image src={mem.icon} alt="" width={28} height={28} className="pixel-art flex-shrink-0 mt-1" />
                <div>
                  <p className="font-bold text-stardew-brown text-sm mb-1">{mem.title}</p>
                  <p className="text-stardew-brown/70 text-xs">{mem.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="bg-stardew-green/5 py-14">
        <div className="max-w-6xl mx-auto px-4">
          <h2 className="font-[family-name:var(--font-pixel)] text-xl text-center text-stardew-brown mb-4">账号信息</h2>
          <p className="text-center text-stardew-brown/70 mb-10 text-xs">本次会话与平台绑定概况</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 max-w-4xl mx-auto">
            {profileCards.map((p) => (
              <div key={p.label} className="parchment rounded-xl p-4 flex items-center gap-3">
                <Image src={p.icon} alt="" width={22} height={22} className="pixel-art flex-shrink-0" />
                <span className="text-stardew-brown/70 text-sm flex-1">{p.label}</span>
                <span className="bg-stardew-gold/20 text-stardew-wood-dark text-xs font-bold px-2 py-0.5 rounded max-w-[50%] truncate" title={p.value}>
                  {p.value}
                </span>
              </div>
            ))}
          </div>
          <p className="mt-8 text-center">
            <Link href="/dashboard" className="text-[10px] text-stardew-blue hover:underline">
              打开完整实时看板 →
            </Link>
          </p>
        </div>
      </section>
    </>
  );
}
