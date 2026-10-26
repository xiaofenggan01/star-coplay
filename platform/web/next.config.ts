import fs from "fs";
import path from "path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

const configDir = path.dirname(fileURLToPath(import.meta.url));

const MERGE_KEYS = new Set([
  "ADMIN_SECRET",
  "SUPABASE_SERVICE_ROLE_KEY",
  "NEXT_PUBLIC_SUPABASE_URL",
  "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
]);

function mergeFromSupabaseEnv() {
  const envPath = path.join(process.cwd(), "..", "supabase", ".env");
  if (!fs.existsSync(envPath)) return;
  const raw = fs.readFileSync(envPath, "utf8");
  for (const line of raw.split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const eq = t.indexOf("=");
    if (eq < 1) continue;
    const k = t.slice(0, eq).trim();
    if (!MERGE_KEYS.has(k)) continue;
    if ((process.env[k] ?? "").trim()) continue;
    let v = t.slice(eq + 1).trim();
    if (
      (v.startsWith('"') && v.endsWith('"')) ||
      (v.startsWith("'") && v.endsWith("'"))
    ) {
      v = v.slice(1, -1);
    }
    if (v) process.env[k] = v;
  }
}

mergeFromSupabaseEnv();

const nextConfig: NextConfig = {
  images: {
    unoptimized: true,
  },
  transpilePackages: ["html2canvas"],
};

if (process.env.NODE_ENV !== "production") {
  nextConfig.allowedDevOrigins = ["127.0.0.1", "localhost"];
}

export default nextConfig;
