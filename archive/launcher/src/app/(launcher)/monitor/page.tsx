"use client";

import { useState, useEffect, useRef } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";

type Player = {
  id: string;
  name: string;
  icon: string;
};

const mockPlayers: Player[] = [
  { id: "Player1", name: "小风", icon: "/icons/Abigail_Icon.png" },
  { id: "Player2", name: "AI搭子", icon: "/icons/Book_of_Mysteries.png" },
];

const checkSteps = [
  { title: "检查 SMAPI 安装", desc: "通过 SMAPI 启动游戏，确认窗口标题栏出现 SMAPI 版本号。如果未安装，前往 smapi.io 下载。", image: undefined as string | undefined, imageAlt: undefined as string | undefined },
  { title: "检查 StarCoPlay Mod 位置", desc: "确认 StarCoPlay 文件夹已放入游戏的 Mods 目录中。" },
  { title: "加入联机游戏", desc: "通过 SMAPI 启动游戏，进入存档，确保已建造联机小屋并开启联机。然后在第二个实例中选择「加入局域网游戏」，地址填 127.0.0.1。", image: "/assets/guide/启动你的游戏（第一个实例）.webp", imageAlt: "游戏中开启联机功能" },
  { title: "验证 Player 2 加入", desc: "回到 Setup UI，刷新角色列表，选择第二个玩家角色设为 AI 控制，点击「启动」。AI 角色开始自主活动后即表示成功。", image: "/assets/guide/验证 AI 搭子已加入.webp", imageAlt: "AI 搭子在游戏中活动" },
];

export default function MonitorPage() {
  const router = useRouter();
  const [launching, setLaunching] = useState(true);
  const [launchProgress, setLaunchProgress] = useState(0);
  const [players, setPlayers] = useState<Player[]>([]);
  const [showChecklist, setShowChecklist] = useState(false);
  const [selectedAI, setSelectedAI] = useState("Player2");
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const countRef = useRef(0);

  const launchSteps = [
    "启动第一个游戏实例...",
    "启动第二个游戏实例...",
    "等待游戏加载...",
    "检测在线角色...",
    "完成！",
  ];

  useEffect(() => {
    countRef.current = 0;
    timerRef.current = setInterval(() => {
      const idx = countRef.current;
      if (idx < launchSteps.length) {
        setLaunchProgress(idx + 1);
        countRef.current = idx + 1;
      } else {
        if (timerRef.current) clearInterval(timerRef.current);
        setPlayers(mockPlayers);
        setLaunching(false);
        setShowChecklist(true);
      }
    }, 1000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const confirmConnect = () => {
    localStorage.setItem("stardew_connected", "true");
    router.push("/dashboard");
  };

  return (
    <div className="bg-stardew-cream min-h-screen">
      <div className="stardew-sky pt-8 pb-12 text-center">
        <h1 className="font-[family-name:var(--font-pixel)] text-lg md:text-xl text-white drop-shadow-lg mb-3">
          游戏实例监控
        </h1>
        <p className="text-white/80 text-sm max-w-md mx-auto">
          {launching ? "正在启动游戏实例..." : "选择 AI 控制的角色"}
        </p>
      </div>

      <div className="max-w-4xl mx-auto px-4 py-8 space-y-8">
        {/* Launching Progress */}
        {launching && (
          <section className="parchment rounded-2xl p-6 md:p-8 text-center">
            <div className="w-full h-3 bg-stardew-brown/10 rounded-full overflow-hidden mb-4">
              <div
                className="h-full bg-gradient-to-r from-stardew-gold to-stardew-sunset rounded-full transition-all duration-500"
                style={{ width: `${Math.round((launchProgress / launchSteps.length) * 100)}%` }}
              />
            </div>
            <p className="text-sm text-stardew-brown/50 mb-4">
              {Math.round((launchProgress / launchSteps.length) * 100)}%
            </p>
            {launchProgress > 0 && launchProgress <= launchSteps.length && (
              <p className="text-stardew-blue text-sm">
                {launchSteps[launchProgress - 1]}
              </p>
            )}
          </section>
        )}

        {/* Players — select AI */}
        {!launching && !showChecklist && players.length > 0 && (
          <>
            <section className="parchment rounded-2xl p-6 md:p-8">
              <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-2">
                在线角色 ({players.length})
              </h2>
              <p className="text-sm text-stardew-brown/60 mb-6">
                选择你要让 AI 控制的角色，然后点击确认连接
              </p>
              <div className="space-y-4">
                {players.map((player) => {
                  const isSelected = player.id === selectedAI;
                  return (
                    <button
                      key={player.id}
                      onClick={() => setSelectedAI(player.id)}
                      className={`w-full bg-white/40 rounded-xl p-4 flex items-center gap-4 text-left transition-all ${
                        isSelected ? "ring-2 ring-stardew-green/50 bg-stardew-green/5" : "hover:bg-white/60"
                      }`}
                    >
                      <div className="w-12 h-12 rounded-full bg-stardew-wood/20 flex items-center justify-center">
                        <Image src={player.icon} alt="" width={28} height={28} className="pixel-art" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="font-bold text-stardew-brown text-sm">{player.name}</p>
                        <p className="text-xs text-stardew-brown/50">ID: {player.id}</p>
                      </div>
                      <div className="flex items-center gap-1.5 mr-2">
                        <div className="w-2 h-2 rounded-full bg-stardew-green" />
                        <span className="text-xs text-stardew-brown/60">在线</span>
                      </div>
                      {isSelected ? (
                        <span className="px-3 py-1.5 rounded-lg text-xs font-medium bg-stardew-green/20 text-stardew-green">
                          AI 控制
                        </span>
                      ) : (
                        <span className="px-3 py-1.5 rounded-lg text-xs font-medium bg-stardew-blue/20 text-stardew-blue">
                          主机
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </section>

            <div className="text-center pt-2">
              <button
                onClick={confirmConnect}
                className="stardew-btn inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl text-lg font-bold animate-launch-pulse"
              >
                <Image src="/icons/Golden_Walnut.png" alt="" width={20} height={20} className="pixel-art" />
                确认连接
              </button>
            </div>
          </>
        )}
      </div>

      {/* Checklist Modal */}
      {showChecklist && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/70 px-4 py-8 overflow-y-auto">
          <div className="parchment rounded-2xl p-6 md:p-8 max-w-2xl w-full animate-fade-in-up my-auto">
            <div className="flex items-center justify-between mb-6">
              <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown">
                唤醒你的搭子 — 自检清单
              </h2>
            </div>

            <div className="space-y-6">
              {checkSteps.map((step, i) => (
                <div key={i} className="flex gap-4">
                  <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                    <span className="text-white text-xs font-bold">{i + 1}</span>
                  </div>
                  <div className="flex-1 min-w-0 space-y-2">
                    <h3 className="font-bold text-stardew-brown text-sm">{step.title}</h3>
                    <p className="text-sm text-stardew-brown/70 leading-relaxed">{step.desc}</p>
                    {step.image && (
                      <Image
                        src={step.image}
                        alt={step.imageAlt || ""}
                        width={800}
                        height={500}
                        className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto"
                      />
                    )}
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-8 text-center">
              <button
                onClick={() => setShowChecklist(false)}
                className="stardew-btn px-6 py-3 rounded-xl font-bold"
              >
                了解了，去连接！
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
