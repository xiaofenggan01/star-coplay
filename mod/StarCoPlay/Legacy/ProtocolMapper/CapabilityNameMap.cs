using System;
using System.Collections.Generic;
using StarCoPlay.V2;

namespace StarCoPlay.Protocol;

/// <summary>
/// Single mapping point between protocol capability names and current V2 runtime names.
/// </summary>
public static class CapabilityNameMap
{
    private static readonly Dictionary<string, string> ProtocolToRuntime = new(StringComparer.OrdinalIgnoreCase)
    {
        ["say"] = AdapterV2.CapabilitySay,
        ["emote"] = AdapterV2.CapabilityEmote,
        ["face"] = AdapterV2.CapabilityFace,
        ["move_to"] = AdapterV2.CapabilityMoveTo,
        ["go_to"] = AdapterV2.CapabilityGoTo,
        ["go_to_bed"] = AdapterV2.CapabilityGoBed,
        ["interact"] = "interact",
        ["use_tool"] = AdapterV2.CapabilityUseTool,
        ["equip"] = AdapterV2.CapabilityEquip,
        ["open_menu"] = "open_menu",
        ["menu_click"] = "menu_click",
        ["menu_close"] = "menu_close",
        ["tp"] = AdapterV2.CapabilityTp,
    };

    private static readonly Dictionary<string, string> RuntimeToProtocol = new(StringComparer.OrdinalIgnoreCase)
    {
        [AdapterV2.CapabilitySay] = "say",
        [AdapterV2.CapabilityEmote] = "emote",
        [AdapterV2.CapabilityFace] = "face",
        [AdapterV2.CapabilityMoveTo] = "move_to",
        [AdapterV2.CapabilityGoTo] = "go_to",
        [AdapterV2.CapabilityGoBed] = "go_to_bed",
        ["interact"] = "interact",
        [AdapterV2.CapabilityUseTool] = "use_tool",
        [AdapterV2.CapabilityEquip] = "equip",
        ["open_menu"] = "open_menu",
        ["menu_click"] = "menu_click",
        ["menu_close"] = "menu_close",
        [AdapterV2.CapabilityTp] = "tp",
    };

    public static string ToRuntime(string protocolCapability)
    {
        if (string.IsNullOrWhiteSpace(protocolCapability))
            throw ProtocolMappingException.InvalidParams("CommandRequest.capability is required");

        if (ProtocolToRuntime.TryGetValue(protocolCapability, out var runtime))
            return runtime;

        throw ProtocolMappingException.CapabilityMissing($"Unsupported protocol capability: {protocolCapability}");
    }

    public static string ToProtocol(string runtimeCapability)
    {
        if (string.IsNullOrWhiteSpace(runtimeCapability))
            return "";

        return RuntimeToProtocol.TryGetValue(runtimeCapability, out var protocol)
            ? protocol
            : runtimeCapability;
    }

    public static bool IsInternalProtocolCapability(string protocolCapability) =>
        string.Equals(protocolCapability, "tp", StringComparison.OrdinalIgnoreCase);
}
