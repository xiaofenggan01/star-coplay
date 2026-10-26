/**
 * SDV color utilities.
 * Ported from lybell-art/stardew-dressup/src/utils/colors.js
 *
 * SDV uses HSB where H:0-100 (NOT standard 0-360), S:0-100, B:0-100.
 * Colors are stored as packed integers: (R << 16) | (G << 8) | B.
 */

/* ── HSB ↔ RGB ── */

export function hsbToRgb(hue: number, saturation: number, brightness: number): [number, number, number] {
  const hueDegrees = (hue / 100) * 360;
  const s = saturation / 100;
  const b = brightness / 100;
  const k = (n: number) => (n + hueDegrees / 60) % 6;
  const f = (n: number) => b * (1 - s * Math.max(0, Math.min(k(n), 4 - k(n), 1)));
  return [5, 3, 1].map((i) => Math.floor(255 * f(i))) as [number, number, number];
}

export function rgbToHsb(r: number, g: number, b: number): [number, number, number] {
  const rf = r / 255, gf = g / 255, bf = b / 255;
  const min = Math.min(rf, gf, bf);
  const max = Math.max(rf, gf, bf);
  const delta = max - min;
  const brightness = max * 100;
  if (delta === 0) return [0, 0, Math.floor(brightness)];
  const saturation = (delta / max) * 100;
  let hue: number;
  if (rf === max) hue = (gf - bf) / delta;
  else if (gf === max) hue = 2 + (bf - rf) / delta;
  else hue = 4 + (rf - gf) / delta;
  hue *= 60;
  if (hue < 0) hue += 360;
  return [Math.floor(hue), Math.floor(saturation), Math.floor(brightness)];
}

/* ── Packed integer helpers ── */

export function hexToRgb(hex: number): [number, number, number] {
  return [(hex >> 16) & 0xff, (hex >> 8) & 0xff, hex & 0xff];
}

export function rgbToHex(r: number, g: number, b: number): number {
  return ((r & 0xff) << 16) | ((g & 0xff) << 8) | (b & 0xff);
}

/* ── Multiply blend ── */

export function multiplyColor(c1: number, c2: number | [number, number, number]): number {
  const a = hexToRgb(c1);
  const b = typeof c2 === "number" ? hexToRgb(c2) : c2;
  return rgbToHex(
    Math.round((a[0] / 255) * (b[0] / 255) * 255),
    Math.round((a[1] / 255) * (b[1] / 255) * 255),
    Math.round((a[2] / 255) * (b[2] / 255) * 255),
  );
}

/* ── Brightness adjustment (matches SDV FarmerRenderer.changeBrightness) ── */

export function changeBrightness(color: number, brightness: number): number {
  const c = hexToRgb(color);
  const blueAdj = brightness > 0 ? (brightness * 5) / 6 : (brightness * 8) / 7;
  const r = Math.max(0, Math.min(255, c[0] + brightness));
  const g = Math.max(0, Math.min(255, c[1] + brightness));
  const b = Math.max(0, Math.min(255, c[2] + Math.floor(blueAdj)));
  return rgbToHex(r, g, b);
}

/* ── Pixel-level color replacement on ImageData ── */

export function replaceColorsInImageData(
  imageData: ImageData,
  replacements: Array<{ from: number; to: number }>,
  tolerance = 3,
): void {
  const { data } = imageData;
  // Precompute RGB arrays
  const fromRgbs = replacements.map((r) => hexToRgb(r.from));
  const toRgbs = replacements.map((r) => hexToRgb(r.to));
  const tolSq = tolerance * tolerance;

  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] === 0) continue; // skip transparent
    const r = data[i], g = data[i + 1], b = data[i + 2];
    for (let j = 0; j < fromRgbs.length; j++) {
      const fr = fromRgbs[j];
      const dr = r - fr[0], dg = g - fr[1], db = b - fr[2];
      if (dr * dr + dg * dg + db * db <= tolSq) {
        const to = toRgbs[j];
        data[i] = to[0];
        data[i + 1] = to[1];
        data[i + 2] = to[2];
        break;
      }
    }
  }
}

/* ── Canvas 2D multiply tint ── */

export function applyTint(
  ctx: CanvasRenderingContext2D,
  sourceRegion: HTMLCanvasElement | ImageBitmap,
  tintRgb: [number, number, number],
  dx: number,
  dy: number,
): void {
  // Draw source
  ctx.drawImage(sourceRegion, dx, dy);
  // Multiply blend with tint color
  ctx.globalCompositeOperation = "multiply";
  ctx.fillStyle = `rgb(${tintRgb[0]},${tintRgb[1]},${tintRgb[2]})`;
  ctx.fillRect(dx, dy, sourceRegion instanceof HTMLCanvasElement ? sourceRegion.width : sourceRegion.width, sourceRegion instanceof HTMLCanvasElement ? sourceRegion.height : sourceRegion.height);
  // Restore alpha from source
  ctx.globalCompositeOperation = "destination-in";
  ctx.drawImage(sourceRegion, dx, dy);
  ctx.globalCompositeOperation = "source-over";
}
