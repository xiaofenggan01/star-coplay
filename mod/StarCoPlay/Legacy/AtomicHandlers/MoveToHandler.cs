// ============================================================
// [LEGACY] 旧版移动实现 — 已被当前 move_to 执行原子替代
// 保留原因：UseInputBridgeHandlers=false 时的 fallback
// 计划：protocol-capability-completion 迭代中评估清理
// ============================================================
using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using System.Text.Json;
using StarCoPlay;
using StarCoPlay.GameServices.Movement;
using StarCoPlay.V2.Compound;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2;

/// <summary>
/// V2 move_to capability handler.
///
/// 设计原则：移动与 warp 彻底解耦。
/// - 同地图移动：PathFindController 挂载到 player.controller，引擎自动处理移动+动画+碰撞
/// - 跨地图 warp：先导航到出口 tile → 手动 warpFarmer → 等待地图加载 → 新地图继续
///
/// 状态机阶段：
///   同地图: Navigate → Complete
///   跨地图(有路径): WalkingToExit → Warping → WaitingForMapLoad → WalkingToTarget → Complete
///   跨地图(无路径): 返回 PATH_BLOCKED，绝不以传送代替导航
/// </summary>
public sealed class MoveToHandler : IMoveToHandler
{
    /// <summary>移动状态机阶段。</summary>
    private enum Phase
    {
        None,
        /// <summary>同地图导航中（PathFindController 驱动）。</summary>
        Navigating,
        /// <summary>正在走向当前地图的出口 tile。</summary>
        WalkingToExit,
        /// <summary>已触发 warpFarmer，等待地图切换完成。</summary>
        Warping,
        /// <summary>地图已切换，等待 Context.CanPlayerMove 恢复。</summary>
        WaitingForMapLoad,
        /// <summary>在目标地图内走向最终坐标。</summary>
        WalkingToTarget,
        /// <summary>同地图导航失败，等待若干帧后重试。</summary>
        RetryWaiting,
        /// <summary>已到达最终目标。</summary>
        Complete
    }

    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;
    private readonly int _defaultTimeoutFrames;

    private V2Command? _cmd;
    private string _targetLocation = "";
    private int _targetX;
    private int _targetY;
    private int _timeoutFrames;
    private int _elapsedFrames;

    // ── 状态机 ──
    private Phase _phase = Phase.None;
    private List<MapExit> _route = new();
    private int _routeIndex;
    private string _expectedMapAfterWarp = "";
    private int _waitFrames;
    private bool _callerSpecifiedTile; // 调用方是否明确指定了 x,y 坐标
    private const int MaxWarpWaitFrames = 300; // 5 秒等待地图切换
    private const int MaxMapLoadWaitFrames = 120; // 2 秒等待地图加载

    // ── 同地图导航重试 ──
    private int _sameMapRetryCount;      // 当前已重试次数，上限 2
    private int _retryWaitFrames;        // 重试倒计时（帧），递减到 0 时触发重试
    private int _pendingRetryX;          // 待重试的目标 X 坐标
    private int _pendingRetryY;          // 待重试的目标 Y 坐标
    private const int MaxSameMapRetries = 2;   // 最多重试次数
    private const int RetryWaitFrameCount = 30; // 每次重试前等待帧数

    // ── TargetRef 追踪模式 ──
    private string? _trackingTargetRef; // 非 null 时启用追踪模式
    private Vector2 _lastTrackedPosition; // 上次寻路目标位置
    private const float TrackingRethresholdTiles = 2f; // 实体移动超过此距离重新计算路径

    /// <summary>是否有正在执行的移动（供 CommandProcessor 判断）。</summary>
    public bool IsActive => _phase != Phase.None;

    public MoveToHandler(ActionExecutor executor, IMonitor monitor, int defaultTimeoutFrames = 1800)
    {
        _executor = executor;
        _monitor = monitor;
        _defaultTimeoutFrames = defaultTimeoutFrames;
    }

    /// <summary>Adapter entrypoint used by AdapterV2.</summary>
    public V2Result? Handle(V2Command cmd) => Start(cmd);

    /// <summary>
    /// Start the move action.
    /// Fast validation failures return a V2Result immediately.
    /// Navigation returns null so CommandProcessor can wait for completion via Tick().
    /// </summary>
    public V2Result? Start(V2Command cmd)
    {
        Reset();
        _cmd = cmd;
        _timeoutFrames = TryGetInt(cmd.Params, "timeout_frames", out var timeoutFrames)
            ? timeoutFrames
            : _defaultTimeoutFrames;
        if (_timeoutFrames <= 0)
            _timeoutFrames = _defaultTimeoutFrames;

        _arriveDistance = TryGetInt(cmd.Params, "arrive_distance", out var arriveVal) ? arriveVal : 2;
        if (_arriveDistance < 1) _arriveDistance = 2;

        if (!cmd.IsValid())
        {
            var failure = ResultBuilder.Failure(
                cmd.Seq,
                cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "move_to 命令缺少必要字段");
            Reset();
            return failure;
        }

        // ── TargetRef 追踪模式：target 参数包含 ":" 时启用 ──
        if (TryGetString(cmd.Params, "target", out var targetParam) &&
            TargetRefResolver.IsTargetRef(targetParam))
        {
            return StartTargetRefTracking(cmd, targetParam);
        }

        // 读取目标参数
        bool hasLocation = TryReadLocation(cmd.Params, out var location);
        bool hasTile = TryReadTargetTile(cmd.Params, out var x, out var y);

        string currentMap = Game1.currentLocation?.NameOrUniqueName ?? "";

        // ── Case 1: 有 location 参数 ──
        if (hasLocation)
        {
            if (!MapData.IsKnownMap(location))
            {
                var failure = ResultBuilder.Failure(
                    cmd.Seq,
                    cmd.ActionId,
                    ResultBuilder.Errors.TargetNotFound,
                    $"未知地图: {location}");
                Reset();
                return failure;
            }

            if (string.Equals(location, currentMap, StringComparison.OrdinalIgnoreCase))
            {
                // 同地图：如果有 x/y 就寻路，否则已在目标地图
                if (hasTile)
                    return StartSameMapNavigation(x, y);

                return BuildImmediateSuccess("已在目标地图");
            }

            // 跨地图：查找路径
            _targetLocation = location;
            _callerSpecifiedTile = hasTile;
            if (hasTile)
            {
                _targetX = x;
                _targetY = y;
            }
            else
            {
                var safeTarget = MapData.GetSafeWarpTarget(location);
                if (safeTarget == null)
                {
                    var failure = ResultBuilder.Failure(
                        cmd.Seq,
                        cmd.ActionId,
                        ResultBuilder.Errors.TargetNotFound,
                        $"地图 {location} 没有安全坐标");
                    Reset();
                    return failure;
                }
                _targetX = safeTarget.Value.x;
                _targetY = safeTarget.Value.y;
            }

            return StartCrossMapNavigation(currentMap, location);
        }

        // ── Case 2: 只有 x/y，同地图寻路 ──
        if (hasTile)
            return StartSameMapNavigation(x, y);

        // ── Case 3: 参数不足 ──
        var paramFailure = ResultBuilder.Failure(
            cmd.Seq,
            cmd.ActionId,
            ResultBuilder.Errors.InvalidParams,
            "move_to 需要 location 或 x/y (或 target_tile.{x,y}) 参数");
        Reset();
        return paramFailure;
    }

    /// <summary>
    /// 每帧驱动，由 CommandProcessor 在 pending 状态下调用。
    /// 返回 V2Result 表示完成/失败，null 表示仍在进行中。
    /// </summary>
    public V2Result? Tick()
    {
        if (_cmd == null || _phase == Phase.None)
            return null;

        _elapsedFrames++;
        if (_elapsedFrames > _timeoutFrames)
        {
            _monitor.Log(
                $"[V2] move_to timeout after {_elapsedFrames} frames, phase={_phase}",
                LogLevel.Warn);
            CleanupMovement();
            var timeoutResult = ResultBuilder.Failure(
                _cmd.Seq,
                _cmd.ActionId,
                ResultBuilder.Errors.NavigationTimeout,
                $"move_to 执行超时（>{_timeoutFrames} 帧），阶段={_phase}");
            Reset();
            return timeoutResult;
        }

        switch (_phase)
        {
            case Phase.Navigating:
                return TickNavigating();

            case Phase.RetryWaiting:
                return TickRetryWaiting();

            case Phase.WalkingToExit:
                return TickWalkingToExit();

            case Phase.Warping:
                return TickWarping();

            case Phase.WaitingForMapLoad:
                return TickWaitingForMapLoad();

            case Phase.WalkingToTarget:
                return TickWalkingToTarget();

            case Phase.Complete:
                return BuildCompletionResult();

            default:
                return null;
        }
    }

    /// <summary>Cancel the in-flight move action, if any.</summary>
    public void Cancel()
    {
        CleanupMovement();
        Reset();
    }

    // ════════════════════════════════════════════════════════
    //  TargetRef 追踪模式
    // ════════════════════════════════════════════════════════

    /// <summary>启动 TargetRef 追踪：解析实体位置并开始导航，每帧追踪实体移动。</summary>
    private V2Result? StartTargetRefTracking(V2Command cmd, string targetRef)
    {
        var resolved = TargetRefResolver.Resolve(targetRef, _monitor);
        if (resolved == null)
        {
            var failure = ResultBuilder.Failure(
                cmd.Seq,
                cmd.ActionId,
                ResultBuilder.Errors.ItemNotFound,
                $"TargetRef 解析失败，找不到实体: {targetRef}");
            Reset();
            return failure;
        }

        _trackingTargetRef = targetRef;
        _trackingFrameCounter = 0;
        _trackingArriveDistance = TryGetInt(cmd.Params, "arrive_distance", out var ad) ? ad : 2;

        var arriveDistance = _trackingArriveDistance;
        var entityPos = resolved.CurrentPosition;
        var entityLocation = resolved.Location;

        _monitor.Log(
            $"[V2] move_to: TargetRef 追踪模式 → {targetRef} 当前在 {entityLocation.Name} ({(int)entityPos.X},{(int)entityPos.Y})",
            LogLevel.Info);

        string currentMap = Game1.currentLocation?.NameOrUniqueName ?? "";
        string entityMap = entityLocation.NameOrUniqueName;

        // 如果实体在不同地图，先跨地图导航
        if (!string.Equals(currentMap, entityMap, StringComparison.OrdinalIgnoreCase))
        {
            _targetLocation = entityMap;
            _targetX = (int)entityPos.X;
            _targetY = (int)entityPos.Y;
            _lastTrackedPosition = entityPos;
            return StartCrossMapNavigation(currentMap, entityMap);
        }

        // 同地图：导航到实体旁边的可走 tile（而非实体精确位置）
        _lastTrackedPosition = entityPos;
        _targetLocation = currentMap;
        _targetX = (int)entityPos.X;
        _targetY = (int)entityPos.Y;

        var adjacentTile = CompoundUtils.FindAdjacentWalkable(
            Game1.currentLocation!, new Point((int)entityPos.X, (int)entityPos.Y));
        if (adjacentTile.HasValue)
        {
            return StartSameMapNavigation(adjacentTile.Value.X, adjacentTile.Value.Y);
        }
        // 没有可走的相邻 tile，尝试直接导航（fallback）
        return StartSameMapNavigation((int)entityPos.X, (int)entityPos.Y);
    }

    /// <summary>追踪 Resolve 的帧节流计数器。每 10 帧 Resolve 一次（6fps 足够追踪移动实体）。</summary>
    private int _trackingFrameCounter;
    private const int TrackingResolveInterval = 10;
    private int _trackingArriveDistance = 2; // 缓存 arrive_distance，避免每帧解析
    private int _arriveDistance = 2; // 非追踪模式的到达判定距离

    /// <summary>追踪模式下的到达检测和路径重算。在每帧 Tick 的 Navigating/WalkingToTarget 阶段调用。</summary>
    private V2Result? CheckTrackingUpdate()
    {
        if (_trackingTargetRef == null)
            return null; // 非追踪模式，不处理

        // 帧节流：每 TrackingResolveInterval 帧 Resolve 一次
        _trackingFrameCounter++;
        if (_trackingFrameCounter % TrackingResolveInterval != 0)
        {
            // 非 Resolve 帧：仅在接近目标时强制 Resolve 确认
            var quickDist = Vector2.Distance(Game1.player.Tile, _lastTrackedPosition);
            if (quickDist > _trackingArriveDistance)
                return null; // 远离目标，跳过本帧
            // 接近目标，fall through 到下方 Resolve 做精确确认
        }

        var resolved = TargetRefResolver.Resolve(_trackingTargetRef, _monitor);
        if (resolved == null)
        {
            // 实体消失
            _monitor.Log($"[V2] move_to: 追踪目标消失 {_trackingTargetRef}", LogLevel.Warn);
            CleanupMovement();
            var failure = ResultBuilder.Failure(
                _cmd!.Seq,
                _cmd.ActionId,
                ResultBuilder.Errors.ItemNotFound,
                $"追踪目标已消失: {_trackingTargetRef}");
            Reset();
            return failure;
        }

        var currentPos = resolved.CurrentPosition;
        var player = Game1.player;

        // 到达判定：玩家与实体当前位置距离 ≤ arrive_distance
        var distToEntity = Vector2.Distance(player.Tile, currentPos);
        if (distToEntity <= _trackingArriveDistance)
        {
            _monitor.Log(
                $"[V2] move_to: 追踪到达目标 {_trackingTargetRef}，距离 {distToEntity:F1}",
                LogLevel.Debug);
            CleanupMovement();
            _targetX = (int)currentPos.X;
            _targetY = (int)currentPos.Y;
            _phase = Phase.Complete;
            return BuildCompletionResult();
        }

        // 如果实体移动超过阈值，重新计算路径
        var drift = Vector2.Distance(_lastTrackedPosition, currentPos);
        if (drift > TrackingRethresholdTiles)
        {
            _monitor.Log(
                $"[V2] move_to: 追踪目标移动了 {drift:F1} tiles，重新计算路径 → ({(int)currentPos.X},{(int)currentPos.Y})",
                LogLevel.Debug);
            _lastTrackedPosition = currentPos;
            _targetX = (int)currentPos.X;
            _targetY = (int)currentPos.Y;

            // 重新导航到实体旁边的可走 tile
            _executor.CancelCurrent();
            var adjTile = CompoundUtils.FindAdjacentWalkable(
                Game1.currentLocation, new Point((int)currentPos.X, (int)currentPos.Y));
            var navTarget = adjTile ?? new Point((int)currentPos.X, (int)currentPos.Y);
            if (_executor.NavigateTo(navTarget))
            {
                _phase = Phase.Navigating;
                _elapsedFrames = 0; // 重置超时计数，避免长时间追踪触发无意义超时
            }
            // 导航失败不报错，继续尝试（实体可能在不可达位置但会移动回来）
        }

        return null; // 继续追踪
    }

    // ════════════════════════════════════════════════════════
    //  同地图寻路
    // ════════════════════════════════════════════════════════

    private V2Result? StartSameMapNavigation(int x, int y)
    {
        if (!_executor.NavigateTo(new Point(x, y)))
        {
            // NavigateTo 失败：目标 tile 当前不可达（可能被动物/NPC 占用）
            if (_sameMapRetryCount < MaxSameMapRetries)
            {
                _sameMapRetryCount++;
                _retryWaitFrames = RetryWaitFrameCount;
                _pendingRetryX = x;
                _pendingRetryY = y;
                _phase = Phase.RetryWaiting;
                _monitor.Log(
                    $"[V2] move_to: 无法导航到 ({x},{y})，等待 {RetryWaitFrameCount} 帧后重试（第 {_sameMapRetryCount}/{MaxSameMapRetries} 次）",
                    LogLevel.Debug);
                return null; // 异步，等 Tick
            }

            // 已达重试上限，返回失败
            var failure = ResultBuilder.Failure(
                _cmd!.Seq,
                _cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"无法导航到 ({x},{y})，目标可能不可达（已重试 {MaxSameMapRetries} 次）");
            Reset();
            return failure;
        }

        _phase = Phase.Navigating;
        _targetLocation = Game1.currentLocation?.NameOrUniqueName ?? "";
        _targetX = x;
        _targetY = y;
        _elapsedFrames = 0;

        _monitor.Log($"[V2] move_to: 同地图寻路 → ({x},{y})", LogLevel.Debug);
        return null; // 异步，等 Tick
    }

    // ════════════════════════════════════════════════════════
    //  跨地图导航
    // ════════════════════════════════════════════════════════

    private V2Result? StartCrossMapNavigation(string currentMap, string targetMap)
    {
        _route = MapData.FindRoute(currentMap, targetMap);
        if (_route.Count == 0)
        {
            // 诊断：输出当前地图是否在MapData中，以及Game1.locations包含哪些地图
            bool currentKnown = MapData.IsKnownMap(currentMap);
            bool targetKnown = MapData.IsKnownMap(targetMap);
            var allGameLocations = string.Join(", ", Game1.locations.Select(l => l?.Name ?? "null").Take(10));

            _monitor.Log(
                $"[V2] NO_PATH 诊断: currentMap={currentMap} (known={currentKnown}), " +
                $"targetMap={targetMap} (known={targetKnown}), " +
                $"Game1.locations前10个: {allGameLocations}",
                LogLevel.Warn);

            // 尝试重新扫描MapData（可能是动态地图在SaveLoaded后才加载）
            if (!currentKnown || !targetKnown)
            {
                _monitor.Log("[V2] 尝试重新扫描MapData...", LogLevel.Info);
                var newExitCount = MapData.BuildFromGameData(_monitor);
                _monitor.Log($"[V2] 重新扫描完成，共 {newExitCount} 个出口", LogLevel.Info);

                // 重试寻路
                _route = MapData.FindRoute(currentMap, targetMap);
                if (_route.Count > 0)
                {
                    _monitor.Log($"[V2] 重新扫描后找到路径，共 {_route.Count} 段", LogLevel.Info);
                    _routeIndex = 0;
                    _elapsedFrames = 0;
                    return StartWalkToCurrentExit();
                }
            }

            _monitor.Log(
                $"[V2] move_to: no route from {currentMap} to {targetMap}; " +
                "teleport fallback is disabled",
                LogLevel.Warn);
            var failure = ResultBuilder.Failure(
                _cmd!.Seq,
                _cmd.ActionId,
                ResultBuilder.Errors.PathBlocked,
                $"No walkable route from {currentMap} to {targetMap}; teleport fallback is disabled");
            Reset();
            return failure;
        }

        _routeIndex = 0;
        _elapsedFrames = 0;

        _monitor.Log(
            $"[V2] move_to: 跨地图导航 {currentMap} → {targetMap}，共 {_route.Count} 段",
            LogLevel.Debug);

        return StartWalkToCurrentExit();
    }

    /// <summary>开始走向当前路段的出口坐标。</summary>
    private V2Result? StartWalkToCurrentExit()
    {
        var exit = _route[_routeIndex];
        var exitPoint = new Point(exit.SourceX, exit.SourceY);

        _monitor.Log(
            $"[V2] move_to: 走向出口 ({exit.SourceX},{exit.SourceY}) → {exit.TargetMap}",
            LogLevel.Debug);

        if (!_executor.NavigateTo(exitPoint))
        {
            var failure = ResultBuilder.Failure(
                _cmd!.Seq,
                _cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"无法走到出口 ({exit.SourceX},{exit.SourceY})");
            Reset();
            return failure;
        }

        _phase = Phase.WalkingToExit;
        _expectedMapAfterWarp = exit.TargetMap;
        _waitFrames = 0;
        return null;
    }

    // ════════════════════════════════════════════════════════
    //  Tick 各阶段（职责单一，每个阶段只做一件事）
    // ════════════════════════════════════════════════════════

    /// <summary>同地图导航失败后的等待重试：倒计时结束后尝试 FindAdjacentWalkable 再重试。</summary>
    private V2Result? TickRetryWaiting()
    {
        _retryWaitFrames--;
        if (_retryWaitFrames > 0)
            return null; // 继续等待

        // 倒计时结束，优先尝试目标 tile 的相邻可走格子
        var adjTile = CompoundUtils.FindAdjacentWalkable(
            Game1.currentLocation!, new Point(_pendingRetryX, _pendingRetryY));
        int retryX = adjTile.HasValue ? adjTile.Value.X : _pendingRetryX;
        int retryY = adjTile.HasValue ? adjTile.Value.Y : _pendingRetryY;

        _monitor.Log(
            $"[V2] move_to: 重试导航 → ({retryX},{retryY})" +
            (adjTile.HasValue && (retryX != _pendingRetryX || retryY != _pendingRetryY)
                ? $"（原目标 ({_pendingRetryX},{_pendingRetryY}) 的相邻可走 tile）"
                : ""),
            LogLevel.Debug);

        return StartSameMapNavigation(retryX, retryY);
    }

    /// <summary>同地图导航中：等待 PathFindController 完成。</summary>
    private V2Result? TickNavigating()
    {
        // 追踪模式：检查实体移动和到达判定
        var trackingResult = CheckTrackingUpdate();
        if (trackingResult != null)
            return trackingResult;

        if (_executor.IsBusy)
            return null;

        // 追踪模式下 PathFindController 完成但未到达 → 精确 Resolve 确认
        // 注意：这里需要一次新的 Resolve（实体可能在 PathFind 期间移动），不同于 CheckTrackingUpdate 中的节流 Resolve
        if (_trackingTargetRef != null)
        {
            var resolved = TargetRefResolver.Resolve(_trackingTargetRef, _monitor);
            if (resolved != null)
            {
                var dist = Vector2.Distance(Game1.player.Tile, resolved.CurrentPosition);
                if (dist > _trackingArriveDistance)
                {
                    // 尚未到达，重新导航到实体旁边的可走 tile
                    _lastTrackedPosition = resolved.CurrentPosition;
                    _targetX = (int)resolved.CurrentPosition.X;
                    _targetY = (int)resolved.CurrentPosition.Y;
                    var adj = CompoundUtils.FindAdjacentWalkable(
                        Game1.currentLocation, new Point(_targetX, _targetY));
                    var target = adj ?? new Point(_targetX, _targetY);
                    if (_executor.NavigateTo(target))
                    {
                        return null; // 继续导航
                    }
                }
            }
        }

        // 导航完成
        _phase = Phase.Complete;
        return BuildCompletionResult();
    }

    /// <summary>走向出口中：等待 PathFindController 到达出口 tile。</summary>
    private V2Result? TickWalkingToExit()
    {
        string currentMap = Game1.currentLocation?.Name ?? "";
        string currentMapUnique = Game1.currentLocation?.NameOrUniqueName ?? "";

        // 1. 到达预期地图（自然边界 warp 成功）
        if (currentMap == _expectedMapAfterWarp || currentMapUnique == _expectedMapAfterWarp)
        {
            _monitor.Log($"[V2] move_to: 自然 warp 到 {currentMap}，等待地图加载", LogLevel.Debug);
            CleanupMovement();
            _phase = Phase.WaitingForMapLoad;
            _waitFrames = 0;
            return null;
        }

        // 2. ActionExecutor 仍在导航 → 继续等待
        if (_executor.IsBusy)
            return null;

        // 3. 导航完成，已到达出口 tile 附近 → 手动触发 warp
        _monitor.Log($"[V2] move_to: 到达出口附近，执行 warp → {_expectedMapAfterWarp}", LogLevel.Debug);
        return ExecuteWarp();
    }

    /// <summary>执行 warp：查找最近 warp 点或 fallback 到安全坐标。</summary>
    private V2Result? ExecuteWarp()
    {
        // 先清理移动状态，避免 warp 后角色继续移动
        CleanupMovement();

        var player = Game1.player;
        var location = Game1.currentLocation;

        // 查找最近的匹配 warp 点
        if (location?.warps != null)
        {
            Warp? bestWarp = null;
            float bestDist = float.MaxValue;

            foreach (var warp in location.warps)
            {
                if (warp.TargetName != _expectedMapAfterWarp)
                    continue;

                float dx = player.Tile.X - warp.X;
                float dy = player.Tile.Y - warp.Y;
                float dist = dx * dx + dy * dy;
                if (dist < bestDist)
                {
                    bestDist = dist;
                    bestWarp = warp;
                }
            }

            if (bestWarp != null && bestDist <= 25f) // 5 tile 半径内
            {
                _monitor.Log(
                    $"[V2] move_to: 使用 warp ({bestWarp.X},{bestWarp.Y}) → {bestWarp.TargetName} ({bestWarp.TargetX},{bestWarp.TargetY})",
                    LogLevel.Debug);
                Game1.warpFarmer(bestWarp.TargetName, bestWarp.TargetX, bestWarp.TargetY, false);
                _phase = Phase.Warping;
                _waitFrames = 0;
                return null;
            }
        }

        // Reaching an approximate exit is not enough evidence to teleport.
        _monitor.Log(
            $"[V2] move_to: no nearby matching warp for {_expectedMapAfterWarp}; " +
            "teleport fallback is disabled",
            LogLevel.Warn);
        var failure = ResultBuilder.Failure(
            _cmd!.Seq,
            _cmd.ActionId,
            ResultBuilder.Errors.PathBlocked,
            $"No nearby walk-through warp to {_expectedMapAfterWarp}; teleport fallback is disabled");
        Reset();
        return failure;
    }

    /// <summary>等待 warp 完成（地图切换）。</summary>
    private V2Result? TickWarping()
    {
        string currentMap = Game1.currentLocation?.Name ?? "";
        string currentMapUnique = Game1.currentLocation?.NameOrUniqueName ?? "";
        if (currentMap == _expectedMapAfterWarp || currentMapUnique == _expectedMapAfterWarp)
        {
            _monitor.Log($"[V2] move_to: warp 完成，已到达 {currentMap}，等待地图加载", LogLevel.Debug);
            _phase = Phase.WaitingForMapLoad;
            _waitFrames = 0;
            return null;
        }

        _waitFrames++;
        if (_waitFrames > MaxWarpWaitFrames)
        {
            CleanupMovement();
            var failure = ResultBuilder.Failure(
                _cmd!.Seq,
                _cmd.ActionId,
                ResultBuilder.Errors.WarpTimeout,
                $"等待地图切换超时。当前={currentMap}，期望={_expectedMapAfterWarp}");
            Reset();
            return failure;
        }

        return null;
    }

    /// <summary>等待地图加载完成（Context.CanPlayerMove 恢复）。</summary>
    private V2Result? TickWaitingForMapLoad()
    {
        _waitFrames++;

        if (Context.CanPlayerMove)
        {
            _monitor.Log($"[V2] move_to: 地图加载完成，CanPlayerMove=true", LogLevel.Debug);
            return OnMapSwitched();
        }

        // 不强制继续——等待全局超时兜底。
        // 强制 OnMapSwitched() 在 CanPlayerMove=false 时会导致 NavigateTo 失败。
        if (_waitFrames % 60 == 0)
        {
            _monitor.Log(
                $"[V2] move_to: 仍在等待地图加载 ({_waitFrames} 帧)，CanPlayerMove=false",
                LogLevel.Debug);
        }

        return null;
    }

    /// <summary>在目标地图内走向最终坐标。</summary>
    private V2Result? TickWalkingToTarget()
    {
        // 追踪模式：检查实体移动和到达判定
        var trackingResult = CheckTrackingUpdate();
        if (trackingResult != null)
            return trackingResult;

        if (_executor.IsBusy)
            return null;

        // 导航完成
        _phase = Phase.Complete;
        return BuildCompletionResult();
    }

    /// <summary>地图切换后的处理：决定继续下一段还是走向最终目标。</summary>
    private V2Result? OnMapSwitched()
    {
        _routeIndex++;

        // 还有更多中间段
        if (_routeIndex < _route.Count)
        {
            _monitor.Log(
                $"[V2] move_to: 还有 {_route.Count - _routeIndex} 段路径",
                LogLevel.Debug);
            return StartWalkToCurrentExit();
        }

        // 已到达目标地图，走向最终坐标（优先找相邻可走 tile）
        var finalTarget = new Point(_targetX, _targetY);
        var adjFinal = CompoundUtils.FindAdjacentWalkable(Game1.currentLocation, finalTarget);
        var navFinal = adjFinal ?? finalTarget;
        _monitor.Log(
            $"[V2] move_to: 已到达目标地图，寻路到 ({navFinal.X},{navFinal.Y})" +
            (adjFinal.HasValue && adjFinal != finalTarget ? $"（原目标 ({_targetX},{_targetY}) 的相邻可走 tile）" : ""),
            LogLevel.Debug);

        if (!_executor.NavigateTo(navFinal))
        {
            // 导航失败但已到达目标地图，返回部分成功
            _monitor.Log(
                $"[V2] move_to: 无法寻路到 ({_targetX},{_targetY})，但已到达目标地图",
                LogLevel.Warn);
            _phase = Phase.Complete;
            return BuildCompletionResult();
        }

        _phase = Phase.WalkingToTarget;
        return null;
    }

    // ════════════════════════════════════════════════════════
    //  清理与结果构建
    // ════════════════════════════════════════════════════════

    /// <summary>清理移动相关状态（停止 PathFindController + 清除残留方向）。</summary>
    private void CleanupMovement()
    {
        _executor.CancelCurrent();
        // 确保玩家完全停下，无残留移动方向
        var player = Game1.player;
        player.controller = null;
        player.movementDirections.Clear();
        player.Halt();
    }

    private V2Result BuildCompletionResult()
    {
        var player = Game1.player;
        var location = Game1.currentLocation;

        // 确定 mode
        string mode;
        if (_route.Count > 0)
            mode = "cross_map";
        else
            mode = "navigate";

        var data = new Dictionary<string, object>
        {
            ["mode"] = mode,
            ["target"] = new Dictionary<string, object>
            {
                ["location"] = _targetLocation,
                ["x"] = _targetX,
                ["y"] = _targetY
            },
            ["final_position"] = new Dictionary<string, object>
            {
                ["location"] = location?.Name ?? "Unknown",
                ["x"] = (int)player.Tile.X,
                ["y"] = (int)player.Tile.Y
            }
        };

        if (_route.Count > 0)
        {
            data["route_hops"] = _route.Count;
        }

        // ── 到达判定：检查最终位置是否在 arrive_distance 内 ──
        float dx = player.Tile.X - _targetX;
        float dy = player.Tile.Y - _targetY;
        float distToTarget = (float)Math.Sqrt(dx * dx + dy * dy);
        int effectiveArriveDistance = _trackingTargetRef != null ? _trackingArriveDistance : _arriveDistance;

        // 跨地图导航但调用方未指定 x,y 时，到达目标地图即算成功。
        bool skipDistanceCheck = _route.Count > 0 && !_callerSpecifiedTile;

        if (!skipDistanceCheck && distToTarget > effectiveArriveDistance)
        {
            // 玩家未到达目标位置，返回失败
            _monitor.Log(
                $"[V2] move_to: 未到达目标 ({_targetX},{_targetY})，" +
                $"最终位置 ({(int)player.Tile.X},{(int)player.Tile.Y})，" +
                $"距离 {distToTarget:F1} > arrive_distance {effectiveArriveDistance}",
                LogLevel.Warn);

            var failure = ResultBuilder.Failure(
                _cmd!.Seq,
                _cmd.ActionId,
                ResultBuilder.Errors.TargetUnreachable,
                $"寻路完成但未到达目标: 最终位置 ({(int)player.Tile.X},{(int)player.Tile.Y}) 距目标 ({_targetX},{_targetY}) {distToTarget:F1} tiles，超过 arrive_distance={effectiveArriveDistance}");
            failure.Data = data; // 附带位置数据便于调试
            Reset();
            return failure;
        }

        // 构建 observation
        string observation;
        if (_route.Count > 0)
        {
            observation = $"已到达 {location?.Name ?? "Unknown"} ({(int)player.Tile.X},{(int)player.Tile.Y})";
        }
        else
        {
            observation = $"已到达坐标 ({(int)player.Tile.X},{(int)player.Tile.Y})";
        }

        var result = ResultBuilder.Success(_cmd!.Seq, _cmd.ActionId, data)
            .WithObservation(observation);

        Reset();
        return result;
    }

    private V2Result BuildImmediateSuccess(string observation)
    {
        var player = Game1.player;
        var location = Game1.currentLocation;
        var data = new Dictionary<string, object>
        {
            ["mode"] = "already_there",
            ["final_position"] = new Dictionary<string, object>
            {
                ["location"] = location?.Name ?? "Unknown",
                ["x"] = (int)player.Tile.X,
                ["y"] = (int)player.Tile.Y
            }
        };

        var result = ResultBuilder.Success(_cmd!.Seq, _cmd.ActionId, data)
            .WithObservation(observation);

        Reset();
        return result;
    }

    // ════════════════════════════════════════════════════════
    //  参数解析工具
    // ════════════════════════════════════════════════════════

    private static bool TryReadLocation(Dictionary<string, object> args, out string location)
    {
        location = "";
        if (TryGetString(args, "location", out location) && !string.IsNullOrWhiteSpace(location))
            return true;

        if (TryGetString(args, "location_name", out location) && !string.IsNullOrWhiteSpace(location))
            return true;

        if (TryGetString(args, "target_location", out location) && !string.IsNullOrWhiteSpace(location))
            return true;

        return false;
    }

    private static bool TryReadTargetTile(Dictionary<string, object> args, out int x, out int y)
    {
        x = -1;
        y = -1;

        if (TryGetInt(args, "x", out x) && TryGetInt(args, "y", out y))
            return x >= 0 && y >= 0;

        if (TryGetInt(args, "tile_x", out x) && TryGetInt(args, "tile_y", out y))
            return x >= 0 && y >= 0;

        if (args.TryGetValue("target_tile", out var tileObj) && tileObj is Dictionary<string, object> tileDict)
        {
            var hasX = TryGetInt(tileDict, "x", out x);
            var hasY = TryGetInt(tileDict, "y", out y);
            return hasX && hasY && x >= 0 && y >= 0;
        }

        return false;
    }

    private static bool TryGetString(Dictionary<string, object> args, string key, out string value)
    {
        value = "";
        if (!args.TryGetValue(key, out var obj) || obj == null)
            return false;

        switch (obj)
        {
            case string s:
                value = s;
                return true;
            case JsonElement element when element.ValueKind == JsonValueKind.String:
                value = element.GetString() ?? "";
                return true;
            default:
                value = obj.ToString() ?? "";
                return !string.IsNullOrWhiteSpace(value);
        }
    }

    private static bool TryGetInt(Dictionary<string, object> args, string key, out int value)
    {
        value = -1;
        if (!args.TryGetValue(key, out var obj) || obj == null)
            return false;

        switch (obj)
        {
            case int i:
                value = i;
                return true;
            case long l:
                value = (int)l;
                return true;
            case double d:
                value = (int)d;
                return true;
            case float f:
                value = (int)f;
                return true;
            case decimal m:
                value = (int)m;
                return true;
            case string s when int.TryParse(s, out var parsed):
                value = parsed;
                return true;
            case JsonElement element:
                if (element.ValueKind == JsonValueKind.Number && element.TryGetInt32(out var parsedInt))
                {
                    value = parsedInt;
                    return true;
                }

                if (element.ValueKind == JsonValueKind.String && int.TryParse(element.GetString(), out parsedInt))
                {
                    value = parsedInt;
                    return true;
                }
                break;
        }

        return false;
    }

    private void Reset()
    {
        _cmd = null;
        _phase = Phase.None;
        _targetLocation = "";
        _targetX = 0;
        _targetY = 0;
        _timeoutFrames = _defaultTimeoutFrames;
        _elapsedFrames = 0;
        _route = new List<MapExit>();
        _routeIndex = 0;
        _expectedMapAfterWarp = "";
        _waitFrames = 0;
        _callerSpecifiedTile = false;
        _trackingTargetRef = null;
        _lastTrackedPosition = Vector2.Zero;
        _trackingFrameCounter = 0;
        _trackingArriveDistance = 2;
        _arriveDistance = 2;
        _sameMapRetryCount = 0;
        _retryWaitFrames = 0;
        _pendingRetryX = 0;
        _pendingRetryY = 0;
    }
}
