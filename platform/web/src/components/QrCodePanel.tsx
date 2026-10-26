"use client";

import { useEffect, useState } from "react";
import QRCode from "qrcode";

interface QrCodePanelProps {
  value: string;
  label?: string;
  className?: string;
  size?: number;
}

export default function QrCodePanel({
  value,
  label = "路演扫码入口",
  className = "",
  size = 224,
}: QrCodePanelProps) {
  const [dataUrl, setDataUrl] = useState("");

  useEffect(() => {
    let cancelled = false;
    if (!value) {
      setDataUrl("");
      return;
    }

    QRCode.toDataURL(value, {
      width: size,
      margin: 2,
      errorCorrectionLevel: "M",
      color: {
        dark: "#3b2417",
        light: "#fffaf0",
      },
    })
      .then((url) => {
        if (!cancelled) setDataUrl(url);
      })
      .catch(() => {
        if (!cancelled) setDataUrl("");
      });

    return () => {
      cancelled = true;
    };
  }, [size, value]);

  return (
    <div className={`flex flex-col items-center ${className}`}>
      <div
        className="bg-[#fffaf0] border-4 border-stardew-wood/30 rounded-xl p-3 shadow-sm"
        style={{ width: size + 28, minHeight: size + 28 }}
      >
        {dataUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={dataUrl}
            alt={label}
            width={size}
            height={size}
            className="block h-auto w-full"
          />
        ) : (
          <div
            className="flex items-center justify-center text-[10px] text-stardew-brown/55"
            style={{ width: size, height: size }}
          >
            生成中...
          </div>
        )}
      </div>
    </div>
  );
}
