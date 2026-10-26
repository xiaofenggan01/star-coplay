"use client";

interface ComingSoonProps {
  title: string;
  description: string;
  icon: string;
}

export default function ComingSoon({ title, description, icon }: ComingSoonProps) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 font-[family-name:var(--font-sans)]">
      <span className="text-5xl">{icon}</span>
      <h2 className="text-lg font-bold text-[#4a3728]">{title}</h2>
      <p className="text-sm text-[#8b7355]">{description}</p>
      <span className="px-4 py-1.5 rounded-full bg-[#c4a35a]/10 border-[1.5px] border-[#c4a35a]/30 text-xs text-[#c4a35a] font-semibold">
        即将上线
      </span>
    </div>
  );
}
