/**
 * AI 搭子外观配置（Appearance）
 *
 * 存储位置: public.profiles.config.appearance (jsonb)
 * 数据来源: StarCoplay Mod 采集游戏内 Farmer 外观参数
 * 色彩格式: SDV 使用 HSB (H: 0-100, S: 0-100, B: 0-100)，非标准 HSV
 */

/* ------------------------------------------------------------------ */
/*  Color Utilities                                                    */
/* ------------------------------------------------------------------ */

export interface HSBColor {
  h: number; // 0-100 (SDV scale, NOT standard 0-360)
  s: number; // 0-100
  b: number; // 0-100
}

export interface RGBColor {
  r: number; // 0-255
  g: number; // 0-255
  b: number; // 0-255
}

export function hsbToRgb(hsb: HSBColor): RGBColor {
  const h = (hsb.h / 100) * 360;
  const s = hsb.s / 100;
  const v = hsb.b / 100;
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;
  let r = 0, g = 0, b = 0;
  if (h < 60) { r = c; g = x; }
  else if (h < 120) { r = x; g = c; }
  else if (h < 180) { g = c; b = x; }
  else if (h < 240) { g = x; b = c; }
  else if (h < 300) { r = x; b = c; }
  else { r = c; b = x; }
  return {
    r: Math.round((r + m) * 255),
    g: Math.round((g + m) * 255),
    b: Math.round((b + m) * 255),
  };
}

export function rgbToHex(rgb: RGBColor): string {
  const hex = (v: number) => v.toString(16).padStart(2, "0");
  return `#${hex(rgb.r)}${hex(rgb.g)}${hex(rgb.b)}`;
}

export function hsbToHex(hsb: HSBColor): string {
  return rgbToHex(hsbToRgb(hsb));
}

/* ------------------------------------------------------------------ */
/*  Core Data Structures                                               */
/* ------------------------------------------------------------------ */

export interface AppearanceConfig {
  gender: "Male" | "Female";
  skin: number;           // 0-23
  hairstyle: number;      // 0-73
  hairColor: HSBColor;
  eyeColor: HSBColor;
  accessory: number;      // -1 ~ 30 (-1 = none)
  shirtIndex: number;     // 0-111
  pantsIndex: number;     // 0-3
  pantsColor: HSBColor;
  hatIndex: number | null;     // null = no hat
  bootsIndex: number | null;
  facingDirection: 0 | 1 | 2 | 3; // 0=up 1=right 2=down 3=left
  updated_at: string;
}

export type AppearanceSubmit = Omit<AppearanceConfig, "updated_at">;

/* ------------------------------------------------------------------ */
/*  Validation                                                         */
/* ------------------------------------------------------------------ */

export const APPEARANCE_CONSTRAINTS = {
  SKIN_MIN: 0,
  SKIN_MAX: 23,
  HAIRSTYLE_MIN: 0,
  HAIRSTYLE_MAX: 73,
  ACCESSORY_MIN: -1,
  ACCESSORY_MAX: 30,
  SHIRT_MIN: 0,
  SHIRT_MAX: 111,
  PANTS_MIN: 0,
  PANTS_MAX: 3,
  HAT_MIN: 0,
  HAT_MAX: 999,
  BOOTS_MIN: 0,
  BOOTS_MAX: 999,
} as const;

/* ------------------------------------------------------------------ */
/*  Skin Palette (24 tones from SDV farmer_base)                       */
/* ------------------------------------------------------------------ */

export const SKIN_PALETTE: string[] = [
  "#fce4c0", "#f0c8a0", "#e0a878", "#c89060",
  "#a07040", "#805830", "#604020", "#402810",
  "#f8dcc0", "#f0c898", "#d8a870", "#c09058",
  "#f5d4b8", "#e4b890", "#cc9c68", "#b48450",
  "#f0ccb0", "#d8ac80", "#c09460", "#a87c48",
  "#e8c0a0", "#d0a478", "#b88c58", "#a07440",
];

export function getSkinColorHex(skinIndex: number): string {
  return SKIN_PALETTE[Math.max(0, Math.min(skinIndex, SKIN_PALETTE.length - 1))];
}

/* ------------------------------------------------------------------ */
/*  Shirt Color Palette (simplified mapping for rendering)             */
/* ------------------------------------------------------------------ */

export const SHIRT_PALETTE: string[] = [
  "#4a90d9", "#d94040", "#40b040", "#d9a040",
  "#9040d9", "#40b0b0", "#d97040", "#707070",
  "#d94090", "#90d940", "#4060d9", "#d9d940",
  "#40d990", "#d94060", "#6090d9", "#d9b040",
];

export function getShirtColorHex(shirtIndex: number): string {
  return SHIRT_PALETTE[shirtIndex % SHIRT_PALETTE.length];
}

/* ------------------------------------------------------------------ */
/*  Default & Mock Data                                                */
/* ------------------------------------------------------------------ */

export const DEFAULT_APPEARANCE: AppearanceSubmit = {
  gender: "Male",
  skin: 0,
  hairstyle: 0,
  hairColor: { h: 15, s: 50, b: 40 },
  eyeColor: { h: 60, s: 40, b: 70 },
  accessory: -1,
  shirtIndex: 0,
  pantsIndex: 0,
  pantsColor: { h: 60, s: 30, b: 40 },
  hatIndex: null,
  bootsIndex: null,
  facingDirection: 2,
};

export const MOCK_APPEARANCES: Record<string, AppearanceSubmit> = {
  farmer: {
    gender: "Male",
    skin: 0,
    hairstyle: 3,
    hairColor: { h: 15, s: 60, b: 50 },
    eyeColor: { h: 60, s: 40, b: 70 },
    accessory: -1,
    shirtIndex: 0,
    pantsIndex: 1,
    pantsColor: { h: 60, s: 20, b: 30 },
    hatIndex: null,
    bootsIndex: null,
    facingDirection: 2,
  },
  fisher: {
    gender: "Male",
    skin: 3,
    hairstyle: 8,
    hairColor: { h: 10, s: 40, b: 30 },
    eyeColor: { h: 50, s: 50, b: 60 },
    accessory: 1,
    shirtIndex: 4,
    pantsIndex: 0,
    pantsColor: { h: 60, s: 15, b: 25 },
    hatIndex: 5,
    bootsIndex: null,
    facingDirection: 2,
  },
  socialite: {
    gender: "Female",
    skin: 1,
    hairstyle: 15,
    hairColor: { h: 5, s: 70, b: 80 },
    eyeColor: { h: 45, s: 50, b: 75 },
    accessory: 2,
    shirtIndex: 6,
    pantsIndex: 2,
    pantsColor: { h: 80, s: 40, b: 50 },
    hatIndex: null,
    bootsIndex: null,
    facingDirection: 2,
  },
};
