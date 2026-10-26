using System.Collections.Generic;

namespace StarCoPlay.V2.RefId;

public sealed record ParsedRefId(
    string Raw,
    string RefType,
    string Type,
    IReadOnlyList<string> LocatorParts,
    string IdentityGuard);
