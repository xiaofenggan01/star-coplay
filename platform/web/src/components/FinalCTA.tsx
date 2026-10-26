import Image from "next/image";
import Link from "next/link";

const floatingIcons = [
  { src: "/assets/icons/Forester.png", left: "5%", top: "10%" },
  { src: "/assets/icons/Dandelion.png", left: "90%", top: "15%" },
  { src: "/assets/icons/Corn.png", left: "10%", top: "80%" },
  { src: "/assets/icons/Hoe.png", left: "85%", top: "75%" },
  { src: "/assets/icons/Quality_Sprinkler.png", left: "50%", top: "5%" },
];

export default function FinalCTA() {
  return (
    <section id="cta" className="stardew-sky py-20 relative overflow-hidden">
      {floatingIcons.map((icon, i) => (
        <div
          key={i}
          className="absolute opacity-15 animate-float"
          style={{
            left: icon.left,
            top: icon.top,
            animationDelay: `${i * 0.4}s`,
            animationDuration: `${3 + (i % 3)}s`,
          }}
        >
          <Image
            src={icon.src}
            alt=""
            width={28}
            height={28}
            className="pixel-art"
          />
        </div>
      ))}

      <div className="absolute inset-0 opacity-20">
        {[...Array(15)].map((_, i) => (
          <div
            key={i}
            className="absolute w-1.5 h-1.5 bg-white rounded-full"
            style={{
              left: `${(i * 23 + 5) % 100}%`,
              top: `${(i * 19 + 7) % 100}%`,
              animation: `float ${2.5 + (i % 3)}s ease-in-out infinite`,
              animationDelay: `${i * 0.2}s`,
            }}
          />
        ))}
      </div>

      <div className="max-w-3xl mx-auto px-4 text-center relative z-10">
        <div className="mb-8">
          <Image
            src="/assets/cta-banner.png"
            alt="加入 PLAYER 2 计划，和你的数字伙伴一起游戏"
            width={600}
            height={200}
            className="mx-auto max-w-full h-auto"
          />
        </div>

        <p className="text-white/90 text-lg md:text-xl leading-relaxed mb-8 max-w-xl mx-auto">
          在星露谷的世界里，<br />
          有一个人永远不会放你鸽子，<br />
          永远记得你们一起种下的第一颗防风草。
        </p>

        <p className="text-stardew-gold font-bold text-xl mb-10">
          「寻觅永恒的陪伴」
        </p>

        <Link
          href="/register"
          className="stardew-btn inline-flex items-center gap-3 px-10 py-5 rounded-xl text-xl font-bold"
        >
          <Image
            src="/assets/Stardrop.png"
            alt=""
            width={28}
            height={28}
            className="pixel-art"
          />
          立即申请内测
        </Link>

        <p className="text-white/50 text-sm mt-4">
          内测期间免费体验 · AI 调用费用自付
        </p>
      </div>
    </section>
  );
}
