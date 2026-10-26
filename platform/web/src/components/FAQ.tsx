"use client";

import { useState } from "react";
import Image from "next/image";

const faqs: Array<{ question: string; answer: string; icon: string; tag?: string }> = [
  {
    question: "电脑配置有什么要求？",
    answer:
      "建议 16GB 内存，需要同时运行两个星露谷实例（你的 + AI 的）。支持 Windows 和 macOS，需要星露谷 1.6.x 版本和最新版 SMAPI。",
    icon: "/assets/icons/Controls_Icon.png",
  },
  {
    question: "为什么要开两个游戏？",
    answer:
      "AI 搭子是以 Player 2 的身份加入星露谷联机的，就像一个真人朋友加入你的游戏一样。所以需要一个游戏实例给 AI 角色使用，我们提供了一键启动脚本简化操作。",
    icon: "/assets/icons/Multiplayer_Icon.png",
  },
  {
    question: "API Key 大概要花多少钱？",
    answer:
      "取决于你选择的模型。推荐使用 DeepSeek，大约 ¥0.5/小时；GPT-4o 大约 ¥3-5/小时。内测期间平台服务免费，AI 调用费用自付。",
    icon: "/assets/icons/Golden_Walnut.png",
  },
  {
    question: "能自定义搭子的性格吗？",
    answer:
      "支持预设人格快速开玩，也支持自由编写背景故事深度定制。你可以塑造独一无二的 AI 伙伴。",
    icon: "/assets/icons/The_Player_Icon.png",
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
  {
    question: "数据安全吗？",
    answer:
      "AI 完全在你的本地电脑运行，游戏数据不会上传。内测期间会采集使用时长、AI 交互等数据用于改进产品，你可以在配置中关闭对话内容采集。",
    icon: "/assets/icons/Secret_Note_Icon.png",
  },
  {
    question: "遇到问题怎么办？",
    answer:
      "Setup UI 中可以查看实时日志定位问题。也欢迎加入微信社群直接和开发者交流，或在 GitHub Issues 提交 Bug 报告。",
    icon: "/assets/icons/Book_of_Mysteries.png",
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
