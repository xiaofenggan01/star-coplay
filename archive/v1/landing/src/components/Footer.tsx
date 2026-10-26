export default function Footer() {
  return (
    <footer className="bg-stardew-brown text-stardew-cream/70 py-12">
      <div className="max-w-6xl mx-auto px-4">
        <div className="grid md:grid-cols-3 gap-8 mb-8">
          {/* Brand */}
          <div>
            <h3 className="font-[family-name:var(--font-pixel)] text-stardew-gold text-sm mb-3">
              Star CoPlay
            </h3>
            <p className="text-sm leading-relaxed">
              AI Co-Player for Stardew Valley
              <br />
              进化酒馆 Agent 黑客松 2026 作品
            </p>
          </div>

          {/* Links */}
          <div>
            <h4 className="font-bold text-stardew-cream mb-3">链接</h4>
            <ul className="space-y-2 text-sm">
              <li>
                <a
                  href="https://github.com/luy-0/star-coplay"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-stardew-gold transition-colors"
                >
                  GitHub 仓库
                </a>
              </li>
              <li>
                <a
                  href="https://smapi.io/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-stardew-gold transition-colors"
                >
                  SMAPI 官网
                </a>
              </li>
              <li>
                <a
                  href="https://www.stardewvalley.net/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-stardew-gold transition-colors"
                >
                  星露谷物语官网
                </a>
              </li>
            </ul>
          </div>

          {/* Team */}
          <div>
            <h4 className="font-bold text-stardew-cream mb-3">团队</h4>
            <p className="text-sm leading-relaxed">
              小海 · 康康 · 晓风 · 图图
              <br />
              <span className="text-stardew-cream/50">
                我们都是星露谷深度玩家
              </span>
            </p>
          </div>
        </div>

        <div className="border-t border-stardew-cream/10 pt-6 text-center text-xs text-stardew-cream/40">
          <p>
            Star CoPlay is a fan-made project. Stardew Valley is developed by
            ConcernedApe.
          </p>
          <p className="mt-1">© 2026 Star CoPlay Team</p>
        </div>
      </div>
    </footer>
  );
}
