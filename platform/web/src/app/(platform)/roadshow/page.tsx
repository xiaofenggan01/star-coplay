"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const MAX_TEXT_CHARS = 300;
const VISIBLE_MESSAGE_LIMIT = 30;
const VISIBLE_WINDOW_MS = 30 * 60 * 1000;

type MessageRole = "viewer" | "assistant" | "system";

interface MessageItem {
  id: string;
  role: MessageRole;
  kind: string;
  text: string;
  ts: string;
  meta?: string;
}

interface AgentEvent {
  id: number;
  type: string;
  ts: string;
  data?: Record<string, unknown> | null;
}

interface RoadshowHistory {
  ok?: boolean;
  route?: { route_key: string; title?: string | null };
  agent_events?: AgentEvent[];
  error?: string;
}

function newInputId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `inp_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function isVisibleMessage(msg: MessageItem): boolean {
  return msg.role === "viewer" || msg.role === "assistant" || msg.role === "system";
}

function mergeMessages(prev: MessageItem[], incoming: MessageItem[]) {
  const byId = new Map<string, MessageItem>();
  for (const msg of prev) byId.set(msg.id, msg);
  for (const msg of incoming) byId.set(msg.id, msg);
  const windowStart = Date.now() - VISIBLE_WINDOW_MS;
  return Array.from(byId.values())
    .filter(isVisibleMessage)
    .filter((msg) => messageTime(msg) >= windowStart)
    .sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime())
    .slice(-VISIBLE_MESSAGE_LIMIT);
}

function agentEventToMessage(event: AgentEvent): MessageItem | null {
  const data = event.data ?? {};
  if (event.type === "external_text.input_merged" || event.type === "roadshow.input_merged") {
    const text = typeof data.text === "string" ? data.text.trim() : "";
    if (!text) return null;
    const count = typeof data.input_count === "number" ? data.input_count : 1;
    return {
      id: `input_${event.id}`,
      role: "viewer",
      kind: "input_merged",
      text,
      ts: event.ts,
      meta: count > 1 ? `合并 ${count} 条` : "现场提问",
    };
  }
  if (event.type === "external_text.agent_visible_output" || event.type === "roadshow.agent_say") {
    const text = typeof data.text === "string" ? data.text.trim() : "";
    if (!text) return null;
    return {
      id: `agent_${event.id}`,
      role: "assistant",
      kind: "agent_visible_output",
      text,
      ts: event.ts,
    };
  }
  return null;
}

function historyToMessages(history: RoadshowHistory): MessageItem[] {
  const items: MessageItem[] = [];
  for (const event of history.agent_events ?? []) {
    const msg = agentEventToMessage(event);
    if (msg) items.push(msg);
  }
  return items;
}

function timeLabel(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function messageTime(msg: MessageItem): number {
  const time = new Date(msg.ts).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function recentMessages(messages: MessageItem[], now: number): MessageItem[] {
  const windowStart = now - VISIBLE_WINDOW_MS;
  return messages
    .filter(isVisibleMessage)
    .filter((msg) => messageTime(msg) >= windowStart)
    .slice(-VISIBLE_MESSAGE_LIMIT);
}

function roleClasses(role: MessageRole) {
  if (role === "viewer") {
    return {
      row: "justify-end",
      bubble: "bg-stardew-green text-white rounded-br-sm",
      meta: "text-stardew-green/80 text-right",
    };
  }
  if (role === "assistant") {
    return {
      row: "justify-start",
      bubble: "bg-white/85 text-stardew-brown border border-stardew-wood/20 rounded-bl-sm",
      meta: "text-stardew-brown/55",
    };
  }
  return {
    row: "justify-center",
    bubble: "bg-stardew-blue/10 text-stardew-brown border border-stardew-blue/20",
    meta: "text-stardew-brown/50 text-center",
  };
}

export default function RoadshowPage() {
  const [text, setText] = useState("");
  const [inputId, setInputId] = useState(() => newInputId());
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<"idle" | "sent" | "error">("idle");
  const [message, setMessage] = useState("");
  const [routeKey, setRouteKey] = useState("");
  const [title, setTitle] = useState("现场消息");
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [liveState, setLiveState] = useState("连接中");
  const [now, setNow] = useState(() => Date.now());
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setRouteKey(new URLSearchParams(window.location.search).get("r")?.trim() ?? "");
  }, []);

  const remaining = MAX_TEXT_CHARS - text.length;
  const canSend = useMemo(
    () => !!routeKey && !sending && !!text.trim() && text.trim().length <= MAX_TEXT_CHARS,
    [routeKey, sending, text],
  );
  const defaultVisibleMessages = useMemo(() => recentMessages(messages, now), [messages, now]);
  const visibleMessages = defaultVisibleMessages;

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const loadHistory = useCallback(async (key: string) => {
    if (!key) return;
    setLoadingHistory(true);
    try {
      const res = await fetch(`/api/roadshow?route_key=${encodeURIComponent(key)}&limit=${VISIBLE_MESSAGE_LIMIT}`, {
        cache: "no-store",
      });
      const data = (await res.json().catch(() => ({}))) as RoadshowHistory;
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || "历史加载失败");
      }
      setTitle(data.route?.title || "现场消息");
      setMessages((prev) => mergeMessages(prev, historyToMessages(data)));
      setLiveState("已连接");
    } catch (e) {
      setStatus("error");
      setMessage(e instanceof Error ? e.message : "历史加载失败");
      setLiveState("不可用");
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  useEffect(() => {
    if (!routeKey) return;
    void loadHistory(routeKey);

    const es = new EventSource(`/api/roadshow/stream?route_key=${encodeURIComponent(routeKey)}`);
    es.onopen = () => setLiveState("已连接");
    es.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as MessageItem;
        if (!msg.id || !msg.text) return;
        if (!isVisibleMessage(msg)) return;
        setMessages((prev) => mergeMessages(prev, [msg]));
      } catch {
        // Ignore malformed event frames.
      }
    };
    es.addEventListener("closed", (ev) => {
      try {
        const msg = JSON.parse((ev as MessageEvent).data) as MessageItem;
        if (msg.id && msg.text && isVisibleMessage(msg)) {
          setMessages((prev) => mergeMessages(prev, [msg]));
        }
      } catch {
        // Ignore.
      }
      setLiveState("已断开");
    });
    es.onerror = () => {
      setLiveState("重连中");
    };

    const poll = window.setInterval(() => void loadHistory(routeKey), 12_000);
    return () => {
      es.close();
      window.clearInterval(poll);
    };
  }, [loadHistory, routeKey]);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [visibleMessages.length]);

  async function submit() {
    const clean = text.trim();
    if (!routeKey) {
      setStatus("error");
      setMessage("入口未配置");
      return;
    }
    if (!clean) {
      setStatus("error");
      setMessage("请输入内容");
      return;
    }
    if (clean.length > MAX_TEXT_CHARS) {
      setStatus("error");
      setMessage(`最多 ${MAX_TEXT_CHARS} 字`);
      return;
    }

    const currentInputId = inputId;
    setSending(true);
    setStatus("idle");
    setMessage("");
    try {
      const res = await fetch("/api/roadshow", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ route_key: routeKey, input_id: currentInputId, text: clean }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        throw new Error(typeof data.error === "string" ? data.error : "发送失败");
      }
      setStatus("sent");
      setMessage(data.status === "duplicate" ? "已提交过，等待 AI 处理" : "已提交，等待 AI 处理");
      setText("");
      setInputId(newInputId());
      void loadHistory(routeKey);
    } catch (e) {
      const err = e instanceof Error ? e.message : "发送失败";
      setStatus("error");
      setMessage(err);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] px-3 py-4 md:px-6 md:py-6 flex items-center justify-center">
      <main className="parchment rounded-2xl w-full max-w-2xl h-[calc(100dvh-5.5rem)] md:h-[calc(100dvh-7rem)] flex flex-col overflow-hidden">
        <div className="px-4 py-3 border-b border-stardew-wood/20 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="font-[family-name:var(--font-pixel)] text-xs md:text-sm text-stardew-brown truncate">
              {title}
            </h1>
            <p className="text-[10px] text-stardew-brown/60 font-mono truncate">
              {routeKey || "未配置入口"}
            </p>
          </div>
          <span className="shrink-0 rounded-full border border-stardew-wood/20 bg-white/45 px-2 py-1 text-[10px] text-stardew-brown/70">
            {loadingHistory ? "同步中" : liveState}
          </span>
        </div>

        <div ref={listRef} className="flex-1 overflow-y-auto px-3 py-4 space-y-3 bg-white/20">
          {visibleMessages.map((msg) => {
            const c = roleClasses(msg.role);
            return (
              <div key={msg.id} className={`flex ${c.row}`}>
                <div className={`max-w-[84%] rounded-2xl px-3 py-2 text-sm leading-6 whitespace-pre-wrap break-words ${c.bubble}`}>
                  {msg.text}
                  <div className={`mt-1 text-[10px] leading-4 ${c.meta}`}>
                    {[timeLabel(msg.ts), msg.meta].filter(Boolean).join(" · ")}
                  </div>
                </div>
              </div>
            );
          })}
          {visibleMessages.length === 0 && (
            <div className="h-full flex items-center justify-center text-xs text-stardew-brown/55">
              暂无消息
            </div>
          )}
        </div>

        <div className="px-3 py-3 border-t border-stardew-wood/20 bg-[#f5e6c8]/85">
          <textarea
            value={text}
            onChange={(e) => {
              setText(e.target.value.slice(0, MAX_TEXT_CHARS + 20));
              if (status !== "idle") {
                setStatus("idle");
                setMessage("");
              }
            }}
            rows={3}
            maxLength={MAX_TEXT_CHARS + 20}
            placeholder="输入你想让 AI 完成或回应的内容"
            className="w-full px-3 py-2 rounded-xl border-2 border-stardew-wood/30 bg-white/80 text-sm leading-6 focus:border-stardew-wood focus:outline-none resize-none"
          />
          <div className="mt-2 flex items-center justify-between gap-3">
            <span className={`text-[10px] ${remaining < 0 ? "text-red-600 font-bold" : "text-stardew-brown/55"}`}>
              {Math.max(0, remaining)} / {MAX_TEXT_CHARS}
            </span>
            {message && (
              <span className={`text-[10px] font-bold ${status === "sent" ? "text-stardew-green" : "text-red-600"}`}>
                {message}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={() => void submit()}
            disabled={!canSend}
            className="stardew-btn w-full py-3 rounded-xl text-sm font-bold mt-3 disabled:opacity-50"
          >
            {sending ? "发送中..." : "发送"}
          </button>
        </div>
      </main>
    </div>
  );
}
