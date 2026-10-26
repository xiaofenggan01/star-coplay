"use client";

import {
  useState,
  useCallback,
  useEffect,
  useRef,
  useMemo,
  type ReactNode,
} from "react";
import CharacterRenderer from "./CharacterRenderer";
import HSBColorPicker from "./HSBColorPicker";
import type { AppearanceSubmit, HSBColor } from "@/lib/types/appearance";
import {
  DEFAULT_APPEARANCE,
  SKIN_PALETTE,
  APPEARANCE_CONSTRAINTS,
} from "@/lib/types/appearance";
import { SHIRTS_DATA, PANTS_DATA, HATS_DATA } from "@/lib/sdv/sprite-data";
import {
  getHairBounds,
  getShirtBounds,
  getPantsBounds,
  getHatBounds,
} from "@/lib/sdv/sprite-coords";
import { getCached, type SpriteKey } from "@/lib/sdv/sprite-loader";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface AppearanceEditorProps {
  initialAppearance?: AppearanceSubmit;
  onSave?: (appearance: AppearanceSubmit) => Promise<void>;
  saving?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Sprite thumbnail — crops a region from a sprite sheet             */
/*  Reuses sprite-loader cache to avoid duplicate network requests.   */
/* ------------------------------------------------------------------ */

const SDV_ASSETS = "/assets/sdv";

/** Map sprite sheet URL → SpriteKey for cache lookup */
const URL_TO_KEY: Record<string, SpriteKey> = {
  [`${SDV_ASSETS}/farmer_base.webp`]: "body_male",
  [`${SDV_ASSETS}/farmer_base_bald.webp`]: "body_male_bald",
  [`${SDV_ASSETS}/farmer_girl_base.webp`]: "body_female",
  [`${SDV_ASSETS}/farmer_girl_base_bald.webp`]: "body_female_bald",
  [`${SDV_ASSETS}/hairstyle.webp`]: "hairstyle",
  [`${SDV_ASSETS}/hairstyles2.webp`]: "hairstyles2",
  [`${SDV_ASSETS}/hats.webp`]: "hats",
  [`${SDV_ASSETS}/shirts.webp`]: "shirts",
  [`${SDV_ASSETS}/pants.webp`]: "pants",
};

interface SpriteCropProps {
  src: string;
  x: number;
  y: number;
  w: number;
  h: number;
  scale?: number;
  className?: string;
  bgColor?: string;
}

function SpriteCrop({
  src,
  x,
  y,
  w,
  h,
  scale = 3,
  className = "",
  bgColor,
}: SpriteCropProps) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;

    function draw(img: HTMLImageElement) {
      const ctx = canvas!.getContext("2d")!;
      ctx.imageSmoothingEnabled = false;
      ctx.clearRect(0, 0, canvas!.width, canvas!.height);
      if (bgColor) {
        ctx.fillStyle = bgColor;
        ctx.fillRect(0, 0, canvas!.width, canvas!.height);
      }
      ctx.drawImage(img, x, y, w, h, 0, 0, w * scale, h * scale);
    }

    // Try sprite-loader cache first (avoids duplicate download)
    const key = URL_TO_KEY[src];
    if (key) {
      const cached = getCached(key);
      if (cached) { draw(cached); return; }
    }

    // Fallback: load from network
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => draw(img);
    img.src = src;
  }, [src, x, y, w, h, scale, bgColor]);

  return (
    <canvas
      ref={ref}
      width={w * scale}
      height={h * scale}
      className={`pixel-art ${className}`}
      style={{ imageRendering: "pixelated" }}
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Section card wrapper                                               */
/* ------------------------------------------------------------------ */

function Section({
  icon,
  title,
  children,
}: {
  icon: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-xl border-2 border-stardew-wood/15 bg-white/40 p-3 space-y-2">
      <h3 className="text-[11px] font-bold text-stardew-brown flex items-center gap-1.5">
        {icon.startsWith("/") ? (
          <img src={icon} alt="" className="w-3.5 h-3.5 pixel-art opacity-70 shrink-0" />
        ) : (
          <span aria-hidden="true">{icon}</span>
        )}
        {title}
      </h3>
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Cycle selector — left/right arrows with optional thumbnail         */
/* ------------------------------------------------------------------ */

function CycleSelector({
  value,
  min,
  max,
  onChange,
  formatLabel,
  thumbnail,
  allowNone,
}: {
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
  formatLabel?: (v: number) => string;
  thumbnail?: ReactNode;
  allowNone?: boolean;
}) {
  const cycle = (delta: number) => {
    let next = value + delta;
    if (allowNone && next < -1) next = max;
    else if (next < min) next = allowNone ? -1 : max;
    else if (next > max) next = allowNone ? -1 : min;
    onChange(next);
  };

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => cycle(-1)}
        className="stardew-btn w-7 h-7 rounded-lg text-[11px] flex items-center justify-center shrink-0"
      >
        ◀
      </button>
      <div className="flex-1 flex items-center justify-center gap-2 min-h-[32px]">
        {thumbnail}
        <span className="text-[10px] text-stardew-brown/70 font-bold">
          {formatLabel
            ? formatLabel(value)
            : allowNone && value === -1
              ? "无"
              : String(value)}
        </span>
      </div>
      <button
        type="button"
        onClick={() => cycle(1)}
        className="stardew-btn w-7 h-7 rounded-lg text-[11px] flex items-center justify-center shrink-0"
      >
        ▶
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Thumbnail helpers — compute sprite crop for each category          */
/* ------------------------------------------------------------------ */

export function hairThumbnail(hairstyleIndex: number) {
  const hair = getHairBounds(hairstyleIndex, -1, 0);
  const key: SpriteKey = hair.sheet === "default" ? "hairstyle" : "hairstyles2";
  const file = key === "hairstyle" ? "hairstyle.webp" : "hairstyles2.webp";
  return (
    <SpriteCrop
      src={`${SDV_ASSETS}/${file}`}
      x={hair.rect.x}
      y={hair.rect.y}
      w={hair.rect.w}
      h={hair.rect.h}
      scale={2}
    />
  );
}

export function shirtThumbnail(shirtIndex: number, isMale: boolean) {
  const data = SHIRTS_DATA[shirtIndex];
  if (!data) return null;
  const bounds = getShirtBounds(shirtIndex, 0, isMale);
  return (
    <SpriteCrop
      src={`${SDV_ASSETS}/shirts.webp`}
      x={bounds.uncolored.x}
      y={bounds.uncolored.y}
      w={bounds.uncolored.w}
      h={bounds.uncolored.h}
      scale={4}
    />
  );
}

export function pantsThumbnail(pantsIndex: number, isMale: boolean) {
  const data = PANTS_DATA[pantsIndex];
  if (!data) return null;
  const bounds = getPantsBounds(pantsIndex, 0, isMale);
  return (
    <SpriteCrop
      src={`${SDV_ASSETS}/pants.webp`}
      x={bounds.rect.x}
      y={bounds.rect.y}
      w={bounds.rect.w}
      h={bounds.rect.h}
      scale={2}
    />
  );
}

export function hatThumbnail(hatIndex: number) {
  const bounds = getHatBounds(hatIndex, 0);
  if (!bounds) return null;
  return (
    <SpriteCrop
      src={`${SDV_ASSETS}/hats.webp`}
      x={bounds.rect.x}
      y={bounds.rect.y}
      w={bounds.rect.w}
      h={bounds.rect.h}
      scale={3}
      bgColor="#fdf6e3"
      className="rounded"
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Randomize                                                          */
/* ------------------------------------------------------------------ */

function randomInt(min: number, max: number) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function randomHSB(): HSBColor {
  return { h: randomInt(0, 100), s: randomInt(0, 100), b: randomInt(20, 100) };
}

function randomAppearance(): AppearanceSubmit {
  const gender = Math.random() < 0.5 ? "Male" : "Female";
  return {
    gender: gender as "Male" | "Female",
    skin: randomInt(0, 23),
    hairstyle: randomInt(0, APPEARANCE_CONSTRAINTS.HAIRSTYLE_MAX),
    hairColor: randomHSB(),
    eyeColor: randomHSB(),
    accessory: randomInt(-1, APPEARANCE_CONSTRAINTS.ACCESSORY_MAX),
    shirtIndex: randomInt(0, APPEARANCE_CONSTRAINTS.SHIRT_MAX),
    pantsIndex: randomInt(0, APPEARANCE_CONSTRAINTS.PANTS_MAX),
    pantsColor: randomHSB(),
    hatIndex: Math.random() < 0.7 ? null : randomInt(0, HATS_DATA.length - 1),
    bootsIndex: null,
    facingDirection: 2,
  };
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export default function AppearanceEditor({
  initialAppearance,
  onSave,
  saving = false,
}: AppearanceEditorProps) {
  const [appearance, setAppearance] = useState<AppearanceSubmit>(
    initialAppearance ?? DEFAULT_APPEARANCE,
  );

  const update = useCallback(
    <K extends keyof AppearanceSubmit>(key: K, value: AppearanceSubmit[K]) => {
      setAppearance((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const updateColor = useCallback(
    (key: "hairColor" | "eyeColor" | "pantsColor", color: HSBColor) => {
      setAppearance((prev) => ({ ...prev, [key]: color }));
    },
    [],
  );

  const handleRandomize = useCallback(() => {
    setAppearance(randomAppearance());
  }, []);

  const handleSave = useCallback(() => {
    onSave?.(appearance);
  }, [appearance, onSave]);

  const isMale = appearance.gender === "Male";

  // Time-based background
  const timeBg = useMemo(() => {
    const h = new Date().getHours();
    if (h >= 6 && h < 12) return "/assets/sdv/backgrounds/farm-day.webp";
    if (h >= 12 && h < 17) return "/assets/sdv/backgrounds/multiplayer-1.webp";
    if (h >= 17 && h < 20) return "/assets/sdv/backgrounds/farm-evening.webp";
    return "/assets/sdv/backgrounds/night-market.webp";
  }, []);

  return (
    <div className="flex flex-col md:flex-row gap-6">
      {/* ── Left: Preview Panel ── */}
      <div className="shrink-0 flex flex-col gap-3 md:sticky md:top-24">
        {/* Character preview with scene background */}
        <div className="rounded-2xl border-4 border-stardew-wood-dark shadow-lg overflow-hidden"
          style={{ boxShadow: "inset 0 2px 4px rgba(255,255,255,0.2), 0 4px 8px rgba(0,0,0,0.3)" }}
        >
          {/* Scene background behind character */}
          <div className="relative" style={{ width: 128, height: 256 }}>
            <img
              src={timeBg}
              alt=""
              className="absolute inset-0 w-full h-full"
              style={{ objectFit: "cover", objectPosition: "center bottom" }}
            />
            {/* Character on top */}
            <div className="absolute inset-0 flex items-center justify-center">
              <CharacterRenderer appearance={appearance} scale={8} />
            </div>
          </div>

          {/* Direction toggle */}
          <div className="flex gap-1.5">
            {(
              [
                { label: "↑", dir: 0 },
                { label: "→", dir: 1 },
                { label: "↓", dir: 2 },
                { label: "←", dir: 3 },
              ] as const
            ).map(({ label, dir }) => (
              <button
                key={dir}
                type="button"
                onClick={() => update("facingDirection", dir)}
                className={`w-7 h-7 rounded-lg text-[11px] font-bold ${
                  appearance.facingDirection === dir
                    ? "stardew-btn"
                    : "border-2 border-stardew-wood/20 bg-stardew-cream/80 text-stardew-brown/60"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Gender — independent card */}
        <div className="rounded-xl border-2 border-stardew-wood/15 bg-white/40 p-3">
          <p className="text-[10px] text-stardew-brown/55 font-bold mb-2 flex items-center gap-1.5">
            <img src="/assets/icons/The_Player_Icon.png" alt="" className="w-3.5 h-3.5 pixel-art opacity-70" />
            性别</p>
          <div className="flex gap-2">
            {(["Male", "Female"] as const).map((g) => (
              <button
                key={g}
                type="button"
                onClick={() => update("gender", g)}
                className={`flex-1 py-1.5 rounded-lg text-[11px] font-bold ${
                  appearance.gender === g
                    ? "stardew-btn"
                    : "border-2 border-stardew-wood/20 bg-stardew-cream/80 text-stardew-brown/60"
                }`}
              >
                {g === "Male" ? "♂ 男孩" : "♀ 女孩"}
              </button>
            ))}
          </div>
        </div>

        {/* Accessory — independent card */}
        <div className="rounded-xl border-2 border-stardew-wood/15 bg-white/40 p-3">
          <p className="text-[10px] text-stardew-brown/55 font-bold mb-2 flex items-center gap-1.5">
            <img src="/assets/icons/Achievements_Icon.png" alt="" className="w-3.5 h-3.5 pixel-art opacity-70" />
            饰品</p>
          <CycleSelector
            value={appearance.accessory}
            min={-1}
            max={APPEARANCE_CONSTRAINTS.ACCESSORY_MAX}
            onChange={(v) => update("accessory", v)}
            formatLabel={(v) => (v === -1 ? "无" : `#${v + 1}`)}
            allowNone
          />
        </div>

        {/* Randomize */}
        <button
          type="button"
          onClick={handleRandomize}
          className="stardew-btn px-4 py-2 rounded-xl text-[11px] font-bold flex items-center gap-1.5"
        >
          🎲 随机
        </button>
      </div>

      {/* ── Right: Controls ── */}
      <div className="flex-1 min-w-0 space-y-3">
        {/* Skin — full width */}
        <Section icon="/assets/icons/Health.png" title="肤色">
          <div className="grid grid-cols-8 gap-1.5">
            {SKIN_PALETTE.map((hex, i) => (
              <button
                key={i}
                type="button"
                onClick={() => update("skin", i)}
                className={`w-6 h-6 rounded-full border-2 transition-all ${
                  appearance.skin === i
                    ? "border-stardew-gold ring-2 ring-stardew-gold/30 scale-110"
                    : "border-stardew-wood/20 hover:border-stardew-wood/40"
                }`}
                style={{ backgroundColor: hex }}
                title={`肤色 ${i + 1}`}
              />
            ))}
          </div>
        </Section>

        {/* Hairstyle + Shirt */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Section icon="/assets/icons/Forester.png" title={`发型 (${appearance.hairstyle + 1}/${APPEARANCE_CONSTRAINTS.HAIRSTYLE_MAX + 1})`}>
            <CycleSelector
              value={appearance.hairstyle}
              min={0}
              max={APPEARANCE_CONSTRAINTS.HAIRSTYLE_MAX}
              onChange={(v) => update("hairstyle", v)}
              formatLabel={() => `#${appearance.hairstyle + 1}`}
              thumbnail={hairThumbnail(appearance.hairstyle)}
            />
          </Section>

          <Section icon="/assets/icons/Inventory_Icon.png" title={`上衣 (${appearance.shirtIndex + 1}/${APPEARANCE_CONSTRAINTS.SHIRT_MAX + 1})`}>
            <CycleSelector
              value={appearance.shirtIndex}
              min={0}
              max={APPEARANCE_CONSTRAINTS.SHIRT_MAX}
              onChange={(v) => update("shirtIndex", v)}
              formatLabel={() => `#${appearance.shirtIndex + 1}`}
              thumbnail={shirtThumbnail(appearance.shirtIndex, isMale)}
            />
          </Section>
        </div>

        {/* Hair color + Eye color */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Section icon="/assets/icons/Weather_Icon.png" title="发色">
            <HSBColorPicker
              value={appearance.hairColor}
              onChange={(c) => updateColor("hairColor", c)}
            />
          </Section>

          <Section icon="/assets/icons/Secret_Note_Icon.png" title="瞳色">
            <HSBColorPicker
              value={appearance.eyeColor}
              onChange={(c) => updateColor("eyeColor", c)}
            />
          </Section>
        </div>

        {/* Pants + Hat */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Section icon="/assets/icons/Quests_Icon.png" title="裤子">
            <CycleSelector
              value={appearance.pantsIndex}
              min={0}
              max={APPEARANCE_CONSTRAINTS.PANTS_MAX}
              onChange={(v) => update("pantsIndex", v)}
              formatLabel={(v) => `裤子 ${v + 1}`}
              thumbnail={pantsThumbnail(appearance.pantsIndex, isMale)}
            />
          </Section>

          <Section
            icon="/assets/icons/All_Seasons_Icon.png"
            title={`帽子 (${
              appearance.hatIndex == null
                ? "无"
                : `${appearance.hatIndex + 1}/${HATS_DATA.length}`
            })`}
          >
            <CycleSelector
              value={appearance.hatIndex ?? -1}
              min={-1}
              max={HATS_DATA.length - 1}
              onChange={(v) => update("hatIndex", v < 0 ? null : v)}
              formatLabel={(v) => (v < 0 ? "无" : `帽子 ${v + 1}`)}
              thumbnail={appearance.hatIndex != null ? hatThumbnail(appearance.hatIndex) : null}
              allowNone
            />
          </Section>
        </div>

        {/* Pants color — full width */}
        <Section icon="/assets/icons/Cooking_Icon.png" title="裤色">
          <HSBColorPicker
            value={appearance.pantsColor}
            onChange={(c) => updateColor("pantsColor", c)}
          />
        </Section>

        {/* Save */}
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="stardew-btn w-full py-3 rounded-xl text-[13px] font-bold disabled:opacity-50"
        >
          {saving ? "保存中..." : "💾 保存外观"}
        </button>
      </div>
    </div>
  );
}
