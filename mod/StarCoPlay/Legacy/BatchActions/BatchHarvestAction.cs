using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.TerrainFeatures;

namespace StarCoPlay.Actions
{
    /// <summary>
    /// 批量收获 — 扫描农场成熟作物，依次走过去收获。
    /// 非阻塞状态机，每帧 Tick() 推进一步。
    /// </summary>
    public class BatchHarvestAction : IBatchAction
    {
        private readonly IMonitor _monitor;
        private readonly ActionExecutor _executor;
        private readonly StateReader _stateReader;

        private enum Phase { Scanning, NavigatingToTile, Harvesting, Complete }

        private Phase _currentPhase = Phase.Scanning;
        private Queue<Point> _targets = new();
        private Point _currentTarget;
        private int _harvestedCount;
        private bool _isSuccess;
        private string _resultMessage = "";
        private string? _errorCode;

        public bool IsComplete => _currentPhase == Phase.Complete;
        public bool IsSuccess => _isSuccess;
        public string ResultMessage => _resultMessage;
        public string? ErrorCode => _errorCode;

        public BatchHarvestAction(IMonitor monitor, ActionExecutor executor, StateReader stateReader)
        {
            _monitor = monitor;
            _executor = executor;
            _stateReader = stateReader;
        }

        public void Start()
        {
            try
            {
                _currentPhase = Phase.Scanning;
                _harvestedCount = 0;

                var farm = Game1.getFarm();
                if (farm == null)
                {
                    _monitor.Log("BatchHarvest.Start: 无法访问农场对象", LogLevel.Error);
                    Complete(false, "无法访问农场", "NO_FARM");
                    return;
                }

                var playerTile = Game1.player.Tile;
                var harvestTiles = new List<(Point tile, float dist)>();

                foreach (var pair in farm.terrainFeatures.Pairs)
                {
                    if (pair.Value is not HoeDirt dirt) continue;
                    if (dirt.crop == null) continue;
                    if (!dirt.readyForHarvest()) continue;

                    var tile = new Point((int)pair.Key.X, (int)pair.Key.Y);
                    float dist = Vector2.Distance(pair.Key, playerTile);
                    harvestTiles.Add((tile, dist));
                }

                if (harvestTiles.Count == 0)
                {
                    _monitor.Log("BatchHarvest.Start: 扫描完成，没有成熟可收获的作物", LogLevel.Info);
                    Complete(true, "没有成熟可收获的作物", null);
                    return;
                }

                // 按距离排序
                foreach (var item in harvestTiles.OrderBy(t => t.dist))
                    _targets.Enqueue(item.tile);

                _monitor.Log($"BatchHarvest.Start: 扫描到 {_targets.Count} 块成熟作物，玩家位置 ({(int)playerTile.X},{(int)playerTile.Y})", LogLevel.Info);
                _currentPhase = Phase.NavigatingToTile;
            }
            catch (Exception ex)
            {
                _monitor.Log($"BatchHarvest.Start 异常: {ex.Message}\n{ex.StackTrace}", LogLevel.Error);
                Complete(false, $"启动失败: {ex.Message}", "START_ERROR");
            }
        }

        public void Tick()
        {
            try
            {
                switch (_currentPhase)
                {
                    case Phase.NavigatingToTile:
                        TickNavigating();
                        break;
                    case Phase.Harvesting:
                        TickHarvesting();
                        break;
                }
            }
            catch (Exception ex)
            {
                _monitor.Log($"BatchHarvest.Tick 异常: {ex.Message}", LogLevel.Error);
                Complete(false, $"执行异常: {ex.Message}", "TICK_ERROR");
            }
        }

        public void Cancel()
        {
            _executor.CancelCurrent();
            Complete(false, $"已取消，已收获 {_harvestedCount} 块", "CANCELLED");
        }

        private void TickNavigating()
        {
            if (_executor.IsBusy) return;

            if (_targets.Count == 0)
            {
                _monitor.Log($"BatchHarvest: 队列已空，收获完成。已收获 {_harvestedCount}", LogLevel.Info);
                Complete(true, $"收获完成，共收了 {_harvestedCount} 块", null);
                return;
            }

            // 检查背包是否满了
            var player = Game1.player;
            int usedSlots = player.Items.Count(i => i != null);
            if (usedSlots >= player.Items.Count)
            {
                _monitor.Log($"BatchHarvest: 背包已满 ({usedSlots}/{player.Items.Count})，停止。已收获 {_harvestedCount}", LogLevel.Info);
                Complete(true, $"背包已满，已收获 {_harvestedCount} 块", null);
                return;
            }

            _currentTarget = _targets.Dequeue();

            _monitor.Log($"BatchHarvest: 导航到 ({_currentTarget.X},{_currentTarget.Y})，剩余 {_targets.Count} 块", LogLevel.Debug);
            bool navOk = _executor.NavigateTo(_currentTarget);
            if (!navOk)
            {
                _monitor.Log($"BatchHarvest: 无法导航到 ({_currentTarget.X},{_currentTarget.Y})，跳过", LogLevel.Warn);
                return;
            }

            _currentPhase = Phase.Harvesting;
        }

        private void TickHarvesting()
        {
            if (_executor.IsBusy) return;

            // 到达目标，通过交互来收获
            bool ok = _executor.InteractWithTile(_currentTarget.X, _currentTarget.Y);
            _harvestedCount++;

            _monitor.Log($"BatchHarvest: 收获 ({_currentTarget.X},{_currentTarget.Y}) → {(ok ? "成功" : "交互失败")}，进度 {_harvestedCount}/{_harvestedCount + _targets.Count}", LogLevel.Debug);

            if (_targets.Count == 0)
            {
                _monitor.Log($"BatchHarvest: 队列已空，收获完成。已收获 {_harvestedCount}", LogLevel.Info);
                Complete(true, $"收获完成，共收了 {_harvestedCount} 块", null);
                return;
            }

            // 检查背包
            var player = Game1.player;
            int usedSlots = player.Items.Count(i => i != null);
            if (usedSlots >= player.Items.Count)
            {
                _monitor.Log($"BatchHarvest: 背包已满 ({usedSlots}/{player.Items.Count})，停止。已收获 {_harvestedCount}", LogLevel.Info);
                Complete(true, $"背包已满，已收获 {_harvestedCount} 块", null);
                return;
            }

            _currentPhase = Phase.NavigatingToTile;
        }

        private void Complete(bool success, string message, string? errorCode)
        {
            _isSuccess = success;
            _resultMessage = message;
            _errorCode = errorCode;
            _currentPhase = Phase.Complete;
            _monitor.Log($"BatchHarvest 完成: success={success}, harvested={_harvestedCount}, message={message}",
                success ? LogLevel.Info : LogLevel.Warn);
        }
    }
}
