"use client";

import { useRef, useCallback, useState, useEffect } from "react";
import QRCode from "qrcode";
import type { CustomFlavor } from "@/lib/types/agentee";
import type { AppearanceSubmit } from "@/lib/types/appearance";
import { DEFAULT_APPEARANCE } from "@/lib/types/appearance";
import { renderFarmer } from "@/lib/sdv/renderer";
import { loadAllSprites, getCached } from "@/lib/sdv/sprite-loader";
import type { SpriteKey } from "@/lib/sdv/sprite-loader";
import { getSiteUrl } from "@/lib/site-url";

/* ── Background presets ── */
export const BACKGROUND_PRESETS = [
  { id: "farm-day", label: "农场·白天", src: "/assets/sdv/backgrounds/farm-day.webp" },
  { id: "farm-evening", label: "农场·黄昏", src: "/assets/sdv/backgrounds/farm-evening.webp" },
  { id: "night-market", label: "夜市", src: "/assets/sdv/backgrounds/night-market.webp" },
  { id: "multiplayer", label: "多人农场", src: "/assets/sdv/backgrounds/multiplayer-1.webp" },
  { id: "horses", label: "骑马郊游", src: "/assets/sdv/backgrounds/multiplayer-horses.webp" },
] as const;

/* ── Card image assets ── */
const CARD = {
  frame: "/assets/cards/frame.png",
  infoBase: "/assets/cards/info-base.png",
  nameBadge: "/assets/cards/name-badge.png",
  qrFrame: "/assets/cards/qr-frame.png",
  logo: "/assets/cards/logo.png",
  sunflower: "/assets/cards/sunflower.png",
  wheat: "/assets/cards/wheat.png",
  notes: "/assets/cards/notes.png",
} as const;

const ALL_SPRITE_KEYS: SpriteKey[] = [
  "body_male", "body_male_bald", "body_female", "body_female_bald",
  "hairstyle", "hairstyles2", "hats", "shirts", "pants",
];

const PX = '"zpix", "Press Start 2P", monospace';
const SITE_URL = getSiteUrl(typeof window === "undefined" ? undefined : window.location.origin);

// 9:16 collectible card ratio
const CARD_W = 320;
const CARD_H = Math.round(CARD_W * 16 / 9); // 569

interface CompanionCardProps {
  agentName?: string;
  persona?: { description?: string; custom_flavor?: CustomFlavor[] };
  appearance?: AppearanceSubmit;
  backgroundSrc?: string;
}

export default function CompanionCard({
  agentName = "小枫",
  persona,
  appearance = DEFAULT_APPEARANCE,
  backgroundSrc = BACKGROUND_PRESETS[0].src,
}: CompanionCardProps) {
  const cardRef = useRef<HTMLDivElement>(null);
  const charCanvasRef = useRef<HTMLCanvasElement>(null);
  const qrCanvasRef = useRef<HTMLCanvasElement>(null);
  const [exporting, setExporting] = useState(false);
  const [spritesLoaded, setSpritesLoaded] = useState(false);

  const desc = persona?.description ?? "一个热爱星露谷生活的 AI 搭子";
  const flavors = persona?.custom_flavor ?? [];

  useEffect(() => {
    loadAllSprites().then(() => setSpritesLoaded(true));
  }, []);

  useEffect(() => {
    const canvas = qrCanvasRef.current;
    if (!canvas) return;
    QRCode.toCanvas(canvas, SITE_URL, {
      width: 52,
      margin: 0,
      errorCorrectionLevel: "M",
      color: { dark: "#3E2723", light: "#F5E6C4" },
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!spritesLoaded) return;
    const sprites = new Map<SpriteKey, HTMLImageElement>();
    for (const k of ALL_SPRITE_KEYS) {
      const img = getCached(k);
      if (img) sprites.set(k, img);
    }
    const result = renderFarmer(appearance, sprites);
    const display = charCanvasRef.current;
    if (!display) return;
    // Render at display resolution for crisp html2canvas export
    const scale = 9;
    display.width = Math.round(result.width * scale);
    display.height = Math.round(result.height * scale);
    const ctx = display.getContext("2d");
    if (!ctx) return;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(result, 0, 0, display.width, display.height);
  }, [appearance, spritesLoaded]);

  const captureCard = useCallback(async () => {
    if (!cardRef.current) return null;
    const html2canvas = (await import("html2canvas")).default;
    return html2canvas(cardRef.current, { scale: 2, backgroundColor: null, useCORS: true });
  }, []);

  const handleExport = useCallback(async () => {
    setExporting(true);
    try {
      const canvas = await captureCard();
      if (!canvas) return;
      const a = document.createElement("a");
      a.download = `star-coplay-${agentName}-${Date.now()}.png`;
      a.href = canvas.toDataURL("image/png");
      a.click();
    } catch { /* */ } finally { setExporting(false); }
  }, [captureCard, agentName]);

  const handleShare = useCallback(async () => {
    try {
      const canvas = await captureCard();
      if (!canvas) return;
      canvas.toBlob(async (blob) => {
        if (!blob) return;
        const file = new File([blob], "star-coplay-companion.png", { type: "image/png" });
        if (navigator.share && navigator.canShare?.({ files: [file] })) {
          await navigator.share({ title: `我的星露谷 AI 搭子「${agentName}」`, text: desc, files: [file] });
        } else {
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.download = `star-coplay-${agentName}-${Date.now()}.png`;
          a.href = url;
          a.click();
          URL.revokeObjectURL(url);
        }
      }, "image/png");
    } catch { /* */ }
  }, [captureCard, agentName, desc]);

  return (
    <div className="relative z-0 isolate w-full max-w-full overflow-hidden">
      {/* Responsive outer wrapper */}
      <div style={{ width: "100%", maxWidth: CARD_W, margin: "0 auto", overflow: "hidden" }}>

        {/* ═══ Card body: fixed 9:16 ratio ═══ */}
        <div ref={cardRef} style={{
          width: "100%",
          maxWidth: CARD_W,
          aspectRatio: "9 / 16",
          position: "relative",
          overflow: "hidden",
          fontFamily: PX,
          isolation: "isolate",
          zIndex: 0,
        }}>

          {/* ── z-0: Background scene ── */}
          <div style={{
            position: "absolute", inset: 0,
            backgroundImage: `url(${backgroundSrc})`,
            backgroundSize: "cover",
            backgroundPosition: "center",
            zIndex: 0,
          }} />

          {/* ── z-10: Character sprite ── */}
          <div style={{
            position: "absolute",
            bottom: "40%",
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 10,
          }}>
            <canvas
              ref={charCanvasRef}
              width={16}
              height={32}
              style={{
                width: 120,
                height: 240,
                imageRendering: "pixelated",
                display: "block",
              }}
            />
          </div>

          {/* ── z-20: Info card base (parchment) ── */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={CARD.infoBase}
            alt=""
            crossOrigin="anonymous"
            style={{
              position: "absolute",
              bottom: 0,
              left: 0,
              width: "100%",
              zIndex: 20,
            }}
          />

          {/* ── z-30: Content layer ── */}
          <div style={{
            position: "absolute",
            bottom: "5%",
            left: "50%",
            transform: "translateX(-50%)",
            width: "78%",
            zIndex: 30,
            textAlign: "center",
          }}>

            {/* Name badge */}
            <div style={{ position: "relative", width: "70%", margin: "0 auto 6px" }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={CARD.nameBadge}
                alt=""
                crossOrigin="anonymous"
                style={{ width: "100%", display: "block" }}
              />
              <div style={{
                position: "absolute",
                top: "50%",
                left: "50%",
                transform: "translate(-50%, -50%)",
                fontFamily: PX,
                fontSize: 13,
                color: "#3E2723",
                textShadow: "none",
                whiteSpace: "nowrap",
              }}>
                {agentName}
              </div>
            </div>

            {/* AI COMPANION ribbon */}
            <div style={{
              display: "inline-block",
              padding: "2px 16px",
              marginBottom: 6,
              background: "linear-gradient(180deg, #C0392B 0%, #962D22 100%)",
              color: "#F5E6C4",
              fontFamily: PX,
              fontSize: 6,
              letterSpacing: 1,
              textShadow: "0 1px 0px rgba(0,0,0,0.25)",
              clipPath: "polygon(8px 0%, calc(100% - 8px) 0%, 100% 50%, calc(100% - 8px) 100%, 8px 100%, 0% 50%)",
            }}>
              AI COMPANION
            </div>

            {/* Description with wheat flanking */}
            <div style={{ position: "relative", padding: "0 24px", marginBottom: 4 }}>
              {/* Wheat left */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={CARD.wheat}
                alt=""
                crossOrigin="anonymous"
                style={{
                  position: "absolute", left: 0, top: "50%",
                  transform: "translateY(-50%)",
                  width: 18, opacity: 0.7,
                }}
              />
              <p style={{
                fontFamily: PX, fontSize: 8, color: "#4A3728",
                lineHeight: 1.7, margin: 0,
              }}>
                &ldquo;{desc}&rdquo;
              </p>
              {/* Wheat right */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={CARD.wheat}
                alt=""
                crossOrigin="anonymous"
                style={{
                  position: "absolute", right: 0, top: "50%",
                  transform: "translateY(-50%) scaleX(-1)",
                  width: 18, opacity: 0.7,
                }}
              />
            </div>

            {/* Trait tags */}
            {flavors.length > 0 && (
              <div style={{
                display: "flex", gap: 4,
                flexWrap: "wrap", justifyContent: "center",
                margin: "4px 0",
              }}>
                {flavors.map((f, i) => (
                  <span key={i} style={{
                    fontFamily: PX, fontSize: 6, padding: "2px 7px", borderRadius: 2,
                    background: "linear-gradient(180deg, #A0713E 0%, #6B4226 100%)",
                    border: "1.5px solid #3E2510", color: "#F5E6C4", fontWeight: 700,
                    textShadow: "0 1px 0px rgba(0,0,0,0.25)",
                  }}>
                    {f.label}
                  </span>
                ))}
              </div>
            )}

            {/* QR code with decorative frame */}
            <div style={{ marginTop: 6, display: "flex", flexDirection: "column", alignItems: "center" }}>
              <div style={{ position: "relative", display: "inline-block" }}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={CARD.qrFrame}
                  alt=""
                  crossOrigin="anonymous"
                  style={{ width: 96, display: "block" }}
                />
                <div style={{
                  position: "absolute",
                  top: "50%", left: "50%",
                  transform: "translate(-50%, -50%)",
                }}>
                  <canvas
                    ref={qrCanvasRef}
                    width={52}
                    height={52}
                    aria-label="StarCoplay site QR code"
                    style={{ width: 52, height: 52, display: "block" }}
                  />
                </div>
              </div>
              <div style={{ fontFamily: PX, fontSize: 5, color: "rgba(74,55,40,0.5)", marginTop: 2 }}>
                扫码领养你的搭子!
              </div>
            </div>

            {/* Brand bar with logo */}
            <div style={{
              marginTop: 6,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 6,
            }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={CARD.sunflower}
                alt=""
                crossOrigin="anonymous"
                style={{ width: 20, opacity: 0.8 }}
              />
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={CARD.logo}
                alt="StarCoplay"
                crossOrigin="anonymous"
                style={{ height: 18 }}
              />
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={CARD.sunflower}
                alt=""
                crossOrigin="anonymous"
                style={{ width: 20, opacity: 0.8, transform: "scaleX(-1)" }}
              />
            </div>
          </div>

          {/* ── z-40: Decorative notes (floating) ── */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={CARD.notes}
            alt=""
            crossOrigin="anonymous"
            style={{
              position: "absolute",
              top: "6%", right: "8%",
              width: 28, zIndex: 40, opacity: 0.6,
            }}
          />

          {/* ── z-50: Outer frame overlay ── */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={CARD.frame}
            alt=""
            crossOrigin="anonymous"
            style={{
              position: "absolute", inset: 0,
              width: "100%", height: "100%",
              zIndex: 50,
              pointerEvents: "none",
            }}
          />
        </div>
      </div>

      {/* ── Action Buttons ── */}
      <div className="flex items-center justify-center gap-3 mt-6">
        <button onClick={handleExport} disabled={exporting} className="stardew-btn px-5 py-2 rounded-lg text-sm disabled:opacity-50">
          {exporting ? "生成中..." : "生成分享图"}
        </button>
        <button onClick={handleShare} className="bg-stardew-cream border-2 border-stardew-wood-dark text-stardew-brown font-semibold text-sm px-5 py-2 rounded-lg hover:border-stardew-gold transition-colors">
          分享
        </button>
      </div>
    </div>
  );
}
