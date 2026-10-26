namespace StarCoPlay
{
    /// <summary>Mod 配置（自动读写 config.json）。</summary>
    public class ModConfig
    {
        /// <summary>是否在每天开始时自动执行 Demo 行为。</summary>
        public bool EnableDailyDemo { get; set; } = true;

        /// <summary>卡住检测阈值（tick 数，默认 300 = 5 秒无移动判为卡住）。</summary>
        public int StuckThresholdTicks { get; set; } = 300;
    }
}
