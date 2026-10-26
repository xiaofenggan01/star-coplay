import Image from "next/image";
import { JoinButton } from "@/components/JoinModal";

/* Floating decoration icons scattered in the sky background */
const floatingIcons = [
  { src: "/assets/icons/Dandelion.png", left: "8%", top: "15%" },
  { src: "/assets/icons/Sweet_Pea.png", left: "85%", top: "20%" },
  { src: "/assets/icons/Corn.png", left: "92%", top: "55%" },
  { src: "/assets/icons/Apple_Sapling.png", left: "5%", top: "60%" },
  { src: "/assets/icons/Golden_Walnut.png", left: "15%", top: "75%" },
  { src: "/assets/icons/Cinder_Shard.png", left: "88%", top: "75%" },
];

export default function Hero() {
  return (
    <section className="stardew-sky relative min-h-screen flex items-center justify-center pt-16 overflow-hidden">
      {/* Floating game icons as decorations */}
      {floatingIcons.map((icon, i) => (
        <div
          key={i}
          className="absolute opacity-20 animate-float"
          style={{
            left: icon.left,
            top: icon.top,
            animationDelay: `${i * 0.5}s`,
            animationDuration: `${3 + (i % 3)}s`,
          }}
        >
          <Image
            src={icon.src}
            alt=""
            width={32}
            height={32}
            className="pixel-art"
          />
        </div>
      ))}

      {/* Decorative pixel stars */}
      <div className="absolute inset-0 opacity-30">
        {[...Array(20)].map((_, i) => (
          <div
            key={i}
            className="absolute w-1 h-1 bg-white rounded-full"
            style={{
              left: `${(i * 17 + 7) % 100}%`,
              top: `${(i * 13 + 3) % 40}%`,
              animationDelay: `${i * 0.3}s`,
              animation: `float ${2 + (i % 3)}s ease-in-out infinite`,
            }}
          />
        ))}
      </div>

      <div className="relative z-10 max-w-6xl mx-auto px-4 py-20 text-center">
        {/* Pain points cloud */}
        <div className="mb-8 animate-fade-in-up">
          <Image
            src="/assets/pain-points.png"
            alt="玩家们的痛点：找不到搭子、被放鸽子、时间对不上..."
            width={700}
            height={350}
            className="mx-auto opacity-80 max-w-full h-auto"
            priority
          />
        </div>

        {/* Main headline */}
        <h1
          className="text-3xl md:text-5xl text-white mb-2 drop-shadow-lg font-black"
          style={{ animationDelay: "0.3s" }}
        >
          星露谷中永远在线的搭子
        </h1>

        <div className="mb-6">
          <Image
            src="/assets/logo-new.png"
            alt="Star CoPlay"
            width={400}
            height={120}
            className="mx-auto max-w-[80%] h-auto drop-shadow-lg"
          />
        </div>

        <p className="text-lg md:text-xl text-white font-bold max-w-2xl mx-auto mb-10 leading-relaxed">
          一个活在星露谷世界里的 AI 搭子。
          <br />
          永不放鸽子，懂你的游戏风格，陪你种田、探矿、过节。
        </p>

        {/* Dual CTA */}
        <div className="flex flex-col sm:flex-row gap-4 justify-center items-center">
          <JoinButton
            className="stardew-btn px-8 py-4 rounded-xl text-lg font-bold flex items-center gap-2"
          >
            <Image
              src="/assets/icons/Achievements_Icon.png"
              alt=""
              width={24}
              height={24}
              className="pixel-art"
            />
            加入内测群
          </JoinButton>
          <a
            href="#product"
            className="px-8 py-4 rounded-xl text-lg font-medium text-white border-2 border-white/40 hover:border-white/80 hover:bg-white/10 transition-all flex items-center gap-2"
          >
            <Image
              src="/assets/icons/Quests_Icon.png"
              alt=""
              width={24}
              height={24}
              className="pixel-art"
            />
            看看 Demo
          </a>
        </div>
      </div>

      {/* Bottom wave transition */}
      <div className="absolute bottom-0 left-0 right-0">
        <svg viewBox="0 0 1440 120" className="w-full h-auto">
          <path
            fill="#fdf6e3"
            d="M0,64L48,58.7C96,53,192,43,288,48C384,53,480,75,576,80C672,85,768,75,864,64C960,53,1056,43,1152,42.7C1248,43,1344,53,1392,58.7L1440,64L1440,120L0,120Z"
          />
        </svg>
      </div>
    </section>
  );
}
