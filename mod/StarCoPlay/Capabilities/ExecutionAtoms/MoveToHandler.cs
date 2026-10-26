using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.GameServices.Movement;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.V2;
using StarCoPlay.V2.Compound;
using StarCoPlay.V2.Models;

namespace StarCoPlay.Capabilities.ExecutionAtoms;

/// <summary>
/// L1 V2 — move_to 命令：精简版 move_to，只承担"原子移动"职责。
///
/// 与旧 <see cref="MoveToHandler"/> 的区别：
/// - 旧版承担过多职责：跨地图 BFS 路由、warp 编排、TargetRef 追踪、tp fallback、
///   动态实体重路径……一个 Handler 内塞进 5 阶段状态机 + 多种 fallback。
/// - V2 版回归 L1 的"原子"定位：单地图寻路 / 边界传送 / 走到目标相邻 + 自动面朝。
///   跨地图语义导航、动态目标追踪等"组合"语义上移到 L2 (go_to / execute_on_targets)。
///
/// 三种模式（互斥）：
///   A. 精准坐标 + 可选转向    x, y [+ face_direction]    → 同地图寻路到 (x, y)，到达后可选转向
///   B. 边界传送              x, y, direction            → 寻路到调用方指定的 (x, y) → 持续按方向键触发 warp
///   C. 走到相邻 + 面朝目标    x, y, adjacent_side        → 寻路到目标 adjacent_side 侧的相邻 tile → 自动面朝目标
///
/// 模式 A 例：触发酒馆门 = move_to(x=45, y=71, face_direction="up")
///   - 走到酒馆门 tile (45, 71)
///   - 然后朝上看（face_direction 可选；不提供则到达即完成）
///
/// 模式 B 设计变更（2026-04-30）：
/// - 旧版自动算 mapWidth-1 边界 tile，但实测边界 tile 经常不可达（如 (64,23) 被障碍占据），导致寻路失败。
/// - 新版要求调用方提供 (x, y) — 调用方负责选择"靠近边界且可达"的 tile（L2 知识层应该掌握地图布局信息）。
/// - 不再 fallback FindAdjacentWalkable，因为相邻 tile 也可能远离 warp 触发位置。
///
/// 模式 C 重新定义（2026-05-01）：
/// - 旧语义：face_direction="up" → 走到 (x, y+1) 然后朝 up（语义反直觉，调用方需脑补"反向"）
/// - 新语义：adjacent_side="south" → 玩家站在目标的南侧 (x, y+1)，自动朝北面对目标（调用方直接表达"站哪边"）
/// - 例：站门下方 = move_to(x=45, y=71, adjacent_side="south") → 玩家位于 (45, 72) 朝北
///
/// 入参契约：
///   x: int                       目标 tile X（模式 A/B/C 都需要）
///   y: int                       目标 tile Y（模式 A/B/C 都需要）
///   direction: string            edge warp 方向：east / west / north / south（模式 B 必需）
///   face_direction: string       到达后转向：up / down / left / right（north/south/east/west 也接受）（模式 A 可选）
///   adjacent_side: string        玩家最终站在目标哪一侧：north/south/east/west 或 up/down/left/right（模式 C 必需）
///   timeout_frames: int          可选超时帧数，默认 1800
///
/// 模式判定优先级（C > B > A）：
///   有 x,y + adjacent_side                 → C（走到相邻 + 自动面朝目标）
///   有 x,y + direction                     → B（寻路 + Hold direction 触发 warp）
///   有 x,y [+ 可选 face_direction]         → A（精准坐标 + 可选转向）
///   有 direction 但无 x,y                  → 错误
///   都没有                                  → 错误
///
/// 注意：
/// - 本 Handler 不做跨地图寻路（旧 MoveToHandler.location/cross_map 已彻底剥离）
/// - 不支持 TargetRef 追踪（动态实体追踪交给 L2 execute_on_targets）
/// </summary>
public sealed class MoveToHandler : IMoveToHandler
{
    /// <summary>移动状态机阶段。三种模式共用一套状态机，按 _mode 决定流转。</summary>
    private enum Phase
    {
        None,
        /// <summary>模式 A: 同地图寻路中（PathFindController 驱动）。</summary>
        Navigating,
        /// <summary>模式 B: 走向地图边界 tile（PathFindController 驱动）。</summary>
        NavigatingToEdge,
        /// <summary>模式 B: 已到达边界，持续按方向键等待 warp 触发。</summary>
        HoldingDirection,
        /// <summary>模式 C: 走向"目标 tile 的 adjacent_side 侧相邻 tile"（PathFindController 驱动）。</summary>
        NavigatingToTarget,
        /// <summary>模式 A/C: 寻路完成，等待 InputCombo.Face 转身完成。</summary>
        Facing,
        /// <summary>已到达，构建 success。</summary>
        Done,
        /// <summary>已失败（占位，实际失败直接返回 V2Result）。</summary>
        Failed
    }

    private enum Mode
    {
        None,
        /// <summary>A: 精准坐标 + 可选转向。</summary>
        Coord,
        /// <summary>B: 边界传送。</summary>
        EdgeWarp,
        /// <summary>C: 走到目标相邻 + 自动面朝目标。</summary>
        WalkAndAdjacent
    }

    private readonly ActionExecutor _executor;
    private readonly InputCombo _combo;
    private readonly InputBridge _bridge;
    private readonly AnimationAwaiter _animAwaiter;
    private readonly IMonitor _monitor;
    private readonly int _defaultTimeoutFrames;

    // ── 当前命令状态 ──
    private V2Command? _cmd;
    private Phase _phase = Phase.None;
    private Mode _mode = Mode.None;
    private int _elapsedFrames;
    private int _timeoutFrames;

    // ── 共用目标参数 ──
    private int _targetX;
    private int _targetY;
    private string _startLocation = "";

    // ── 模式 A 专用（可选转向）──
    private bool _hasFaceAfterMove;
    private int _faceAfterMoveDir;     // 0=up 1=right 2=down 3=left（InputCombo.Face 语义）

    // ── 模式 B 专用 ──
    private string _edgeDirection = ""; // east / west / north / south
    private int _edgeDirectionInt;      // 0=north 1=east 2=south 3=west（与 InputCombo 不同：B 用罗盘语义）
    private int _holdElapsed;
    private bool _holdStarted;
    private const int HoldTimeoutTicks = 300; // 5 秒按住超时

    // ── 模式 C 专用 ──
    private int _faceDirectionInt; // 0=up 1=right 2=down 3=left（InputCombo.Face 语义，朝向目标）
    private int _walkTargetX;      // 实际寻路到的 tile（adjacent_side 决定）
    private int _walkTargetY;
    private string _adjacentSide = ""; // 仅记录用于 result/log
    private bool _faceStarted;

    /// <summary>是否有正在执行的移动（供 CommandProcessor 判断）。</summary>
    public bool IsActive => _phase != Phase.None;

    public MoveToHandler(
        ActionExecutor executor,
        InputCombo combo,
        InputBridge bridge,
        AnimationAwaiter animAwaiter,
        IMonitor monitor,
        int defaultTimeoutFrames = 1800)
    {
        _executor = executor;
        _combo = combo;
        _bridge = bridge;
        _animAwaiter = animAwaiter;
        _monitor = monitor;
        _defaultTimeoutFrames = defaultTimeoutFrames;
    }

    /// <summary>Adapter entrypoint used by AdapterV2.</summary>
    public V2Result? Handle(V2Command cmd) => Start(cmd);

    /// <summary>
    /// 解析参数并启动对应模式。
    /// - 同步失败：直接返回 V2Result
    /// - 异步进行中：返回 null，CommandProcessor 后续调用 Tick() 推进
    /// </summary>
    public V2Result? Start(V2Command cmd)
    {
        Reset();
        _cmd = cmd;

        if (!cmd.IsValid())
        {
            var failure = ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "move_to 命令缺少必要字段");
            Reset();
            return failure;
        }

        // 注：原"失焦防呆（IsActive）"已在 Stage A 切换到 IInputSimulator 后移除。
        // 模式 A/B/C 通过 simulator 路径直接覆写 SDV ref bool，失焦也能工作；防呆变成 anti-feature。

        var p = cmd.Params ?? new Dictionary<string, object>();

        _timeoutFrames = CompoundUtils.TryGetInt(p, "timeout_frames", out var t) && t > 0
            ? t
            : _defaultTimeoutFrames;

        bool hasX = CompoundUtils.TryGetInt(p, "x", out var x);
        bool hasY = CompoundUtils.TryGetInt(p, "y", out var y);
        bool hasTile = hasX && hasY;
        bool hasDirection = CompoundUtils.TryGetString(p, "direction", out var directionStr)
            && !string.IsNullOrWhiteSpace(directionStr);
        bool hasAdjacentSide = CompoundUtils.TryGetString(p, "adjacent_side", out var adjacentStr)
            && !string.IsNullOrWhiteSpace(adjacentStr);

        _startLocation = Game1.currentLocation?.NameOrUniqueName ?? "";

        // ── 模式判定（互斥，优先级 C > B > A）──
        if (hasAdjacentSide)
        {
            if (!hasTile)
            {
                var failure = ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.InvalidParams,
                    "模式 C (adjacent_side) 需要同时提供 x, y 参数");
                Reset();
                return failure;
            }
            return StartWalkAndAdjacent(x, y, adjacentStr);
        }

        if (hasDirection)
        {
            // 模式 B：调用方必须提供 (x, y) — 表示"靠近边界且可达的 tile"。
            // 旧设计自动算 mapWidth-1 边界 tile，但实测边界 tile 经常不可达。
            if (!hasTile)
            {
                var failure = ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.InvalidParams,
                    "模式 B (direction) 需要同时提供 x, y 参数（调用方选择靠近边界且可达的 tile）");
                Reset();
                return failure;
            }
            return StartEdgeWarp(x, y, directionStr);
        }

        if (hasTile)
        {
            // 模式 A：精准坐标，face_direction 可选
            return StartCoord(x, y);
        }

        var paramFailure = ResultBuilder.Failure(
            cmd.Seq, cmd.ActionId,
            ResultBuilder.Errors.InvalidParams,
            "move_to 需要参数：(A) x, y [+ face_direction] / (B) x, y, direction / (C) x, y, adjacent_side");
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
                $"[ExecutionAtom] move_to timeout after {_elapsedFrames} frames, phase={_phase}, mode={_mode}",
                LogLevel.Warn);
            CleanupMovement();
            var timeoutResult = ResultBuilder.Failure(
                _cmd.Seq, _cmd.ActionId,
                ResultBuilder.Errors.NavigationTimeout,
                $"move_to 超时（>{_timeoutFrames} 帧），mode={_mode}, phase={_phase}");
            Reset();
            return timeoutResult;
        }

        switch (_phase)
        {
            case Phase.Navigating:
                return TickNavigating();
            case Phase.NavigatingToEdge:
                return TickNavigatingToEdge();
            case Phase.HoldingDirection:
                return TickHoldingDirection();
            case Phase.NavigatingToTarget:
                return TickNavigatingToTarget();
            case Phase.Facing:
                return TickFacing();
            case Phase.Done:
                return BuildCompletionResult();
            default:
                return null;
        }
    }

    /// <summary>取消当前移动（CommandProcessor 在停止/清理时调用）。</summary>
    public void Cancel()
    {
        CleanupMovement();
        Reset();
    }

    // ════════════════════════════════════════════════════════
    //  模式 A: 精准坐标 + 可选转向
    // ════════════════════════════════════════════════════════

    private V2Result? StartCoord(int x, int y)
    {
        _mode = Mode.Coord;
        _targetX = x;
        _targetY = y;

        // 解析可选 face_direction
        _hasFaceAfterMove = false;
        if (CompoundUtils.TryGetString(_cmd!.Params ?? new Dictionary<string, object>(), "face_direction", out var faceStr)
            && !string.IsNullOrWhiteSpace(faceStr))
        {
            if (!TryParseDirection(faceStr, out var dir))
            {
                var failure = ResultBuilder.Failure(
                    _cmd.Seq, _cmd.ActionId,
                    ResultBuilder.Errors.InvalidParams,
                    $"模式 A: face_direction 无效（必须是 up/down/left/right 或 north/south/east/west），实际: {faceStr}");
                Reset();
                return failure;
            }
            _hasFaceAfterMove = true;
            _faceAfterMoveDir = dir;
        }

        if (!_executor.NavigateTo(new Point(x, y)))
        {
            var failure = ResultBuilder.Failure(
                _cmd.Seq, _cmd.ActionId,
                ResultBuilder.Errors.PathBlocked,
                $"模式 A: 无法寻路到 ({x},{y})，目标可能不可达");
            Reset();
            return failure;
        }

        _phase = Phase.Navigating;
        _monitor.Log(
            _hasFaceAfterMove
                ? $"[ExecutionAtom] move_to mode=A coord → ({x},{y}) face={_faceAfterMoveDir}"
                : $"[ExecutionAtom] move_to mode=A coord → ({x},{y})",
            LogLevel.Debug);
        return null; // 异步等 Tick
    }

    private V2Result? TickNavigating()
    {
        if (_executor.IsBusy)
            return null;

        // bug #11 修复：区分"自然到达"与"被外部强制取消（StuckDetector / 其他模块）"。
        // ActionExecutor.CancelCurrent 会把 _currentState 立即置为 Idle 并清掉 player.controller，
        // 此时 _executor.IsBusy=false 与"controller 自然完成"无法区分。
        // 唯一可靠区分：玩家是否到了目标 tile 附近（distSq ≤ 4，与 ActionExecutor 内部 success 阈值一致）。
        // 距离过大 → 视为被打断未到达，返回 PathBlocked 让上层（ESM）走失败链而非误判 Success。
        if (DetectInterruptedNavigation(_targetX, _targetY, "模式 A"))
        {
            return BuildInterruptedResult(_targetX, _targetY, "模式 A");
        }

        // 寻路完成 → 如果有 face_direction，入队 Face 并进入 Facing 阶段
        if (_hasFaceAfterMove)
        {
            _combo.Face(_faceAfterMoveDir);
            _faceStarted = true;
            _phase = Phase.Facing;
            return null;
        }

        _phase = Phase.Done;
        return BuildCompletionResult();
    }

    // ════════════════════════════════════════════════════════
    //  模式 B: 边界传送
    // ════════════════════════════════════════════════════════

    private V2Result? StartEdgeWarp(int x, int y, string directionStr)
    {
        _mode = Mode.EdgeWarp;
        _edgeDirection = directionStr.ToLowerInvariant();

        // direction 罗盘语义 → InputCombo.Face 语义（0=上 1=右 2=下 3=左）
        // 注意：与 face_direction 共用 InputCombo 方向值，方便复用 Hold(button)
        switch (_edgeDirection)
        {
            case "north": case "up":    _edgeDirectionInt = 0; break;
            case "east":  case "right": _edgeDirectionInt = 1; break;
            case "south": case "down":  _edgeDirectionInt = 2; break;
            case "west":  case "left":  _edgeDirectionInt = 3; break;
            default:
                var fail = ResultBuilder.Failure(
                    _cmd!.Seq, _cmd.ActionId,
                    ResultBuilder.Errors.InvalidParams,
                    $"模式 B: direction 必须是 east/west/north/south，实际: {directionStr}");
                Reset();
                return fail;
        }

        // 调用方提供的 tile（应为"靠近边界且可达"的 tile，由 L2 知识层负责选择）
        _targetX = x;
        _targetY = y;

        _monitor.Log(
            $"[ExecutionAtom] move_to mode=B edge_warp direction={_edgeDirection} 调用方指定 tile ({x},{y})",
            LogLevel.Debug);

        // 玩家已"足够接近"目标 tile（切比雪夫距离 ≤ 2）→ 跳过 NavigateTo，直接 hold direction 触发 walk-through warp。
        // 典型场景：
        //   1. 玩家完全在目标 tile 上（PFC 空路径会判失败）
        //   2. 上段 IntraMap 把玩家停在 warp tile 旁（PFC 把 walk-through warp tile 视为不可停留，
        //      会停在最近可停留 tile，距离 1-2 格，例：BusStop (43,23) 距离 (44,22) 约 1.4）
        // hold direction 走一步即可走上 walk-through tile 触发 warp。
        var pTile = Game1.player?.Tile;
        if (pTile.HasValue)
        {
            int dx = Math.Abs((int)pTile.Value.X - x);
            int dy = Math.Abs((int)pTile.Value.Y - y);
            if (dx <= 2 && dy <= 2)
            {
                _monitor.Log(
                    $"[ExecutionAtom] move_to mode=B 玩家 ({(int)pTile.Value.X},{(int)pTile.Value.Y}) 距离目标 ({x},{y}) 仅 {dx},{dy} 格，跳过 NavigateTo 直接 hold direction",
                    LogLevel.Debug);
                _phase = Phase.HoldingDirection;
                _holdElapsed = 0;
                _holdStarted = false;
                return null;
            }
        }

        // 寻路到调用方指定的 tile（不再自动算边界 + 不再 fallback FindAdjacentWalkable）
        // 调用方负责选可达 tile；不可达则直接失败，由调用方重新选 tile 重试。
        if (!_executor.NavigateTo(new Point(x, y)))
        {
            var fail = ResultBuilder.Failure(
                _cmd!.Seq, _cmd.ActionId,
                ResultBuilder.Errors.PathBlocked,
                $"模式 B: 无法寻路到 ({x},{y})，请确保该 tile 可达");
            Reset();
            return fail;
        }

        _phase = Phase.NavigatingToEdge;
        return null;
    }

    private V2Result? TickNavigatingToEdge()
    {
        // 还在寻路 → 继续等
        if (_executor.IsBusy)
            return null;

        // bug #11 修复：被外部强制取消但未到达边界 tile 时，直接失败而非误进入 HoldingDirection。
        // 否则会在错误位置按方向键导致 5 秒 HoldTimeout 后才返回 WarpTimeout（浪费 5 秒）。
        if (DetectInterruptedNavigation(_targetX, _targetY, "模式 B"))
        {
            return BuildInterruptedResult(_targetX, _targetY, "模式 B");
        }

        // 寻路完成，开始持续按方向键直到 location 改变
        _phase = Phase.HoldingDirection;
        _holdElapsed = 0;
        _holdStarted = false;
        _monitor.Log(
            $"[ExecutionAtom] move_to mode=B 到达边界，开始按方向键 direction={_edgeDirection}",
            LogLevel.Debug);
        return null;
    }

    private V2Result? TickHoldingDirection()
    {
        // 检查 location 是否已改变 → warp 触发成功
        string currentLocation = Game1.currentLocation?.NameOrUniqueName ?? "";
        if (!string.Equals(currentLocation, _startLocation, StringComparison.OrdinalIgnoreCase))
        {
            _monitor.Log(
                $"[ExecutionAtom] move_to mode=B warp 触发：{_startLocation} → {currentLocation}",
                LogLevel.Info);
            // 按键队列可能还有残留，停止后清空
            _bridge.Clear();
            _targetX = (int)Game1.player.Tile.X;
            _targetY = (int)Game1.player.Tile.Y;
            _phase = Phase.Done;
            return BuildCompletionResult();
        }

        // 第一次进入：入队 Hold(direction button, stopCondition=location 改变)
        // 用 Hold 而非 HoldFor，让 InputBridge 在 location 变化时自动停止
        if (!_holdStarted)
        {
            var button = DirectionToButton(_edgeDirectionInt);
            string startLoc = _startLocation;
            _bridge.Hold(
                button,
                stopCondition: () => !string.Equals(
                    Game1.currentLocation?.NameOrUniqueName ?? "",
                    startLoc,
                    StringComparison.OrdinalIgnoreCase),
                timeout: HoldTimeoutTicks);
            _holdStarted = true;
        }

        _holdElapsed++;
        if (_holdElapsed > HoldTimeoutTicks)
        {
            _bridge.Clear();
            CleanupMovement();
            var failure = ResultBuilder.Failure(
                _cmd!.Seq, _cmd.ActionId,
                ResultBuilder.Errors.WarpTimeout,
                $"模式 B: 按 {_edgeDirection} 方向键 {HoldTimeoutTicks} 帧后 location 仍未改变（边界可能没有 warp）");
            Reset();
            return failure;
        }

        return null;
    }

    // ════════════════════════════════════════════════════════
    //  模式 C: 走到目标相邻 + 自动面朝目标
    // ════════════════════════════════════════════════════════

    private V2Result? StartWalkAndAdjacent(int x, int y, string adjacentSide)
    {
        _mode = Mode.WalkAndAdjacent;
        _targetX = x;
        _targetY = y;
        _adjacentSide = adjacentSide.ToLowerInvariant();

        // 解析 adjacent_side 为方向值（0=up 1=right 2=down 3=left）
        if (!TryParseDirection(adjacentSide, out var sideDir))
        {
            var failure = ResultBuilder.Failure(
                _cmd!.Seq, _cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                $"模式 C: adjacent_side 无效（必须是 north/south/east/west 或 up/down/left/right），实际: {adjacentSide}");
            Reset();
            return failure;
        }

        // 主候选 + fallback：优先尝试用户指定的 adjacent_side，失败时依次尝试其他三个方向。
        // 典型场景：NPC 站在柜台/墙后，用户期望 south adjacent 但 (x, y+1) 不可达（柜台后），
        // fallback 自动尝试 north/east/west 找到可寻路 tile。
        // 顺序：sideDir 自身 → 反方向 → 两侧
        var primary = sideDir;
        var fallbacks = new[] { (primary + 2) % 4, (primary + 1) % 4, (primary + 3) % 4 };
        var candidates = new[] { primary }.Concat(fallbacks).ToArray();

        foreach (var dir in candidates)
        {
            var (wtx, wty, face) = ComputeWalkAndFace(x, y, dir);
            if (_executor.NavigateTo(new Point(wtx, wty)))
            {
                _walkTargetX = wtx;
                _walkTargetY = wty;
                _faceDirectionInt = face;
                _adjacentSide = DirIntToString(dir);
                _monitor.Log(
                    $"[ExecutionAtom] move_to mode=C walk_and_adjacent 目标 ({x},{y}) adjacent_side={_adjacentSide}{(dir == primary ? "" : " [fallback]")} → 寻路到 ({wtx},{wty}) 后朝 face={face}",
                    LogLevel.Debug);
                _phase = Phase.NavigatingToTarget;
                return null;
            }
        }

        // 4 个方向都不可达 → 失败
        var (defWtx, defWty, _) = ComputeWalkAndFace(x, y, primary);
        var failureAll = ResultBuilder.Failure(
            _cmd!.Seq, _cmd.ActionId,
            ResultBuilder.Errors.PathBlocked,
            $"模式 C: 4 个 adjacent 方向都不可达（目标 ({x},{y})，主候选 {_adjacentSide} → ({defWtx},{defWty})）");
        Reset();
        return failureAll;
    }

    /// <summary>计算 adjacent_side dir int 对应的 walk_target 坐标 + face 方向。</summary>
    private static (int wtx, int wty, int face) ComputeWalkAndFace(int x, int y, int sideDir) => sideDir switch
    {
        0 => (x, y - 1, 2),  // 站北侧 → 朝南
        1 => (x + 1, y, 3),  // 站东侧 → 朝西
        2 => (x, y + 1, 0),  // 站南侧 → 朝北
        3 => (x - 1, y, 1),  // 站西侧 → 朝东
        _ => (x, y, 0),
    };

    private static string DirIntToString(int dir) => dir switch
    {
        0 => "north", 1 => "east", 2 => "south", 3 => "west", _ => "?",
    };

    private V2Result? TickNavigatingToTarget()
    {
        if (_executor.IsBusy)
            return null;

        // PFC 在长路径上可能提前完成 controller（SDV 内部 sprite barrier / NPC 阻挡），
        // 玩家未到 walk_target 时自动重试 NavigateTo，最多 N 次。
        if (DetectInterruptedNavigation(_walkTargetX, _walkTargetY, "模式 C"))
        {
            if (_modeCRetries < MaxModeCRetries)
            {
                _modeCRetries++;
                _monitor.Log(
                    $"[ExecutionAtom] 模式 C 重试 {_modeCRetries}/{MaxModeCRetries}: NavigateTo ({_walkTargetX},{_walkTargetY})",
                    LogLevel.Debug);
                if (_executor.NavigateTo(new Point(_walkTargetX, _walkTargetY)))
                    return null; // NavigateTo 成功，等下一帧 IsBusy=true → 进入下次 Tick
                // 重试 NavigateTo 失败 → fall through 到 BuildInterruptedResult
            }
            return BuildInterruptedResult(_walkTargetX, _walkTargetY, "模式 C");
        }

        // 寻路完成 → 入队 Face（朝向目标）
        _modeCRetries = 0;
        _combo.Face(_faceDirectionInt);
        _faceStarted = true;
        _phase = Phase.Facing;
        return null;
    }

    private int _modeCRetries;
    private const int MaxModeCRetries = 3;

    private V2Result? TickFacing()
    {
        // 等 InputBridge 队列消化完（Face 入队的 Tap 完成）
        if (_bridge.IsBusy)
            return null;

        // 二次确认：若已经面朝目标方向则视为完成（容错：极快情况下 Face 可能已被旧朝向短路）
        int expectedFace = _mode == Mode.Coord ? _faceAfterMoveDir : _faceDirectionInt;
        if (Game1.player.FacingDirection != expectedFace && _faceStarted)
        {
            // Face 完成但方向未对齐（极少见）→ 不视为失败，直接 Done（玩家位置已到位）
            _monitor.Log(
                $"[ExecutionAtom] move_to mode={_mode}: Face 完成但 FacingDirection={Game1.player.FacingDirection} 与期望 {expectedFace} 不一致",
                LogLevel.Warn);
        }

        _phase = Phase.Done;
        return BuildCompletionResult();
    }

    // ════════════════════════════════════════════════════════
    //  完成与清理
    // ════════════════════════════════════════════════════════

    private V2Result BuildCompletionResult()
    {
        var player = Game1.player;
        var location = Game1.currentLocation;

        string modeStr = _mode switch
        {
            Mode.Coord => "coord",
            Mode.EdgeWarp => "edge_warp",
            Mode.WalkAndAdjacent => "walk_and_adjacent",
            _ => "unknown"
        };

        var data = new Dictionary<string, object>
        {
            ["mode"] = modeStr,
            ["target"] = new Dictionary<string, object>
            {
                ["x"] = _targetX,
                ["y"] = _targetY
            },
            ["final_position"] = new Dictionary<string, object>
            {
                ["location"] = location?.NameOrUniqueName ?? "Unknown",
                ["x"] = (int)player.Tile.X,
                ["y"] = (int)player.Tile.Y
            },
            ["facing"] = player.FacingDirection
        };

        if (_mode == Mode.Coord && _hasFaceAfterMove)
        {
            data["face_direction"] = _faceAfterMoveDir;
        }

        if (_mode == Mode.EdgeWarp)
        {
            data["start_location"] = _startLocation;
            data["direction"] = _edgeDirection;
        }

        if (_mode == Mode.WalkAndAdjacent)
        {
            data["walk_to"] = new Dictionary<string, object>
            {
                ["x"] = _walkTargetX,
                ["y"] = _walkTargetY
            };
            data["adjacent_side"] = _adjacentSide;
            data["face_direction"] = _faceDirectionInt;
        }

        string observation = _mode switch
        {
            Mode.Coord =>
                _hasFaceAfterMove
                    ? $"已到达 ({(int)player.Tile.X},{(int)player.Tile.Y}) 并转向 {_faceAfterMoveDir}"
                    : $"已到达 ({(int)player.Tile.X},{(int)player.Tile.Y})",
            Mode.EdgeWarp =>
                $"沿 {_edgeDirection} 方向离开 {_startLocation}，已到达 {location?.NameOrUniqueName ?? "Unknown"} ({(int)player.Tile.X},{(int)player.Tile.Y})",
            Mode.WalkAndAdjacent =>
                $"已到达 ({_walkTargetX},{_walkTargetY})（目标 ({_targetX},{_targetY}) 的 {_adjacentSide} 侧）并面朝目标",
            _ => $"已到达 ({(int)player.Tile.X},{(int)player.Tile.Y})"
        };

        var result = ResultBuilder.Success(_cmd!.Seq, _cmd.ActionId, data)
            .WithObservation(observation);

        Reset();
        return result;
    }

    /// <summary>
    /// bug #11 修复：检测寻路是否被外部强制中断（如 StuckDetector → StuckRecovery → CancelCurrent）。
    ///
    /// 触发场景：
    ///   ActionExecutor.CancelCurrent() 会立即把 _currentState 置 Idle 并清掉 player.controller，
    ///   此时 _executor.IsBusy=false 与"controller 自然走完到达目标"完全无法区分。
    ///
    /// 唯一可靠的区分手段：玩家当前位置距期望目标的距离。
    ///   - distSq ≤ 4（曼哈顿等价 ≈ 2 tile）→ 视为正常到达（与 ActionExecutor.TickNavigation success 阈值一致）
    ///   - distSq > 4 → 视为被外部打断，未到达
    ///
    /// 修复前：被打断时 move_to 原子误返回 Success，ESM 信以为真，DoorInteract 段直接 InteractWithTile，
    ///         玩家在远处不可能交互成功 → ESM verify 失败 → 重发同样 NavigateTo → StuckDetector 再次触发 → 死循环。
    /// </summary>
    private bool DetectInterruptedNavigation(int expectedX, int expectedY, string modeLabel)
    {
        var player = Game1.player;
        if (player == null) return false;
        float dx = player.Tile.X - expectedX;
        float dy = player.Tile.Y - expectedY;
        float distSq = dx * dx + dy * dy;
        if (distSq <= 4f) return false; // 已到达视野内，视为正常完成
        _monitor.Log(
            $"[ExecutionAtom] {modeLabel}: 检测到外部打断（IsBusy=false 但玩家 ({(int)player.Tile.X},{(int)player.Tile.Y}) 距目标 ({expectedX},{expectedY}) dist²={distSq:F1} > 4），返回 PATH_BLOCKED",
            LogLevel.Warn);
        return true;
    }

    /// <summary>bug #11 修复：构建"被外部打断"失败结果。</summary>
    private V2Result BuildInterruptedResult(int expectedX, int expectedY, string modeLabel)
    {
        var player = Game1.player;
        int actualX = (int)(player?.Tile.X ?? 0);
        int actualY = (int)(player?.Tile.Y ?? 0);
        var failure = ResultBuilder.Failure(
            _cmd!.Seq, _cmd.ActionId,
            ResultBuilder.Errors.PathBlocked,
            $"{modeLabel}: 寻路被外部中断，玩家位于 ({actualX},{actualY}) 距目标 ({expectedX},{expectedY}) 过远（可能由 StuckDetector / 其他模块取消）");
        CleanupMovement();
        Reset();
        return failure;
    }

    /// <summary>清理移动相关状态（停止 PathFindController + 清除残留方向）。</summary>
    private void CleanupMovement()
    {
        _executor.CancelCurrent();
        // 清理 InputBridge 队列（防止模式 B 的 Hold / 模式 A&C 的 Face 残留按键）
        if (_holdStarted || _faceStarted)
            _bridge.Clear();

        var player = Game1.player;
        player.controller = null;
        player.movementDirections.Clear();
        player.Halt();
    }

    /// <summary>方向值转 SButton 按键（与 InputCombo.DirectionToButton 同语义）。</summary>
    /// <param name="direction">0=上(W) 1=右(D) 2=下(S) 3=左(A)</param>
    private static SButton DirectionToButton(int direction)
    {
        return direction switch
        {
            0 => SButton.W,
            1 => SButton.D,
            2 => SButton.S,
            3 => SButton.A,
            _ => throw new ArgumentOutOfRangeException(nameof(direction),
                $"方向值必须是 0-3，实际: {direction}")
        };
    }

    /// <summary>
    /// 解析方向字符串为 InputCombo.Face 语义（0=up 1=right 2=down 3=left）。
    /// 同时接受罗盘语义（north/east/south/west）和 WASD 语义（up/right/down/left）。
    /// </summary>
    private static bool TryParseDirection(string s, out int direction)
    {
        direction = -1;
        if (string.IsNullOrWhiteSpace(s)) return false;
        direction = s.Trim().ToLowerInvariant() switch
        {
            "up" or "north" => 0,
            "right" or "east" => 1,
            "down" or "south" => 2,
            "left" or "west" => 3,
            _ => -1,
        };
        return direction >= 0;
    }

    private void Reset()
    {
        _cmd = null;
        _phase = Phase.None;
        _mode = Mode.None;
        _elapsedFrames = 0;
        _timeoutFrames = _defaultTimeoutFrames;
        _targetX = 0;
        _targetY = 0;
        _startLocation = "";
        _hasFaceAfterMove = false;
        _faceAfterMoveDir = 0;
        _edgeDirection = "";
        _edgeDirectionInt = 0;
        _holdElapsed = 0;
        _holdStarted = false;
        _faceDirectionInt = 0;
        _walkTargetX = 0;
        _walkTargetY = 0;
        _adjacentSide = "";
        _faceStarted = false;
    }
}
