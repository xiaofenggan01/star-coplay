"use client";

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
const navLinks = [
  { label: "产品介绍", href: "#product" },
  { label: "功能对比", href: "#compare" },
  { label: "使用方法", href: "#how-it-works" },
  { label: "安装指南", href: "/guide" },
  { label: "常见问题", href: "#faq" },
];

export default function Navbar() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <nav className="fixed top-0 left-0 right-0 z-50 bg-stardew-brown/95 backdrop-blur-sm border-b-4 border-stardew-wood-dark">
      <div className="max-w-6xl mx-auto px-4 h-16 flex items-center justify-between">
        <Link href="/" className="flex items-center -ml-4">
          <Image
            src="/assets/logo-new.png"
            alt="Star CoPlay"
            width={240}
            height={64}
            className="h-16 w-auto"
            priority
          />
        </Link>

        <div className="hidden md:flex items-center gap-8">
          {navLinks.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="text-stardew-cream hover:text-stardew-gold transition-colors font-medium"
            >
              {link.label}
            </a>
          ))}
          <Link
            href="/login"
            className="text-stardew-cream hover:text-stardew-gold transition-colors font-medium"
          >
            登录
          </Link>
          <Link
            href="/register"
            className="stardew-btn px-5 py-2 rounded-lg font-bold text-sm"
          >
            申请内测
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
          {navLinks.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="block py-3 text-stardew-cream hover:text-stardew-gold transition-colors"
              onClick={() => setMobileOpen(false)}
            >
              {link.label}
            </a>
          ))}
          <Link
            href="/login"
            className="block py-3 text-stardew-cream hover:text-stardew-gold transition-colors"
            onClick={() => setMobileOpen(false)}
          >
            登录
          </Link>
          <Link
            href="/register"
            className="stardew-btn block text-center px-5 py-2 rounded-lg font-bold text-sm mt-2"
            onClick={() => setMobileOpen(false)}
          >
            申请内测
          </Link>
        </div>
      )}
    </nav>
  );
}
