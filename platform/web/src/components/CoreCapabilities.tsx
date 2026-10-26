import Image from "next/image";

const memoryTimeline = [
  {
    day: "第 1 天",
    season: "春",
    message: "你好，我是简！今天想先做什么？",
    label: "初次见面",
    color: "bg-stardew-green",
    icon: "/assets/icons/Dandelion.png",
  },
  {
    day: "第 7 天",
    season: "春",
    message: "今天下雨，不用浇水了。我去帮你整理箱子吧",
    label: "开始懂你",
    color: "bg-stardew-blue",
    icon: "/assets/icons/Quality_Sprinkler.png",
  },
  {
    day: "第 30 天",
    season: "秋",
    message: "南瓜季到了，我已经把去年你喜欢的地块都种好了",
    label: "默契搭档",
    color: "bg-stardew-gold",
    icon: "/assets/icons/Stardrop.png",
  },
];

export default function CoreCapabilities() {
  return (
    <section className="bg-stardew-green/5 py-20">
      <div className="max-w-5xl mx-auto px-4">
        <h2 className="font-[family-name:var(--font-pixel)] text-xl md:text-2xl text-center text-stardew-brown mb-4">
          越陪越懂你
        </h2>
        <p className="text-center text-stardew-brown/70 mb-14 max-w-xl mx-auto">
          不是被编程的 NPC，是在共同生活中成长的搭子
        </p>

        <div className="mb-20">
          <div className="flex items-center gap-2 mb-8 justify-center">
            <Image
              src="/assets/icons/Book_of_Mysteries.png"
              alt=""
              width={28}
              height={28}
              className="pixel-art"
            />
            <h3 className="font-bold text-stardew-brown text-lg">
              共生记忆 — 记得你们一起经历的每一天
            </h3>
          </div>

          <div className="hidden md:block relative">
            <div className="absolute top-[52px] left-[10%] right-[10%] h-1 bg-stardew-wood-light/30 rounded-full" />

            <div className="grid grid-cols-3 gap-6 relative">
              {memoryTimeline.map((item, i) => (
                <div key={i} className="flex flex-col items-center">
                  <span className="text-xs font-bold text-stardew-brown/50 mb-2">
                    {item.day} · {item.season}
                  </span>

                  <div className={`w-10 h-10 ${item.color} rounded-full flex items-center justify-center z-10 shadow-md mb-4`}>
                    <Image
                      src={item.icon}
                      alt=""
                      width={24}
                      height={24}
                      className="pixel-art"
                    />
                  </div>

                  <div className="parchment rounded-xl p-4 w-full">
                    <div className="flex items-start gap-2 mb-2">
                      <span className="text-sm">🤖</span>
                      <p className="text-sm text-stardew-brown/80 leading-relaxed">
                        「{item.message}」
                      </p>
                    </div>
                    <span className={`inline-block text-xs font-bold text-white px-2 py-0.5 rounded-full ${item.color}`}>
                      {item.label}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="md:hidden space-y-6">
            {memoryTimeline.map((item, i) => (
              <div key={i} className="flex gap-4">
                <div className="flex flex-col items-center">
                  <div className={`w-10 h-10 ${item.color} rounded-full flex items-center justify-center shadow-md flex-shrink-0`}>
                    <Image
                      src={item.icon}
                      alt=""
                      width={24}
                      height={24}
                      className="pixel-art"
                    />
                  </div>
                  {i < memoryTimeline.length - 1 && (
                    <div className="w-0.5 flex-1 bg-stardew-wood-light/30 mt-2" />
                  )}
                </div>

                <div className="parchment rounded-xl p-4 flex-1">
                  <span className="text-xs font-bold text-stardew-brown/50">
                    {item.day} · {item.season}
                  </span>
                  <div className="flex items-start gap-2 mt-1 mb-2">
                    <span className="text-sm">🤖</span>
                    <p className="text-sm text-stardew-brown/80 leading-relaxed">
                      「{item.message}」
                    </p>
                  </div>
                  <span className={`inline-block text-xs font-bold text-white px-2 py-0.5 rounded-full ${item.color}`}>
                    {item.label}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div>
          <div className="flex items-center gap-2 mb-8 justify-center">
            <Image
              src="/assets/icons/Cinder_Shard.png"
              alt=""
              width={28}
              height={28}
              className="pixel-art"
            />
            <h3 className="font-bold text-stardew-brown text-lg">
              自主学习 — 犯错、反思、下次不再犯
            </h3>
          </div>

          <div className="max-w-2xl mx-auto">
            <div className="parchment rounded-2xl p-6 md:p-8">
              <div className="space-y-3 mb-6">
                <div className="flex items-start gap-3">
                  <div className="w-8 h-8 bg-stardew-blue/20 rounded-full flex items-center justify-center flex-shrink-0">
                    <span className="text-sm">🤖</span>
                  </div>
                  <div className="bg-stardew-blue/10 rounded-xl rounded-tl-none px-4 py-2.5 max-w-[80%]">
                    <p className="text-sm text-stardew-brown/70 mb-1">简 把钻石卖给了皮埃尔</p>
                  </div>
                </div>

                <div className="flex items-start gap-3 flex-row-reverse">
                  <div className="w-8 h-8 bg-stardew-green/20 rounded-full flex items-center justify-center flex-shrink-0">
                    <span className="text-sm">👤</span>
                  </div>
                  <div className="bg-stardew-green/10 rounded-xl rounded-tr-none px-4 py-2.5 max-w-[80%]">
                    <p className="text-sm text-stardew-brown font-medium">别卖钻石！那个要留着送人</p>
                  </div>
                </div>

                <div className="flex items-start gap-3">
                  <div className="w-8 h-8 bg-stardew-blue/20 rounded-full flex items-center justify-center flex-shrink-0">
                    <span className="text-sm">🤖</span>
                  </div>
                  <div className="bg-stardew-blue/10 rounded-xl rounded-tl-none px-4 py-2.5 max-w-[80%]">
                    <p className="text-sm text-stardew-brown/70">好的，我记住了！以后稀有物品卖之前先问你</p>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3 my-6">
                <div className="flex-1 h-px bg-stardew-wood/20" />
                <span className="text-xs font-bold text-stardew-brown/40 whitespace-nowrap">
                  ── 三天后 ──
                </span>
                <div className="flex-1 h-px bg-stardew-wood/20" />
              </div>

              <div className="space-y-3">
                <div className="flex items-start gap-3">
                  <div className="w-8 h-8 bg-stardew-blue/20 rounded-full flex items-center justify-center flex-shrink-0">
                    <span className="text-sm">🤖</span>
                  </div>
                  <div className="bg-stardew-blue/10 rounded-xl rounded-tl-none px-4 py-2.5 max-w-[80%]">
                    <p className="text-sm text-stardew-brown/70">背包里有虹彩碎片，要卖掉还是留着？</p>
                  </div>
                </div>

                <div className="flex items-start gap-3 flex-row-reverse">
                  <div className="w-8 h-8 bg-stardew-green/20 rounded-full flex items-center justify-center flex-shrink-0">
                    <span className="text-sm">👤</span>
                  </div>
                  <div className="bg-stardew-gold/15 rounded-xl rounded-tr-none px-4 py-2.5">
                    <p className="text-sm text-stardew-brown font-medium flex items-center gap-1.5">
                      留着！你学会了
                      <Image
                        src="/assets/icons/Achievements_Icon.png"
                        alt=""
                        width={16}
                        height={16}
                        className="pixel-art inline"
                      />
                    </p>
                  </div>
                </div>
              </div>

              <div className="mt-6 pt-4 border-t border-stardew-wood/15 text-center">
                <p className="text-xs text-stardew-brown/50">
                  不是预设的规则，是从你们的互动中自然学会的
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
