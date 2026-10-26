// ============================================================================
// DEPRECATED — go_to L2 重设计旧方案（已弃用,2026-05-03）
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

namespace StarCoPlay.V2.Compound;

/// <summary>
/// 纯计算型路径规划，接收 RouteRequest 输出 PathSegment[]。
/// 不感知游戏帧，不驱动任何移动 API。可被多次调用（ReRouting 场景）。
/// </summary>
public static class GoToRoutePlanner
{
    public static RouteResult PlanRoute(RouteRequest request, IMonitor? monitor = null)
    {
        var target = request.Target;
        string fromMap = request.FromMap;
        string targetMap = target.MapName;

        // Player 类型：立即完成（不需要任何段）
        if (target.Kind == TargetKind.Player)
        {
            return new RouteResult { Success = true, Segments = new List<PathSegment>() };
        }

        // 同地图快路径
        if (string.Equals(fromMap, targetMap, StringComparison.OrdinalIgnoreCase))
        {
            var seg = BuildFinalIntraMapSegment(request.FromX, request.FromY, target, fromMap);
            var segs = new List<PathSegment>();
            if (seg != null) segs.Add(seg);
            return new RouteResult { Success = true, Segments = segs };
        }

        // 跨地图：BFS 规划
        var route = MapData.FindRoute(fromMap, targetMap);
        if (route.Count == 0)
        {
            monitor?.Log($"[GoToRoutePlanner] BFS 路径为空，尝试重建 MapData: {fromMap} → {targetMap}", LogLevel.Info);
            MapData.BuildFromGameData(monitor);
            route = MapData.FindRoute(fromMap, targetMap);
        }

        if (route.Count == 0)
        {
            monitor?.Log($"[GoToRoutePlanner] NO_ROUTE: {fromMap} → {targetMap}", LogLevel.Warn);
            return new RouteResult
            {
                Success = false,
                ErrorCode = GoToErrors.NoRoute,
                ErrorMessage = $"从 {fromMap} 到 {targetMap} 未找到可用路径",
            };
        }

        // 切分：Warp 段序列 + 最终 IntraMap 段
        var segments = new List<PathSegment>();
        string currentMap = fromMap;
        int currentX = request.FromX;
        int currentY = request.FromY;

        foreach (var exit in route)
        {
            var warpSeg = BuildWarpSegment(currentMap, exit, currentX, currentY, monitor);
            segments.Add(warpSeg);
            currentMap = exit.TargetMap;
            currentX = exit.TargetX;
            currentY = exit.TargetY;
        }

        // 最终段（在目标地图内走向目标）
        var finalSeg = BuildFinalIntraMapSegment(currentX, currentY, target, currentMap);
        if (finalSeg != null)
            segments.Add(finalSeg);

        monitor?.Log(
            $"[GoToRoutePlanner] 规划完成: {fromMap} → {targetMap}，共 {segments.Count} 段 ({route.Count} warp + {(finalSeg != null ? 1 : 0)} final)",
            LogLevel.Debug);

        return new RouteResult { Success = true, Segments = segments };
    }

    // ── Warp 段构建 ──────────────────────────────────────────────────────────

    private static PathSegment BuildWarpSegment(
        string fromMap, MapData.MapExit exit,
        int fromX, int fromY,
        IMonitor? monitor)
    {
        var mapEntry = MapData.GetMap(fromMap);
        int mapWidth = mapEntry?.Width ?? 0;
        int mapHeight = mapEntry?.Height ?? 0;

        WarpMechanism mechanism;
        string? direction = null;

        if (IsEdgeWarp(exit.OriginalWarpX, exit.OriginalWarpY, mapWidth, mapHeight))
        {
            mechanism = WarpMechanism.EdgeWarp;
            direction = InferEdgeDirection(exit.OriginalWarpX, exit.OriginalWarpY, mapWidth, mapHeight);
            monitor?.Log(
                $"[GoToRoutePlanner] EdgeWarp {fromMap}({exit.SourceX},{exit.SourceY}) dir={direction} → {exit.TargetMap}",
                LogLevel.Debug);
        }
        else
        {
            mechanism = WarpMechanism.DoorInteract;
            monitor?.Log(
                $"[GoToRoutePlanner] DoorInteract {fromMap}({exit.SourceX},{exit.SourceY}) → {exit.TargetMap}",
                LogLevel.Debug);
        }

        // bug #8 修复：从 MapData 收集所有候选 warp tile，ESM 启动失败时可按顺序回退。
        // 仅 EdgeWarp 收集多候选（DoorInteract 一个建筑只有一个门入口）。
        IReadOnlyList<(int X, int Y)>? candidates = null;
        if (mechanism == WarpMechanism.EdgeWarp)
        {
            candidates = CollectEdgeWarpCandidates(fromMap, exit, mapWidth, mapHeight, monitor);
        }

        // 首选 tile = 候选列表第一个（若有），否则用 BFS 给的 exit
        int chosenFromX = exit.SourceX;
        int chosenFromY = exit.SourceY;
        if (candidates != null && candidates.Count > 0)
        {
            chosenFromX = candidates[0].X;
            chosenFromY = candidates[0].Y;
        }

        return new PathSegment(
            Type: SegmentType.Warp,
            FromMap: fromMap,
            FromX: chosenFromX,
            FromY: chosenFromY,
            ToMap: exit.TargetMap,
            ToX: exit.TargetX,
            ToY: exit.TargetY,
            Mechanism: mechanism,
            FinalHint: null,
            WarpCandidates: candidates
        );
    }

    /// <summary>
    /// 收集 fromMap → exit.TargetMap 的所有边缘 warp tile 候选。
    /// 同一边缘可能有多个 warp tile（如 Farm 北边整条边界），返回各自调整后的 SourceX/SourceY。
    /// 候选顺序：BFS 给的代表 exit 排第一（保持原首选行为），其余按 MapData 注册顺序追加。
    /// 若仅单候选返回 null（无需回退逻辑）。
    /// </summary>
    private static IReadOnlyList<(int X, int Y)>? CollectEdgeWarpCandidates(
        string fromMap, MapData.MapExit primaryExit, int mapWidth, int mapHeight, IMonitor? monitor)
    {
        var allExits = MapData.FindExits(fromMap, primaryExit.TargetMap);
        // 仅保留同样是边缘机制的候选（避免门 + 边缘混入）
        var edgeCandidates = allExits
            .Where(e => IsEdgeWarp(e.OriginalWarpX, e.OriginalWarpY, mapWidth, mapHeight))
            .ToList();

        if (edgeCandidates.Count <= 1) return null;

        // 把 primaryExit 排在第一位（保持现有首选行为），其余按注册顺序追加
        var ordered = new List<(int X, int Y)>
        {
            (primaryExit.SourceX, primaryExit.SourceY),
        };
        foreach (var e in edgeCandidates)
        {
            if (e.OriginalWarpX == primaryExit.OriginalWarpX
                && e.OriginalWarpY == primaryExit.OriginalWarpY)
                continue; // 跳过首选自身
            ordered.Add((e.SourceX, e.SourceY));
        }

        monitor?.Log(
            $"[GoToRoutePlanner] EdgeWarp 多候选 {fromMap}→{primaryExit.TargetMap}: {ordered.Count} 个 tile",
            LogLevel.Debug);
        return ordered;
    }

    private static bool IsEdgeWarp(int warpX, int warpY, int mapWidth, int mapHeight)
    {
        if (mapWidth <= 0 || mapHeight <= 0) return false;
        return warpX <= 1 || warpX >= mapWidth - 2
            || warpY <= 1 || warpY >= mapHeight - 2;
    }

    private static string InferEdgeDirection(int warpX, int warpY, int mapWidth, int mapHeight)
    {
        if (warpY <= 0) return "north";
        if (warpY >= mapHeight - 1) return "south";
        if (warpX <= 0) return "west";
        return "east";
    }

    // ── 最终 IntraMap 段构建 ─────────────────────────────────────────────────

    private static PathSegment? BuildFinalIntraMapSegment(
        int fromX, int fromY, ResolvedTarget target, string mapName)
    {
        if (!target.HasPreciseCoord && !target.IsTrackingEntity)
        {
            // Map 类型且无精确坐标 → 到达地图即完成，不需要最终段
            return null;
        }

        bool useAdjacent = target.IsTrackingEntity;
        FinalTargetHint? hint = null;

        if (useAdjacent)
        {
            hint = new FinalTargetHint(
                UseAdjacentSide: true,
                AdjacentSide: target.PreferredSide,
                FaceDirection: target.FaceDirection
            );
        }
        else if (target.HasPreciseCoord)
        {
            hint = new FinalTargetHint(
                UseAdjacentSide: false,
                AdjacentSide: null,
                FaceDirection: target.FaceDirection
            );
        }

        return new PathSegment(
            Type: SegmentType.IntraMap,
            FromMap: mapName,
            FromX: fromX,
            FromY: fromY,
            ToMap: mapName,
            ToX: target.TileX,
            ToY: target.TileY,
            Mechanism: null,
            FinalHint: hint
        );
    }
}
