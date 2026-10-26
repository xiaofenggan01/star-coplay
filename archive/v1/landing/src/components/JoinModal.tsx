"use client";

import { createContext, useContext, useState, ReactNode } from "react";
import Image from "next/image";

const ModalContext = createContext<{
  open: boolean;
  setOpen: (v: boolean) => void;
}>({ open: false, setOpen: () => {} });

export function useJoinModal() {
  return useContext(ModalContext);
}

export function JoinModalProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);

  return (
    <ModalContext.Provider value={{ open, setOpen }}>
      {children}
      {open && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setOpen(false)}
        >
          <div
            className="parchment rounded-2xl p-8 max-w-sm mx-4 text-center relative"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Close button */}
            <button
              onClick={() => setOpen(false)}
              className="absolute top-3 right-3 w-8 h-8 rounded-full bg-stardew-brown/10 hover:bg-stardew-brown/20 flex items-center justify-center transition-colors"
              aria-label="关闭"
            >
              <svg className="w-4 h-4 text-stardew-brown" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>

            <Image
              src="/assets/icons/Junimo_Icon.png"
              alt=""
              width={48}
              height={48}
              className="pixel-art mx-auto mb-4"
            />

            <h3 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-2">
              加入 Player 2 计划
            </h3>
            <p className="text-stardew-brown/70 text-xs mb-6 leading-relaxed">
              扫码加入微信群，获取最新 Mod 下载链接、<br />
              参与内测、和开发者直接交流
            </p>

            {/* QR Code */}
            <div className="bg-white rounded-xl p-3 inline-block mb-4">
              <Image
                src="/assets/wechat-qr.jpg"
                alt="微信群二维码"
                width={200}
                height={200}
                className="rounded"
              />
            </div>

            <p className="text-stardew-brown/50 text-[10px]">
              微信扫一扫，加入星露谷 AI 搭子社区
            </p>
          </div>
        </div>
      )}
    </ModalContext.Provider>
  );
}

/** CTA button that opens the join modal */
export function JoinButton({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  const { setOpen } = useJoinModal();
  return (
    <button onClick={() => setOpen(true)} className={className}>
      {children}
    </button>
  );
}
