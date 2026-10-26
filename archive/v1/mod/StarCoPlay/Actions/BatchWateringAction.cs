using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.TerrainFeatures;
using StardewValley.Tools;

namespace StarCoPlay.Actions
{
    /// <summary>
    /// 批量浇水 — 扫描农场未浇水作物，依次走过去浇水。
    /// 非阻塞状态机，每帧 Tick() 推进一步。
    /// 使用 tool.DoFunction() 播放真实浇水动画，模拟正常玩家行为。
    /// </summary>
    public class BatchWateringAction : IBatchAction
    {
        private readonly IMonitor _monitor;
        private readonly ActionExecutor _executor;
        private readonly StateReader _stateReader;
        private readonly int _maxCount;

        private enum Phase { Scanning, Equipping, NavigatingToTile, Watering, WaitingForAnimation, NavigatingToWater, Refilling, WaitingForRefillAnimation, Complete }

        private Phase _currentPhase = Phase.Scanning;
        private Queue<Point> _targets = new();
        private Point _currentTarget;
        private int _wateredCount;
        private int _skippedCount;
        private int _refillCount;
        private int _animWaitTicks;
        private const int MaxAnimWaitTicks = 120; // 2 秒动画超时（60fps × 2s）
        private const int MaxRefillAttempts = 5;  // 单次批量浇水最多加水次数（防无限循环）
        private bool _isSuccess;
        private string _resultMessage = "";
        private string? _errorCode;

        public bool IsComplete => _currentPhase == Phase.Complete;
        public bool IsSuccess => _isSuccess;
        public string ResultMessage => _resultMessage;
        public string? ErrorCode => _errorCode;

        public BatchWateringAction(IMonitor monitor, ActionExecutor executor, StateReader stateReader, int maxCount = 999)
        {
            _monitor = monitor;
            _executor = executor;
            _stateReader = stateReader;
            _maxCount = maxCount;
        }

        public void Start()
        {
            try
            {
                _currentPhase = Phase.Scanning;
                _wateredCount = 0;
                _skippedCount = 0;
                _refillCount = 0;

                var farm = Game1.getFarm();
                if (farm == null)
                {
                    _monitor.Log("BatchWatering.Start: 无法访问农场对象", LogLevel.Error);
                    Complete(false, "无法访问农场", "NO_FARM");
                    return;
                }

                // 扫描所有未浇水且有作物的 HoeDirt
                var playerTile = Game1.player.Tile;
                var unwateredTiles = new List<(Point tile, float dist)>();

                foreach (var pair in farm.terrainFeatures.Pairs)
                {
                    if (pair.Value is not HoeDirt dirt) continue;
                    if (dirt.crop == null) continue;
                    if (dirt.state.Value == 1) continue; // 已浇水

                    var tile = new Point((int)pair.Key.X, (int)pair.Key.Y);
                    float dist = Vector2.Distance(pair.Key, playerTile);
                    unwateredTiles.Add((tile, dist));
                }

                if (unwateredTiles.Count == 0)
                {
                    _monitor.Log("BatchWatering.Start: 扫描完成，没有需要浇水的作物", LogLevel.Info);
                    Complete(true, "没有需要浇水的作物", null);
                    return;
                }

                // 贪心最近邻排序：每次从当前位置选最近的下一块，避免来回跑
                var remaining = unwateredTiles.Select(t => t.tile).ToList();
                var current = new Point((int)playerTile.X, (int)playerTile.Y);
                while (remaining.Count > 0)
                {
                    int bestIdx = 0;
                    float bestDist = float.MaxValue;
                    for (int i = 0; i < remaining.Count; i++)
                    {
                        float d = Math.Abs(remaining[i].X - current.X) + Math.Abs(remaining[i].Y - current.Y); // 曼哈顿距离
                        if (d < bestDist)
                        {
                            bestDist = d;
                            bestIdx = i;
                        }
                    }
                    _targets.Enqueue(remaining[bestIdx]);
                    current = remaining[bestIdx];
                    remaining.RemoveAt(bestIdx);
                }

                _monitor.Log($"BatchWatering.Start: 扫描到 {_targets.Count} 块未浇水作物，玩家位置 ({(int)playerTile.X},{(int)playerTile.Y})", LogLevel.Info);

                // 检查浇水壶状态
                var wateringCan = Game1.player.Items.OfType<WateringCan>().FirstOrDefault();
                if (wateringCan == null)
                {
                    _monitor.Log("BatchWatering.Start: 背包中找不到浇水壶", LogLevel.Warn);
                    Complete(false, "背包中找不到浇水壶", "NO_WATERING_CAN");
                    return;
                }

                _monitor.Log($"BatchWatering.Start: 浇水壶状态 — 水量 {wateringCan.WaterLeft}/{wateringCan.waterCanMax}，升级等级 {wateringCan.UpgradeLevel}", LogLevel.Info);

                // 装备浇水壶
                if (!_executor.EquipItemByName("Watering Can"))
                {
                    _monitor.Log("BatchWatering.Start: 装备浇水壶失败（不在快捷栏）", LogLevel.Warn);
                    Complete(false, "快捷栏中找不到浇水壶", "NO_WATERING_CAN");
                    return;
                }

                _monitor.Log($"BatchWatering: 开始批量浇水，目标 {_targets.Count} 块，体力 {Game1.player.Stamina}/{Game1.player.maxStamina.Value}", LogLevel.Info);

                // 如果水壶启动时就是空的，先去加水
                if (wateringCan.WaterLeft <= 0)
                {
                    _monitor.Log("BatchWatering.Start: 水壶为空，先去加水", LogLevel.Info);
                    if (TryStartRefill())
                    {
                        return; // 进入加水流程，加完水后自动回到 NavigatingToTile
                    }
                    else
                    {
                        Complete(false, "水壶没水且找不到水源", "NO_WATER_SOURCE");
                        return;
                    }
                }

                _currentPhase = Phase.NavigatingToTile;
            }
            catch (Exception ex)
            {
                _monitor.Log($"BatchWatering.Start 异常: {ex.Message}\n{ex.StackTrace}", LogLevel.Error);
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
                    case Phase.Watering:
                        TickWatering();
                        break;
                    case Phase.WaitingForAnimation:
                        TickWaitingForAnimation();
                        break;
                    case Phase.NavigatingToWater:
                        TickNavigatingToWater();
                        break;
                    case Phase.Refilling:
                        TickRefilling();
                        break;
                    case Phase.WaitingForRefillAnimation:
                        TickWaitingForRefillAnimation();
                        break;
                    // Scanning, Equipping, Complete — 不需要 Tick
                }
            }
            catch (Exception ex)
            {
                _monitor.Log($"BatchWatering.Tick 异常 (phase={_currentPhase}): {ex.Message}\n{ex.StackTrace}", LogLevel.Error);
                Complete(false, $"执行异常 (phase={_currentPhase}): {ex.Message}", "TICK_ERROR");
            }
        }

        public void Cancel()
        {
            _monitor.Log($"BatchWatering: 收到取消请求，当前 phase={_currentPhase}，已浇水 {_wateredCount} 块", LogLevel.Info);

            // 确保取消时清理 UsingTool 状态，防止玩家冻结
            ForceResetToolState();

            _executor.CancelCurrent();
            Complete(false, $"已取消，已浇水 {_wateredCount} 块", "CANCELLED");
        }

        private void TickNavigating()
        {
            // 还在走路，等
            if (_executor.IsBusy) return;

            // 检查队列是否还有目标
            if (_targets.Count == 0)
            {
                _monitor.Log($"BatchWatering: 队列已空，浇水完成。已浇水 {_wateredCount}，跳过 {_skippedCount}", LogLevel.Info);
                Complete(true, $"浇水完成，共浇了 {_wateredCount} 块（跳过 {_skippedCount} 块）", null);
                return;
            }

            _currentTarget = _targets.Dequeue();

            // 发起导航
            _monitor.Log($"BatchWatering: 导航到 ({_currentTarget.X},{_currentTarget.Y})，剩余 {_targets.Count} 块", LogLevel.Debug);
            bool navOk = _executor.NavigateTo(_currentTarget);
            if (!navOk)
            {
                // 导航失败，跳过这块，尝试下一块
                _skippedCount++;
                _monitor.Log($"BatchWatering: 无法导航到 ({_currentTarget.X},{_currentTarget.Y})，跳过（累计跳过 {_skippedCount}）", LogLevel.Warn);
                // 保持 NavigatingToTile 阶段，下次 Tick 会尝试下一块
                return;
            }

            _currentPhase = Phase.Watering;
        }

        private void TickWatering()
        {
            // 还没到达，等导航完成
            if (_executor.IsBusy) return;

            var player = Game1.player;
            var tool = player.CurrentTool as WateringCan;
            var location = Game1.currentLocation;

            // 前置检查
            if (tool == null)
            {
                _monitor.Log("BatchWatering.TickWatering: 当前工具不是浇水壶，尝试重新装备", LogLevel.Warn);
                if (!_executor.EquipItemByName("Watering Can"))
                {
                    _monitor.Log("BatchWatering.TickWatering: 重新装备浇水壶失败", LogLevel.Error);
                    Complete(false, $"浇水壶丢失，已浇水 {_wateredCount} 块", "TOOL_LOST");
                    return;
                }
                tool = player.CurrentTool as WateringCan;
                if (tool == null)
                {
                    Complete(false, $"无法装备浇水壶，已浇水 {_wateredCount} 块", "TOOL_LOST");
                    return;
                }
            }

            if (location == null)
            {
                _monitor.Log("BatchWatering.TickWatering: 当前地图为 null", LogLevel.Error);
                Complete(false, "当前地图为 null", "NO_LOCATION");
                return;
            }

            // 检查目标瓦片是否仍需浇水
            var tileVec = new Vector2(_currentTarget.X, _currentTarget.Y);
            if (location.terrainFeatures.TryGetValue(tileVec, out var terrain)
                && terrain is HoeDirt dirt && dirt.state.Value != 1)
            {
                // 朝向目标瓦片
                int direction = GetDirectionTo(_currentTarget.X, _currentTarget.Y);
                player.faceDirection(direction);

                _monitor.Log($"BatchWatering: 浇水 ({_currentTarget.X},{_currentTarget.Y})，朝向={direction}，水量={tool.WaterLeft}，体力={player.Stamina:F0}", LogLevel.Debug);

                try
                {
                    // 使用 DoFunction 播放真实浇水动画
                    tool.DoFunction(location, _currentTarget.X * 64, _currentTarget.Y * 64, 1, player);
                    _wateredCount++;

                    _monitor.Log($"BatchWatering: 浇水完成 ({_currentTarget.X},{_currentTarget.Y})，进度 {_wateredCount}/{_wateredCount + _targets.Count}，UsingTool={player.UsingTool}", LogLevel.Debug);

                    // 进入等待动画完成阶段
                    _animWaitTicks = 0;
                    _currentPhase = Phase.WaitingForAnimation;
                    return;
                }
                catch (Exception ex)
                {
                    _monitor.Log($"BatchWatering: DoFunction 异常 @ ({_currentTarget.X},{_currentTarget.Y}): {ex.Message}\n{ex.StackTrace}", LogLevel.Error);
                    // DoFunction 异常时强制重置工具状态
                    ForceResetToolState();
                    _skippedCount++;
                }
            }
            else
            {
                // 该格已不需要浇水（可能已被雨水浇灌），跳过
                _skippedCount++;
                _monitor.Log($"BatchWatering: ({_currentTarget.X},{_currentTarget.Y}) 不需要浇水（state={((terrain as HoeDirt)?.state.Value ?? -1)}），跳过", LogLevel.Debug);
            }

            // 检查是否继续
            if (!CheckContinueConditions()) return;

            // 继续下一块
            _currentPhase = Phase.NavigatingToTile;
        }

        private void TickWaitingForAnimation()
        {
            _animWaitTicks++;
            var player = Game1.player;

            // 动画完成条件：UsingTool 变为 false
            if (!player.UsingTool)
            {
                _monitor.Log($"BatchWatering: 动画完成，等待了 {_animWaitTicks} ticks ({_animWaitTicks / 60.0:F1}s)", LogLevel.Trace);

                if (!CheckContinueConditions()) return;

                // 继续下一块
                _currentPhase = Phase.NavigatingToTile;
                return;
            }

            // 动画超时 — 强制重置，防止玩家冻结
            if (_animWaitTicks >= MaxAnimWaitTicks)
            {
                _monitor.Log($"BatchWatering: 动画等待超时 ({MaxAnimWaitTicks} ticks)，强制重置 UsingTool。" +
                    $" player.UsingTool={player.UsingTool}, player.canMove={player.canMove}", LogLevel.Warn);

                ForceResetToolState();

                if (!CheckContinueConditions()) return;

                _currentPhase = Phase.NavigatingToTile;
            }
        }

        /// <summary>检查是否满足继续浇水的条件。返回 false 表示应停止。</summary>
        private bool CheckContinueConditions()
        {
            var player = Game1.player;

            if (player.Stamina < 10)
            {
                _monitor.Log($"BatchWatering: 体力不足 ({player.Stamina:F0})，停止。已浇水 {_wateredCount}，跳过 {_skippedCount}", LogLevel.Info);
                Complete(true, $"体力不足，已浇水 {_wateredCount} 块（保留回家体力）", null);
                return false;
            }

            var can = player.CurrentTool as WateringCan;
            if (can != null && can.WaterLeft <= 0)
            {
                // 检查是否超过加水次数上限
                if (_refillCount >= MaxRefillAttempts)
                {
                    _monitor.Log($"BatchWatering: 已加水 {_refillCount} 次达到上限，停止。已浇水 {_wateredCount}，跳过 {_skippedCount}", LogLevel.Info);
                    Complete(true, $"加水次数达到上限，已浇水 {_wateredCount} 块", null);
                    return false;
                }

                // 尝试找水源并自动加水
                if (TryStartRefill())
                {
                    return false; // 进入加水流程，暂停浇水
                }
                else
                {
                    _monitor.Log($"BatchWatering: 水壶没水且找不到水源，停止。已浇水 {_wateredCount}，跳过 {_skippedCount}", LogLevel.Info);
                    Complete(true, $"水壶没水且附近找不到水源，已浇水 {_wateredCount} 块", null);
                    return false;
                }
            }

            if (_wateredCount >= _maxCount)
            {
                _monitor.Log($"BatchWatering: 达到上限 {_maxCount}，停止", LogLevel.Info);
                Complete(true, $"达到上限，已浇水 {_wateredCount} 块", null);
                return false;
            }

            if (_targets.Count == 0)
            {
                _monitor.Log($"BatchWatering: 队列已空，浇水完成。已浇水 {_wateredCount}，跳过 {_skippedCount}", LogLevel.Info);
                Complete(true, $"浇水完成，共浇了 {_wateredCount} 块（跳过 {_skippedCount} 块）", null);
                return false;
            }

            return true;
        }

        /*────────────────────────────────────────────
         *  自动加水流程
         *────────────────────────────────────────────*/

        /// <summary>尝试启动加水流程：找到最近水源并导航过去。</summary>
        private bool TryStartRefill()
        {
            var location = Game1.currentLocation;
            if (location == null) return false;

            var waterTile = FindNearestWaterTile(location);
            if (waterTile == null)
            {
                _monitor.Log("BatchWatering.TryStartRefill: 当前地图没有可用水源", LogLevel.Warn);
                return false;
            }

            var target = waterTile.Value;
            _monitor.Log($"BatchWatering: 水壶没水，前往水源 ({target.X},{target.Y}) 加水（第 {_refillCount + 1} 次）", LogLevel.Info);

            // 导航到水源旁边（找一个相邻的可行走瓦片）
            var adjacentTile = FindAdjacentWalkableTile(location, target);
            if (adjacentTile == null)
            {
                _monitor.Log($"BatchWatering.TryStartRefill: 水源 ({target.X},{target.Y}) 旁边没有可行走的瓦片", LogLevel.Warn);
                return false;
            }

            _currentTarget = adjacentTile.Value; // 临时复用 _currentTarget 存储水源的相邻瓦片
            bool navOk = _executor.NavigateTo(adjacentTile.Value);
            if (!navOk)
            {
                _monitor.Log($"BatchWatering.TryStartRefill: 无法导航到水源旁 ({adjacentTile.Value.X},{adjacentTile.Value.Y})", LogLevel.Warn);
                return false;
            }

            // 记录水源瓦片坐标（导航目标是旁边的瓦片，加水要对着水源）
            _waterSourceTile = target;
            _currentPhase = Phase.NavigatingToWater;
            return true;
        }

        /// <summary>水源瓦片坐标（加水时使用 DoFunction 的目标）。</summary>
        private Point _waterSourceTile;

        /// <summary>在当前地图查找最近的水源瓦片。</summary>
        private Point? FindNearestWaterTile(GameLocation location)
        {
            var playerTile = Game1.player.Tile;
            float bestDist = float.MaxValue;
            Point? best = null;

            // 扫描地图上的水源瓦片
            // SDV 标准农场的池塘大约在 (40-50, 80-90) 区域，但其他农场布局不同
            // 用 isOpenWater 通用检测，搜索范围限制在合理距离内（60 tiles）
            var mapWidth = location.Map.Layers[0].LayerWidth;
            var mapHeight = location.Map.Layers[0].LayerHeight;

            for (int x = 0; x < mapWidth; x++)
            {
                for (int y = 0; y < mapHeight; y++)
                {
                    if (!location.isOpenWater(x, y)) continue;

                    float dist = Vector2.Distance(new Vector2(x, y), playerTile);
                    if (dist < bestDist)
                    {
                        bestDist = dist;
                        best = new Point(x, y);
                    }
                }
            }

            if (best != null)
                _monitor.Log($"BatchWatering.FindNearestWaterTile: 最近水源 ({best.Value.X},{best.Value.Y})，距离 {bestDist:F1}", LogLevel.Debug);

            return best;
        }

        /// <summary>找到水源瓦片旁边的一个可行走瓦片（用于站立加水）。</summary>
        private Point? FindAdjacentWalkableTile(GameLocation location, Point waterTile)
        {
            // 优先检查四方向，然后对角线
            Point[] offsets = new[]
            {
                new Point(0, -1), // 上
                new Point(0, 1),  // 下
                new Point(-1, 0), // 左
                new Point(1, 0),  // 右
                new Point(-1, -1), new Point(1, -1), new Point(-1, 1), new Point(1, 1)
            };

            var playerTile = Game1.player.Tile;
            Point? best = null;
            float bestDist = float.MaxValue;

            foreach (var offset in offsets)
            {
                int tx = waterTile.X + offset.X;
                int ty = waterTile.Y + offset.Y;

                // 检查瓦片是否可行走（无碰撞、无占据）
                var tileVec = new Vector2(tx, ty);
                if (location.isTileOnMap(tileVec)
                    && !location.isWaterTile(tx, ty)
                    && !location.Objects.ContainsKey(tileVec)
                    && location.isTilePassable(new xTile.Dimensions.Location(tx, ty), Game1.viewport))
                {
                    float dist = Vector2.Distance(new Vector2(tx, ty), playerTile);
                    if (dist < bestDist)
                    {
                        bestDist = dist;
                        best = new Point(tx, ty);
                    }
                }
            }

            return best;
        }

        private void TickNavigatingToWater()
        {
            if (_executor.IsBusy) return;

            // 到达水源旁边，开始加水
            _monitor.Log($"BatchWatering: 到达水源旁，准备对 ({_waterSourceTile.X},{_waterSourceTile.Y}) 加水", LogLevel.Info);
            _currentPhase = Phase.Refilling;
        }

        private void TickRefilling()
        {
            var player = Game1.player;
            var tool = player.CurrentTool as WateringCan;
            var location = Game1.currentLocation;

            if (tool == null)
            {
                // 尝试重新装备
                if (!_executor.EquipItemByName("Watering Can"))
                {
                    _monitor.Log("BatchWatering.TickRefilling: 装备浇水壶失败", LogLevel.Error);
                    Complete(false, $"加水时浇水壶丢失，已浇水 {_wateredCount} 块", "TOOL_LOST");
                    return;
                }
                tool = player.CurrentTool as WateringCan;
                if (tool == null)
                {
                    Complete(false, $"无法装备浇水壶，已浇水 {_wateredCount} 块", "TOOL_LOST");
                    return;
                }
            }

            if (location == null)
            {
                Complete(false, "当前地图为 null", "NO_LOCATION");
                return;
            }

            // 朝向水源
            int direction = GetDirectionTo(_waterSourceTile.X, _waterSourceTile.Y);
            player.faceDirection(direction);

            _monitor.Log($"BatchWatering: 对水源 ({_waterSourceTile.X},{_waterSourceTile.Y}) 使用浇水壶加水，水量 {tool.WaterLeft}/{tool.waterCanMax}", LogLevel.Info);

            try
            {
                // 对水源使用浇水壶 → 游戏自动加满水
                tool.DoFunction(location, _waterSourceTile.X * 64, _waterSourceTile.Y * 64, 1, player);
                _refillCount++;

                _animWaitTicks = 0;
                _currentPhase = Phase.WaitingForRefillAnimation;
            }
            catch (Exception ex)
            {
                _monitor.Log($"BatchWatering.TickRefilling 异常: {ex.Message}", LogLevel.Error);
                ForceResetToolState();
                // 加水失败，尝试继续浇水（可能还有残余水量）
                _currentPhase = Phase.NavigatingToTile;
            }
        }

        private void TickWaitingForRefillAnimation()
        {
            _animWaitTicks++;
            var player = Game1.player;

            if (!player.UsingTool)
            {
                var can = player.CurrentTool as WateringCan;
                int waterNow = can?.WaterLeft ?? -1;
                _monitor.Log($"BatchWatering: 加水完成，水量 {waterNow}/{can?.waterCanMax ?? 0}，继续浇水（剩余 {_targets.Count} 块）", LogLevel.Info);

                // 加水完成，回到浇水流程
                _currentPhase = Phase.NavigatingToTile;
                return;
            }

            if (_animWaitTicks >= MaxAnimWaitTicks)
            {
                _monitor.Log($"BatchWatering: 加水动画超时，强制重置", LogLevel.Warn);
                ForceResetToolState();
                _currentPhase = Phase.NavigatingToTile;
            }
        }

        /// <summary>
        /// 强制重置工具使用状态，防止玩家被冻结。
        /// DoFunction 触发动画后设置 UsingTool=true，如果动画回调未正常完成，
        /// 该标志永远不会被重置，导致玩家无法移动。
        /// </summary>
        private void ForceResetToolState()
        {
            var player = Game1.player;
            if (player.UsingTool)
            {
                _monitor.Log($"BatchWatering.ForceResetToolState: 重置 UsingTool (was true), canMove={player.canMove}", LogLevel.Warn);
                player.UsingTool = false;
                player.canMove = true;
                player.forceCanMove();
                player.completelyStopAnimatingOrDoingAction();
            }
        }

        /// <summary>计算从玩家到目标瓦片的朝向（0=上 1=右 2=下 3=左）。</summary>
        private static int GetDirectionTo(int tileX, int tileY)
        {
            int dx = tileX - (int)Game1.player.Tile.X;
            int dy = tileY - (int)Game1.player.Tile.Y;
            if (Math.Abs(dx) > Math.Abs(dy))
                return dx > 0 ? 1 : 3;
            return dy > 0 ? 2 : 0;
        }

        private void Complete(bool success, string message, string? errorCode)
        {
            _isSuccess = success;
            _resultMessage = message;
            _errorCode = errorCode;
            _currentPhase = Phase.Complete;

            // 完成时确保工具状态干净
            ForceResetToolState();

            string level = success ? "Info" : "Warn";
            _monitor.Log($"BatchWatering 完成: success={success}, watered={_wateredCount}, skipped={_skippedCount}, refilled={_refillCount}, message={message}",
                success ? LogLevel.Info : LogLevel.Warn);
        }
    }
}
