"use client";

import { forwardRef, useRef, useCallback, useState, useEffect, useImperativeHandle } from "react";
import type { ForwardedRef } from "react";
import QRCode from "qrcode";
import type { AppearanceSubmit } from "@/lib/types/appearance";
import { DEFAULT_APPEARANCE } from "@/lib/types/appearance";
import type { CorePreferences, CustomFlavor, PreferenceDimension } from "@/lib/types/agentee";
import { PREFERENCE_DIMENSIONS } from "@/lib/types/agentee";
import { renderFarmer } from "@/lib/sdv/renderer";
import { loadAllSprites, getCached } from "@/lib/sdv/sprite-loader";
import type { SpriteKey } from "@/lib/sdv/sprite-loader";
import { getSiteUrl } from "@/lib/site-url";
import { BACKGROUND_PRESETS } from "./CompanionCard";

const SPRITE_KEYS: SpriteKey[] = [
  "body_male", "body_male_bald", "body_female", "body_female_bald",
  "hairstyle", "hairstyles2", "hats", "shirts", "pants",
];

const PX = '"zpix", "Press Start 2P", monospace';
const SITE_URL = getSiteUrl(typeof window === "undefined" ? undefined : window.location.origin);

/* Outer container dimensions */
const W = 380;
const H = 640;

/* Pill badge icons */
const PILL_ICONS = [
  "/assets/icons/Health.png",
  "/assets/icons/Golden_Walnut.png",
  "/assets/icons/The_Mines_Icon.png",
] as const;

interface InviteShareCardProps {
  agentName?: string;
  appearance?: AppearanceSubmit;
  inviteCode?: string | null;
  inviteCodeState?: "available" | "exhausted" | "empty";
  relationshipType?: string;
  styleSummary?: string;
  corePreferences?: CorePreferences;
  customFlavor?: CustomFlavor[];
  backgroundSrc?: string;
}

export interface InviteShareCardHandle {
  saveImage: () => Promise<boolean>;
}

/** Dimension labels for the 6-axis mini bars */
const DIM_LABELS: Record<PreferenceDimension, { name: string; low: string; mid: string; high: string }> = {
  warmth:       { name: "温暖度", low: "冷淡", mid: "友善", high: "温暖" },
  expressiveness: { name: "表达欲", low: "沉默", mid: "适中", high: "话多" },
  playfulness:  { name: "活泼度", low: "正经", mid: "轻松", high: "搞怪" },
  initiative:   { name: "主动性", low: "被动", mid: "配合", high: "主动" },
  carefulness:  { name: "谨慎度", low: "大胆", mid: "平衡", high: "谨慎" },
  goal_focus:   { name: "目标感", low: "佛系", mid: "均衡", high: "效率" },
};

function dimEndpoint(dim: PreferenceDimension, value: number): string {
  if (value <= 2) return DIM_LABELS[dim].low;
  if (value >= 4) return DIM_LABELS[dim].high;
  return DIM_LABELS[dim].mid;
}

function InviteShareCard({
  agentName = "小枫",
  appearance = DEFAULT_APPEARANCE,
  inviteCode,
  inviteCodeState = "available",
  relationshipType,
  styleSummary,
  corePreferences,
  customFlavor = [],
  backgroundSrc = BACKGROUND_PRESETS[0].src,
}: InviteShareCardProps, ref: ForwardedRef<InviteShareCardHandle>) {
  const cardRef = useRef<HTMLDivElement>(null);
  const charCanvasRef = useRef<HTMLCanvasElement>(null);
  const qrCanvasRef = useRef<HTMLCanvasElement>(null);
  const [spritesLoaded, setSpritesLoaded] = useState(false);

  const hasInviteCode = Boolean(inviteCode?.trim());
  const isExhausted = inviteCodeState === "exhausted";
  const qrUrl = hasInviteCode ? `${SITE_URL}/register?ref=${inviteCode}` : "";
  const flavors = customFlavor.slice(0, 3);

  useEffect(() => { loadAllSprites().then(() => setSpritesLoaded(true)); }, []);

  useEffect(() => {
    const canvas = qrCanvasRef.current;
    if (!canvas) return;
    if (!qrUrl) {
      const ctx = canvas.getContext("2d");
      ctx?.clearRect(0, 0, canvas.width, canvas.height);
      return;
    }
    QRCode.toCanvas(canvas, qrUrl, {
      width: 52,
      margin: 0,
      errorCorrectionLevel: "M",
      color: { dark: "#3e2723", light: "#fefdfb" },
    }).catch(() => {});
  }, [qrUrl]);

  useEffect(() => {
    if (!spritesLoaded) return;
    const sprites = new Map<SpriteKey, HTMLImageElement>();
    for (const k of SPRITE_KEYS) {
      const img = getCached(k);
      if (img) sprites.set(k, img);
    }
    const result = renderFarmer(appearance, sprites);
    const canvas = charCanvasRef.current;
    if (!canvas) return;
    const scale = 6;
    canvas.width = Math.round(result.width * scale);
    canvas.height = Math.round(result.height * scale);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(result, 0, 0, canvas.width, canvas.height);
  }, [appearance, spritesLoaded]);

  const captureCard = useCallback(async () => {
    if (!cardRef.current) return null;
    const html2canvas = (await import("html2canvas")).default;
    return html2canvas(cardRef.current, { scale: 3, backgroundColor: null, useCORS: true });
  }, []);

  const saveImage = useCallback(async () => {
    try {
      const canvas = await captureCard();
      if (!canvas) return false;
      const a = document.createElement("a");
      a.download = `star-coplay-invite-${Date.now()}.png`;
      a.href = canvas.toDataURL("image/png");
      a.click();
      return true;
    } catch {
      return false;
    }
  }, [captureCard]);

  useImperativeHandle(ref, () => ({ saveImage }), [saveImage]);

  return (
    <div>
      <div style={{ width: "100%", maxWidth: W, margin: "0 auto" }}>

        {/* ═══ z-0: Outer container — light gradient, capture target ═══ */}
        <div
          ref={cardRef}
          style={{
            width: W, height: H,
            position: "relative", overflow: "hidden",
            fontFamily: PX,
            borderRadius: 20,
            background: "linear-gradient(160deg, #eef6f1 0%, #faf8f0 40%, #f6f1e8 100%)",
          }}
        >

          {/* ═══ z-5: Polaroid card — white, tilted, shadowed ═══ */}
          <div style={{
            position: "absolute",
            top: 30, left: 25, right: 25, bottom: 55,
            background: "#fefdfb",
            borderRadius: 6,
            boxShadow: "0 20px 44px rgba(0,0,0,0.08), 0 6px 14px rgba(0,0,0,0.04)",
            transform: "rotate(-1.5deg)",
            overflow: "hidden",
            zIndex: 5,
          }}>

            {/* ── Scene area (top ~40%) ── */}
            <div style={{
              height: 220,
              position: "relative",
              backgroundImage: `url(${backgroundSrc})`,
              backgroundSize: "cover", backgroundPosition: "center",
            }}>
              {/* Soft white overlay — faded polaroid photo feel */}
              <div style={{
                position: "absolute", inset: 0,
                background: "rgba(255,255,255,0.2)",
              }} />
              {/* Fade to white at bottom */}
              <div style={{
                position: "absolute", bottom: 0, left: 0, right: 0, height: 40,
                background: "linear-gradient(to bottom, transparent, #fefdfb)",
              }} />

              {/* Character sprite */}
              <div style={{
                position: "absolute",
                bottom: 14, left: "50%", transform: "translateX(-50%)",
              }}>
                <canvas
                  ref={charCanvasRef}
                  width={16} height={32}
                  style={{
                    width: 96, height: 192,
                    imageRendering: "pixelated",
                    display: "block",
                  }}
                />
                {/* Baseline shadow — figurine stand feel */}
                <div style={{
                  width: 70, height: 5,
                  margin: "-2px auto 0",
                  borderRadius: "50%",
                  background: "radial-gradient(ellipse, rgba(0,0,0,0.1) 0%, transparent 70%)",
                }} />
              </div>
            </div>

            {/* ── White content area ── */}
            <div style={{ padding: "12px 20px 14px" }}>

              {/* Name — large, bold */}
              <div style={{
                fontFamily: PX, fontSize: 18,
                color: "#2c2c2c",
                letterSpacing: 1,
              }}>
                {agentName}
              </div>

              {/* Relationship type subtitle */}
              {relationshipType && (
                <div style={{
                  fontFamily: PX, fontSize: 7,
                  color: "#c0b0a0", marginTop: 2, marginBottom: 10,
                  display: "flex", alignItems: "center", gap: 2,
                }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src="/assets/Stardrop.png"
                    alt=""
                    style={{ width: 8, height: 8, imageRendering: "pixelated", flexShrink: 0 }}
                  />
                  <span>{relationshipType}</span>
                </div>
              )}

              {/* 6-dim personality mini bars */}
              {corePreferences && (
                <div style={{ marginBottom: 8 }}>
                  {PREFERENCE_DIMENSIONS.map((dim) => {
                    const val = corePreferences[dim];
                    const pct = ((val - 1) / 4) * 100; // 1-5 → 0-100%
                    return (
                      <div key={dim} style={{
                        display: "grid",
                        gridTemplateColumns: "42px 1fr 30px",
                        gap: 4, alignItems: "center",
                        marginBottom: 2,
                      }}>
                        <span style={{ fontFamily: PX, fontSize: 5, color: "#8b7b6b" }}>
                          {DIM_LABELS[dim].name}
                        </span>
                        <div style={{
                          height: 4, borderRadius: 2,
                          background: "#ece5d8",
                          overflow: "hidden",
                        }}>
                          <div style={{
                            width: `${pct}%`, height: "100%",
                            borderRadius: 2,
                            background: "linear-gradient(90deg, #c4a87c, #a08060)",
                            transition: "width 0.3s ease",
                          }} />
                        </div>
                        <span style={{ fontFamily: PX, fontSize: 5, color: "#a09080", textAlign: "right" }}>
                          {dimEndpoint(dim, val)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Trait cards — 3-column row, label + description stacked */}
              <div style={{
                display: "flex", gap: 6,
                marginBottom: 10,
              }}>
                {flavors.map((f, i) => (
                  <div key={i} style={{
                    flex: 1,
                    display: "flex", flexDirection: "column", alignItems: "center",
                    padding: "8px 4px",
                    borderRadius: 8,
                    background: "#f5f0e8",
                    border: "1px solid #e8e0d4",
                    textAlign: "center",
                  }}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={PILL_ICONS[i % PILL_ICONS.length]}
                      alt=""
                      crossOrigin="anonymous"
                      style={{ width: 16, height: 16, objectFit: "contain", marginBottom: 3 }}
                    />
                    <span style={{
                      fontFamily: PX, fontSize: 9,
                      color: "#3e2723", fontWeight: "bold",
                      display: "block",
                    }}>
                      {f.label}
                    </span>
                    {f.description && (
                      <span style={{
                        fontFamily: PX, fontSize: 5,
                        color: "#8b7b6b",
                        display: "block", marginTop: 2,
                        lineHeight: 1.4,
                      }}>
                        {f.description}
                      </span>
                    )}
                  </div>
                ))}
              </div>

              {/* Style summary / signature quote */}
              <p style={{
                fontFamily: PX, fontSize: 7,
                color: "#c8b8a8",
                margin: "0 0 24px", lineHeight: 1.5,
              }}>
                &ldquo;{styleSummary
                  ? styleSummary.replace(/^你是/, "")
                  : "别找我，周五晚上我只在餐吧"
                }&rdquo;
              </p>

              {/* ── Footer: grid left-QR / right-CTA ── */}
              <div style={{
                display: "grid",
                gridTemplateColumns: "auto 1fr",
                gap: 14, alignItems: "end",
              }}>
                {/* QR side */}
                <div style={{ textAlign: "center" }}>
                  <div style={{
                    padding: 4,
                    border: "1px solid #e0d8c8",
                    borderRadius: 4,
                    display: "inline-block",
                  }}>
                    {hasInviteCode ? (
                      <canvas
                        ref={qrCanvasRef}
                        width={52}
                        height={52}
                        aria-label="StarCoplay invite QR code"
                        style={{ width: 52, height: 52, display: "block" }}
                      />
                    ) : isExhausted ? (
                      <div
                        style={{
                          width: 52,
                          height: 52,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          background: "#f5f0e8",
                          color: "#8b7b6b",
                          fontFamily: PX,
                          fontSize: 6,
                          lineHeight: 1.4,
                          textAlign: "center",
                        }}
                      >
                        名额
                        <br />
                        已用完
                        </div>
                    ) : (
                      <div
                        style={{
                          width: 52,
                          height: 52,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          background: "#f5f0e8",
                          color: "#8b7b6b",
                          fontFamily: PX,
                          fontSize: 6,
                          lineHeight: 1.4,
                          textAlign: "center",
                        }}
                      >
                        暂无
                        <br />
                        邀请码
                      </div>
                    )}
                  </div>
                  <div style={{
                    fontFamily: PX, fontSize: 5,
                    color: "#b8a898", marginTop: 3,
                  }}>
                    {hasInviteCode ? "扫码领养" : isExhausted ? "名额已用完" : "暂无邀请码"}
                  </div>
                </div>

                {/* CTA side — right-aligned, magazine-style */}
                <div style={{ textAlign: "right" }}>
                  <div style={{
                    fontFamily: PX, fontSize: 10,
                    color: "#3e2723", lineHeight: 1.7,
                  }}>
                    想要一个帮你浇水的
                    <br />
                    AI 搭子吗？
                  </div>
                  <div style={{
                    fontFamily: PX, fontSize: 6,
                    color: "#c0b0a0", marginTop: 3,
                  }}>
                    StarCoPlay · 免费领养
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* ═══ z-15: Pin decoration — overlaps polaroid top-left ═══ */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/assets/Stardrop.png"
            alt=""
            crossOrigin="anonymous"
            style={{
              position: "absolute",
              top: 16, left: 36,
              width: 26, zIndex: 15,
              transform: "rotate(8deg)",
            }}
          />

          {/* ═══ z-20: Mascot — breaks out of polaroid bottom-right ═══ */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/assets/icons/Junimo_Icon.png"
            alt=""
            crossOrigin="anonymous"
            style={{
              position: "absolute",
              bottom: 28, right: 18,
              width: 48, zIndex: 20,
              transform: "rotate(6deg)",
            }}
          />
        </div>
      </div>

    </div>
  );
}

export default forwardRef(InviteShareCard);
