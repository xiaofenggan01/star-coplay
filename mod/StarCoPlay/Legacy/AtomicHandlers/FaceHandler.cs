// ============================================================
// [LEGACY] 旧版转向实现 — 已被当前 face 执行原子替代
// 保留原因：UseInputBridgeHandlers=false 时的 fallback
// 计划：protocol-capability-completion 迭代中评估清理
// ============================================================
using System;
using System.Collections.Generic;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2;

/// <summary>
/// 处理 face(direction) 能力：转向指定方向。
/// </summary>
public class FaceHandler
{
    private readonly IMonitor _monitor;

    /// <summary>方向名称到游戏方向值的映射：0=上 1=右 2=下 3=左。</summary>
    private static readonly Dictionary<string, int> DirectionMap = new(StringComparer.OrdinalIgnoreCase)
    {
        { "up", 0 }, { "north", 0 },
        { "right", 1 }, { "east", 1 },
        { "down", 2 }, { "south", 2 },
        { "left", 3 }, { "west", 3 },
    };

    public FaceHandler(IMonitor monitor)
    {
        _monitor = monitor;
    }

    public V2Result? Handle(V2Command cmd)
    {
        if (!TryGetString(cmd.Params, "direction", out var direction))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "face 缺少 direction 参数");
        }

        if (!DirectionMap.TryGetValue(direction, out var directionInt))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                $"不支持的方向: {direction}，可选: up/down/left/right (north/south/west/east)");
        }

        var player = Game1.player;
        player.faceDirection(directionInt);
        player.FarmerSprite.StopAnimation();

        _monitor.Log($"[V2] face: 已转向 {direction} ({directionInt})", LogLevel.Trace);

        return ResultBuilder.Success(
                cmd.Seq, cmd.ActionId,
                new { direction, direction_int = directionInt })
            .WithObservation($"已转向 {direction}");
    }

    private static bool TryGetString(Dictionary<string, object> p, string key, out string value)
    {
        value = "";
        if (!p.TryGetValue(key, out var raw)) return false;
        value = raw?.ToString() ?? "";
        return !string.IsNullOrEmpty(value);
    }
}
