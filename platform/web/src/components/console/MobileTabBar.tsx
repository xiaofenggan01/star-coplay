"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useVisibleMenuItems } from "./use-visible-menu-items";

export default function MobileTabBar() {
  const pathname = usePathname();
  const mobileItems = useVisibleMenuItems().slice(0, 5);

  return (
    <nav className="fixed bottom-0 inset-x-0 md:hidden bg-[#4a3728] border-t border-[#3a2a1e] flex items-center justify-around px-2 py-2 z-50">
      {mobileItems.map((item) => {
        const isActive = pathname === item.href || pathname.startsWith(item.href + "/");

        return (
          <Link
            key={item.key}
            href={item.href}
            className={`
              flex flex-col items-center gap-0.5 px-2 py-1 rounded-lg text-center transition-colors
              font-[family-name:var(--font-sans)]
              ${isActive ? "text-[#c4a35a]" : "text-[#e8d5b7]/60"}
              ${item.comingSoon ? "opacity-50" : ""}
            `}
          >
            <span className="leading-none">
              {item.icon.startsWith("/") ? (
                <img
                  src={item.icon}
                  alt=""
                  className="w-5 h-5 pixel-art mx-auto"
                  style={{ imageRendering: "pixelated" }}
                />
              ) : (
                <span className="text-lg leading-none block">{item.icon}</span>
              )}
            </span>
            <span className="text-[10px] leading-tight">{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
