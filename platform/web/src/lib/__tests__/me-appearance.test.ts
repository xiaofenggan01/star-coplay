import { test } from "node:test";
import assert from "node:assert/strict";
import { handleGetAppearance } from "../me-appearance-handler";
import { DEFAULT_APPEARANCE } from "../types/appearance";
import type { AuthenticatedUser } from "../types/auth";

const user: AuthenticatedUser = {
  user_id: "user-appearance",
  auth_id: "auth-appearance",
  source: "jwt",
  email: "appearance@example.test",
};

test("handleGetAppearance returns the Mod-synced appearance from profile config", async () => {
  const syncedAppearance = {
    ...DEFAULT_APPEARANCE,
    hairstyle: 12,
    shirtIndex: 20,
    updated_at: "2026-06-23T00:00:00.000Z",
  };

  const result = await handleGetAppearance(
    {
      async getConfig(userId) {
        assert.equal(userId, user.user_id);
        return { appearance: syncedAppearance };
      },
    },
    user,
  );

  assert.equal(result.status, 200);
  assert.deepEqual(result.body, { appearance: syncedAppearance });
});

test("handleGetAppearance returns null when the Mod has not synced appearance yet", async () => {
  const result = await handleGetAppearance(
    {
      async getConfig() {
        return {};
      },
    },
    user,
  );

  assert.equal(result.status, 200);
  assert.deepEqual(result.body, { appearance: null });
});
