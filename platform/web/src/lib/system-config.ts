/**
 * 统一业务配置系统 — Web/Vercel 核心 helper
 *
 * 职责：
 *   - 校验 domain / config_key / environment / value_type / config_value
 *   - 按 value_type 解析 parsed_value
 *   - 无缓存读取（Web 侧不做进程内 TTL 缓存）
 *   - 统一错误码
 *
 * 不包含：TTL 缓存（由 backend / Agent Runtime 各自实现）
 */

// ─── 错误码 ────────────────────────────────────────────────────────────────

export type ConfigErrorCode =
  | "CONFIG_NOT_FOUND"
  | "CONFIG_DISABLED"
  | "CONFIG_ALREADY_EXISTS"
  | "CONFIG_INVALID_ENVIRONMENT"
  | "CONFIG_INVALID_NAME"
  | "CONFIG_TYPE_MISMATCH"
  | "CONFIG_PARSE_ERROR"
  | "CONFIG_UNAUTHORIZED"
  | "CONFIG_SERVICE_ERROR";

export class ConfigError extends Error {
  constructor(
    public readonly code: ConfigErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "ConfigError";
  }
}

// ─── 枚举 ──────────────────────────────────────────────────────────────────

export type ValueTypeName =
  | "default"
  | "bool"
  | "int"
  | "decimal"
  | "string"
  | "json";

export type EnvironmentName = "dev" | "test" | "prod";

export const CONFIG_ENVIRONMENTS = ["dev", "test", "prod"] as const;
export const DEFAULT_CONFIG_ENVIRONMENT: EnvironmentName = "prod";

const VALUE_TYPE_NAME_TO_CODE: Record<ValueTypeName, number> = {
  default: 0,
  bool: 1,
  int: 2,
  decimal: 3,
  string: 4,
  json: 5,
};

const VALUE_TYPE_CODE_TO_NAME: Record<number, ValueTypeName> = {
  0: "default",
  1: "bool",
  2: "int",
  3: "decimal",
  4: "string",
  5: "json",
};

const STATUS_CODE_TO_NAME: Record<number, "active" | "disabled"> = {
  0: "disabled",
  1: "active",
};

export function normalizeSystemConfigEnvironment(
  raw: unknown,
  fallback: EnvironmentName = DEFAULT_CONFIG_ENVIRONMENT,
): EnvironmentName {
  const value = typeof raw === "string" ? raw.trim() : "";
  const resolved = (value || fallback) as string;
  if (
    resolved !== CONFIG_ENVIRONMENTS[0] &&
    resolved !== CONFIG_ENVIRONMENTS[1] &&
    resolved !== CONFIG_ENVIRONMENTS[2]
  ) {
    throw new ConfigError(
      "CONFIG_INVALID_ENVIRONMENT",
      `environment must be dev/test/prod, got: ${String(resolved)}`,
    );
  }
  return resolved;
}

export function readStarcoplayConfigEnvironment(
  raw: unknown = process.env.STARCOPLAY_CONFIG_ENV,
): EnvironmentName {
  return normalizeSystemConfigEnvironment(raw, DEFAULT_CONFIG_ENVIRONMENT);
}

// ─── 数据库原始行类型 ───────────────────────────────────────────────────────

export interface RawSystemConfig {
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

// ─── API 响应类型 ──────────────────────────────────────────────────────────

export interface SystemConfigResponse {
  id: string;
  domain: string;
  config_key: string;
  environment: string;
  status: "active" | "disabled";
  status_code: number;
  value_type: ValueTypeName;
  value_type_code: number;
  config_value: string;
  parsed_value: unknown;
  description: string;
  created_at: string;
  updated_at: string;
}

// ─── 写入校验 payload ──────────────────────────────────────────────────────

export interface SystemConfigWritePayload {
  domain: string;
  config_key: string;
  environment: string;
  status?: string | number;
  value_type: string | number;
  config_value: unknown;
  description?: string;
}

export interface SystemConfigNormalized {
  domain: string;
  config_key: string;
  environment: string;
  status: number;
  value_type: number;
  config_value: string;
  description: string;
}

type ValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: ConfigError };

// ─── 命名校验 ──────────────────────────────────────────────────────────────

const NAME_RE = /^[a-z][a-z0-9_-]{1,63}$/;

function validateName(value: unknown, field: string): string | ConfigError {
  if (typeof value !== "string" || !NAME_RE.test(value)) {
    return new ConfigError(
      "CONFIG_INVALID_NAME",
      `${field} must match ^[a-z][a-z0-9_-]{1,63}$, got: ${String(value)}`,
    );
  }
  return value;
}

// ─── value_type 解析 ───────────────────────────────────────────────────────

function resolveValueTypeCode(raw: unknown): number | ConfigError {
  if (typeof raw === "number" && raw in VALUE_TYPE_CODE_TO_NAME) return raw;
  if (typeof raw === "string" && raw in VALUE_TYPE_NAME_TO_CODE)
    return VALUE_TYPE_NAME_TO_CODE[raw as ValueTypeName];
  return new ConfigError(
    "CONFIG_TYPE_MISMATCH",
    `unsupported value_type: ${String(raw)}`,
  );
}

// ─── 写入值校验 ────────────────────────────────────────────────────────────

function validateConfigValue(
  valueTypeCode: number,
  raw: unknown,
): string | ConfigError {
  const value = String(raw ?? "");
  switch (valueTypeCode) {
    case 0: // default
    case 4: // string
      return value;
    case 1: // bool
      if (value === "true" || value === "false") return value;
      return new ConfigError(
        "CONFIG_TYPE_MISMATCH",
        `bool value must be "true" or "false", got: ${value}`,
      );
    case 2: // int
      if (/^-?\d+$/.test(value)) return value;
      return new ConfigError(
        "CONFIG_TYPE_MISMATCH",
        `int value must match ^-?\\d+$, got: ${value}`,
      );
    case 3: // decimal
      if (/^-?\d+(\.\d+)?$/.test(value)) return value;
      return new ConfigError(
        "CONFIG_TYPE_MISMATCH",
        `decimal value must match ^-?\\d+(\\.\\d+)?$, got: ${value}`,
      );
    case 5: // json
      try {
        JSON.parse(value);
        return value;
      } catch {
        return new ConfigError(
          "CONFIG_TYPE_MISMATCH",
          `json value is not valid JSON: ${value}`,
        );
      }
    default:
      return new ConfigError(
        "CONFIG_TYPE_MISMATCH",
        `unknown value_type code: ${valueTypeCode}`,
      );
  }
}

// ─── 公开 API: validateSystemConfigPayload ─────────────────────────────────

export function validateSystemConfigPayload(
  payload: unknown,
): ValidationResult<SystemConfigNormalized> {
  if (typeof payload !== "object" || payload === null) {
    return {
      ok: false,
      error: new ConfigError("CONFIG_SERVICE_ERROR", "payload must be object"),
    };
  }
  const p = payload as Record<string, unknown>;

  const domainResult = validateName(p.domain, "domain");
  if (domainResult instanceof ConfigError)
    return { ok: false, error: domainResult };

  const keyResult = validateName(p.config_key, "config_key");
  if (keyResult instanceof ConfigError) return { ok: false, error: keyResult };

  let env: EnvironmentName;
  try {
    env = normalizeSystemConfigEnvironment(p.environment);
  } catch (error) {
    return { ok: false, error: error as ConfigError };
  }

  const vtCode = resolveValueTypeCode(p.value_type);
  if (vtCode instanceof ConfigError) return { ok: false, error: vtCode };

  const cvResult = validateConfigValue(vtCode, p.config_value);
  if (cvResult instanceof ConfigError) return { ok: false, error: cvResult };

  // status: 默认 disabled(0)
  let statusCode = 0;
  if (p.status !== undefined) {
    if (p.status === "active" || p.status === 1) statusCode = 1;
    else if (p.status === "disabled" || p.status === 0) statusCode = 0;
  }

  return {
    ok: true,
    value: {
      domain: domainResult,
      config_key: keyResult,
      environment: env,
      status: statusCode,
      value_type: vtCode,
      config_value: cvResult,
      description: typeof p.description === "string" ? p.description : "",
    },
  };
}

// ─── 公开 API: parseSystemConfigValue ─────────────────────────────────────

export function parseSystemConfigValue(
  valueType: number | ValueTypeName,
  configValue: string,
): unknown {
  const code =
    typeof valueType === "number"
      ? valueType
      : VALUE_TYPE_NAME_TO_CODE[valueType];

  switch (code) {
    case 0: // default
    case 4: // string
      return configValue;
    case 1: // bool
      if (configValue === "true") return true;
      if (configValue === "false") return false;
      throw new ConfigError(
        "CONFIG_PARSE_ERROR",
        `cannot parse bool from: ${configValue}`,
      );
    case 2: // int
      if (!/^-?\d+$/.test(configValue))
        throw new ConfigError(
          "CONFIG_PARSE_ERROR",
          `cannot parse int from: ${configValue}`,
        );
      return parseInt(configValue, 10);
    case 3: // decimal
      if (!/^-?\d+(\.\d+)?$/.test(configValue))
        throw new ConfigError(
          "CONFIG_PARSE_ERROR",
          `cannot parse decimal from: ${configValue}`,
        );
      return parseFloat(configValue);
    case 5: // json
      try {
        return JSON.parse(configValue);
      } catch {
        throw new ConfigError(
          "CONFIG_PARSE_ERROR",
          `cannot parse json from: ${configValue}`,
        );
      }
    default:
      throw new ConfigError(
        "CONFIG_PARSE_ERROR",
        `unknown value_type code: ${code}`,
      );
  }
}

// ─── Fetcher 类型 ──────────────────────────────────────────────────────────

export type SystemConfigFetcher = (
  domain: string,
  configKey: string,
  environment: string,
) => Promise<RawSystemConfig>;

// ─── 公开 API: readSystemConfig ────────────────────────────────────────────

export async function readSystemConfig(opts: {
  fetcher: SystemConfigFetcher;
  domain: string;
  configKey: string;
  environment: string;
}): Promise<SystemConfigResponse> {
  const { fetcher, domain, configKey, environment } = opts;

  // 每次都调用 fetcher，Web 侧不做 TTL 缓存
  const row = await fetcher(domain, configKey, environment);

  if (row.status === 0) {
    throw new ConfigError("CONFIG_DISABLED", `config is disabled`);
  }

  const parsed = parseSystemConfigValue(row.value_type, row.config_value);
  const vtName = VALUE_TYPE_CODE_TO_NAME[row.value_type] ?? "default";
  const statusName = STATUS_CODE_TO_NAME[row.status] ?? "disabled";

  return {
    id: row.id,
    domain: row.domain,
    config_key: row.config_key,
    environment: row.environment,
    status: statusName,
    status_code: row.status,
    value_type: vtName,
    value_type_code: row.value_type,
    config_value: row.config_value,
    parsed_value: parsed,
    description: row.description,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

// ─── 公开 API: readSystemConfigTyped ──────────────────────────────────────

export async function readSystemConfigTyped(opts: {
  fetcher: SystemConfigFetcher;
  domain: string;
  configKey: string;
  environment: string;
  expectedType: ValueTypeName;
}): Promise<SystemConfigResponse> {
  const result = await readSystemConfig(opts);
  if (result.value_type !== opts.expectedType) {
    throw new ConfigError(
      "CONFIG_TYPE_MISMATCH",
      `expected value_type ${opts.expectedType}, got ${result.value_type}`,
    );
  }
  return result;
}

// ─── Supabase fetcher 工厂（供 API route 使用）────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function makeSupabaseFetcher(supabase: any): SystemConfigFetcher {
  return async (domain, configKey, environment) => {
    const { data, error } = await supabase
      .from("system_configs")
      .select("*")
      .eq("domain", domain)
      .eq("config_key", configKey)
      .eq("environment", environment);

    if (error) {
      throw new ConfigError("CONFIG_SERVICE_ERROR", error.message);
    }
    if (!data || data.length === 0) {
      throw new ConfigError(
        "CONFIG_NOT_FOUND",
        `config not found: ${domain}/${configKey}/${environment}`,
      );
    }
    return data[0];
  };
}
