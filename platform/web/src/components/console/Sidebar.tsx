"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useVisibleMenuItems } from "./use-visible-menu-items";

interface SidebarProps {
  user: { nickname: string; email: string } | null;
  onSignOut: () => void;
}

export default function Sidebar({ user, onSignOut }: SidebarProps) {
  const pathname = usePathname();
  const menuItems = useVisibleMenuItems();

  return (
    <aside className="hidden md:flex fixed top-0 left-0 bottom-0 w-60 z-[60] bg-[#4a3728] border-r border-[#3a2a1e] flex-col pt-5 pb-6 px-6 shrink-0 overflow-y-auto">
      {/* Logo — full-height sidebar reaches the top */}
      <div className="mb-6 shrink-0">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/assets/logo-new.png"
          alt="Star CoPlay"
          className="h-9 w-auto"
        />
      </div>

      {/* Menu */}
      <nav className="flex-1 flex flex-col gap-1">
        {menuItems.map((item) => {
          const isActive = pathname === item.href || pathname.startsWith(item.href + "/");
          const isDisabled = item.comingSoon;

          return (
            <Link
              key={item.key}
              href={item.href}
              className={`
                flex items-center gap-2.5 px-3.5 py-3 rounded-[10px] text-[13px] transition-all
                font-[family-name:var(--font-sans)]
                ${isActive ? "bg-[#f5c842] text-[#4a3728] font-semibold" : "text-[#e8d5b7] hover:bg-[#f5c842]/[0.08]"}
                ${isDisabled ? "opacity-50" : ""}
              `}
            >
              <span className="text-base leading-none shrink-0">
                {item.icon.startsWith("/") ? (
                  <img
                    src={item.icon}
                    alt=""
                    className={`w-4 h-4 pixel-art ${isActive ? "" : "opacity-80"}`}
                    style={{ imageRendering: "pixelated" }}
                  />
                ) : (
                  item.icon
                )}
              </span>
              <div className="flex flex-col min-w-0">
                <span className="truncate">{item.label}</span>
                <span className={`text-[11px] truncate ${isActive ? "text-[#4a3728]/70" : "text-[#e8d5b7]/70"}`}>
                  {item.description}
                </span>
              </div>
              {item.comingSoon && (
                <span
                  className="text-[8px] px-1.5 py-0.5 ml-auto shrink-0 font-bold tracking-wide rounded-sm pixel-art"
                  style={{
                    background: "linear-gradient(180deg, #c9a96e, #8b6914)",
                    border: "1px solid #5a3e1b",
                    color: "#fdf6e3",
                    boxShadow: "inset 0 1px 0 rgba(255,255,255,0.25)",
                  }}
                >
                  Soon
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* User info */}
      <div className="mt-6 pt-4 border-t border-[#3a2a1e]">
        {user ? (
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full bg-[#f5c842]/20 flex items-center justify-center text-[#f5c842] text-sm shrink-0">
              {user.nickname.charAt(0).toUpperCase()}
            </div>
            <div className="flex flex-col min-w-0 flex-1">
              <span className="text-[12px] font-medium text-[#e8d5b7] truncate">
                {user.nickname}
              </span>
              <span className="text-[11px] text-[#e8d5b7]/50 truncate">
                {user.email}
              </span>
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full bg-[#e8d5b7]/10 shrink-0" />
            <span className="text-[12px] text-[#e8d5b7]/50">未登录</span>
          </div>
        )}
        <button
          type="button"
          onClick={onSignOut}
          className="mt-3 w-full text-[12px] text-[#e8d5b7]/60 hover:text-[#e8d5b7] transition-colors text-left font-[family-name:var(--font-sans)]"
        >
          退出登录
        </button>
      </div>
    </aside>
  );
}
