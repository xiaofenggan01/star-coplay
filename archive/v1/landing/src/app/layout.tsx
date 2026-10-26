import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Star CoPlay - 你的星露谷永不掉线的 AI 搭子",
  description:
    "AI Co-Player for Stardew Valley — 一个活在星露谷世界里的 AI 搭子，永不放鸽子，懂你的游戏风格，陪你种田、探矿、过节。",
  keywords: ["星露谷物语", "Stardew Valley", "AI", "搭子", "Mod", "Co-Player"],
  openGraph: {
    title: "Star CoPlay ⭐ AI Co-Player for Stardew Valley",
    description: "永不掉线的星露谷 AI 搭子",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Noto+Sans+SC:wght@400;500;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
