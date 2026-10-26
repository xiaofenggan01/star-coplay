"use client";

import Image from "next/image";
import { QQGroupInfo } from "@/components/QQGroupModal";

/**
 * ContactModal — 首页「联系我们」弹窗。
 *
 * 展示 QQ 内测群二维码 + 群号（复用 QQGroupInfo 的复制逻辑）。
 */

export function ContactModal({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm px-4"
      onClick={onClose}
    >
      <div
        className="parchment rounded-2xl p-6 max-w-sm w-full text-center relative"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          className="absolute top-3 right-3 w-8 h-8 rounded-full bg-stardew-brown/10 hover:bg-stardew-brown/20 flex items-center justify-center transition-colors"
          aria-label="关闭"
        >
          <svg
            className="w-4 h-4 text-stardew-brown"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>

        <Image
          src="/assets/icons/Speech_bubble.png"
          alt=""
          width={40}
          height={40}
          className="pixel-art mx-auto mb-3"
        />
        <h3 className="font-[family-name:var(--font-pixel)] text-xs text-stardew-brown mb-2">
          联系我们
        </h3>
        <p className="text-[10px] text-stardew-brown/70 mb-4">
          扫码或搜索群号加入 QQ 内测群，获取最新动态和技术支持
        </p>

        <div className="bg-white/60 rounded-lg p-4 mb-4">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/assets/qq-group-qr.png"
            alt="QQ 内测群二维码"
            className="w-48 mx-auto mb-3 rounded-lg"
          />
          <p className="text-[10px] text-stardew-brown/60 mb-2">QQ 群号</p>
          <QQGroupInfo />
        </div>

        <button
          onClick={onClose}
          className="stardew-btn px-5 py-2 rounded-lg text-xs font-bold"
        >
          关闭
        </button>
      </div>
    </div>
  );
}
