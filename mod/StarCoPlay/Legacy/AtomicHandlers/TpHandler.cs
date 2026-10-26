using System.Collections.Generic;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2;

/// <summary>
/// tp 指令处理器 — 瞬移到指定地图位置。
/// 用于 move_to 无法到达（NO_PATH）时的后备传送手段。
/// 使用 ActionExecutor.WarpTo（底层调用 Game1.warpFarmer）实现，在游戏主线程同步执行。
/// </summary>
public sealed class TpHandler
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    public TpHandler(ActionExecutor executor, IMonitor monitor)
    {
        _executor = executor;
        _monitor = monitor;
    }

    public V2Result? Handle(V2Command cmd)
    {
        if (!cmd.IsValid())
        {
            return ResultBuilder.Failure(
                cmd.Seq,
                cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "tp 命令缺少必要字段");
        }

        // 读取参数
        string location = "";
        int x = -1, y = -1;

        if (cmd.Params.TryGetValue("location", out var locObj))
            location = locObj?.ToString() ?? "";

        if (cmd.Params.TryGetValue("x", out var xObj))
            TryConvertInt(xObj, out x);
        if (cmd.Params.TryGetValue("y", out var yObj))
            TryConvertInt(yObj, out y);

        // 如果没有 location，使用当前地图
        if (string.IsNullOrWhiteSpace(location))
            location = Game1.currentLocation?.Name ?? "";

        // 如果没有 x/y，使用目标地图的安全点
        if (x < 0 || y < 0)
        {
            var safeTarget = MapData.GetSafeWarpTarget(location);
            if (safeTarget != null)
            {
                x = safeTarget.Value.x;
                y = safeTarget.Value.y;
            }
            else
            {
                return ResultBuilder.Failure(
                    cmd.Seq,
                    cmd.ActionId,
                    ResultBuilder.Errors.InvalidParams,
                    $"tp 需要 x/y 参数，或目标地图 {location} 需要有安全坐标");
            }
        }

        // 执行瞬移
        bool ok = _executor.WarpTo(location, x, y);
        if (!ok)
        {
            return ResultBuilder.Failure(
                cmd.Seq,
                cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"无法传送到 {location} ({x},{y})");
        }

        _monitor.Log($"[V2] tp: 瞬移到 {location} ({x},{y})", LogLevel.Debug);

        return ResultBuilder.Success(
            cmd.Seq,
            cmd.ActionId,
            new Dictionary<string, object>
            {
                ["mode"] = "teleport",
                ["target"] = new Dictionary<string, object>
                {
                    ["location"] = location,
                    ["x"] = x,
                    ["y"] = y
                }
            })
            .WithObservation($"已瞬移到 {location} ({x},{y})");
    }

    private static bool TryConvertInt(object? raw, out int value)
    {
        value = -1;
        switch (raw)
        {
            case int i: value = i; return true;
            case long l: value = (int)l; return true;
            case double d: value = (int)d; return true;
            case string s when int.TryParse(s, out var parsed): value = parsed; return true;
            default: return false;
        }
    }
}
