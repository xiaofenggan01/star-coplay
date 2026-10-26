"use client";

import { useState, useEffect, useRef } from "react";
import Image from "next/image";

const companionStats = [
  { label: "默契度", value: 78, color: "bg-stardew-green" },
  { label: "记忆量", value: 35, color: "bg-stardew-gold" },
  { label: "自主性", value: 62, color: "bg-stardew-blue" },
  { label: "农作技能", value: 90, color: "bg-stardew-green-light" },
];

type LogEntry = {
  time: string;
  type: "chat" | "think" | "action";
  text: string;
  from?: string;
  icon?: string;
};

const seedLogs: LogEntry[] = [
  { time: "10:23:15", type: "think", text: "南瓜田需要浇水，先完成农活..." },
  { time: "10:23:18", type: "action", text: "正在浇水... 南瓜田区域" },
  { time: "10:23:45", type: "action", text: "发现野生弹簧洋葱！已采集" },
  { time: "10:24:02", type: "chat", text: "帮我去矿洞看一下", from: "小风", icon: "/icons/Abigail_Icon.png" },
  { time: "10:24:05", type: "think", text: "主人让我去矿洞，需要先整理背包..." },
  { time: "10:24:08", type: "action", text: "前往矿区，目标：铜矿层" },
  { time: "10:24:15", type: "chat", text: "好的！我先准备一下，马上出发～", from: "AI搭子", icon: "/icons/Book_of_Mysteries.png" },
];

const liveActions = [
  { type: "action" as const, text: "与村民对话中... (莱纳斯)" },
  { type: "action" as const, text: "整理背包，出售多余物品" },
  { type: "think" as const, text: "今天天气不错，适合钓鱼..." },
  { type: "action" as const, text: "检查自动采集器状态" },
  { type: "action" as const, text: "播种新一批作物：甜瓜" },
  { type: "think" as const, text: "栅栏需要修理了，明天找罗宾买材料" },
  { type: "action" as const, text: "修复栅栏损坏部分" },
  { type: "chat" as const, text: "今天的鱼竿该升级了", from: "小风", icon: "/icons/Abigail_Icon.png" },
  { type: "think" as const, text: "主人想要升级鱼竿，我帮他收集材料" },
  { type: "chat" as const, text: "我来帮你攒纤维！海边有很多", from: "AI搭子", icon: "/icons/Book_of_Mysteries.png" },
  { type: "action" as const, text: "前往海滩，采集纤维" },
  { type: "action" as const, text: "浇水完成！小麦田区域" },
];

const memories = [
  { icon: "/icons/Dandelion.png", title: "第一次收获", desc: "我们一起种出了第一批防风草", date: "第3天" },
  { icon: "/icons/Copper_Node.png", title: "初次探险", desc: "第一次挖矿到达第20层", date: "第8天" },
  { icon: "/icons/Golden_Walnut.png", title: "教训", desc: "学会了不要卖稀有矿物", date: "第15天" },
  { icon: "/icons/Cinder_Shard.png", title: "传奇之鱼", desc: "一起钓到了传说级鱼类", date: "第22天" },
  { icon: "/icons/Animals_Icon.png", title: "畜棚升级", desc: "合作完成了畜棚扩建", date: "第30天" },
  { icon: "/icons/Poppy.png", title: "礼物", desc: "收到了一朵罂粟花", date: "第38天" },
];

const preferences = [
  { icon: "/icons/Quality_Sprinkler.png", label: "浇水偏好", value: "自动" },
  { icon: "/icons/Copper_Node.png", label: "采矿倾向", value: "探索型" },
  { icon: "/icons/Golden_Walnut.png", label: "稀有物品", value: "收藏" },
  { icon: "/icons/Marriage_Icon.png", label: "送礼策略", value: "好感优先" },
  { icon: "/icons/Speech_bubble.png", label: "对话风格", value: "温柔" },
  { icon: "/icons/Clock_Icon.png", label: "作息时间", value: "早鸟型" },
];

function now() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

export default function DashboardPage() {
  const [logs, setLogs] = useState<LogEntry[]>(seedLogs);
  const logRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const idxRef = useRef(0);

  useEffect(() => {
    idxRef.current = 0;
    timerRef.current = setInterval(() => {
      const action = liveActions[idxRef.current % liveActions.length];
      idxRef.current++;
      setLogs((prev) => [...prev, { ...action, time: now() }]);
    }, 6000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <>
      {/* Companion Status */}
      <section className="bg-stardew-cream py-20">
        <div className="max-w-6xl mx-auto px-4">
          <div className="parchment rounded-2xl p-8">
            <div className="flex items-center gap-4 mb-8">
              <div className="w-16 h-16 rounded-full bg-stardew-gold/20 border-2 border-stardew-gold flex items-center justify-center flex-shrink-0 animate-float">
                <Image src="/icons/Abigail_Icon.png" alt="Companion" width={40} height={40} className="pixel-art" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-3 mb-2">
                  <h2 className="font-bold text-stardew-brown text-lg">小麦</h2>
                  <span className="bg-stardew-gold/20 text-stardew-wood-dark text-xs font-bold px-2 py-0.5 rounded">Lv.5</span>
                  <span className="bg-stardew-green/20 text-stardew-green text-xs font-bold px-2 py-0.5 rounded-full">在线</span>
                </div>
                <div className="flex gap-2 flex-wrap">
                  {["温柔", "勤劳", "好奇", "学习型"].map((t) => (
                    <span key={t} className="bg-stardew-brown/5 text-stardew-brown/70 text-xs px-2 py-0.5 rounded">{t}</span>
                  ))}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
              {companionStats.map((stat) => (
                <div key={stat.label}>
                  <div className="flex justify-between text-sm mb-2">
                    <span className="text-stardew-brown/70">{stat.label}</span>
                    <span className="font-bold text-stardew-brown">{stat.value}%</span>
                  </div>
                  <div className="w-full h-3 bg-stardew-brown/10 rounded-full overflow-hidden">
                    <div
                      className={`h-full ${stat.color} rounded-full transition-all duration-1000`}
                      style={{ width: `${stat.value}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Live Log */}
      <section className="bg-stardew-green/5 py-20">
        <div className="max-w-6xl mx-auto px-4">
          <h2 className="font-[family-name:var(--font-pixel)] text-xl text-center text-stardew-brown mb-4">运行日志</h2>
          <p className="text-center text-stardew-brown/70 mb-12">AI 搭子的思考、行为与对话实时记录</p>

          <div className="max-w-3xl mx-auto">
            <div
              ref={logRef}
              className="parchment rounded-2xl p-4 md:p-6 max-h-[500px] overflow-y-auto space-y-2"
            >
              {logs.map((log, i) => (
                <div key={i} className={`rounded-lg px-3 py-2 text-sm leading-relaxed ${
                  log.type === "think"
                    ? "bg-stardew-purple/10 text-stardew-brown/70"
                    : log.type === "action"
                    ? "bg-stardew-green/10 text-stardew-brown/80"
                    : log.from === "小风"
                    ? "bg-stardew-blue/10 text-stardew-brown/80"
                    : "bg-stardew-gold/10 text-stardew-brown/80"
                }`}>
                  <div className="flex items-start gap-2">
                    <span className="text-stardew-brown/40 flex-shrink-0 font-mono text-xs">[{log.time}]</span>
                    {log.type === "think" && <span className="flex-shrink-0">💭</span>}
                    {log.type === "action" && <span className="flex-shrink-0">🎯</span>}
                    {log.type === "chat" && log.icon && (
                      <Image src={log.icon} alt="" width={16} height={16} className="pixel-art flex-shrink-0 mt-0.5" />
                    )}
                    <div className="flex-1 min-w-0">
                      {log.type === "chat" && log.from && (
                        <span className="font-bold text-stardew-brown text-xs mr-1.5">{log.from}:</span>
                      )}
                      {log.type === "think" && (
                        <span className="font-bold text-stardew-purple text-xs mr-1.5">AI思考:</span>
                      )}
                      {log.type === "action" && (
                        <span className="font-bold text-stardew-green text-xs mr-1.5">行为:</span>
                      )}
                      <span className="text-stardew-brown/80">{log.text}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Memory Bank */}
      <section className="bg-stardew-cream py-20">
        <div className="max-w-6xl mx-auto px-4">
          <h2 className="font-[family-name:var(--font-pixel)] text-xl text-center text-stardew-brown mb-4">记忆银行</h2>
          <p className="text-center text-stardew-brown/70 mb-12">你们一起经历的每一天</p>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {memories.map((mem) => (
              <div key={mem.title} className="parchment rounded-xl p-4 flex items-start gap-3">
                <Image src={mem.icon} alt="" width={28} height={28} className="pixel-art flex-shrink-0 mt-1" />
                <div>
                  <p className="font-bold text-stardew-brown text-sm mb-1">{mem.title}</p>
                  <p className="text-stardew-brown/70 text-xs">{mem.desc}</p>
                  <p className="text-stardew-brown/40 text-xs mt-1">{mem.date}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Behavior Preferences */}
      <section className="bg-stardew-green/5 py-20">
        <div className="max-w-6xl mx-auto px-4">
          <h2 className="font-[family-name:var(--font-pixel)] text-xl text-center text-stardew-brown mb-4">行为偏好</h2>
          <p className="text-center text-stardew-brown/70 mb-12">AI 搭子的个性化设置</p>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 max-w-4xl mx-auto">
            {preferences.map((pref) => (
              <div key={pref.label} className="parchment rounded-xl p-4 flex items-center gap-3">
                <Image src={pref.icon} alt="" width={22} height={22} className="pixel-art flex-shrink-0" />
                <span className="text-stardew-brown/70 text-sm flex-1">{pref.label}</span>
                <span className="bg-stardew-gold/20 text-stardew-wood-dark text-xs font-bold px-2 py-0.5 rounded">{pref.value}</span>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
