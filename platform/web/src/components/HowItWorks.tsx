import Image from "next/image";

const steps = [
  {
    number: "1",
    title: "注册并获取 Token",
    desc: "申请内测资格，获取你的专属 Platform Token。",
    icon: "/assets/icons/Achievements_Icon.png",
  },
  {
    number: "2",
    title: "依照手册 5 分钟安装",
    desc: "按照安装指南，完成 SMAPI、Mod 和 AI 模型的配置。",
    icon: "/assets/icons/ShippingBox.png",
  },
  {
    number: "3",
    title: "启动游戏，遇见搭子",
    desc: "启动游戏，你的 AI 搭子作为 Player 2 加入你的世界。",
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
          简单三步，开始你的 AI 搭子之旅
        </p>

        <div className="relative">
          <div className="hidden md:block absolute top-1/2 left-[15%] right-[15%] h-1 bg-stardew-wood-light/40 -translate-y-1/2 rounded-full" />

          <div className="grid md:grid-cols-3 gap-8 relative">
            {steps.map((step, i) => (
              <div key={step.number} className="relative">
                {i < steps.length - 1 && (
                  <div className="md:hidden absolute left-1/2 top-full w-1 h-8 bg-stardew-wood-light/40 -translate-x-1/2" />
                )}

                <div className="parchment rounded-2xl p-6 text-center relative z-10">
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
