export interface MenuItem {
  key: string;
  label: string;
  description: string;
  href: string;
  icon: string;
  comingSoon: boolean;
}

export const MENU_ITEMS: MenuItem[] = [
  { key: "profile", label: "我的", description: "账号与设置", href: "/me", icon: "/assets/icons/The_Player_Icon.png", comingSoon: false },
  { key: "persona", label: "性格", description: "定制搭子人设", href: "/persona", icon: "/assets/icons/Book_of_Mysteries.png", comingSoon: false },
  { key: "memory", label: "回忆", description: "记忆系统", href: "/memory", icon: "/assets/icons/Golden_Walnut.png", comingSoon: true },
  { key: "skills", label: "能力", description: "技能仓库", href: "/skills", icon: "/assets/icons/Achievements_Icon.png", comingSoon: true },
  { key: "plan", label: "计划书", description: "目标与日程", href: "/plan", icon: "/assets/icons/Quests_Icon.png", comingSoon: true },
  { key: "channels", label: "聊天方式", description: "聊天渠道", href: "/channels", icon: "/assets/icons/Speech_bubble.png", comingSoon: false },
  { key: "llm", label: "AI 用量", description: "积分与使用记录", href: "/llm", icon: "/assets/icons/SMAPI_mascot.png", comingSoon: false },
];
