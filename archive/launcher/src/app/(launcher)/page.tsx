"use client";

import { useState, useEffect, useRef } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";

const scanItems = [
  { label: "游戏版本", key: "game" },
  { label: "SMAPI 模组加载器", key: "smapi" },
  { label: "StarCoPlay Mod", key: "mod" },
  { label: "平台 Token", key: "token" },
];

export default function HomePage() {
  const router = useRouter();
  const [scanResults, setScanResults] = useState<{ label: string; ok: boolean }[]>([]);
  const [scanning, setScanning] = useState(true);
  const countRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    countRef.current = 0;
    timerRef.current = setInterval(() => {
      const idx = countRef.current;
      if (idx < scanItems.length) {
        setScanResults((prev) => [...prev, { label: scanItems[idx].label, ok: true }]);
        countRef.current = idx + 1;
      } else {
        if (timerRef.current) clearInterval(timerRef.current);
        setScanning(false);
      }
    }, 800);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const allPassed = !scanning && scanResults.length === scanItems.length;

  return (
    <section className="bg-stardew-cream py-20 min-h-[80vh] flex items-center">
      <div className="max-w-lg mx-auto px-4 w-full animate-fade-in-up">
        <div className="parchment rounded-2xl p-8 text-center">
          <div className="animate-float mb-4">
            <Image src="/icons/Achievements_Icon.png" alt="" width={48} height={48} className="pixel-art mx-auto" />
          </div>

          {scanning ? (
            <>
              <h2 className="font-[family-name:var(--font-pixel)] text-base text-stardew-brown mb-6">
                环境检测
              </h2>
              <div className="w-full h-3 bg-stardew-brown/10 rounded-full overflow-hidden mb-4">
                <div
                  className="h-full bg-gradient-to-r from-stardew-gold to-stardew-sunset rounded-full transition-all duration-500"
                  style={{ width: `${Math.round((scanResults.length / scanItems.length) * 100)}%` }}
                />
              </div>
              <p className="text-sm text-stardew-brown/50 mb-4">
                {Math.round((scanResults.length / scanItems.length) * 100)}%
              </p>
              {scanResults.length < scanItems.length && (
                <p className="text-stardew-blue text-sm mb-4">
                  正在检测 {scanItems[scanResults.length].label}...
                </p>
              )}
              <div className="bg-stardew-cream rounded-xl p-4 max-h-[200px] overflow-y-auto text-left">
                {scanResults.map((r, i) => (
                  <div key={i} className="flex items-center gap-2 py-1 text-sm">
                    <span className={r.ok ? "text-stardew-green" : "text-stardew-red"}>
                      {r.ok ? "✓" : "✗"}
                    </span>
                    <span className="text-stardew-brown/70">{r.label}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              <h2 className="font-[family-name:var(--font-pixel)] text-base text-stardew-brown mb-4">
                环境就绪！
              </h2>
              <div className="bg-stardew-cream rounded-xl p-4 mb-6 text-left space-y-2">
                {scanResults.map((r, i) => (
                  <p key={i} className="text-sm">
                    <span className={r.ok ? "text-stardew-green" : "text-stardew-red"}>
                      {r.ok ? "✓" : "✗"}
                    </span>{" "}
                    {r.label}: {r.ok ? "已就绪" : "未通过"}
                  </p>
                ))}
              </div>
              <button
                onClick={() => {
                  localStorage.setItem("stardew_launched", "true");
                  router.push("/monitor");
                }}
                disabled={!allPassed}
                className={`stardew-btn inline-flex px-8 py-4 rounded-xl text-lg font-bold ${
                  allPassed ? "animate-launch-pulse" : "opacity-50 cursor-not-allowed"
                }`}
              >
                <Image src="/icons/Golden_Walnut.png" alt="" width={20} height={20} className="pixel-art" />
                唤醒你的搭子
              </button>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
