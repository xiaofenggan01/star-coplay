import Image from "next/image";

const compareGroups = [
  {
    before: {
      img: "/assets/compare-before-1.jpg",
      captions: ["朋友不在线，想玩没人陪", "时间总是对不上，联机机会少"],
    },
    after: {
      img: "/assets/compare-after-1.jpg",
      captions: ["随时在线，填补朋友不在的空档", "你的时间就是游戏时间，随开随玩"],
    },
  },
  {
    before: {
      img: "/assets/compare-before-2.png",
      captions: ["游戏风格不同，节奏难以同步", "繁琐的农活全得自己干"],
    },
    after: {
      img: "/assets/compare-after-2.png",
      captions: ["自定义性格，完美匹配你的游戏风格", "帮你浇水、收获、整理，专注享受乐趣"],
    },
  },
  {
    before: {
      img: "/assets/compare-before-3.jpg",
      captions: ["一个人玩少了分享的乐趣", "长期存档缺少共同回忆"],
    },
    after: {
      img: "/assets/compare-after-3.png",
      captions: ["有搭子一起经历每一天的惊喜", "共生记忆，记得你们种下的每一颗种子"],
    },
  },
];

export default function BeforeAfter() {
  return (
    <section id="compare" className="bg-stardew-green/10 py-20">
      <div className="max-w-6xl mx-auto px-4">
        <h2 className="font-[family-name:var(--font-pixel)] text-xl md:text-2xl text-center text-stardew-brown mb-4">
          当朋友不在的时候
        </h2>
        <p className="text-center text-stardew-brown/70 mb-12">
          AI 搭子不是替代朋友，是填补他们不在时的空档
        </p>

        <div className="space-y-12 max-w-5xl mx-auto">
          {compareGroups.map((group, i) => (
            <div key={i} className="grid md:grid-cols-2 gap-6">
              <div className="parchment rounded-2xl p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Image
                    src="/assets/icons/Multiplayer_Icon.png"
                    alt=""
                    width={24}
                    height={24}
                    className="pixel-art"
                  />
                  <span className="font-bold text-stardew-brown text-sm">一个人玩</span>
                </div>
                <div className="rounded-xl overflow-hidden mb-3 bg-stardew-brown/5">
                  <Image
                    src={group.before.img}
                    alt="真人搭子的烦恼"
                    width={500}
                    height={300}
                    className="w-full h-48 object-cover"
                  />
                </div>
                <ul className="space-y-2">
                  {group.before.captions.map((cap, j) => (
                    <li key={j} className="flex items-start gap-2 text-sm text-stardew-brown/70">
                      <span className="text-red-400 flex-shrink-0 mt-0.5">✗</span>
                      {cap}
                    </li>
                  ))}
                </ul>
              </div>

              <div className="parchment rounded-2xl p-4 ring-2 ring-stardew-gold">
                <div className="flex items-center gap-2 mb-3">
                  <Image
                    src="/assets/icons/Junimo_Icon.png"
                    alt=""
                    width={24}
                    height={24}
                    className="pixel-art"
                  />
                  <span className="font-bold text-stardew-brown text-sm">AI 搭子</span>
                  <span className="ml-auto bg-stardew-gold/20 text-stardew-wood-dark text-xs font-bold px-2 py-0.5 rounded">
                    PLAYER 2
                  </span>
                </div>
                <div className="rounded-xl overflow-hidden mb-3 bg-stardew-brown/5">
                  <Image
                    src={group.after.img}
                    alt="AI 搭子的优势"
                    width={500}
                    height={300}
                    className="w-full h-48 object-cover"
                  />
                </div>
                <ul className="space-y-2">
                  {group.after.captions.map((cap, j) => (
                    <li key={j} className="flex items-start gap-2 text-sm text-stardew-brown/80">
                      <Image
                        src="/assets/icons/Achievements_Icon.png"
                        alt=""
                        width={16}
                        height={16}
                        className="pixel-art flex-shrink-0 mt-0.5"
                      />
                      {cap}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ))}
        </div>

        <div className="flex justify-center mt-10">
          <Image
            src="/assets/icons/Golden_Walnut.png"
            alt=""
            width={40}
            height={40}
            className="pixel-art animate-float"
          />
        </div>
      </div>
    </section>
  );
}
