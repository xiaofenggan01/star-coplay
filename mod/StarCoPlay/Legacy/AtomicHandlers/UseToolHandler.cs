// ============================================================
// [LEGACY] 旧版使用工具实现 — 已被当前 use_tool 执行原子替代
// 保留原因：UseInputBridgeHandlers=false 时的 fallback
// 计划：protocol-capability-completion 迭代中评估清理
// ============================================================
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.Json;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.V2.Compound;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2;

/// <summary>
/// V2 use_tool capability handler.
/// use_tool(target?) = 使用当前装备的工具对目标瓦片执行操作。
/// 工具类型由玩家当前装备决定，handler 不做工具切换。
/// </summary>
public class UseToolHandler
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;
    private readonly AnimationAwaiter _animAwaiter;

    public UseToolHandler(ActionExecutor executor, IMonitor monitor, AnimationAwaiter animAwaiter)
    {
        _executor = executor;
        _monitor = monitor;
        _animAwaiter = animAwaiter;
    }

    public V2Result? Handle(V2Command cmd)
    {
        var player = Game1.player;

        // 如果传了 tool_category，按类别查找并装备工具（优先于当前装备）
        if (cmd.Params.TryGetValue("tool_category", out var tcObj))
        {
            string? toolCat = tcObj is string s ? s
                : tcObj is JsonElement je && je.ValueKind == JsonValueKind.String ? je.GetString()
                : null;

            if (!string.IsNullOrEmpty(toolCat))
            {
                var foundTool = CompoundUtils.FindToolByCategory(player, toolCat);
                if (foundTool == null)
                {
                    return ResultBuilder.Failure(
                        cmd.Seq,
                        cmd.ActionId,
                        ResultBuilder.Errors.ItemNotFound,
                        $"背包中找不到 tool_category='{toolCat}' 对应的工具");
                }
                // Equip the resolved tool
                var equipResult = _executor.EquipItemByName(foundTool.Name);
                if (!equipResult.Success)
                {
                    return ResultBuilder.Failure(
                        cmd.Seq,
                        cmd.ActionId,
                        ResultBuilder.Errors.ItemNotFound,
                        $"装备 tool_category='{toolCat}' 解析到的工具 '{foundTool.Name}' 失败");
                }
                _monitor.Log($"[V2] use_tool: tool_category='{toolCat}' 解析为 '{foundTool.DisplayName}'", LogLevel.Debug);
            }
        }

        // 检查当前是否装备了工具
        var tool = player.CurrentTool;
        if (tool == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq,
                cmd.ActionId,
                ResultBuilder.Errors.ItemNotFound,
                "当前没有装备工具");
        }

        var toolName = tool.DisplayName ?? tool.Name ?? "未知工具";

        // 确定目标瓦片：优先使用参数坐标，否则使用玩家面前的瓦片
        bool hasExplicitCoords = TryGetTargetTile(cmd.Params, out var tileX, out var tileY);
        if (!hasExplicitCoords)
        {
            var facing = player.FacingDirection; // 0=上 1=右 2=下 3=左
            var tile = player.Tile;
            tileX = (int)tile.X + (facing == 1 ? 1 : facing == 3 ? -1 : 0);
            tileY = (int)tile.Y + (facing == 2 ? 1 : facing == 0 ? -1 : 0);
        }
        else
        {
            // 显式坐标时，先调整玩家朝向目标瓦片再调用 UseToolAt。
            // DoFunction 的动画依赖 FacingDirection 与目标一致，
            // 若同帧内 faceDirection+DoFunction，动画可能无法正确完成导致 ANIMATION_TIMEOUT。
            // 提前设置朝向可让 UseToolAt 内的 faceDirection 变为 no-op，与默认路径行为一致。
            int dx = tileX - (int)player.Tile.X;
            int dy = tileY - (int)player.Tile.Y;
            // 同 tile 时保持当前朝向，避免错误地强制面朝上
            if (dx != 0 || dy != 0)
            {
                int dir;
                if (Math.Abs(dx) > Math.Abs(dy))
                    dir = dx > 0 ? 1 : 3;
                else
                    dir = dy > 0 ? 2 : 0;
                player.faceDirection(dir);
            }
        }

        // 记录使用前的精力值
        float energyBefore = player.Stamina;

        var ok = _executor.UseToolAt(tileX, tileY);
        if (!ok)
        {
            return ResultBuilder.Failure(
                cmd.Seq,
                cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"在 ({tileX},{tileY}) 使用 {toolName} 失败");
        }

        _monitor.Log(
            $"[V2] use_tool: {toolName} at ({tileX},{tileY}), deferring until animation completes",
            LogLevel.Debug);

        // Defer result until tool animation completes
        _animAwaiter.Begin(cmd, $"use_tool({toolName})", energyBefore);
        return null;
    }

    // ── 参数解析 ──────────────────────────────────────────────────────────

    /// <summary>
    /// 尝试从参数中读取目标瓦片坐标。
    /// 支持 x/y 顶层参数，或 target.x/target.y 嵌套参数。
    /// </summary>
    private static bool TryGetTargetTile(Dictionary<string, object> parameters, out int x, out int y)
    {
        // 顶层 x, y
        if (TryGetInt(parameters, "x", out x) && TryGetInt(parameters, "y", out y))
            return true;

        // 嵌套 target.x, target.y
        if (parameters.TryGetValue("target", out var targetObj))
        {
            Dictionary<string, object>? nested = null;

            if (targetObj is Dictionary<string, object> dict)
                nested = dict;
            else if (targetObj is JsonElement element && element.ValueKind == JsonValueKind.Object)
                nested = DeserializeJsonObject(element);

            if (nested != null && TryGetInt(nested, "x", out x) && TryGetInt(nested, "y", out y))
                return true;
        }

        x = 0;
        y = 0;
        return false;
    }

    private static bool TryGetInt(Dictionary<string, object> args, string key, out int value)
    {
        value = 0;
        if (!args.TryGetValue(key, out var obj) || obj == null)
            return false;

        switch (obj)
        {
            case int i:
                value = i;
                return true;
            case long l when l >= int.MinValue && l <= int.MaxValue:
                value = (int)l;
                return true;
            case double d when d >= int.MinValue && d <= int.MaxValue:
                value = (int)d;
                return true;
            case float f when f >= int.MinValue && f <= int.MaxValue:
                value = (int)f;
                return true;
            case string s when int.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed):
                value = parsed;
                return true;
            case JsonElement element when element.ValueKind == JsonValueKind.Number:
                if (element.TryGetInt32(out var parsedInt))
                {
                    value = parsedInt;
                    return true;
                }
                break;
            case JsonElement element when element.ValueKind == JsonValueKind.String:
                if (int.TryParse(element.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsedStr))
                {
                    value = parsedStr;
                    return true;
                }
                break;
        }

        return false;
    }

    private static Dictionary<string, object> DeserializeJsonObject(JsonElement element)
    {
        var result = new Dictionary<string, object>();
        foreach (var prop in element.EnumerateObject())
        {
            object? val = prop.Value.ValueKind switch
            {
                JsonValueKind.String => prop.Value.GetString(),
                JsonValueKind.Number when prop.Value.TryGetInt64(out var l) => l,
                JsonValueKind.Number => prop.Value.GetDouble(),
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                _ => null
            };
            if (val != null)
                result[prop.Name] = val;
        }
        return result;
    }
}
