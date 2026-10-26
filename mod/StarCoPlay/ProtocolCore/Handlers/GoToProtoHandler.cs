using System;
using System.Collections.Generic;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.ProtocolCore.GoTo;
using StarCoPlay.ProtocolCore.Results;

namespace StarCoPlay.ProtocolCore.Handlers;

/// <summary>
/// Proto-native go_to handler (L2 composite).
///
/// Executes cross-map navigation by:
///   1. Planning a route via MapData.FindRoute (WalkThrough = EdgeWarp, InteractDoor = adjacent + interact)
///   2. Executing each step via child MoveToProtoHandler / InteractProtoHandler (direct calls, no scheduler)
///   3. Waiting for map transition stability between steps
///   4. Resolving (-1,-1) to the runtime map's safe point
///   5. Final positioning via move_to exact/adjacent
///
/// Does NOT:
///   - Use tp fallback
///   - Call Game1.warpFarmer directly
///   - Dispatch child commands via ProtoCommandScheduler
///   - Interpret semantic targets other than exact map names and unique display names
/// </summary>
public sealed class GoToProtoHandler : IProtoCommandHandler
{
    private const int DefaultTimeoutFrames   = 3600;
    private const int MapStableTimeoutFrames = 600;
    private const int MapStableRequiredFrames = 10;
    private const int MaxChildTimeoutFrames  = 1800;

    private readonly MoveToProtoHandler   _moveTo;
    private readonly InteractProtoHandler _interact;
    private readonly IMonitor             _monitor;

    public string Capability => "go_to";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.GoTo;
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveLong;
    public bool IsActive { get; private set; }

    // ── Per-command state ──
    private GoToPhase       _phase;
    private GoToRoutePlan   _plan = null!;
    private int             _stepIndex;
    private int             _elapsed;
    private int             _totalTimeout;
    private int             _mapStableElapsed;
    private int             _mapStableFrames;
    private int             _lastStableTileX;
    private int             _lastStableTileY;
    private LocationSnapshot _startSnapshot = null!;
    private GoToParams      _request = null!;
    private string          _targetMap = "";
    private int             _targetX;
    private int             _targetY;
    private MoveToResult?   _lastFinalMoveResult;
    private int             _stepsCompleted;

    // Child contexts
    private ProtoCommandContext? _childCtx;
    // Track which child is active (move_to or interact)
    private bool _childIsMoveToActive;
    private bool _childIsInteractActive;

    public GoToProtoHandler(
        MoveToProtoHandler moveTo,
        InteractProtoHandler interact,
        IMonitor monitor)
    {
        _moveTo   = moveTo;
        _interact = interact;
        _monitor  = monitor;
    }

    // ─────────────────────────────────────────
    //  Validate
    // ─────────────────────────────────────────

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        if (Game1.player == null)
            return ProtoValidationResult.Invalid("player is null");
        if (Game1.currentLocation == null)
            return ProtoValidationResult.Invalid("currentLocation is null");
        if (Game1.activeClickableMenu != null)
            return ProtoValidationResult.Invalid("a menu is open");
        if (Game1.player.UsingTool)
            return ProtoValidationResult.Invalid("player is using a tool");

        var p = ctx.Request.GoTo;
        if (string.IsNullOrEmpty(p.MapId))
            return ProtoValidationResult.Invalid("map_id is required");
        if (p.ArrivalMode == ArrivalMode.Unspecified)
            return ProtoValidationResult.Invalid("arrival_mode must not be UNSPECIFIED");

        string currentMap = Game1.currentLocation.NameOrUniqueName;
        if (!MapData.EnsureKnown(currentMap, _monitor))
            return ProtoValidationResult.Invalid($"current map '{currentMap}' not known to MapData");
        string? targetMap = MapData.ResolveMapName(p.MapId);
        if (targetMap == null)
        {
            MapData.EnsureKnown(p.MapId, _monitor);
            targetMap = MapData.ResolveMapName(p.MapId);
        }
        if (targetMap == null)
            return ProtoValidationResult.Invalid(
                $"target map '{p.MapId}' is unknown or has an ambiguous display name");

        bool safePointRequested = p.X == -1 && p.Y == -1;
        if ((p.X == -1) != (p.Y == -1))
            return ProtoValidationResult.Invalid("safe-point coordinates must be exactly (-1,-1)");
        if (safePointRequested && p.ArrivalMode != ArrivalMode.ArrivalExact)
            return ProtoValidationResult.Invalid("safe-point coordinates require ARRIVAL_EXACT");
        if (safePointRequested && MapData.GetSafeWarpTarget(targetMap) == null)
            return ProtoValidationResult.Invalid($"target map '{targetMap}' has no safe point");

        return ProtoValidationResult.Ok();
    }

    // ─────────────────────────────────────────
    //  Start
    // ─────────────────────────────────────────

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        ResetState();

        _request       = ctx.Request.GoTo;
        _targetMap     = MapData.ResolveMapName(_request.MapId)!;
        _targetX       = _request.X;
        _targetY       = _request.Y;
        if (_targetX == -1 && _targetY == -1)
        {
            var safePoint = MapData.GetSafeWarpTarget(_targetMap)!.Value;
            _targetX = safePoint.x;
            _targetY = safePoint.y;
        }
        _startSnapshot = ProtoSnapshotBuilder.CurrentLocationSnapshot();
        _totalTimeout  = _request.TimeoutFrames > 0 ? _request.TimeoutFrames : DefaultTimeoutFrames;
        ProtoSnapshotBuilder.CaptureStartState(ctx);

        string currentMap = Game1.currentLocation!.NameOrUniqueName;

        _plan = GoToRoutePlanner.Plan(
            fromMap:       currentMap,
            targetMap:     _targetMap,
            targetX:       _targetX,
            targetY:       _targetY,
            arrivalMode:   _request.ArrivalMode,
            standSide:     _request.StandSide,
            faceDirection: _request.FaceDirection,
            monitor:       _monitor);

        if (_plan == null)
            return HandlerStartResult.Rejected($"no route from '{currentMap}' to '{_targetMap}'");

        _monitor.Log(
            $"[GoToProto] Route planned: {string.Join(" → ", _plan.RouteMaps)}, {_plan.Steps.Count} step(s)",
            LogLevel.Info);

        _phase    = GoToPhase.StartingStep;
        _stepIndex = 0;
        IsActive  = true;
        return HandlerStartResult.Async();
    }

    // ─────────────────────────────────────────
    //  Tick
    // ─────────────────────────────────────────

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        _elapsed++;

        if (_elapsed >= _totalTimeout)
        {
            CancelActiveChild("total timeout");
            return Fail(ErrorCode.Timeout,
                $"go_to total timeout ({_elapsed} frames) at step {_stepIndex}/{_plan.Steps.Count}");
        }

        return _phase switch
        {
            GoToPhase.StartingStep     => TickStartingStep(ctx),
            GoToPhase.WaitingMoveTo    => TickWaitingMoveTo(ctx),
            GoToPhase.WaitingInteract  => TickWaitingInteract(ctx),
            GoToPhase.WaitingMapStable => TickWaitingMapStable(ctx),
            _                          => HandlerTickResult.Continue
        };
    }

    // ─────────────────────────────────────────
    //  StartingStep
    // ─────────────────────────────────────────

    private HandlerTickResult TickStartingStep(ProtoCommandContext ctx)
    {
        if (_stepIndex >= _plan.Steps.Count)
            return Succeed(ctx);

        var step = _plan.Steps[_stepIndex];
        int remaining    = _totalTimeout - _elapsed;
        int childTimeout = Math.Min(remaining, MaxChildTimeoutFrames);

        _monitor.Log(
            $"[GoToProto] Starting step[{_stepIndex}] {step.Kind} {step.SourceMap}→{step.TargetMap} ({step.X},{step.Y})",
            LogLevel.Debug);

        if (step.Kind == GoToStepKind.DoorInteract)
            return StartDoorInteractMoveTo(ctx, step, childTimeout);

        return StartMoveTo(ctx, step, childTimeout);
    }

    private HandlerTickResult StartMoveTo(ProtoCommandContext ctx, GoToStep step, int childTimeout)
    {
        var moveToParams = BuildMoveToParams(step, childTimeout);
        _childCtx = GoToChildCommandFactory.CreateMoveToContext(ctx, _stepIndex, moveToParams, Game1.ticks);

        var validation = _moveTo.Validate(_childCtx);
        if (!validation.IsValid)
        {
            string msg = GoToErrorMapper.BuildStepFailMessage(
                _stepIndex, step, "move_to", $"validation: {validation.ErrorMessage}");
            return Fail(ErrorCode.InvalidParams, msg);
        }

        var startResult = _moveTo.Start(_childCtx);
        if (!startResult.Accepted)
        {
            string msg = GoToErrorMapper.BuildStepFailMessage(
                _stepIndex, step, "move_to", $"rejected: {startResult.ErrorMessage}");
            return Fail(ErrorCode.ExecutionFailed, msg);
        }

        _childIsMoveToActive = true;
        _phase = GoToPhase.WaitingMoveTo;

        if (startResult.CompletedImmediately)
        {
            _childIsMoveToActive = false;
            EnsureImmediateMoveToResult(step);
            return OnMoveToCompleted(ctx, step);
        }

        return HandlerTickResult.Continue;
    }

    private HandlerTickResult StartDoorInteractMoveTo(ProtoCommandContext ctx, GoToStep step, int childTimeout)
    {
        // DoorInteract phase 1: navigate adjacent to the door tile
        var moveToParams = new MoveToParams
        {
            Mode          = MoveToMode.MoveToAdjacent,
            X             = step.X,
            Y             = step.Y,
            AdjacentSide  = Direction.Unspecified,
            TimeoutFrames = childTimeout,
        };
        _childCtx = GoToChildCommandFactory.CreateMoveToContext(ctx, _stepIndex, moveToParams, Game1.ticks);

        var validation = _moveTo.Validate(_childCtx);
        if (!validation.IsValid)
        {
            string msg = GoToErrorMapper.BuildStepFailMessage(
                _stepIndex, step, "move_to(door-adjacent)", $"validation: {validation.ErrorMessage}");
            return Fail(ErrorCode.InvalidParams, msg);
        }

        var startResult = _moveTo.Start(_childCtx);
        if (!startResult.Accepted)
        {
            string msg = GoToErrorMapper.BuildStepFailMessage(
                _stepIndex, step, "move_to(door-adjacent)", $"rejected: {startResult.ErrorMessage}");
            return Fail(ErrorCode.ExecutionFailed, msg);
        }

        _childIsMoveToActive = true;
        _phase = GoToPhase.WaitingMoveTo;

        if (startResult.CompletedImmediately)
        {
            _childIsMoveToActive = false;
            EnsureImmediateMoveToResult(step);
            return OnMoveToCompleted(ctx, step);
        }

        return HandlerTickResult.Continue;
    }

    private MoveToParams BuildMoveToParams(GoToStep step, int childTimeout) => step.Kind switch
    {
        GoToStepKind.EdgeWarp => new MoveToParams
        {
            Mode          = MoveToMode.MoveToEdgeWarp,
            X             = step.X,
            Y             = step.Y,
            Direction     = step.Direction,
            TimeoutFrames = childTimeout,
        },
        GoToStepKind.FinalMoveExact => new MoveToParams
        {
            Mode           = MoveToMode.MoveToExact,
            X              = step.X,
            Y              = step.Y,
            FaceDirection  = step.FaceDirection,
            TimeoutFrames  = childTimeout,
        },
        GoToStepKind.FinalMoveAdjacent => new MoveToParams
        {
            Mode          = MoveToMode.MoveToAdjacent,
            X             = step.X,
            Y             = step.Y,
            AdjacentSide  = step.StandSide,
            TimeoutFrames = childTimeout,
        },
        _ => new MoveToParams()
    };

    // ─────────────────────────────────────────
    //  WaitingMoveTo
    // ─────────────────────────────────────────

    private HandlerTickResult TickWaitingMoveTo(ProtoCommandContext ctx)
    {
        if (_childCtx == null) return HandlerTickResult.Continue;

        // P1-A: advance child ctx frame so child timing fields are meaningful
        _childCtx.AdvanceFrame(Game1.ticks);
        var tickResult = _moveTo.Tick(_childCtx);

        if (tickResult.Status == HandlerTickStatus.Completed)
        {
            _childIsMoveToActive = false;
            var step = _plan.Steps[_stepIndex];
            return OnMoveToCompleted(ctx, step);
        }

        if (tickResult.Status == HandlerTickStatus.Failed)
        {
            _childIsMoveToActive = false;
            var step = _plan.Steps[_stepIndex];
            string msg = GoToErrorMapper.BuildStepFailMessage(
                _stepIndex, step, "move_to", tickResult.FailureMessage ?? "failed");
            return Fail(tickResult.FailureCode ?? ErrorCode.ExecutionFailed, msg);
        }

        return HandlerTickResult.Continue;
    }

    private HandlerTickResult OnMoveToCompleted(ProtoCommandContext ctx, GoToStep step)
    {
        var moveToResult = _childCtx?.TypedResult as MoveToResult;

        if (step.Kind is GoToStepKind.FinalMoveExact or GoToStepKind.FinalMoveAdjacent)
        {
            _lastFinalMoveResult = moveToResult;
            _stepsCompleted++;
            return Succeed(ctx);
        }

        if (step.Kind == GoToStepKind.EdgeWarp)
        {
            _stepsCompleted++;
            EnterMapStable(step);
            return HandlerTickResult.Continue;
        }

        if (step.Kind == GoToStepKind.DoorInteract)
        {
            // Adjacent positioning done — now interact with the door
            return StartInteract(ctx, step);
        }

        return HandlerTickResult.Continue;
    }

    // ─────────────────────────────────────────
    //  WaitingInteract
    // ─────────────────────────────────────────

    private HandlerTickResult StartInteract(ProtoCommandContext ctx, GoToStep step)
    {
        int remaining    = _totalTimeout - _elapsed;
        int childTimeout = Math.Min(remaining, MaxChildTimeoutFrames);

        var tilePoint = new Protocol.V245.TilePoint { X = step.X, Y = step.Y };
        _childCtx = GoToChildCommandFactory.CreateInteractContext(ctx, _stepIndex, tilePoint, Game1.ticks);

        var validation = _interact.Validate(_childCtx);
        if (!validation.IsValid)
        {
            string msg = GoToErrorMapper.BuildStepFailMessage(
                _stepIndex, step, "interact", $"validation: {validation.ErrorMessage}");
            return Fail(ErrorCode.InvalidParams, msg);
        }

        var startResult = _interact.Start(_childCtx);
        if (!startResult.Accepted)
        {
            string msg = GoToErrorMapper.BuildStepFailMessage(
                _stepIndex, step, "interact", $"rejected: {startResult.ErrorMessage}");
            return Fail(ErrorCode.ExecutionFailed, msg);
        }

        _childIsInteractActive = true;
        _phase = GoToPhase.WaitingInteract;

        if (startResult.CompletedImmediately)
        {
            _childIsInteractActive = false;
            _stepsCompleted++;
            EnterMapStable(step);
            return HandlerTickResult.Continue;
        }

        return HandlerTickResult.Continue;
    }

    private HandlerTickResult TickWaitingInteract(ProtoCommandContext ctx)
    {
        if (_childCtx == null) return HandlerTickResult.Continue;

        // P1-A: advance child ctx frame so child timing fields are meaningful
        _childCtx.AdvanceFrame(Game1.ticks);
        var tickResult = _interact.Tick(_childCtx);

        if (tickResult.Status == HandlerTickStatus.Completed)
        {
            _childIsInteractActive = false;
            var step = _plan.Steps[_stepIndex];
            _stepsCompleted++;
            EnterMapStable(step);
            return HandlerTickResult.Continue;
        }

        if (tickResult.Status == HandlerTickStatus.Failed)
        {
            _childIsInteractActive = false;
            var step = _plan.Steps[_stepIndex];
            string msg = GoToErrorMapper.BuildStepFailMessage(
                _stepIndex, step, "interact", tickResult.FailureMessage ?? "failed");
            return Fail(tickResult.FailureCode ?? ErrorCode.ExecutionFailed, msg);
        }

        return HandlerTickResult.Continue;
    }

    // ─────────────────────────────────────────
    //  WaitingMapStable
    // ─────────────────────────────────────────

    private void EnterMapStable(GoToStep step)
    {
        _phase             = GoToPhase.WaitingMapStable;
        _mapStableElapsed  = 0;
        _mapStableFrames   = 0;
        _lastStableTileX   = -1;
        _lastStableTileY   = -1;
        _monitor.Log($"[GoToProto] Waiting for map stable on '{step.TargetMap}'", LogLevel.Debug);
    }

    private HandlerTickResult TickWaitingMapStable(ProtoCommandContext ctx)
    {
        var step = _plan.Steps[_stepIndex];
        int remaining       = _totalTimeout - _elapsed;
        int stableTimeout   = Math.Min(remaining, MapStableTimeoutFrames);

        _mapStableElapsed++;
        if (_mapStableElapsed > stableTimeout)
        {
            return Fail(ErrorCode.ExecutionFailed,
                $"map transition to '{step.TargetMap}' did not stabilize in {stableTimeout} frames");
        }

        var loc = Game1.currentLocation;
        var player = Game1.player;

        if (loc == null || player == null) return HandlerTickResult.Continue;
        if (loc.NameOrUniqueName != step.TargetMap) return HandlerTickResult.Continue;
        if (!player.canMove) return HandlerTickResult.Continue;
        if (Game1.activeClickableMenu != null) return HandlerTickResult.Continue;
        if (player.UsingTool) return HandlerTickResult.Continue;

        int px = (int)player.Tile.X;
        int py = (int)player.Tile.Y;

        if (px == _lastStableTileX && py == _lastStableTileY)
        {
            _mapStableFrames++;
        }
        else
        {
            _lastStableTileX = px;
            _lastStableTileY = py;
            _mapStableFrames = 1;
        }

        if (_mapStableFrames < MapStableRequiredFrames)
            return HandlerTickResult.Continue;

        _monitor.Log($"[GoToProto] Map stable on '{step.TargetMap}' at ({px},{py})", LogLevel.Debug);
        _stepIndex++;
        _phase = GoToPhase.StartingStep;
        return HandlerTickResult.Continue;
    }

    // ─────────────────────────────────────────
    //  Cancel
    // ─────────────────────────────────────────

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
        CancelActiveChild(reason);
        IsActive = false;
        _phase   = GoToPhase.None;
        _monitor.Log($"[GoToProto] Cancelled: {reason}", LogLevel.Debug);
    }

    private void CancelActiveChild(string reason)
    {
        if (_childIsMoveToActive && _childCtx != null)
        {
            _moveTo.Cancel(_childCtx, reason);
            _childIsMoveToActive = false;
        }
        if (_childIsInteractActive && _childCtx != null)
        {
            _interact.Cancel(_childCtx, reason);
            _childIsInteractActive = false;
        }
    }

    // ─────────────────────────────────────────
    //  Succeed / Fail helpers
    // ─────────────────────────────────────────

    private HandlerTickResult Succeed(ProtoCommandContext ctx)
    {
        var result = new GoToResult
        {
            Start           = _startSnapshot,
            TargetMap       = _targetMap,
            TargetX         = _targetX,
            TargetY         = _targetY,
            Final           = ProtoSnapshotBuilder.CurrentLocationSnapshot(),
            ArrivalMode     = _request.ArrivalMode,
            StandSide       = _request.StandSide,
            FaceDirection   = _request.FaceDirection,
            StepsTotal      = _plan.Steps.Count,
            StepsCompleted  = _stepsCompleted,
            FailedStepIndex = -1,
            FinalMoveResult = _lastFinalMoveResult,
            Timing          = ProtoTiming.Capture(ctx),
        };
        foreach (var m in _plan.RouteMaps)
            result.RouteMaps.Add(m);

        ctx.TypedResult = result;
        IsActive = false;
        _phase   = GoToPhase.Done;
        return HandlerTickResult.Completed;
    }

    // P2: unified failure path — always clears IsActive so no dangling active state
    private HandlerTickResult Fail(ErrorCode code, string message)
    {
        CancelActiveChild(message);
        IsActive = false;
        _phase   = GoToPhase.Failed;
        return HandlerTickResult.Failed(code, message);
    }

    // P1-B: when move_to completes immediately (already-at-target), TypedResult is null.
    // Synthesise a minimal MoveToResult so final_move_result is never empty on success paths.
    private void EnsureImmediateMoveToResult(GoToStep step)
    {
        if (_childCtx == null || _childCtx.TypedResult != null) return;
        var snap = ProtoSnapshotBuilder.CurrentLocationSnapshot();
        var isAdjacent = step.Kind == GoToStepKind.FinalMoveAdjacent
                      || step.Kind == GoToStepKind.DoorInteract;
        var result = new MoveToResult
        {
            Mode                 = isAdjacent ? MoveToMode.MoveToAdjacent : MoveToMode.MoveToExact,
            TargetX              = step.X,
            TargetY              = step.Y,
            ResolvedDestinationX = snap.X,
            ResolvedDestinationY = snap.Y,
            Final                = snap,
            ReachedDestination   = true,
        };
        if (isAdjacent)
        {
            result.StandSide = ComputeActualStandSide(snap.X, snap.Y, step.X, step.Y);
        }
        _childCtx.TypedResult = result;
    }

    private static Direction ComputeActualStandSide(int playerX, int playerY, int targetX, int targetY)
    {
        int dx = playerX - targetX;
        int dy = playerY - targetY;
        if (Math.Abs(dx) >= Math.Abs(dy))
            return dx > 0 ? Direction.Right : Direction.Left;
        return dy > 0 ? Direction.Down : Direction.Up;
    }

    private void ResetState()
    {
        _phase                 = GoToPhase.None;
        _stepIndex             = 0;
        _elapsed               = 0;
        _totalTimeout          = DefaultTimeoutFrames;
        _mapStableElapsed      = 0;
        _mapStableFrames       = 0;
        _lastStableTileX       = -1;
        _lastStableTileY       = -1;
        _startSnapshot         = null!;
        _request               = null!;
        _targetMap             = "";
        _targetX               = 0;
        _targetY               = 0;
        _plan                  = null!;
        _lastFinalMoveResult   = null;
        _stepsCompleted        = 0;
        _childCtx              = null;
        _childIsMoveToActive   = false;
        _childIsInteractActive = false;
        IsActive               = false;
    }
}
