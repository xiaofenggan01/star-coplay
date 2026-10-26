using System.Collections.Generic;
using Newtonsoft.Json;

namespace StarCoPlay.V2.Models;

/// <summary>
/// V2 Command — protocol_version == "2.0" 时使用此格式。
/// Capability 替代 V1 的 action，params 为 Dictionary。
/// </summary>
public class V2Command
{
    /// <summary>Sequence number, echoed in result.</summary>
    [JsonProperty("seq")]
    public int Seq { get; set; }

    /// <summary>Must be "2.0".</summary>
    [JsonProperty("protocol_version")]
    public string ProtocolVersion { get; set; } = "2.0";

    /// <summary>move_to | interact | equip | say | query | observe | emote</summary>
    [JsonProperty("capability")]
    public string Capability { get; set; } = "";

    /// <summary>Capability-specific parameters.</summary>
    [JsonProperty("params")]
    public Dictionary<string, object> Params { get; set; } = new();

    /// <summary>Target player ID (multiplayer). If null/0, applies to current player.</summary>
    [JsonProperty("target_player_id")]
    public ulong? TargetPlayerId { get; set; }

    /// <summary>Unique action ID, echoed in result.</summary>
    [JsonProperty("action_id")]
    public string ActionId { get; set; } = "";

    public bool IsValid()
    {
        // Seq 允许 0（第一条命令），仅要求非负
        return Seq >= 0
            && ProtocolVersion == "2.0"
            && !string.IsNullOrEmpty(Capability)
            && !string.IsNullOrEmpty(ActionId);
    }
}
