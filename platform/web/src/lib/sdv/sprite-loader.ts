/**
 * Sprite sheet loader with module-level caching.
 * Lazy-loads PNG sprite sheets on first access.
 */

const BASE_PATH = "/assets/sdv";

export type SpriteKey =
  | "body_male"
  | "body_male_bald"
  | "body_female"
  | "body_female_bald"
  | "hairstyle"
  | "hairstyles2"
  | "hats"
  | "shirts"
  | "pants";

const FILE_MAP: Record<SpriteKey, string> = {
  body_male: "farmer_base.webp",
  body_male_bald: "farmer_base_bald.webp",
  body_female: "farmer_girl_base.webp",
  body_female_bald: "farmer_girl_base_bald.webp",
  hairstyle: "hairstyle.webp",
  hairstyles2: "hairstyles2.webp",
  hats: "hats.webp",
  shirts: "shirts.webp",
  pants: "pants.webp",
};

const cache = new Map<SpriteKey, HTMLImageElement>();
const pending = new Map<SpriteKey, Promise<HTMLImageElement>>();

function loadImage(key: SpriteKey): Promise<HTMLImageElement> {
  if (cache.has(key)) return Promise.resolve(cache.get(key)!);
  if (pending.has(key)) return pending.get(key)!;

  const promise = new Promise<HTMLImageElement>((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      cache.set(key, img);
      pending.delete(key);
      resolve(img);
    };
    img.onerror = () => {
      pending.delete(key);
      reject(new Error(`Failed to load sprite: ${key}`));
    };
    img.src = `${BASE_PATH}/${FILE_MAP[key]}`;
  });

  pending.set(key, promise);
  return promise;
}

export function loadSprite(key: SpriteKey): Promise<HTMLImageElement> {
  return loadImage(key);
}

export async function loadAllSprites(): Promise<Map<SpriteKey, HTMLImageElement>> {
  const keys = Object.keys(FILE_MAP) as SpriteKey[];
  await Promise.all(keys.map(loadImage));
  return new Map(cache);
}

export function isLoaded(key: SpriteKey): boolean {
  return cache.has(key);
}

export function getCached(key: SpriteKey): HTMLImageElement | undefined {
  return cache.get(key);
}
