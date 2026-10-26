"use client";

import { useEffect, useMemo, useState } from "react";
import { createClient } from "@/lib/supabase-browser";
import { MENU_ITEMS } from "./menu-config";

export function useVisibleMenuItems() {
  const supabase = useMemo(() => createClient(), []);
  const [personaAllowed, setPersonaAllowed] = useState(false);
  const [llmAllowed, setLlmAllowed] = useState(false);
  const [channelsAllowed, setChannelsAllowed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function loadAccess() {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session?.access_token) return;
      const headers = { Authorization: `Bearer ${session.access_token}` };
      const [personaRes, llmRes, channelsRes] = await Promise.all([
        fetch("/api/me/persona", { headers }).catch(() => null),
        fetch("/api/me/ai-relay?access=1", { headers }).catch(() => null),
        fetch("/api/me/channels/access", { headers }).catch(() => null),
      ]);
      if (!cancelled) {
        setPersonaAllowed(personaRes?.ok === true);
        setLlmAllowed(llmRes?.ok === true);
        setChannelsAllowed(channelsRes?.ok === true);
      }
    }

    void loadAccess();
    return () => {
      cancelled = true;
    };
  }, [supabase]);

  return MENU_ITEMS.filter((item) => {
    if (item.key === "persona") return personaAllowed;
    if (item.key === "llm") return llmAllowed;
    if (item.key === "channels") return channelsAllowed;
    return true;
  });
}
