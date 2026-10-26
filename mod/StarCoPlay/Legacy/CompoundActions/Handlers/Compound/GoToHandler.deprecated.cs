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
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// L2 语义导航引擎 — go_to capability handler（重设计版）。
///
/// 三子系统串联：
///   GoToTargetResolver   — 语义解析（8 级优先级）
///   GoToRoutePlanner     — BFS 路由规划（纯计算）
///   GoToExecutorStateMachine — 7 态状态机执行器（L1V2 三模式）
///
/// 此类只做编排：Start→Resolve→Plan→Execute；
/// 不含路由逻辑、warp 逻辑、追踪逻辑（全在子系统中）。
/// </summary>
public class GoToHandler : ICompoundHandler
{
    private readonly IMonitor _monitor;
    private readonly GoToExecutorStateMachine _stateMachine;

    private V2Command? _cmd;
    private string _rawTarget = "";

    /// <inheritdoc />
    public bool IsActive => _stateMachine.IsActive;

    public GoToHandler(IMoveToHandler moveToV2, ActionExecutor executor, IMonitor monitor)
    {
        _monitor = monitor;
        _stateMachine = new GoToExecutorStateMachine(moveToV2, executor, monitor);
    }

    // ════════════════════════════════════════════════════════
    //  ICompoundHandler 接口实现
    // ════════════════════════════════════════════════════════

    /// <inheritdoc />
    public V2Result? Start(V2Command cmd)
    {
        _cmd = cmd;

        if (!CompoundUtils.TryGetString(cmd.Params, "target", out var target) ||
            string.IsNullOrWhiteSpace(target))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "go_to 需要 target 参数");
        }

        _rawTarget = target;

        // ── Step 1: 语义解析 ──────────────────────────────
        var resolved = GoToTargetResolver.Resolve(target, null, _monitor);
        if (!resolved.IsSuccess)
        {
            string errorCode = MapResolveErrorToResultError(resolved.ErrorCode);
            string message = resolved.ErrorMessage ?? $"无法解析目标: {target}";
            var failResult = ResultBuilder.Failure(cmd.Seq, cmd.ActionId, errorCode, message);
            if (resolved.Suggestions != null && resolved.Suggestions.Count > 0)
            {
                failResult.Data = new Dictionary<string, object>
                {
                    ["suggestions"] = resolved.Suggestions,
                    ["target"] = target,
                };
            }
            return failResult;
        }

        // ── 快路径：Player 类型（当前玩家已在场）直接完成 ──
        if (resolved.Kind == TargetKind.Player)
        {
            var loc = Game1.currentLocation;
            return ResultBuilder.Success(cmd.Seq, cmd.ActionId, new Dictionary<string, object>
            {
                ["final_location"] = loc?.NameOrUniqueName ?? "Unknown",
                ["final_tile"] = new Dictionary<string, object>
                {
                    ["x"] = (int)(Game1.player?.Tile.X ?? 0),
                    ["y"] = (int)(Game1.player?.Tile.Y ?? 0),
                },
                ["facing"] = FacingDirectionToString(Game1.player?.FacingDirection ?? 2),
                ["route_segments"] = 0,
                ["warps_traversed"] = 0,
                ["re_resolve_count"] = 0,
                ["re_route_count"] = 0,
                ["segments"] = new List<string>(),
            }).WithObservation($"已在玩家位置，无需移动");
        }

        // ── Step 2: 路由规划 ──────────────────────────────
        string fromMap = Game1.currentLocation?.NameOrUniqueName ?? "";
        int fromX = (int)(Game1.player?.Tile.X ?? 0);
        int fromY = (int)(Game1.player?.Tile.Y ?? 0);

        var request = new RouteRequest(fromMap, fromX, fromY, resolved);
        var routeResult = GoToRoutePlanner.PlanRoute(request, _monitor);

        if (!routeResult.Success)
        {
            string routeErrorCode = routeResult.ErrorCode ?? GoToErrors.NoRoute;
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                routeErrorCode,
                routeResult.ErrorMessage ?? $"无法规划路由: {fromMap} → {resolved.MapName}");
        }

        // ── Step 3: 执行器启动 ────────────────────────────
        _stateMachine.Start(routeResult.Segments.ToArray(), resolved, _rawTarget, cmd);

        // 如果路径为空且没有立即完成（只有 Player 会是空路径）则说明已在目标地图
        // StateMachine 的 Completed 状态会在下一 Tick 返回结果
        return null;
    }

    /// <inheritdoc />
    public V2Result? Tick()
    {
        if (_cmd == null || !_stateMachine.IsActive)
            return null;

        return _stateMachine.Tick();
    }

    /// <inheritdoc />
    public V2Result Cancel()
    {
        if (_stateMachine.IsActive)
        {
            return _stateMachine.Cancel();
        }

        return ResultBuilder.Partial(_cmd?.Seq ?? 0, _cmd?.ActionId ?? "", new Dictionary<string, object>
        {
            ["target"] = _rawTarget,
            ["reason"] = "cancelled",
        }).WithObservation("go_to 被取消（状态机未激活）");
    }

    // ════════════════════════════════════════════════════════
    //  工具方法
    // ════════════════════════════════════════════════════════

    private static string MapResolveErrorToResultError(ResolveErrorCode? code)
    {
        return code switch
        {
            ResolveErrorCode.AmbiguousAnimal => GoToErrors.AmbiguousAnimal,
            ResolveErrorCode.AmbiguousBuilding => GoToErrors.AmbiguousBuilding,
            ResolveErrorCode.TrackingLost => GoToErrors.TrackingLost,
            _ => GoToErrors.TargetNotFound,
        };
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
}
