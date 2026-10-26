// ============================================================
// [LEGACY] 旧版通用意图执行 — 已标记 @Obsolete
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
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2;

/// <summary>
/// V2 do capability handler.
/// do(target?) = 按动作键，游戏上下文决定实际效果（对话、开箱、捡物等）。
/// 这是最通用的交互动作，不做语义判断，完全由游戏的 checkAction 机制处理。
/// </summary>
/// <summary>
/// [已弃用] do 命令处理器（19 种 intent 路由）。
/// 替代方案：启用 ModConfig.UseInputBridgeHandlers=true 后使用当前 interact + use_tool 执行原子。
/// 旧路径直接调 SDV API（DoFunction/checkAction），无动画、不拟人。
/// 新路径模拟玩家按键（face + Tap C/X），有完整动画 + 自动 AnimationAwaiter 等待。
/// 保留以兼容旧调用，新代码请用 interact + use_tool。
/// </summary>
[Obsolete("Use current interact + use_tool execution atoms instead. Enable via ModConfig.UseInputBridgeHandlers=true.")]
public class DoHandler
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;
    private InteractHandler? _interactHandler;

    public DoHandler(ActionExecutor executor, IMonitor monitor)
    {
        _executor = executor;
        _monitor = monitor;
    }

    /// <summary>注入 InteractHandler，用于 intent 路由。</summary>
    public void SetInteractHandler(InteractHandler handler)
    {
        _interactHandler = handler;
    }

    public V2Result? Handle(V2Command cmd)
    {
        // 有 intent 参数时，委托给 InteractHandler 处理精确操作
        if (TryGetString(cmd.Params, "intent", out var intent) && !string.IsNullOrWhiteSpace(intent))
        {
            if (_interactHandler == null)
            {
                return ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.ExecutionFailed,
                    "InteractHandler 未初始化");
            }
            _monitor.Log($"[V2] do: 检测到 intent={intent}，委托给 InteractHandler", LogLevel.Debug);
            return _interactHandler.Handle(cmd);
        }

        // 无 intent → 原来的通用 checkAction 逻辑
        // 确定目标瓦片：优先使用参数坐标，否则使用玩家面前的瓦片
        if (!TryGetTargetTile(cmd.Params, out var tileX, out var tileY))
        {
            var player = Game1.player;
            var facing = player.FacingDirection; // 0=上 1=右 2=下 3=左
            var tile = player.Tile;
            tileX = (int)tile.X + (facing == 1 ? 1 : facing == 3 ? -1 : 0);
            tileY = (int)tile.Y + (facing == 2 ? 1 : facing == 0 ? -1 : 0);
        }

        // 提取可选的 target 描述（用于日志，不影响逻辑）
        TryGetString(cmd.Params, "target", out var targetDesc);

        float energyBefore = Game1.player.Stamina;

        var ok = _executor.InteractWithTile(tileX, tileY);
        if (!ok)
        {
            var desc = string.IsNullOrWhiteSpace(targetDesc)
                ? $"({tileX},{tileY})"
                : $"{targetDesc} ({tileX},{tileY})";

            return ResultBuilder.Failure(
                cmd.Seq,
                cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"在 {desc} 执行动作失败");
        }

        // 构建成功结果
        var observation = string.IsNullOrWhiteSpace(targetDesc)
            ? $"已在 ({tileX},{tileY}) 执行动作"
            : $"已对 {targetDesc} ({tileX},{tileY}) 执行动作";

        _monitor.Log($"[V2] do: ({tileX},{tileY}) target={targetDesc ?? "none"}", LogLevel.Debug);

        var resultData = new Dictionary<string, object>
        {
            ["target"] = new { x = tileX, y = tileY }
        };
        if (!string.IsNullOrWhiteSpace(targetDesc))
            resultData["target_desc"] = targetDesc;

        return ResultBuilder.Success(
                cmd.Seq,
                cmd.ActionId,
                resultData)
            .WithObservation(observation)
            .WithSideEffectIfEnergyChanged(energyBefore);
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

        // 嵌套 target.x, target.y（仅当 target 是对象时）
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

    private static bool TryGetString(Dictionary<string, object> args, string key, out string? value)
    {
        value = null;
        if (!args.TryGetValue(key, out var obj) || obj == null)
            return false;

        switch (obj)
        {
            case string s:
                value = s;
                return true;
            case JsonElement element when element.ValueKind == JsonValueKind.String:
                value = element.GetString();
                return true;
            default:
                // 如果 target 是对象类型，不当作字符串处理
                if (obj is Dictionary<string, object> || (obj is JsonElement je && je.ValueKind == JsonValueKind.Object))
                    return false;
                value = obj.ToString();
                return !string.IsNullOrWhiteSpace(value);
        }
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
