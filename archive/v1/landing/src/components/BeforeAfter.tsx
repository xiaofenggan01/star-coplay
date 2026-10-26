import Image from "next/image";

const compareGroups = [
  {
    before: {
      img: "/assets/compare-before-2.png",
      captions: ["游玩理念不合，三观不同容易吵架", "情绪劳动成本太高，维护关系很累"],
    },
    after: {
      img: "/assets/compare-after-2.png",
      captions: ["可自定义性格，完美契合你的游戏风格", "零社交压力，纯粹享受游戏乐趣"],
    },
  },
  {
    before: {
      img: "/assets/compare-before-1.jpg",
      captions: ["搭子老放鸽子，约好的时间说不来就不来", "时间总是安排不到一起，档都开不了"],
    },
    after: {
      img: "/assets/compare-after-1.jpg",
      captions: ["永不放鸽子，全天候 100% 在线搭子", "随时随地开玩，你的时间就是游戏时间"],
    },
  },
  {
    before: {
      img: "/assets/compare-before-3.jpg",
      captions: ["不敢做真实的自己，得一直\"端着\"", "分手了、档废了，之前的存档全白费"],
    },
    after: {
      img: "/assets/compare-after-3.png",
      captions: ["做真实的自己，AI 面前不需要人设", "数字化寄托，记忆永不丢失"],
    },
  },
];

export default function BeforeAfter() {
  return (
    <section id="compare" className="bg-stardew-green/10 py-20">
      <div className="max-w-6xl mx-auto px-4">
        <h2 className="font-[family-name:var(--font-pixel)] text-xl md:text-2xl text-center text-stardew-brown mb-4">
          比现有方案好在哪？
        </h2>
        <p className="text-center text-stardew-brown/70 mb-12">
          同样是找搭子，体验天差地别
        </p>

        {/* Compare groups */}
        <div className="space-y-12 max-w-5xl mx-auto">
          {compareGroups.map((group, i) => (
            <div key={i} className="grid md:grid-cols-2 gap-6">
              {/* Before */}
              <div className="parchment rounded-2xl p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Image
                    src="/assets/icons/Multiplayer_Icon.png"
                    alt=""
                    width={24}
                    height={24}
                    className="pixel-art"
                  />
                  <span className="font-bold text-stardew-brown text-sm">真人搭子</span>
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

              {/* After */}
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

        {/* Decoration */}
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
