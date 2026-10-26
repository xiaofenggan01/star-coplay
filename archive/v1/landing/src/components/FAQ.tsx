"use client";

import { useState } from "react";
import Image from "next/image";

const faqs = [
  {
    question: "未来会支持其他游戏吗？",
    answer:
      "当前专注星露谷，但架构设计上支持扩展到其他 Mod 友好的游戏。我们计划支持双人成行、博德之门等双人/多人游戏，让你的搭子陪你探索更多世界。",
    tag: "即将开放",
    icon: "/assets/icons/Controls_Icon.png",
  },
  {
    question: "能自定义搭子的外形和性格吗？",
    answer:
      "支持预设人格快速开玩，也支持自由编写背景故事深度定制。未来还将开放外形自定义，打造独一无二的专属搭子。",
    icon: "/assets/icons/The_Player_Icon.png",
  },
  {
    question: "能从 ChatGPT / Claude 导入记忆吗？",
    answer:
      "计划支持从外部 AI 平台导入核心人设与对话记忆，让你在其他平台培养的 AI 伙伴无缝迁移到游戏世界里——从文字搭子变成真正在场的搭子。",
    tag: "即将开放",
    icon: "/assets/icons/Bundle_Icon.png",
  },
  {
    question: "需要一直联网吗？",
    answer:
      "AI 搭子需要联网调用大模型进行思考，但游戏本身可以正常离线运行。断网时搭子会暂停决策，重新联网后自动恢复。",
    icon: "/assets/icons/Satellite_Dish.png",
  },
  {
    question: "会影响游戏性能吗？",
    answer:
      "不会。AI 在后台独立进程运行，通过文件通信，不修改游戏代码，不影响帧率。和普通 Mod 一样轻量。",
    icon: "/assets/icons/SMAPI_mascot.png",
  },
];

export default function FAQ() {
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  return (
    <section id="faq" className="bg-stardew-green/5 py-20">
      <div className="max-w-3xl mx-auto px-4">
        <h2 className="font-[family-name:var(--font-pixel)] text-xl md:text-2xl text-center text-stardew-brown mb-4">
          常见问题
        </h2>
        <p className="text-center text-stardew-brown/70 mb-12">
          关于 Star CoPlay 你可能想知道的
        </p>

        <div className="space-y-3">
          {faqs.map((faq, i) => (
            <div key={i} className="parchment rounded-xl overflow-hidden">
              <button
                className="w-full flex items-center gap-3 px-5 py-4 text-left"
                onClick={() => setOpenIndex(openIndex === i ? null : i)}
                aria-expanded={openIndex === i}
              >
                <Image
                  src={faq.icon}
                  alt=""
                  width={24}
                  height={24}
                  className="pixel-art flex-shrink-0"
                />
                <span className="font-bold text-stardew-brown text-sm flex-1">
                  {faq.question}
                </span>
                {faq.tag && (
                  <span className="bg-stardew-sunset text-white text-[10px] font-bold px-2 py-0.5 rounded-full flex-shrink-0">
                    {faq.tag}
                  </span>
                )}
                <svg
                  className={`w-4 h-4 text-stardew-brown/40 flex-shrink-0 transition-transform duration-200 ${
                    openIndex === i ? "rotate-180" : ""
                  }`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M19 9l-7 7-7-7"
                  />
                </svg>
              </button>

              <div
                className={`overflow-hidden transition-all duration-200 ${
                  openIndex === i ? "max-h-40 opacity-100" : "max-h-0 opacity-0"
                }`}
              >
                <p className="px-5 pb-4 text-sm text-stardew-brown/70 leading-relaxed pl-12">
                  {faq.answer}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
