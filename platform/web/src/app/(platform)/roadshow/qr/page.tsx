"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import QrCodePanel from "@/components/QrCodePanel";

export default function RoadshowQrPage() {
  const [roadshowUrl, setRoadshowUrl] = useState("/roadshow?r=main");

  useEffect(() => {
    setRoadshowUrl(`${window.location.origin}/roadshow?r=main`);
  }, []);

  return (
    <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] px-4 py-8 flex items-center justify-center">
      <main className="parchment rounded-2xl p-5 md:p-7 w-full max-w-lg">
        <div className="mb-6 text-center">
          <p className="text-[10px] font-bold text-stardew-brown/60 mb-2">
            ROADSHOW QR
          </p>
          <h1 className="font-[family-name:var(--font-pixel)] text-base text-stardew-brown mb-3">
            现场提问入口
          </h1>
          <p className="text-xs text-stardew-brown/70 leading-6">
            把这个页面投到现场屏幕或单独打开，观众扫码后进入路演提问页。
          </p>
        </div>

        <QrCodePanel value={roadshowUrl} size={260} className="mb-5" />

        <p className="rounded-lg bg-white/55 border border-stardew-wood/20 px-3 py-2 text-[11px] text-stardew-brown/70 break-all font-mono">
          {roadshowUrl}
        </p>

        <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Link
            href="/roadshow?r=main"
            className="stardew-btn text-center rounded-xl px-4 py-3 text-sm font-bold"
          >
            打开提问页
          </Link>
          <Link
            href="/"
            className="text-center rounded-xl px-4 py-3 text-sm font-bold border-2 border-stardew-wood/30 text-stardew-brown bg-white/45 hover:bg-white/70 transition-colors"
          >
            返回首页
          </Link>
        </div>
      </main>
    </div>
  );
}
