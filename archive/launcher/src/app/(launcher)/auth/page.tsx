"use client";

import { useState, useEffect } from "react";
import Image from "next/image";
import Link from "next/link";

export default function AuthPage() {
  const [savedToken, setSavedToken] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("stardew_token");
    if (token) setSavedToken(token);
  }, []);

  const copyToken = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      alert("复制失败，请手动复制");
    }
  };

  const logout = () => {
    localStorage.removeItem("stardew_token");
    localStorage.removeItem("stardew_launched");
    localStorage.removeItem("stardew_connected");
    window.location.reload();
  };

  return (
    <section className="bg-stardew-cream py-20 min-h-[80vh] flex items-center">
      <div className="max-w-lg mx-auto px-4 w-full animate-fade-in-up">
        <div className="parchment rounded-2xl p-8 text-center">
          <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-stardew-gold/20 border-2 border-stardew-gold flex items-center justify-center animate-float">
            <Image src="/icons/Abigail_Icon.png" alt="" width={40} height={40} className="pixel-art" />
          </div>

          <h2 className="font-[family-name:var(--font-pixel)] text-base text-stardew-brown mb-2">
            确认账户
          </h2>

          <div className="bg-stardew-cream rounded-xl p-4 mb-6 text-left">
            <p className="text-xs text-stardew-brown/50 mb-1">你的 Token:</p>
            <div className="flex items-center gap-2">
              <p className="text-stardew-gold text-sm break-all flex-1 select-all font-mono">
                {savedToken || "未找到 Token"}
              </p>
              <button
                className="stardew-btn px-3 py-1 rounded text-xs"
                onClick={() => copyToken(savedToken)}
              >
                {copied ? "已复制！" : "复制"}
              </button>
            </div>
          </div>

          <div className="flex gap-3">
            <Link href="/setup" className="stardew-btn flex-1 px-4 py-3 rounded-xl font-bold text-center">
              登入
            </Link>
            <button
              className="flex-1 px-4 py-3 rounded-xl font-bold bg-stardew-red text-white hover:bg-stardew-red/90 transition-colors"
              onClick={logout}
            >
              退出登录
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
