"use client";

import { useCallback, useEffect, useState } from "react";

type RolloutMode = "allowlist" | "all_authenticated";

interface RolloutConfigRow {
  id: string;
  status: "active" | "disabled";
  parsed_value: unknown;
}

interface RolloutForm {
  enabled: boolean;
  mode: RolloutMode;
  userIds: string;
  emails: string;
}

const EMPTY_FORM: RolloutForm = {
  enabled: false,
  mode: "allowlist",
  userIds: "",
  emails: "",
};

function splitEntries(value: string): string[] {
  return Array.from(new Set(
    value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean),
  ));
}

function parseForm(value: unknown): RolloutForm {
  if (!value || typeof value !== "object") return EMPTY_FORM;
  const record = value as Record<string, unknown>;
  const mode = record.mode === "all_authenticated" ? "all_authenticated" : "allowlist";
  return {
    enabled: record.enabled === true,
    mode,
    userIds: Array.isArray(record.user_ids) ? record.user_ids.join("\n") : "",
    emails: Array.isArray(record.emails) ? record.emails.join("\n") : "",
  };
}

export default function HostedRolloutPanel({
  secret,
  environment,
  onSaved,
}: {
  secret: string;
  environment: string;
  onSaved: () => void;
}) {
  const [row, setRow] = useState<RolloutConfigRow | null>(null);
  const [form, setForm] = useState<RolloutForm>(EMPTY_FORM);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const headers = { "x-admin-secret": secret, "Content-Type": "application/json" };

  const load = useCallback(async () => {
    setLoading(true);
    setMessage("");
    try {
      const params = new URLSearchParams({
        environment,
        domain: "agent_runtime",
      });
      const response = await fetch(`/api/admin/configs?${params}`, { headers });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "读取灰度配置失败");
      const found = (payload.configs ?? []).find(
        (item: { config_key?: string }) => item.config_key === "hosted_rollout",
      ) as RolloutConfigRow | undefined;
      setRow(found ?? null);
      setForm(found ? parseForm(found.parsed_value) : EMPTY_FORM);
    } catch (error) {
      setRow(null);
      setForm(EMPTY_FORM);
      setMessage(error instanceof Error ? error.message : "读取灰度配置失败");
    } finally {
      setLoading(false);
    }
  }, [secret, environment]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { void load(); }, [load]);

  async function save() {
    setSaving(true);
    setMessage("");
    try {
      const configValue = JSON.stringify({
        enabled: form.enabled,
        mode: form.mode,
        user_ids: splitEntries(form.userIds),
        emails: splitEntries(form.emails).map((email) => email.toLowerCase()),
      });
      const body = JSON.stringify({
        domain: "agent_runtime",
        config_key: "hosted_rollout",
        environment,
        status: "active",
        value_type: "json",
        config_value: configValue,
        description: "Controls Hosted Claude Code + MCP rollout. Non-matching users remain on Legacy.",
      });
      const response = await fetch(
        row ? `/api/admin/configs/${row.id}` : "/api/admin/configs",
        { method: row ? "PUT" : "POST", headers, body },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "保存灰度配置失败");
      const savedId = String(payload.id ?? row?.id ?? "");
      if (savedId && (payload.status === "disabled" || row?.status === "disabled")) {
        const enableResponse = await fetch(`/api/admin/configs/${savedId}/enable`, {
          method: "POST",
          headers,
        });
        if (!enableResponse.ok) {
          const enablePayload = await enableResponse.json();
          throw new Error(enablePayload.error?.message ?? "启用灰度配置失败");
        }
      }
      setMessage("已保存，Backend 最迟 60 秒内刷新");
      await load();
      onSaved();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存灰度配置失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="parchment rounded-lg p-4 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-bold">Hosted 灰度</h3>
          <p className="text-[11px] text-stardew-brown/60 mt-1">
            当前环境：<span className="font-mono">{environment}</span>。未命中用户继续使用 Legacy。
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs font-semibold">
          <input
            type="checkbox"
            checked={form.enabled}
            disabled={loading || saving}
            onChange={(event) => setForm((current) => ({
              ...current,
              enabled: event.target.checked,
            }))}
          />
          启用 Hosted
        </label>
      </div>

      <div className="inline-flex border border-stardew-wood/30 rounded-md overflow-hidden">
        {([
          ["allowlist", "指定用户"],
          ["all_authenticated", "全部已认证用户"],
        ] as const).map(([value, label]) => (
          <button
            key={value}
            type="button"
            disabled={loading || saving}
            onClick={() => setForm((current) => ({ ...current, mode: value }))}
            className={`px-3 py-1.5 text-[11px] font-bold ${
              form.mode === value
                ? "bg-stardew-wood/20 text-stardew-brown"
                : "bg-white/40 text-stardew-brown/60"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {form.mode === "allowlist" && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="text-[11px] font-semibold">
            User ID
            <textarea
              rows={4}
              value={form.userIds}
              onChange={(event) => setForm((current) => ({
                ...current,
                userIds: event.target.value,
              }))}
              placeholder="每行一个 user ID"
              className="mt-1 w-full px-2 py-1.5 border border-stardew-wood/30 rounded bg-white/60 font-mono font-normal"
            />
          </label>
          <label className="text-[11px] font-semibold">
            邮箱
            <textarea
              rows={4}
              value={form.emails}
              onChange={(event) => setForm((current) => ({
                ...current,
                emails: event.target.value,
              }))}
              placeholder="每行一个邮箱"
              className="mt-1 w-full px-2 py-1.5 border border-stardew-wood/30 rounded bg-white/60 font-mono font-normal"
            />
          </label>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className={`text-[11px] ${message.includes("失败") ? "text-red-600" : "text-stardew-brown/60"}`}>
          {message}
        </p>
        <button
          type="button"
          disabled={loading || saving}
          onClick={() => void save()}
          className="stardew-btn px-4 py-2 rounded-lg text-xs font-bold disabled:opacity-50"
        >
          {saving ? "保存中..." : "保存灰度"}
        </button>
      </div>
    </section>
  );
}
