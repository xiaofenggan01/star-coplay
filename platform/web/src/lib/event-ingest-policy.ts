const DROPPED_EVENT_TYPES = new Set(["ai.llm_call", "ai.error", "ai.action"]);
const DROPPED_EVENT_PREFIXES = ["error."];

export function shouldPersistEvent(type: unknown): boolean {
  if (typeof type !== "string") return false;
  return (
    !DROPPED_EVENT_TYPES.has(type) &&
    !DROPPED_EVENT_PREFIXES.some((prefix) => type.startsWith(prefix))
  );
}

export function countDroppedEvents(events: Array<{ type?: unknown }>): number {
  return events.reduce((count, event) => count + (shouldPersistEvent(event.type) ? 0 : 1), 0);
}
