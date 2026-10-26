/**
 * 浏览器端 Supabase client（@supabase/ssr）。
 *
 * 用法：在 'use client' 组件中 `const supabase = createClient()`，
 * 用于触发 supabase.auth.signInWithPassword / signOut / onAuthStateChange 等。
 *
 * 注意：本文件**不要**写 'use client' —— 由调用方组件标注。
 */
import { createBrowserClient } from "@supabase/ssr";

export function createClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !anonKey) {
    throw new Error(
      "Missing NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
    );
  }
  return createBrowserClient(url, anonKey);
}
