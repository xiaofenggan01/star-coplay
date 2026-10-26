export default function Footer() {
  return (
    <footer className="bg-stardew-brown text-stardew-cream/70 py-12">
      <div className="max-w-6xl mx-auto px-4">
        <div className="grid md:grid-cols-3 gap-8 mb-8">
          <div>
            <h3 className="font-[family-name:var(--font-pixel)] text-stardew-gold text-sm mb-3">
              Stardew AI CoPlay
            </h3>
            <p className="text-sm leading-relaxed">
              星露谷 AI 搭子启动器
              <br />
              你的像素世界永远在线的伙伴
            </p>
          </div>

          <div>
            <h4 className="font-bold text-stardew-cream mb-3">页面</h4>
            <ul className="space-y-2 text-sm">
              <li><a href="/" className="hover:text-stardew-gold transition-colors">首页</a></li>
              <li><a href="/dashboard" className="hover:text-stardew-gold transition-colors">仪表盘</a></li>
              <li><a href="/config" className="hover:text-stardew-gold transition-colors">配置</a></li>
              <li><a href="/setup" className="hover:text-stardew-gold transition-colors">安装向导</a></li>
            </ul>
          </div>

          <div>
            <h4 className="font-bold text-stardew-cream mb-3">关于</h4>
            <p className="text-sm leading-relaxed">
              基于 Star CoPlay 项目
              <br />
              <span className="text-stardew-cream/50">
                Stardew Valley is developed by ConcernedApe
              </span>
            </p>
          </div>
        </div>

        <div className="border-t border-stardew-cream/10 pt-6 text-center text-xs text-stardew-cream/40">
          <p>&copy; 2025 Stardew AI CoPlay</p>
        </div>
      </div>
    </footer>
  );
}
