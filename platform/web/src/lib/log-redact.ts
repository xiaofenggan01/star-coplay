/**
 * 日志/上报场景下脱敏：把 URL 中的 token query 替换为 REDACTED。
 * 用于 SSE 端点（?token=xxx 不可避免地暴露在 access log / Referer / Sentry breadcrumbs）。
 *
 * 为什么不在中间件里全局做：Next.js 没有统一的 access log hook；
 * 这是个供调用方在主动 console / Sentry capture 时使用的工具函数。
 */

export function redactTokenInUrl(rawUrl: string): string {
  return rawUrl.replace(/([?&])token=[^&]+/g, "$1token=REDACTED");
}
