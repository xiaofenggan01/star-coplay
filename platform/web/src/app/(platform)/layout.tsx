import PlatformNav from "@/components/PlatformNav";

export default function PlatformLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen font-[family-name:var(--font-sans)] text-sm">
      <PlatformNav />
      <main className="pt-14 md:pt-16 min-h-screen border-t border-stardew-wood-dark/20 bg-stardew-sky/30">
        {children}
      </main>
    </div>
  );
}
