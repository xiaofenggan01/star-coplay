import Image from "next/image";

const features = [
  {
    icon: "/assets/icons/Book_of_Mysteries.png",
    title: "共生记忆体",
    desc: "拥有长期记忆，记得你们一起经历的每一天，承载儿时羁绊的情感锚点。",
  },
  {
    icon: "/assets/icons/Standard_Farm_Map_Icon.png",
    title: "农场共生",
    desc: "自动化资源管理与任务协同执行，帮你浇水、收获、整理箱子。",
  },
  {
    icon: "/assets/icons/Speech_bubble.png",
    title: "自然对话",
    desc: "听懂你的语言指令，用星露谷的方式回应你。不是冷冰冰的命令行。",
  },
  {
    icon: "/assets/icons/Marriage_Icon.png",
    title: "越陪越默契",
    desc: "随着共同游戏时间增长，AI 搭子会学习你的偏好，进化出独特的行为模式。",
  },
];

export default function ProductShowcase() {
  return (
    <section id="product" className="bg-stardew-cream py-20">
      <div className="max-w-6xl mx-auto px-4">
        <h2 className="font-[family-name:var(--font-pixel)] text-xl md:text-2xl text-center text-stardew-brown mb-4">
          你的 Player 2，由你定义
        </h2>
        <p className="text-center text-stardew-brown/70 mb-12 max-w-xl mx-auto">
          拥有长期记忆、自主决策能力、会主动帮忙干活的 AI 搭子 — 性格由你定制
        </p>

        <div className="grid md:grid-cols-2 gap-12 items-center">
          <div className="flex justify-center">
            <Image
              src="/assets/product-jian.png"
              alt="AI 角色「简 (Jian)」- 你的星露谷 AI 搭子"
              width={500}
              height={400}
              className="max-w-full h-auto drop-shadow-xl"
            />
          </div>

          <div className="space-y-6">
            {features.map((feature) => (
              <div key={feature.title} className="flex gap-4 items-start">
                <Image
                  src={feature.icon}
                  alt=""
                  width={40}
                  height={40}
                  className="pixel-art flex-shrink-0 mt-0.5"
                />
                <div>
                  <h3 className="font-bold text-stardew-brown text-lg mb-1">
                    {feature.title}
                  </h3>
                  <p className="text-stardew-brown/70 leading-relaxed">
                    {feature.desc}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-16 parchment rounded-2xl p-8 max-w-4xl mx-auto">
          <div className="flex items-center gap-3 mb-6">
            <Image
              src="/assets/icons/The_Player_Icon.png"
              alt=""
              width={36}
              height={36}
              className="pixel-art"
            />
            <h3 className="font-[family-name:var(--font-pixel)] text-base md:text-lg text-stardew-brown">
              自定义你的专属搭子
            </h3>
          </div>

          <div className="grid md:grid-cols-3 gap-6">
            <div className="text-center">
              <div className="w-20 h-20 mx-auto mb-3 rounded-xl bg-stardew-gold/20 border-2 border-stardew-gold flex items-center justify-center">
                <Image
                  src="/assets/icons/Marriage_Icon.png"
                  alt=""
                  width={40}
                  height={40}
                  className="pixel-art"
                />
              </div>
              <p className="font-bold text-stardew-brown text-sm mb-1">预设人格</p>
              <p className="text-stardew-brown/60 text-xs leading-relaxed">
                温柔型 / 活泼型 / 学者型 / 冒险型…<br />
                标签化快速设定，秒速开玩
              </p>
            </div>

            <div className="text-center">
              <div className="w-20 h-20 mx-auto mb-3 rounded-xl bg-stardew-blue/10 border-2 border-stardew-blue flex items-center justify-center">
                <Image
                  src="/assets/icons/Secret_Note_Icon.png"
                  alt=""
                  width={40}
                  height={40}
                  className="pixel-art"
                />
              </div>
              <p className="font-bold text-stardew-brown text-sm mb-1">深度自定义</p>
              <p className="text-stardew-brown/60 text-xs leading-relaxed">
                自由编写人设背景故事，<br />
                塑造独一无二的 AI 伙伴
              </p>
            </div>

            <div className="text-center relative">
              <span className="absolute -top-2 -right-2 bg-stardew-sunset text-white text-[10px] font-bold px-2 py-0.5 rounded-full">
                即将开放
              </span>
              <div className="w-20 h-20 mx-auto mb-3 rounded-xl bg-stardew-green/10 border-2 border-stardew-green/40 border-dashed flex items-center justify-center">
                <Image
                  src="/assets/icons/Bundle_Icon.png"
                  alt=""
                  width={40}
                  height={40}
                  className="pixel-art"
                />
              </div>
              <p className="font-bold text-stardew-brown text-sm mb-1">人设迁移</p>
              <p className="text-stardew-brown/60 text-xs leading-relaxed">
                从 ChatGPT / Claude 等平台<br />
                丝滑迁移核心人设与记忆
              </p>
            </div>
          </div>
        </div>

        <div className="mt-16">
          <div className="parchment rounded-2xl p-2 max-w-3xl mx-auto">
            <div
              className="relative w-full bg-stardew-brown/10 rounded-xl overflow-hidden"
              style={{ paddingBottom: "56.25%" }}
            >
              <iframe
                className="absolute inset-0 w-full h-full"
                src="https://www.youtube.com/embed/me1iYFyjnJE"
                title="Star CoPlay Demo"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowFullScreen
              />
            </div>
          </div>

          <div className="flex justify-center items-end gap-8 mt-8">
            <div className="text-center">
              <Image
                src="/assets/character-harmony-1.jpg"
                alt="玩家角色"
                width={180}
                height={240}
                className="rounded-xl shadow-lg"
              />
            </div>
            <div className="flex flex-col items-center mb-8">
              <Image
                src="/assets/icons/Marriage_Icon.png"
                alt=""
                width={120}
                height={120}
                className="pixel-art animate-float"
              />
              <p className="text-stardew-brown/60 text-sm mt-2 font-medium">和谐共处</p>
            </div>
            <div className="text-center">
              <Image
                src="/assets/character-harmony-2.png"
                alt="AI 搭子角色"
                width={180}
                height={240}
                className="rounded-xl shadow-lg"
              />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
