using Microsoft.Xna.Framework;
using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Protocol;
using ProtocolRefId = StarCoPlay.Protocol.V245.RefId;

namespace StarCoPlay.V2.RefId.Resolution;

/// <summary>
/// Carries the resolved source object, location, and tile so that downstream projectors
/// can produce typed Fact data without re-resolving.
/// </summary>
public sealed record ResolvedRefSource(
    object? Value,
    GameLocation? Location = null,
    Vector2? Tile = null,
    string? ContainerKind = null,
    string? ContainerLocator = null,
    int? InventorySlot = null);

/// <summary>
/// Lightweight wrapper for door resolution data (doorTile + targetMap),
/// stored in <see cref="ResolvedRefSource.Value"/> for DoorProjector.
/// </summary>
public sealed record DoorResolvedData(Point DoorTile, string TargetMap);

public sealed record RefIdResolutionResult(
    RefIdStatus Status,
    ParsedRefId? ParsedRefId,
    string FactKind,
    string? Summary,
    string? DebugReason,
    ResolvedRefSource? Source = null)
{
    public ProtocolRefId? ProtocolRef => ParsedRefId == null ? null : RefIdProtocolMapper.ToProtocolRefId(ParsedRefId);

    public RefResolution ToProtocolResolution(string rawRefId)
    {
        return ParsedRefId == null
            ? RefIdProtocolMapper.CreateMalformedResolution(rawRefId, DebugReason ?? "Malformed ref.")
            : RefIdProtocolMapper.CreateResolution(ParsedRefId, Status, FactKind, Summary, DebugReason);
    }
}
