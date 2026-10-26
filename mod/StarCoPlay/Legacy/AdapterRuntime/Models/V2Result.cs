using System.Collections.Generic;
using Newtonsoft.Json;

namespace StarCoPlay.V2.Models;

/// <summary>
/// V2 Result — unified return format for all V2 capabilities.
/// Written to results.jsonl.
/// </summary>
public class V2Result
{
    [JsonProperty("seq")]
    public int Seq { get; set; }

    [JsonProperty("action_id")]
    public string ActionId { get; set; } = "";

    [JsonProperty("protocol_version")]
    public string ProtocolVersion { get; set; } = "2.0";

    [JsonProperty("success")]
    public bool Success { get; set; }

    /// <summary>
    /// Three-value status: "ok", "partial", "failed".
    /// </summary>
    [JsonProperty("status")]
    public string Status { get; set; } = "ok";

    [JsonProperty("data")]
    public object? Data { get; set; }

    [JsonProperty("error")]
    public V2Error? Error { get; set; }

    /// <summary>
    /// Human-readable observation string summarizing what happened.
    /// </summary>
    [JsonProperty("observation")]
    public string? Observation { get; set; }

    [JsonProperty("side_effects")]
    public List<V2SideEffect>? SideEffects { get; set; }
}

public class V2Error
{
    [JsonProperty("code")]
    public string Code { get; set; } = "";

    [JsonProperty("message")]
    public string Message { get; set; } = "";
}

public class V2SideEffect
{
    [JsonProperty("type")]
    public string Type { get; set; } = "";

    [JsonProperty("detail")]
    public string Detail { get; set; } = "";
}
