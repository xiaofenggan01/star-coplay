using System;
using System.Collections.Generic;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.RefId.Parsing;

public static class RefIdParser
{
    public static RefIdParseResult Parse(string? refId)
    {
        if (string.IsNullOrWhiteSpace(refId))
            return RefIdParseResult.Malformed("RefId is empty.");

        var hashIndex = refId.IndexOf('#', StringComparison.Ordinal);
        if (hashIndex < 0)
            return RefIdParseResult.Malformed("RefId must contain exactly one '#'.");

        if (hashIndex != refId.LastIndexOf('#'))
            return RefIdParseResult.Malformed("RefId must contain exactly one '#'.");

        if (hashIndex == 0 || hashIndex == refId.Length - 1)
            return RefIdParseResult.Malformed("RefId must contain both path and identity guard.");

        var path = refId[..hashIndex];
        var identityGuard = refId[(hashIndex + 1)..];
        if (!RefIdTokenEncoder.IsSafeToken(identityGuard))
            return RefIdParseResult.Malformed("Identity guard is not a safe token.");

        var parts = path.Split(':');
        if (parts.Length < 3)
            return RefIdParseResult.Malformed("RefId path must contain ref_type, type, and locator.");

        foreach (var part in parts)
        {
            if (!RefIdTokenEncoder.IsSafeToken(part))
                return RefIdParseResult.Malformed("RefId contains an unsafe or empty segment.");
        }

        var locatorParts = new List<string>(parts.Length - 2);
        for (var i = 2; i < parts.Length; i++)
            locatorParts.Add(parts[i]);

        var parsed = new ParsedRefId(
            refId,
            parts[0],
            parts[1],
            locatorParts,
            identityGuard);

        return RefIdParseResult.Success(parsed);
    }
}
