export function getSiteUrl(fallback?: string): string {
  const envUrl = process.env.NEXT_PUBLIC_SITE_URL?.trim();
  const base = envUrl && envUrl.length > 0 ? envUrl : fallback ?? "https://star-coplay.luosea.cn";
  return base.replace(/\/$/, "");
}
