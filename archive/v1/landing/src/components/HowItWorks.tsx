import Image from "next/image";

const steps = [
  {
    number: "1",
    title: "安装 SMAPI",
    desc: "下载最新版 SMAPI 框架，双击安装包一键搞定。",
    icon: "/assets/icons/SMAPI_mascot.png",
  },
  {
    number: "2",
    title: "安装 Star CoPlay Mod",
    desc: "将 Mod 文件拖入 Mods 文件夹，配置 AI 参数。",
    icon: "/assets/icons/ShippingBox.png",
  },
  {
    number: "3",
    title: "开始游戏，遇见你的搭子",
    desc: "启动游戏，你的 AI 搭子已经在农场等你了。",
    icon: "/assets/icons/Controls_Icon.png",
  },
];

export default function HowItWorks() {
  return (
    <section id="how-it-works" className="bg-stardew-cream py-20">
      <div className="max-w-5xl mx-auto px-4">
        <h2 className="font-[family-name:var(--font-pixel)] text-xl md:text-2xl text-center text-stardew-brown mb-4">
          三步开始
        </h2>
        <p className="text-center text-stardew-brown/70 mb-14">
          不需要写代码，不需要复杂配置，3 分钟搞定
        </p>

        {/* Steps */}
        <div className="relative">
          {/* Desktop connector line */}
          <div className="hidden md:block absolute top-1/2 left-[15%] right-[15%] h-1 bg-stardew-wood-light/40 -translate-y-1/2 rounded-full" />

          <div className="grid md:grid-cols-3 gap-8 relative">
            {steps.map((step, i) => (
              <div key={step.number} className="relative">
                {/* Mobile connector line */}
                {i < steps.length - 1 && (
                  <div className="md:hidden absolute left-1/2 top-full w-1 h-8 bg-stardew-wood-light/40 -translate-x-1/2" />
                )}

                <div className="parchment rounded-2xl p-6 text-center relative z-10">
                  {/* Step number badge */}
                  <div className="w-12 h-12 wood-panel rounded-full flex items-center justify-center mx-auto mb-4">
                    <span className="font-[family-name:var(--font-pixel)] text-white text-sm">
                      {step.number}
                    </span>
                  </div>

                  <Image
                    src={step.icon}
                    alt=""
                    width={48}
                    height={48}
                    className="pixel-art mx-auto mb-3"
                  />
                  <h3 className="font-bold text-stardew-brown text-lg mb-2">
                    {step.title}
                  </h3>
                  <p className="text-stardew-brown/70 text-sm leading-relaxed">
                    {step.desc}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
