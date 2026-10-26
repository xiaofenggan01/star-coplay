"use client";

import { useState } from "react";
import Image from "next/image";

interface Config {
  gamePath: string;
  smapiVersion: string;
  aiEndpoint: string;
  platformToken: string;
  personality: string;
  companionName: string;
  backstory: string;
  speakingStyles: string[];
}

const personalities = [
  { id: "gentle", name: "温柔", subtitle: "蒲公英", icon: "/icons/Dandelion.png", desc: "轻声细语，体贴入微" },
  { id: "lively", name: "活泼", subtitle: "能量饮料", icon: "/icons/Energy_Drink.png", desc: "充满活力，积极向上" },
  { id: "scholar", name: "学者", subtitle: "神秘之书", icon: "/icons/Book_of_Mysteries.png", desc: "博学多识，善于分析" },
  { id: "adventure", name: "冒险", subtitle: "锈剑", icon: "/icons/Rusty_Sword.png", desc: "勇敢无畏，探索未知" },
];

const speakingStyleOptions = ["温柔", "活泼", "正经", "毒舌", "文艺", "可爱", "稳重", "幽默"];

export default function ConfigPage() {
  const [config, setConfig] = useState<Config>({
    gamePath: "", smapiVersion: "", aiEndpoint: "", platformToken: "",
    personality: "gentle", companionName: "", backstory: "", speakingStyles: [],
  });

  const update = <K extends keyof Config>(key: K, value: Config[K]) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  };

  const toggleStyle = (style: string) => {
    setConfig((prev) => ({
      ...prev,
      speakingStyles: prev.speakingStyles.includes(style)
        ? prev.speakingStyles.filter((s) => s !== style)
        : [...prev.speakingStyles, style],
    }));
  };

  return (
    <>
      {/* SMAPI / Mod Config */}
      <section className="bg-stardew-cream py-20">
        <div className="max-w-4xl mx-auto px-4">
          <h2 className="font-[family-name:var(--font-pixel)] text-xl text-center text-stardew-brown mb-4">
            SMAPI / Mod 配置
          </h2>
          <p className="text-center text-stardew-brown/70 mb-12">配置你的游戏环境和 AI 参数</p>

          <div className="parchment rounded-2xl p-8 space-y-6">
            <div>
              <label className="text-sm font-bold text-stardew-brown block mb-2">游戏安装路径</label>
              <input
                className="stardew-input"
                placeholder="C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley"
                value={config.gamePath}
                onChange={(e) => update("gamePath", e.target.value)}
              />
            </div>

            <div className="grid md:grid-cols-2 gap-6">
              <div>
                <label className="text-sm font-bold text-stardew-brown block mb-2">SMAPI 版本</label>
                <div className="flex gap-2">
                  <input
                    className="stardew-input flex-1"
                    placeholder="自动检测"
                    value={config.smapiVersion}
                    onChange={(e) => update("smapiVersion", e.target.value)}
                  />
                  <button
                    className="stardew-btn px-4 py-2 rounded-lg text-sm"
                    onClick={() => alert("正在检测 SMAPI 版本...")}
                  >
                    检测
                  </button>
                </div>
              </div>

              <div>
                <label className="text-sm font-bold text-stardew-brown block mb-2">AI 模型端点</label>
                <input
                  className="stardew-input"
                  placeholder="https://api.example.com/v1"
                  value={config.aiEndpoint}
                  onChange={(e) => update("aiEndpoint", e.target.value)}
                />
              </div>
            </div>

            <div>
              <label className="text-sm font-bold text-stardew-brown block mb-2">平台 Token</label>
              <div className="flex gap-2">
                <input
                  className="stardew-input flex-1"
                  placeholder="scp_..."
                  value={config.platformToken}
                  onChange={(e) => update("platformToken", e.target.value)}
                />
                <button
                  className="stardew-btn px-4 py-2 rounded-lg text-sm"
                  onClick={() => alert("正在验证 Token...")}
                >
                  验证
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Personality Selection */}
      <section className="bg-stardew-green/5 py-20">
        <div className="max-w-4xl mx-auto px-4">
          <h2 className="font-[family-name:var(--font-pixel)] text-xl text-center text-stardew-brown mb-4">
            性格选择
          </h2>
          <p className="text-center text-stardew-brown/70 mb-12">选择你的 AI 搭子的基础性格</p>

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {personalities.map((p) => (
              <button
                key={p.id}
                className={`parchment rounded-2xl p-6 text-center cursor-pointer transition-all hover:-translate-y-1 ${
                  config.personality === p.id
                    ? "ring-2 ring-stardew-gold shadow-lg"
                    : ""
                }`}
                onClick={() => update("personality", p.id)}
              >
                <Image
                  src={p.icon}
                  alt={p.name}
                  width={40}
                  height={40}
                  className={`pixel-art mx-auto mb-3 ${config.personality === p.id ? "animate-float" : ""}`}
                />
                <p className="font-bold text-stardew-brown text-sm mb-0.5">{p.name}</p>
                <p className="text-xs text-stardew-brown/50 mb-1">{p.subtitle}</p>
                <p className="text-xs text-stardew-brown/70">{p.desc}</p>
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* Custom Persona */}
      <section className="bg-stardew-cream py-20">
        <div className="max-w-4xl mx-auto px-4">
          <h2 className="font-[family-name:var(--font-pixel)] text-xl text-center text-stardew-brown mb-4">
            自定义人设
          </h2>
          <p className="text-center text-stardew-brown/70 mb-12">深度定制你的 AI 搭子</p>

          <div className="parchment rounded-2xl p-8 space-y-6">
            <div>
              <label className="text-sm font-bold text-stardew-brown block mb-2">搭子名称</label>
              <input
                className="stardew-input"
                placeholder="给你的搭子起个名字..."
                value={config.companionName}
                onChange={(e) => update("companionName", e.target.value)}
              />
            </div>

            <div>
              <label className="text-sm font-bold text-stardew-brown block mb-2">背景故事</label>
              <textarea
                className="stardew-input"
                placeholder="描述你希望搭子拥有的背景故事..."
                value={config.backstory}
                onChange={(e) => update("backstory", e.target.value)}
              />
            </div>

            <div>
              <label className="text-sm font-bold text-stardew-brown block mb-3">说话风格（可多选）</label>
              <div className="flex flex-wrap gap-2">
                {speakingStyleOptions.map((style) => (
                  <button
                    key={style}
                    className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all cursor-pointer ${
                      config.speakingStyles.includes(style)
                        ? "bg-stardew-gold/20 text-stardew-wood-dark border-2 border-stardew-gold"
                        : "bg-stardew-brown/5 text-stardew-brown/60 border-2 border-transparent hover:border-stardew-wood/20"
                    }`}
                    onClick={() => toggleStyle(style)}
                  >
                    {style}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex gap-4 justify-end pt-4">
              <button
                className="px-6 py-3 rounded-xl font-bold border-2 border-stardew-wood/30 text-stardew-brown hover:bg-stardew-brown/5 transition-colors"
                onClick={() => {
                  setConfig({
                    gamePath: "", smapiVersion: "", aiEndpoint: "", platformToken: "",
                    personality: "gentle", companionName: "", backstory: "", speakingStyles: [],
                  });
                }}
              >
                重置
              </button>
              <button
                className="stardew-btn px-6 py-3 rounded-xl font-bold"
                onClick={() => alert("配置已保存！（开发中）")}
              >
                保存配置
              </button>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
