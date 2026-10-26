using System.Collections.Generic;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

// ─── Enums ───────────────────────────────────────────────────────────────────

public enum GoToPhase
{
    Resolving,
    Routing,
    Segment,
    Tracking,
    Done,
    Failed
}

public enum TargetKind
{
    Player,
    MapCoord,
    Npc,
    Animal,
    Building,
    Tile,
    Unknown
}

public enum SegmentType
{
    IntraMap,
    EdgeWarp,
    DoorInteract
}

// ─── Input ────────────────────────────────────────────────────────────────────

/// <summary>
/// K4: single-string target parameter with optional face direction.
/// </summary>
public record GoToInput(string Target, string? FaceDirection = null);

// ─── Intermediate / routing types ────────────────────────────────────────────

/// <summary>
/// Result of target resolution (Resolving phase).
/// </summary>
public record ResolvedTarget(
    TargetKind Kind,
    string LocationName,        // NameOrUniqueName — never short Name
    int X,
    int Y,
    bool IsDynamic,             // true = NPC / Animal, needs live Tracking
    object? GameObject,         // NPC / FarmAnimal reference, or null
    string? FaceDirection       // forwarded from GoToInput
);

/// <summary>
/// One leg of a multi-map route (Routing / Segment phase).
/// 段类型 → 字段语义：
///   IntraMap     — 同地图内移动；TargetX/Y = 终点；Exit = null
///   EdgeWarp     — WalkThrough 出口（玩家走到 warp tile + 持续按方向）；
///                  Exit.WarpTiles 列出所有候选 tile，Exit.PressDirection = 按键方向；
///                  SourceX/Y = 代表性 tile（向后兼容，等于 Exit.WarpTiles[0]）
///   DoorInteract — InteractDoor 出口（走到 door 旁 + interact 触发）；
///                  Exit.DoorTiles 列出所有 trigger tile（梯子可多 tile）；
///                  SourceX/Y = 代表性 tile（向后兼容）
/// </summary>
public record PathSegment(
    SegmentType Type,
    string SourceMap,           // NameOrUniqueName
    string TargetMap,           // NameOrUniqueName
    int SourceX, int SourceY,
    int TargetX, int TargetY,
    string? Direction,          // 兼容字段：EdgeWarp 段的按键方向（lowercase: north/south/east/west）
    int? OriginalWarpX,         // 兼容字段：代表性 warp tile X
    int? OriginalWarpY,         // 兼容字段：代表性 warp tile Y
    StarCoPlay.MapExit? Exit = null  // ★ 新设计：完整 MapExit 引用（仅 EdgeWarp/DoorInteract 段非 null）
);

/// <summary>
/// Candidate returned when AMBIGUOUS_BUILDING is raised (K1).
/// </summary>
public record BuildingCandidate(
    string LocationName,
    string BuildingType,
    int Distance,
    int X,
    int Y
);

// ─── Output ───────────────────────────────────────────────────────────────────

/// <summary>
/// Unified output for the go_to capability — covers success, failure, and ambiguity.
/// </summary>
public record GoToOutput(
    bool Success,
    string? ErrorCode,
    string? Message,
    ResolvedTarget? Resolved,
    List<PathSegment>? Segments,
    GoToPhase FinalPhase,
    int ElapsedMs,
    List<BuildingCandidate>? Candidates,    // populated on AMBIGUOUS_BUILDING
    string? LastKnownLocation,              // populated on TRACKING_LOST
    int? LastKnownX,
    int? LastKnownY,
    Dictionary<string, object>? Metadata    // phase_history, elapsed_ms, etc.
)
{
    /// <summary>
    /// Quick factory for failure outputs — keeps call-sites concise.
    /// </summary>
    public static GoToOutput Failure(
        string errorCode,
        string message,
        GoToPhase finalPhase,
        int elapsedMs) =>
        new(
            Success: false,
            ErrorCode: errorCode,
            Message: message,
            Resolved: null,
            Segments: null,
            FinalPhase: finalPhase,
            ElapsedMs: elapsedMs,
            Candidates: null,
            LastKnownLocation: null,
            LastKnownX: null,
            LastKnownY: null,
            Metadata: null
        );

    /// <summary>
    /// Convert to the standard V2Result wire format.
    /// </summary>
    public V2Result ToV2Result() =>
        new()
        {
            Success = Success,
            Status = Success ? "ok" : "failed",
            Data = Success ? (object?)new
            {
                resolved = Resolved,
                segments = Segments,
                final_phase = FinalPhase.ToString().ToLower(),
                elapsed_ms = ElapsedMs,
                metadata = Metadata
            } : null,
            Error = Success ? null : new V2Error
            {
                Code = ErrorCode ?? GoToErrors.InvalidParams,
                Message = Message ?? ""
            },
            Observation = Success
                ? $"go_to completed ({FinalPhase})"
                : $"go_to failed: [{ErrorCode}] {Message}"
        };
}

// ─── Error code constants ─────────────────────────────────────────────────────

/// <summary>
/// Canonical error code strings — aligned with mainline-design §5 and Sprint Contract.
/// </summary>
public static class GoToErrors
{
    public const string AmbiguousBuilding  = "AMBIGUOUS_BUILDING";
    public const string TargetUnreachable  = "TARGET_UNREACHABLE";
    public const string MapNotFound        = "MAP_NOT_FOUND";
    public const string NoRoute            = "NO_ROUTE";
    public const string WarpTimeout        = "WARP_TIMEOUT";
    public const string PathBlocked        = "PATH_BLOCKED";
    public const string TrackingLost       = "TRACKING_LOST";
    public const string NavigationTimeout  = "NAVIGATION_TIMEOUT";
    public const string TargetNotFound     = "TARGET_NOT_FOUND";
    public const string InvalidParams      = "INVALID_PARAMS";
    public const string AmbiguousTarget    = "AMBIGUOUS_TARGET";
}

// ─── Extension method (alternative entry point) ───────────────────────────────

public static class GoToOutputExtensions
{
    /// <summary>
    /// Extension-method variant of ToV2Result for call-sites that prefer extension syntax.
    /// </summary>
    public static V2Result ToV2Result(this GoToOutput output) => output.ToV2Result();
}
