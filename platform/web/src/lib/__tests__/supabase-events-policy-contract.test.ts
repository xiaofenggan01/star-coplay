import { test } from "node:test";
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { shouldPersistEvent as shouldPersistWebEvent } from "../event-ingest-policy";
import { shouldPersistEvent as shouldPersistSupabaseEvent } from "../../../../supabase/functions/events/policy.ts";

const policy = JSON.parse(
  readFileSync(resolve(process.cwd(), "../shared/telemetry-event-policy.json"), "utf8"),
) as {
  persisted_examples: string[];
  dropped_examples: string[];
};

test("Supabase events policy matches shared contract", () => {
  for (const eventType of policy.persisted_examples) {
    assert.equal(shouldPersistSupabaseEvent(eventType), true, eventType);
  }
  for (const eventType of policy.dropped_examples) {
    assert.equal(shouldPersistSupabaseEvent(eventType), false, eventType);
  }
});

test("Supabase and web event filters stay in sync", () => {
  for (const eventType of [...policy.persisted_examples, ...policy.dropped_examples]) {
    assert.equal(
      shouldPersistSupabaseEvent(eventType),
      shouldPersistWebEvent(eventType),
      eventType,
    );
  }
});
