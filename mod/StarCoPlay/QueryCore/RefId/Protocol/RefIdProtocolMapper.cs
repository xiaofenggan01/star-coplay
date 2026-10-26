using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Parsing;
using ProtocolRefId = StarCoPlay.Protocol.V245.RefId;

namespace StarCoPlay.V2.RefId.Protocol;

public static class RefIdProtocolMapper
{
    public static ProtocolRefId ToProtocolRefId(ParsedRefId parsedRefId)
    {
        var refId = new ProtocolRefId
        {
            Value = parsedRefId.Raw,
            RefType = parsedRefId.RefType,
            Type = parsedRefId.Type,
            IdentityGuard = parsedRefId.IdentityGuard,
        };
        refId.LocatorSegments.Add(parsedRefId.LocatorParts);
        return refId;
    }

    public static RefResolveStatus ToProtocolStatus(RefIdStatus status)
    {
        return status switch
        {
            RefIdStatus.Resolved => RefResolveStatus.RefResolved,
            RefIdStatus.ResolvedDegraded => RefResolveStatus.RefResolvedDegraded,
            RefIdStatus.Missing => RefResolveStatus.RefMissing,
            RefIdStatus.Ambiguous => RefResolveStatus.RefAmbiguous,
            RefIdStatus.Unsupported => RefResolveStatus.RefUnsupported,
            RefIdStatus.StaleTypeMismatch => RefResolveStatus.RefStaleTypeMismatch,
            RefIdStatus.StaleGuardMismatch => RefResolveStatus.RefStaleGuardMismatch,
            RefIdStatus.StaleContextMismatch => RefResolveStatus.RefStaleContextMismatch,
            RefIdStatus.MalformedRef => RefResolveStatus.RefMalformedRef,
            _ => RefResolveStatus.Unspecified,
        };
    }

    public static RefResolution CreateResolution(
        ParsedRefId parsedRefId,
        RefIdStatus status,
        string? factKind = null,
        string? summary = null,
        string? debugReason = null)
    {
        return new RefResolution
        {
            Ref = ToProtocolRefId(parsedRefId),
            Status = ToProtocolStatus(status),
            RefType = parsedRefId.RefType,
            Type = parsedRefId.Type,
            FactKind = factKind ?? InferFactKind(parsedRefId),
            Summary = summary ?? "",
            DebugReason = debugReason ?? "",
        };
    }

    public static RefResolution CreateMalformedResolution(string rawRefId, string debugReason)
    {
        return new RefResolution
        {
            Ref = new ProtocolRefId { Value = rawRefId },
            Status = RefResolveStatus.RefMalformedRef,
            Summary = "Malformed ref.",
            DebugReason = debugReason,
        };
    }

    public static string InferFactKind(ParsedRefId parsedRefId)
    {
        return parsedRefId.RefType switch
        {
            "inventory" when parsedRefId.Type == "item" => "inventory_item",
            "ui_element" => "ui_element",
            "character" => parsedRefId.Type,
            "world_entity" => parsedRefId.Type,
            _ => parsedRefId.Type,
        };
    }
}
