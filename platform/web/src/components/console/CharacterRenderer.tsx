"use client";

import { useRef, useEffect, useState } from "react";
import type { AppearanceSubmit } from "@/lib/types/appearance";
import { DEFAULT_APPEARANCE } from "@/lib/types/appearance";
import { renderFarmer } from "@/lib/sdv/renderer";
import { loadAllSprites, getCached, isLoaded } from "@/lib/sdv/sprite-loader";
import type { SpriteKey } from "@/lib/sdv/sprite-loader";

const ALL_KEYS: SpriteKey[] = [
  "body_male", "body_male_bald", "body_female", "body_female_bald",
  "hairstyle", "hairstyles2", "hats", "shirts", "pants",
];

interface CharacterRendererProps {
  appearance?: AppearanceSubmit;
  scale?: number;
  className?: string;
}

export default function CharacterRenderer({
  appearance = DEFAULT_APPEARANCE,
  scale = 8,
  className = "",
}: CharacterRendererProps) {
  const displayRef = useRef<HTMLCanvasElement>(null);
  const [loading, setLoading] = useState(() => !isLoaded("body_male"));

  useEffect(() => {
    let cancelled = false;
    loadAllSprites().then(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (loading) return;

    const sprites = new Map<SpriteKey, HTMLImageElement>();
    for (const k of ALL_KEYS) {
      const img = getCached(k);
      if (img) sprites.set(k, img);
    }

    const result = renderFarmer(appearance, sprites);
    const display = displayRef.current;
    if (!display) return;

    const ctx = display.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, display.width, display.height);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(result, 0, 0);
  }, [appearance, loading]);

  const W = 16;
  const H = 32;

  if (loading) {
    return (
      <div
        className={`overflow-hidden flex items-center justify-center ${className}`}
        style={{ width: W * scale, height: H * scale }}
      >
        <span className="text-[10px] text-[#8b7355]">...</span>
      </div>
    );
  }

  return (
    <canvas
      ref={displayRef}
      width={W}
      height={H}
      className={`pixel-art ${className}`}
      style={{
        width: W * scale,
        height: H * scale,
        imageRendering: "pixelated",
        display: "block",
        maxWidth: "100%",
        maxHeight: "100%",
      }}
    />
  );
}
