import { test } from "node:test";
import assert from "node:assert/strict";
import {
  defaultPlatformOnboardingPolicy,
  getPlatformKeyAccess,
  readPlatformOnboardingPolicy,
} from "../platform-access";

interface MockConfigRow {
  id: string;
  domain: string;
  config_key: string;
  environment: string;
  status: number;
  value_type: number;
  config_value: string;
  description: string;
  created_at: string;
  updated_at: string;
}

interface MockState {
  configRows?: MockConfigRow[];
  profileRow?: { user_type: string } | null;
  profileError?: { message: string } | null;
}

function makeConfigRow(configValue: string): MockConfigRow {
  return {
    id: "config-1",
    domain: "platform_access",
    config_key: "onboarding_policy",
    environment: "prod",
    status: 1,
    value_type: 5,
    config_value: configValue,
    description: "",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

function makeSupabase(state: MockState): any {
  return {
    from(table: string) {
      const filters: Array<[string, unknown]> = [];
      const query: any = {
        select() {
          return query;
        },
        eq(key: string, value: unknown) {
          filters.push([key, value]);
          return query;
        },
        async maybeSingle() {
          if (table === "profiles") {
            return {
              data: state.profileRow ?? null,
              error: state.profileError ?? null,
            };
          }
          throw new Error(`unexpected maybeSingle table=${table}`);
        },
        then(resolve: (value: unknown) => unknown, reject: (reason: unknown) => unknown) {
          if (table !== "system_configs") {
            return Promise.reject(new Error(`unexpected awaited table=${table}`)).then(resolve, reject);
          }
          const rows = (state.configRows ?? []).filter((row) => {
            return filters.every(([key, value]) => (row as unknown as Record<string, unknown>)[key] === value);
          });
          return Promise.resolve({ data: rows, error: null }).then(resolve, reject);
        },
      };
      return query;
    },
  };
}

test("readPlatformOnboardingPolicy: config missing uses fail-closed default", async () => {
  const policy = await readPlatformOnboardingPolicy(makeSupabase({ configRows: [] }));
  assert.deepEqual(policy, defaultPlatformOnboardingPolicy());
});

test("getPlatformKeyAccess: guest is denied by default policy", async () => {
  const access = await getPlatformKeyAccess(
    "user-1",
    makeSupabase({
      configRows: [makeConfigRow(JSON.stringify(defaultPlatformOnboardingPolicy()))],
      profileRow: { user_type: "guest" },
    }),
  );
  assert.equal(access.ok, false);
  assert.equal(access.ok ? "" : access.code, "access_not_approved");
  assert.equal(access.ok ? 0 : access.status, 403);
});

test("getPlatformKeyAccess: beta_user is allowed by default policy", async () => {
  const access = await getPlatformKeyAccess(
    "user-2",
    makeSupabase({
      configRows: [makeConfigRow(JSON.stringify(defaultPlatformOnboardingPolicy()))],
      profileRow: { user_type: "beta_user" },
    }),
  );
  assert.equal(access.ok, true);
  assert.equal(access.ok ? access.user_type : "", "beta_user");
});

test("getPlatformKeyAccess: profile query failure returns fail-closed service error", async () => {
  const access = await getPlatformKeyAccess(
    "user-3",
    makeSupabase({
      configRows: [makeConfigRow(JSON.stringify(defaultPlatformOnboardingPolicy()))],
      profileError: { message: "db down" },
    }),
  );
  assert.equal(access.ok, false);
  assert.equal(access.ok ? "" : access.code, "access_check_failed");
  assert.equal(access.ok ? 0 : access.status, 503);
});

