import { test } from "node:test";
import assert from "node:assert/strict";
import { attachOfficialInviteRelation } from "../user-relations";

interface MockState {
  existing: { user_id: string; inviter_user_id: string | null; invite_code: string | null } | null;
  inserted: Record<string, unknown>[];
  updated: Record<string, unknown>[];
  updateResult: { user_id: string } | null;
}

function makeSupabase(state: MockState): any {
  return {
    from(table: string) {
      assert.equal(table, "user_relations");
      const filters: Array<[string, unknown]> = [];
      const nullFilters: string[] = [];
      const builder: any = {
        select() {
          return builder;
        },
        eq(key: string, value: unknown) {
          filters.push([key, value]);
          return builder;
        },
        is(key: string, value: unknown) {
          if (value === null) nullFilters.push(key);
          return builder;
        },
        async maybeSingle() {
          return { data: state.existing, error: null };
        },
        insert(payload: Record<string, unknown>) {
          state.inserted.push(payload);
          return Promise.resolve({ error: null });
        },
        update(payload: Record<string, unknown>) {
          state.updated.push({ payload, filters, nullFilters });
          return {
            eq(key: string, value: unknown) {
              filters.push([key, value]);
              return this;
            },
            is(key: string, value: unknown) {
              if (value === null) nullFilters.push(key);
              return this;
            },
            select() {
              return {
                async maybeSingle() {
                  return { data: state.updateResult, error: null };
                },
              };
            },
          };
        },
      };
      return builder;
    },
  };
}

test("attachOfficialInviteRelation updates an empty root relation", async () => {
  const state: MockState = {
    existing: { user_id: "user-1", inviter_user_id: null, invite_code: null },
    inserted: [],
    updated: [],
    updateResult: { user_id: "user-1" },
  };

  const result = await attachOfficialInviteRelation(
    { user_id: "user-1", invite_code: "OFFICIAL-1234" },
    makeSupabase(state),
  );

  assert.deepEqual(result, { ok: true, status: "updated" });
  assert.equal(state.inserted.length, 0);
  assert.equal(state.updated.length, 1);
  assert.equal((state.updated[0].payload as Record<string, unknown>).invite_code, "OFFICIAL-1234");
});

test("attachOfficialInviteRelation rejects an already bound relation", async () => {
  const state: MockState = {
    existing: { user_id: "user-1", inviter_user_id: null, invite_code: "OTHER-CODE" },
    inserted: [],
    updated: [],
    updateResult: null,
  };

  const result = await attachOfficialInviteRelation(
    { user_id: "user-1", invite_code: "OFFICIAL-1234" },
    makeSupabase(state),
  );

  assert.deepEqual(result, { ok: false, reason: "relation_already_bound" });
  assert.equal(state.inserted.length, 0);
  assert.equal(state.updated.length, 0);
});
