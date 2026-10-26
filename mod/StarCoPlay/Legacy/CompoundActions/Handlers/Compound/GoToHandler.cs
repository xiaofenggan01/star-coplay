using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.GameServices.Movement;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// L2 Compound: go_to — 语义化跨地图导航。
///
/// 5 态状态机（与 mainline §4 一致，对应 Sprint Contract S1-S13 验收契约）：
///   Resolving → Routing → Segment → (循环) → Tracking? → Done
///       │           │          │                  │
///       └──────────┴──────────┴──────────────────┴──→ Failed
///
/// 各阶段职责：
///   Resolving — 调 TargetNameResolver.Resolve 解析 target；
///               处理 K1 多实例建筑歧义（→ Failed AmbiguousBuilding + Candidates）。
///   Routing   — 调 MapData.EnsureKnown + GoToRoutePlanner.Plan；
///               空段列表 → Failed NoRoute。
///   Segment   — 按段类型派发子 move_to 命令（V2Command）：
///               IntraMap 段：模式 A（Coord，可选 face_direction 仅末段）
///               EdgeWarp 段：模式 B（提供 direction）
///               DoorInteract 段：模式 C（adjacent_side） + interact 命令触发门
///               末段（NPC/动物，无 face_direction）：模式 C（adjacent_side=auto）
///               末段（带 face_direction）：模式 A + face_direction（站终点本身）
///   Tracking  — 仅 IsDynamic=true 时进入；每 10 帧重 Resolve 目标；
///               距离 > 阈值 → 重发末段 move_to（C 模式）；
///               重 Resolve 上限 8（K6：跨地图 → 直接 TrackingLost，不重 Plan）。
///   Done      — 构造 GoToOutput.Success → ToV2Result。
///
/// 子命令派发机制：
///   主代理已确认"通过 V2Command 派发，禁止直接调 SMAPI"。
///   本 Handler 通过构造 V2Command + 调用 IMoveToHandler.Start(cmd) / Tick()
///   完成对当前 move_to 执行原子的派发，等价于走 CommandProcessor 路径。
///   注：CommandProcessor 全局只允许一条 pending V2 命令，故本 Handler 不能
///   把子命令塞回 CommandProcessor — 而是直接持有 IMoveToHandler 引用驱动其
///   Tick()。这同 GoBed/ExecuteOnTargets 等 Compound 直接持有 ActionExecutor 一致。
///   interact 子命令同理：构造命令后交给当前执行原子处理。
///
/// K 决策落实：
///   K1 — Resolving 阶段 ResolveType.AmbiguousTarget → Failed + Candidates（见 TickResolving）
///   K4 — GoToInput 单参 target + 可选 face_direction（见 ParseInput）
///   K5 — DoorInteract 段失败 → 直接 PathBlocked Failed，无 final-step retry（见 TickSegment）
///   K6 — Tracking 期间 entity 跨地图 → 直接 TrackingLost，不重 Plan（见 TickTracking）
///
/// 参考：
///   handoff/2026-05-03-goto-v2-mainline-design.md §3-§5
///   handoff/2026-05-03-K-decisions-overlay.md
///   handoff/2026-05-03-goto-v2-sprint-contract.md
/// </summary>
public class GoToHandler : ICompoundHandler, ICompoundDebugState
{
    // ── 注入依赖 ──────────────────────────────────────────────
    private readonly IMoveToHandler _moveToHandler;
    // ActionExecutor 仅用于 DoorInteract 段触发 InteractWithTile（等价 L1 atom：Bridge/CommandProcessor 内部
    // 同样通过 ActionExecutor 派发 InteractHandler）。本 Handler 不直接调 SMAPI。
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    // ── 状态机 ────────────────────────────────────────────────
    private GoToPhase _phase = GoToPhase.Resolving;
    private bool _isActive;

    // ── 命令上下文 ────────────────────────────────────────────
    private V2Command? _cmd;
    private int _seq;
    private string _actionId = "";
    private GoToInput? _input;
    private readonly Stopwatch _stopwatch = new();

    // ── Resolving / Routing 阶段产物 ─────────────────────────
    private ResolvedTarget? _resolved;
    private List<PathSegment>? _segments;
    private int _segIdx;

    // ── Segment 阶段子状态 ────────────────────────────────────
    private bool _moveToInflight;
    private bool _interactInflight;
    private int _interactWaitTicks;
    private const int InteractWaitTimeoutFrames = 180; // ~3 秒

    // ── Warp 后等待地图加载（WaitingMapLoad 子阶段）─────────
    private bool _waitingMapLoad;
    private string? _expectedTargetMap;
    private int _mapLoadStableTicks;     // canMove=true 且在目标地图 + 玩家 tile 不变的连续帧数
    private int _mapLoadTotalWaitTicks;  // 总等待帧数
    private int _lastPlayerTileX = int.MinValue;  // 上一帧玩家 tile（位置稳定判据）
    private int _lastPlayerTileY = int.MinValue;
    private const int MapLoadRaceGuardFrames = 30;  // 防抖：30 帧（0.5 秒）位置不变才推进，覆盖 BusStop 等入场动画
    private const int MaxMapLoadWaitFrames = 600;   // 超时：10 秒

    // ── Tracking 阶段计数 ────────────────────────────────────
    private int _trackingFrameCounter;
    private int _reResolveCount;
    private const int ReResolveIntervalFrames = 10;
    private const int ReResolveDistanceThreshold = 2;
    private const int MaxReResolveCount = 8;

    // ── 元数据 ────────────────────────────────────────────────
    private readonly List<string> _phaseHistory = new();
    private GoToPhase _lastTrackedPhase = GoToPhase.Resolving;

    // ── 输出预存（失败时供 Cancel 使用）─────────────────────
    private string? _terminalErrorCode;
    private string? _terminalMessage;
    private List<BuildingCandidate>? _terminalCandidates;
    private string? _terminalLastKnownLocation;
    private int? _terminalLastKnownX;
    private int? _terminalLastKnownY;

    /// <inheritdoc />
    public bool IsActive => _isActive;

    public string GetDebugState()
    {
        var segment = _segments != null
            ? $"{_segIdx + 1}/{_segments.Count}"
            : "none";
        var resolved = _resolved != null
            ? $"{_resolved.Kind}@{_resolved.LocationName}:{_resolved.X},{_resolved.Y}"
            : "none";
        return $"phase={_phase},segment={segment},moveInflight={_moveToInflight},interactInflight={_interactInflight},waitMap={_waitingMapLoad},mapWait={_mapLoadTotalWaitTicks},interactWait={_interactWaitTicks},trackingReresolve={_reResolveCount},resolved={resolved}";
    }

    public GoToHandler(
        IMoveToHandler moveToHandler,
        ActionExecutor executor,
        IMonitor monitor)
    {
        _moveToHandler = moveToHandler;
        _executor = executor;
        _monitor = monitor;
    }

    // ════════════════════════════════════════════════════════
    //  ICompoundHandler 入口
    // ════════════════════════════════════════════════════════

    /// <inheritdoc />
    public V2Result? Start(V2Command cmd)
    {
        Reset();
        _cmd = cmd;
        _seq = cmd.Seq;
        _actionId = cmd.ActionId;
        _stopwatch.Restart();
        _isActive = true;

        // 种子 phase_history（初始 Resolving）
        _phase = GoToPhase.Resolving;
        _lastTrackedPhase = GoToPhase.Resolving;
        _phaseHistory.Add(GoToPhase.Resolving.ToString());

        // ── K4: 解析输入参数（单参 target + 可选 face_direction）──
        if (!ParseInput(cmd, out var input, out var parseError))
        {
            return BuildFailure(GoToErrors.InvalidParams, parseError, GoToPhase.Resolving);
        }
        _input = input;
        _monitor.Log($"[GoTo] Start target='{input.Target}' face_direction={input.FaceDirection ?? "(none)"}", LogLevel.Info);

        // 立即在同一帧驱动一次状态机：Resolving / Routing 多为同步操作，
        // 短路场景（player / 同地图等）可能直接给出最终结果。
        return Tick();
    }

    /// <inheritdoc />
    public V2Result? Tick()
    {
        if (!_isActive) return null;

        try
        {
            return _phase switch
            {
                GoToPhase.Resolving => TickResolving(),
                GoToPhase.Routing   => TickRouting(),
                GoToPhase.Segment   => TickSegment(),
                GoToPhase.Tracking  => TickTracking(),
                GoToPhase.Done      => BuildSuccessResult(),
                GoToPhase.Failed    => BuildFailureResult(),
                _ => null
            };
        }
        catch (Exception ex)
        {
            _monitor.Log($"[GoTo] Tick exception in phase {_phase}: {ex.Message}\n{ex.StackTrace}", LogLevel.Error);
            return BuildFailure(GoToErrors.NavigationTimeout,
                $"go_to internal exception in phase {_phase}: {ex.Message}",
                _phase);
        }
    }

    /// <inheritdoc />
    public V2Result Cancel()
    {
        _monitor.Log($"[GoTo] Cancel in phase={_phase}, segIdx={_segIdx}", LogLevel.Info);

        // 取消正在进行的子 move_to
        if (_moveToHandler.IsActive)
            _moveToHandler.Cancel();

        var cancelOutput = new GoToOutput(
            Success: false,
            ErrorCode: ResultBuilder.Errors.Cancelled,
            Message: $"go_to cancelled at phase={_phase}",
            Resolved: _resolved,
            Segments: _segments,
            FinalPhase: _phase,
            ElapsedMs: (int)_stopwatch.ElapsedMilliseconds,
            Candidates: null,
            LastKnownLocation: null,
            LastKnownX: null,
            LastKnownY: null,
            Metadata: BuildMetadata());

        var result = cancelOutput.ToV2Result();
        result.Seq = _seq;
        result.ActionId = _actionId;

        Reset();
        return result;
    }

    // ════════════════════════════════════════════════════════
    //  阶段 1: Resolving — 解析 target 字符串
    // ════════════════════════════════════════════════════════

    private V2Result? TickResolving()
    {
        var input = _input!;
        var target = input.Target;

        // ── 子情形 A：MapName:x,y 坐标语法（Sprint Contract S4/S5）──
        if (TryParseMapCoord(target, out var mapName, out var coordX, out var coordY))
        {
            _monitor.Log($"[GoTo] Resolving: 坐标语法 {mapName}:({coordX},{coordY})", LogLevel.Debug);
            // MapData 自愈：未知地图触发一次重扫
            if (!StarCoPlay.MapData.IsKnownMap(mapName))
                StarCoPlay.MapData.EnsureKnown(mapName, _monitor);

            if (!StarCoPlay.MapData.IsKnownMap(mapName))
            {
                return BuildFailure(GoToErrors.MapNotFound,
                    $"map '{mapName}' not found in MapData (after rescan)",
                    GoToPhase.Resolving);
            }

            _resolved = new ResolvedTarget(
                Kind: TargetKind.MapCoord,
                LocationName: mapName,
                X: coordX, Y: coordY,
                IsDynamic: false,
                GameObject: null,
                FaceDirection: input.FaceDirection);

            TransitionTo(GoToPhase.Routing);
            return TickRouting();
        }

        // ── 子情形 B：TargetRef 语法（含":entityKind_"）──
        if (target.Contains(":") && (target.Contains(":animal_") || target.Contains(":npc_")
            || target.Contains(":crop_") || target.Contains(":tree_") || target.Contains(":rock_")
            || target.Contains(":tile_") || target.Contains(":water_") || target.Contains(":object_")
            || target.Contains(":obj_") || target.Contains(":debris_") || target.Contains(":furniture_")
            || target.Contains(":chest_") || target.Contains(":fridge_") || target.Contains(":shipping_bin_")
            || target.Contains(":mini_")))
        {
            var refResolved = TargetRefResolver.Resolve(target, _monitor);
            if (refResolved == null)
            {
                return BuildFailure(GoToErrors.TargetNotFound,
                    $"TargetRef '{target}' could not be resolved",
                    GoToPhase.Resolving);
            }

            var refKind = refResolved.IsDynamic
                ? (refResolved.Kind == "animal" ? TargetKind.Animal : TargetKind.Npc)
                : TargetKind.Tile;

            _resolved = new ResolvedTarget(
                Kind: refKind,
                LocationName: refResolved.Location.NameOrUniqueName,
                X: (int)refResolved.CurrentPosition.X,
                Y: (int)refResolved.CurrentPosition.Y,
                IsDynamic: refResolved.IsDynamic,
                GameObject: refResolved.Entity,
                FaceDirection: input.FaceDirection);

            TransitionTo(GoToPhase.Routing);
            return TickRouting();
        }

        // ── 子情形 C：语义名称（player / 地图名 / NPC 名 / 动物名 / 建筑短名 / GUID）──
        var nameResult = TargetNameResolver.Resolve(target, _monitor);

        // K1：多实例建筑歧义 → 直接 Failed + Candidates
        if (nameResult.Type == TargetNameResolver.ResolveType.AmbiguousTarget)
        {
            var candidates = (nameResult.Candidates ?? new List<TargetNameResolver.BuildingCandidate>())
                .Select(c => new BuildingCandidate(
                    LocationName: c.LocationName,
                    BuildingType: c.BuildingType,
                    Distance: c.Distance,
                    X: c.X,
                    Y: c.Y))
                .ToList();

            _terminalCandidates = candidates;
            return BuildFailure(GoToErrors.AmbiguousBuilding,
                nameResult.ErrorMessage ?? $"ambiguous target '{target}'",
                GoToPhase.Resolving);
        }

        // 解析失败
        if (nameResult.Type == TargetNameResolver.ResolveType.Unknown)
        {
            return BuildFailure(GoToErrors.TargetNotFound,
                nameResult.ErrorMessage ?? $"target '{target}' not found",
                GoToPhase.Resolving);
        }

        // ── TrackingEntity（NPC / animal）：经 TargetRefResolver 解析得到坐标 ──
        if (nameResult.Type == TargetNameResolver.ResolveType.TrackingEntity)
        {
            var refResolved = TargetRefResolver.Resolve(nameResult.TargetRef!, _monitor);
            if (refResolved == null)
            {
                return BuildFailure(GoToErrors.TargetNotFound,
                    $"TargetNameResolver returned ref '{nameResult.TargetRef}' but TargetRefResolver failed",
                    GoToPhase.Resolving);
            }

            var entityKind = refResolved.Kind == "animal" ? TargetKind.Animal : TargetKind.Npc;
            _resolved = new ResolvedTarget(
                Kind: entityKind,
                LocationName: refResolved.Location.NameOrUniqueName,
                X: (int)refResolved.CurrentPosition.X,
                Y: (int)refResolved.CurrentPosition.Y,
                IsDynamic: true,
                GameObject: refResolved.Entity,
                FaceDirection: input.FaceDirection);

            // 短路 S1：player 在自己脚下，直接 Done
            // （TargetNameResolver R1 走 StaticLocation 路径，不会走到这里；保险起见检测）
        }
        else // StaticLocation（地图名 / DisplayName / 建筑 GUID / building 单实例 / player）
        {
            // R1 player 短路
            if (nameResult.ResolvedBy == "player")
            {
                _resolved = new ResolvedTarget(
                    Kind: TargetKind.Player,
                    LocationName: nameResult.LocationName!,
                    X: nameResult.X,
                    Y: nameResult.Y,
                    IsDynamic: false,
                    GameObject: null,
                    FaceDirection: input.FaceDirection);

                _segments = new List<PathSegment>(); // 0 段
                TransitionTo(GoToPhase.Done);
                return BuildSuccessResult();
            }

            var staticKind = nameResult.ResolvedBy switch
            {
                "building" or "building_guid" => TargetKind.Building,
                "map_name" or "map_display"   => TargetKind.MapCoord,
                _ => TargetKind.Tile
            };

            _resolved = new ResolvedTarget(
                Kind: staticKind,
                LocationName: nameResult.LocationName!,
                X: nameResult.X,
                Y: nameResult.Y,
                IsDynamic: false,
                GameObject: null,
                FaceDirection: input.FaceDirection);
        }

        TransitionTo(GoToPhase.Routing);
        return TickRouting();
    }

    // ════════════════════════════════════════════════════════
    //  阶段 2: Routing — BFS 跨地图路由
    // ════════════════════════════════════════════════════════

    private V2Result? TickRouting()
    {
        var resolved = _resolved!;
        var player = Game1.player;
        var fromMap = Game1.currentLocation?.NameOrUniqueName ?? "";
        var fromX = (int)player.Tile.X;
        var fromY = (int)player.Tile.Y;
        var toMap = resolved.LocationName;

        // MapData 自愈
        if (!StarCoPlay.MapData.IsKnownMap(toMap))
            StarCoPlay.MapData.EnsureKnown(toMap, _monitor);

        if (!StarCoPlay.MapData.IsKnownMap(toMap))
        {
            return BuildFailure(GoToErrors.MapNotFound,
                $"target map '{toMap}' not in MapData after rescan",
                GoToPhase.Routing);
        }

        // 已在目标地图、坐标重合 → 直接 Done
        if (string.Equals(fromMap, toMap, StringComparison.OrdinalIgnoreCase)
            && Math.Abs(fromX - resolved.X) <= 1
            && Math.Abs(fromY - resolved.Y) <= 1
            && !resolved.IsDynamic
            && string.IsNullOrEmpty(resolved.FaceDirection))
        {
            _segments = new List<PathSegment>();
            TransitionTo(GoToPhase.Done);
            return BuildSuccessResult();
        }

        // 调用 W4.2 路由规划器
        var planned = GoToRoutePlanner.Plan(
            fromMap, fromX, fromY,
            toMap, resolved.X, resolved.Y,
            _monitor);

        if (planned == null || planned.Count == 0)
        {
            // 同地图寻路返回单段 IntraMap，不会走到这；空列表 = NO_ROUTE
            return BuildFailure(GoToErrors.NoRoute,
                $"no route from '{fromMap}' to '{toMap}'",
                GoToPhase.Routing);
        }

        // 不可达过滤：动态 entity 但坐标无效（极少见）→ TARGET_UNREACHABLE
        if (resolved.X < 0 || resolved.Y < 0)
        {
            return BuildFailure(GoToErrors.TargetUnreachable,
                $"resolved target has invalid coordinate ({resolved.X},{resolved.Y})",
                GoToPhase.Routing);
        }

        _segments = planned;
        _segIdx = 0;
        _monitor.Log($"[GoTo] Routing 完成: {fromMap}→{toMap}, segments={planned.Count}", LogLevel.Info);

        TransitionTo(GoToPhase.Segment);
        return null; // 让出帧，下一帧 TickSegment 启动第一段
    }

    // ════════════════════════════════════════════════════════
    //  阶段 3: Segment — 逐段派发 move_to / interact
    // ════════════════════════════════════════════════════════

    private V2Result? TickSegment()
    {
        if (_segments == null) return BuildFailure(GoToErrors.NoRoute, "segments null", GoToPhase.Segment);

        // ── 自然 warp 跳段检测（W4.7）──
        // SDV 中走到地图边界的 walk_to tile 会自动 warp，不需要按方向键。
        // 若玩家已离开当前段 SourceMap，找路由链中对应的目标段并跳过，避免 NavigateTo timeout。
        // 注意：_waitingMapLoad 期间不检查 W4.7 — 此时玩家还在原地图等 warp 触发是正常状态，
        // 误检会导致 TrackingLost（如 Mode B sync 成功立即推进段索引时，玩家此刻还在原地图）。
        if (!_waitingMapLoad)
        {
            var naturalWarpResult = CheckNaturalWarpSkip();
            if (naturalWarpResult != null) return naturalWarpResult;
        }

        // ── 等待地图加载稳定（EdgeWarp/DoorInteract 段完成后的中间态）──
        // 三重稳定判据：① 玩家在目标地图 ② canMove=true ③ 玩家 tile 位置 N 帧不变
        // 位置不变判据用于覆盖入场动画（如 BusStop：玩家进图后 SDV 自动 transit 到等候点，
        // 期间 canMove 仍可能为 true，但实际位置在变化，PFC 派下一段会被动画卡住）
        if (_waitingMapLoad)
        {
            _mapLoadTotalWaitTicks++;
            var curMap = Game1.currentLocation?.NameOrUniqueName ?? "";
            bool atTarget = string.Equals(curMap, _expectedTargetMap, StringComparison.OrdinalIgnoreCase);
            bool canMove = Game1.player != null
                && !Game1.player.UsingTool
                && Game1.player.canMove
                && !Game1.player.FarmerSprite.PauseForSingleAnimation;
            int curTileX = Game1.player != null ? (int)Game1.player.Tile.X : int.MinValue;
            int curTileY = Game1.player != null ? (int)Game1.player.Tile.Y : int.MinValue;
            bool tileUnchanged = curTileX == _lastPlayerTileX && curTileY == _lastPlayerTileY;
            _lastPlayerTileX = curTileX;
            _lastPlayerTileY = curTileY;

            if (atTarget && canMove && tileUnchanged)
            {
                _mapLoadStableTicks++;
                if (_mapLoadStableTicks >= MapLoadRaceGuardFrames)
                {
                    _monitor.Log($"[GoTo] 地图加载稳定（{_mapLoadStableTicks}帧 + 位置静止 ({curTileX},{curTileY})），进入段[{_segIdx}]", LogLevel.Trace);
                    _waitingMapLoad = false;
                    _expectedTargetMap = null;
                    _mapLoadStableTicks = 0;
                    _mapLoadTotalWaitTicks = 0;
                    _lastPlayerTileX = int.MinValue;
                    _lastPlayerTileY = int.MinValue;
                    // 继续，下方逻辑将派发下一段
                }
                else
                {
                    return null; // 尚未稳定，继续等
                }
            }
            else
            {
                _mapLoadStableTicks = 0; // 任一条件不满足时重置防抖计数
                if (_mapLoadTotalWaitTicks >= MaxMapLoadWaitFrames)
                {
                    return BuildFailure(GoToErrors.WarpTimeout,
                        $"等待地图加载超时：期望 '{_expectedTargetMap}'，当前 '{curMap}'，canMove={canMove}, tileUnchanged={tileUnchanged}",
                        GoToPhase.Segment);
                }
                return null; // 继续等待
            }
        }

        // ── 等待当前 interact 子命令完成（DoorInteract 段第二步）──
        if (_interactInflight)
        {
            _interactWaitTicks++;
            // interact 执行原子是异步的，但 Compound 不能直接 Tick 它。
            // 简化：发出 InteractWithTile（通过 ActionExecutor）后等若干帧 + 检测 location 变化。
            var curMap = Game1.currentLocation?.NameOrUniqueName ?? "";
            var seg = _segments[_segIdx];
            if (!string.Equals(curMap, seg.SourceMap, StringComparison.OrdinalIgnoreCase))
            {
                // 已切到新地图 → DoorInteract 成功，进入等待地图加载子阶段
                _monitor.Log($"[GoTo] DoorInteract 成功: {seg.SourceMap}→{curMap}，等待 canMove 稳定", LogLevel.Info);
                _interactInflight = false;
                _interactWaitTicks = 0;
                _segIdx++;
                _waitingMapLoad = true;
                _expectedTargetMap = seg.TargetMap;
                _mapLoadStableTicks = 0;
                _mapLoadTotalWaitTicks = 0;
                return null;
            }

            if (_interactWaitTicks >= InteractWaitTimeoutFrames)
            {
                // K5：DoorInteract 失败直接上抛 PathBlocked，不做 retry
                return BuildFailure(GoToErrors.PathBlocked,
                    $"DoorInteract segment timeout: tried interact at ({seg.OriginalWarpX},{seg.OriginalWarpY}) but location did not change after {InteractWaitTimeoutFrames} frames",
                    GoToPhase.Segment);
            }
            return null;
        }

        // ── 等待当前 move_to 子命令完成 ──
        if (_moveToInflight)
        {
            if (_moveToHandler.IsActive)
            {
                var subResult = _moveToHandler.Tick();
                if (subResult == null) return null; // 还在跑

                _moveToInflight = false;
                if (!subResult.Success)
                {
                    // 子命令失败 → 透传错误码（带映射）
                    var subCode = subResult.Error?.Code ?? "UNKNOWN";
                    var subMsg = subResult.Error?.Message ?? "(no message)";
                    var mappedCode = MapSubErrorToGoToError(subCode);
                    var seg = _segments[_segIdx];
                    return BuildFailure(mappedCode,
                        $"segment[{_segIdx}] {seg.Type} move_to failed: [{subCode}] {subMsg}",
                        GoToPhase.Segment);
                }

                // move_to 成功
                var currentSeg = _segments[_segIdx];
                if (currentSeg.Type == SegmentType.DoorInteract)
                {
                    // DoorInteract: move_to 完成后还需发 interact 触发门
                    return DispatchDoorInteract(currentSeg);
                }

                // EdgeWarp: warp 已触发，进入等待地图加载子阶段
                if (currentSeg.Type == SegmentType.EdgeWarp)
                {
                    _monitor.Log($"[GoTo] Segment[{_segIdx}] EdgeWarp 完成，等待地图加载/canMove（目标: {currentSeg.TargetMap}）", LogLevel.Trace);
                    _segIdx++;
                    _waitingMapLoad = true;
                    _expectedTargetMap = currentSeg.TargetMap;
                    _mapLoadStableTicks = 0;
                    _mapLoadTotalWaitTicks = 0;
                    return null;
                }

                // 推进到下一段
                _segIdx++;
                return null;
            }
            // _moveToHandler 不再 active 但 _moveToInflight 还为 true（极少）→ 重置
            _moveToInflight = false;
        }

        // ── 检查段是否走完 ──
        if (_segIdx >= _segments.Count)
        {
            // 末段执行完毕
            if (_resolved!.IsDynamic)
            {
                // 动态目标 → 进入 Tracking
                _trackingFrameCounter = 0;
                _reResolveCount = 0;
                TransitionTo(GoToPhase.Tracking);
                return null;
            }

            TransitionTo(GoToPhase.Done);
            return BuildSuccessResult();
        }

        // ── 派发当前段 ──
        var s = _segments[_segIdx];
        var isLastSeg = _segIdx == _segments.Count - 1;
        return DispatchSegment(s, isLastSeg);
    }

    /// <summary>
    /// 按段类型派发子 move_to 命令。
    /// </summary>
    private V2Result? DispatchSegment(PathSegment seg, bool isLastSeg)
    {
        var subParams = new Dictionary<string, object>();
        var subCmd = new V2Command
        {
            Seq = _seq,
            ProtocolVersion = "2.0",
            Capability = AdapterV2.CapabilityMoveTo,
            ActionId = $"{_actionId}-seg{_segIdx}",
            Params = subParams
        };

        switch (seg.Type)
        {
            case SegmentType.IntraMap:
                {
                    // 末段且终点是 NPC/动物 + 无 face_direction → 模式 C（adjacent_side=auto）
                    // 末段 + 有 face_direction → 模式 A + face_direction（站终点本身）
                    // 中间 IntraMap（走到 warp 起点）→ 模式 A 纯坐标
                    subParams["x"] = seg.TargetX;
                    subParams["y"] = seg.TargetY;

                    if (isLastSeg
                        && _resolved != null
                        && (_resolved.Kind == TargetKind.Npc || _resolved.Kind == TargetKind.Animal)
                        && string.IsNullOrEmpty(_resolved.FaceDirection))
                    {
                        // 模式 C
                        subParams["adjacent_side"] = ComputeAdjacentSide(seg.TargetX, seg.TargetY);
                        _monitor.Log($"[GoTo] Segment[{_segIdx}] IntraMap LAST entity → Mode C adjacent ({seg.TargetX},{seg.TargetY})", LogLevel.Debug);
                    }
                    else if (isLastSeg && !string.IsNullOrEmpty(_resolved?.FaceDirection))
                    {
                        // 模式 A + face_direction
                        subParams["face_direction"] = _resolved.FaceDirection!;
                        _monitor.Log($"[GoTo] Segment[{_segIdx}] IntraMap LAST face → Mode A+face ({seg.TargetX},{seg.TargetY}, face={_resolved.FaceDirection})", LogLevel.Debug);
                    }
                    else
                    {
                        // 纯坐标模式 A
                        _monitor.Log($"[GoTo] Segment[{_segIdx}] IntraMap → Mode A ({seg.TargetX},{seg.TargetY})", LogLevel.Debug);
                    }
                    break;
                }

            case SegmentType.EdgeWarp:
                {
                    // 模式 B：走到调用方指定 tile + Hold direction
                    if (string.IsNullOrEmpty(seg.Direction))
                    {
                        return BuildFailure(GoToErrors.InvalidParams,
                            $"segment[{_segIdx}] EdgeWarp missing direction",
                            GoToPhase.Segment);
                    }
                    subParams["x"] = seg.SourceX;
                    subParams["y"] = seg.SourceY;
                    subParams["direction"] = seg.Direction;
                    _monitor.Log($"[GoTo] Segment[{_segIdx}] EdgeWarp → Mode B ({seg.SourceX},{seg.SourceY}, dir={seg.Direction})", LogLevel.Debug);
                    break;
                }

            case SegmentType.DoorInteract:
                {
                    // 模式 C：走到门前的相邻 tile + 朝门面朝。
                    // SDV 门约定朝南开（玩家站门下 Y+1 朝北 interact 触发 warp）。
                    // 强制 adjacent_side="south"（玩家寻路 doorY+1 朝北面对门 tile），
                    // 比 ComputeAdjacentSide 基于玩家当前位置启发式更稳定（避免选到不可走的门 sprite 上半部分）。
                    var doorX = seg.OriginalWarpX ?? seg.TargetX;
                    var doorY = seg.OriginalWarpY ?? seg.TargetY;
                    subParams["x"] = doorX;
                    subParams["y"] = doorY;
                    subParams["adjacent_side"] = "south";
                    _monitor.Log($"[GoTo] Segment[{_segIdx}] DoorInteract → Mode C door=({doorX},{doorY}), adjacent=south", LogLevel.Debug);
                    break;
                }
        }

        // 派发子 move_to
        var subResult = _moveToHandler.Start(subCmd);
        if (subResult != null)
        {
            // 同步失败
            if (!subResult.Success)
            {
                var subCode = subResult.Error?.Code ?? "UNKNOWN";
                var subMsg = subResult.Error?.Message ?? "(no message)";
                var mappedCode = MapSubErrorToGoToError(subCode);
                return BuildFailure(mappedCode,
                    $"segment[{_segIdx}] {seg.Type} sync failure: [{subCode}] {subMsg}",
                    GoToPhase.Segment);
            }

            // 同步成功（极少见，move_to 几乎总是异步）
            if (seg.Type == SegmentType.DoorInteract)
                return DispatchDoorInteract(seg);

            // EdgeWarp 同步成功：进入等待地图加载子阶段
            if (seg.Type == SegmentType.EdgeWarp)
            {
                _monitor.Log($"[GoTo] Segment[{_segIdx}] EdgeWarp 同步完成，等待地图加载（目标: {seg.TargetMap}）", LogLevel.Trace);
                _segIdx++;
                _waitingMapLoad = true;
                _expectedTargetMap = seg.TargetMap;
                _mapLoadStableTicks = 0;
                _mapLoadTotalWaitTicks = 0;
                return null;
            }

            _segIdx++;
            return null;
        }

        // 异步进行中
        _moveToInflight = true;
        return null;
    }

    /// <summary>
    /// DoorInteract 段：move_to 完成后，发 interact 触发门 warp。
    /// 通过 ActionExecutor.InteractWithTile（与当前 interact 原子同等机制）。
    /// </summary>
    private V2Result? DispatchDoorInteract(PathSegment seg)
    {
        var doorX = seg.OriginalWarpX ?? seg.TargetX;
        var doorY = seg.OriginalWarpY ?? seg.TargetY;

        _monitor.Log($"[GoTo] DoorInteract: move_to 完成 → InteractWithTile({doorX},{doorY})", LogLevel.Info);

        bool ok = _executor.InteractWithTile(doorX, doorY);
        if (!ok)
        {
            // K5：直接上抛
            return BuildFailure(GoToErrors.PathBlocked,
                $"segment[{_segIdx}] DoorInteract: InteractWithTile({doorX},{doorY}) returned false",
                GoToPhase.Segment);
        }

        _interactInflight = true;
        _interactWaitTicks = 0;
        return null;
    }

    /// <summary>
    /// 计算 adjacent_side：从玩家当前 tile 看，目标在哪一侧。
    /// 玩家应站在目标的"对侧" — adjacent_side 描述玩家最终站位相对目标的方向。
    /// </summary>
    private static string ComputeAdjacentSide(int targetX, int targetY)
    {
        var player = Game1.player;
        if (player == null) return "south";

        int dx = targetX - (int)player.Tile.X;
        int dy = targetY - (int)player.Tile.Y;
        // 玩家 tile vs target tile：取较大轴决定从哪侧靠近
        if (Math.Abs(dx) > Math.Abs(dy))
            return dx >= 0 ? "west" : "east"; // 目标在右 → 玩家站左侧 west
        return dy >= 0 ? "north" : "south";   // 目标在下 → 玩家站上侧 north
    }

    // ════════════════════════════════════════════════════════
    //  阶段 4: Tracking — 动态目标周期重 Resolve
    // ════════════════════════════════════════════════════════

    private V2Result? TickTracking()
    {
        var resolved = _resolved!;
        if (!resolved.IsDynamic)
        {
            // 应不会到这；保险起见走 Done
            TransitionTo(GoToPhase.Done);
            return BuildSuccessResult();
        }

        // 等正在执行的子 move_to 完成（重发末段后）
        if (_moveToInflight)
        {
            if (_moveToHandler.IsActive)
            {
                var innerSubResult = _moveToHandler.Tick();
                if (innerSubResult == null) return null;

                _moveToInflight = false;
                if (!innerSubResult.Success)
                {
                    var subCode = innerSubResult.Error?.Code ?? "UNKNOWN";
                    var mappedCode = MapSubErrorToGoToError(subCode);
                    return BuildFailure(mappedCode,
                        $"tracking re-issued move_to failed: [{subCode}] {innerSubResult.Error?.Message ?? ""}",
                        GoToPhase.Tracking);
                }
                // 成功 → 继续 tracking 循环
            }
            else
            {
                _moveToInflight = false;
            }
        }

        _trackingFrameCounter++;
        if (_trackingFrameCounter < ReResolveIntervalFrames)
            return null;

        _trackingFrameCounter = 0;

        // 重 Resolve 实体当前位置
        var entity = resolved.GameObject;
        Vector2 currentTile;
        string currentLoc;

        if (entity is NPC npc)
        {
            if (npc.currentLocation == null)
            {
                return BuildTrackingLost("NPC currentLocation null");
            }
            currentTile = npc.Tile;
            currentLoc = npc.currentLocation.NameOrUniqueName;
        }
        else if (entity is FarmAnimal animal)
        {
            if (animal.currentLocation == null)
            {
                return BuildTrackingLost("FarmAnimal currentLocation null");
            }
            currentTile = animal.Tile;
            currentLoc = animal.currentLocation.NameOrUniqueName;
        }
        else
        {
            // 不应发生；保守处理
            TransitionTo(GoToPhase.Done);
            return BuildSuccessResult();
        }

        // K6：跨地图直接 TrackingLost
        if (!string.Equals(currentLoc, resolved.LocationName, StringComparison.OrdinalIgnoreCase))
        {
            return BuildTrackingLost($"target moved to '{currentLoc}', original was '{resolved.LocationName}' (K6: no re-Plan)");
        }

        // 距离玩家
        var player = Game1.player;
        int dx = (int)currentTile.X - (int)player.Tile.X;
        int dy = (int)currentTile.Y - (int)player.Tile.Y;
        int chebyshev = Math.Max(Math.Abs(dx), Math.Abs(dy));

        // chebyshev=1 → 紧贴 NPC，Done（玩家与 NPC 相邻一格即视作到达）
        if (chebyshev <= 1)
        {
            _monitor.Log($"[GoTo] Tracking Done: 玩家 ({(int)player.Tile.X},{(int)player.Tile.Y}) 紧贴 entity ({(int)currentTile.X},{(int)currentTile.Y}) chebyshev={chebyshev}",
                LogLevel.Info);
            TransitionTo(GoToPhase.Done);
            return BuildSuccessResult();
        }

        // 距离 > 1 → 重发末段 move_to（C 模式 + 新位置），让 PFC 重新规划玩家逼近 NPC
        _reResolveCount++;
        if (_reResolveCount > MaxReResolveCount)
        {
            return BuildTrackingLost($"re-resolve count exceeded {MaxReResolveCount}");
        }

        _monitor.Log($"[GoTo] Tracking re-resolve #{_reResolveCount}: target moved to ({currentTile.X},{currentTile.Y}), chebyshev={chebyshev}, re-issuing move_to", LogLevel.Debug);

        // 更新 _resolved 坐标（保持 LocationName / IsDynamic 不变）
        _resolved = _resolved with
        {
            X = (int)currentTile.X,
            Y = (int)currentTile.Y
        };

        // 取消可能残留的 move_to 后重发
        if (_moveToHandler.IsActive)
            _moveToHandler.Cancel();

        var subParams = new Dictionary<string, object>
        {
            ["x"] = (int)currentTile.X,
            ["y"] = (int)currentTile.Y,
            ["adjacent_side"] = ComputeAdjacentSide((int)currentTile.X, (int)currentTile.Y)
        };
        var subCmd = new V2Command
        {
            Seq = _seq,
            ProtocolVersion = "2.0",
            Capability = AdapterV2.CapabilityMoveTo,
            ActionId = $"{_actionId}-track{_reResolveCount}",
            Params = subParams
        };

        var subResult = _moveToHandler.Start(subCmd);
        if (subResult != null && !subResult.Success)
        {
            var subCode = subResult.Error?.Code ?? "UNKNOWN";
            var mappedCode = MapSubErrorToGoToError(subCode);
            return BuildFailure(mappedCode,
                $"tracking re-issue sync failure: [{subCode}] {subResult.Error?.Message ?? ""}",
                GoToPhase.Tracking);
        }
        _moveToInflight = subResult == null;
        return null;
    }

    private V2Result BuildTrackingLost(string reason)
    {
        _terminalLastKnownLocation = _resolved?.LocationName;
        _terminalLastKnownX = _resolved?.X;
        _terminalLastKnownY = _resolved?.Y;
        return BuildFailure(GoToErrors.TrackingLost,
            $"tracking lost: {reason} (re_resolve_count={_reResolveCount})",
            GoToPhase.Tracking);
    }

    // ════════════════════════════════════════════════════════
    //  W4.7: 自然 warp 跳段检测
    // ════════════════════════════════════════════════════════

    /// <summary>
    /// 每帧在 TickSegment 入口检测玩家是否已因自然 warp 离开当前段的 SourceMap。
    /// 若已离开且目标地图在路由链中，跳段并进入 WaitingMapLoad 子状态。
    /// 若目标地图完全不在路由链中，返回 TrackingLost。
    /// 无事发生时返回 null（继续正常逻辑）。
    /// </summary>
    private V2Result? CheckNaturalWarpSkip()
    {
        if (_segments == null || _segIdx >= _segments.Count) return null;
        if (Game1.player == null) return null;

        var actualMap = Game1.player.currentLocation?.NameOrUniqueName;
        if (string.IsNullOrEmpty(actualMap)) return null;

        var current = _segments[_segIdx];

        // 玩家仍在当前段的 SourceMap — 无需处理
        if (string.Equals(actualMap, current.SourceMap, StringComparison.OrdinalIgnoreCase))
            return null;

        // 玩家已不在 SourceMap — 查找路由链中匹配 TargetMap == actualMap 的段
        // 即：玩家通过自然 warp 到达了某段的终点，从该段之后继续执行
        for (int i = _segIdx; i < _segments.Count; i++)
        {
            if (string.Equals(_segments[i].TargetMap, actualMap, StringComparison.OrdinalIgnoreCase))
            {
                _monitor.Log(
                    $"[GoTo] W4.7 自然 warp 检测: 玩家已在 {actualMap}，跳过段[{_segIdx}..{i}]，推进到段[{i + 1}]",
                    LogLevel.Info);

                // 取消正在进行的子命令
                CancelInflightSubCommand();

                _segIdx = i + 1;

                // 复用已有 WaitingMapLoad 等待 canMove 稳定
                _waitingMapLoad = true;
                _expectedTargetMap = actualMap;
                _mapLoadStableTicks = 0;
                _mapLoadTotalWaitTicks = 0;
                return null;
            }
        }

        // 玩家在路由链以外的地图 — 被外部传送或手动移动，路由失效
        _monitor.Log(
            $"[GoTo] W4.7 玩家位置异常: 在 {actualMap}，路由链不含此地图（当前段[{_segIdx}] SourceMap={current.SourceMap}），终止",
            LogLevel.Warn);
        return BuildFailure(GoToErrors.TrackingLost,
            $"玩家被外部传送到 {actualMap}，路由链不含此地图，路由失效",
            GoToPhase.Segment);
    }

    /// <summary>
    /// 取消正在进行的子命令（move_to 或 interact），重置相关状态。
    /// 跳段时调用，避免旧子命令持续占用资源或触发 timeout。
    /// </summary>
    private void CancelInflightSubCommand()
    {
        if (_moveToInflight)
        {
            if (_moveToHandler.IsActive)
            {
                _monitor.Log("[GoTo] W4.7 取消 move_to 子命令（自然 warp 跳段）", LogLevel.Debug);
                _moveToHandler.Cancel();
            }
            _moveToInflight = false;
        }

        if (_interactInflight)
        {
            _monitor.Log("[GoTo] W4.7 取消 interact 子命令（自然 warp 跳段）", LogLevel.Debug);
            _interactInflight = false;
            _interactWaitTicks = 0;
        }
    }

    // ════════════════════════════════════════════════════════
    //  输入解析（K4）
    // ════════════════════════════════════════════════════════

    private static bool ParseInput(V2Command cmd, out GoToInput input, out string error)
    {
        input = new GoToInput("");
        error = "";

        var p = cmd.Params ?? new Dictionary<string, object>();

        if (!CompoundUtils.TryGetString(p, "target", out var target) || string.IsNullOrWhiteSpace(target))
        {
            error = "go_to requires 'target' parameter (non-empty string)";
            return false;
        }

        string? faceDir = null;
        if (CompoundUtils.TryGetString(p, "face_direction", out var fd) && !string.IsNullOrWhiteSpace(fd))
            faceDir = fd;

        input = new GoToInput(target.Trim(), faceDir);
        return true;
    }

    /// <summary>
    /// 解析 "MapName:x,y" 坐标语法（Sprint Contract S4/S5）。
    /// 不与 TargetRef 语法冲突 — TargetRef 含 entityKind_ 下划线模式。
    /// </summary>
    private static bool TryParseMapCoord(string target, out string mapName, out int x, out int y)
    {
        mapName = "";
        x = y = 0;
        var colonIdx = target.IndexOf(':');
        if (colonIdx <= 0) return false;

        var rest = target[(colonIdx + 1)..];
        // 必须形如 "x,y"（数字逗号数字）；TargetRef 是 "kind_xxx" 模式（含下划线）
        if (rest.Contains('_')) return false;

        var commaIdx = rest.IndexOf(',');
        if (commaIdx <= 0) return false;

        var sx = rest[..commaIdx].Trim();
        var sy = rest[(commaIdx + 1)..].Trim();

        if (!int.TryParse(sx, out x)) return false;
        if (!int.TryParse(sy, out y)) return false;

        mapName = target[..colonIdx];
        return true;
    }

    // ════════════════════════════════════════════════════════
    //  错误码映射 + 状态转换
    // ════════════════════════════════════════════════════════

    private static string MapSubErrorToGoToError(string subCode)
    {
        return subCode switch
        {
            "PATH_BLOCKED"        => GoToErrors.PathBlocked,
            "WARP_TIMEOUT"        => GoToErrors.WarpTimeout,
            "NAVIGATION_TIMEOUT"  => GoToErrors.NavigationTimeout,
            "INVALID_PARAMS"      => GoToErrors.InvalidParams,
            _                     => subCode // 透传未识别错误码
        };
    }

    private void TransitionTo(GoToPhase next)
    {
        if (_lastTrackedPhase != next)
        {
            _phaseHistory.Add(next.ToString());
            _lastTrackedPhase = next;
            _monitor.Log($"[GoTo] phase: {_phase} → {next}", LogLevel.Trace);
        }
        _phase = next;
    }

    private Dictionary<string, object> BuildMetadata()
    {
        var meta = new Dictionary<string, object>
        {
            ["phase_history"] = string.Join("→", _phaseHistory),
            ["elapsed_ms"] = (int)_stopwatch.ElapsedMilliseconds,
            ["segment_count"] = _segments?.Count ?? 0,
            ["re_resolve_count"] = _reResolveCount
        };
        return meta;
    }

    // ════════════════════════════════════════════════════════
    //  失败/成功 V2Result 构造
    // ════════════════════════════════════════════════════════

    private V2Result? BuildFailure(string errorCode, string message, GoToPhase finalPhase)
    {
        _terminalErrorCode = errorCode;
        _terminalMessage = message;
        _phase = GoToPhase.Failed;

        _monitor.Log($"[GoTo] FAIL [{errorCode}] {message}", LogLevel.Warn);
        return BuildFailureResult();
    }

    private V2Result BuildFailureResult()
    {
        _stopwatch.Stop();

        var output = new GoToOutput(
            Success: false,
            ErrorCode: _terminalErrorCode ?? GoToErrors.NavigationTimeout,
            Message: _terminalMessage ?? "go_to failed (no message)",
            Resolved: _resolved,
            Segments: _segments,
            FinalPhase: GoToPhase.Failed,
            ElapsedMs: (int)_stopwatch.ElapsedMilliseconds,
            Candidates: _terminalCandidates,
            LastKnownLocation: _terminalLastKnownLocation,
            LastKnownX: _terminalLastKnownX,
            LastKnownY: _terminalLastKnownY,
            Metadata: BuildMetadata());

        var result = output.ToV2Result();
        result.Seq = _seq;
        result.ActionId = _actionId;

        // K1：candidates 写入 data 字段（Sprint Contract S9 期望 result.data.candidates）
        if (_terminalErrorCode == GoToErrors.AmbiguousBuilding && _terminalCandidates != null)
        {
            var data = new Dictionary<string, object>
            {
                ["candidates"] = _terminalCandidates.Select(c => new Dictionary<string, object>
                {
                    ["location_name"] = c.LocationName,
                    ["building_type"] = c.BuildingType,
                    ["distance"] = c.Distance,
                    ["x"] = c.X,
                    ["y"] = c.Y
                }).ToList<object>()
            };
            result.Data = data;
        }
        else if (_terminalErrorCode == GoToErrors.TrackingLost)
        {
            // S11/S8 期望 last_known_location / x / y / re_resolve_count
            var data = new Dictionary<string, object>();
            if (_terminalLastKnownLocation != null) data["last_known_location"] = _terminalLastKnownLocation;
            if (_terminalLastKnownX != null) data["last_known_x"] = _terminalLastKnownX.Value;
            if (_terminalLastKnownY != null) data["last_known_y"] = _terminalLastKnownY.Value;
            data["re_resolve_count"] = _reResolveCount;
            data["reason"] = _terminalMessage ?? "tracking lost";
            result.Data = data;
        }

        Reset();
        return result;
    }

    private V2Result BuildSuccessResult()
    {
        _stopwatch.Stop();
        var resolved = _resolved!;
        var player = Game1.player;
        var curMap = Game1.currentLocation?.NameOrUniqueName ?? "";
        int finalX = (int)player.Tile.X;
        int finalY = (int)player.Tile.Y;
        int facing = player.FacingDirection;

        var data = new Dictionary<string, object>
        {
            ["final_location"] = curMap,
            ["final_x"] = finalX,
            ["final_y"] = finalY
        };

        // 当末段是 entity 或带 face_direction 时，补 final_facing（Sprint Contract 通用断言 + S6/S7/S12）
        if (resolved.Kind == TargetKind.Npc
            || resolved.Kind == TargetKind.Animal
            || !string.IsNullOrEmpty(resolved.FaceDirection))
        {
            data["final_facing"] = FacingToString(facing);
        }

        data["metadata"] = BuildMetadata();

        var v2 = ResultBuilder.Success(_seq, _actionId, data)
            .WithObservation($"go_to 完成 → {curMap} ({finalX},{finalY})");

        Reset();
        return v2;
    }

    private static string FacingToString(int dir)
    {
        return dir switch
        {
            0 => "up",
            1 => "right",
            2 => "down",
            3 => "left",
            _ => "unknown"
        };
    }

    // ════════════════════════════════════════════════════════
    //  Reset
    // ════════════════════════════════════════════════════════

    private void Reset()
    {
        _isActive = false;
        _phase = GoToPhase.Resolving;
        _lastTrackedPhase = GoToPhase.Resolving;
        _phaseHistory.Clear();

        _cmd = null;
        _seq = 0;
        _actionId = "";
        _input = null;
        _stopwatch.Reset();

        _resolved = null;
        _segments = null;
        _segIdx = 0;

        _moveToInflight = false;
        _interactInflight = false;
        _interactWaitTicks = 0;

        _trackingFrameCounter = 0;
        _reResolveCount = 0;

        _terminalErrorCode = null;
        _terminalMessage = null;
        _terminalCandidates = null;
        _terminalLastKnownLocation = null;
        _terminalLastKnownX = null;
        _terminalLastKnownY = null;
    }
}
