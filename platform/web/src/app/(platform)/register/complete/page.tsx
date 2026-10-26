"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";

export default function RegisterCompletePage() {
  const router = useRouter();

  useEffect(() => {
    const timer = window.setTimeout(() => router.replace("/me"), 800);
    return () => window.clearTimeout(timer);
  }, [router]);

  return (
    <div className="stardew-sky flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-8 md:min-h-[calc(100dvh-4rem)]">
      <div className="parchment w-full max-w-md rounded-2xl p-8 text-center animate-fade-in-up">
        <Image
          src="/logo-new.png"
          alt="StarCoPlay"
          width={48}
          height={48}
          className="mx-auto mb-3"
        />
        <h1 className="mb-2 font-[family-name:var(--font-pixel)] text-sm">
          注册已完成
        </h1>
        <p className="text-xs leading-relaxed text-stardew-brown/70">
          正在进入我的页面。没有邀请码的账号会以游客身份进入，你可以在我的页面补充邀请码开通内测资源。
        </p>
        <button
          type="button"
          onClick={() => router.replace("/me")}
          className="stardew-btn mt-5 w-full rounded-lg py-3 text-sm font-bold"
        >
          进入我的页面
        </button>
      </div>
    </div>
  );
}
