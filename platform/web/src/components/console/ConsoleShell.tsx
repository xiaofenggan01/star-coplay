"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";
import Sidebar from "./Sidebar";
import MobileTabBar from "./MobileTabBar";

interface ConsoleShellProps {
  children: React.ReactNode;
}

export default function ConsoleShell({ children }: ConsoleShellProps) {
  const router = useRouter();
  const [user, setUser] = useState<{ nickname: string; email: string } | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let active = true;
    let supabase: ReturnType<typeof createClient> | null = null;

    try {
      supabase = createClient();
    } catch {
      router.push("/login");
      return () => { active = false; };
    }

    supabase.auth
      .getSession()
      .then(({ data }) => {
        if (!active) return;
        const session = data.session;
        if (!session) {
          router.push("/login");
          return;
        }
        const u = session.user;
        const nickname =
          (u.user_metadata && typeof u.user_metadata.nickname === "string"
            ? u.user_metadata.nickname.trim()
            : "") || u.email || "用户";
        setUser({ nickname, email: u.email || "" });
        setReady(true);
      })
      .catch(() => {
        if (active) router.push("/login");
      });

    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!active) return;
      if (!session) {
        setUser(null);
        router.push("/login");
        return;
      }
      const u = session.user;
      const nickname =
        (u.user_metadata && typeof u.user_metadata.nickname === "string"
          ? u.user_metadata.nickname.trim()
          : "") || u.email || "用户";
      setUser({ nickname, email: u.email || "" });
    });

    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
  }, [router]);

  const handleSignOut = useCallback(async () => {
    try {
      const supabase = createClient();
      await supabase.auth.signOut();
    } catch {
      // ignore
    }
    setUser(null);
    router.push("/login");
    router.refresh();
  }, [router]);

  if (!ready) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#faf5eb]">
        <span className="text-sm text-[#8b7355] font-[family-name:var(--font-sans)]">加载中...</span>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex font-[family-name:var(--font-sans)]">
      <Sidebar user={user} onSignOut={handleSignOut} />
      <main className="flex-1 md:ml-60 bg-[#faf5eb] p-8 overflow-auto pb-20 md:pb-8">
        {children}
      </main>
      <MobileTabBar />
    </div>
  );
}
