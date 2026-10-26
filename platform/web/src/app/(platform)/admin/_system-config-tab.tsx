"use client";

import { useState, useEffect, useCallback } from "react";
import HostedRolloutPanel from "./_hosted-rollout-panel";

// ─── Tab: System Config ───────────────────────────────────────────────────────

interface SystemConfig {
  id: string;
  domain: string;
  config_key: string;
  environment: string;
  status: "active" | "disabled";
  status_code: number;
  value_type: string;
  value_type_code: number;
  config_value: string;
  parsed_value: unknown;
  description: string;
  created_at: string;
  updated_at: string;
}

const ENVIRONMENTS = ["dev", "test", "prod"] as const;
const VALUE_TYPES = ["default", "bool", "int", "decimal", "string", "json"] as const;

const ENVIRONMENT_LABELS: Record<string, string> = {
  dev: "开发环境（dev）",
  test: "测试环境（test）",
  prod: "生产环境（prod）",
};

const STATUS_LABELS: Record<SystemConfig["status"], string> = {
  active: "启用（active）",
  disabled: "停用（disabled）",
};

const VALUE_TYPE_LABELS: Record<string, string> = {
  default: "默认类型（default）",
  bool: "布尔值（bool）",
  int: "整数（int）",
  decimal: "小数（decimal）",
  string: "字符串（string）",
  json: "JSON 对象/数组（json）",
};

function environmentLabel(value: string): string {
  return ENVIRONMENT_LABELS[value] ?? value;
}

function statusLabel(value: SystemConfig["status"]): string {
  return STATUS_LABELS[value] ?? value;
}

function valueTypeLabel(value: string): string {
  return VALUE_TYPE_LABELS[value] ?? value;
}

function valueTypeHelp(value: string): string {
  switch (value) {
    case "bool":
      return "只能填写 true 或 false，适合功能开关。";
    case "int":
      return "只能填写整数，适合次数、天数、上限等配置。";
    case "decimal":
      return "可填写小数，适合倍率、比例、阈值等配置。";
    case "json":
      return '必须是合法 JSON，例如 {"balanced":"claude-sonnet-4-6"} 或 ["a","b"]。';
    case "default":
      return "由后端按默认类型解析；新配置不确定时优先使用 string 或 json。";
    default:
      return "按原始字符串保存，适合模型名、URL、提示文案等文本配置。";
  }
}

function buildEmptyForm(environment: string) {
  return {
    domain: "",
    config_key: "",
    environment,
    value_type: "string" as string,
    config_value: "",
    description: "",
  };
}

const FALLBACK_ENVIRONMENT = "prod";

const EMPTY_FORM = {
  domain: "",
  config_key: "",
  environment: FALLBACK_ENVIRONMENT as string,
  value_type: "string" as string,
  config_value: "",
  description: "",
};

export default function SystemConfigTab({ secret }: { secret: string }) {
  const [configs, setConfigs] = useState<SystemConfig[]>([]);
  const [defaultEnvironment, setDefaultEnvironment] = useState(FALLBACK_ENVIRONMENT);
  const [defaultsReady, setDefaultsReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [filterEnv, setFilterEnv] = useState("");
  const [filterDomain, setFilterDomain] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterQ, setFilterQ] = useState("");

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [saving, setSaving] = useState(false);
  const [formErr, setFormErr] = useState("");

  const headers = { "x-admin-secret": secret, "Content-Type": "application/json" };

  const fetchConfigs = useCallback(async () => {
    if (!defaultsReady) return;
    setLoading(true);
    setErr("");
    try {
      const params = new URLSearchParams();
      params.set("environment", filterEnv || "*");
      if (filterDomain) params.set("domain", filterDomain);
      if (filterStatus) params.set("status", filterStatus);
      if (filterQ) params.set("q", filterQ);
      const res = await fetch(`/api/admin/configs?${params}`, { headers });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error?.message ?? "请求失败");
      const runtimeDefaultEnvironment =
        typeof json.default_environment === "string"
          ? json.default_environment
          : FALLBACK_ENVIRONMENT;
      setDefaultEnvironment(runtimeDefaultEnvironment);
      setConfigs(json.configs ?? []);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "请求失败");
    } finally {
      setLoading(false);
    }
  }, [secret, defaultsReady, filterEnv, filterDomain, filterStatus, filterQ]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let cancelled = false;
    const initDefaults = async () => {
      try {
        const res = await fetch("/api/admin/configs", { headers });
        const json = await res.json();
        if (!res.ok) throw new Error(json.error?.message ?? "请求失败");
        const runtimeDefaultEnvironment =
          typeof json.default_environment === "string"
            ? json.default_environment
            : FALLBACK_ENVIRONMENT;
        if (cancelled) return;
        setDefaultEnvironment(runtimeDefaultEnvironment);
        setFilterEnv(runtimeDefaultEnvironment);
        setForm(buildEmptyForm(runtimeDefaultEnvironment));
        setConfigs(json.configs ?? []);
      } catch (e: unknown) {
        if (cancelled) return;
        setErr(e instanceof Error ? e.message : "请求失败");
        setDefaultEnvironment(FALLBACK_ENVIRONMENT);
        setFilterEnv(FALLBACK_ENVIRONMENT);
        setForm(buildEmptyForm(FALLBACK_ENVIRONMENT));
      } finally {
        if (!cancelled) setDefaultsReady(true);
      }
    };
    void initDefaults();
    return () => {
      cancelled = true;
    };
  }, [secret]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { void fetchConfigs(); }, [fetchConfigs]);

  function openCreate() {
    setEditingId(null);
    setForm(buildEmptyForm(defaultEnvironment));
    setFormErr("");
    setShowForm(true);
  }

  function openEdit(c: SystemConfig) {
    setEditingId(c.id);
    setForm({
      domain: c.domain,
      config_key: c.config_key,
      environment: c.environment,
      value_type: c.value_type,
      config_value: c.config_value,
      description: c.description,
    });
    setFormErr("");
    setShowForm(true);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setFormErr("");
    try {
      const body = JSON.stringify(form);
      const res = editingId
        ? await fetch(`/api/admin/configs/${editingId}`, { method: "PUT", headers, body })
        : await fetch("/api/admin/configs", { method: "POST", headers, body });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error?.message ?? "保存失败");
      setShowForm(false);
      void fetchConfigs();
    } catch (e: unknown) {
      setFormErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleEnable(c: SystemConfig) {
    const isProd = c.environment === "prod";
    if (isProd && !confirm(`确定启用 prod 环境配置？\n\n${c.domain} / ${c.config_key}\n值：${c.config_value}`)) return;
    try {
      const res = await fetch(`/api/admin/configs/${c.id}/enable`, { method: "POST", headers });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error?.message ?? "启用失败");
      void fetchConfigs();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "启用失败");
    }
  }

  async function handleDisable(c: SystemConfig) {
    if (!confirm(`确定停用配置？\n\n${c.domain} / ${c.config_key} (${c.environment})`)) return;
    try {
      const res = await fetch(`/api/admin/configs/${c.id}/disable`, { method: "POST", headers });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error?.message ?? "停用失败");
      void fetchConfigs();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "停用失败");
    }
  }

  return (
    <div className="space-y-4">
      <HostedRolloutPanel
        secret={secret}
        environment={filterEnv || defaultEnvironment}
        onSaved={() => { void fetchConfigs(); }}
      />
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="font-bold text-sm">系统配置</h2>
        <div className="flex gap-2 flex-wrap">
          <button type="button" onClick={() => void fetchConfigs()} disabled={loading}
            className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold disabled:opacity-50">
            刷新
          </button>
          <button type="button" onClick={openCreate}
            className="stardew-btn px-3 py-1.5 rounded-lg text-[10px] font-bold">
            + 新建配置
          </button>
        </div>
      </div>

      <div className="parchment rounded-xl p-4">
        <h3 className="text-sm font-bold mb-2">配置维护说明</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-[11px] text-stardew-brown/65 leading-relaxed">
          <div>
            <p className="font-bold text-stardew-brown mb-1">配置定位方式</p>
            <p>系统配置由“配置域（domain）+ 配置键（config_key）+ 环境（environment）”唯一定位。查代码或排查接口时，优先用这三个字段回溯。</p>
          </div>
          <div>
            <p className="font-bold text-stardew-brown mb-1">环境和启用状态</p>
            <p>dev/test/prod 代表不同运行环境。新建配置默认停用，需要人工启用后才会生效；启用 prod 配置前页面会再次确认。</p>
          </div>
          <div>
            <p className="font-bold text-stardew-brown mb-1">值类型和配置值</p>
            <p>值类型决定后端如何解析配置值。JSON、布尔值、数字类配置必须按对应格式填写，否则可能保存成功但运行时解析失败。</p>
          </div>
        </div>
      </div>

      {/* 筛选栏 */}
      <div className="parchment rounded-xl p-3 flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-stardew-brown/60">环境（environment）</label>
          <select value={filterEnv} onChange={e => setFilterEnv(e.target.value)}
            className="px-2 py-1 rounded border border-stardew-wood/30 bg-white/60 text-xs">
            <option value="">全部</option>
            {ENVIRONMENTS.map(e => <option key={e} value={e}>{environmentLabel(e)}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-stardew-brown/60">状态</label>
          <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)}
            className="px-2 py-1 rounded border border-stardew-wood/30 bg-white/60 text-xs">
            <option value="">全部</option>
            <option value="active">{STATUS_LABELS.active}</option>
            <option value="disabled">{STATUS_LABELS.disabled}</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-stardew-brown/60">配置域（domain）</label>
          <input value={filterDomain} onChange={e => setFilterDomain(e.target.value)}
            placeholder="精确匹配"
            className="px-2 py-1 rounded border border-stardew-wood/30 bg-white/60 text-xs w-32" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-stardew-brown/60">搜索</label>
          <input value={filterQ} onChange={e => setFilterQ(e.target.value)}
            placeholder="配置域 / 配置键 / 说明"
            className="px-2 py-1 rounded border border-stardew-wood/30 bg-white/60 text-xs w-40" />
        </div>
      </div>

      {err && <p className="text-red-600 text-xs bg-red-50 p-2 rounded">{err}</p>}

      {/* 配置列表 */}
      <div className="parchment rounded-xl overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="border-b border-stardew-wood/20 text-stardew-brown/60">
              <th className="text-left p-2 font-semibold">配置域 / 配置键</th>
              <th className="text-left p-2 font-semibold">环境</th>
              <th className="text-left p-2 font-semibold">值类型</th>
              <th className="text-left p-2 font-semibold">配置值</th>
              <th className="text-left p-2 font-semibold">状态</th>
              <th className="text-left p-2 font-semibold">更新时间</th>
              <th className="p-2" />
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={7} className="p-4 text-center text-stardew-brown/40">加载中…</td></tr>
            )}
            {!loading && configs.length === 0 && (
              <tr><td colSpan={7} className="p-4 text-center text-stardew-brown/40">暂无配置</td></tr>
            )}
            {configs.map(c => (
              <tr key={c.id} className="border-b border-stardew-wood/10 hover:bg-stardew-wood/5">
                <td className="p-2">
                  <div className="font-mono font-semibold">{c.domain}</div>
                  <div className="font-mono text-stardew-brown/60">{c.config_key}</div>
                  {c.description && <div className="text-stardew-brown/40 text-[10px] mt-0.5">{c.description}</div>}
                </td>
                <td className="p-2">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                    c.environment === "prod" ? "bg-red-100 text-red-700"
                    : c.environment === "test" ? "bg-yellow-100 text-yellow-700"
                    : "bg-blue-100 text-blue-700"
                  }`}>{environmentLabel(c.environment)}</span>
                </td>
                <td className="p-2 text-stardew-brown/60">{valueTypeLabel(c.value_type)}</td>
                <td className="p-2 font-mono max-w-[160px] truncate" title={c.config_value}>{c.config_value}</td>
                <td className="p-2">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                    c.status === "active" ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"
                  }`}>{statusLabel(c.status)}</span>
                </td>
                <td className="p-2 text-stardew-brown/40 whitespace-nowrap">
                  {c.updated_at.slice(0, 16).replace("T", " ")}
                </td>
                <td className="p-2">
                  <div className="flex gap-1 justify-end">
                    <button type="button" onClick={() => openEdit(c)}
                      className="px-2 py-1 rounded text-[10px] bg-stardew-wood/10 hover:bg-stardew-wood/20 font-bold">
                      编辑
                    </button>
                    {c.status === "disabled" ? (
                      <button type="button" onClick={() => void handleEnable(c)}
                        className="px-2 py-1 rounded text-[10px] bg-green-100 hover:bg-green-200 text-green-700 font-bold">
                        启用
                      </button>
                    ) : (
                      <button type="button" onClick={() => void handleDisable(c)}
                        className="px-2 py-1 rounded text-[10px] bg-gray-100 hover:bg-gray-200 text-gray-600 font-bold">
                        停用
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 创建/编辑表单 */}
      {showForm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="parchment rounded-2xl p-6 w-full max-w-2xl shadow-2xl max-h-[90vh] overflow-y-auto">
            <h3 className="font-bold text-sm mb-4">{editingId ? "编辑配置" : "新建配置"}</h3>
            <form onSubmit={e => void handleSave(e)} className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[10px] text-stardew-brown/60 block mb-1">配置域（domain）*</label>
                  <input required value={form.domain} onChange={e => setForm(f => ({ ...f, domain: e.target.value }))}
                    placeholder="agent_runtime"
                    className="w-full px-2 py-1.5 rounded border border-stardew-wood/30 bg-white/60 text-xs font-mono" />
                  <p className="text-[10px] text-stardew-brown/45 mt-1">用于按业务模块分组，例如 agent_runtime、ai_relay。</p>
                </div>
                <div>
                  <label className="text-[10px] text-stardew-brown/60 block mb-1">配置键（config_key）*</label>
                  <input required value={form.config_key} onChange={e => setForm(f => ({ ...f, config_key: e.target.value }))}
                    placeholder="default_model_tier"
                    className="w-full px-2 py-1.5 rounded border border-stardew-wood/30 bg-white/60 text-xs font-mono" />
                  <p className="text-[10px] text-stardew-brown/45 mt-1">同一配置域下的具体配置名，建议使用英文下划线命名。</p>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[10px] text-stardew-brown/60 block mb-1">环境（environment）*</label>
                  <select required value={form.environment} onChange={e => setForm(f => ({ ...f, environment: e.target.value }))}
                    className="w-full px-2 py-1.5 rounded border border-stardew-wood/30 bg-white/60 text-xs">
                    {ENVIRONMENTS.map(e => <option key={e} value={e}>{environmentLabel(e)}</option>)}
                  </select>
                  <p className="text-[10px] text-stardew-brown/45 mt-1">prod 为生产环境，启用前会二次确认。</p>
                </div>
                <div>
                  <label className="text-[10px] text-stardew-brown/60 block mb-1">值类型（value_type）*</label>
                  <select required value={form.value_type} onChange={e => setForm(f => ({ ...f, value_type: e.target.value }))}
                    className="w-full px-2 py-1.5 rounded border border-stardew-wood/30 bg-white/60 text-xs">
                    {VALUE_TYPES.map(t => <option key={t} value={t}>{valueTypeLabel(t)}</option>)}
                  </select>
                  <p className="text-[10px] text-stardew-brown/45 mt-1">{valueTypeHelp(form.value_type)}</p>
                </div>
              </div>
              <div>
                <label className="text-[10px] text-stardew-brown/60 block mb-1">配置值（config_value）*</label>
                <textarea required value={form.config_value} onChange={e => setForm(f => ({ ...f, config_value: e.target.value }))}
                  rows={form.value_type === "json" ? 4 : 2}
                  placeholder={form.value_type === "bool" ? "true / false" : form.value_type === "json" ? '{"key":"value"}' : ""}
                  className="w-full px-2 py-1.5 rounded border border-stardew-wood/30 bg-white/60 text-xs font-mono resize-y" />
                <p className="text-[10px] text-stardew-brown/45 mt-1">保存的是原始文本，后端会按值类型解析；JSON 建议先在编辑器里校验格式。</p>
              </div>
              <div>
                <label className="text-[10px] text-stardew-brown/60 block mb-1">维护说明（description）</label>
                <input value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                  placeholder="写清楚用途、影响范围、修改注意事项"
                  className="w-full px-2 py-1.5 rounded border border-stardew-wood/30 bg-white/60 text-xs" />
                <p className="text-[10px] text-stardew-brown/45 mt-1">建议说明该配置影响哪个功能、谁维护、修改后是否需要重启或刷新缓存。</p>
              </div>
              {!editingId && (
                <p className="text-[10px] text-stardew-brown/50">新建配置默认 disabled，创建后需手动启用。</p>
              )}
              {formErr && <p className="text-red-600 text-xs bg-red-50 p-2 rounded">{formErr}</p>}
              <div className="flex gap-2 justify-end pt-1">
                <button type="button" onClick={() => setShowForm(false)}
                  className="px-4 py-2 rounded-lg text-xs bg-stardew-wood/10 hover:bg-stardew-wood/20 font-bold">
                  取消
                </button>
                <button type="submit" disabled={saving}
                  className="stardew-btn px-4 py-2 rounded-lg text-xs font-bold disabled:opacity-50">
                  {saving ? "保存中…" : "保存"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
