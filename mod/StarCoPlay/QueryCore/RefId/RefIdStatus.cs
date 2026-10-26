using Newtonsoft.Json;

namespace StarCoPlay.V2.RefId;

public enum RefIdStatus
{
    [JsonProperty("resolved")]
    Resolved,

    [JsonProperty("resolved_degraded")]
    ResolvedDegraded,

    [JsonProperty("missing")]
    Missing,

    [JsonProperty("ambiguous")]
    Ambiguous,

    [JsonProperty("unsupported")]
    Unsupported,

    [JsonProperty("stale_type_mismatch")]
    StaleTypeMismatch,

    [JsonProperty("stale_guard_mismatch")]
    StaleGuardMismatch,

    [JsonProperty("stale_context_mismatch")]
    StaleContextMismatch,

    [JsonProperty("malformed_ref")]
    MalformedRef,
}
