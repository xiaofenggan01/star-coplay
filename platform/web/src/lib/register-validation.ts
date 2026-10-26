/** 去掉空白、常见分隔符（手机号输入） */
export function stripPhoneSeparators(raw: string): string {
  return raw.replace(/[\s\-().\u00A0]/g, "");
}

/**
 * 校验可选手机号：空为通过。
 * 通过：中国大陆 11 位（1[3-9]xxxxxxxxx），可选前缀 +86 / 86。
 * 或通过：E.164 风格 + 后跟 8–15 位数字。
 */
export function validatePhoneOptional(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;

  const compact = stripPhoneSeparators(trimmed);

  if (compact.startsWith("+")) {
    const afterPlus = compact.slice(1);
    if (!/^\d+$/.test(afterPlus)) {
      return "国际号码在 + 之后须为连续数字（不含字母或符号）";
    }
    if (afterPlus.length < 8 || afterPlus.length > 15) {
      return "国际号码须为 + 开头，后跟 8～15 位数字";
    }
    return null;
  }

  let digits = compact.replace(/\D/g, "");
  if (digits.startsWith("86") && digits.length === 13) {
    digits = digits.slice(2);
  }
  if (/^1[3-9]\d{9}$/.test(digits)) {
    return null;
  }
  return "大陆手机号为 11 位，以 13–19 开头；或使用 +国家码 的国际格式";
}

/**
 * 提交用规范化：大陆号存 11 位数字；国际号存 +XXXXXXXX。
 */
export function normalizePhoneForSubmit(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";

  const compact = stripPhoneSeparators(trimmed);
  if (compact.startsWith("+")) {
    const digits = compact.slice(1).replace(/\D/g, "");
    return digits ? `+${digits}` : "";
  }

  let digits = compact.replace(/\D/g, "");
  if (digits.startsWith("86") && digits.length === 13) {
    digits = digits.slice(2);
  }
  if (/^1[3-9]\d{9}$/.test(digits)) {
    return digits;
  }
  return trimmed;
}

const EMAIL_RE =
  /^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$/;

/** 校验可选邮箱：空为通过。 */
export function validateEmailOptional(raw: string): string | null {
  const t = raw.trim();
  if (!t) return null;
  if (t.length > 254) return "邮箱总长度不能超过 254 个字符";
  const at = t.lastIndexOf("@");
  if (at <= 0 || at === t.length - 1) return "邮箱格式不正确";
  const local = t.slice(0, at);
  if (local.length > 64) return "邮箱 @ 前的部分过长";
  if (!EMAIL_RE.test(t)) return "邮箱格式不正确（请检查域名与符号）";
  return null;
}

export function normalizeEmailForSubmit(raw: string): string {
  return raw.trim().toLowerCase();
}
