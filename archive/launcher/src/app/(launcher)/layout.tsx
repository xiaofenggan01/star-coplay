"use client";

import { useState, useEffect } from "react";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";

const gatedPaths = ["/monitor", "/config"];
const connectedPaths = ["/dashboard"];

export default function LauncherLayout({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [error, setError] = useState("");
  const [mounted, setMounted] = useState(false);
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    const saved = localStorage.getItem("stardew_token");
    if (saved) setToken(saved);
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted || !token) return;
    const launched = localStorage.getItem("stardew_launched");
    if (!launched && gatedPaths.includes(pathname)) {
      router.replace("/");
      return;
    }
    const connected = localStorage.getItem("stardew_connected");
    if (!connected && connectedPaths.includes(pathname)) {
      router.replace("/monitor");
    }
  }, [mounted, token, pathname, router]);

  const submitToken = () => {
    if (!inputValue.trim()) {
      setError("请输入 Token 密钥");
      return;
    }
    localStorage.setItem("stardew_token", inputValue.trim());
    setToken(inputValue.trim());
  };

  if (!mounted) {
    return <div className="min-h-screen bg-stardew-cream" />;
  }

  if (!token) {
    return (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 px-6">
        <div className="parchment rounded-2xl p-8 max-w-md w-full animate-fade-in-up">
          <div className="animate-float mb-4 text-center">
            <Image
              src="/icons/Golden_Walnut.png"
              alt=""
              width={48}
              height={48}
              className="pixel-art mx-auto"
            />
          </div>

          <h2 className="font-[family-name:var(--font-pixel)] text-base text-center text-stardew-brown mb-2">
            输入 Token 密钥
          </h2>
          <p className="text-sm text-stardew-brown/70 mb-4 text-center">
            请输入你的平台 Token 以访问 Stardew AI CoPlay
          </p>

          <input
            className="stardew-input mb-2"
            placeholder="scp_..."
            value={inputValue}
            onChange={(e) => { setInputValue(e.target.value); setError(""); }}
            onKeyDown={(e) => e.key === "Enter" && submitToken()}
          />
          {error && <p className="text-stardew-red text-sm mb-2">{error}</p>}

          <button
            className="stardew-btn w-full px-4 py-3 rounded-xl font-bold mt-2"
            onClick={submitToken}
          >
            确认
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-stardew-cream">
      <Nav />
      <main className="flex-1 pt-16">
        {children}
      </main>
      <Footer />
    </div>
  );
}
