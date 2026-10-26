/**
 * SDV sprite data — typed re-exports of the JSON data files
 * ported from lybell-art/stardew-dressup/src/data/
 */

/* ── Skin colors (24 tones × 3 shading levels) ── */

export interface SkinTone {
  light: number; // packed RGB: (R<<16) | (G<<8) | B
  mid: number;
  dark: number;
}

import skinData from "./skinData.json";
export const SKIN_COLORS: SkinTone[] = skinData as SkinTone[];

/* ── Hats (~92 entries) ── */

export interface HatData {
  hairDrawType: number; // 0=original, 1=covered, 2=hide hair
  ignoreHairstyleOffset: boolean;
  isMask: boolean;
  prismatic: boolean;
}

import hatsData from "./hatsData.json";
export const HATS_DATA: HatData[] = hatsData as HatData[];

/* ── Hairstyles ── */

export interface HairstyleExtra {
  sheet: "hairstyles2";
  tileX: number;
  tileY: number;
  useUniqueLeft: boolean;
  coveredHair: number;
  isBald: boolean;
}

import hairstyleData from "./hairstyleData.json";
export const HAIRSTYLE_EXTRA: Record<string, HairstyleExtra> =
  hairstyleData as unknown as Record<string, HairstyleExtra>;

/** Covered-hair lookup table for base hairstyles (indices 0-55). */
export const HAIR_COVERED_TABLE: number[] = [
  7, 1, 7, 11, 7, 5, 6, 7,
  7, 9, 7, 11, 7, 7, 7, 7,
  30, 17, 23, 23, 20, 23, 30, 23,
  24, 25, 30, 27, 28, 29, 30, 23,
  32, 33, 34, 30, 36, 30, 30, 39,
  30, 41, 46, 43, 44, 45, 46, 47,
  6, 52, 50, 51, 52, 53, 54, 55,
];

/* ── Shirts ── */

export interface ShirtData {
  name: string;
  male: number; // sprite index in shirts.png for male
  female: number; // sprite index for female
  color: [number, number, number]; // default RGB tint
  dyeable: boolean;
  sleeveless: boolean;
  prismatic: boolean;
}

import shirtData from "./shirtData.json";
export const SHIRTS_DATA: ShirtData[] = shirtData as ShirtData[];

/* ── Sleeves (per shirt sprite index) ── */

export interface SleeveColor {
  light: number; // packed RGB
  mid: number;
  dark: number;
  dyeable: number; // bitmask: bit2=light, bit1=mid, bit0=dark
}

import sleeveDataRaw from "./sleeveData.json";
export const SLEEVE_DATA: SleeveColor[] = sleeveDataRaw as SleeveColor[];

/* ── Pants ── */

export interface PantsData {
  name: string;
  sheetIndex: number;
  color: [number, number, number];
  dyeable: boolean;
  prismatic: boolean;
}

import pantsData from "./pantsData.json";
export const PANTS_DATA: PantsData[] = pantsData as PantsData[];

/* ── Default body colors ── */

export const DEFAULT_BODY_COLORS = {
  skin: [0xf9ae89, 0xe06b65, 0x6b003a] as [number, number, number],
  eye: [0x682b0f, 0x2d1206] as [number, number],
  sleeve: [0x8e1f0c, 0x701718, 0x4a0c06] as [number, number, number],
};

/* ── Sprite sheet grid constants ── */

export const SHEET_LAYOUT = {
  body: { frameW: 16, frameH: 32, armOffsetX: 96 },
  hairstyle: { columns: 8, deltaX: 16, deltaY: 96, frameW: 16, frameH: 32 },
  hats: { columns: 12, deltaX: 20, deltaY: 80, frameW: 20, frameH: 20 },
  shirts: { columns: 16, deltaX: 8, deltaY: 32, frameW: 8, frameH: 8, coloredOffsetX: 128 },
  pants: { columns: 10, deltaX: 192, deltaY: 688, frameW: 16, frameH: 32, femaleOffsetX: 96, dyeableOffsetY: 672 },
} as const;
