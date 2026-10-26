"use client";

import Image from "next/image";

export default function EmailConfirmedPage() {
  return (
    <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] flex items-center justify-center px-4 py-8">
      <div className="parchment rounded-2xl p-8 max-w-md w-full text-center animate-fade-in-up">
        <Image
          src="/logo-new.png"
          alt="StarCoPlay"
          width={48}
          height={48}
          className="mx-auto mb-4"
        />
        <div className="text-3xl mb-3">✅</div>
        <h1 className="font-[family-name:var(--font-pixel)] text-sm mb-2">
          邮箱已验证
        </h1>
        <p className="text-xs text-stardew-brown/80 leading-relaxed mb-6">
          你的邮箱已成功验证，现在可以登录了。
        </p>
        <a
          href="/login"
          className="stardew-btn py-2.5 px-6 rounded-lg font-bold text-xs text-center no-underline inline-block"
        >
          前往登录
        </a>
      </div>
    </div>
  );
}
