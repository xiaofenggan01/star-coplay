/**
 * SDV farmer sprite renderer — pure Canvas 2D implementation.
 *
 * Composites 7 layers in z-order:
 *   Body(0) → Pants(1) → Shirt(2) → Hair(3) → Hat(4) → Arm(5/-1)
 *
 * Color manipulation:
 *   - Body: pixel-level replacement for skin/eye/sleeve (8 colors)
 *   - Pants/Shirt/Hair/Hat: multiply blend tinting
 */

import type { SpriteKey } from "./sprite-loader";
import type { AppearanceSubmit } from "../types/appearance";
import type { Direction } from "./types";
import { DIR } from "./types";
import { DEFAULT_BODY_COLORS, PANTS_DATA, SHIRTS_DATA } from "./sprite-data";
import { hsbToRgb, replaceColorsInImageData } from "./color-utils";
import {
  getBodyBounds,
  getHairBounds,
  getShirtBounds,
  getPantsBounds,
  getHatBounds,
  getHatYOffset,
  getHairYOffset,
  getShirtYOffset,
  computeSkinColor,
  computeEyeColor,
  computeSleeveColor,
  getBodySheetName,
  getHatMaskInfo,
} from "./sprite-coords";

const CANVAS_W = 16;
const CANVAS_H = 32;

function getCardRenderDirection(): Direction {
  return DIR.DOWN;
}

/* ── Helper: crop a region from a sprite sheet ── */

function cropRegion(
  img: HTMLImageElement,
  rect: { x: number; y: number; w: number; h: number },
): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = rect.w;
  c.height = rect.h;
  const ctx = c.getContext("2d")!;
  ctx.drawImage(img, rect.x, rect.y, rect.w, rect.h, 0, 0, rect.w, rect.h);
  return c;
}

/* ── Helper: apply pixel-level color replacement to a canvas ── */

function replaceColors(
  canvas: HTMLCanvasElement,
  from: number[],
  to: number[],
): void {
  const ctx = canvas.getContext("2d")!;
  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const replacements = from.map((f, i) => ({ from: f, to: to[i] }));
  replaceColorsInImageData(imageData, replacements);
  ctx.putImageData(imageData, 0, 0);
}

/* ── Helper: apply multiply tint to a cropped region ── */

function tintRegion(img: HTMLImageElement, rect: { x: number; y: number; w: number; h: number }, tint: [number, number, number]): HTMLCanvasElement {
  const source = cropRegion(img, rect);
  const c = document.createElement("canvas");
  c.width = rect.w;
  c.height = rect.h;
  const ctx = c.getContext("2d")!;

  // Draw source
  ctx.drawImage(source, 0, 0);
  // Multiply blend
  ctx.globalCompositeOperation = "multiply";
  ctx.fillStyle = `rgb(${tint[0]},${tint[1]},${tint[2]})`;
  ctx.fillRect(0, 0, c.width, c.height);
  // Restore alpha from source
  ctx.globalCompositeOperation = "destination-in";
  ctx.drawImage(source, 0, 0);
  ctx.globalCompositeOperation = "source-over";

  return c;
}

/* ── Draw a canvas flipped horizontally ── */

function drawFlipped(ctx: CanvasRenderingContext2D, src: HTMLCanvasElement, dx: number, dy: number) {
  ctx.save();
  ctx.translate(dx + src.width, dy);
  ctx.scale(-1, 1);
  ctx.drawImage(src, 0, 0);
  ctx.restore();
}

/* ── Main render function ── */

export function renderFarmer(
  appearance: AppearanceSubmit,
  sprites: globalThis.Map<SpriteKey, HTMLImageElement>,
): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = CANVAS_W;
  canvas.height = CANVAS_H;
  const ctx = canvas.getContext("2d")!;

  const dir = getCardRenderDirection();
  const isMale = appearance.gender === "Male";
  const hatIndex = appearance.hatIndex ?? -1;
  const shirtIndex = appearance.shirtIndex >= 0 ? appearance.shirtIndex : 0;
  const pantsIndex = appearance.pantsIndex >= 0 ? appearance.pantsIndex : 0;

  // Resolve hair (with hat interaction)
  const hair = getHairBounds(appearance.hairstyle, hatIndex, dir);
  const bodySheet = getBodySheetName(isMale, hair.isBald) as SpriteKey;

  // Compute colors
  const skinColor = computeSkinColor(appearance.skin);
  const eyeColor = computeEyeColor(appearance.eyeColor);
  const hairColorRgb = hsbToRgb(appearance.hairColor.h, appearance.hairColor.s, appearance.hairColor.b);
  const shirtColorRgb = hsbToRgb(
    ...(SHIRTS_DATA[shirtIndex]?.dyeable
      ? [appearance.hairColor.h, appearance.hairColor.s, appearance.hairColor.b] as [number, number, number]
      : SHIRTS_DATA[shirtIndex]?.color ?? [255, 255, 255]),
  );
  const sleeveless = SHIRTS_DATA[shirtIndex]?.sleeveless ?? false;
  const sleeveColor = computeSleeveColor(
    shirtIndex,
    isMale,
    shirtColorRgb,
    skinColor,
    sleeveless,
  );

  // Color replacement pairs for body
  const colorFrom = [...DEFAULT_BODY_COLORS.skin, ...DEFAULT_BODY_COLORS.eye, ...DEFAULT_BODY_COLORS.sleeve];
  const colorTo = [...skinColor, ...eyeColor, ...sleeveColor];

  // ── Layer 0: Body base ──
  const bodyImg = sprites.get(bodySheet)!;
  const bodyBounds = getBodyBounds(dir);
  const bodyRegion = cropRegion(bodyImg, bodyBounds.base);
  replaceColors(bodyRegion, colorFrom, colorTo);
  if (bodyBounds.flipped) {
    drawFlipped(ctx, bodyRegion, 0, 0);
  } else {
    ctx.drawImage(bodyRegion, 0, 0);
  }

  // ── Layer 1: Pants ──
  const pantsImg = sprites.get("pants")!;
  const pantsBounds = getPantsBounds(pantsIndex, dir, isMale);
  const pantsData = PANTS_DATA[pantsIndex];
  if (pantsData) {
    let pantsRegion: HTMLCanvasElement;
    if (pantsData.dyeable) {
      const pantsRgb = hsbToRgb(appearance.pantsColor.h, appearance.pantsColor.s, appearance.pantsColor.b);
      pantsRegion = tintRegion(pantsImg, pantsBounds.rect, pantsRgb);
    } else {
      pantsRegion = cropRegion(pantsImg, pantsBounds.rect);
    }
    if (pantsBounds.flipped) {
      drawFlipped(ctx, pantsRegion, 0, 0);
    } else {
      ctx.drawImage(pantsRegion, 0, 0);
    }
  }

  // ── Layer 2: Shirt ──
  const shirtsImg = sprites.get("shirts")!;
  const shirtBounds = getShirtBounds(shirtIndex, dir, isMale);
  const shirtData = SHIRTS_DATA[shirtIndex];
  if (shirtData) {
    const shirtYOff = getShirtYOffset(dir, isMale);
    // Uncolored layer
    const uncolored = cropRegion(shirtsImg, shirtBounds.uncolored);
    ctx.drawImage(uncolored, 4, shirtYOff);
    // Colored layer (multiply tint)
    if (shirtData.dyeable || !shirtData.prismatic) {
      const tinted = tintRegion(shirtsImg, shirtBounds.colored, shirtColorRgb);
      ctx.drawImage(tinted, 4, shirtYOff);
    }
  }

  // ── Layer 3: Hair ──
  if (hair.innerIndex !== -1) {
    const hairSheetKey: SpriteKey = hair.sheet === "default" ? "hairstyle" : "hairstyles2";
    const hairImg = sprites.get(hairSheetKey)!;
    const hairYOff = getHairYOffset(hair.innerIndex, isMale);
    const hairTinted = tintRegion(hairImg, hair.rect, hairColorRgb);
    if (hair.flipped) {
      drawFlipped(ctx, hairTinted, 0, hairYOff);
    } else {
      ctx.drawImage(hairTinted, 0, hairYOff);
    }
  }

  // ── Layer 4: Hat ──
  if (hatIndex >= 0) {
    const hatBounds = getHatBounds(hatIndex, dir);
    if (hatBounds) {
      const hatImg = sprites.get("hats")!;
      const hatYOff = getHatYOffset(hatIndex, hair.innerIndex, dir, isMale);
      const { isMask } = getHatMaskInfo(hatIndex);

      if (isMask && dir === DIR.UP) {
        // Mask mode: split into upper (drawn under hair) and lower
        const upper = cropRegion(hatImg, { x: hatBounds.rect.x, y: hatBounds.rect.y, w: hatBounds.rect.w, h: 11 });
        const lower = cropRegion(hatImg, { x: hatBounds.rect.x, y: hatBounds.rect.y + 11, w: hatBounds.rect.w, h: 9 });
        ctx.drawImage(lower, -2, hatYOff + 11);
        // Upper part drawn at z=2.75 (after hair, before hat) — we draw it here for simplicity
        ctx.drawImage(upper, -2, hatYOff);
      } else {
        const hatRegion = cropRegion(hatImg, hatBounds.rect);
        ctx.drawImage(hatRegion, -2, hatYOff);
      }
    }
  }

  // ── Layer 5: Body arm (drawn on top for front/sides, behind for back) ──
  if (dir !== DIR.UP) {
    const armRegion = cropRegion(bodyImg, bodyBounds.arm);
    replaceColors(armRegion, colorFrom, colorTo);
    if (bodyBounds.flipped) {
      drawFlipped(ctx, armRegion, 0, 0);
    } else {
      ctx.drawImage(armRegion, 0, 0);
    }
  }

  return canvas;
}
