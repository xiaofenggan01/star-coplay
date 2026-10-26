"use client";

import { useMemo } from "react";
import type { HSBColor } from "@/lib/types/appearance";

/**
 * SDV HSB (H:0-100, NOT standard 0-360) → CSS rgb() string.
 * Uses the same algorithm as color-utils.ts hsbToRgb.
 */
function hsbToCss(h: number, s: number, b: number): string {
  const sF = s / 100;
  const bF = b / 100;
  const k = (n: number) => (n + h / 60) % 6;
  const f = (n: number) =>
    bF * (1 - sF * Math.max(0, Math.min(k(n), 4 - k(n), 1)));
  const r = Math.floor(255 * f(5));
  const g = Math.floor(255 * f(3));
  const bl = Math.floor(255 * f(1));
  return `rgb(${r},${g},${bl})`;
}

interface HSBColorPickerProps {
  value: HSBColor;
  onChange: (color: HSBColor) => void;
  label?: string;
}

export default function HSBColorPicker({
  value,
  onChange,
  label,
}: HSBColorPickerProps) {
  const preview = useMemo(
    () => hsbToCss(value.h, value.s, value.b),
    [value],
  );

  const hueGradient = useMemo(() => {
    const stops = Array.from({ length: 11 }, (_, i) =>
      hsbToCss(i * 10, 100, 100),
    );
    return `linear-gradient(to right, ${stops.join(", ")})`;
  }, []);

  const satGradient = useMemo(
    () =>
      `linear-gradient(to right, ${hsbToCss(value.h, 0, value.b)}, ${hsbToCss(value.h, 100, value.b)})`,
    [value.h, value.b],
  );

  const brightGradient = useMemo(
    () =>
      `linear-gradient(to right, ${hsbToCss(value.h, value.s, 0)}, ${hsbToCss(value.h, value.s, 100)})`,
    [value.h, value.s],
  );

  return (
    <div className="space-y-1.5">
      {label && (
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[11px] font-bold text-stardew-brown">
            {label}
          </span>
          <div
            className="w-4 h-4 rounded border-2 border-stardew-wood/30 pixel-art"
            style={{ backgroundColor: preview }}
          />
        </div>
      )}
      {(
        [
          { ch: "h", gradient: hueGradient },
          { ch: "s", gradient: satGradient },
          { ch: "b", gradient: brightGradient },
        ] as const
      ).map(({ ch, gradient }) => (
        <div key={ch} className="flex items-center gap-1.5">
          <span className="text-[9px] font-bold text-stardew-brown/50 w-2.5">
            {ch.toUpperCase()}
          </span>
          <div
            className="flex-1 h-3 rounded-full relative overflow-hidden"
            style={{ background: gradient }}
          >
            <input
              type="range"
              min={0}
              max={100}
              value={value[ch]}
              onChange={(e) =>
                onChange({ ...value, [ch]: Number(e.target.value) })
              }
              className="sdv-color-slider absolute inset-0 w-full"
            />
          </div>
        </div>
      ))}

      <style jsx>{`
        .sdv-color-slider {
          appearance: none;
          background: transparent;
          height: 12px;
          cursor: pointer;
        }
        .sdv-color-slider::-webkit-slider-runnable-track {
          height: 12px;
          background: transparent;
        }
        .sdv-color-slider::-webkit-slider-thumb {
          appearance: none;
          width: 14px;
          height: 14px;
          border-radius: 50%;
          border: 2px solid var(--color-stardew-wood-dark);
          background: #fff7e3;
          box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
          cursor: pointer;
          margin-top: -1px;
        }
        .sdv-color-slider::-moz-range-track {
          height: 12px;
          background: transparent;
          border: none;
        }
        .sdv-color-slider::-moz-range-thumb {
          width: 14px;
          height: 14px;
          border-radius: 50%;
          border: 2px solid var(--color-stardew-wood-dark);
          background: #fff7e3;
          box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
          cursor: pointer;
        }
      `}</style>
    </div>
  );
}
