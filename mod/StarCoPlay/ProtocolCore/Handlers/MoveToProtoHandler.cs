using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.ProtocolCore.Results;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.GameServices.Movement;
using NavTile = StarCoPlay.TilePoint;

namespace StarCoPlay.ProtocolCore.Handlers;

/// <summary>
/// Proto-native move_to handler (L1 atom).
///
/// Three modes, determined by MoveToParams.Mode:
///   EXACT     — Navigate to (x,y) on current map, optional face_direction after arrival.
///   EDGE_WARP — Navigate to approach tile (resolved via MapData.WarpTiles), then hold
///               direction until the player's movement bbox crosses the warp tile.
///   ADJACENT  — Navigate to a tile adjacent to (x,y). adjacent_side=UNSPECIFIED → auto-pick
///               any reachable side. Reports actual side used in MoveToResult.stand_side.
///
/// Design notes:
///   - Kind=ActiveLong (navigation can take 30 s; scheduler must not time it out early).
///   - All failure paths → HandlerTickResult.Failed(), never Completed with reached=false.
///   - ResetCommandState() clears every per-command field before each Start().
///   - EDGE_WARP: resolved_destination_x/y = approach tile (before the warp), not post-warp tile.
///   - EDGE_WARP: enumerate ALL warp-lane approach candidates, try NavigateTo in order.
///     raw (x,y) is appended as last-resort fallback only if no lane tile is navigable.
///   - EXACT already-at-target: NavigateTo returns false for trivial path → treat as arrived.
///   - ADJACENT Unspecified adjacent_side → auto-pick best reachable side.
/// </summary>
public sealed class MoveToProtoHandler : IProtoCommandHandler
{
    private enum Phase { None, Navigating, NavigatingToEdge, HoldingDirection, NavigatingToTarget, Facing }
    private enum NavMode { None, Exact, EdgeWarp, Adjacent }

    private const int DefaultTimeoutFrames = 1800;
    private const int HoldTimeoutFrames    = 300;
    private const int StuckThreshold       = 240;
    private const int MaxAdjacentRetries   = 3;

    private readonly ActionExecutor _executor;
    private readonly InputCombo     _combo;
    private readonly InputBridge    _bridge;
    private readonly IMonitor       _monitor;

    public string Capability => "move_to";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.MoveTo;
    // fix #1: ActiveLong — navigation can take up to 30 s
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveLong;
    public bool IsActive { get; private set; }

    // ── Per-command state (all reset in ResetCommandState) ──
    private Phase   _phase;
    private NavMode _navMode;
    private int     _elapsed;
    private int     _timeout;
    private int     _targetX, _targetY;
    private int     _resolvedX, _resolvedY;   // approach tile (EDGE_WARP) / walk tile (ADJACENT)
    private LocationSnapshot _startSnapshot = null!;

    // Exact
    private Direction _requestedFace;
    private bool      _faceStarted;
    private bool      _exactTargetIsWalkThroughWarp;

    // EdgeWarp
    private string    _startLocation;
    private Direction _requestedDirection;
    private int       _edgeDirInt;
    private bool      _holdStarted;
    private int       _holdElapsed;
    private bool      _warpTriggered;

    // Adjacent
    private Direction _requestedSide;
    private Direction _resolvedSide;
    private int       _adjacentFaceDir;
    private int       _adjacentRetries;

    // Stuck detection
    private int _stuckX, _stuckY, _stuckFrames;

    public MoveToProtoHandler(ActionExecutor executor, InputCombo combo, InputBridge bridge, IMonitor monitor)
    {
        _executor = executor;
        _combo    = combo;
        _bridge   = bridge;
        _monitor  = monitor;
        _startLocation = "";
    }

    // ─────────────────────────────────────────
    //  Validate
    // ─────────────────────────────────────────

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        if (Game1.player == null)
            return ProtoValidationResult.Invalid("player is null");
        if (Game1.activeClickableMenu != null)
            return ProtoValidationResult.Invalid("a menu is open");
        if (Game1.player.UsingTool)
            return ProtoValidationResult.Invalid("player is using a tool");

        var p = ctx.Request.MoveTo;
        if (p.Mode == MoveToMode.Unspecified)
            return ProtoValidationResult.Invalid("mode must not be UNSPECIFIED");
        // fix #2: ADJACENT Unspecified adjacent_side is now allowed (auto-pick)
        if (p.Mode == MoveToMode.MoveToEdgeWarp && p.Direction == Direction.Unspecified)
            return ProtoValidationResult.Invalid("EDGE_WARP requires direction");
        return ProtoValidationResult.Ok();
    }

    // ─────────────────────────────────────────
    //  Start
    // ─────────────────────────────────────────

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        // fix #4: reset all per-command fields before populating
        ResetCommandState();

        var p = ctx.Request.MoveTo;
        _targetX       = p.X;
        _targetY       = p.Y;
        _timeout       = p.TimeoutFrames > 0 ? p.TimeoutFrames : DefaultTimeoutFrames;
        _startSnapshot = ProtoSnapshotBuilder.CurrentLocationSnapshot();
        _startLocation = Game1.currentLocation?.NameOrUniqueName ?? "";
        ResetStuck();

        return p.Mode switch
        {
            MoveToMode.MoveToExact    => StartExact(p),
            MoveToMode.MoveToEdgeWarp => StartEdgeWarp(p),
            MoveToMode.MoveToAdjacent => StartAdjacent(p),
            _ => HandlerStartResult.Rejected($"unhandled mode: {p.Mode}")
        };
    }

    // ── Exact ────────────────────────────────

    private HandlerStartResult StartExact(MoveToParams p)
    {
        _navMode       = NavMode.Exact;
        _requestedFace = p.FaceDirection;
        _resolvedX     = _targetX;
        _resolvedY     = _targetY;
        _exactTargetIsWalkThroughWarp = IsWalkThroughWarpTile(
            MapData.GetMap(_startLocation), _targetX, _targetY);

        if (!_executor.NavigateTo(new Point(_targetX, _targetY)))
        {
            // fix #6: already at target → PFC returns false for trivial path → treat as arrived
            if (IsAlreadyAt(_targetX, _targetY))
            {
                _monitor.Log($"[MoveToProto] EXACT: already at ({_targetX},{_targetY})", LogLevel.Debug);
                if (_exactTargetIsWalkThroughWarp)
                {
                    _phase = Phase.Navigating;
                    IsActive = true;
                    return HandlerStartResult.Async();
                }
                return StartFaceOrImmediate(p.FaceDirection);
            }
            return HandlerStartResult.Rejected($"cannot navigate to ({_targetX},{_targetY})");
        }

        _monitor.Log($"[MoveToProto] EXACT → ({_targetX},{_targetY})", LogLevel.Debug);
        _phase = Phase.Navigating;
        IsActive = true;
        return HandlerStartResult.Async();
    }

    private HandlerStartResult StartFaceOrImmediate(Direction face)
    {
        if (face != Direction.Unspecified)
        {
            _combo.Face(ProtoSnapshotBuilder.FromProtoDirection(face));
            _faceStarted = true;
            _phase = Phase.Facing;
            IsActive = true;
            return HandlerStartResult.Async();
        }
        // No face needed and already there → immediate success
        return HandlerStartResult.Immediate();
    }

    // ── EdgeWarp ─────────────────────────────

    private HandlerStartResult StartEdgeWarp(MoveToParams p)
    {
        _navMode        = NavMode.EdgeWarp;
        _startLocation  = Game1.currentLocation?.NameOrUniqueName ?? "";

        // Build approach candidates for all 4 directions around the warp tile.
        // The caller's p.Direction is a hint (preferred first), but we try all directions
        // because indoor warps trigger on tile collision regardless of facing.
        int hintDirInt = ProtoSnapshotBuilder.FromProtoDirection(p.Direction);
        var candidates = BuildEdgeApproachCandidatesAllDirs(_targetX, _targetY, hintDirInt);

        foreach (var (cx, cy, dirInt) in candidates)
        {
            if (IsAlreadyAt(cx, cy) || IsOnAxis(cx, cy, dirInt))
            {
                _resolvedX          = cx;
                _resolvedY          = cy;
                _edgeDirInt         = dirInt;
                _requestedDirection = FacingToProtoDirection(dirInt);
                _monitor.Log($"[MoveToProto] EDGE_WARP: already at/adjacent-on-axis approach ({cx},{cy}), hold {_requestedDirection}", LogLevel.Debug);
                _phase = Phase.HoldingDirection;
                IsActive = true;
                return HandlerStartResult.Async();
            }
            if (_executor.NavigateTo(new Point(cx, cy)))
            {
                _resolvedX          = cx;
                _resolvedY          = cy;
                _edgeDirInt         = dirInt;
                _requestedDirection = FacingToProtoDirection(dirInt);
                _monitor.Log($"[MoveToProto] EDGE_WARP: nav → ({cx},{cy}) then hold {_requestedDirection}", LogLevel.Debug);
                _phase = Phase.NavigatingToEdge;
                IsActive = true;
                return HandlerStartResult.Async();
            }
        }

        return HandlerStartResult.Rejected(
            $"cannot reach any warp approach tile near ({_targetX},{_targetY}) direction={p.Direction}");
    }

    /// <summary>
    /// Build ordered list of approach tile candidates for EDGE_WARP, trying all 4 directions.
    ///
    /// For each direction, the approach tile is one step back from the warp tile (opposite
    /// to the walk direction). The hint direction is tried first, then the remaining three.
    /// This ensures indoor warps (triggered by tile collision, not edge-crossing) find a
    /// reachable approach regardless of the exit-level direction heuristic.
    /// </summary>
    private List<(int x, int y, int dirInt)> BuildEdgeApproachCandidatesAllDirs(int refX, int refY, int hintDirInt)
    {
        var result = new List<(int, int, int)>();
        var seen   = new HashSet<(int, int)>();

        // Order: hint direction first, then remaining directions
        var dirs = new int[4];
        dirs[0] = hintDirInt;
        int idx = 1;
        for (int d = 0; d < 4; d++)
        {
            if (d != hintDirInt) dirs[idx++] = d;
        }

        // Primary: use MapData lane tiles for the matching exit
        var mapEntry = MapData.GetMap(Game1.currentLocation?.NameOrUniqueName ?? "");
        if (mapEntry != null)
        {
            MapExit? bestExit = null;
            int      bestDist = int.MaxValue;
            foreach (var exit in mapEntry.Exits)
            {
                if (exit.Kind != WarpKind.WalkThrough) continue;
                foreach (var tile in exit.WarpTiles)
                {
                    int dist = Math.Max(Math.Abs(tile.X - refX), Math.Abs(tile.Y - refY));
                    if (dist < bestDist) { bestDist = dist; bestExit = exit; }
                }
            }

            if (bestExit != null && bestDist <= 2)
            {
                // For each warp tile in the exit, try all directions
                // Sort lane tiles by distance to the reference point
                var laneTiles = bestExit.WarpTiles
                    .OrderBy(t => Math.Abs(t.X - refX) + Math.Abs(t.Y - refY))
                    .ToList();

                foreach (var warpTile in laneTiles)
                {
                    foreach (int d in dirs)
                    {
                        var (ax, ay) = ApproachTileOffset(warpTile.X, warpTile.Y, d);
                        if (seen.Add((ax, ay)))
                            result.Add((ax, ay, d));
                    }
                }

                _monitor.Log(
                    $"[MoveToProto] EDGE_WARP: built {result.Count} approach candidate(s) from {laneTiles.Count} lane tile(s)",
                    LogLevel.Debug);
            }
        }

        // Fallback: try all directions for the raw reference tile
        foreach (int d in dirs)
        {
            var (ax, ay) = ApproachTileOffset(refX, refY, d);
            if (seen.Add((ax, ay)))
                result.Add((ax, ay, d));
        }

        return result;
    }

    /// <summary>One step back from warp tile, opposite to walk direction.</summary>
    private static (int x, int y) ApproachTileOffset(int wx, int wy, int dirInt) => dirInt switch
    {
        0 => (wx,     wy + 1),   // walk north → stand south
        1 => (wx - 1, wy),       // walk east  → stand west
        2 => (wx,     wy - 1),   // walk south → stand north
        3 => (wx + 1, wy),       // walk west  → stand east
        _ => (wx,     wy)
    };

    // ── Adjacent ─────────────────────────────

    private HandlerStartResult StartAdjacent(MoveToParams p)
    {
        _navMode       = NavMode.Adjacent;
        _requestedSide = p.AdjacentSide;
        _resolvedSide  = Direction.Unspecified;

        // fix #2: Unspecified → auto-pick; otherwise start from requested side
        int startDir = p.AdjacentSide != Direction.Unspecified
            ? ProtoSnapshotBuilder.FromProtoDirection(p.AdjacentSide)
            : 2;  // default start: south

        // Try: requested → opposite → +90 → -90
        int[] candidates = { startDir, (startDir + 2) % 4, (startDir + 1) % 4, (startDir + 3) % 4 };

        foreach (var sideDir in candidates)
        {
            var (wx, wy, faceDir) = ComputeAdjacentWalkTarget(_targetX, _targetY, sideDir);

            // Already there?
            if (IsAlreadyAt(wx, wy))
            {
                _resolvedX       = wx;
                _resolvedY       = wy;
                _adjacentFaceDir = faceDir;
                _resolvedSide    = FacingToProtoDirection(sideDir);
                _monitor.Log($"[MoveToProto] ADJACENT: already at ({wx},{wy}) side={_resolvedSide}", LogLevel.Debug);
                _combo.Face(faceDir);
                _faceStarted = true;
                _phase = Phase.Facing;
                IsActive = true;
                return HandlerStartResult.Async();
            }

            if (_executor.NavigateTo(new Point(wx, wy)))
            {
                _resolvedX       = wx;
                _resolvedY       = wy;
                _adjacentFaceDir = faceDir;
                _resolvedSide    = FacingToProtoDirection(sideDir);
                bool isFallback  = sideDir != startDir || p.AdjacentSide == Direction.Unspecified;
                _monitor.Log(
                    $"[MoveToProto] ADJACENT: target ({_targetX},{_targetY}) side={_resolvedSide}{(isFallback ? " [auto/fallback]" : "")} → walk ({wx},{wy})",
                    LogLevel.Debug);
                _phase = Phase.NavigatingToTarget;
                IsActive = true;
                return HandlerStartResult.Async();
            }
        }

        return HandlerStartResult.Rejected($"no walkable adjacent tile around ({_targetX},{_targetY})");
    }

    // ─────────────────────────────────────────
    //  Tick
    // ─────────────────────────────────────────

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        _elapsed++;
        if (_elapsed > _timeout)
        {
            _monitor.Log($"[MoveToProto] timeout after {_elapsed} frames mode={_navMode} phase={_phase}", LogLevel.Warn);
            CleanupMovement();
            // fix #3: timeout is a failure, not a success
            return Fail(ErrorCode.Timeout, $"move_to timeout ({_elapsed} frames) mode={_navMode}");
        }

        return _phase switch
        {
            Phase.Navigating         => TickNavigating(ctx),
            Phase.NavigatingToEdge   => TickNavigatingToEdge(ctx),
            Phase.HoldingDirection   => TickHoldingDirection(ctx),
            Phase.NavigatingToTarget => TickNavigatingToTarget(ctx),
            Phase.Facing             => TickFacing(ctx),
            _ => HandlerTickResult.Continue
        };
    }

    private HandlerTickResult TickNavigating(ProtoCommandContext ctx)
    {
        ObserveStuck();

        string currentLocation = Game1.currentLocation?.NameOrUniqueName ?? "";
        if (!string.Equals(currentLocation, _startLocation, StringComparison.OrdinalIgnoreCase))
        {
            _warpTriggered = true;
            _monitor.Log($"[MoveToProto] EXACT: warp triggered → {currentLocation}", LogLevel.Info);
            if (_requestedFace != Direction.Unspecified)
            {
                _combo.Face(ProtoSnapshotBuilder.FromProtoDirection(_requestedFace));
                _faceStarted = true;
                _phase = Phase.Facing;
                return HandlerTickResult.Continue;
            }
            return Succeed(ctx);
        }

        if (_executor.IsBusy) return HandlerTickResult.Continue;

        // Walk-through warps transition on a game update after navigation reaches the
        // source tile. Do not report the pre-warp tile as the final result.
        if (_exactTargetIsWalkThroughWarp)
            return HandlerTickResult.Continue;

        if (IsNavInterrupted(_resolvedX, _resolvedY, "EXACT"))
            // fix #3: interrupted navigation is a failure
            return Fail(ErrorCode.ExecutionFailed, $"EXACT nav interrupted, player not at ({_resolvedX},{_resolvedY})");

        if (_requestedFace != Direction.Unspecified)
        {
            _combo.Face(ProtoSnapshotBuilder.FromProtoDirection(_requestedFace));
            _faceStarted = true;
            _phase = Phase.Facing;
            return HandlerTickResult.Continue;
        }
        return Succeed(ctx);
    }

    private HandlerTickResult TickNavigatingToEdge(ProtoCommandContext ctx)
    {
        ObserveStuck();
        if (_executor.IsBusy) return HandlerTickResult.Continue;

        if (IsNavInterrupted(_resolvedX, _resolvedY, "EDGE_WARP"))
            return Fail(ErrorCode.ExecutionFailed, $"EDGE_WARP nav interrupted, player not at approach tile ({_resolvedX},{_resolvedY})");

        _phase = Phase.HoldingDirection;
        _holdStarted = false;
        _holdElapsed = 0;
        ResetStuck();
        _monitor.Log($"[MoveToProto] EDGE_WARP: at approach tile, holding {_requestedDirection}", LogLevel.Debug);
        return HandlerTickResult.Continue;
    }

    private HandlerTickResult TickHoldingDirection(ProtoCommandContext ctx)
    {
        string curLoc = Game1.currentLocation?.NameOrUniqueName ?? "";
        if (!string.Equals(curLoc, _startLocation, StringComparison.OrdinalIgnoreCase))
        {
            _bridge.Clear();
            _warpTriggered = true;
            // fix #5: resolved_destination stays as approach tile; do NOT overwrite with post-warp position
            _monitor.Log($"[MoveToProto] EDGE_WARP: warp triggered → {curLoc}", LogLevel.Info);
            return Succeed(ctx);
        }

        if (!_holdStarted)
        {
            string startLoc = _startLocation;
            _bridge.Hold(
                DirectionToButton(_edgeDirInt),
                stopCondition: () => !string.Equals(
                    Game1.currentLocation?.NameOrUniqueName ?? "",
                    startLoc,
                    StringComparison.OrdinalIgnoreCase),
                timeout: HoldTimeoutFrames);
            _holdStarted = true;
        }

        _holdElapsed++;
        if (_holdElapsed > HoldTimeoutFrames)
        {
            _bridge.Clear();
            CleanupMovement();
            return Fail(ErrorCode.Timeout,
                $"EDGE_WARP: held {_requestedDirection} for {HoldTimeoutFrames} frames, warp not triggered");
        }
        return HandlerTickResult.Continue;
    }

    private HandlerTickResult TickNavigatingToTarget(ProtoCommandContext ctx)
    {
        ObserveStuck();
        if (_executor.IsBusy) return HandlerTickResult.Continue;

        if (IsNavInterrupted(_resolvedX, _resolvedY, "ADJACENT"))
        {
            if (_adjacentRetries < MaxAdjacentRetries && _executor.NavigateTo(new Point(_resolvedX, _resolvedY)))
            {
                _adjacentRetries++;
                ResetStuck();
                _monitor.Log($"[MoveToProto] ADJACENT retry {_adjacentRetries}/{MaxAdjacentRetries}", LogLevel.Debug);
                return HandlerTickResult.Continue;
            }
            return Fail(ErrorCode.ExecutionFailed,
                $"ADJACENT nav interrupted after {_adjacentRetries} retries, player not at ({_resolvedX},{_resolvedY})");
        }

        _adjacentRetries = 0;
        _combo.Face(_adjacentFaceDir);
        _faceStarted = true;
        _phase = Phase.Facing;
        return HandlerTickResult.Continue;
    }

    private HandlerTickResult TickFacing(ProtoCommandContext ctx)
    {
        if (_bridge.IsBusy) return HandlerTickResult.Continue;
        return Succeed(ctx);
    }

    // ─────────────────────────────────────────
    //  Cancel
    // ─────────────────────────────────────────

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
        CleanupMovement();
        IsActive = false;
        _monitor.Log($"[MoveToProto] Cancelled: {reason}", LogLevel.Debug);
    }

    // ─────────────────────────────────────────
    //  Succeed / Fail
    // ─────────────────────────────────────────

    private HandlerTickResult Succeed(ProtoCommandContext ctx)
    {
        ctx.TypedResult = BuildResult(reached: true, ctx);
        IsActive = false;
        _phase   = Phase.None;
        return HandlerTickResult.Completed;
    }

    /// <summary>
    /// fix #3: all failure paths use HandlerTickResult.Failed(), never Completed.
    /// The scheduler maps Failed → CommandResult with success=false / status=FAILED.
    /// </summary>
    private HandlerTickResult Fail(ErrorCode code, string message)
    {
        IsActive = false;
        _phase   = Phase.None;
        return HandlerTickResult.Failed(code, message);
    }

    private MoveToResult BuildResult(bool reached, ProtoCommandContext ctx)
    {
        return new MoveToResult
        {
            Mode = _navMode switch
            {
                NavMode.Exact    => MoveToMode.MoveToExact,
                NavMode.EdgeWarp => MoveToMode.MoveToEdgeWarp,
                NavMode.Adjacent => MoveToMode.MoveToAdjacent,
                _                => MoveToMode.Unspecified
            },
            Start                  = _startSnapshot,
            TargetX                = _targetX,
            TargetY                = _targetY,
            // fix #5: approach tile for EDGE_WARP, adjacent walk tile for ADJACENT
            ResolvedDestinationX   = _resolvedX,
            ResolvedDestinationY   = _resolvedY,
            Final                  = ProtoSnapshotBuilder.CurrentLocationSnapshot(),
            RequestedDirection     = _requestedDirection,
            RequestedFaceDirection = _requestedFace,
            StandSide              = _resolvedSide,
            ReachedDestination     = reached,
            WarpTriggered          = _warpTriggered,
            Timing                 = ProtoTiming.Capture(ctx),
        };
    }

    // ─────────────────────────────────────────
    //  Stuck observation
    // ─────────────────────────────────────────

    private void ObserveStuck()
    {
        var player = Game1.player;
        if (player == null) return;
        int cx = (int)player.Tile.X;
        int cy = (int)player.Tile.Y;
        if (cx == _stuckX && cy == _stuckY)
        {
            _stuckFrames++;
            if (_stuckFrames == StuckThreshold)
                _monitor.Log($"[MoveToProto] possible stuck at ({cx},{cy}) for {StuckThreshold} frames (phase={_phase})", LogLevel.Warn);
        }
        else
        {
            _stuckX = cx; _stuckY = cy; _stuckFrames = 0;
        }
    }

    private void ResetStuck()
    {
        _stuckX      = (int)(Game1.player?.Tile.X ?? 0);
        _stuckY      = (int)(Game1.player?.Tile.Y ?? 0);
        _stuckFrames = 0;
    }

    // ─────────────────────────────────────────
    //  Navigation helpers
    // ─────────────────────────────────────────

    private bool IsNavInterrupted(int expectedX, int expectedY, string label)
    {
        var player = Game1.player;
        if (player == null) return false;
        float dx = player.Tile.X - expectedX;
        float dy = player.Tile.Y - expectedY;
        float distSq = dx * dx + dy * dy;
        if (distSq <= 4f) return false;
        _monitor.Log(
            $"[MoveToProto] {label}: nav interrupted — player ({(int)player.Tile.X},{(int)player.Tile.Y}) expected ({expectedX},{expectedY}) dist²={distSq:F1}",
            LogLevel.Warn);
        return true;
    }

    // fix #6: check if player is already on (or essentially at) the target tile
    private static bool IsAlreadyAt(int x, int y)
    {
        var tile = Game1.player?.Tile;
        if (!tile.HasValue) return false;
        float dx = tile.Value.X - x;
        float dy = tile.Value.Y - y;
        return dx * dx + dy * dy <= 0.25f;  // within 0.5 tile
    }

    private static bool IsNearby(int x, int y)
    {
        var tile = Game1.player?.Tile;
        if (!tile.HasValue) return false;
        int dx = Math.Abs((int)tile.Value.X - x);
        int dy = Math.Abs((int)tile.Value.Y - y);
        return dx <= 2 && dy <= 2;
    }

    internal static bool IsWalkThroughWarpTile(MapEntry? map, int x, int y)
    {
        if (map == null) return false;
        return map.Exits.Any(exit =>
            exit.Kind == WarpKind.WalkThrough
            && exit.WarpTiles.Any(tile => tile.X == x && tile.Y == y));
    }

    /// <summary>
    /// Returns true if the player is on the same axis as the approach tile AND at most 1 tile away.
    /// dirInt: 0=up, 1=right, 2=down, 3=left.
    /// For up/down warps: same X column, dy <= 1.
    /// For left/right warps: same Y row, dx <= 1.
    /// This ensures the player can trigger the warp from the adjacent tile on the correct lane.
    /// </summary>
    private static bool IsOnAxis(int x, int y, int dirInt)
    {
        var tile = Game1.player?.Tile;
        if (!tile.HasValue) return false;
        int px = (int)tile.Value.X;
        int py = (int)tile.Value.Y;

        return dirInt switch
        {
            0 or 2 => px == x && Math.Abs(py - y) <= 1, // up/down: same column, adjacent row
            1 or 3 => py == y && Math.Abs(px - x) <= 1, // left/right: same row, adjacent column
            _      => false,
        };
    }

    private void CleanupMovement()
    {
        _executor.CancelCurrent();
        if (_holdStarted || _faceStarted) _bridge.Clear();
        var player = Game1.player;
        if (player == null) return;
        player.controller = null;
        player.movementDirections.Clear();
        player.Halt();
    }

    // ─────────────────────────────────────────
    //  Reset
    // ─────────────────────────────────────────

    /// <summary>
    /// fix #4: clear all per-command fields so state from a prior invocation
    /// cannot leak into the next Start().
    /// </summary>
    private void ResetCommandState()
    {
        _phase             = Phase.None;
        _navMode           = NavMode.None;
        _elapsed           = 0;
        _timeout           = DefaultTimeoutFrames;
        _targetX           = 0;
        _targetY           = 0;
        _resolvedX         = 0;
        _resolvedY         = 0;
        _startSnapshot     = null!;
        // Exact
        _requestedFace     = Direction.Unspecified;
        _faceStarted       = false;
        _exactTargetIsWalkThroughWarp = false;
        // EdgeWarp
        _startLocation     = "";
        _requestedDirection = Direction.Unspecified;
        _edgeDirInt        = 0;
        _holdStarted       = false;
        _holdElapsed       = 0;
        _warpTriggered     = false;
        // Adjacent
        _requestedSide     = Direction.Unspecified;
        _resolvedSide      = Direction.Unspecified;
        _adjacentFaceDir   = 0;
        _adjacentRetries   = 0;
        // Stuck
        _stuckX            = 0;
        _stuckY            = 0;
        _stuckFrames       = 0;
    }

    // ─────────────────────────────────────────
    //  Direction utilities
    // ─────────────────────────────────────────

    private static SButton DirectionToButton(int sdvFacing) => sdvFacing switch
    {
        0 => SButton.W,
        1 => SButton.D,
        2 => SButton.S,
        3 => SButton.A,
        _ => throw new ArgumentOutOfRangeException(nameof(sdvFacing))
    };

    private static (int wx, int wy, int faceDir) ComputeAdjacentWalkTarget(int tx, int ty, int sideDir) => sideDir switch
    {
        0 => (tx,     ty - 1, 2),   // stand north → face south(2)
        1 => (tx + 1, ty,     3),   // stand east  → face west(3)
        2 => (tx,     ty + 1, 0),   // stand south → face north(0)
        3 => (tx - 1, ty,     1),   // stand west  → face east(1)
        _ => (tx,     ty,     0)
    };

    private static Direction FacingToProtoDirection(int sdvFacing) => sdvFacing switch
    {
        0 => Direction.Up,
        1 => Direction.Right,
        2 => Direction.Down,
        3 => Direction.Left,
        _ => Direction.Unspecified
    };
}
