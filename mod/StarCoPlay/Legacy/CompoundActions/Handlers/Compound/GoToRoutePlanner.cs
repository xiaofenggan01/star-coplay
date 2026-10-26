using System;
using System.Collections.Generic;
using StardewModdingAPI;
using TilePoint = StarCoPlay.TilePoint;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// 纯计算 BFS 路由规划器（无状态，无副作用）。
///
/// 关键设计（2026-05-04 重设计）：
///   段类型不再使用 IsEdgeTile 启发式判位置，而是**按 MapExit.Kind 直接派发**：
///     WalkThrough  → SegmentType.EdgeWarp     （走过 warp tile + 按方向键）
///     InteractDoor → SegmentType.DoorInteract （走到旁 + interact）
///   方向来源：`MapExit.PressDirection`（已含三级查找：配置/白名单/启发式）
///   多候选位点：完整 MapExit 引用塞进 PathSegment.Exit，由 GoToHandler 决定单 tile 还是轮询
/// </summary>
public static class GoToRoutePlanner
{
    public static List<PathSegment>? Plan(
        string fromMap, int fromX, int fromY,
        string toMap, int toX, int toY,
        IMonitor? monitor = null)
    {
        // ── 1. 同地图快路径 ───────────────────────────────────────────
        if (string.Equals(fromMap, toMap, StringComparison.OrdinalIgnoreCase))
        {
            monitor?.Log($"[RoutePlanner] 同地图: {fromMap} ({fromX},{fromY}) → ({toX},{toY})", LogLevel.Trace);
            return new List<PathSegment>
            {
                new PathSegment(
                    SegmentType.IntraMap,
                    fromMap, toMap,
                    fromX, fromY, toX, toY,
                    null, null, null, null)
            };
        }

        // ── 2. 跨地图：BFS 路由 ───────────────────────────────────────
        var route = StarCoPlay.MapData.FindRoute(fromMap, toMap);
        if (route == null || route.Count == 0)
        {
            monitor?.Log($"[RoutePlanner] 无路径: {fromMap} → {toMap}", LogLevel.Warn);
            return null;
        }

        monitor?.Log($"[RoutePlanner] 路由链长度={route.Count}: {fromMap} → {toMap}", LogLevel.Trace);

        // ── 3. 为每跳生成（IntraMap → 出口段） ───────────────────────
        var segments = new List<PathSegment>();
        int currentX = fromX;
        int currentY = fromY;
        string currentMap = fromMap;

        for (int i = 0; i < route.Count; i++)
        {
            var exit = route[i];
            var (entryTile, segType, dirStr) = PrepareExitSegment(exit, currentMap);

            // 3a. WalkThrough 才前置 IntraMap 走到 walk-through tile（玩家可停留位置）。
            //     DoorInteract 段省略前置 IntraMap：Door tile 本身不可寻路（PFC 拒绝），
            //     由 DoorInteract Mode C 内部用 adjacent_side 自行寻路 door 旁可走 tile。
            if (segType == SegmentType.EdgeWarp)
            {
                segments.Add(new PathSegment(
                    SegmentType.IntraMap,
                    currentMap, currentMap,
                    currentX, currentY,
                    entryTile.X, entryTile.Y,
                    null, null, null, null));
            }

            // 3b. 出口段：直接传 MapExit 引用，类型由 Kind 决定
            segments.Add(new PathSegment(
                segType,
                currentMap, exit.TargetMap,
                entryTile.X, entryTile.Y,
                exit.TargetX, exit.TargetY,
                dirStr,
                entryTile.X, entryTile.Y,
                exit));

            currentMap = exit.TargetMap;
            currentX = exit.TargetX;
            currentY = exit.TargetY;
        }

        // ── 4. 末段：在 toMap 内移动到目标 ───────────────────────────
        segments.Add(new PathSegment(
            SegmentType.IntraMap,
            toMap, toMap,
            currentX, currentY,
            toX, toY,
            null, null, null, null));

        monitor?.Log($"[RoutePlanner] 生成段数={segments.Count}", LogLevel.Trace);
        return segments;
    }

    /// <summary>
    /// 根据 MapExit.Kind 决定段类型 + 选取代表性入口 tile + 方向字符串。
    /// - WalkThrough → EdgeWarp；入口 tile = WarpTiles[0]，**仅当超出地图绝对边界**才内移 1 格；
    ///   玩家先走到 entryTile，段 EdgeWarp Mode B 检测已在终点时跳过 NavigateTo 直接 hold direction 触发。
    /// - InteractDoor → DoorInteract；入口 tile = DoorTiles[0]（Mode C 自己处理 adjacent）
    ///
    /// 边界内移规则（仅 WalkThrough 适用）：
    ///   warp.X &lt; 0      → 0
    ///   warp.X &gt;= Width → Width - 1
    ///   warp.Y 同理
    /// 内陆 warp tile（如 BusStop 道路 (44,22)）不做调整，保持原坐标。
    /// </summary>
    private static (TilePoint entryTile, SegmentType type, string? direction) PrepareExitSegment(
        StarCoPlay.MapExit exit, string fromMap)
    {
        if (exit.Kind == StarCoPlay.WarpKind.WalkThrough)
        {
            var raw = exit.WarpTiles.Count > 0 ? exit.WarpTiles[0] : new TilePoint(exit.TargetX, exit.TargetY);
            var tile = ClampToMapBounds(raw, fromMap);
            string? dir = exit.PressDirection.HasValue
                ? exit.PressDirection.Value.ToString().ToLowerInvariant()
                : null;
            return (tile, SegmentType.EdgeWarp, dir);
        }
        // InteractDoor — DoorTiles 是真实 door tile（玩家不需走到上面，由 Mode C 处理 adjacent）
        var doorTile = exit.DoorTiles.Count > 0 ? exit.DoorTiles[0] : new TilePoint(exit.TargetX, exit.TargetY);
        return (doorTile, SegmentType.DoorInteract, null);
    }

    /// <summary>仅当 warp tile 超出地图绝对边界时夹回边界内（X 0..Width-1, Y 0..Height-1）。</summary>
    private static TilePoint ClampToMapBounds(TilePoint p, string mapName)
    {
        var mi = StarCoPlay.MapData.GetMap(mapName);
        if (mi == null || mi.Width <= 0 || mi.Height <= 0) return p;
        int x = p.X, y = p.Y;
        if (x < 0) x = 0;
        else if (x >= mi.Width) x = mi.Width - 1;
        if (y < 0) y = 0;
        else if (y >= mi.Height) y = mi.Height - 1;
        return new TilePoint(x, y);
    }
}
