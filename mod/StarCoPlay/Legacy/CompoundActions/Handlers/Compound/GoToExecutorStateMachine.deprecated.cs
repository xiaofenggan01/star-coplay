// ============================================================================
// DEPRECATED — go_to L2 重设计旧方案（已弃用，2026-05-03）
//
// 状态：暂停使用，等待新版 go_to L2 实现替代
// 排除：通过 StarCoPlay.csproj 中 `<Compile Remove="**\*.deprecated.cs" />` 排除编译
// 原因：方案重新设计；保留源码作为旧实现的历史快照与设计参考
//
// MUST NOT：在新代码中引用本文件中的任何类型；如需复用思路请整段重写
// ============================================================================

using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// 7 态状态机，逐段消费 PathSegment[]，串行调用 L1V2 三模式 / InteractWithTile。
/// 处理动态追踪（re-resolve）、失败计数、重规划（re-route）。
/// </summary>
public class GoToExecutorStateMachine
{
    private enum State
    {
        Idle,
        SegmentStarting,
        SegmentRunning,
        WaitingForMapLoad,
        ReResolving,
        ReRouting,
        Completed,
        Failed,
    }

    private const int MaxNavFailures = 5;
    private const int MaxReRouteCount = 3;
    private const int WarpClosedTimeoutFrames = 300;
    private const int MapLoadRaceGuardFrames = 2;
    private const int DoorInteractWaitFrames = 60;

    private readonly IMoveToHandler _moveToV2;
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    private State _state = State.Idle;
    private PathSegment[] _segments = Array.Empty<PathSegment>();
    private int _segmentIndex;
    private ResolvedTarget _resolvedTarget = null!;
    private string _rawTarget = "";
    private V2Command _cmd = null!;

    private int _totalNavFailures;
    private int _reRouteCount;
    private int _reResolveCount;
    private int _warpsTraversed;
    private int _waitFrames;
    private int _elapsedFrames;
    private int _totalTimeoutFrames;
    private bool _doorInteractPending;

    // bug #8 修复：当前 segment 内已尝试的 warp tile 候选索引（0 = 首选）。
    // 切换 segment 时重置；Mode A/B 启动失败且仍有候选时递增并重新启动。
    private int _warpCandidateIndex;

    // bug #5 修复：当前 warp 候选是否已尝试过 FindAdjacentWalkable 替代 tile。
    // 防止"替代失败 → 又找替代"无限循环。切换候选 / 进入新 segment 时重置。
    private bool _currentCandidateAlternativeTried;

    // bug #5 修复：若已切到替代 tile，记录其坐标用于 StartWarpSegment 重启时使用。
    // null 表示当前用首选/原候选 tile，非 null 表示用替代 tile。
    private (int X, int Y)? _activeAlternativeTile;

    // bug #12 修复：DoorInteract 段在替代 tile 走完后，是否已尝试 final-step
    // （再次 NavigateTo 原门站立 tile）。避免"replay 又失败 → 又激活替代 → 又
    // replay → ...”循环。每个 candidate 一次，切候选/进新 segment 时重置。
    // 必须 final-step：(58,87) 替代 tile 上调 InteractWithTile(58,86) 时，玩家
    // 面朝 north → checkAction 看 (58,86) 而非真正 doorPoint=(58,85)，warp
    // 不会触发；只有玩家踩到 (58,86)（SourceY 站立位置）后面朝 north 才能 reach
    // doorPoint=(58,85) 上的 Action:Warp。
    private bool _doorFinalStepRetried;

    // bug #3 修复：启动失败后冷却帧数，避免每帧重试导致 navFails 瞬时打满。
    // >0 时 TickSegmentStarting 入口直接递减并 return null；归零才尝试重启。
    // 仅在"候选耗尽 + 替代也失败"路径设置（30 帧 ≈ 0.5 秒）。
    private int _navRetryCooldownFrames;
    private const int NavRetryCooldownFrames = 30;

    // WaitingForMapLoad 后等待竞态保护帧数
    private int _raceGuardFrames;

    public bool IsActive => _state != State.Idle;

    public GoToExecutorStateMachine(
        IMoveToHandler moveToV2,
        ActionExecutor executor,
        IMonitor monitor)
    {
        _moveToV2 = moveToV2;
        _executor = executor;
        _monitor = monitor;
    }

    public void Start(
        PathSegment[] segments,
        ResolvedTarget resolvedTarget,
        string rawTarget,
        V2Command cmd)
    {
        _segments = segments;
        _segmentIndex = 0;
        _resolvedTarget = resolvedTarget;
        _rawTarget = rawTarget;
        _cmd = cmd;
        _totalNavFailures = 0;
        _reRouteCount = 0;
        _reResolveCount = 0;
        _warpsTraversed = 0;
        _waitFrames = 0;
        _elapsedFrames = 0;
        _doorInteractPending = false;
        _raceGuardFrames = 0;
        _warpCandidateIndex = 0;
        _currentCandidateAlternativeTried = false;
        _activeAlternativeTile = null;
        _doorFinalStepRetried = false;
        _navRetryCooldownFrames = 0;

        int segCount = segments.Length;
        _totalTimeoutFrames = (segCount + 1) * 1800 + MaxReRouteCount * 1800;

        if (segments.Length == 0)
        {
            _state = State.Completed;
        }
        else
        {
            _state = State.SegmentStarting;
        }
    }

    public V2Result? Tick()
    {
        if (_state == State.Idle)
            return null;

        _elapsedFrames++;
        if (_elapsedFrames > _totalTimeoutFrames)
        {
            _monitor.Log(
                $"[GoToESM] NAVIGATION_TIMEOUT after {_elapsedFrames} frames, state={_state}",
                LogLevel.Warn);
            return FailResult(GoToErrors.NavigationTimeout,
                $"go_to 执行超时（>{_totalTimeoutFrames} 帧），当前阶段={_state}");
        }

        return _state switch
        {
            State.SegmentStarting => TickSegmentStarting(),
            State.SegmentRunning => TickSegmentRunning(),
            State.WaitingForMapLoad => TickWaitingForMapLoad(),
            State.ReResolving => TickReResolving(),
            State.ReRouting => TickReRouting(),
            State.Completed => BuildSuccessResult(),
            State.Failed => null,
            _ => null,
        };
    }

    public V2Result Cancel()
    {
        if (_moveToV2.IsActive) _moveToV2.Cancel();
        CleanupMovement();
        var result = BuildPartialResult("cancelled");
        Reset();
        return result;
    }

    // ── SegmentStarting ────────────────────────────────────────────────────

    private V2Result? TickSegmentStarting()
    {
        // bug #3 修复：失败后冷却中，递减并跳过本帧（区分瞬时与永久不可达）
        if (_navRetryCooldownFrames > 0)
        {
            _navRetryCooldownFrames--;
            return null;
        }

        if (_segmentIndex >= _segments.Length)
        {
            _state = State.Completed;
            return BuildSuccessResult();
        }

        var seg = _segments[_segmentIndex];

        if (seg.Type == SegmentType.IntraMap)
        {
            return StartIntraMapSegment(seg);
        }
        else
        {
            return StartWarpSegment(seg);
        }
    }

    private V2Result? StartIntraMapSegment(PathSegment seg)
    {
        V2Command l1Cmd;

        if (seg.FinalHint == null)
        {
            l1Cmd = BuildMoveToCommand_A(seg.ToX, seg.ToY);
        }
        else if (!seg.FinalHint.UseAdjacentSide)
        {
            l1Cmd = BuildMoveToCommand_A(seg.ToX, seg.ToY, seg.FinalHint.FaceDirection);
        }
        else
        {
            string? side = seg.FinalHint.AdjacentSide;
            if (side == null)
            {
                side = FindBestAdjacentSide(seg.ToX, seg.ToY);
            }
            if (side == null)
            {
                l1Cmd = BuildMoveToCommand_A(seg.ToX, seg.ToY);
            }
            else
            {
                l1Cmd = BuildMoveToCommand_C(seg.ToX, seg.ToY, side);
            }
        }

        var startResult = _moveToV2.Start(l1Cmd);
        if (startResult != null && !startResult.Success)
        {
            _totalNavFailures++;
            _monitor.Log(
                $"[GoToESM] L1V2 启动失败 ({seg.ToX},{seg.ToY}) navFails={_totalNavFailures}",
                LogLevel.Warn);
            if (_totalNavFailures >= MaxNavFailures)
            {
                return FailResult(GoToErrors.TargetUnreachable,
                    $"目标物理不可达，已尝试 {_totalNavFailures} 次导航失败");
            }
            // bug #3 修复：失败后设 cooldown 30 帧（≈0.5s），区分瞬时不可达
            _navRetryCooldownFrames = NavRetryCooldownFrames;
            _state = State.SegmentStarting;
            return null;
        }

        if (startResult != null && startResult.Success)
        {
            AdvanceToNextSegment();
            return null;
        }

        _state = State.SegmentRunning;
        return null;
    }

    private V2Result? StartWarpSegment(PathSegment seg)
    {
        // bug #8 修复：根据当前候选索引取当前要尝试的 warp tile 坐标。
        // 候选列表第 0 项即 seg.FromX/FromY；后续候选用于首选失败时回退。
        // bug #5 修复：若 _activeAlternativeTile 已设置，优先用替代 tile（覆盖候选首选）。
        var (curFromX, curFromY) = _activeAlternativeTile ?? GetCurrentWarpCandidate(seg);

        if (seg.Mechanism == WarpMechanism.EdgeWarp)
        {
            string direction = InferDirectionFromSegment(seg);
            var l1Cmd = BuildMoveToCommand_B(curFromX, curFromY, direction);
            var startResult = _moveToV2.Start(l1Cmd);
            if (startResult != null && !startResult.Success)
            {
                _monitor.Log(
                    $"[GoToESM] 模式 B 启动失败 ({curFromX},{curFromY}) candIdx={_warpCandidateIndex} altTried={_currentCandidateAlternativeTried}",
                    LogLevel.Warn);
                // bug #5：先尝试 FindAdjacentWalkable 找当前候选的邻近替代 tile
                if (TryActivateAlternativeTile(seg, curFromX, curFromY))
                {
                    _state = State.SegmentStarting;
                    return null;
                }
                // bug #8：替代失败 → 切换到下一个候选 warp tile
                if (TryAdvanceWarpCandidate(seg))
                {
                    _state = State.SegmentStarting;
                    return null;
                }

                _totalNavFailures++;
                _monitor.Log(
                    $"[GoToESM] 模式 B 候选耗尽 navFails={_totalNavFailures}",
                    LogLevel.Warn);
                if (_totalNavFailures >= MaxNavFailures)
                {
                    return FailResult(GoToErrors.TargetUnreachable,
                        $"无法到达 warp tile ({curFromX},{curFromY})，累计 {_totalNavFailures} 次失败（候选已穷尽）");
                }
                // bug #3：候选耗尽 + 替代也失败 → 设 cooldown
                _navRetryCooldownFrames = NavRetryCooldownFrames;
                _state = State.SegmentStarting;
                return null;
            }

            if (startResult != null && startResult.Success)
            {
                // 模式 B 成功（已切换地图）
                _warpsTraversed++;
                _waitFrames = 0;
                _raceGuardFrames = 0;
                _state = State.WaitingForMapLoad;
                return null;
            }

            _state = State.SegmentRunning;
            return null;
        }
        else
        {
            // DoorInteract（多候选场景理论上罕见，但同样支持回退以防御未来变化）
            var l1Cmd = BuildMoveToCommand_A(curFromX, curFromY);
            var startResult = _moveToV2.Start(l1Cmd);
            if (startResult != null && !startResult.Success)
            {
                _monitor.Log(
                    $"[GoToESM] DoorInteract 启动失败 ({curFromX},{curFromY}) candIdx={_warpCandidateIndex} altTried={_currentCandidateAlternativeTried}",
                    LogLevel.Warn);
                // bug #5 修复：先尝试 FindAdjacentWalkable 找邻近替代 tile（门前被占据时关键）
                if (TryActivateAlternativeTile(seg, curFromX, curFromY))
                {
                    _state = State.SegmentStarting;
                    return null;
                }
                // bug #8：替代失败 → 切换候选 warp tile
                if (TryAdvanceWarpCandidate(seg))
                {
                    _state = State.SegmentStarting;
                    return null;
                }
                _totalNavFailures++;
                if (_totalNavFailures >= MaxNavFailures)
                    return FailResult(GoToErrors.TargetUnreachable,
                        $"无法走到门前 ({curFromX},{curFromY})（候选已穷尽）");
                // bug #3：候选耗尽 + 替代也失败 → 设 cooldown
                _navRetryCooldownFrames = NavRetryCooldownFrames;
                _state = State.SegmentStarting;
                return null;
            }

            _doorInteractPending = true;
            _state = State.SegmentRunning;
            return null;
        }
    }

    /// <summary>
    /// 取当前 segment 应使用的 warp tile 坐标。
    /// _warpCandidateIndex=0 时返回 seg.FromX/FromY（首选）；
    /// >0 时从 seg.WarpCandidates 中取（已越界则 fallback 回首选）。
    /// </summary>
    private (int X, int Y) GetCurrentWarpCandidate(PathSegment seg)
    {
        if (seg.WarpCandidates == null || _warpCandidateIndex <= 0
            || _warpCandidateIndex >= seg.WarpCandidates.Count)
        {
            return (seg.FromX, seg.FromY);
        }
        return seg.WarpCandidates[_warpCandidateIndex];
    }

    /// <summary>
    /// 尝试切换到下一个 warp tile 候选。
    /// 候选耗尽返回 false，由调用方走累计失败计数路径。
    /// bug #5：切换候选时重置 alternativeTried，新候选可独立尝试 FindAdjacentWalkable。
    /// </summary>
    private bool TryAdvanceWarpCandidate(PathSegment seg)
    {
        if (seg.WarpCandidates == null) return false;
        int next = _warpCandidateIndex + 1;
        if (next >= seg.WarpCandidates.Count) return false;
        _warpCandidateIndex = next;
        _currentCandidateAlternativeTried = false;
        _activeAlternativeTile = null;
        _doorFinalStepRetried = false; // bug #12：新候选独立尝试 final-step
        var (nx, ny) = seg.WarpCandidates[next];
        _monitor.Log(
            $"[GoToESM] 切换 warp tile 候选 idx={next}/{seg.WarpCandidates.Count - 1} → ({nx},{ny})",
            LogLevel.Info);
        return true;
    }

    /// <summary>
    /// bug #5 修复：尝试激活当前候选的"邻近替代 tile"。
    /// 触发条件：当前候选启动失败且尚未尝试过替代（防止循环）。
    /// 用 CompoundUtils.FindAdjacentWalkable 找原 tile 邻近的可走 tile，
    /// 找到则设 _activeAlternativeTile，下次 SegmentStarting 重新启动时会用它。
    /// 返回 false 表示无替代可用（地图不匹配 / 已尝试过 / 无邻近 walkable）。
    /// </summary>
    private bool TryActivateAlternativeTile(PathSegment seg, int origX, int origY)
    {
        if (_currentCandidateAlternativeTried) return false;
        _currentCandidateAlternativeTried = true;

        var location = Game1.currentLocation;
        if (location == null) return false;

        // 仅对当前 segment 的"出发地图"启用替代（玩家应在此地图上）
        if (!string.Equals(location.NameOrUniqueName, seg.FromMap,
                StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        var alt = CompoundUtils.FindAdjacentWalkable(location, new Point(origX, origY));
        if (alt == null)
        {
            _monitor.Log(
                $"[GoToESM] FindAdjacentWalkable 无邻近可走 tile ({origX},{origY})",
                LogLevel.Debug);
            return false;
        }

        _activeAlternativeTile = (alt.Value.X, alt.Value.Y);
        _monitor.Log(
            $"[GoToESM] 激活替代 tile ({origX},{origY}) → ({alt.Value.X},{alt.Value.Y}) candIdx={_warpCandidateIndex}",
            LogLevel.Info);
        return true;
    }

    // ── SegmentRunning ─────────────────────────────────────────────────────

    private V2Result? TickSegmentRunning()
    {
        var seg = _segments[_segmentIndex];

        // DoorInteract 模式：走完后执行 InteractWithTile
        if (_doorInteractPending && seg.Type == SegmentType.Warp && seg.Mechanism == WarpMechanism.DoorInteract)
        {
            var l1Tick = _moveToV2.IsActive ? _moveToV2.Tick() : null;
            if (_moveToV2.IsActive)
            {
                if (l1Tick == null) return null; // 仍在走
                if (!l1Tick.Success)
                {
                    _doorInteractPending = false;
                    var (origX, origY) = GetCurrentWarpCandidate(seg);
                    // bug #5：异步行走失败时也尝试 FindAdjacentWalkable（如门前突现障碍）
                    if (TryActivateAlternativeTile(seg, origX, origY))
                    {
                        _state = State.SegmentStarting;
                        return null;
                    }
                    // bug #8：替代失败 → 切换到下一个候选
                    if (TryAdvanceWarpCandidate(seg))
                    {
                        _state = State.SegmentStarting;
                        return null;
                    }
                    _totalNavFailures++;
                    if (_totalNavFailures >= MaxNavFailures)
                        return FailResult(GoToErrors.TargetUnreachable, $"走到门前失败（候选已穷尽）");
                    // bug #3：候选耗尽 + 替代也失败 → 设 cooldown
                    _navRetryCooldownFrames = NavRetryCooldownFrames;
                    _state = State.SegmentStarting;
                    return null;
                }
            }

            // bug #12 修复：若刚才走的是替代 tile，再尝试一次走原门站立 tile，
            // 让玩家踩到 SourceX/SourceY 上后再 InteractWithTile（checkAction 才能
            // 在面朝方向 reach 真正的 doorPoint）。
            // 替代 tile 上调 InteractWithTile 等于在远处 checkAction，warp 不会触发。
            // 每个候选只 final-step 一次（_doorFinalStepRetried 防循环）。
            if (_activeAlternativeTile != null && !_doorFinalStepRetried)
            {
                _doorFinalStepRetried = true;
                var (origDoorX, origDoorY) = GetCurrentWarpCandidate(seg);
                _monitor.Log(
                    $"[GoToESM] DoorInteract final-step：替代 tile 已就位，重新 NavigateTo 原门 tile ({origDoorX},{origDoorY}) candIdx={_warpCandidateIndex}",
                    LogLevel.Info);
                _activeAlternativeTile = null; // 清空 → BuildMoveToCommand_A 用原 (FromX, FromY)
                var l1Cmd = BuildMoveToCommand_A(origDoorX, origDoorY);
                var startResult = _moveToV2.Start(l1Cmd);
                if (startResult != null && !startResult.Success)
                {
                    // 同步启动失败（NPC 仍占据原门 tile 等）→ 走失败链
                    _monitor.Log(
                        $"[GoToESM] DoorInteract final-step 同步启动失败 ({origDoorX},{origDoorY})",
                        LogLevel.Warn);
                    _doorInteractPending = false;
                    return HandleDoorInteractDistanceFailure(seg, origDoorX, origDoorY);
                }
                if (startResult == null)
                {
                    // 异步进行中 → 保持 _doorInteractPending=true，下一帧重入 TickSegmentRunning
                    // 会走 _moveToV2.Tick() 等待该次 NavigateTo 结果
                    return null;
                }
                // startResult.Success == true：同步成功（极少见，玩家本就在 origDoor 旁）
                // → 落到下方 InteractWithTile
            }

            // 到达门前，执行交互。
            // 注意：即便用了 alt tile 走到位（玩家实际站在 alt），InteractWithTile 仍传
            // 原候选 SourceX/SourceY 让玩家面朝门方向触发 checkAction，与 V2 现有语义保持一致。
            // bug #12 之后：正常路径下玩家此时已踩到原 (FromX, FromY)，InteractWithTile
            // 在自己脚下 face north → checkAction 触达真正 doorPoint。
            _doorInteractPending = false;
            var (doorX, doorY) = GetCurrentWarpCandidate(seg);

            // bug #11 修复：InteractWithTile 之前 verify 玩家是否真的站在门旁（曼哈顿距离 ≤ 1）。
            // 防御层：即便 L1 MoveToHandlerV2 漏报"被外部 cancel"返回了 Success，
            // 玩家实际仍在远处时不应盲目 InteractWithTile（必然 fail_no_response），
            // 否则会进入 WaitingForMapLoad → verify 失败 → 段失败链 → 重发 NavigateTo → StuckDetector
            // 再触发 → 死循环（实测从 5 PM 卡到 9 PM）。
            // 走失败链（与 bug #5/#8/#3 共用），让候选切换/替代 tile/cooldown 一起把玩家解套。
            var playerTile = Game1.player?.Tile;
            int doorDist = playerTile.HasValue
                ? Math.Abs((int)playerTile.Value.X - doorX) + Math.Abs((int)playerTile.Value.Y - doorY)
                : int.MaxValue;
            if (doorDist > 1)
            {
                _monitor.Log(
                    $"[GoToESM] DoorInteract 段尾 verify 失败：玩家 ({(int)(playerTile?.X ?? 0)},{(int)(playerTile?.Y ?? 0)}) 距门 ({doorX},{doorY}) 曼哈顿={doorDist} > 1，可能 NavigateTo 被 stuck-cancel，走失败链",
                    LogLevel.Warn);
                return HandleDoorInteractDistanceFailure(seg, doorX, doorY);
            }

            _executor.InteractWithTile(doorX, doorY);
            _warpsTraversed++;
            _waitFrames = 0;
            _raceGuardFrames = 0;
            _state = State.WaitingForMapLoad;
            return null;
        }

        // 普通 L1V2 轮询
        var result = _moveToV2.Tick();
        if (result == null) return null; // 仍在进行

        if (!result.Success)
        {
            _monitor.Log(
                $"[GoToESM] L1V2 失败 seg={_segmentIndex} candIdx={_warpCandidateIndex}",
                LogLevel.Warn);
            // bug #5：warp 段异步失败也尝试 FindAdjacentWalkable 找邻近替代
            if (seg.Type == SegmentType.Warp)
            {
                var (origX, origY) = GetCurrentWarpCandidate(seg);
                if (TryActivateAlternativeTile(seg, origX, origY))
                {
                    _state = State.SegmentStarting;
                    return null;
                }
                // bug #8：替代失败 → 切换候选
                if (TryAdvanceWarpCandidate(seg))
                {
                    _state = State.SegmentStarting;
                    return null;
                }
            }
            _totalNavFailures++;
            if (_totalNavFailures >= MaxNavFailures)
            {
                return FailResult(GoToErrors.TargetUnreachable,
                    $"目标物理不可达，已尝试 {_totalNavFailures} 次导航失败");
            }
            // bug #3：候选耗尽 + 替代也失败 → 设 cooldown 区分瞬时与永久不可达
            _navRetryCooldownFrames = NavRetryCooldownFrames;
            _state = State.SegmentStarting;
            return null;
        }

        // L1V2 成功
        if (seg.Type == SegmentType.Warp)
        {
            _warpsTraversed++;
            _waitFrames = 0;
            _raceGuardFrames = 0;
            _state = State.WaitingForMapLoad;
        }
        else
        {
            AdvanceToNextSegment();
        }

        return null;
    }

    // ── WaitingForMapLoad ──────────────────────────────────────────────────

    private V2Result? TickWaitingForMapLoad()
    {
        _waitFrames++;

        if (!Context.CanPlayerMove)
        {
            if (_waitFrames > WarpClosedTimeoutFrames)
            {
                string errorCode = GoToErrors.WarpClosed;
                string message = $"等待地图加载超时（{WarpClosedTimeoutFrames} 帧），warp 可能在已知锁定";
                return FailResult(errorCode, message);
            }
            return null;
        }

        // 地图加载完成，等待竞态保护帧
        _raceGuardFrames++;
        if (_raceGuardFrames < MapLoadRaceGuardFrames) return null;

        // bug #10 修复：段间推进前 verify currentLocation == seg.ToMap。
        // 防止 DoorInteract 失败（NavigateTo cancelled / InteractWithTile fail_no_response）
        // 或 EdgeWarp 异常情况（玩家被剧情/对话框送到别处）下 ESM 错误推进到下一段。
        // 若 warp 段实际未发生地图切换，不能 AdvanceToNextSegment，否则 Final IntraMap
        // 段会以为玩家在 ToMap 上，去寻路 ToMap 内部坐标，必然 NavigateTo 失败。
        var seg = _segments[_segmentIndex];
        if (!VerifySegmentArrival(seg))
        {
            return HandleSegmentArrivalFailure(seg);
        }

        // 准备 ReResolve（追踪模式）或推进下一段
        _state = State.ReResolving;
        return null;
    }

    /// <summary>
    /// bug #10 修复：校验当前 segment 是否真的推进到目标地图。
    /// IntraMap 段不需要校验（不涉及地图切换）。
    /// Warp 段必须校验 Game1.currentLocation.NameOrUniqueName 严格等于 seg.ToMap。
    /// </summary>
    private bool VerifySegmentArrival(PathSegment seg)
    {
        if (seg.Type != SegmentType.Warp) return true;
        var current = Game1.currentLocation?.NameOrUniqueName;
        if (string.IsNullOrEmpty(current)) return false;
        return string.Equals(current, seg.ToMap, StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>
    /// bug #10 修复：warp 段标称完成但 currentLocation 不是 seg.ToMap 时的回退。
    /// 复用 bug #5/#8/#3 的失败链：候选切换 → 替代 tile → cooldown → 累加 navFails。
    /// </summary>
    private V2Result? HandleSegmentArrivalFailure(PathSegment seg)
    {
        var actual = Game1.currentLocation?.NameOrUniqueName ?? "(null)";
        _monitor.Log(
            $"[GoToESM] 段间推进 verify 失败: 期望 ToMap={seg.ToMap} 实际={actual} candIdx={_warpCandidateIndex} altTried={_currentCandidateAlternativeTried}",
            LogLevel.Warn);

        // 重置 warp 段流转状态（_doorInteractPending 已在 SegmentRunning 路径清掉，但稳妥起见再清一次）
        _doorInteractPending = false;

        var (curFromX, curFromY) = _activeAlternativeTile ?? GetCurrentWarpCandidate(seg);

        // 仅当玩家仍在 fromMap 上才能尝试替代/候选；若玩家被送到第三方地图（剧情、对话框送回家等）
        // 则替代/候选都无意义，直接累加 navFails 走 NoRoute / TargetUnreachable 路径。
        bool stillOnFromMap = string.Equals(
            Game1.currentLocation?.NameOrUniqueName, seg.FromMap,
            StringComparison.OrdinalIgnoreCase);

        if (stillOnFromMap)
        {
            // bug #5：先尝试 FindAdjacentWalkable 找邻近替代 tile
            if (TryActivateAlternativeTile(seg, curFromX, curFromY))
            {
                _state = State.SegmentStarting;
                return null;
            }
            // bug #8：替代失败 → 切换候选 warp tile
            if (TryAdvanceWarpCandidate(seg))
            {
                _state = State.SegmentStarting;
                return null;
            }
        }

        _totalNavFailures++;
        if (_totalNavFailures >= MaxNavFailures)
        {
            return FailResult(GoToErrors.TargetUnreachable,
                $"warp 段未真正切换到 {seg.ToMap}（实际位于 {actual}），累计 {_totalNavFailures} 次失败");
        }
        // bug #3：候选耗尽 + 替代也失败 → 设 cooldown
        _navRetryCooldownFrames = NavRetryCooldownFrames;
        _state = State.SegmentStarting;
        return null;
    }

    /// <summary>
    /// bug #11 修复：DoorInteract 段尾"L1 报 Success 但玩家不在门旁"的回退处理。
    /// 复用 bug #5/#8/#3 的失败链：候选切换 → 替代 tile → cooldown → 累加 navFails。
    /// 与 HandleSegmentArrivalFailure 的区别：此时 _waitFrames/_raceGuardFrames 还没启动，
    /// 玩家还在 fromMap 上（尚未 InteractWithTile），无需检查 currentLocation。
    /// </summary>
    private V2Result? HandleDoorInteractDistanceFailure(PathSegment seg, int doorX, int doorY)
    {
        // bug #5：先尝试 FindAdjacentWalkable 找邻近替代 tile（门前突现障碍 / NPC 占据）
        if (TryActivateAlternativeTile(seg, doorX, doorY))
        {
            _state = State.SegmentStarting;
            return null;
        }
        // bug #8：替代失败 → 切换到下一个候选 warp tile
        if (TryAdvanceWarpCandidate(seg))
        {
            _state = State.SegmentStarting;
            return null;
        }
        _totalNavFailures++;
        _monitor.Log(
            $"[GoToESM] DoorInteract 距离 verify 候选耗尽 navFails={_totalNavFailures}",
            LogLevel.Warn);
        if (_totalNavFailures >= MaxNavFailures)
        {
            return FailResult(GoToErrors.TargetUnreachable,
                $"无法走到门前 ({doorX},{doorY})（候选已穷尽 + L1 反复被 stuck-cancel）");
        }
        // bug #3：候选耗尽 + 替代也失败 → 设 cooldown，给 NPC/障碍让路时间
        _navRetryCooldownFrames = NavRetryCooldownFrames;
        _state = State.SegmentStarting;
        return null;
    }

    // ── ReResolving ────────────────────────────────────────────────────────

    private V2Result? TickReResolving()
    {
        var seg = _segments[_segmentIndex];

        if (_resolvedTarget.IsTrackingEntity && _resolvedTarget.TrackingRef != null)
        {
            // ReResolve 用用户原输入（"Lewis" / "[animal:甜豆#abc]"），而非内部 TrackingRef
            // ("Farm:npc_Lewis"）。Resolver 8 级路径不识别 TrackingRef 格式，会必然失败。
            var newResolved = GoToTargetResolver.Resolve(_resolvedTarget.RawInput, null, _monitor);
            if (!newResolved.IsSuccess)
            {
                return FailResult(GoToErrors.TrackingLost,
                    $"追踪目标 '{_rawTarget}' 已消失，无法继续导航");
            }

            _reResolveCount++;
            bool mapChanged = !string.Equals(
                newResolved.MapName, _resolvedTarget.MapName,
                StringComparison.OrdinalIgnoreCase);

            float driftX = Math.Abs(newResolved.TileX - _resolvedTarget.TileX);
            float driftY = Math.Abs(newResolved.TileY - _resolvedTarget.TileY);
            bool largeCoordDrift = (driftX + driftY) > 10;

            _resolvedTarget = newResolved;

            if (mapChanged || largeCoordDrift)
            {
                _reRouteCount++;
                if (_reRouteCount > MaxReRouteCount)
                {
                    return FailResult(GoToErrors.NoRoute,
                        $"重规划次数超限（>{MaxReRouteCount}）");
                }
                _state = State.ReRouting;
                return null;
            }
        }

        AdvanceToNextSegment();
        return null;
    }

    // ── ReRouting ──────────────────────────────────────────────────────────

    private V2Result? TickReRouting()
    {
        string currentMap = Game1.currentLocation?.NameOrUniqueName ?? "";
        int fromX = (int)Game1.player.Tile.X;
        int fromY = (int)Game1.player.Tile.Y;

        var request = new RouteRequest(currentMap, fromX, fromY, _resolvedTarget);
        var routeResult = GoToRoutePlanner.PlanRoute(request, _monitor);

        if (!routeResult.Success)
        {
            return FailResult(GoToErrors.NoRoute,
                routeResult.ErrorMessage ?? $"重规划失败：{currentMap} → {_resolvedTarget.MapName}");
        }

        _segments = routeResult.Segments.ToArray();
        _segmentIndex = 0;
        _state = State.SegmentStarting;
        return null;
    }

    // ── 工具方法 ────────────────────────────────────────────────────────────

    private void AdvanceToNextSegment()
    {
        _segmentIndex++;
        _warpCandidateIndex = 0; // bug #8：进入新 segment 时重置候选索引
        _currentCandidateAlternativeTried = false; // bug #5：新 segment 重置替代标记
        _activeAlternativeTile = null;
        _doorFinalStepRetried = false; // bug #12：新 segment 重置 final-step 标记
        _navRetryCooldownFrames = 0; // bug #3：新 segment 清除 cooldown
        if (_segmentIndex >= _segments.Length)
            _state = State.Completed;
        else
            _state = State.SegmentStarting;
    }

    private string? FindBestAdjacentSide(int targetX, int targetY)
    {
        var location = Game1.currentLocation;
        if (location == null) return null;

        var adj = CompoundUtils.FindAdjacentWalkable(location, new Point(targetX, targetY));
        if (adj == null) return null;

        int dx = adj.Value.X - targetX;
        int dy = adj.Value.Y - targetY;

        if (dy < 0) return "north";
        if (dy > 0) return "south";
        if (dx > 0) return "east";
        return "west";
    }

    private string InferDirectionFromSegment(PathSegment seg)
    {
        var mapEntry = MapData.GetMap(seg.FromMap);
        int mapWidth = mapEntry?.Width ?? 0;
        int mapHeight = mapEntry?.Height ?? 0;

        if (mapWidth > 0 && mapHeight > 0)
        {
            if (seg.FromY <= 1) return "north";
            if (seg.FromY >= mapHeight - 2) return "south";
            if (seg.FromX <= 1) return "west";
            if (seg.FromX >= mapWidth - 2) return "east";
        }

        return "south";
    }

    private void CleanupMovement()
    {
        if (_moveToV2.IsActive) _moveToV2.Cancel();
        var player = Game1.player;
        if (player != null)
        {
            player.controller = null;
            player.movementDirections.Clear();
            player.Halt();
        }
    }

    // ── L1V2 命令构建 ────────────────────────────────────────────────────────

    private V2Command BuildMoveToCommand_A(int x, int y, string? faceDirection = null)
    {
        var p = new Dictionary<string, object>
        {
            ["x"] = x,
            ["y"] = y,
            ["timeout_frames"] = 1800,
        };
        if (faceDirection != null) p["face_direction"] = faceDirection;
        return new V2Command
        {
            Seq = _cmd.Seq,
            ActionId = _cmd.ActionId,
            ProtocolVersion = "2.0",
            Capability = "move_to",
            Params = p,
        };
    }

    private V2Command BuildMoveToCommand_B(int x, int y, string direction)
    {
        return new V2Command
        {
            Seq = _cmd.Seq,
            ActionId = _cmd.ActionId,
            ProtocolVersion = "2.0",
            Capability = "move_to",
            Params = new Dictionary<string, object>
            {
                ["x"] = x,
                ["y"] = y,
                ["direction"] = direction,
                ["timeout_frames"] = 1800,
            },
        };
    }

    private V2Command BuildMoveToCommand_C(int x, int y, string adjacentSide)
    {
        return new V2Command
        {
            Seq = _cmd.Seq,
            ActionId = _cmd.ActionId,
            ProtocolVersion = "2.0",
            Capability = "move_to",
            Params = new Dictionary<string, object>
            {
                ["x"] = x,
                ["y"] = y,
                ["adjacent_side"] = adjacentSide,
                ["timeout_frames"] = 1800,
            },
        };
    }

    // ── 结果构建 ─────────────────────────────────────────────────────────────

    private V2Result? BuildSuccessResult()
    {
        FaceTrackingTarget();

        var player = Game1.player;
        var location = Game1.currentLocation;
        string facing = FacingDirectionToString(player?.FacingDirection ?? 2);

        var data = new Dictionary<string, object>
        {
            ["final_location"] = location?.NameOrUniqueName ?? "Unknown",
            ["final_tile"] = new Dictionary<string, object>
            {
                ["x"] = (int)(player?.Tile.X ?? 0),
                ["y"] = (int)(player?.Tile.Y ?? 0),
            },
            ["facing"] = facing,
            ["route_segments"] = _segments.Length,
            ["warps_traversed"] = _warpsTraversed,
            ["re_resolve_count"] = _reResolveCount,
            ["re_route_count"] = _reRouteCount,
            ["segments"] = BuildSegmentDescriptions(),
        };

        string obs = $"已到达 {location?.NameOrUniqueName ?? "Unknown"} ({(int)(player?.Tile.X ?? 0)},{(int)(player?.Tile.Y ?? 0)})，经过 {_warpsTraversed} 次 warp";
        var result = ResultBuilder.Success(_cmd.Seq, _cmd.ActionId, data).WithObservation(obs);
        Reset();
        return result;
    }

    private V2Result FailResult(string errorCode, string message)
    {
        var player = Game1.player;
        var location = Game1.currentLocation;
        var data = new Dictionary<string, object>
        {
            ["target"] = _rawTarget,
            ["reason"] = errorCode,
            ["last_location"] = location?.NameOrUniqueName ?? "Unknown",
            ["last_tile"] = new Dictionary<string, object>
            {
                ["x"] = (int)(player?.Tile.X ?? 0),
                ["y"] = (int)(player?.Tile.Y ?? 0),
            },
            ["nav_failure_count"] = _totalNavFailures,
        };

        CleanupMovement();
        var result = ResultBuilder.Failure(_cmd.Seq, _cmd.ActionId, errorCode, message);
        result.Data = data;
        Reset();
        return result;
    }

    private V2Result BuildPartialResult(string reason)
    {
        var player = Game1.player;
        var location = Game1.currentLocation;
        return ResultBuilder.Partial(_cmd.Seq, _cmd.ActionId, new Dictionary<string, object>
        {
            ["target"] = _rawTarget,
            ["reason"] = reason,
            ["last_location"] = location?.NameOrUniqueName ?? "Unknown",
            ["last_tile"] = new Dictionary<string, object>
            {
                ["x"] = (int)(player?.Tile.X ?? 0),
                ["y"] = (int)(player?.Tile.Y ?? 0),
            },
            ["warps_traversed"] = _warpsTraversed,
        }).WithObservation($"go_to 被取消，state={_state}");
    }

    private void FaceTrackingTarget()
    {
        if (!_resolvedTarget.IsTrackingEntity) return;
        var resolved = GoToTargetResolver.Resolve(_resolvedTarget.TrackingRef ?? _rawTarget, null, _monitor);
        if (!resolved.IsSuccess) return;

        var player = Game1.player;
        if (player == null) return;

        float dx = resolved.TileX - player.Tile.X;
        float dy = resolved.TileY - player.Tile.Y;
        int dir = Math.Abs(dx) > Math.Abs(dy)
            ? (dx > 0 ? 1 : 3)
            : (dy > 0 ? 2 : 0);
        player.faceDirection(dir);
    }

    private List<string> BuildSegmentDescriptions()
    {
        var descs = new List<string>();
        foreach (var seg in _segments)
        {
            string desc = seg.Type == SegmentType.Warp
                ? $"{seg.FromMap}→{seg.ToMap}"
                : $"{seg.FromMap} (final)";
            descs.Add(desc);
        }
        return descs;
    }

    private static string FacingDirectionToString(int dir)
    {
        return dir switch
        {
            0 => "north",
            1 => "east",
            2 => "south",
            3 => "west",
            _ => "south",
        };
    }

    private void Reset()
    {
        _state = State.Idle;
        _segments = Array.Empty<PathSegment>();
        _segmentIndex = 0;
        _totalNavFailures = 0;
        _reRouteCount = 0;
        _reResolveCount = 0;
        _warpsTraversed = 0;
        _waitFrames = 0;
        _elapsedFrames = 0;
        _doorInteractPending = false;
        _raceGuardFrames = 0;
        _warpCandidateIndex = 0;
        _currentCandidateAlternativeTried = false;
        _activeAlternativeTile = null;
        _doorFinalStepRetried = false;
        _navRetryCooldownFrames = 0;
    }
}
