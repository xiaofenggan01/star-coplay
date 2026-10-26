"use client";

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { label: "首页", href: "/" },
  { label: "监控", href: "/monitor" },
  { label: "配置", href: "/config" },
  { label: "安装向导", href: "/setup" },
];

export default function Nav() {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <nav className="fixed top-0 left-0 right-0 z-50 bg-stardew-brown/95 backdrop-blur-sm border-b-4 border-stardew-wood-dark">
      <div className="max-w-6xl mx-auto px-4 h-16 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-3">
          <Image
            src="/icons/Abigail_Icon.png"
            alt="Logo"
            width={32}
            height={32}
            className="pixel-art animate-float"
          />
          <span className="font-[family-name:var(--font-pixel)] text-stardew-gold text-xs">
            Stardew AI CoPlay
          </span>
        </Link>

        <div className="hidden md:flex items-center gap-8">
          {navItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`transition-colors font-medium ${
                pathname === item.href
                  ? "text-stardew-gold"
                  : "text-stardew-cream hover:text-stardew-gold"
              }`}
            >
              {item.label}
            </Link>
          ))}
          <Link
            href="/auth"
            className="stardew-btn px-5 py-2 rounded-lg font-bold text-sm"
          >
            账户
          </Link>
        </div>

        <button
          className="md:hidden text-stardew-cream p-2"
          onClick={() => setMobileOpen(!mobileOpen)}
          aria-label="菜单"
        >
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            {mobileOpen ? (
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            ) : (
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            )}
          </svg>
        </button>
      </div>

      {mobileOpen && (
        <div className="md:hidden bg-stardew-brown/95 border-t border-stardew-wood-dark px-4 pb-4">
          {navItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`block py-3 transition-colors ${
                pathname === item.href
                  ? "text-stardew-gold"
                  : "text-stardew-cream hover:text-stardew-gold"
              }`}
              onClick={() => setMobileOpen(false)}
            >
              {item.label}
            </Link>
          ))}
          <Link
            href="/auth"
            className="stardew-btn block text-center px-5 py-2 rounded-lg font-bold text-sm mt-2"
            onClick={() => setMobileOpen(false)}
          >
            账户
          </Link>
        </div>
      )}
    </nav>
  );
}
