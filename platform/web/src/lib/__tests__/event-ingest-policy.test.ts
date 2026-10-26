import { test } from "node:test";
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { countDroppedEvents, shouldPersistEvent } from "../event-ingest-policy";

const policy = JSON.parse(
  readFileSync(resolve(process.cwd(), "../shared/telemetry-event-policy.json"), "utf8"),
) as {
  persisted_examples: string[];
  dropped_examples: string[];
};

test("shouldPersistEvent: keeps business and product events", () => {
  for (const eventType of policy.persisted_examples) {
    assert.equal(shouldPersistEvent(eventType), true, eventType);
  }
});

test("shouldPersistEvent: drops runtime llm/action/error telemetry", () => {
  for (const eventType of policy.dropped_examples) {
    assert.equal(shouldPersistEvent(eventType), false, eventType);
  }
});

test("countDroppedEvents: reports dropped events without rejecting the batch", () => {
  assert.equal(
    countDroppedEvents([
      { type: "player.message" },
      { type: "ai.llm_call" },
      { type: "error.runtime" },
      { type: "session.end" },
    ]),
    2,
  );
});
