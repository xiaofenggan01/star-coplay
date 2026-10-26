"use client";

import Link from "next/link";
import { QQGroupInfo } from "@/components/QQGroupModal";

/**
 * TokenInfoModal — 说明「内测邀请码 / 平台 Token」的获取逻辑。
 *
 * 关键逻辑：
 *   - 加入 QQ 群可获取内测邀请码
 *   - 用邀请码注册 → 内测身份 → 注册完成后显示平台 Token
 *   - 无邀请码注册 → 仅游客身份，不会显示 Token
 */
export function TokenInfoModal({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm px-4 py-6"
      onClick={onClose}
    >
      <div
        className="parchment rounded-2xl p-6 max-w-md w-full relative max-h-full overflow-y-auto"
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

        <h3 className="font-[family-name:var(--font-pixel)] text-xs text-stardew-brown mb-4 text-center">
          如何获取平台 Token？
        </h3>

        {/* 三步逻辑 */}
        <ol className="space-y-3 mb-5">
          <li className="flex gap-3">
            <span className="flex-shrink-0 w-6 h-6 wood-panel rounded-full flex items-center justify-center text-white text-[11px] font-bold">
              1
            </span>
            <p className="text-xs text-stardew-brown/80 leading-relaxed">
              加入 QQ 内测群，获取一枚<strong>内测邀请码</strong>。
            </p>
          </li>
          <li className="flex gap-3">
            <span className="flex-shrink-0 w-6 h-6 wood-panel rounded-full flex items-center justify-center text-white text-[11px] font-bold">
              2
            </span>
            <p className="text-xs text-stardew-brown/80 leading-relaxed">
              用<strong>内测邀请码</strong>注册，即成为<strong className="text-stardew-green">内测用户</strong>。注册完成后页面会显示你的专属<strong>平台 Token</strong>，可随时在「我的」页面查看复制。
            </p>
          </li>
          <li className="flex gap-3">
            <span className="flex-shrink-0 w-6 h-6 wood-panel rounded-full flex items-center justify-center text-white text-[11px] font-bold">
              3
            </span>
            <p className="text-xs text-stardew-brown/80 leading-relaxed">
              若<strong>没有邀请码</strong>直接注册，则只会成为<strong className="text-stardew-brown/60">游客身份</strong>，<strong>不会</strong>显示平台 Token。你可以稍后在「我的」页面补充邀请码，激活内测身份。
            </p>
          </li>
        </ol>

        {/* QQ 群二维码 */}
        <div className="bg-white/60 rounded-xl p-4 mb-4 text-center">
          <p className="text-xs font-bold text-stardew-brown mb-1">加入 QQ 群领取邀请码</p>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/assets/qq-group-qr.png"
            alt="QQ 内测群二维码"
            className="w-40 mx-auto my-3 rounded-lg"
          />
          <p className="text-[10px] text-stardew-brown/60 mb-2">QQ 群号</p>
          <QQGroupInfo />
        </div>

        <Link
          href="/register"
          className="stardew-btn block w-full py-3 rounded-lg font-bold text-sm text-center"
        >
          前往注册 →
        </Link>
      </div>
    </div>
  );
}
