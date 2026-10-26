import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

type EventRow = {
  user_id: string | null;
  session_id: string | null;
  type: string;
  ts: string;
};

const ACTIVE_EVENT_TYPES = new Set([
  "session.start",
  "session.heartbeat",
  "session.end",
  "player.message",
  "user_report",
  "external_text.input_merged",
  "external_text.agent_visible_output",
]);

const SESSION_EVENT_TYPES = new Set([
  "session.start",
  "session.heartbeat",
  "session.end",
  "player.message",
]);

function iso(value: string) {
  return new Date(value);
}

function daysAgo(now: Date, days: number) {
  return new Date(now.getTime() - days * 86_400_000);
}

function computeDau(events: EventRow[], now: Date, days: number) {
  const windowStart = daysAgo(now, Math.max(days, 1)).getTime();
  const grouped = new Map<string, Set<string>>();

  for (const event of events) {
    if (!event.user_id || !ACTIVE_EVENT_TYPES.has(event.type)) continue;
    const at = iso(event.ts).getTime();
    if (at < windowStart) continue;
    const day = event.ts.slice(0, 10);
    if (!grouped.has(day)) grouped.set(day, new Set());
    grouped.get(day)!.add(event.user_id);
  }

  return [...grouped.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, users]) => ({ day, dau: users.size }));
}

function computeAvgSessionDuration(events: EventRow[], now: Date, days: number) {
  const windowStart = daysAgo(now, Math.max(days, 1)).getTime();
  const sessions = new Map<string, { first: Date; last: Date; heartbeats: number; day: string }>();

  for (const event of events) {
    if (!event.session_id || !SESSION_EVENT_TYPES.has(event.type)) continue;
    const at = iso(event.ts);
    if (at.getTime() < windowStart) continue;
    const current = sessions.get(event.session_id);
    if (!current) {
      sessions.set(event.session_id, {
        first: at,
        last: at,
        heartbeats: event.type === "session.heartbeat" ? 1 : 0,
        day: event.ts.slice(0, 10),
      });
      continue;
    }
    if (at < current.first) {
      current.first = at;
      current.day = event.ts.slice(0, 10);
    }
    if (at > current.last) current.last = at;
    if (event.type === "session.heartbeat") current.heartbeats += 1;
  }

  const perDay = new Map<string, number[]>();
  for (const session of sessions.values()) {
    const elapsedMinutes = (session.last.getTime() - session.first.getTime()) / 60_000;
    const minutes = Math.max(elapsedMinutes, session.heartbeats * 5, 1);
    if (!perDay.has(session.day)) perDay.set(session.day, []);
    perDay.get(session.day)!.push(minutes);
  }

  return [...perDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, minutes]) => ({
      day,
      total_sessions: minutes.length,
      avg_duration_min: Math.round((minutes.reduce((sum, value) => sum + value, 0) / minutes.length) * 10) / 10,
    }));
}

function computeRetention(events: EventRow[], today: string) {
  const activeDays = new Map<string, Set<string>>();
  for (const event of events) {
    if (!event.user_id || !ACTIVE_EVENT_TYPES.has(event.type) || event.type === "external_text.agent_visible_output") continue;
    if (!activeDays.has(event.user_id)) activeDays.set(event.user_id, new Set());
    activeDays.get(event.user_id)!.add(event.ts.slice(0, 10));
  }

  const todayDate = iso(`${today}T00:00:00.000Z`);
  const cohorts = [...activeDays.entries()]
    .map(([userId, days]) => ({ userId, days: [...days].sort() }))
    .reduce((cohorts, current) => {
      const firstDay = current.days[0];
      if (!cohorts.has(firstDay)) cohorts.set(firstDay, []);
      cohorts.get(firstDay)!.push(current.days);
      return cohorts;
    }, new Map<string, string[][]>());

  return [...cohorts.entries()]
    .map(([cohortDate, members]) => {
      const first = iso(`${cohortDate}T00:00:00.000Z`);
      const cohortSize = members.length;
      const metric = (offset: number) => {
        const matureBoundary = new Date(first.getTime() + offset * 86_400_000);
        if (todayDate.getTime() <= matureBoundary.getTime()) return null;
        const targetDay = matureBoundary.toISOString().slice(0, 10);
        const retained = members.filter((memberDays) => memberDays.includes(targetDay)).length;
        return Math.round((retained / cohortSize) * 1000) / 10;
      };
      return {
        cohort_date: cohortDate,
        cohort_size: cohortSize,
        d1: metric(1),
        d3: metric(3),
        d7: metric(7),
      };
    })
    .sort((a, b) => b.cohort_date.localeCompare(a.cohort_date));
}

test("dashboard metric fixtures cover empty table and repeated same-day activity", () => {
  const now = iso("2026-07-11T12:00:00.000Z");
  const events: EventRow[] = [
    { user_id: "u1", session_id: "s1", type: "session.start", ts: "2026-07-10T08:00:00.000Z" },
    { user_id: "u1", session_id: "s1", type: "player.message", ts: "2026-07-10T08:03:00.000Z" },
    { user_id: "u1", session_id: "s1", type: "user_report", ts: "2026-07-10T08:10:00.000Z" },
  ];

  assert.deepEqual(computeDau([], now, 7), []);
  assert.deepEqual(computeAvgSessionDuration([], now, 7), []);
  assert.deepEqual(computeRetention([], "2026-07-11"), []);
  assert.deepEqual(computeDau(events, now, 7), [{ day: "2026-07-10", dau: 1 }]);
});

test("admin dashboard runtime metrics come from the ECS internal endpoint", () => {
  const route = readFileSync(resolve(process.cwd(), "src/app/api/admin/route.ts"), "utf8");
  const dashboardBlock = route.slice(
    route.indexOf('if (action === "dashboard")'),
    route.indexOf('if (action === "llm_stats")'),
  );
  assert.ok(
    dashboardBlock.includes("callBackendInternal(`/internal/admin/dashboard?days=${days}`)"),
  );
  assert.doesNotMatch(dashboardBlock, /supabase\.rpc\("admin_dau"/);
  assert.doesNotMatch(dashboardBlock, /supabase\.rpc\("admin_avg_session_duration"/);
  assert.doesNotMatch(dashboardBlock, /supabase\.rpc\("admin_retention"/);
});

test("single heartbeat floors a session to five minutes and window start is inclusive", () => {
  const now = iso("2026-07-11T12:00:00.000Z");
  const boundaryTs = "2026-07-04T12:00:00.000Z";
  const events: EventRow[] = [
    { user_id: "u1", session_id: "s1", type: "session.heartbeat", ts: boundaryTs },
    { user_id: "u2", session_id: "s2", type: "session.heartbeat", ts: "2026-07-04T11:59:59.000Z" },
  ];

  assert.deepEqual(computeDau(events, now, 7), [{ day: "2026-07-04", dau: 1 }]);
  assert.deepEqual(computeAvgSessionDuration(events, now, 7), [
    { day: "2026-07-04", total_sessions: 1, avg_duration_min: 5 },
  ]);
});

test("cross-day sessions stay attributed to the first seen day", () => {
  const now = iso("2026-07-11T12:00:00.000Z");
  const events: EventRow[] = [
    { user_id: "u1", session_id: "night-run", type: "session.start", ts: "2026-07-09T23:55:00.000Z" },
    { user_id: "u1", session_id: "night-run", type: "session.end", ts: "2026-07-10T00:10:00.000Z" },
  ];

  assert.deepEqual(computeAvgSessionDuration(events, now, 7), [
    { day: "2026-07-09", total_sessions: 1, avg_duration_min: 15 },
  ]);
});

test("immature retention cohorts return null until the measurement window closes", () => {
  const events: EventRow[] = [
    { user_id: "mature-1", session_id: "s1", type: "session.start", ts: "2026-07-01T08:00:00.000Z" },
    { user_id: "mature-1", session_id: "s2", type: "session.start", ts: "2026-07-02T08:00:00.000Z" },
    { user_id: "mature-2", session_id: "s3", type: "session.start", ts: "2026-07-01T09:00:00.000Z" },
    { user_id: "fresh-1", session_id: "s4", type: "session.start", ts: "2026-07-10T08:00:00.000Z" },
  ];

  assert.deepEqual(computeRetention(events, "2026-07-11"), [
    {
      cohort_date: "2026-07-10",
      cohort_size: 1,
      d1: null,
      d3: null,
      d7: null,
    },
    {
      cohort_date: "2026-07-01",
      cohort_size: 2,
      d1: 50,
      d3: 0,
      d7: 0,
    },
  ]);
});
