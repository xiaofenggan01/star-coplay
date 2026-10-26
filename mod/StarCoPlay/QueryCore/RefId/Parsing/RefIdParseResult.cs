using StarCoPlay.V2.RefId;

namespace StarCoPlay.V2.RefId.Parsing;

public sealed record RefIdParseResult(
    RefIdStatus Status,
    ParsedRefId? ParsedRefId,
    string? DebugReason)
{
    public bool IsValid => Status != RefIdStatus.MalformedRef && ParsedRefId != null;

    public static RefIdParseResult Success(ParsedRefId parsedRefId)
    {
        return new RefIdParseResult(RefIdStatus.Resolved, parsedRefId, null);
    }

    public static RefIdParseResult Malformed(string debugReason)
    {
        return new RefIdParseResult(RefIdStatus.MalformedRef, null, debugReason);
    }
}
