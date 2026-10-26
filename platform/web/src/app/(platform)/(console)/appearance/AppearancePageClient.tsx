"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";
import CharacterRenderer from "@/components/console/CharacterRenderer";
import type { AppearanceConfig, AppearanceSubmit, HSBColor } from "@/lib/types/appearance";
import { DEFAULT_APPEARANCE, hsbToHex } from "@/lib/types/appearance";

export default function AppearancePageClient() {
  const router = useRouter();
  const supabase = createClient();

  const [loading, setLoading] = useState(true);
  const [appearance, setAppearance] = useState<AppearanceSubmit | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    void fetchAppearance();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function getToken(): Promise<string | null> {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session?.access_token) {
      router.push("/");
      return null;
    }
    return session.access_token;
  }

  async function fetchAppearance() {
    setLoading(true);
    try {
      const token = await getToken();
      if (!token) return;

      const res = await fetch("/api/me/appearance", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return;

      const data = (await res.json()) as {
        appearance: AppearanceConfig | null;
      };
      if (data.appearance) {
        const { updated_at, ...submit } = data.appearance;
        setAppearance(submit);
        setUpdatedAt(updated_at ?? null);
      }
    } finally {
      setLoading(false);
    }
  }

  const displayAppearance = appearance ?? DEFAULT_APPEARANCE;

  if (loading) {
    return (
      <div
        className="min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] bg-stardew-cream px-4 py-10"
        style={{ fontFamily: "var(--font-sans)" }}
      >
        <div className="max-w-4xl mx-auto parchment rounded-2xl p-6 md:p-8 text-center">
          <p className="text-sm text-stardew-brown">Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <div
      className="min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] bg-stardew-cream px-4 py-6 md:py-8"
      style={{ fontFamily: "var(--font-sans)" }}
    >
      <div className="max-w-4xl mx-auto">
        <main className="parchment rounded-2xl p-5 md:p-8 space-y-5">
          <header className="flex items-center gap-3">
            <button
              onClick={() => router.push("/me")}
              className="shrink-0 w-8 h-8 rounded-lg border-2 border-stardew-wood/20 bg-stardew-cream/80 text-stardew-brown/60 text-sm font-bold hover:bg-stardew-cream transition-colors flex items-center justify-center"
            >
              &lt;
            </button>
            <div>
              <h1
                className="text-base md:text-lg text-stardew-brown font-bold flex items-center gap-1.5"
                style={{ textShadow: "1px 1px 0 #e8dcc8" }}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src="/assets/Stardrop.png"
                  alt=""
                  className="w-5 h-5 pixel-art"
                  style={{ imageRendering: "pixelated" }}
                />
                Companion Appearance
              </h1>
              <p className="text-[11px] text-stardew-brown/60 mt-0.5">
                Synced from the Stardew Valley Mod when the Agent link is established. This view is read-only.
              </p>
            </div>
          </header>

          <section className="grid gap-5 md:grid-cols-[180px_1fr]">
            <div className="wood-panel rounded-xl p-4 flex items-center justify-center">
              <CharacterRenderer appearance={displayAppearance} scale={8} />
            </div>

            <div className="rounded-xl border-2 border-stardew-wood/20 bg-stardew-cream/50 p-4">
              <div className="grid grid-cols-2 gap-x-5 gap-y-3 text-[12px]">
                <AppearanceItem label="Gender" value={displayAppearance.gender} />
                <AppearanceItem label="Skin" value={`#${displayAppearance.skin + 1}`} />
                <AppearanceItem label="Hair" value={`#${displayAppearance.hairstyle + 1}`} />
                <AppearanceItem label="Shirt" value={formatAppearanceIndex(displayAppearance.shirtIndex)} />
                <AppearanceItem label="Pants" value={formatAppearanceIndex(displayAppearance.pantsIndex)} />
                <AppearanceItem
                  label="Hat"
                  value={formatOptionalAppearanceIndex(displayAppearance.hatIndex)}
                />
                <AppearanceItem
                  label="Boots"
                  value={formatOptionalAppearanceIndex(displayAppearance.bootsIndex)}
                />
                <AppearanceItem label="Facing" value={formatFacing(displayAppearance.facingDirection)} />
                <AppearanceItem label="Hair color" value={<ColorValue color={displayAppearance.hairColor} />} />
                <AppearanceItem label="Eye color" value={<ColorValue color={displayAppearance.eyeColor} />} />
                <AppearanceItem label="Pants color" value={<ColorValue color={displayAppearance.pantsColor} />} />
                <AppearanceItem
                  label="Synced at"
                  value={updatedAt ? new Date(updatedAt).toLocaleString("zh-CN") : "Not synced yet"}
                />
              </div>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}

function formatFacing(direction: AppearanceSubmit["facingDirection"]) {
  return ["Up", "Right", "Down", "Left"][direction] ?? "Down";
}

function formatAppearanceIndex(index: number) {
  return index < 0 ? "None" : `#${index + 1}`;
}

function formatOptionalAppearanceIndex(index: number | null) {
  return index == null || index < 0 ? "None" : `#${index + 1}`;
}

function AppearanceItem({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] font-bold text-stardew-brown/50">{label}</p>
      <div className="mt-0.5 text-[13px] font-bold text-stardew-brown truncate">{value}</div>
    </div>
  );
}

function ColorValue({ color }: { color: HSBColor }) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <span
        className="inline-block h-4 w-4 shrink-0 rounded border-2 border-stardew-wood/30"
        style={{ backgroundColor: hsbToHex(color) }}
      />
      <span className="truncate">{color.h}/{color.s}/{color.b}</span>
    </span>
  );
}
