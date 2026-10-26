using Microsoft.Xna.Framework;
using StarCoPlay.Logging;
using StardewModdingAPI;

namespace StarCoPlay
{
    /// <summary>
    /// 卡住检测器 — 监控玩家是否长时间无位移。
    /// 灵感来源：Starbot 的 300 帧卡住检测机制。
    /// </summary>
    public class StuckDetector
    {
        private readonly IMonitor _monitor;
        private readonly int _thresholdTicks;
        private ModLogger? _logger;

        private Vector2 _lastPosition;
        private int _stuckTicks;

        /// <summary>是否判定为卡住。</summary>
        public bool IsStuck => _stuckTicks >= _thresholdTicks;

        /// <summary>当前无移动累积 tick 数。</summary>
        public int StuckTicks => _stuckTicks;

        public StuckDetector(IMonitor monitor, int thresholdTicks = 300)
        {
            _monitor = monitor;
            _thresholdTicks = thresholdTicks;
            _lastPosition = Vector2.Zero;
            _stuckTicks = 0;
        }

        /// <summary>注入文件日志。</summary>
        public void SetLogger(ModLogger logger) => _logger = logger;

        /// <summary>
        /// 更新检测（建议每秒调用一次，传入当前瓦片坐标）。
        /// 每秒检测一次但累加 60 ticks（对应实际经过的游戏帧数）。
        /// </summary>
        public void Update(Vector2 currentTile)
        {
            if (Vector2.Distance(currentTile, _lastPosition) < 0.5f)
            {
                _stuckTicks += 60; // 累加一秒的帧数
                if (_stuckTicks >= _thresholdTicks && _stuckTicks - 60 < _thresholdTicks)
                {
                    _monitor.Log($"卡住检测: 已 {_stuckTicks / 60} 秒无移动 @ ({(int)currentTile.X},{(int)currentTile.Y})", LogLevel.Warn);
                    _logger?.LogExecution($"卡住检测触发: pos=({(int)currentTile.X},{(int)currentTile.Y}) stuck={_stuckTicks}ticks ({_stuckTicks / 60}秒)", LogLevel.Warn);
                }
            }
            else
            {
                _stuckTicks = 0;
            }

            _lastPosition = currentTile;
        }

        /// <summary>重置计数器（换地图、新任务时调用）。</summary>
        public void Reset()
        {
            _stuckTicks = 0;
            _lastPosition = Vector2.Zero;
        }
    }
}
