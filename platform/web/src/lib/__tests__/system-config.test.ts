/**
 * unified-business-config TDD 契约测试。
 *
 * 这些测试先于实现编写，用来固定统一业务配置系统的最小行为：
 *   - domain/config_key/environment 命名和范围校验
 *   - value_type 与 config_value 的写入校验
 *   - 读取时 parsed_value 的解析规则
 *   - Web/Vercel 读取 helper 不做 TTL 缓存，每次请求都命中 fetcher
 *   - 类型化读取不允许跨类型强转
 *
 * 运行：
 *   cd platform/web && npx tsx --test src/lib/__tests__/system-config.test.ts
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  readStarcoplayConfigEnvironment,
  normalizeSystemConfigEnvironment,
  parseSystemConfigValue,
  readSystemConfig,
  readSystemConfigTyped,
  validateSystemConfigPayload,
  type ConfigErrorCode,
  type RawSystemConfig,
  type SystemConfigFetcher,
} from "../system-config";

function assertConfigError(error: unknown, code: ConfigErrorCode): void {
  assert.equal(
    typeof error,
    "object",
    `expected config error object, got ${typeof error}`,
  );
  assert.notEqual(error, null);
  assert.equal((error as { code?: string }).code, code);
}

function row(overrides: Partial<RawSystemConfig> = {}): RawSystemConfig {
  return {
    id: "cfg_1",
    domain: "agent_runtime",
    config_key: "default_model_tier",
    environment: "prod",
    status: 1,
    value_type: 4,
    config_value: "balanced",
    description: "默认模型档位",
    created_at: "2026-05-22T00:00:00.000Z",
    updated_at: "2026-05-22T00:00:00.000Z",
    ...overrides,
  };
}

test("validateSystemConfigPayload accepts the canonical minimal payload and normalizes defaults", () => {
  const result = validateSystemConfigPayload({
    domain: "agent_runtime",
    config_key: "default_model_tier",
    environment: "prod",
    value_type: "string",
    config_value: "balanced",
  });

  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.deepEqual(result.value, {
    domain: "agent_runtime",
    config_key: "default_model_tier",
    environment: "prod",
    status: 0,
    value_type: 4,
    config_value: "balanced",
    description: "",
  });
});

test("readStarcoplayConfigEnvironment defaults to prod and rejects invalid values", () => {
  assert.equal(readStarcoplayConfigEnvironment(undefined), "prod");
  assert.equal(readStarcoplayConfigEnvironment("dev"), "dev");
  assert.equal(normalizeSystemConfigEnvironment(" test "), "test");

  assert.throws(
    () => readStarcoplayConfigEnvironment("stage"),
    (error) => {
      assertConfigError(error, "CONFIG_INVALID_ENVIRONMENT");
      return true;
    },
  );
});

test("validateSystemConfigPayload rejects invalid names, environments, and enum values", () => {
  const cases: Array<[string, unknown, ConfigErrorCode]> = [
    [
      "uppercase domain",
      {
        domain: "AgentRuntime",
        config_key: "default_model_tier",
        environment: "prod",
        value_type: "string",
        config_value: "balanced",
      },
      "CONFIG_INVALID_NAME",
    ],
    [
      "one-char key",
      {
        domain: "agent_runtime",
        config_key: "x",
        environment: "prod",
        value_type: "string",
        config_value: "balanced",
      },
      "CONFIG_INVALID_NAME",
    ],
    [
      "unsupported environment",
      {
        domain: "agent_runtime",
        config_key: "default_model_tier",
        environment: "staging",
        value_type: "string",
        config_value: "balanced",
      },
      "CONFIG_INVALID_ENVIRONMENT",
    ],
    [
      "unsupported value_type",
      {
        domain: "agent_runtime",
        config_key: "default_model_tier",
        environment: "prod",
        value_type: "secret",
        config_value: "balanced",
      },
      "CONFIG_TYPE_MISMATCH",
    ],
  ];

  for (const [name, payload, code] of cases) {
    const result = validateSystemConfigPayload(payload);
    assert.equal(result.ok, false, name);
    if (!result.ok) assert.equal(result.error.code, code, name);
  }
});

test("validateSystemConfigPayload enforces value_type write validation", () => {
  const validCases: Array<[string, unknown, unknown]> = [
    ["bool true", "bool", "true"],
    ["bool false", 1, "false"],
    ["int", "int", "-42"],
    ["decimal", "decimal", "3.1415"],
    ["string", "string", "anything"],
    ["json object", "json", "{\"mode\":\"balanced\",\"max_steps\":8}"],
    ["json array", 5, "[1,2,3]"],
  ];

  for (const [name, valueType, configValue] of validCases) {
    const result = validateSystemConfigPayload({
      domain: "agent_runtime",
      config_key: "default_model_tier",
      environment: "prod",
      value_type: valueType,
      config_value: configValue,
    });
    assert.equal(result.ok, true, name);
  }

  const invalidCases: Array<[string, unknown, unknown]> = [
    ["bool is strict", "bool", "yes"],
    ["int rejects decimal", "int", "1.5"],
    ["decimal requires digits before dot", "decimal", ".5"],
    ["json rejects invalid", "json", "{bad"],
  ];

  for (const [name, valueType, configValue] of invalidCases) {
    const result = validateSystemConfigPayload({
      domain: "agent_runtime",
      config_key: "default_model_tier",
      environment: "prod",
      value_type: valueType,
      config_value: configValue,
    });
    assert.equal(result.ok, false, name);
    if (!result.ok) assert.equal(result.error.code, "CONFIG_TYPE_MISMATCH", name);
  }
});

test("parseSystemConfigValue returns typed parsed_value and fails on dirty persisted values", () => {
  assert.equal(parseSystemConfigValue(0, "raw"), "raw");
  assert.equal(parseSystemConfigValue("default", "raw"), "raw");
  assert.equal(parseSystemConfigValue(1, "true"), true);
  assert.equal(parseSystemConfigValue("bool", "false"), false);
  assert.equal(parseSystemConfigValue(2, "-7"), -7);
  assert.equal(parseSystemConfigValue("decimal", "1.25"), 1.25);
  assert.equal(parseSystemConfigValue(4, "hello"), "hello");
  assert.deepEqual(parseSystemConfigValue(5, "{\"enabled\":true}"), {
    enabled: true,
  });

  assert.throws(
    () => parseSystemConfigValue("bool", "TRUE"),
    (error) => {
      assertConfigError(error, "CONFIG_PARSE_ERROR");
      return true;
    },
  );
  assert.throws(
    () => parseSystemConfigValue("json", "{bad"),
    (error) => {
      assertConfigError(error, "CONFIG_PARSE_ERROR");
      return true;
    },
  );
});

test("readSystemConfig returns active config with parsed_value and performs no Web-side TTL caching", async () => {
  const calls: Array<[string, string, string]> = [];
  const fetcher: SystemConfigFetcher = async (domain, configKey, environment) => {
    calls.push([domain, configKey, environment]);
    return row({ value_type: 2, config_value: String(calls.length) });
  };

  assert.deepEqual(await readSystemConfig({
    fetcher,
    domain: "agent_runtime",
    configKey: "default_model_tier",
    environment: "prod",
  }), {
    id: "cfg_1",
    domain: "agent_runtime",
    config_key: "default_model_tier",
    environment: "prod",
    status: "active",
    status_code: 1,
    value_type: "int",
    value_type_code: 2,
    config_value: "1",
    parsed_value: 1,
    description: "默认模型档位",
    created_at: "2026-05-22T00:00:00.000Z",
    updated_at: "2026-05-22T00:00:00.000Z",
  });
  assert.equal((await readSystemConfig({
    fetcher,
    domain: "agent_runtime",
    configKey: "default_model_tier",
    environment: "prod",
  })).parsed_value, 2);
  assert.equal(calls.length, 2, "Web helper must not cache between requests");
});

test("readSystemConfig maps not_found, disabled, and parse errors without caching them", async () => {
  const scripts: Array<RawSystemConfig | Error> = [
    Object.assign(new Error("missing"), { code: "CONFIG_NOT_FOUND" }),
    Object.assign(new Error("disabled"), { code: "CONFIG_DISABLED" }),
    row({ value_type: 5, config_value: "{bad" }),
    row({ value_type: 5, config_value: "{\"ok\":true}" }),
  ];
  let calls = 0;
  const fetcher: SystemConfigFetcher = async () => {
    calls += 1;
    const next = scripts.shift();
    if (!next) throw Object.assign(new Error("missing"), { code: "CONFIG_NOT_FOUND" });
    if (next instanceof Error) throw next;
    return next;
  };

  await assert.rejects(
    () => readSystemConfig({
      fetcher,
      domain: "agent_runtime",
      configKey: "default_model_tier",
      environment: "prod",
    }),
    (error) => {
      assertConfigError(error, "CONFIG_NOT_FOUND");
      return true;
    },
  );
  await assert.rejects(
    () => readSystemConfig({
      fetcher,
      domain: "agent_runtime",
      configKey: "default_model_tier",
      environment: "prod",
    }),
    (error) => {
      assertConfigError(error, "CONFIG_DISABLED");
      return true;
    },
  );
  await assert.rejects(
    () => readSystemConfig({
      fetcher,
      domain: "agent_runtime",
      configKey: "default_model_tier",
      environment: "prod",
    }),
    (error) => {
      assertConfigError(error, "CONFIG_PARSE_ERROR");
      return true;
    },
  );
  assert.deepEqual(
    (await readSystemConfig({
      fetcher,
      domain: "agent_runtime",
      configKey: "default_model_tier",
      environment: "prod",
    })).parsed_value,
    { ok: true },
  );
  assert.equal(calls, 4);
});

test("readSystemConfigTyped rejects mismatched value_type instead of coercing", async () => {
  const fetcher: SystemConfigFetcher = async () =>
    row({ value_type: 4, config_value: "true" });

  await assert.rejects(
    () => readSystemConfigTyped({
      fetcher,
      domain: "agent_runtime",
      configKey: "default_model_tier",
      environment: "prod",
      expectedType: "bool",
    }),
    (error) => {
      assertConfigError(error, "CONFIG_TYPE_MISMATCH");
      return true;
    },
  );
});
