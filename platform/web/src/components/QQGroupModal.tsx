"use client";

import { useState } from "react";

const QQ_GROUP = "564214272";

export function QQGroupInfo({ onCopy }: { onCopy?: () => void }) {
  const [copied, setCopied] = useState(false);
  function handleCopy() {
    navigator.clipboard.writeText(QQ_GROUP);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
    onCopy?.();
  }
  return (
    <div className="flex items-center gap-2 justify-center flex-wrap">
      <span className="font-mono text-sm font-bold text-stardew-brown">{QQ_GROUP}</span>
      <button
        type="button"
        onClick={handleCopy}
        className="stardew-btn px-3 py-1 rounded text-[10px] font-bold"
      >
        {copied ? "已复制 ✓" : "复制群号"}
      </button>
    </div>
  );
}

export function QQGroupModal({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm px-4"
      onClick={onClose}
    >
      <div
        className="parchment rounded-2xl p-6 max-w-sm w-full text-center"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-3xl mb-3">💬</div>
        <h3 className="font-[family-name:var(--font-pixel)] text-xs text-stardew-brown mb-2">
          加入 QQ 内测群
        </h3>
        <p className="text-[10px] text-stardew-brown/70 mb-4">
          扫码或搜索群号加入我们的内测群，获取最新动态和技术支持
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
