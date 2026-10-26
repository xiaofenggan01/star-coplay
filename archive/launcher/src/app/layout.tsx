import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Stardew AI CoPlay - 星露谷AI搭子启动器",
  description: "星露谷 AI 搭子启动器 - 你的像素世界永远在线的伙伴",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Noto+Sans+SC:wght@400;500;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="antialiased min-h-screen">{children}</body>
    </html>
  );
}
