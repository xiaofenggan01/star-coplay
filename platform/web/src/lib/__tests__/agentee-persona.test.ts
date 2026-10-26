/**
 * agentee persona web 侧调用测试
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { canAccessAgenteePersona } from "../agentee-persona-access";
import { compilePersonaArtifacts, parsePersonaDraft } from "../agentee-persona-generation";
import { canAccessLlmUi } from "../llm-ui-access";
import { validateAgenteeInput } from "../types/agentee";

const originalEnv = {
  INTERNAL_API_TOKEN: process.env.INTERNAL_API_TOKEN,
  INTERNAL_CONFIG_TOKEN: process.env.INTERNAL_CONFIG_TOKEN,
  PLATFORM_WS_HTTP_URL: process.env.PLATFORM_WS_HTTP_URL,
};

function restoreEnv() {
  process.env.INTERNAL_API_TOKEN = originalEnv.INTERNAL_API_TOKEN;
  process.env.INTERNAL_CONFIG_TOKEN = originalEnv.INTERNAL_CONFIG_TOKEN;
  process.env.PLATFORM_WS_HTTP_URL = originalEnv.PLATFORM_WS_HTTP_URL;
}

test("parsePersonaDraft 通过 backend internal 调用 persona parse 接口", async () => {
  const originalFetch = globalThis.fetch;
  const seen: { url?: string; auth?: string; body?: unknown } = {};

  process.env.INTERNAL_API_TOKEN = "internal-token";
  process.env.PLATFORM_WS_HTTP_URL = "http://backend.test";

  globalThis.fetch = (async (_url: RequestInfo | URL, init?: RequestInit) => {
    seen.url = String(_url);
    seen.auth = init?.headers instanceof Headers ? init.headers.get("Authorization") ?? undefined : undefined;
    if (typeof init?.body === "string") {
      seen.body = JSON.parse(init.body);
    }
    return new Response(
      JSON.stringify({
        core_preferences: {
          warmth: 5,
          expressiveness: 4,
          playfulness: 3,
          initiative: 2,
          carefulness: 4,
          goal_focus: 5,
        },
        custom_flavor: [{ label: "嘴硬关心", description: "先吐槽再补位" }],
        generated_by_model: "gpt-5.4",
        persona_schema_version: 1,
        generation_status: "generated",
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  }) as typeof fetch;

  try {
    const result = await parsePersonaDraft({
      custom_description: "嘴上嫌弃我但很靠谱。",
      tags: ["quiet_companion"],
    });

    assert.equal(seen.url, "http://backend.test/internal/persona/parse");
    assert.equal(seen.auth, "Bearer internal-token");
    assert.deepEqual(seen.body, {
      custom_description: "嘴上嫌弃我但很靠谱。",
      tags: ["quiet_companion"],
      selected_tags: ["quiet_companion"],
      persona_schema_version: 1,
    });
    assert.equal(result.core_preferences.warmth, 5);
    assert.equal(result.custom_flavor.length, 1);
    assert.equal(result.generation_status, "generated");
    assert.equal(result.generated_by_model, "gpt-5.4");
  } finally {
    globalThis.fetch = originalFetch;
    restoreEnv();
  }
});

test("compilePersonaArtifacts 通过 backend internal 调用 persona compile 接口", async () => {
  const originalFetch = globalThis.fetch;
  const seen: { url?: string; auth?: string; body?: unknown } = {};

  process.env.INTERNAL_API_TOKEN = "internal-token";
  process.env.PLATFORM_WS_HTTP_URL = "http://backend.test";

  globalThis.fetch = (async (_url: RequestInfo | URL, init?: RequestInit) => {
    seen.url = String(_url);
    seen.auth = init?.headers instanceof Headers ? init.headers.get("Authorization") ?? undefined : undefined;
    if (typeof init?.body === "string") {
      seen.body = JSON.parse(init.body);
    }
    return new Response(
      JSON.stringify({
        style_summary: "温柔可靠",
        speech_guidelines: ["语气亲切", "简短直接"],
        collaboration_guidelines: ["主动提供建议"],
        gameplay_guidelines: ["优先协助完成目标"],
        persona_card: "persona card",
        generated_by_model: "gpt-5.4",
        persona_schema_version: 1,
        generation_status: "generated",
        generation_error: null,
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  }) as typeof fetch;

  try {
    const result = await compilePersonaArtifacts({
      profile: {
        display_name: "小枫",
        user_call_name: "主人",
        relationship_type: "游戏搭子",
        short_bio: "一起种田",
      },
      persona: {
        custom_description: "平时安静，关键时刻主动提醒我。",
        core_preferences: {
          warmth: 4,
          expressiveness: 3,
          playfulness: 2,
          initiative: 4,
          carefulness: 4,
          goal_focus: 5,
        },
        custom_flavor: [{ label: "细心", description: "会盯着细节" }],
      },
    });

    assert.equal(seen.url, "http://backend.test/internal/persona/compile");
    assert.equal(seen.auth, "Bearer internal-token");
    assert.equal(result.persona_card, "persona card");
    assert.equal(result.generation_status, "generated");
    assert.equal(result.generated_by_model, "gpt-5.4");
  } finally {
    globalThis.fetch = originalFetch;
    restoreEnv();
  }
});

test("validateAgenteeInput 接受 backend 生成的 custom_flavor 长度上限", () => {
  const result = validateAgenteeInput({
    profile: {
      display_name: "小枫",
      user_call_name: "主人",
      relationship_type: "游戏搭子",
    },
    persona: {
      custom_description: "希望有一个稳定可靠的搭子。",
      core_preferences: {
        warmth: 4,
        expressiveness: 3,
        playfulness: 2,
        initiative: 4,
        carefulness: 4,
        goal_focus: 5,
      },
      custom_flavor: [{
        label: "标".repeat(12),
        description: "说".repeat(80),
      }],
    },
  });

  assert.equal(result.ok, true);
});

function mockConfigSupabase(parsedValue: unknown) {
  return {
    from() {
      return {
        select() {
          return {
            eq() {
              return {
                eq() {
                  return {
                    eq() {
                      return Promise.resolve({
                        data: [{
                          id: "cfg",
                          domain: "agentee_persona",
                          config_key: "access_whitelist",
                          environment: "prod",
                          status: 1,
                          value_type: 5,
                          config_value: JSON.stringify(parsedValue),
                          description: "",
                          created_at: "",
                          updated_at: "",
                        }],
                        error: null,
                      });
                    },
                  };
                },
              };
            },
          };
        },
      };
    },
  };
}

test("canAccessAgenteePersona 支持 system_configs 白名单邮箱", async () => {
  const supabase = mockConfigSupabase({ enabled: true, allow_all: false, emails: ["2024057531@qq.com"], user_ids: [] });

  const allowed = await canAccessAgenteePersona(
    { user_id: "u1", email: "2024057531@qq.com" },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    supabase as any,
  );
  const denied = await canAccessAgenteePersona(
    { user_id: "u2", email: "other@example.com" },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    supabase as any,
  );

  assert.equal(allowed, true);
  assert.equal(denied, false);
});

test("canAccessLlmUi supports allow_all and user_id whitelist", async () => {
  const allowAllSupabase = mockConfigSupabase({ enabled: true, allow_all: true, emails: [], user_ids: [] });
  assert.equal(
    await canAccessLlmUi(
      { user_id: "u-any", email: "any@example.com" },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      allowAllSupabase as any,
    ),
    true,
  );

  const graySupabase = mockConfigSupabase({ enabled: true, allow_all: false, emails: [], user_ids: ["u-gray"] });
  assert.equal(
    await canAccessLlmUi(
      { user_id: "u-gray", email: "other@example.com" },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      graySupabase as any,
    ),
    true,
  );
  assert.equal(
    await canAccessLlmUi(
      { user_id: "u-denied", email: "other@example.com" },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      graySupabase as any,
    ),
    false,
  );
});
