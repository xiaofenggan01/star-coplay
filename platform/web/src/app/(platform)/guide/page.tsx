"use client";

import { useState, useEffect } from "react";
import Image from "next/image";
import Link from "next/link";
import { QQGroupInfo, QQGroupModal } from "@/components/QQGroupModal";
import { TokenInfoModal } from "@/components/TokenInfoModal";

/* ------------------------------------------------------------------ */
/*  章节定义                                                           */
/* ------------------------------------------------------------------ */

const sections = [
  { id: "before",   label: "开始之前",           icon: "/assets/icons/Quests_Icon.png" },
  { id: "smapi",    label: "安装 SMAPI",         icon: "/assets/icons/SMAPI_mascot.png" },
  { id: "install",  label: "安装 StarCoPlay Mod", icon: "/assets/icons/ShippingBox.png" },
  { id: "launch",   label: "启动游戏与联机",     icon: "/assets/icons/Multiplayer_Icon.png" },
  { id: "commands", label: "配置并启动 AI",      icon: "/assets/icons/Controls_Icon.png" },
  { id: "trouble",  label: "遇到问题",           icon: "/assets/icons/Book_of_Mysteries.png" },
];

/** 章节图标：统一用星露谷像素图标渲染 */
function SectionIcon({ src, size = 20 }: { src: string; size?: number }) {
  return (
    <Image
      src={src}
      alt=""
      width={size}
      height={size}
      className="pixel-art inline-block align-[-0.15em]"
    />
  );
}

/** 「加入 QQ 群」按钮：点击弹出 QQ 群二维码弹窗 */
function QQGroupButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="stardew-btn inline-flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-bold"
    >
      <Image
        src="/assets/icons/Speech_bubble.png"
        alt=""
        width={16}
        height={16}
        className="pixel-art"
      />
      加入 QQ 群获取
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  辅助组件                                                           */
/* ------------------------------------------------------------------ */

function GuideImage({ src, alt, className = "" }: { src: string; alt: string; className?: string }) {
  return (
    <Image
      src={src}
      alt={alt}
      width={800}
      height={500}
      className={`rounded-xl border-2 border-stardew-wood/15 w-full h-auto ${className}`}
    />
  );
}

function Tip({ children, type = "info" }: { children: React.ReactNode; type?: "info" | "warn" | "success" }) {
  const styles = {
    info: "bg-stardew-blue/10 border-stardew-blue/30 text-stardew-brown",
    warn: "bg-amber-50 border-amber-300 text-amber-900",
    success: "bg-emerald-50 border-emerald-300 text-emerald-900",
  };
  const icons = {
    info: "/assets/icons/Speech_bubble.png",
    warn: "/assets/icons/Secret_Note_Icon.png",
    success: "/assets/icons/Achievements_Icon.png",
  };
  return (
    <div className={`border-2 rounded-xl p-4 text-sm leading-relaxed flex gap-2 ${styles[type]}`}>
      <Image
        src={icons[type]}
        alt=""
        width={20}
        height={20}
        className="pixel-art flex-shrink-0 mt-0.5"
      />
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}

function CodeBlock({ children }: { children: string }) {
  return (
    <pre className="bg-stardew-brown/90 text-stardew-cream rounded-xl p-4 text-xs overflow-x-auto font-mono leading-relaxed">
      {children}
    </pre>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4">
      <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
        <span className="text-white text-xs font-bold">{n}</span>
      </div>
      <div className="flex-1 min-w-0">
        <h4 className="font-bold text-stardew-brown mb-2">{title}</h4>
        <div className="space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
          {children}
        </div>
      </div>
    </div>
  );
}

/* ---- 准备流程图（SVG 手绘判定图，两阶段全连通，节点可点击跳转，无第三方依赖） ---- */

function PrepFlow() {
  const brown = "#8b6914";
  const amber = "#d97706";
  const trunk = 220;

  // 判定节点中心 y
  const cy = { d1: 196, d2: 344, d3: 492, d4: 666, d5: 814 };
  // 「否」补救后回流汇入主干的点
  const dot = { d1: 270, d2: 418, d3: 552, d4: 740, d5: 874 };

  // 单个「判定 + 否分支 + 回流」的连线组
  const branch = (c: number, d: number) => (
    <>
      {/* 否：判定右顶点 → 补救框 */}
      <line x1={312} y1={c} x2={402} y2={c} stroke={amber} strokeWidth="2.5" strokeDasharray="5 3" markerEnd="url(#arrow-right)" />
      {/* 补救后回流到主干 */}
      <path d={`M552,${c + 28} L552,${d} L${trunk},${d}`} fill="none" stroke={brown} strokeWidth="2.5" strokeDasharray="5 3" markerEnd="url(#arrow-left)" />
      <circle cx={trunk} cy={d} r="4" fill={brown} />
      {/* 是 / 否 标签 */}
      <text x={228} y={c + 66} fontSize="12" fontWeight="bold" fill="#166534">是</text>
      <text x={330} y={c - 8} fontSize="12" fontWeight="bold" fill="#b45309">否</text>
    </>
  );

  // 判定菱形（可点击跳转）
  const diamond = (c: number, href: string, icon: string, l1: string, l2: string) => (
    <a href={href} className="cursor-pointer" style={{ cursor: "pointer" }}>
      <polygon points={`${trunk},${c - 46} 312,${c} ${trunk},${c + 46} 128,${c}`} fill="#ffffff" stroke="#a08240" strokeWidth="2" />
      <image href={icon} x={trunk - 9} y={c - 40} width={18} height={18} style={{ imageRendering: "pixelated" }} />
      <text x={trunk} y={c + 2} textAnchor="middle" fontSize="11.5" fontWeight="bold" fill="#5a3e1b">{l1}</text>
      <text x={trunk} y={c + 18} textAnchor="middle" fontSize="11.5" fontWeight="bold" fill="#5a3e1b">{l2}</text>
    </a>
  );

  // 补救框（可点击跳转）
  const fixBox = (c: number, href: string, icon: string, text: string) => (
    <a href={href} className="cursor-pointer" style={{ cursor: "pointer" }}>
      <rect x={402} y={c - 28} width={300} height={56} rx={12} fill="#fffbeb" stroke="#fcd34d" strokeWidth="2" />
      <image href={icon} x={418} y={c - 9} width={18} height={18} style={{ imageRendering: "pixelated" }} />
      <text x={562} y={c + 4} textAnchor="middle" fontSize="12" fontWeight="bold" fill="#92400e" textDecoration="underline">{text}</text>
    </a>
  );

  // 阶段标题横幅
  const phase = (yTop: number, text: string) => (
    <>
      <rect x={70} y={yTop} width={300} height={30} rx={15} fill="#efe0c0" stroke="#c9a96e" strokeWidth="1.5" />
      <text x={220} y={yTop + 20} textAnchor="middle" fontSize="12" fontWeight="bold" fill="#5a3e1b">{text}</text>
    </>
  );

  return (
    <section className="parchment rounded-2xl p-6 md:p-8">
      <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-2 flex items-center gap-2">
        <img
          src="/assets/icons/Standard_Farm_Map_Icon.png"
          alt=""
          width={18}
          height={18}
          className="pixel-art"
          style={{ imageRendering: "pixelated" }}
        />
        <span>准备清单流程图</span>
      </h2>
      <p className="text-sm text-stardew-brown/70 mb-4">
        分两个阶段：先备齐游戏环境，再注册激活账号。回答「是」沿主线一路向下，回答「否」就先去右边补齐，补完连回主线。
        <strong className="text-stardew-blue">不知道某一步怎么做？直接点对应的方框，会跳到下方详细教程。</strong>
      </p>

      <div className="overflow-x-auto">
        <svg
          viewBox="0 0 720 970"
          role="img"
          aria-label="准备清单判定流程图，各步骤可点击跳转到详细教程"
          className="mx-auto block"
          style={{ minWidth: 620, maxWidth: 720, width: "100%", height: "auto" }}
        >
          <defs>
            <marker id="arrow-down" markerWidth="12" markerHeight="12" refX="4" refY="7" orient="auto" markerUnits="userSpaceOnUse">
              <path d="M0,0 L8,0 L4,7 Z" fill={brown} />
            </marker>
            <marker id="arrow-right" markerWidth="12" markerHeight="12" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse">
              <path d="M0,0 L0,8 L7,4 Z" fill={amber} />
            </marker>
            <marker id="arrow-left" markerWidth="12" markerHeight="12" refX="1" refY="4" orient="auto" markerUnits="userSpaceOnUse">
              <path d="M8,0 L8,8 L1,4 Z" fill={brown} />
            </marker>
          </defs>

          {/* ===== 主干竖线（是 → 一路贯穿） ===== */}
          <line x1={trunk} y1={70} x2={trunk} y2={96} stroke={brown} strokeWidth="2.5" markerEnd="url(#arrow-down)" />
          <line x1={trunk} y1={126} x2={trunk} y2={150} stroke={brown} strokeWidth="2.5" markerEnd="url(#arrow-down)" />
          <line x1={trunk} y1={242} x2={trunk} y2={298} stroke={brown} strokeWidth="2.5" markerEnd="url(#arrow-down)" />
          <line x1={trunk} y1={390} x2={trunk} y2={446} stroke={brown} strokeWidth="2.5" markerEnd="url(#arrow-down)" />
          <line x1={trunk} y1={538} x2={trunk} y2={566} stroke={brown} strokeWidth="2.5" markerEnd="url(#arrow-down)" />
          <line x1={trunk} y1={596} x2={trunk} y2={620} stroke={brown} strokeWidth="2.5" markerEnd="url(#arrow-down)" />
          <line x1={trunk} y1={712} x2={trunk} y2={768} stroke={brown} strokeWidth="2.5" markerEnd="url(#arrow-down)" />
          <line x1={trunk} y1={860} x2={trunk} y2={888} stroke={brown} strokeWidth="2.5" markerEnd="url(#arrow-down)" />

          {/* ===== 各判定的否分支 + 回流 ===== */}
          {branch(cy.d1, dot.d1)}
          {branch(cy.d2, dot.d2)}
          {branch(cy.d3, dot.d3)}
          {branch(cy.d4, dot.d4)}
          {branch(cy.d5, dot.d5)}

          {/* ===== 起点 ===== */}
          <rect x={115} y={20} width={210} height={50} rx={14} fill="#5a3e1b" stroke="#3E2723" strokeWidth="2" />
          <image href="/assets/icons/Junimo_Icon.png" x={150} y={35} width={20} height={20} style={{ imageRendering: "pixelated" }} />
          <text x={228} y={51} textAnchor="middle" fontSize="13" fontWeight="bold" fill="#f4e4c1">想要一个 AI 搭子</text>

          {/* ===== 阶段一 ===== */}
          {phase(96, "第一阶段 · 准备游戏环境")}
          {diamond(cy.d1, "#before", "/assets/icons/Standard_Farm_Map_Icon.png", "有《星露谷物语》", "Steam 正版吗？")}
          {fixBox(cy.d1, "#before", "/assets/icons/ShippingBox.png", "点击查看：准备工作")}
          {diamond(cy.d2, "#smapi", "/assets/icons/SMAPI_mascot.png", "装好 SMAPI", "加载框架了吗？")}
          {fixBox(cy.d2, "#smapi", "/assets/icons/SMAPI_mascot.png", "点击查看第一步：安装 SMAPI")}
          {diamond(cy.d3, "#install", "/assets/icons/Bundle_Icon.png", "装好 StarCoPlay", "Mod 了吗？")}
          {fixBox(cy.d3, "#install", "/assets/icons/Bundle_Icon.png", "点击查看第二步：安装 Mod")}

          {/* ===== 阶段二 ===== */}
          {phase(566, "第二阶段 · 注册与激活账号")}
          {diamond(cy.d4, "/register", "/assets/icons/Quests_Icon.png", "注册官网账号了吗？", "(注册后为游客)")}
          {fixBox(cy.d4, "/register", "/assets/icons/Quests_Icon.png", "点击前往：注册账号")}
          {diamond(cy.d5, "/me", "/assets/icons/Multiplayer_Icon.png", "用邀请码激活了吗？", "(激活得内测身份 + Token)")}
          {fixBox(cy.d5, "/me", "/assets/icons/Multiplayer_Icon.png", "点击前往：用邀请码激活")}

          {/* ===== 终点（拿到 Token，进游戏激活搭子） ===== */}
          <a href="#commands" className="cursor-pointer" style={{ cursor: "pointer" }}>
            <rect x={110} y={888} width={220} height={56} rx={14} fill="#ecfdf5" stroke="#6ee7b7" strokeWidth="2.5" />
            <image href="/assets/icons/Achievements_Icon.png" x={132} y={906} width={20} height={20} style={{ imageRendering: "pixelated" }} />
            <text x={230} y={912} textAnchor="middle" fontSize="12.5" fontWeight="bold" fill="#065f46">拿到 Token！</text>
            <text x={230} y={928} textAnchor="middle" fontSize="12.5" fontWeight="bold" fill="#065f46">进游戏激活搭子 →</text>
          </a>
        </svg>
      </div>

      <div className="mt-6">
        <Tip type="info">
          两个阶段都齐全后，就能在游戏里用 Token 激活搭子了。已经准备好的话，直接跳到下方「第一步」开始安装。
        </Tip>
      </div>
    </section>
  );
}

function ChatCommand({
  cmd,
  desc,
  example,
  note,
}: {
  cmd: string;
  desc: string;
  example: string;
  note?: React.ReactNode;
}) {
  return (
    <div className="bg-white/60 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="bg-stardew-brown/90 text-stardew-cream px-2 py-0.5 rounded text-xs font-mono">{cmd}</span>
      </div>
      <p className="text-xs text-stardew-brown/70 mb-2">{desc}</p>
      <div className="rounded-lg border-2 border-amber-300 bg-amber-50 px-3 py-2">
        <span className="mr-2 inline-block rounded bg-amber-400/90 px-1.5 py-0.5 text-[10px] font-bold text-amber-950">
          正确示例
        </span>
        <span className="font-mono text-[13px] font-bold text-amber-900">{example}</span>
      </div>
      {note && (
        <p className="mt-2 text-[11px] font-bold text-stardew-red">{note}</p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  主页面                                                             */
/* ------------------------------------------------------------------ */

export default function GuidePage() {
  const [activeSection, setActiveSection] = useState("before");
  const [showTokenInfo, setShowTokenInfo] = useState(false);
  const [showQQModal, setShowQQModal] = useState(false);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id);
          }
        }
      },
      { rootMargin: "-20% 0px -60% 0px" }
    );
    sections.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  return (
    <div className="min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] bg-stardew-cream">
      {/* 顶部横幅 */}
      <div className="stardew-sky pt-6 md:pt-8 pb-12 text-center">
        <h1 className="font-[family-name:var(--font-pixel)] text-lg md:text-xl text-white drop-shadow-lg mb-3">
          StarCoPlay 安装指南
        </h1>
        <p className="text-white/80 text-sm max-w-md mx-auto">
          从零开始，5 分钟拥有你的 AI 搭子
        </p>
      </div>

      <div className="max-w-6xl mx-auto px-4 py-8 flex gap-8">
        {/* 侧边导航 */}
        <nav className="hidden lg:block w-48 flex-shrink-0">
          <div className="sticky top-20 space-y-1">
            {sections.map((s) => (
              <a
                key={s.id}
                href={`#${s.id}`}
                className={`block px-3 py-2 rounded-lg text-xs transition-all ${
                  activeSection === s.id
                    ? "bg-stardew-wood/15 text-stardew-brown font-bold"
                    : "text-stardew-brown/60 hover:text-stardew-brown hover:bg-stardew-wood/5"
                }`}
              >
                <span className="mr-2 inline-flex items-center"><SectionIcon src={s.icon} size={16} /></span>
                {s.label}
              </a>
            ))}
          </div>
        </nav>

        {/* 主内容区 */}
        <main className="flex-1 min-w-0 space-y-12">

          {/* ⓪ 开始之前 */}
          <section id="before" className="scroll-mt-20 parchment rounded-2xl p-6 md:p-8">
            <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6 flex items-center gap-2">
              <SectionIcon src="/assets/icons/Quests_Icon.png" /> 开始之前
            </h2>
            <p className="text-sm text-stardew-brown/70 mb-6">
              请确认你具备以下条件，避免安装中途卡住。
            </p>

            <div className="grid sm:grid-cols-2 gap-4 mb-6">
              <div className="bg-white/40 rounded-xl p-4">
                <h4 className="font-bold text-stardew-brown text-sm mb-3">系统要求</h4>
                <ul className="space-y-2 text-xs text-stardew-brown/70">
                  <li className="flex items-center gap-2"><span className="text-stardew-green">☑</span> Windows 10+ 或 macOS 12+</li>
                  <li className="flex items-center gap-2"><span className="text-stardew-green">☑</span> 16GB 内存（需运行两个游戏实例）</li>
                  <li className="flex items-center gap-2"><span className="text-stardew-green">☑</span> 星露谷物语 1.6.x（Steam 版）</li>
                </ul>
              </div>
              <div className="bg-white/40 rounded-xl p-4">
                <h4 className="font-bold text-stardew-brown text-sm mb-3">你还需要</h4>
                <ul className="space-y-2 text-xs text-stardew-brown/70">
                  <li className="flex items-center gap-2"><span className="text-stardew-green">☑</span> StarCoPlay 内测邀请码 + 平台 Token（<button type="button" onClick={() => setShowTokenInfo(true)} className="text-stardew-blue underline">如何获取？</button>）</li>
                  <li className="flex items-center gap-2"><span className="text-stardew-green">☑</span> 约 5 分钟的时间</li>
                </ul>
              </div>
            </div>

            <Tip>
              无需安装 Python 或任何开发工具，全程只需安装 Mod + 游戏内输入指令即可。
            </Tip>
          </section>

          {/* 准备清单流程图 */}
          <PrepFlow />

          {/* ① 安装 SMAPI */}
          <section id="smapi" className="scroll-mt-20 parchment rounded-2xl p-6 md:p-8">
            <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6 flex items-center gap-2">
              <SectionIcon src="/assets/icons/SMAPI_mascot.png" /> 第一步：安装 SMAPI
            </h2>

            <p className="text-sm text-stardew-brown/70 mb-6">
              SMAPI 是星露谷的 Mod 加载框架，类似 Minecraft 的 Forge。StarCoPlay 依赖它运行。
            </p>

            <div className="mb-6">
              <Tip type="warn">
                请务必使用最新版 <strong>SMAPI 4.5.2</strong>。旧版本可能与游戏 1.6.x 或 StarCoPlay 不兼容。如果你此前装过旧版 SMAPI，请用新版覆盖更新。
              </Tip>
            </div>

            <div className="space-y-6">
              <Step n={1} title="下载 SMAPI">
                <p>前往 SMAPI 官网下载最新版安装包：</p>
                <div className="flex flex-wrap gap-3 my-1">
                  <a
                    href="https://smapi.io"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 rounded-xl border-2 border-stardew-wood/30 bg-stardew-cream/70 px-4 py-2 text-xs font-bold text-stardew-brown hover:bg-white/75"
                  >
                    前往官网 smapi.io →
                  </a>
                </div>
                <p className="text-xs text-stardew-brown/70">
                  国内访问官网较慢，或下载遇到困难？加入 QQ 内测群，从群文件获取 SMAPI 安装包，我们已备好最新版。
                </p>
                <div className="my-1">
                  <QQGroupButton onClick={() => setShowQQModal(true)} />
                </div>
                <GuideImage src="/assets/guide/下载 SMAPI.webp" alt="SMAPI 官网下载页截图" />
              </Step>

              <Step n={2} title="运行安装程序">
                <p className="mb-2"><strong>Windows：</strong>解压后双击安装程序，按提示选择游戏目录即可。</p>
                <GuideImage src="/assets/guide/运行安装程序-Windows.webp" alt="Windows SMAPI 安装器界面" />
                <p className="mt-3"><strong>macOS：</strong>解压后在终端运行 <code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">install on Mac.command</code></p>
                <GuideImage src="/assets/guide/运行安装程序-Mac.webp" alt="macOS SMAPI 安装器界面" />
              </Step>

              <Step n={3} title="验证安装成功">
                <p>通过 SMAPI 启动游戏，如果窗口标题栏（最上方）出现 SMAPI 版本号，说明安装成功。</p>
                <GuideImage src="/assets/guide/验证安装成功.webp" alt="游戏启动后窗口标题栏 SMAPI 版本号截图" />
              </Step>
            </div>
          </section>

          {/* ② 安装 StarCoPlay Mod */}
          <section id="install" className="scroll-mt-20 parchment rounded-2xl p-6 md:p-8">
            <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6 flex items-center gap-2">
              <SectionIcon src="/assets/icons/ShippingBox.png" /> 第二步：安装 StarCoPlay Mod
            </h2>

            <div className="space-y-6">
              <Step n={1} title="下载 Mod 安装包">
                <p>加入 QQ 内测群，从<strong>群文件</strong>中获取最新版本的 StarCoPlay Mod 安装包。</p>
                <div className="my-1">
                  <QQGroupButton onClick={() => setShowQQModal(true)} />
                </div>
                <Tip>
                  安装包是一个压缩包，包含 StarCoPlay 文件夹。群文件中始终提供最新版本，我们会持续更新。
                </Tip>
              </Step>

              <Step n={2} title="找到游戏的 Mods 文件夹">
                <p>将压缩包中的 <code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">StarCoPlay</code> 文件夹放入游戏的 <strong>Mods</strong> 目录。</p>

                <Tip type="info">
                  不知道 Mods 文件夹在哪？按以下步骤找到：
                </Tip>

                <div className="space-y-3 mt-2">
                  <div className="bg-white/60 rounded-xl p-4">
                    <h5 className="font-bold text-stardew-brown text-xs mb-2">Steam 版（Windows）</h5>
                    <ol className="list-decimal list-inside space-y-1 text-xs text-stardew-brown/70">
                      <li>打开 Steam，右键点击「星露谷物语」→ 管理 → 浏览本地文件</li>
                      <li>会打开一个文件夹，这就是游戏目录</li>
                      <li>在里面找到 <code className="bg-stardew-brown/10 px-1 rounded text-xs">Mods</code> 文件夹（如果没有就新建一个）</li>
                      <li>把 <code className="bg-stardew-brown/10 px-1 rounded text-xs">StarCoPlay</code> 文件夹拖进去</li>
                    </ol>
                    <CodeBlock>{`典型路径：
C:\\Program Files (x86)\\Steam\\steamapps\\common\\Stardew Valley\\Mods\\StarCoPlay\\`}</CodeBlock>
                    <GuideImage src="/assets/guide/在 steam 中找到文件夹.webp" alt="在 Steam 中右键游戏 → 浏览本地文件" />
                  </div>
                  <div className="bg-white/60 rounded-xl p-4">
                    <h5 className="font-bold text-stardew-brown text-xs mb-2">Steam 版（macOS）</h5>
                    <CodeBlock>{`~/Library/Application Support/Steam/steamapps/common/
Stardew Valley/Contents/MacOS/Mods/StarCoPlay/`}</CodeBlock>
                  </div>
                </div>
                <GuideImage src="/assets/guide/安装 Mod-Windows.webp" alt="Mods 文件夹截图 — StarCoPlay 文件夹位置" />
              </Step>

              <Step n={3} title="确认文件结构">
                <p>安装完成后，Mods 目录下应该有 <code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">StarCoPlay</code> 文件夹，里面包含 <code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">manifest.json</code> 和 <code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">StarCoPlay.dll</code> 等文件。</p>
                <Tip type="warn">
                  不要把 StarCoPlay 文件夹再嵌套一层！应该是 <code className="bg-stardew-brown/10 px-1 rounded text-xs">Mods/StarCoPlay/manifest.json</code>，而不是 <code className="bg-stardew-brown/10 px-1 rounded text-xs">Mods/StarCoPlay/StarCoPlay/manifest.json</code>。
                </Tip>
              </Step>
            </div>
          </section>

          {/* ③ 启动游戏与联机 */}
          <section id="launch" className="scroll-mt-20 parchment rounded-2xl p-6 md:p-8">
            <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6 flex items-center gap-2">
              <SectionIcon src="/assets/icons/Multiplayer_Icon.png" /> 第三步：启动游戏与联机
            </h2>

            <Tip>
              AI 搭子以第二个玩家的身份加入你的联机世界。所以你需要先开启联机模式，然后让 AI 加入。
            </Tip>

            <div className="space-y-6 mt-6">
              <Step n={1} title="通过 SMAPI 启动游戏">
                <p>双击桌面上的 SMAPI 图标（或在 Steam 中启动游戏），确保窗口标题栏出现 SMAPI 版本号。</p>
                <GuideImage src="/assets/guide/通过SMAPI 打开游戏.webp" alt="通过 SMAPI 启动游戏" />
              </Step>

              <Step n={2} title="进入存档，建造联机小屋">
                <p>进入你的存档后，前往木匠罗宾的商店，花费 <strong>100 金币</strong> 建造一个<strong>联机小屋</strong>（Cabin）。</p>
                <Tip type="warn">
                  必须先建造联机小屋才能让其他人（包括 AI）加入你的游戏。如果没有小屋，联机模式无法使用。
                </Tip>
              </Step>

              <Step n={3} title="开启联机模式">
                <p>在游戏菜单中，选择<strong>「主持」</strong>（Host），开启联机功能。其他玩家现在可以加入你的世界了。</p>
                <GuideImage src="/assets/guide/启动你的游戏（第一个实例）.webp" alt="游戏中开启联机功能" />
              </Step>
            </div>
          </section>

          {/* ④ 配置并启动 AI */}
          <section id="commands" className="scroll-mt-20 parchment rounded-2xl p-6 md:p-8">
            <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6 flex items-center gap-2">
              <SectionIcon src="/assets/icons/Controls_Icon.png" /> 第四步：配置并启动 AI
            </h2>

            <p className="text-sm text-stardew-brown/70 mb-4">
              在游戏中按 <kbd className="bg-stardew-brown/10 border-2 border-stardew-wood/30 px-2 py-0.5 rounded text-xs font-bold">T</kbd> 打开聊天栏，然后依次输入以下三条指令：
            </p>

            <div className="mb-6">
              <Tip type="warn">
                <strong>指令里的下划线「_」怎么打？</strong>按住 <kbd className="bg-white/60 border border-amber-300 px-1.5 py-0.5 rounded text-[11px] font-bold">Shift</kbd> + 数字键 <kbd className="bg-white/60 border border-amber-300 px-1.5 py-0.5 rounded text-[11px] font-bold">0</kbd> 右边的 <kbd className="bg-white/60 border border-amber-300 px-1.5 py-0.5 rounded text-[11px] font-bold">-</kbd> 键（Windows 和 Mac 通用）。
                <br />
                务必切换到<strong>英文半角</strong>输入法，中文输入法下打出的可能是全角字符「＿」，游戏无法识别。
              </Tip>
            </div>

            <div className="space-y-6">
              <Step n={1} title="绑定账号">
                <ChatCommand
                  cmd="/agent_token 你的Token"
                  desc="绑定你的平台账号。Token 在注册并用邀请码激活后生成，可在官网「我的」页面查看复制。"
                  example="/agent_token qwertyuiop"
                  note="⚠️ 注意 agent_token 中间是下划线「_」，不要打成空格；指令和 Token 之间留一个空格，直接接上 Token 即可，不要加括号。"
                />
                <Tip type="warn">
                  Token 是你的身份凭证，请妥善保管。如果丢失，可在官网「我的」页面重新查看。
                </Tip>
              </Step>

              <Step n={2} title="启动新游戏实例">
                <ChatCommand
                  cmd="/agent_new"
                  desc="启动第二个星露谷游戏实例。新窗口会自动打开，然后你需要在其中手动加入联机。"
                  example="/agent_new"
                  note="⚠️ 注意 agent_new 中间是下划线「_」，不要打成空格；这条指令直接输入即可，不需要参数，也不要加括号。"
                />
                <p className="text-xs text-stardew-brown/60 mt-2">新窗口打开后，在其中选择「加入局域网游戏」，地址填：</p>
                <CodeBlock>127.0.0.1</CodeBlock>
                <GuideImage src="/assets/guide/让第二个实例加入联机.webp" alt="第二个实例「加入局域网」界面截图" />
              </Step>

              <Step n={3} title="启动 AI 控制">
                <p className="mb-2">在第一个游戏窗口（主机）的聊天栏中输入：</p>
                <ChatCommand
                  cmd="/agent_control 玩家昵称"
                  desc="将指定的玩家角色交给 AI 控制。填入第二个实例创建的角色昵称。"
                  example="/agent_control 小萌妹"
                  note="⚠️ 注意 agent_control 中间是下划线「_」，不要打成空格；指令和玩家昵称之间留一个空格，直接写昵称即可，不要加括号。"
                />
              </Step>
            </div>

            <div className="mt-6 bg-emerald-50 border-2 border-emerald-300 rounded-xl p-5">
              <h3 className="font-bold text-emerald-900 text-sm mb-3">✅ 搞定！AI 搭子已上线</h3>
              <ul className="space-y-2 text-xs text-emerald-900/80">
                <li className="flex items-start gap-2">
                  <span>🏃</span>
                  <span>AI 角色会开始在农场上自主走动、浇水、收获</span>
                </li>
                <li className="flex items-start gap-2">
                  <span>💬</span>
                  <span>你可以在聊天框中和它对话，试试说「你好」或「帮我浇一下水」</span>
                </li>
                <li className="flex items-start gap-2">
                  <span>🎮</span>
                  <span>享受和 AI 搭子一起种田、探矿、过节的时光吧！</span>
                </li>
              </ul>
            </div>

            <div className="bg-amber-50 border-2 border-amber-300 rounded-xl p-5 mt-4">
              <h3 className="font-bold text-amber-900 text-sm mb-3">⚠️ 内测须知</h3>
              <ul className="space-y-2 text-xs text-amber-900/80 list-disc list-inside">
                <li>产品处于内测阶段，AI 搭子可能偶尔做出不太智能的行为，这是正常现象</li>
                <li>建议使用新建存档进行体验，避免影响已有进度</li>
                <li>遇到问题欢迎反馈，帮助我们改进！</li>
              </ul>
            </div>
          </section>

          {/* ⑤ 遇到问题 */}
          <section id="trouble" className="scroll-mt-20 parchment rounded-2xl p-6 md:p-8">
            <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6 flex items-center gap-2">
              <SectionIcon src="/assets/icons/Book_of_Mysteries.png" /> 遇到问题？
            </h2>

            <div className="mb-6">
              <h3 className="font-bold text-stardew-brown text-sm mb-3">常见问题</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b-2 border-stardew-wood/20">
                      <th className="text-left py-2 pr-3 text-stardew-brown font-bold">现象</th>
                      <th className="text-left py-2 pr-3 text-stardew-brown font-bold">可能原因</th>
                      <th className="text-left py-2 text-stardew-brown font-bold">解决方法</th>
                    </tr>
                  </thead>
                  <tbody className="text-stardew-brown/70">
                    <tr className="border-b border-stardew-wood/10">
                      <td className="py-2 pr-3">指令输入后没反应</td>
                      <td className="py-2 pr-3">Token 输入有误</td>
                      <td className="py-2">检查 Token 是否正确，重新复制粘贴</td>
                    </tr>
                    <tr className="border-b border-stardew-wood/10">
                      <td className="py-2 pr-3">AI 不动 / 无反应</td>
                      <td className="py-2 pr-3">Mod 未正确安装或未加载</td>
                      <td className="py-2">检查 Mods 目录和 SMAPI 控制台是否有报错</td>
                    </tr>
                    <tr className="border-b border-stardew-wood/10">
                      <td className="py-2 pr-3">第二个实例连不上</td>
                      <td className="py-2 pr-3">未开启联机 / 没有联机小屋</td>
                      <td className="py-2">确保主机已开启联机且建造了联机小屋</td>
                    </tr>
                    <tr className="border-b border-stardew-wood/10">
                      <td className="py-2 pr-3">AI 卡在某个位置</td>
                      <td className="py-2 pr-3">寻路被障碍物阻挡</td>
                      <td className="py-2">输入 <code className="bg-stardew-brown/10 px-1 rounded text-xs font-mono">/agent_gn</code> 强制让 AI 回家睡觉，下次醒来会重置位置</td>
                    </tr>
                    <tr>
                      <td className="py-2 pr-3">/agent_new 后窗口没弹出</td>
                      <td className="py-2 pr-3">游戏路径未检测到</td>
                      <td className="py-2">确认通过 SMAPI 启动的游戏（标题栏有版本号）</td>
                    </tr>
                    <tr>
                      <td className="py-2 pr-3">联机搭子加入游戏无法找到房主</td>
                      <td className="py-2 pr-3">房主没有建联机小屋</td>
                      <td className="py-2">去罗宾那花 100 金币买一个联机小屋</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div className="bg-stardew-blue/10 rounded-xl p-4 mb-6">
              <h3 className="font-bold text-stardew-brown text-sm mb-2">游戏内指令一览</h3>
              <p className="text-xs text-stardew-brown/70 mb-3">
                在游戏中按 <kbd className="bg-stardew-brown/10 border-2 border-stardew-wood/30 px-2 py-0.5 rounded text-xs font-bold">T</kbd> 打开聊天栏输入指令，或输入 <code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">/agent_help</code> 查看全部命令。
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b-2 border-stardew-blue/20">
                      <th className="text-left py-2 pr-3 text-stardew-brown font-bold">指令</th>
                      <th className="text-left py-2 text-stardew-brown font-bold">说明</th>
                    </tr>
                  </thead>
                  <tbody className="text-stardew-brown/70">
                    <tr className="border-b border-stardew-blue/10">
                      <td className="py-2 pr-3 font-mono">/agent_token &lt;Token&gt;</td>
                      <td className="py-2">设置平台 Token（<code className="bg-stardew-brown/10 px-1 rounded text-xs">clear</code> 清空）</td>
                    </tr>
                    <tr className="border-b border-stardew-blue/10">
                      <td className="py-2 pr-3 font-mono">/agent_control &lt;玩家名&gt;</td>
                      <td className="py-2">查看在线玩家 / 设置 AI 控制的玩家</td>
                    </tr>
                    <tr className="border-b border-stardew-blue/10">
                      <td className="py-2 pr-3 font-mono">/agent_new</td>
                      <td className="py-2">启动第二个游戏实例</td>
                    </tr>
                    <tr className="border-b border-stardew-blue/10">
                      <td className="py-2 pr-3 font-mono">/agent &lt;指令&gt;</td>
                      <td className="py-2">对 AI 搭子下达自然语言指令</td>
                    </tr>
                    <tr className="border-b border-stardew-blue/10">
                      <td className="py-2 pr-3 font-mono">/agent_status</td>
                      <td className="py-2">查看 AI 完整状态</td>
                    </tr>
                    <tr className="border-b border-stardew-blue/10">
                      <td className="py-2 pr-3 font-mono">/agent_cancel</td>
                      <td className="py-2">取消 AI 当前动作</td>
                    </tr>
                    <tr className="border-b border-stardew-blue/10">
                      <td className="py-2 pr-3 font-mono">/agent_gn</td>
                      <td className="py-2">让搭子回家睡觉</td>
                    </tr>
                    <tr className="border-b border-stardew-blue/10">
                      <td className="py-2 pr-3 font-mono">/agent_players</td>
                      <td className="py-2">列出在线玩家</td>
                    </tr>
                    <tr className="border-b border-stardew-blue/10">
                      <td className="py-2 pr-3 font-mono">/agent_ping</td>
                      <td className="py-2">测试服务器连通性</td>
                    </tr>
                    <tr>
                      <td className="py-2 pr-3 font-mono">/agent_bug &lt;描述&gt;</td>
                      <td className="py-2">反馈问题</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div className="bg-stardew-blue/10 rounded-xl p-4">
              <h3 className="font-bold text-stardew-brown text-sm mb-2">还是解决不了？</h3>
              <p className="text-xs text-stardew-brown/70 mb-3">
                扫码或搜索群号加入 QQ 内测群，和开发者直接交流，我们会第一时间帮你解决。
              </p>
              <div className="bg-white/60 rounded-xl p-4 max-w-[240px]">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src="/assets/qq-group-qr.png"
                  alt="QQ 内测群二维码"
                  className="w-44 mx-auto mb-3 rounded-lg"
                />
                <p className="text-[10px] text-stardew-brown/60 mb-2 text-center">QQ 群号</p>
                <QQGroupInfo />
              </div>
            </div>
          </section>

          {/* 底部 CTA */}
          <div className="text-center py-8">
            <p className="text-stardew-brown/50 text-xs mb-4">
              准备好了吗？
            </p>
            <Link
              href="/register"
              className="stardew-btn inline-block px-8 py-3 rounded-xl font-bold text-sm"
            >
              开始注册 →
            </Link>
          </div>

        </main>
      </div>

      {showTokenInfo && <TokenInfoModal onClose={() => setShowTokenInfo(false)} />}
      {showQQModal && <QQGroupModal onClose={() => setShowQQModal(false)} />}
    </div>
  );
}
