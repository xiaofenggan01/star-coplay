/**
 * SDV sprite coordinate calculations.
 * Given an appearance config, computes the {x, y, w, h} crop rectangle
 * for each sprite layer within its sprite sheet.
 *
 * Ported from lybell-art/stardew-dressup CharacterStore computed properties.
 */

import type { Direction, BodyBounds, HairBounds, ShirtBounds, PantsBounds, HatBounds } from "./types";
import { DIR } from "./types";
import {
  SHEET_LAYOUT,
  HATS_DATA,
  HAIRSTYLE_EXTRA,
  HAIR_COVERED_TABLE,
  SHIRTS_DATA,
  PANTS_DATA,
  SLEEVE_DATA,
  SKIN_COLORS,
  DEFAULT_BODY_COLORS,
} from "./sprite-data";
import { hsbToRgb, multiplyColor, changeBrightness } from "./color-utils";
import type { AppearanceSubmit } from "../types/appearance";

/* ── Direction → sprite sheet row index ── */

function dirSheetIdx(dir: Direction, hasLeft = false): number {
  switch (dir) {
    case DIR.DOWN: return 0;       // FRONT
    case DIR.RIGHT: return 1;
    case DIR.UP: return hasLeft ? 3 : 2; // BACK
    case DIR.LEFT: return hasLeft ? 2 : 1;
  }
}

/* ── Hat hair behavior ── */

function getHairDrawType(hatIndex: number): number {
  if (hatIndex < 0 || hatIndex >= HATS_DATA.length) return 0;
  return HATS_DATA[hatIndex].hairDrawType;
}

function getCoveredHairIndex(innerIndex: number): number {
  const key = String(innerIndex);
  if (key in HAIRSTYLE_EXTRA) return HAIRSTYLE_EXTRA[key].coveredHair;
  if (innerIndex >= 56) return 30;
  return HAIR_COVERED_TABLE[innerIndex] ?? 30;
}

function hasUniqueLeftSprite(innerIndex: number): boolean {
  const key = String(innerIndex);
  if (key in HAIRSTYLE_EXTRA) return HAIRSTYLE_EXTRA[key].useUniqueLeft;
  return false;
}

function isBaldHairstyle(innerIndex: number): boolean {
  const key = String(innerIndex);
  if (key in HAIRSTYLE_EXTRA) return HAIRSTYLE_EXTRA[key].isBald;
  return innerIndex > 48 && innerIndex < 56;
}

function getHairInnerIndex(outerIndex: number): number {
  // Basic hairstyles: 0-55 from hairstyle.png (grid: 8 cols × rows)
  const basicCount = (672 / 96) * 8; // = 56
  if (outerIndex < basicCount) return outerIndex;
  // Additional hairstyles: mapped via HAIRSTYLE_EXTRA
  const positiveKeys = Object.keys(HAIRSTYLE_EXTRA)
    .filter((k) => Number(k) > 0)
    .map(Number)
    .sort((a, b) => a - b);
  const offset = outerIndex - basicCount;
  if (offset < positiveKeys.length) return positiveKeys[offset];
  return outerIndex;
}

function getHairSpritePos(innerIndex: number): { x: number; y: number; sheet: "default" | "hairstyles2" } {
  const key = String(innerIndex);
  if (key in HAIRSTYLE_EXTRA) {
    const d = HAIRSTYLE_EXTRA[key];
    return { x: d.tileX * 16, y: d.tileY * 16, sheet: "hairstyles2" };
  }
  const { columns, deltaX, deltaY } = SHEET_LAYOUT.hairstyle;
  return {
    x: (innerIndex % columns) * deltaX,
    y: Math.floor(innerIndex / columns) * deltaY,
    sheet: "default",
  };
}

/* ── Public coordinate computation functions ── */

export function getBodyBounds(dir: Direction): BodyBounds {
  const { frameW, frameH, armOffsetX } = SHEET_LAYOUT.body;
  const y = dirSheetIdx(dir) * frameH;
  return {
    base: { x: 0, y, w: frameW, h: frameH },
    arm: { x: armOffsetX, y, w: frameW, h: frameH },
    flipped: dir === DIR.LEFT,
  };
}

export function getHairBounds(
  outerIndex: number,
  hatIndex: number,
  dir: Direction,
): HairBounds & { innerIndex: number; isBald: boolean; sheet: "default" | "hairstyles2" } {
  let inner = getHairInnerIndex(outerIndex);
  const hairDrawType = getHairDrawType(hatIndex);
  if (hairDrawType === 2) inner = 52; // hide → bald
  else if (hairDrawType === 1) inner = getCoveredHairIndex(inner);

  const isBald = hairDrawType !== 2 && isBaldHairstyle(inner);
  const uniqueLeft = hasUniqueLeftSprite(inner);
  const { frameW, frameH } = SHEET_LAYOUT.hairstyle;

  let yOffset: number;
  if (uniqueLeft) {
    switch (dir) {
      case DIR.DOWN: yOffset = 0; break;
      case DIR.UP: yOffset = 2 * frameH; break;
      case DIR.RIGHT: yOffset = 1 * frameH; break;
      case DIR.LEFT: yOffset = 3 * frameH; break;
    }
  } else {
    yOffset = dirSheetIdx(dir) * frameH;
  }

  const pos = getHairSpritePos(inner);
  return {
    rect: { x: pos.x, y: pos.y + yOffset, w: frameW, h: frameH },
    sheet: pos.sheet,
    flipped: !uniqueLeft && dir === DIR.LEFT,
    innerIndex: inner,
    isBald,
  };
}

export function getShirtBounds(shirtIndex: number, dir: Direction, isMale: boolean): ShirtBounds {
  const data = SHIRTS_DATA[shirtIndex];
  if (!data) return { uncolored: { x: 0, y: 0, w: 8, h: 8 }, colored: { x: 128, y: 0, w: 8, h: 8 } };
  const spriteIndex = isMale ? data.male : data.female;
  const { columns, deltaX, deltaY, frameW, frameH, coloredOffsetX } = SHEET_LAYOUT.shirts;
  const x = (spriteIndex % columns) * deltaX;
  const y = Math.floor(spriteIndex / columns) * deltaY;
  const yOff = dirSheetIdx(dir, true) * frameH;
  return {
    uncolored: { x, y: y + yOff, w: frameW, h: frameH },
    colored: { x: x + coloredOffsetX, y: y + yOff, w: frameW, h: frameH },
  };
}

export function getPantsBounds(pantsIndex: number, dir: Direction, isMale: boolean): PantsBounds {
  const data = PANTS_DATA[pantsIndex];
  if (!data) return { rect: { x: 0, y: 0, w: 16, h: 32 }, flipped: false };
  const { columns, deltaX, deltaY, frameW, frameH, femaleOffsetX } = SHEET_LAYOUT.pants;
  const x = (data.sheetIndex % columns) * deltaX + (isMale ? 0 : femaleOffsetX);
  const yOff = dirSheetIdx(dir) * frameH;
  const y = Math.floor(data.sheetIndex / columns) * deltaY + yOff;
  return {
    rect: { x, y, w: frameW, h: frameH },
    flipped: dir === DIR.LEFT,
  };
}

export function getHatBounds(hatIndex: number, dir: Direction): HatBounds | null {
  if (hatIndex < 0 || hatIndex >= HATS_DATA.length) return null;
  const { columns, deltaX, deltaY, frameW, frameH } = SHEET_LAYOUT.hats;
  // Sprite sheet layout: each hat group occupies 4 direction rows.
  // deltaY (80) = block height per hat group (4 dirs × frameH 20).
  // Direction offset uses frameH (20), NOT deltaY (80).
  const yOffset = dirSheetIdx(dir, true) * frameH;
  const x = (hatIndex % columns) * deltaX;
  const y = Math.floor(hatIndex / columns) * deltaY + yOffset;
  return { rect: { x, y, w: frameW, h: frameH } };
}

export function getHatYOffset(hatIndex: number, hairInnerIndex: number, dir: Direction, isMale: boolean): number {
  let offset = -2;
  if (dir === DIR.UP) offset -= 1;
  const hatData = HATS_DATA[hatIndex];
  if (hatData && !hatData.ignoreHairstyleOffset) {
    const hairMod = hairInnerIndex % 16;
    if (hairMod === 3 || hairMod === 6 || hairMod === 8) offset += 1;
  }
  if (!isMale) offset += 1;
  return offset;
}

export function getHairYOffset(innerIndex: number, isMale: boolean): number {
  let offset = 1;
  if (innerIndex >= 16) offset -= 1;
  if (!isMale) offset += 1;
  return offset;
}

export function getShirtYOffset(dir: Direction, isMale: boolean): number {
  let offset = 15;
  if (dir === DIR.UP) offset -= 1;
  if (!isMale) offset += 1;
  return offset;
}

/* ── Color computation ── */

export function computeSkinColor(skinIndex: number): [number, number, number] {
  const tone = SKIN_COLORS[skinIndex] ?? SKIN_COLORS[0];
  return [tone.light, tone.mid, tone.dark];
}

export function computeEyeColor(eyeHsb: { h: number; s: number; b: number }): [number, number] {
  const rgb = hsbToRgb(eyeHsb.h, eyeHsb.s, eyeHsb.b);
  const base = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2];
  let dark = changeBrightness(base, -75);
  let adjustedBase = base;
  if (base === dark) adjustedBase += 10;
  return [adjustedBase, dark];
}

export function computeSleeveColor(
  shirtIndex: number,
  isMale: boolean,
  shirtColorRgb: [number, number, number],
  skinColor: [number, number, number],
  sleeveless: boolean,
): [number, number, number] {
  if (sleeveless) return skinColor;
  const data = SHIRTS_DATA[shirtIndex];
  if (!data) return DEFAULT_BODY_COLORS.sleeve;
  const spriteIndex = isMale ? data.male : data.female;
  const sleeve = SLEEVE_DATA[spriteIndex];
  if (!sleeve) return DEFAULT_BODY_COLORS.sleeve;

  const tintHex = (shirtColorRgb[0] << 16) | (shirtColorRgb[1] << 8) | shirtColorRgb[2];
  let { light, mid, dark } = sleeve;
  const dyeable = sleeve.dyeable;
  if (dyeable & 4) light = multiplyColor(light, tintHex);
  if (dyeable & 2) mid = multiplyColor(mid, tintHex);
  if (dyeable & 1) dark = multiplyColor(dark, tintHex);
  return [light, mid, dark];
}

export function getBodySheetName(isMale: boolean, isBald: boolean): string {
  const gender = isMale ? "male" : "female";
  const bald = isBald ? "_bald" : "";
  return `body_${gender}${bald}`;
}

export function getHatMaskInfo(hatIndex: number): { isMask: boolean } {
  if (hatIndex < 0 || hatIndex >= HATS_DATA.length) return { isMask: false };
  return { isMask: HATS_DATA[hatIndex].isMask };
}
