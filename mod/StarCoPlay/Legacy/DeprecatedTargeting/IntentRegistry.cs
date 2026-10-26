using System;
using System.Collections.Generic;
using System.Linq;

namespace StarCoPlay.V2;

/// <summary>
/// Central registry for V2 interact intents.
/// Keeps the supported intent list stable and provides validation helpers.
/// </summary>
/// <summary>
/// [已弃用] 19 种 do intent 常量定义和验证。
/// 替代方案：当前模拟输入执行原子不再使用 intent 概念，interact 走"按 X 键"，use_tool 走"按 C 键"，
/// 由 SDV 根据当前装备/上下文自动判断效果。
/// </summary>
[Obsolete("Current simulated-input execution atoms don't use intent. Use interact + use_tool atoms instead.")]
public static class IntentRegistry
{
    public const string Talk = "talk";
    public const string Gift = "gift";
    public const string Answer = "answer";
    public const string Examine = "examine";
    public const string UseTool = "use_tool";
    public const string Water = "water";
    public const string Harvest = "harvest";
    public const string Store = "store";
    public const string Take = "take";
    public const string Drop = "drop";
    public const string Ship = "ship";
    public const string Sleep = "sleep";
    public const string Consume = "consume";
    public const string Pet = "pet";
    public const string Exit = "exit";
    public const string Place = "place";
    public const string PickUp = "pick_up";
    public const string Dismiss = "dismiss";
    public const string Craft = "craft";

    private static readonly HashSet<string> _supportedIntents = new(StringComparer.OrdinalIgnoreCase)
    {
        Talk, Gift, Answer, Examine, UseTool, Water, Harvest,
        Store, Take, Drop, Ship, Sleep, Consume, Pet, Exit,
        Place, PickUp, Dismiss, Craft
    };

    public static string Normalize(string? intent)
    {
        return (intent ?? string.Empty).Trim().ToLowerInvariant();
    }

    public static bool IsValid(string? intent)
    {
        return _supportedIntents.Contains(Normalize(intent));
    }

    public static IReadOnlyList<string> GetSupportedIntents()
    {
        return _supportedIntents.OrderBy(intent => intent, StringComparer.Ordinal).ToArray();
    }
}
