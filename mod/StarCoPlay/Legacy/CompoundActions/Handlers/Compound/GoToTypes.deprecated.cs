// ============================================================================
// DEPRECATED — go_to L2 重设计旧方案（已弃用，2026-05-03）
//
// 状态：暂停使用，等待新版 go_to L2 实现替代
// 排除：通过 StarCoPlay.csproj 中 `<Compile Remove="**\*.deprecated.cs" />` 排除编译
// 原因：方案重新设计；保留源码作为旧实现的历史快照与设计参考
//
// MUST NOT：在新代码中引用本文件中的任何类型；如需复用思路请整段重写
// ============================================================================

using System.Collections.Generic;

namespace StarCoPlay.V2.Compound;

// ── TargetKind ───────────────────────────────────────────────────────────────

public enum TargetKind
{
    Map,
    Npc,
    Animal,
    Player,
    Failure,
}

// ── ResolveErrorCode ─────────────────────────────────────────────────────────

public enum ResolveErrorCode
{
    TargetNotFound,
    AmbiguousAnimal,
    AmbiguousBuilding,
    TrackingLost,
}

// ── ResolvedTarget ───────────────────────────────────────────────────────────

public record ResolvedTarget
{
    public TargetKind Kind { get; init; }
    public string MapName { get; init; } = "";
    public int TileX { get; init; }
    public int TileY { get; init; }
    public bool HasPreciseCoord { get; init; }
    public bool IsTrackingEntity { get; init; }
    public string? TrackingRef { get; init; }
    public string? PreferredSide { get; init; }
    public string? FaceDirection { get; init; }

    public ResolveErrorCode? ErrorCode { get; init; }
    public string? ErrorMessage { get; init; }
    public List<string>? Suggestions { get; init; }

    public string RawInput { get; init; } = "";
    public string? ResolvedBy { get; init; }

    public bool IsSuccess => ErrorCode == null;
}

// ── PathSegment enums ────────────────────────────────────────────────────────

public enum SegmentType
{
    IntraMap,
    Warp,
}

public enum WarpMechanism
{
    EdgeWarp,
    DoorInteract,
}

// ── FinalTargetHint ──────────────────────────────────────────────────────────

public record FinalTargetHint(
    bool UseAdjacentSide,
    string? AdjacentSide,
    string? FaceDirection
);

// ── PathSegment ──────────────────────────────────────────────────────────────

/// <summary>
/// 路径段。Warp 类型可能附带多个候选 warp tile（bug #8 修复）：
/// - <see cref="FromX"/>/<see cref="FromY"/> 始终是首选 tile（向后兼容）。
/// - <see cref="WarpCandidates"/> 是全部候选（含首选），ESM 在首选启动失败时按顺序回退。
///   IntraMap 段或单候选场景 WarpCandidates 为 null。
/// </summary>
public record PathSegment(
    SegmentType Type,
    string FromMap,
    int FromX,
    int FromY,
    string ToMap,
    int ToX,
    int ToY,
    WarpMechanism? Mechanism,
    FinalTargetHint? FinalHint,
    IReadOnlyList<(int X, int Y)>? WarpCandidates = null
);

// ── RouteRequest / RouteResult ───────────────────────────────────────────────

public record RouteRequest(
    string FromMap,
    int FromX,
    int FromY,
    ResolvedTarget Target
);

public record RouteResult
{
    public bool Success { get; init; }
    public string? ErrorCode { get; init; }
    public string? ErrorMessage { get; init; }
    public List<PathSegment> Segments { get; init; } = new();
    public int TotalHops => Segments.Count;
}

// ── GoTo error code string constants ────────────────────────────────────────

public static class GoToErrors
{
    public const string TargetNotFound = "TARGET_NOT_FOUND";
    public const string AmbiguousAnimal = "AMBIGUOUS_ANIMAL";
    public const string AmbiguousBuilding = "AMBIGUOUS_BUILDING";
    public const string TrackingLost = "TRACKING_LOST";
    public const string NoRoute = "NO_ROUTE";
    public const string TargetUnreachable = "TARGET_UNREACHABLE";
    public const string WarpClosed = "WARP_CLOSED";
    public const string WarpTimeout = "WARP_TIMEOUT";
    public const string NavigationTimeout = "NAVIGATION_TIMEOUT";
}
