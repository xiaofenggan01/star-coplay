"use client";

import Image from "next/image";
import Link from "next/link";

const guide = "/assets/guide";

export default function SetupPage() {
  return (
    <div className="bg-stardew-cream min-h-screen">
      {/* Hero */}
      <div className="stardew-sky pt-8 pb-12 text-center">
        <h1 className="font-[family-name:var(--font-pixel)] text-lg md:text-xl text-white drop-shadow-lg mb-3">
          StarCoPlay 安装指南
        </h1>
        <p className="text-white/80 text-sm max-w-md mx-auto">
          从零开始，10 分钟拥有你的 AI 搭子
        </p>
      </div>

      <div className="max-w-4xl mx-auto px-4 py-8 space-y-8">

        {/* 开始之前 */}
        <section className="parchment rounded-2xl p-6 md:p-8">
          <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6">
            📋 开始之前
          </h2>
          <div className="space-y-4">
            <div className="bg-stardew-blue/10 border-2 border-stardew-blue/30 rounded-xl p-4 text-sm text-stardew-brown space-y-1">
              <p><strong>系统要求：</strong></p>
              <ul className="list-disc list-inside space-y-1 text-stardew-brown/70">
                <li>Windows 10+ 或 macOS 12+</li>
                <li>16GB 内存（需要同时运行两个游戏实例）</li>
                <li>星露谷物语 v1.6.x（Steam 版）</li>
              </ul>
            </div>
            <div className="bg-white/40 rounded-xl p-4 text-sm text-stardew-brown space-y-1">
              <p><strong>你还需要：</strong></p>
              <ul className="list-disc list-inside space-y-1 text-stardew-brown/70">
                <li>一个大模型 API Key（下方有注册教程）</li>
                <li>StarCoPlay 内测邀请码</li>
                <li>大约 10-15 分钟</li>
              </ul>
            </div>
            <p className="text-xs text-stardew-brown/50">
              💡 无需安装 Python 或任何开发环境，StarCoPlay Agent 是独立应用程序，下载即用。
            </p>
          </div>
        </section>

        {/* 注册账号 */}
        <section className="parchment rounded-2xl p-6 md:p-8">
          <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6">
            🔑 注册账号，获取 Token
          </h2>
          <div className="space-y-6">
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">1</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>打开注册页面，输入邀请码。</p>
                <Image src={`${guide}/填写信息并提交.webp`} alt="注册页面" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">2</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>填写邀请码和昵称，其他字段可选。提交后会生成唯一的 Platform Token。</p>
                <Image src={`${guide}/保存你的 Token.webp`} alt="保存 Token" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <div className="bg-amber-50 border-2 border-amber-300 rounded-xl p-4 text-sm text-amber-900">
              ⚠️ Token 是你的身份凭证，请务必保存。如果丢失需要重新注册。
            </div>
          </div>
        </section>

        {/* 安装 SMAPI */}
        <section className="parchment rounded-2xl p-6 md:p-8">
          <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6">
            🔧 安装 SMAPI
          </h2>
          <p className="text-sm text-stardew-brown/70 mb-6">
            SMAPI 是星露谷的 Mod 加载框架（类似 Minecraft Forge），StarCoPlay 依赖它运行。
          </p>
          <div className="space-y-6">
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">1</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>前往 SMAPI 官网下载最新版本：<a href="https://smapi.io" target="_blank" rel="noopener noreferrer" className="text-stardew-blue underline">smapi.io</a></p>
                <Image src={`${guide}/下载 SMAPI.webp`} alt="SMAPI 官网" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">2</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p><strong>Windows：</strong>解压后双击安装程序，按提示选择游戏目录即可。</p>
                <Image src={`${guide}/运行安装程序-Windows.webp`} alt="Windows 安装" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
                <p className="mt-3"><strong>macOS：</strong>解压后在终端运行 <code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">install on Mac.command</code></p>
                <Image src={`${guide}/运行安装程序-Mac.webp`} alt="macOS 安装" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">3</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>通过 SMAPI 启动游戏，如果窗口标题栏出现 SMAPI 版本号，说明安装成功。</p>
                <Image src={`${guide}/验证安装成功.webp`} alt="验证 SMAPI 安装" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
          </div>
        </section>

        {/* 安装 StarCoPlay */}
        <section className="parchment rounded-2xl p-6 md:p-8">
          <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6">
            📦 安装 StarCoPlay
          </h2>
          <div className="space-y-6">
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">1</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>从蓝奏云下载最新版本的 StarCoPlay（包含 Mod 和 Agent）。下载链接会在内测群中发布。</p>
                <Image src={`${guide}/下载安装包.webp`} alt="下载安装包" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">2</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>将压缩包中的 <code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">StarCoPlay</code> 文件夹拖入游戏的 Mods 目录：</p>
                <pre className="bg-stardew-brown/90 text-stardew-cream rounded-xl p-4 text-xs overflow-x-auto font-mono leading-relaxed">{`# Windows（默认路径）
C:\\...\\Steam\\steamapps\\common\\Stardew Valley\\Mods\\

# macOS（默认路径）
~/Library/Application Support/Steam/steamapps/common/Stardew Valley/Contents/MacOS/Mods/`}</pre>
                <Image src={`${guide}/安装 Mod-Windows.webp`} alt="安装 Mod" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">3</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>将压缩包中的 <code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">StarCoPlay-Agent</code> 文件夹放到你方便找到的位置（如桌面）。这是一个独立应用程序，无需安装额外依赖。</p>
                <Image src={`${guide}/放置 Agent 程序.webp`} alt="放置 Agent 程序" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
          </div>
        </section>

        {/* 获取 API Key */}
        <section className="parchment rounded-2xl p-6 md:p-8">
          <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6">
            🤖 获取 AI 模型 API Key
          </h2>
          <p className="text-sm text-stardew-brown/70 mb-6">
            AI 搭子需要调用大语言模型来思考和决策。以下以<strong>硅基流动（SiliconFlow）</strong>为例。
          </p>
          <div className="space-y-6">
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">1</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>打开硅基流动官网注册账号：<a href="https://cloud.siliconflow.cn" target="_blank" rel="noopener noreferrer" className="text-stardew-blue underline">cloud.siliconflow.cn</a>（支持手机号或邮箱注册）</p>
                <Image src={`${guide}/注册硅基流动账号.webp`} alt="注册硅基流动" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">2</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>在「模型广场」中，筛选类型为 <strong>Tools/Agent</strong>，找到推荐模型并复制名称。</p>
                <div className="bg-amber-50 border-2 border-amber-300 rounded-xl p-4 text-sm text-amber-900">
                  ⚠️ 使用模型需要付费，建议先充值少量余额（如 ¥10）体验。
                </div>
                <Image src={`${guide}/ 筛选 Tools Agent，然后复制名称.webp`} alt="筛选模型" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">3</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>在左侧菜单找到「API 密钥」，点击「新建 API 密钥」，复制生成的 Key（以 <code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">sk-</code> 开头）。</p>
                <Image src={`${guide}/创建 API Key.webp`} alt="创建 API Key" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <div className="bg-amber-50 border-2 border-amber-300 rounded-xl p-4 text-sm text-amber-900">
              ⚠️ API Key 仅显示一次，请立即复制保存。如果丢失需要重新创建。
            </div>
            <div className="bg-white/60 rounded-xl p-4 space-y-2 text-xs font-mono">
              <div className="flex justify-between"><span className="text-stardew-brown/60">协议方案：</span><span className="font-bold">第三方 API（OpenAI 兼容）</span></div>
              <div className="flex justify-between"><span className="text-stardew-brown/60">Base URL：</span><span className="font-bold">https://api.siliconflow.cn/v1</span></div>
              <div className="flex justify-between"><span className="text-stardew-brown/60">模型名：</span><span className="font-bold">Qwen/Qwen3.5-122B-A10B（推荐）</span></div>
              <div className="flex justify-between"><span className="text-stardew-brown/60">API Key：</span><span className="font-bold">你刚才复制的 sk-xxx</span></div>
            </div>
          </div>
        </section>

        {/* 配置 AI */}
        <section className="parchment rounded-2xl p-6 md:p-8">
          <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6">
            ⚙️ 配置你的 AI（Setup UI）
          </h2>
          <div className="space-y-6">
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">1</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>双击 Agent 文件夹中的启动程序，浏览器会自动打开 Setup UI 控制面板。粘贴你的 Platform Token，点击「验证并绑定」。</p>
                <Image src={`${guide}/启动 Setup UI.webp`} alt="启动 Setup UI" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">2</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>点击「步骤 2 · LLM 配置」，填入 API 信息：</p>
                <ul className="list-disc list-inside space-y-1 text-xs">
                  <li>协议方案：选择「第三方 API（DeepSeek、硅基流动等）」</li>
                  <li>API Key：粘贴你的 <code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">sk-xxx</code></li>
                  <li>Model：<code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">Qwen/Qwen3.5-122B-A10B</code></li>
                  <li>Base URL：<code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">https://api.siliconflow.cn/v1</code></li>
                </ul>
                <p>填完后点击「测试连接」，看到绿色 ✓ 说明配置正确。</p>
                <Image src={`${guide}/配置 LLM（步骤 2）.webp`} alt="配置 LLM" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <p className="text-xs text-stardew-brown/50">
              💡 当三个配置状态灯全部变绿后，「启动」按钮会激活。但先别按——还需要先启动游戏。
            </p>
          </div>
        </section>

        {/* 启动游戏 */}
        <section className="parchment rounded-2xl p-6 md:p-8">
          <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6">
            🎮 启动游戏
          </h2>
          <div className="bg-stardew-blue/10 border-2 border-stardew-blue/30 rounded-xl p-4 text-sm text-stardew-brown mb-6">
            💡 AI 搭子以 <strong>Player 2</strong> 的身份加入你的联机世界，需要运行两个游戏实例。
          </div>
          <div className="space-y-6">
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">1</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>通过 SMAPI 启动星露谷物语，进入你的存档，确保已建造<strong>联机小屋</strong>（找木匠罗宾建造，需要材料）并<strong>开启联机功能</strong>。</p>
                <Image src={`${guide}/启动你的游戏（第一个实例）.webp`} alt="启动第一个实例" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">2</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>回到 Setup UI，点击「步骤 3 · 游戏实例与 AI 角色」里的「启动新游戏实例」。或者手动再打开一个 SMAPI 窗口。</p>
              </div>
            </div>
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">3</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>在第二个游戏窗口中：</p>
                <ul className="list-disc list-inside space-y-1 text-xs">
                  <li>选择「加入局域网游戏」</li>
                  <li>地址填 <code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">127.0.0.1</code></li>
                  <li>选择一个角色进入游戏</li>
                </ul>
                <Image src={`${guide}/让第二个实例加入联机.webp`} alt="第二个实例加入联机" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 wood-panel rounded-full flex items-center justify-center">
                <span className="text-white text-xs font-bold">4</span>
              </div>
              <div className="flex-1 min-w-0 space-y-3 text-sm text-stardew-brown/80 leading-relaxed">
                <p>回到 Setup UI → 步骤 3，点击「刷新角色列表」，选择第二个玩家角色设为 AI 控制。当步骤 3 状态灯变绿后，点击<strong>「启动」</strong>大按钮。</p>
                <Image src={`${guide}/选择 AI 角色并启动 .webp`} alt="选择 AI 角色" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />
              </div>
            </div>
          </div>
        </section>

        {/* 验证 */}
        <section className="parchment rounded-2xl p-6 md:p-8">
          <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6">
            ✅ 验证 AI 搭子已加入
          </h2>
          <div className="space-y-4 mb-6">
            <div className="bg-white/40 rounded-xl p-4 flex items-start gap-3">
              <span className="text-lg">🏃</span>
              <div>
                <p className="font-bold text-stardew-brown text-sm">AI 角色开始自主活动</p>
                <p className="text-xs text-stardew-brown/60">它会在农场上走动、浇水、收获，看起来就像一个真实的玩家。</p>
              </div>
            </div>
            <div className="bg-white/40 rounded-xl p-4 flex items-start gap-3">
              <span className="text-lg">💬</span>
              <div>
                <p className="font-bold text-stardew-brown text-sm">你可以和它对话</p>
                <p className="text-xs text-stardew-brown/60">在游戏聊天框中输入消息，AI 会理解并回应你。试试说「你好」或「帮我浇一下水」。</p>
              </div>
            </div>
            <div className="bg-white/40 rounded-xl p-4 flex items-start gap-3">
              <span className="text-lg">📋</span>
              <div>
                <p className="font-bold text-stardew-brown text-sm">Setup UI 显示实时日志</p>
                <p className="text-xs text-stardew-brown/60">在控制面板中可以看到 AI 的思考过程和执行的动作。</p>
              </div>
            </div>
          </div>
          <Image src={`${guide}/验证 AI 搭子已加入.webp`} alt="AI 搭子在游戏中活动" width={800} height={500} className="rounded-xl border-2 border-stardew-wood/15 w-full h-auto" />

          <div className="bg-emerald-50 border-2 border-emerald-300 rounded-xl p-4 text-sm text-emerald-900 mt-6">
            ✅ 恭喜！你的 AI 搭子已经在农场里了。享受和它一起种田、探矿、过节的时光吧！
          </div>

          <div className="bg-white/40 rounded-xl p-4 text-sm text-stardew-brown/70 mt-4 space-y-1">
            <p className="font-bold text-stardew-brown">📝 内测须知：</p>
            <ul className="list-disc list-inside space-y-1 text-xs">
              <li>产品处于内测阶段，AI 偶尔会出现不智能的行为，这是正常的</li>
              <li>建议使用新存档体验，避免影响原有存档进度</li>
              <li>当前版本不建议引导 AI 进入矿洞或战斗场景</li>
              <li>欢迎反馈问题，帮助我们改进产品</li>
            </ul>
          </div>
        </section>

        {/* 常见问题 */}
        <section className="parchment rounded-2xl p-6 md:p-8">
          <h2 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-6">
            ❓ 遇到问题
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b-2 border-stardew-wood/20">
                  <th className="text-left py-3 text-stardew-brown font-bold">症状</th>
                  <th className="text-left py-3 text-stardew-brown font-bold">可能原因</th>
                  <th className="text-left py-3 text-stardew-brown font-bold">解决方案</th>
                </tr>
              </thead>
              <tbody className="text-stardew-brown/70">
                <tr className="border-b border-stardew-wood/10">
                  <td className="py-3">LLM 测试连接失败</td>
                  <td className="py-3">API Key 错误或余额不足</td>
                  <td className="py-3">检查 Key 是否正确，确认账户有余额</td>
                </tr>
                <tr className="border-b border-stardew-wood/10">
                  <td className="py-3">AI 不动/无响应</td>
                  <td className="py-3">桥接路径未正确配置</td>
                  <td className="py-3">检查步骤 1 目录路径是否正确</td>
                </tr>
                <tr className="border-b border-stardew-wood/10">
                  <td className="py-3">AI 卡在某处不动</td>
                  <td className="py-3">寻路被障碍物阻挡</td>
                  <td className="py-3">移除农场上阻挡的栅栏或物品</td>
                </tr>
                <tr className="border-b border-stardew-wood/10">
                  <td className="py-3">第二个实例连不上</td>
                  <td className="py-3">联机未开启</td>
                  <td className="py-3">在第一个实例中开启联机功能</td>
                </tr>
                <tr>
                  <td className="py-3">Token 验证失败</td>
                  <td className="py-3">Token 输入错误或网络问题</td>
                  <td className="py-3">重新复制粘贴 Token，检查网络</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div className="mt-6 text-sm text-stardew-brown/70">
            <p><strong>日志文件位置：</strong><code className="bg-stardew-brown/10 px-1.5 py-0.5 rounded text-xs">StarCoPlay-Agent/workspace/logs/agent.log</code></p>
          </div>
        </section>

        {/* Bottom CTA */}
        <div className="text-center py-8">
          <Link href="/auth" className="stardew-btn inline-flex px-8 py-4 rounded-xl text-lg font-bold">
            <Image src="/icons/Golden_Walnut.png" alt="" width={20} height={20} className="pixel-art" />
            准备好了？开始注册
          </Link>
        </div>
      </div>
    </div>
  );
}
