/**
 * SDV sprite rendering engine types.
 * Ported from lybell-art/stardew-dressup.
 */

/** Direction constants matching SDV's internal values. */
export const DIR = { UP: 0, RIGHT: 1, DOWN: 2, LEFT: 3 } as const;
export type Direction = (typeof DIR)[keyof typeof DIR];

/** A rectangle region within a sprite sheet. */
export interface SpriteRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Which sprite sheet to use for a layer. */
export type SheetName =
  | "body_male"
  | "body_male_bald"
  | "body_female"
  | "body_female_bald"
  | "hairstyle"
  | "hairstyles2"
  | "hats"
  | "shirts"
  | "pants";

/** Computed bounding boxes for each layer of the farmer sprite. */
export interface BodyBounds {
  base: SpriteRect;
  arm: SpriteRect;
  flipped: boolean;
}

export interface HairBounds {
  rect: SpriteRect;
  sheet: "default" | "hairstyles2";
  flipped: boolean;
}

export interface ShirtBounds {
  uncolored: SpriteRect;
  colored: SpriteRect;
}

export interface PantsBounds {
  rect: SpriteRect;
  flipped: boolean;
}

export interface HatBounds {
  rect: SpriteRect;
}

/** Color replacement pair for pixel-level skin/eye/sleeve recoloring. */
export interface ColorReplacement {
  from: number[]; // source packed RGB integers
  to: number[];   // target packed RGB integers
}

/** Resolved appearance data ready for rendering. */
export interface ResolvedAppearance {
  direction: Direction;
  isMale: boolean;
  skinIndex: number;
  hairInnerIndex: number;
  hairSheet: "default" | "hairstyles2";
  hairFlipped: boolean;
  hairColorRgb: [number, number, number];
  eyeColorRgb: [number, number, number];
  shirtIndex: number;
  shirtColorRgb: [number, number, number];
  pantsSheetIndex: number;
  pantsColorRgb: [number, number, number];
  hatIndex: number;
  accessoryIndex: number;
  sleeveColor: [number, number, number]; // [light, mid, dark] packed
  sleeveless: boolean;
  bodySheet: SheetName;
  hairIsBald: boolean;
}
