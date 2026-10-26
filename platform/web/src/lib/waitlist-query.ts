/**
 * waitlist 查询的辅助函数：PostgREST .or() 与 PG ILIKE 的字符转义。
 * 抽到 lib 是为了能被 __tests__ 单测覆盖（安全关键，不允许回归）。
 */

/**
 * 转义 PostgREST `.or(...)` filter 中的特殊字符。
 * `,` 是条件分隔符；`(` `)` 用于嵌套表达式 — 都不能出现在 value 里。
 * 联系方式（email/wechat/qq）正常不含这些字符；防御性移除。
 */
export function escapeOrValue(v: string): string {
  return v.replace(/[(),]/g, "");
}

/**
 * 转义 PostgreSQL LIKE/ILIKE 通配符。
 * `%` 匹配任意字符序列、`_` 匹配单字符；不转义会让 ilike 退化成模糊匹配，
 * 攻击者输入 `%@%.com` 可枚举所有 approved 邮箱（违反 spec §5.4 的"精确匹配"）。
 * `\` 是 PG ILIKE 默认 ESCAPE 字符（无需手工 ESCAPE 子句）。
 */
export function escapeLikeWildcards(v: string): string {
  return v.replace(/[\\%_]/g, "\\$&");
}
